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
  * differencing: a durable query history per (tenant, app, purpose, dataset) -- NOT per conversation: the adversary
    is the external provider, who sees every request of the tenant, and conversation ids are caller-chosen. It keeps
    each answered query's filter predicate and, for each count it revealed, the set of keyed record hashes behind it
    (no personal data in clear), and refuses a query any of whose counts differs from any earlier one of the scope
    by 1..k-1 records (HistoryStore below). Full history refuses; store failure fails closed (503).
  * optional deterministic rounding of published roster counts to a sealed base (off by default).

Deterministic thresholds, not differential privacy: these outputs are counts and lists a human reads and acts on
(an exception list must name the right people), so calibrated noise would make them wrong; a deterministic threshold
with complementary suppression is the standard tool for published tables (e.g. tau-ARGUS practice). DP (OpenDP) and
ARX-style optimal anonymisation were considered and are not built; DP remains an option for released statistics.

What this does NOT protect against is stated in SECURITY_BOUNDARY.md §12: the provider's background knowledge,
attribute disclosure when all k people in a group share the same value (homogeneity; l-diversity is not enforced),
differencing across different tenants, apps, purposes or datasets, combinations of three or more counts that no
pair reveals, and reasoning about the suppression algorithm itself.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
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
DEFAULT_MAX_QUERIES = 1000          # answered queries kept per history scope (sealed; full -> refuse)
MAX_QUERIES_CEILING = 20_000
DEFAULT_MAX_KEYS = 5_000_000        # stored record hashes per scope (16 bytes each: ~80 MB)
MAX_KEYS_CEILING = 50_000_000
DEFAULT_ROUNDING_BASE = 5
_LABEL_BAD = re.compile(r"[\x00-\x1f\x7f]")
log = logging.getLogger("pgw.disclosure")


class DisclosureRefused(ValueError):
    """A query refused by disclosure control. The message has no value; `code` is the reason (differencing,
    history_full, history_scopes_full); `report` is the records section of the transparency report."""

    def __init__(self, message: str, report: dict | None = None, code: str = "differencing"):
        super().__init__(message)
        self.report = report
        self.code = code


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
    max_queries: int              # answered queries per history scope; full -> refuse (never evict)
    max_keys: int                 # stored record hashes per history scope; full -> refuse
    rounding: int                 # 0: published counts exact; else the sealed rounding base
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
                "rounding", "schemas", "note"}, name)
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
    _keys(dif, {"enabled", "max_queries_per_scope", "max_stored_keys_per_scope", "max_scopes"},
          f"{name}.differencing")
    enabled = dif.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SchemaError(f"{name}.differencing.enabled: true or false")
    rnd = raw.get("rounding") or {}
    _keys(rnd, {"enabled", "base"}, f"{name}.rounding")
    if not isinstance(rnd.get("enabled", False), bool):
        raise SchemaError(f"{name}.rounding.enabled: true or false")
    base = _posint(rnd.get("base", DEFAULT_ROUNDING_BASE), f"{name}.rounding.base", 2, 100)
    schemas = raw.get("schemas") or {}
    _keys(schemas, set(schemas), f"{name}.schemas")
    for sid, block in schemas.items():
        _keys(block, {"quasi_identifiers", "hierarchies", "note"}, f"{name}.schemas.{sid}")
    return Config(version=version, k=k, override=just, listing=listing, differencing=enabled,
                  max_scopes=_posint(dif.get("max_scopes", 10_000), f"{name}.differencing.max_scopes", 1, 10 ** 6),
                  max_queries=_posint(dif.get("max_queries_per_scope", DEFAULT_MAX_QUERIES),
                                      f"{name}.differencing.max_queries_per_scope", 1, MAX_QUERIES_CEILING),
                  max_keys=_posint(dif.get("max_stored_keys_per_scope", DEFAULT_MAX_KEYS),
                                   f"{name}.differencing.max_stored_keys_per_scope", 1, MAX_KEYS_CEILING),
                  rounding=base if rnd.get("enabled", False) else 0, schemas=dict(schemas))


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
    """Bound per RECORDS purpose at startup: the effective k, the listing policy, per-schema controls, whether the
    durable query history (differencing) applies, and the rounding base (0 = exact counts)."""
    cfg: Config
    k: int
    listing: str
    schemas: dict                 # schema id -> SchemaControl
    differencing: bool = True
    rounding: int = 0


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
# rounding of published counts (sealed; off by default, see docs/HRMS_CONTRACTS.md "Small groups")
# =============================================================================================================

def rounded(n: int, base: int) -> str:
    """Deterministic rounding of a published count to the sealed base: 0 stays "0"; any other count prints
    "about B*round_half_up(n/B)", never below one base (so a non-zero count never reads as zero). The same true count
    always prints the same text, in every response and every cell."""
    n = int(n)
    if n <= 0 or base <= 1:
        return str(max(n, 0))
    r = base * ((2 * n + base) // (2 * base))     # round half up, integers only
    return f"about {max(r, base)}"


# =============================================================================================================
# differencing: durable query history per (tenant, app, purpose, dataset)
# =============================================================================================================
#
# Threat model: the adversary is the external LLM provider. It sees every outbound request of the tenant, from every
# conversation, user and day. Conversation ids are chosen by the caller and are therefore NOT a security boundary.
# The history scope is (tenant, app, purpose, dataset identity); the dataset identity is the source schema id plus
# the contract's optional sealed `records.dataset` label, never the export's content hash, so a refreshed export
# shares the history of the export it refreshes.
#
# For every answered roster query the store keeps the filter predicate and, for every count the response revealed
# (matched set, record total, its complement, missing / did-not-match, each group), the SET OF RECORD KEYS behind
# that count. A record key is a keyed hash: HMAC-SHA256(scope key, stable record identity), truncated to 16 bytes.
# The scope key is HMAC(tenant key "disclosure-history" from the gateway key store, scope id); the key id is pinned
# per scope with a key-check value, so a rotation keeps history comparable and a wrong or retired key fails closed.
# The store therefore holds no name, username or identifier in clear, and its hashes cannot be linked across scopes
# or tenants, or recomputed without the tenant key.
#
# A new query is refused when one of its exactly printed counts covers a set of records that differs from ANY
# earlier exactly printed count's set of the scope (any conversation, any day, any export) by 1..k-1 record keys
# (symmetric difference): subtracting the two numbers would give the count of 1..k-1 identifiable records. Not
# compared: a count printed as a suppression marker (its value is an interval, never pinned by one subtraction; the
# within-response audit covers it), and a pair of two whole-export sets (record totals / unfiltered matches: no
# quasi-identifier predicate characterises their difference, which is only "who joined or left"). Full history ->
# refuse (never evict). Store errors -> HistoryUnavailable (503, nothing sent).

KEY_BYTES = 16
HISTORY_DB_NAME = "disclosure_history.sqlite"
KEY_PURPOSE = "disclosure-history"           # key store purpose label of the history key (pgw.keys)
_KCV = b"pgw-disclosure-history/kcv/v1"
_SCOPE = b"pgw-disclosure-history/scope/v1\x00"
REFUSE_DIFFERENCING = "differencing"
REFUSE_FULL = "history_full"
REFUSE_SCOPES = "history_scopes_full"
DATASET_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


class HistoryUnavailable(Exception):
    """The query history cannot be read or written, or its key is unavailable or does not match: the request is
    refused (503) and nothing is sent. The message is a content-free reason."""


@dataclass(frozen=True)
class RevealedSet:
    """The record keys behind one count a response reveals. exact: the count is printed as a number (False: a
    suppression marker; such sets are neither compared nor stored). whole: the set is a whole export (the record
    total, or an unfiltered match)."""
    kind: str
    keys: frozenset
    exact: bool
    whole: bool = False


@dataclass(frozen=True)
class HistoryScope:
    """Who asks (bound by the gateway from the authenticated app, never from a request header): the store, tenant,
    app, purpose and `keyfn(key_id or None) -> (key_id, key bytes)` over the gateway key store."""
    store: "HistoryStore"
    tenant: str
    app: str
    purpose: str
    keyfn: object


def dataset_id(schema_id: str, label: str | None) -> str:
    """Dataset identity of a history scope: the source schema id plus the contract's sealed dataset label."""
    return schema_id if not label else f"{schema_id}/{label}"


def keyset(rows, subkey: bytes, ident, cache: dict) -> frozenset:
    """Keyed record keys of `rows` (in order). A record's key is HMAC(subkey, identity); a repeated identity in the
    same set gets HMAC(subkey, first || n), so duplicates (and records without an identity) count with multiplicity.
    `cache` (row index -> first HMAC) avoids rehashing a row that is in several sets."""
    seen: Counter = Counter()
    out = set()
    for r in rows:
        base = cache.get(r.index)
        if base is None:
            base = cache[r.index] = hmac.digest(subkey, ident(r), "sha256")
        n = seen[base]
        seen[base] = n + 1
        out.add(base[:KEY_BYTES] if n == 0 else hmac.digest(subkey, base + n.to_bytes(4, "big"), "sha256")[:KEY_BYTES])
    return frozenset(out)


def _digest(keys: frozenset) -> bytes:
    return hashlib.sha256(b"".join(sorted(keys))).digest()


def _unpack(blob: bytes) -> set:
    return {blob[i:i + KEY_BYTES] for i in range(0, len(blob), KEY_BYTES)}


class HistoryMetrics:
    """Operator counters (never in pgw_report, never a value): answered, refused (differencing / capacity) and store
    failures since start. `snapshot()` adds the stored scopes and queries."""

    def __init__(self, store: "HistoryStore"):
        self._store = store
        self._lock = threading.Lock()
        self.c = Counter()

    def inc(self, name: str) -> None:
        with self._lock:
            self.c[name] += 1

    def snapshot(self) -> dict:
        with self._lock:
            out = {n: self.c.get(n, 0) for n in ("answered", "refused_differencing", "refused_capacity",
                                                 "store_unavailable")}
        try:
            out.update(self._store.sizes())
        except HistoryUnavailable:
            out["store_unavailable"] += 1
        return out


class HistoryStore:
    """Durable query history (one SQLite file, WAL, synchronous=FULL; the pattern of attest.SqliteReplayStore).
    Shared by every gateway process on a host; check-and-record is one IMMEDIATE transaction, so two processes cannot
    both answer a differencing pair. Any database error raises HistoryUnavailable. Not for network filesystems.

    Complexity of one check: a new revealed set of size n is compared only with stored sets whose size lies in
    [n-k+1, n+k-1] (index on (scope, size); a smaller window cannot hold a symmetric difference below k) and whose
    digest differs (identical sets are skipped without loading them). Each comparison is a set intersection,
    O(|stored set|). Worst case per query: O(sets of the query x stored sets in the window x set size), bounded by the
    sealed max_queries_per_scope and max_stored_keys_per_scope."""

    def __init__(self, path: str):
        import sqlite3
        self._sq = sqlite3
        self.path = path
        self._lock = threading.Lock()
        self.metrics = HistoryMetrics(self)
        try:
            self._db = sqlite3.connect(path, timeout=10.0, isolation_level=None, check_same_thread=False)
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS scope (sid TEXT PRIMARY KEY, tenant TEXT NOT NULL, app TEXT NOT NULL,
                    purpose TEXT NOT NULL, dataset TEXT NOT NULL, key_id TEXT NOT NULL, kcv BLOB NOT NULL,
                    created REAL NOT NULL, queries INTEGER NOT NULL, stored_keys INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS query (sid TEXT NOT NULL, seq INTEGER NOT NULL, ts REAL NOT NULL,
                    predicate TEXT NOT NULL, sets TEXT NOT NULL, PRIMARY KEY (sid, seq));
                CREATE TABLE IF NOT EXISTS rset (sid TEXT NOT NULL, digest BLOB NOT NULL, n INTEGER NOT NULL,
                    whole INTEGER NOT NULL, keys BLOB NOT NULL, PRIMARY KEY (sid, digest));
                CREATE INDEX IF NOT EXISTS rset_size ON rset(sid, n);
                CREATE TABLE IF NOT EXISTS admin_log (ts REAL NOT NULL, action TEXT NOT NULL, sid TEXT,
                    tenant TEXT, app TEXT, purpose TEXT, dataset TEXT, queries INTEGER, reason TEXT NOT NULL);
            """)
        except sqlite3.Error as e:
            raise HistoryUnavailable(type(e).__name__) from None

    @staticmethod
    def scope_id(tenant: str, app: str, purpose: str, dataset: str) -> str:
        return hashlib.sha256(json.dumps([tenant, app, purpose, dataset]).encode()).hexdigest()

    def sizes(self) -> dict:
        try:
            with self._lock:
                r = self._db.execute("SELECT COUNT(*), COALESCE(SUM(queries), 0), COALESCE(MAX(queries), 0) "
                                     "FROM scope").fetchone()
        except self._sq.Error as e:
            raise HistoryUnavailable(type(e).__name__) from None
        return {"scopes": r[0], "queries": r[1], "max_queries_in_a_scope": r[2]}

    def _scope_key(self, keyfn, sid: str, key_id: str | None) -> tuple[str, bytes, bytes]:
        try:
            kid, master = keyfn(key_id)
        except Exception:   # noqa: BLE001 - KeyUnavailable or any key store failure: fail closed, never another key
            raise HistoryUnavailable("key_unavailable") from None
        sub = hmac.digest(master, _SCOPE + sid.encode(), "sha256")
        return kid, sub, hmac.digest(sub, _KCV, "sha256")[:KEY_BYTES]

    def check_and_record(self, scope: HistoryScope, dataset: str, predicate: dict, build_sets, k: int,
                         cfg: Config) -> dict:
        """Refuse (DisclosureRefused with .code) or record one answered query. build_sets(subkey) -> [RevealedSet]
        is called inside the transaction with the scope's key. Returns counts for the report."""
        sid = self.scope_id(scope.tenant, scope.app, scope.purpose, dataset)
        sq = self._sq
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    out = self._check_and_record(scope, dataset, sid, predicate, build_sets, k, cfg)
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
                self._db.execute("COMMIT")
            except DisclosureRefused as e:
                self.metrics.inc("refused_capacity" if e.code in (REFUSE_FULL, REFUSE_SCOPES)
                                 else "refused_differencing")
                if e.code in (REFUSE_FULL, REFUSE_SCOPES):
                    log.error("event='disclosure_history_full' code=%s tenant=%r app=%r purpose=%r dataset=%r",
                              e.code, scope.tenant, scope.app, scope.purpose, dataset)
                raise
            except HistoryUnavailable:
                self.metrics.inc("store_unavailable")
                raise
            except sq.Error as e:
                self.metrics.inc("store_unavailable")
                raise HistoryUnavailable(type(e).__name__) from None
        self.metrics.inc("answered")
        return out

    def _check_and_record(self, scope, dataset, sid, predicate, build_sets, k, cfg) -> dict:
        db = self._db
        row = db.execute("SELECT key_id, kcv, queries, stored_keys FROM scope WHERE sid = ?", (sid,)).fetchone()
        if row is None:
            if db.execute("SELECT COUNT(*) FROM scope").fetchone()[0] >= cfg.max_scopes:
                raise DisclosureRefused("query refused: the disclosure-control history has no room for another "
                                        "scope (an operator must review it)", code=REFUSE_SCOPES)
            kid, sub, kcv = self._scope_key(scope.keyfn, sid, None)
            queries = stored = 0
        else:
            kid, sub, kcv = self._scope_key(scope.keyfn, sid, row[0])
            if not hmac.compare_digest(kcv, row[1]):
                log.error("event='disclosure_history_key_mismatch' tenant=%r app=%r purpose=%r dataset=%r",
                          scope.tenant, scope.app, scope.purpose, dataset)
                raise HistoryUnavailable("key_mismatch")
            queries, stored = row[2], row[3]
        if queries >= cfg.max_queries:
            raise DisclosureRefused("query refused: the disclosure-control history of this data is full (an operator "
                                    "must review and reset it)", code=REFUSE_FULL)
        # this query's exactly printed, non-empty sets, deduplicated by content (whole if any copy is whole)
        new: dict = {}
        for s in build_sets(sub):
            if not s.keys or not s.exact:
                continue
            d = _digest(s.keys)
            o = new.get(d)
            new[d] = s if o is None else RevealedSet(o.kind, o.keys, True, o.whole or s.whole)
        known = set()
        for d in new:
            if db.execute("SELECT 1 FROM rset WHERE sid = ? AND digest = ?", (sid, d)).fetchone() is not None:
                known.add(d)
        added = sum(len(s.keys) for d, s in new.items() if d not in known)
        if stored + added > cfg.max_keys:
            raise DisclosureRefused("query refused: the disclosure-control history of this data is full (an operator "
                                    "must review and reset it)", code=REFUSE_FULL)
        compared = 0
        loaded: dict = {}
        for d, s in new.items():
            n = len(s.keys)
            for od, on, owhole in db.execute("SELECT digest, n, whole FROM rset WHERE sid = ? AND n BETWEEN ? AND ?",
                                             (sid, n - k + 1, n + k - 1)).fetchall():
                if od == d or (s.whole and owhole):
                    continue
                other = loaded.get(od)
                if other is None:
                    other = loaded[od] = _unpack(db.execute("SELECT keys FROM rset WHERE sid = ? AND digest = ?",
                                                            (sid, od)).fetchone()[0])
                compared += 1
                diff = n + on - 2 * len(s.keys & other)
                if 0 < diff < k:
                    raise DisclosureRefused(f"query refused: a count it would print differs from a count already "
                                            f"given for this data (in any conversation, on any day) by fewer than {k} "
                                            "records", code=REFUSE_DIFFERENCING)
        # record
        now = time.time()
        if row is None:
            db.execute("INSERT INTO scope (sid, tenant, app, purpose, dataset, key_id, kcv, created, queries, "
                       "stored_keys) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
                       (sid, scope.tenant, scope.app, scope.purpose, dataset, kid, kcv, now))
        for d, s in new.items():
            if d in known:
                db.execute("UPDATE rset SET whole = MAX(whole, ?) WHERE sid = ? AND digest = ?", (int(s.whole), sid, d))
            else:
                db.execute("INSERT INTO rset (sid, digest, n, whole, keys) VALUES (?, ?, ?, ?, ?)",
                           (sid, d, len(s.keys), int(s.whole), b"".join(sorted(s.keys))))
        sets = [[s.kind, d.hex()[:16]] for d, s in new.items()]
        db.execute("INSERT INTO query (sid, seq, ts, predicate, sets) VALUES (?, ?, ?, ?, ?)",
                   (sid, queries + 1, now, json.dumps(predicate, sort_keys=True), json.dumps(sets)))
        db.execute("UPDATE scope SET queries = ?, stored_keys = ? WHERE sid = ?", (queries + 1, stored + added, sid))
        return {"queries_compared": compared, "history_queries": queries + 1}

    # --- operator actions (python -m pgw.product.disclosure) --------------------------------------------------------

    def list_scopes(self) -> list[dict]:
        try:
            with self._lock:
                rows = self._db.execute("SELECT tenant, app, purpose, dataset, key_id, created, queries, stored_keys "
                                        "FROM scope ORDER BY tenant, app, purpose, dataset").fetchall()
        except self._sq.Error as e:
            raise HistoryUnavailable(type(e).__name__) from None
        return [dict(zip(("tenant", "app", "purpose", "dataset", "key_id", "created", "queries", "stored_keys"), r,
                         strict=True)) for r in rows]

    def reset(self, tenant: str, app: str, purpose: str, dataset: str, reason: str) -> int:
        """Delete one scope's history (a deliberate, logged admin action). Afterwards the provider's earlier answers
        for this scope are no longer compared: a differencing pair split across the reset is answered. Returns the
        number of queries dropped; the admin_log row (and the log line) records who-what-why without any value."""
        if len(reason.strip()) < 20:
            raise ValueError("a reset needs a written reason (20+ characters)")
        sid = self.scope_id(tenant, app, purpose, dataset)
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    r = self._db.execute("SELECT queries FROM scope WHERE sid = ?", (sid,)).fetchone()
                    n = r[0] if r else 0
                    self._db.execute("DELETE FROM rset WHERE sid = ?", (sid,))
                    self._db.execute("DELETE FROM query WHERE sid = ?", (sid,))
                    self._db.execute("DELETE FROM scope WHERE sid = ?", (sid,))
                    self._db.execute("INSERT INTO admin_log (ts, action, sid, tenant, app, purpose, dataset, queries, "
                                     "reason) VALUES (?, 'reset', ?, ?, ?, ?, ?, ?, ?)",
                                     (time.time(), sid, tenant, app, purpose, dataset, n, reason.strip()))
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
                self._db.execute("COMMIT")
            except self._sq.Error as e:
                raise HistoryUnavailable(type(e).__name__) from None
        log.warning("event='disclosure_history_reset' tenant=%r app=%r purpose=%r dataset=%r queries_dropped=%d",
                    tenant, app, purpose, dataset, n)
        return n

    def admin_log(self) -> list[dict]:
        rows = self._db.execute("SELECT ts, action, tenant, app, purpose, dataset, queries, reason FROM admin_log "
                                "ORDER BY ts").fetchall()
        return [dict(zip(("ts", "action", "tenant", "app", "purpose", "dataset", "queries", "reason"), r,
                         strict=True)) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _main(argv: list[str]) -> int:
    """python -m pgw.product.disclosure history list  --db PATH
       python -m pgw.product.disclosure history reset --db PATH --tenant T --app A --purpose P --dataset D
                                                      --reason "ticket and why (20+ characters)"
    Run with the gateway stopped or running (the reset is one transaction). Prints counts and labels only."""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m pgw.product.disclosure")
    ap.add_argument("group", choices=["history"])
    ap.add_argument("command", choices=["list", "reset", "log"])
    ap.add_argument("--db", required=True)
    for a in ("tenant", "app", "purpose", "dataset", "reason"):
        ap.add_argument(f"--{a}")
    a = ap.parse_args(argv)
    if not Path(a.db).is_file():
        ap.error("--db: no such history file")
    store = HistoryStore(a.db)
    if a.command == "list":
        for s in store.list_scopes():
            print(" ".join(f"{k}={v}" for k, v in s.items()))
    elif a.command == "log":
        for s in store.admin_log():
            print(" ".join(f"{k}={v!r}" for k, v in s.items()))
    else:
        if not all((a.tenant, a.app, a.purpose, a.dataset, a.reason)):
            ap.error("reset needs --tenant --app --purpose --dataset --reason")
        n = store.reset(a.tenant, a.app, a.purpose, a.dataset, a.reason)
        print(f"reset tenant={a.tenant} app={a.app} purpose={a.purpose} dataset={a.dataset} queries_dropped={n}")
    store.close()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
