"""Small-group disclosure control for the RECORDS route (docs/HRMS_CONTRACTS.md "Small groups",
SECURITY_BOUNDARY.md §12).

The RECORDS route sends only computed results, aliased identities and KEEP/GENERALIZE values. That is not enough on
its own: a count of 1 in a cross-tab tells the provider the attribute of the one person in that cell, and a listed
alias next to a rare combination of attributes ("the only person in Legal, created 2019-03, PIN 560xxx") can be
linked to a public profile. This module enforces a deterministic k-threshold (`min_group_size`, sealed config
registry/rules/disclosure_control.yaml) on what leaves:

  * count tables (roster_question): every cell defined by a quasi-identifier predicate with 0 < count < k is
    suppressed, and complementary suppression is added until no suppressed cell is uniquely determined by the
    published numbers of the same response (row/column/grand totals, the matched count, the implicit "did not match"
    cell). The audit is exact for the nested-sum tables this route produces (interval propagation over a tree of sum
    constraints; every integer in a bound is feasible);
  * grouped bands (contractor_budget per cost centre): a group with fewer than k people is merged into "other cost
    centres", which is itself shown only when it has at least k people;
  * listings: each listed alias's leaving attributes (the contract's `show` fields) must be shared by at least k people
    of the input population the listing is drawn from; otherwise the attributes are coarsened along the schema's
    hierarchy (month -> year -> 5-year range; PIN 560xxx -> 56xxxx; a sealed enumeration roll-up such as department ->
    function) and, for the rows that still fail, withheld with a marker. Withheld rows must themselves be
    indistinguishable among at least k people;
  * a roster filter matching 1..k-1 people says "fewer than k" and lists nobody;
  * differencing: a per-conversation log of filter predicates (in memory, bounded; predicates only, never values)
    refuses a query whose result set differs from an earlier one in the same conversation by fewer than k people.

Deterministic thresholds, not differential privacy: these outputs are counts and lists a human reads and acts on
(an exception list must name the right people), so calibrated noise would make them wrong; a deterministic threshold
with complementary suppression is the standard tool for published tables (e.g. tau-ARGUS practice). DP (OpenDP) and
ARX-style optimal anonymisation were considered and are not built; DP remains an option for released statistics.

What this does NOT protect against is stated in SECURITY_BOUNDARY.md §12: the provider's background knowledge,
attribute disclosure when all k people in a group share the same value (homogeneity; l-diversity is not enforced),
differencing across conversations, across gateway processes or across different exports, and reasoning about the
suppression algorithm itself.
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .fieldschema import SchemaError, special_hint

CONFIG_ID = "disclosure_control"
CONFIG_KIND = "pgw-disclosure-control"
DEFAULT_K = 5
MIN_K = 3                 # below this only with a sealed override and a written justification
ABSOLUTE_MIN_K = 2        # never below: k = 1 is "no control"
MAX_K = 100
LISTING_POLICIES = ("coarsen", "withhold", "alias_only")
MARK_SUPPRESSED = "suppressed"
MARK_WITHHELD = "withheld"
BIG = 10 ** 15            # "unbounded" in the table audit
_LABEL_BAD = re.compile(r"[\x00-\x1f\x7f]")


class DisclosureRefused(ValueError):
    """A query refused by disclosure control (differencing). The message has no value; `report` is the records
    section of the transparency report for the refused request."""

    def __init__(self, message: str, report: dict | None = None):
        super().__init__(message)
        self.report = report


class _Withheld:
    """A display value that leaves nothing (a column dropped for everyone, or a value withheld for one row)."""

    def __repr__(self) -> str:
        return "<withheld>"


WITHHELD = _Withheld()


# =============================================================================================================
# sealed configuration
# =============================================================================================================

@dataclass(frozen=True)
class Config:
    version: int
    k: int
    override: str                 # justification for k < MIN_K ("" otherwise)
    listing: str
    differencing: bool
    max_scopes: int
    max_queries: int
    ttl_s: float
    schemas: dict                 # schema id -> raw block (checked against the schema at bind time)

    @property
    def ref(self) -> str:
        return f"{CONFIG_ID}@{self.version}"


def _keys(d, allowed: set, where: str) -> None:
    if not isinstance(d, dict):
        raise SchemaError(f"{where}: must be a mapping")
    extra = set(d) - allowed
    if extra:
        raise SchemaError(f"{where}: unknown keys {sorted(extra)}")


def _posint(raw, where: str, lo: int, hi: int) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or not lo <= raw <= hi:
        raise SchemaError(f"{where}: integer between {lo} and {hi}")
    return raw


def config_path(registry_dir: Path) -> Path:
    return Path(registry_dir) / "rules" / f"{CONFIG_ID}.yaml"


def load_config(path: str | Path, data: bytes | None = None) -> Config:
    """registry/rules/disclosure_control.yaml (sealed and signed like every rules file). Fails closed."""
    name = Path(path).name
    try:
        raw = yaml.safe_load(Path(path).read_text() if data is None else data.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        raise SchemaError(f"{name}: not valid YAML") from None
    if not isinstance(raw, dict) or raw.get("kind") != CONFIG_KIND:
        raise SchemaError(f"{name}: kind must be {CONFIG_KIND}")
    _keys(raw, {"kind", "config", "version", "min_group_size", "min_group_size_override", "listing", "differencing",
                "schemas", "note"}, name)
    if raw.get("config") != CONFIG_ID or Path(path).stem != CONFIG_ID:
        raise SchemaError(f"{name}: config id must be {CONFIG_ID} (= the file name)")
    version = _posint(raw.get("version"), f"{name}.version", 1, 10 ** 6)
    k = _posint(raw.get("min_group_size", DEFAULT_K), f"{name}.min_group_size", ABSOLUTE_MIN_K, MAX_K)
    ov = raw.get("min_group_size_override")
    just = ""
    if ov is not None:
        _keys(ov, {"justification"}, f"{name}.min_group_size_override")
        just = str(ov.get("justification", "")).strip()
        if len(just) < 20:
            raise SchemaError(f"{name}: min_group_size_override needs a written justification (20+ characters)")
        if k >= MIN_K:
            raise SchemaError(f"{name}: min_group_size_override applies only to a min_group_size below {MIN_K}")
    elif k < MIN_K:
        raise SchemaError(f"{name}: min_group_size below {MIN_K} needs a sealed min_group_size_override with a "
                          "justification")
    listing = str(raw.get("listing", "coarsen"))
    if listing not in LISTING_POLICIES:
        raise SchemaError(f"{name}.listing: one of {LISTING_POLICIES}")
    dif = raw.get("differencing") or {}
    _keys(dif, {"enabled", "max_queries_per_scope", "max_scopes", "ttl_s"}, f"{name}.differencing")
    enabled = dif.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SchemaError(f"{name}.differencing.enabled: true or false")
    schemas = raw.get("schemas") or {}
    _keys(schemas, set(schemas), f"{name}.schemas")
    for sid, block in schemas.items():
        _keys(block, {"quasi_identifiers", "hierarchies", "note"}, f"{name}.schemas.{sid}")
    return Config(version=version, k=k, override=just, listing=listing, differencing=enabled,
                  max_scopes=_posint(dif.get("max_scopes", 10_000), f"{name}.differencing.max_scopes", 1, 10 ** 6),
                  max_queries=_posint(dif.get("max_queries_per_scope", 64),
                                      f"{name}.differencing.max_queries_per_scope", 1, 1024),
                  ttl_s=float(_posint(dif.get("ttl_s", 86_400), f"{name}.differencing.ttl_s", 60, 30 * 86_400)),
                  schemas=dict(schemas))


@dataclass(frozen=True)
class SchemaControl:
    qis: tuple                    # quasi-identifier field names (default: every KEEP and GENERALIZE field) + additions
    hierarchies: dict             # field name -> tuple of {value: label} levels (KEEP enumerations)


def schema_control(cfg: Config, schema) -> SchemaControl:
    """The QI list and roll-up hierarchies of one field schema. A schema block may only ADD quasi-identifiers: the
    default (every KEEP and GENERALIZE field, i.e. every field whose value can leave) cannot be narrowed."""
    block = cfg.schemas.get(schema.id) or {}
    where = f"{CONFIG_ID}.schemas.{schema.id}"
    default = [f.name for f in schema.fields if f.disposition in ("KEEP", "GENERALIZE")]
    extra = block.get("quasi_identifiers") or []
    if not isinstance(extra, list) or not all(isinstance(x, str) for x in extra):
        raise SchemaError(f"{where}.quasi_identifiers: a list of field names (added to the default)")
    for n in extra:
        if schema.spec(n) is None:
            raise SchemaError(f"{where}.quasi_identifiers: {n!r} is not a field of the schema")
    hier = {}
    raw_h = block.get("hierarchies") or {}
    if not isinstance(raw_h, dict):
        raise SchemaError(f"{where}.hierarchies: field -> list of value-to-label mappings")
    for n, levels in raw_h.items():
        sp = schema.spec(n)
        if sp is None or sp.disposition != "KEEP" or sp.type != "enum":
            raise SchemaError(f"{where}.hierarchies.{n}: only a KEEP enumeration field takes a roll-up hierarchy")
        if not isinstance(levels, list) or not 1 <= len(levels) <= 5:
            raise SchemaError(f"{where}.hierarchies.{n}: 1-5 levels")
        out = []
        for i, m in enumerate(levels):
            if not isinstance(m, dict) or not m:
                raise SchemaError(f"{where}.hierarchies.{n}[{i}]: a mapping of enumeration value -> label")
            for v, lab in m.items():
                if v not in sp.enum:
                    raise SchemaError(f"{where}.hierarchies.{n}[{i}]: {v!r} is not a value of the enumeration")
                if (not isinstance(lab, str) or not 1 <= len(lab) <= 80 or lab.strip() != lab or _LABEL_BAD.search(lab)
                        or sum(c.isdigit() for c in lab) >= 4 or special_hint(lab)):
                    raise SchemaError(f"{where}.hierarchies.{n}[{i}]: labels are short, have no 4+ digits and no "
                                      "special-category word")
            out.append(dict(m))
        hier[n] = tuple(out)
    qis = tuple(dict.fromkeys([*default, *extra]))
    return SchemaControl(qis=qis, hierarchies=hier)


@dataclass
class Control:
    """Bound per RECORDS purpose at startup: the effective k, the listing policy, per-schema controls and the
    in-memory query log."""
    cfg: Config
    k: int
    listing: str
    schemas: dict                 # schema id -> SchemaControl
    log: QueryLog | None = None


# =============================================================================================================
# count tables: primary + complementary suppression with an exact audit
# =============================================================================================================

@dataclass
class Cell:
    name: str
    value: int
    sensitive: bool               # defined by a quasi-identifier predicate
    priority: int                 # complementary suppression prefers the lowest (priority, value)
    shown: bool = True            # False: a structural cell that is never printed (e.g. "did not match")
    total: bool = False
    reveals_small: bool = False   # printed as "fewer than k" when it is a primary suppression
    suppressed: bool = False
    primary: bool = False


class Table:
    """Cells and sum constraints (total = sum of parts). Suppressed cells are known to the reader only by their
    marker: "fewer than k" -> [1, k-1]; a complementary suppression of such a cell -> [k, inf); any other suppressed
    cell -> [1, inf) (zeros are always printed, so a suppressed cell is non-zero); a never-printed cell -> [0, inf)."""

    def __init__(self, k: int):
        self.k = k
        self.cells: dict = {}
        self.eqs: list = []

    def add(self, cell: Cell) -> Cell:
        self.cells[cell.name] = cell
        return cell

    def eq(self, total: str, parts: list) -> None:
        self.eqs.append((total, list(parts)))

    def _initial(self, c: Cell) -> tuple:
        if c.shown and not c.suppressed:
            return (c.value, c.value)
        if not c.shown:
            return (0, BIG)
        if c.reveals_small:
            return (1, self.k - 1) if c.primary else (self.k, BIG)
        return (1, BIG)

    def bounds(self) -> dict:
        b = {n: list(self._initial(c)) for n, c in self.cells.items()}
        for _ in range(4 * len(self.cells) + 8):
            changed = False
            for tot, parts in self.eqs:
                lo = sum(b[p][0] for p in parts)
                hi = sum(b[p][1] for p in parts)
                t = b[tot]
                if lo > t[0]:
                    t[0], changed = lo, True
                if hi < t[1]:
                    t[1], changed = hi, True
                for p in parts:
                    olo, ohi = lo - b[p][0], hi - b[p][1]
                    nlo, nhi = t[0] - ohi, t[1] - olo
                    if nlo > b[p][0]:
                        b[p][0], changed = nlo, True
                    if nhi < b[p][1]:
                        b[p][1], changed = nhi, True
            if not changed:
                break
        return {n: (max(lo, 0), min(hi, BIG)) for n, (lo, hi) in b.items()}

    def _audited(self, c: Cell) -> bool:
        return c.suppressed or (not c.shown and c.primary)

    def pinned(self) -> list:
        b = self.bounds()
        return [c for c in self.cells.values() if self._audited(c) and b[c.name][0] >= b[c.name][1]]

    def protect(self) -> dict:
        """Primary suppression of every sensitive cell with 0 < value < k, then complementary suppression until no
        suppressed cell (and no small never-printed cell) is uniquely determined by what is printed."""
        for c in self.cells.values():
            if c.sensitive and 0 < c.value < self.k:
                c.primary = True
                c.suppressed = c.shown
        for _ in range(len(self.cells) + 2):
            bad = self.pinned()
            if not bad:
                break
            c = bad[0]
            cands = []
            for tot, parts in self.eqs:
                members = [tot, *parts]
                if c.name not in members:
                    continue
                for m in members:
                    mc = self.cells[m]
                    if mc.shown and not mc.suppressed and (mc.value > 0 or mc.total):
                        cands.append(mc)
            if not cands:
                # nothing left to suppress next to it: suppress every printed cell (fail closed)
                rest = [x for x in self.cells.values() if x.shown and not x.suppressed]
                if not rest:
                    break
                for x in rest:
                    x.suppressed = True
                continue
            pick = min(cands, key=lambda x: (x.priority, x.value, x.name))
            pick.suppressed = True
        return {"primary": sum(1 for c in self.cells.values() if c.primary and c.shown),
                "complementary": sum(1 for c in self.cells.values()
                                     if c.suppressed and not c.primary and not c.total),
                "totals": sum(1 for c in self.cells.values() if c.suppressed and not c.primary and c.total)}

    def marker(self, name: str) -> str | None:
        c = self.cells[name]
        if not c.suppressed:
            return None
        if c.reveals_small and c.primary:
            return f"fewer than {self.k}"
        return MARK_SUPPRESSED if not c.reveals_small else MARK_WITHHELD


# =============================================================================================================
# listings: k-anonymous attributes next to aliases
# =============================================================================================================

def _year(v):
    return v[:4] if isinstance(v, str) and re.fullmatch(r"\d{4}(-\d{2})?", v) else WITHHELD


def _five_years(v):
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}(-\d{2})?", v):
        return WITHHELD
    y = int(v[:4])
    lo = y - y % 5
    return f"{lo} to {lo + 4}"


def _pin2(v):
    return v[:2] + "xxxx" if isinstance(v, str) and re.fullmatch(r"[1-9]\dx{4}|[1-9]\d{2}xxx", v) else WITHHELD


def levels_for(spec, ctrl: SchemaControl | None, coupled: bool) -> list:
    """Generalisation levels of a shown field, finest first; the last level withholds the value."""
    def ident(v):
        return v
    out = [ident]
    if not coupled:
        if spec.disposition == "GENERALIZE" and spec.method == "month":
            out += [_year, _five_years]
        elif spec.disposition == "GENERALIZE" and spec.method == "year":
            out += [_five_years]
        elif spec.disposition == "GENERALIZE" and spec.method == "pin_prefix3":
            out += [_pin2]
        elif ctrl is not None and spec.name in ctrl.hierarchies:
            for m in ctrl.hierarchies[spec.name]:
                out.append(lambda v, m=m: WITHHELD if v is None else m.get(v, WITHHELD))
    out.append(lambda v: WITHHELD)
    return out


def _disp(v) -> str:
    return str(v)


def anonymize_listing(pop: list, listed: list, fields: list, levels: dict, k: int, policy: str, person_key,
                      prewithhold: dict | None = None) -> tuple[dict, dict]:
    """Display text of the `fields` of each listed row such that every displayed combination is shared by at least
    k people of `pop` (the population the listing is drawn from), and the rows displayed with their attributes
    withheld are indistinguishable among at least k people too.

    Full-domain generalisation (one level per field for the whole listing, Datafly-style: the field with the most
    distinct values is coarsened first) while more than k listed rows fail, then per-row withholding of the rest.
    Per-field levels (not per-row recoding) keep the reasoning exact: a displayed combination t is consistent with
    exactly the people whose generalised combination is t. prewithhold: field -> set of values always withheld
    (a group whose count was suppressed in the same response)."""
    stats = {"rows_coarsened": 0, "rows_attributes_withheld": 0, "fields_generalised": 0, "fields_dropped": 0}
    if not fields or not listed or policy == "alias_only":
        return {}, stats
    pre = prewithhold or {}
    people = {}
    for r in pop:
        people.setdefault(person_key(r), r)
    pop_rows = list(people.values())
    lv = {f: 0 for f in fields}

    def val(r, f, level):
        v = r.values.get(f)
        if f in pre and v in pre[f]:
            return WITHHELD
        return levels[f][level](v)

    def gen(r):
        return tuple(val(r, f, lv[f]) for f in fields)

    def counts():
        return Counter(gen(p) for p in pop_rows)

    c = counts()
    if policy == "coarsen":
        while True:
            failing = sum(1 for r in listed if c[gen(r)] < k)
            if failing <= k:
                break
            raisable = [f for f in fields if lv[f] < len(levels[f]) - 1]
            if not raisable:
                break
            f = max(raisable, key=lambda f: (len({val(p, f, lv[f]) for p in pop_rows}), -fields.index(f)))
            lv[f] += 1
            c = counts()
    tup = {id(r): gen(r) for r in listed}
    withheld = {t for t in tup.values() if c[t] < k}
    while withheld:
        visible = set(tup.values()) - withheld
        if not visible:
            break
        cand = sum(n for t, n in c.items() if t not in visible)
        if cand >= k:
            break
        withheld.add(min(visible, key=lambda t: (c[t], repr(t))))
    base = {id(r): tuple(val(r, f, 0) for f in fields) for r in listed}
    show = {}
    for r in listed:
        t = tup[id(r)]
        if t in withheld:
            show[id(r)] = f"attributes withheld (fewer than {k} share them)"
            stats["rows_attributes_withheld"] += 1
            continue
        if t != base[id(r)]:
            stats["rows_coarsened"] += 1
        show[id(r)] = "; ".join(f"{f}: {_disp(v)}" for f, v in zip(fields, t, strict=True)
                                if v is not None and v is not WITHHELD)
    stats["fields_generalised"] = sum(1 for f in fields if 0 < lv[f] < len(levels[f]) - 1)
    stats["fields_dropped"] = sum(1 for f in fields if lv[f] == len(levels[f]) - 1)
    return show, stats


# =============================================================================================================
# differencing: a per-conversation log of filter predicates
# =============================================================================================================

@dataclass
class _Scope:
    specs: list = field(default_factory=list)    # filter specs only: ((field, op, sealed value, order), ...)
    total_shown: bool = False                    # the provider saw the record total in this scope
    t: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


class QueryLog:
    """Filter predicates of the roster queries of each conversation (tenant, app, purpose, X-PGW-Conversation-Id;
    requests without a conversation id share one scope per app and purpose). In memory, per gateway process,
    bounded: at most `max_scopes` scopes (least recently used dropped), `max_queries` predicates per scope (oldest
    dropped), idle scopes expire after `ttl_s`. Stores the filter specification only: field names, operators and
    sealed filter values (enumeration members, generalised formats), never a record value or a result.

    A query is refused when its result set, evaluated on the current input, differs from an earlier query's
    result set (or, once the record total was shown, from that set's complement) by 1..k-1 people: printing both
    counts would let the provider subtract them."""

    def __init__(self, max_scopes: int, max_queries: int, ttl_s: float):
        self.max_scopes, self.max_queries, self.ttl_s = max_scopes, max_queries, ttl_s
        self._scopes: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def _scope(self, key) -> _Scope:
        now = time.monotonic()
        with self._lock:
            for old in [s for s, v in self._scopes.items() if now - v.t > self.ttl_s]:
                del self._scopes[old]
            sc = self._scopes.get(key)
            if sc is None:
                sc = self._scopes[key] = _Scope()
                while len(self._scopes) > self.max_scopes:
                    self._scopes.popitem(last=False)
            self._scopes.move_to_end(key)
            sc.t = now
            return sc

    def __len__(self) -> int:
        return len(self._scopes)

    def check_and_record(self, key, flt: list, rows: list, matched: frozenset, total_shown: bool, k: int,
                         match) -> int:
        """Raise DisclosureRefused if the query must be refused; else record it. Returns the number of earlier
        predicates compared. `match(row, flt)` -> 1 matched, 0 not matched, -1 a filtered value missing."""
        sc = self._scope(key)
        spec = tuple((n, op, val) for n, op, val, _o in flt)
        with sc.lock:
            everyone = frozenset(r.index for r in rows)
            known_total = sc.total_shown or total_shown
            compared = 0
            prior_sets = []
            for prev in sc.specs:
                if tuple((n, op, val) for n, op, val, _o in prev) == spec:
                    continue
                p = frozenset(r.index for r in rows if match(r, prev) == 1)
                prior_sets.append(p)
                if known_total:
                    # the total and an earlier count give that query's complement: it must not be small either
                    rest = len(everyone - p)
                    if 0 < rest < k:
                        raise DisclosureRefused(f"query refused: with the record total, an earlier query in this "
                                                f"conversation would single out fewer than {k} records")
                    prior_sets.append(everyone - p)
            if sc.total_shown:
                prior_sets.append(everyone)
            for p in prior_sets:
                compared += 1
                a, b = len(matched - p), len(p - matched)
                if 0 < a < k or 0 < b < k:
                    raise DisclosureRefused(f"query refused: its result differs from an earlier query in this "
                                            f"conversation by fewer than {k} records")
            if all(tuple((n, op, val) for n, op, val, _o in prev) != spec for prev in sc.specs):
                sc.specs.append(list(flt))
                del sc.specs[:-self.max_queries]
            sc.total_shown = known_total
            return compared
