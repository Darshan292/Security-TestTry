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
  * differencing: a durable audit history per (tenant, dataset) -- NOT per conversation, app or purpose: the
    adversary is the external provider, who sees every request of the tenant, and conversation ids are caller-chosen.
    It keeps every cell any answer revealed (printed numbers and suppression markers) as the set of keyed record
    hashes behind it and audits the whole scope as linear constraints over record sets (pgw.product.setaudit): no
    group of 1..k-1 records may have its size determined by any combination of answers, and no suppressed cell may be
    pinned by the markers' bounds. A response that would break this has more secondary cells suppressed, or is
    refused (HistoryStore below). Per-caller rate limits on new questions; full capacity refuses only new
    information; store failure fails closed (503); a missing or replaced history stops startup.
  * optional deterministic rounding of published roster counts to a sealed base (off by default).

Deterministic thresholds, not differential privacy: these outputs are counts and lists a human reads and acts on
(an exception list must name the right people), so calibrated noise would make them wrong; a deterministic threshold
with complementary suppression is the standard tool for published tables (e.g. tau-ARGUS practice). DP (OpenDP) and
ARX-style optimal anonymisation were considered and are not built; DP remains an option for released statistics.

What this does NOT protect against is stated in SECURITY_BOUNDARY.md §12: the provider's background knowledge,
attribute disclosure when all k people in a group share the same value (homogeneity; l-diversity is not enforced),
differencing across different tenants or datasets (or apps / purposes a sealed config separates), and reasoning about
the suppression algorithm itself.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import re
import sys
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import setaudit
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
DEFAULT_MAX_KEYS = 2_000_000        # stored record hashes per scope (16 bytes each: ~32 MB on disk)
MAX_KEYS_CEILING = 50_000_000
DEFAULT_MAX_CELLS = 100_000         # distinct revealed record sets per scope
DEFAULT_MAX_ATOMS = 20_000          # atoms of the audit partition per scope (bounds the audit's cost)
DEFAULT_QUERY_LOG = 10_000          # query-log rows kept per scope (metadata only; older rows are compacted)
# Sealed rate limits (differencing.per_caller). All counters live in the history database (one atomic increment
# inside the check-and-record transaction), so they are shared by every gateway process on the host. Fixed windows
# (calendar minute / UTC day): a caller can use up to twice a minute limit around a boundary.
DEFAULT_PER_MINUTE = 20             # NEW (information-adding) queries per APP per minute
DEFAULT_PER_DAY = 500               # new queries per APP per day
DEFAULT_USER_PER_MINUTE = 5         # per (app, end user): the fair share, well below the app and scope capacity
DEFAULT_USER_PER_DAY = 100
DEFAULT_SCOPE_PER_MINUTE = 40       # per scope (all apps and users of the tenant's dataset together)
DEFAULT_SCOPE_PER_DAY = 1000
DEFAULT_REFUSED_PER_MINUTE = 10     # REFUSED queries are metered too (probing is not free): per (app, end user)
DEFAULT_REFUSED_PER_DAY = 100
DEFAULT_REFUSED_APP_PER_MINUTE = 40 # ... and per app
DEFAULT_REFUSED_APP_PER_DAY = 400
STORE_TIMEOUT_S = 3.0               # bound on every wait for the database / a lock; then fail closed (HistoryUnavailable)
ANONYMOUS = b"*"                    # an app that asserts no end user is ONE bucket (its per-app limits apply)
DEFAULT_ROUNDING_BASE = 5
_LABEL_BAD = re.compile(r"[\x00-\x1f\x7f]")
log = logging.getLogger("pgw.disclosure")


class DisclosureRefused(ValueError):
    """A query refused by disclosure control. The message has no value; `code` is the reason (differencing,
    history_full, history_scopes_full); `report` is the records section of the transparency report."""

    def __init__(self, message: str, report: dict | None = None, code: str = "differencing",
                 retry_after: int | None = None):
        super().__init__(message)
        self.report = report
        self.code = code
        self.retry_after = retry_after       # seconds, for a rate-limit refusal (HTTP Retry-After)


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
    max_keys: int                 # stored record hashes per history scope; full -> new information refused
    rounding: int                 # 0: published counts exact; else the sealed rounding base
    schemas: dict                 # schema id -> raw block (checked against the schema at bind time)
    max_cells: int = DEFAULT_MAX_CELLS
    max_atoms: int = DEFAULT_MAX_ATOMS
    query_log: int = DEFAULT_QUERY_LOG
    per_minute: int = DEFAULT_PER_MINUTE
    per_day: int = DEFAULT_PER_DAY
    audit_budget: int = setaudit.DEFAULT_BUDGET
    user_per_minute: int = DEFAULT_USER_PER_MINUTE
    user_per_day: int = DEFAULT_USER_PER_DAY
    scope_per_minute: int = DEFAULT_SCOPE_PER_MINUTE
    scope_per_day: int = DEFAULT_SCOPE_PER_DAY
    refused_per_minute: int = DEFAULT_REFUSED_PER_MINUTE
    refused_per_day: int = DEFAULT_REFUSED_PER_DAY
    refused_app_per_minute: int = DEFAULT_REFUSED_APP_PER_MINUTE
    refused_app_per_day: int = DEFAULT_REFUSED_APP_PER_DAY
    audit_max_ms: int = setaudit.DEFAULT_MAX_MS   # wall-clock bound of one response's audit (all its trials)
    separate_apps: bool = False   # history scope per app (default: shared by every app of the tenant)
    separate_purposes: bool = False   # history scope per purpose (default: shared by every RECORDS purpose)

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
    _keys(dif, {"enabled", "scope", "max_scopes", "max_stored_keys_per_scope", "max_cells_per_scope",
                "max_atoms_per_scope", "query_log_per_scope", "per_caller", "audit_budget", "audit_max_ms"},
          f"{name}.differencing")
    enabled = dif.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SchemaError(f"{name}.differencing.enabled: true or false")
    sc = dif.get("scope") or {}
    _keys(sc, {"separate_apps", "separate_purposes", "justification"}, f"{name}.differencing.scope")
    for flag in ("separate_apps", "separate_purposes"):
        if not isinstance(sc.get(flag, False), bool):
            raise SchemaError(f"{name}.differencing.scope.{flag}: true or false")
    if (sc.get("separate_apps") or sc.get("separate_purposes")) and len(str(sc.get("justification", "")).strip()) < 20:
        raise SchemaError(f"{name}.differencing.scope: separating histories needs a written justification "
                          "(20+ characters): the provider sees every app and purpose of the tenant")
    pc = dif.get("per_caller") or {}
    _keys(pc, {"new_queries_per_minute", "new_queries_per_day", "new_queries_per_user_per_minute",
               "new_queries_per_user_per_day", "new_queries_per_scope_per_minute", "new_queries_per_scope_per_day",
               "refused_per_user_per_minute", "refused_per_user_per_day", "refused_per_app_per_minute",
               "refused_per_app_per_day"}, f"{name}.differencing.per_caller")
    rnd = raw.get("rounding") or {}
    _keys(rnd, {"enabled", "base"}, f"{name}.rounding")
    if not isinstance(rnd.get("enabled", False), bool):
        raise SchemaError(f"{name}.rounding.enabled: true or false")
    base = _posint(rnd.get("base", DEFAULT_ROUNDING_BASE), f"{name}.rounding.base", 2, 100)
    schemas = raw.get("schemas") or {}
    _keys(schemas, set(schemas), f"{name}.schemas")
    for sid, block in schemas.items():
        _keys(block, {"quasi_identifiers", "hierarchies", "record_key", "note"}, f"{name}.schemas.{sid}")
    w = f"{name}.differencing"
    return Config(version=version, k=k, override=just, listing=listing, differencing=enabled,
                  max_scopes=_posint(dif.get("max_scopes", 10_000), f"{w}.max_scopes", 1, 10 ** 6),
                  max_keys=_posint(dif.get("max_stored_keys_per_scope", DEFAULT_MAX_KEYS),
                                   f"{w}.max_stored_keys_per_scope", 1, MAX_KEYS_CEILING),
                  rounding=base if rnd.get("enabled", False) else 0, schemas=dict(schemas),
                  max_cells=_posint(dif.get("max_cells_per_scope", DEFAULT_MAX_CELLS), f"{w}.max_cells_per_scope",
                                    1, 10 ** 7),
                  max_atoms=_posint(dif.get("max_atoms_per_scope", DEFAULT_MAX_ATOMS), f"{w}.max_atoms_per_scope",
                                    1, 200_000),
                  query_log=_posint(dif.get("query_log_per_scope", DEFAULT_QUERY_LOG), f"{w}.query_log_per_scope",
                                    1, 10 ** 7),
                  per_minute=_posint(pc.get("new_queries_per_minute", DEFAULT_PER_MINUTE),
                                     f"{w}.per_caller.new_queries_per_minute", 1, 10_000),
                  per_day=_posint(pc.get("new_queries_per_day", DEFAULT_PER_DAY),
                                  f"{w}.per_caller.new_queries_per_day", 1, 10 ** 6),
                  audit_budget=_posint(dif.get("audit_budget", setaudit.DEFAULT_BUDGET), f"{w}.audit_budget",
                                       1000, 100_000_000),
                  **{f: _posint(pc.get(k_, d_), f"{w}.per_caller.{k_}", 1, 10 ** 6) for f, k_, d_ in (
                      ("user_per_minute", "new_queries_per_user_per_minute", DEFAULT_USER_PER_MINUTE),
                      ("user_per_day", "new_queries_per_user_per_day", DEFAULT_USER_PER_DAY),
                      ("scope_per_minute", "new_queries_per_scope_per_minute", DEFAULT_SCOPE_PER_MINUTE),
                      ("scope_per_day", "new_queries_per_scope_per_day", DEFAULT_SCOPE_PER_DAY),
                      ("refused_per_minute", "refused_per_user_per_minute", DEFAULT_REFUSED_PER_MINUTE),
                      ("refused_per_day", "refused_per_user_per_day", DEFAULT_REFUSED_PER_DAY),
                      ("refused_app_per_minute", "refused_per_app_per_minute", DEFAULT_REFUSED_APP_PER_MINUTE),
                      ("refused_app_per_day", "refused_per_app_per_day", DEFAULT_REFUSED_APP_PER_DAY))},
                  audit_max_ms=_posint(dif.get("audit_max_ms", setaudit.DEFAULT_MAX_MS), f"{w}.audit_max_ms",
                                       50, 60_000),
                  separate_apps=bool(sc.get("separate_apps", False)),
                  separate_purposes=bool(sc.get("separate_purposes", False)))


NORM_OPTIONS = ("casefold", "strip_domain", "strip_upn_suffix", "strip_leading_zeros", "strip_trailing_dots")
DEFAULT_NORM = {"casefold": True, "strip_domain": True, "strip_upn_suffix": True, "strip_leading_zeros": False,
                "strip_trailing_dots": True}
_INVISIBLE = {"Cc", "Cf", "Zs", "Zl", "Zp"}
# Characters that render as nothing but are NOT in the categories above (Unicode Default_Ignorable_Code_Point and
# blank-looking symbols): combining grapheme joiner, Hangul fillers (halfwidth form maps here under NFKC), Mongolian
# free variation selectors, variation selectors (incl. the emoji presentation selector FE0F), Braille blank, Khmer
# inherent vowels. They are removed (two spellings of one id become one id); every other character outside the key's
# allowed set REFUSES the dataset (below).
_BLANKS = {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180E, 0x180F, 0x2800, 0x3164, 0xFFA0,
           *range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0)}
# The allowed character set of a record key AFTER normalisation, per sealed key spec (record_key.normalise.<f>.charset).
# There is no confusables table: a Cyrillic/Greek look-alike of a Latin letter is not mapped (a table is never
# complete and a wrong entry merges two people); it is outside the set, so the dataset is refused.
CHARSETS = {"username": re.compile(r"[a-z0-9._-]{1,128}"),             # sAMAccountName / login (the default)
            "numeric": re.compile(r"[0-9]{1,40}"),                      # numeric employee ids
            "email": re.compile(r"[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,190}")}   # when the UPN is the key (suffix kept)
DEFAULT_CHARSET = "username"


class IdentityInvalid(ValueError):
    """A record key is missing, empty, or has a character outside its allowed set. The message holds no value."""


@dataclass(frozen=True)
class RecordKey:
    """The dataset-level record identity of a schema (the differencing history's record key): the fields whose
    normalised values identify a person across exports and across contracts, and per field the normalisation."""
    fields: tuple
    norm: dict                    # field -> {option: bool, "charset": name} (NORM_OPTIONS, CHARSETS)


def normalise_identity(value, opts: dict) -> str:
    """One identity value as the record key sees it: Unicode NFKC, every whitespace / control / format / blank
    character (zero-width joiners, BOM, fillers, variation selectors) removed, then per the sealed options: casefold,
    a Windows domain prefix stripped (CORP\\asha and CORP/asha -> asha), a UPN suffix stripped (asha@corp.example ->
    asha), trailing dots stripped, leading zeros stripped from an all-digit value (only if configured). The result
    MUST then match the key's allowed character set (CHARSETS; default [a-z0-9._-], uppercase too if casefold is
    off) or IdentityInvalid is raised: look-alike letters, combining marks, '%40', quotes and the like are refused,
    never guessed at. Lists are normalised element-wise and sorted."""
    if isinstance(value, (list, tuple)):
        if not value:
            raise IdentityInvalid("record key is empty")
        return json.dumps(sorted(normalise_identity(v, opts) for v in value), ensure_ascii=False)
    if isinstance(value, bool) or value is None or not isinstance(value, (str, int)):
        raise IdentityInvalid("record key has an unsupported type")
    t = unicodedata.normalize("NFKC", str(value))
    t = "".join(ch for ch in t if not ch.isspace() and unicodedata.category(ch) not in _INVISIBLE
                and ord(ch) not in _BLANKS)
    if opts.get("casefold"):
        t = unicodedata.normalize("NFKC", t.casefold())
        t = "".join(ch for ch in t if ord(ch) not in _BLANKS)
    charset = opts.get("charset", DEFAULT_CHARSET)
    if charset != "numeric":
        if opts.get("strip_domain") and ("\\" in t or "/" in t):
            t = re.split(r"[\\/]", t)[-1]
        if opts.get("strip_upn_suffix") and charset != "email" and "@" in t:
            t = t.split("@", 1)[0]
        if opts.get("strip_trailing_dots"):
            t = t.rstrip(".")
    if opts.get("strip_leading_zeros") and t.isdigit():
        t = t.lstrip("0") or "0"
    pat = CHARSETS[charset]
    ok = pat.fullmatch(t) if opts.get("casefold") else pat.fullmatch(t.lower()) and t.isascii()
    if not ok:
        raise IdentityInvalid("record key is empty or has a character outside its allowed set")
    return t


@dataclass(frozen=True)
class SchemaControl:
    qis: tuple                    # quasi-identifier field names (default: every KEEP and GENERALIZE field) + additions
    hierarchies: dict             # field name -> tuple of {value: label} levels (KEEP enumerations)
    record_key: RecordKey | None = None   # None: the schema has no employee identifier (contract label fallback)


def _record_key(block, schema, where: str) -> RecordKey | None:
    """record_key: {fields: [..], normalise: {field: {casefold, strip_domain, strip_upn_suffix,
    strip_leading_zeros}}}. Default: the schema's first employee-identifier field, default normalisation (every
    option on except strip_leading_zeros; charset username)."""
    from ..types import EntityType
    raw = block.get("record_key")
    if raw is None:
        ids = [f.name for f in schema.fields if f.entity is EntityType.EMPLOYEE_ID]
        return RecordKey((ids[0],), {ids[0]: {**DEFAULT_NORM, "charset": DEFAULT_CHARSET}}) if ids else None
    _keys(raw, {"fields", "normalise", "note"}, f"{where}.record_key")
    fields = raw.get("fields")
    if not isinstance(fields, list) or not fields or not all(isinstance(x, str) for x in fields):
        raise SchemaError(f"{where}.record_key.fields: a non-empty list of field names")
    for n in fields:
        sp = schema.spec(n)
        if sp is None or sp.disposition != "ALIAS":
            raise SchemaError(f"{where}.record_key.fields: {n!r} is not an identity (ALIAS) field of the schema")
    norm_raw = raw.get("normalise") or {}
    _keys(norm_raw, set(fields), f"{where}.record_key.normalise")
    norm = {}
    for n in fields:
        o = norm_raw.get(n) or {}
        _keys(o, {*NORM_OPTIONS, "charset"}, f"{where}.record_key.normalise.{n}")
        for opt, v in o.items():
            if opt == "charset":
                if v not in CHARSETS:
                    raise SchemaError(f"{where}.record_key.normalise.{n}.charset: one of {sorted(CHARSETS)}")
            elif not isinstance(v, bool):
                raise SchemaError(f"{where}.record_key.normalise.{n}.{opt}: true or false")
        norm[n] = {**DEFAULT_NORM, "charset": DEFAULT_CHARSET, **o}
    return RecordKey(tuple(dict.fromkeys(fields)), norm)


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
    return SchemaControl(qis=qis, hierarchies=hier, record_key=_record_key(block, schema, where))


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




def rounding_interval(n: int, base: int) -> tuple:
    """The counts a reader cannot tell apart from `n` once it is printed rounded: every m >= 1 with the same text."""
    t = rounded(n, base)
    vals = [m for m in range(max(1, n - 2 * base), n + 2 * base + 1) if rounded(m, base) == t]
    return min(vals), max(vals)


# =============================================================================================================
# differencing: durable audit history per (tenant, dataset)
# =============================================================================================================
#
# Threat model: the adversary is the external LLM provider. It sees every outbound request of the tenant, from every
# conversation, user, app, purpose and day. Conversation ids are chosen by the caller and are NOT a security boundary.
# The history scope is therefore (tenant, dataset identity): shared by every app and every RECORDS purpose of the
# tenant over the same dataset (a second contract over the same data shares it), unless the sealed config separates
# apps or purposes (with a written justification). The dataset identity is the source schema id plus the contract's
# optional sealed `records.dataset` label, never the export's content hash, so a refreshed export shares the history.
#
# For every answered roster query the store keeps EVERY cell the response revealed -- printed numbers AND suppression
# markers -- as the set of keyed record hashes behind it with what the reader learned about its size (exact, or an
# interval), and audits the whole scope with pgw.product.setaudit before anything is sent: no union of record sets of
# 1..k-1 records may have its size determined by the exact counts of the scope (any number of answers, any
# combination), and every suppressed cell and every small atom keeps a verified alternative consistent with every
# interval. A response that would break this first has more of its secondary cells suppressed (record total, missing
# count, group counts: complementary suppression that considers the scope history); if its answer itself (the matched
# count) would break it, it is refused. A record hash is HMAC-SHA256(scope key, normalised record identity)[:16];
# the scope key is HMAC(tenant key "disclosure-history" from the gateway key store, scope id), pinned per scope with a
# key-check value. Predicates are stored only as keyed HMACs and dates only as day numbers.

KEY_BYTES = 16
HISTORY_DB_NAME = "disclosure_history.sqlite"
HWM_NAME = "disclosure_history.hwm.json"
HWM_KIND = "pgw-disclosure-history-high-water"
KEY_PURPOSE = "disclosure-history"           # key store purpose label of the history key (pgw.keys)
SCHEMA_VERSION = "2"
_KCV = b"pgw-disclosure-history/kcv/v1"
_SCOPE = b"pgw-disclosure-history/scope/v2\x00"
_PRED = b"pgw-disclosure-history/predicate/v1\x00"
_CALLER = b"pgw-disclosure-history/caller/v1\x00"
REFUSE_DIFFERENCING = "differencing"
REFUSE_FULL = "history_full"
REFUSE_SCOPES = "history_scopes_full"
REFUSE_RATE = "rate_limited"
REFUSE_KEY = "invalid_record_key"
REFUSE_AUDIT_BUDGET = "audit_budget"
OMIT = (0, None)       # AuditCell.alt: the cell is not revealed at all (nor is any other cell of its `group`)
SHARED = "*"
OPERATOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{1,63}")
DATASET_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


class HistoryUnavailable(Exception):
    """The query history cannot be read or written, or its key is unavailable or does not match: the request is
    refused (503) and nothing is sent. The message is a content-free reason."""


class HistoryIntegrityError(ValueError):
    """At startup: the history database is missing, replaced, older than its recorded high-water mark, or has no
    high-water mark. The gateway does not start; an operator runs `history recover` deliberately (logged)."""


@dataclass(frozen=True)
class AuditCell:
    """One count a response would reveal: its record keys, what the reader learns about its size as the response
    stands (lo == hi: printed exactly; hi None: unbounded), whether it is a whole export, and `alt`: the bounds if
    the history suppresses it as well ((1, None): "suppressed"; OMIT: not printed at all), or None if it may not be
    suppressed (the answer). Cells of one `group` (the attribute classes of one listing) are suppressed together:
    suppressing one sets every cell of the group to its alt."""
    name: str
    keys: frozenset
    lo: int
    hi: int | None
    whole: bool = False
    alt: tuple | None = None
    priority: int = 0
    group: str | None = None


@dataclass(frozen=True)
class HistoryScope:
    """Who asks (bound by the gateway from the authenticated app and the app's signed end-user assertion): the store, tenant,
    app (the rate-limited caller; authenticated by its key), purpose, `keyfn(key_id or None) -> (key_id, key bytes)`
    and `user`: the END USER the app asserts (pgw.enduser: HMAC-bound with the app key, so asserted by the app, not
    authenticated by the gateway) or None. Rate limits apply per (app, user), per app and per scope."""
    store: "HistoryStore"
    tenant: str
    app: str
    purpose: str
    keyfn: object
    user: str | None = None


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


def _unpack(blob: bytes) -> list:
    return [blob[i:i + KEY_BYTES] for i in range(0, len(blob), KEY_BYTES)]


def _day(t: float | None = None) -> int:
    return int((time.time() if t is None else t) // 86400)


class HistoryMetrics:
    """Operator counters (never in pgw_report, never a value) since start. `snapshot()` adds the stored sizes."""

    NAMES = ("answered", "answered_repeat", "suppressed_more", "refused_differencing", "refused_audit_budget",
             "refused_capacity", "refused_rate", "refused_identity", "store_unavailable")

    def __init__(self, store: "HistoryStore"):
        self._store = store
        self._lock = threading.Lock()
        self.c = Counter()

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.c[name] += n

    def snapshot(self) -> dict:
        with self._lock:
            out = {n: self.c.get(n, 0) for n in self.NAMES}
        try:
            out.update(self._store.sizes())
        except HistoryUnavailable:
            out["store_unavailable"] += 1
        return out


# --- high-water mark (PGW_STATE_DIR; the pattern of pgw.gateway.rollback) -------------------------------------------
#
# The mark is {kind, db_id, seq, key_id, mac}: mac = HMAC-SHA256(platform key "disclosure-hwm" from the key store,
# db_id, seq, key_id). The database carries the same MAC over its own (db_id, seq) (meta.seq_mac), and
# pgw.gateway.rollback keeps an independent monotonic copy (disclosure_seq.json). So: a mark or database label cannot be
# forged or re-labelled without the key; restoring the database AND the mark to an older consistent pair is caught
# by the copy. Residual: an attacker holding the key store can forge everything; an attacker who restores the WHOLE state
# directory together with the database to one older consistent state replays validly MACed files (keep a remote
# anchor of the copy, OPERATIONS.md). The cell contents of the database are not themselves authenticated.

HWM_MAC_PURPOSE = "disclosure-hwm"          # platform key purpose (tenant "*") of the mark / sequence MACs
_HWM_TAG = b"pgw-disclosure-history/hwm/v1\x00"
_HWM_KEYS = {"kind", "db_id", "seq"}
_HWM_KEYS_MAC = {"kind", "db_id", "seq", "key_id", "mac"}


def seq_mac(key: bytes, db_id: str, seq: int, key_id: str) -> str:
    return hmac.new(key, _HWM_TAG + f"{db_id}\x00{seq}\x00{key_id}".encode(), "sha256").hexdigest()


def mac_keyfn_for(keys):
    """The MAC key function of the high-water mark from a key store (pgw.pipeline.Keys / pgw.keys.TenantKeyring):
    the PLATFORM key's purpose key. keyfn(None) -> the active key id; keyfn(id) -> that id (KeyUnavailable if retired)."""
    def keyfn(key_id=None):
        kid = key_id or keys.active_id("*")
        return kid, keys.derive("*", HWM_MAC_PURPOSE, kid)
    return keyfn


def read_hwm(state_dir) -> dict | None:
    path = Path(state_dir) / HWM_NAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise HistoryIntegrityError(f"{HWM_NAME} is unreadable; startup refused") from None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    if not (isinstance(doc, dict) and set(doc) in (_HWM_KEYS, _HWM_KEYS_MAC) and doc["kind"] == HWM_KIND
            and isinstance(doc["db_id"], str) and re.fullmatch(r"[0-9a-f]{32}", doc["db_id"])
            and isinstance(doc["seq"], int) and not isinstance(doc["seq"], bool) and doc["seq"] >= 0
            and ("mac" not in doc or (isinstance(doc["mac"], str) and re.fullmatch(r"[0-9a-f]{64}", doc["mac"])
                                      and isinstance(doc["key_id"], str) and 1 <= len(doc["key_id"]) <= 32))):
        raise HistoryIntegrityError(f"{HWM_NAME} is corrupt; startup refused (investigate, then run "
                                    "`python -m pgw.product.disclosure history recover`)")
    return doc


def write_hwm(state_dir, db_id: str, seq: int, mac: tuple | None = None, force: bool = False) -> None:
    """Atomically raise the mark (temp file, fsync, rename, directory fsync) under an exclusive lock (bounded wait:
    TimeoutError); never lowered for the same database unless `force`. mac: (key_id, hex) of seq_mac."""
    import contextlib
    import fcntl
    import os

    from ..gateway import rollback
    d = Path(state_dir)
    lock = os.open(d / (HWM_NAME + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        rollback.flock_timeout(lock, STORE_TIMEOUT_S, fcntl.LOCK_EX)
        try:
            cur = read_hwm(d)
        except HistoryIntegrityError:
            cur = None
        if cur is not None and not force and cur["db_id"] == db_id and cur["seq"] >= seq:
            return
        tmp = d / f".{HWM_NAME}.{os.getpid()}.tmp"
        doc = {"kind": HWM_KIND, "db_id": db_id, "seq": seq}
        if mac is not None:
            doc.update(key_id=mac[0], mac=mac[1])
        data = (json.dumps(doc, sort_keys=True) + "\n").encode()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, d / HWM_NAME)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
        dfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        os.close(lock)


def _secure_file(path: str) -> None:
    """The history file (and its WAL / shared-memory sidecars) 0600, its directory not group/world accessible."""
    import os
    import stat
    p = Path(path)
    d = p.parent
    try:
        st = d.stat()
        if st.st_mode & 0o077 and st.st_uid == os.getuid():
            os.chmod(d, stat.S_IMODE(st.st_mode) & 0o700)
            log.warning("event='disclosure_history_directory_permissions_tightened' mode=0700")
        if d.stat().st_mode & 0o077:
            raise HistoryIntegrityError("the history directory is group- or world-accessible and not owned by the "
                                        "gateway user; startup refused")
        if not p.exists():
            os.close(os.open(p, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600))
        for f in (p, Path(str(p) + "-wal"), Path(str(p) + "-shm")):
            if f.exists() and f.stat().st_mode & 0o077:
                os.chmod(f, 0o600)
    except OSError as e:
        raise HistoryUnavailable(type(e).__name__) from None


class HistoryStore:
    """Durable audit history (one SQLite file, WAL, synchronous=FULL, mode 0600). Shared by every gateway process on
    a host; check-and-record is one IMMEDIATE transaction, so two processes cannot both answer a differencing pair,
    and the rate counters (table `rate`) are incremented in the same transaction, so they are shared by every process
    and atomic. Any database error, and any wait longer than `timeout` seconds (database busy, store lock, mark lock),
    raises HistoryUnavailable: fail closed (503), never an unbounded wait. Not for network filesystems.
    All calls are blocking: the gateway calls them from worker threads only (never on the event loop).

    Startup integrity (state_dir given): the database carries a random id and a commit sequence; the high-water
    mark <state_dir>/disclosure_history.hwm.json records both, and pgw.gateway.rollback keeps an independent monotonic
    copy; with `mac_keyfn` (production: required) the mark, the copy and the database's own sequence are HMACed with a
    platform key from the key store (see the section above). Production refuses to start when the database is
    missing, replaced (other id), older than the mark or the copy, when a MAC fails, or when there is no mark -- except
    on the first start of a fresh state directory (`fresh`: no config high-water mark yet), where all are created
    (logged). Development only warns.

    `live=True` (the gateway) holds a shared lock on <db>.live for its lifetime: the operator's `recover` takes the
    exclusive lock and refuses while any gateway process holds the store.

    Per scope, an in-memory pgw.product.setaudit.Auditor is kept and rebuilt from the stored cells when another
    process has written (scope generation counter)."""

    def __init__(self, path: str, state_dir: str | None = None, production: bool = False, fresh: bool = False,
                 mac_keyfn=None, live: bool = False, timeout: float = STORE_TIMEOUT_S):
        import sqlite3
        self._sq = sqlite3
        self.path = path
        self.state_dir = state_dir
        self.production = production
        self.mac_keyfn = mac_keyfn
        self.timeout = timeout
        self._lock = threading.Lock()
        self.metrics = HistoryMetrics(self)
        self._auditors: dict = {}          # sid -> (generation, Auditor)
        self._live_fd = None
        persistent = path != ":memory:"
        existed = persistent and Path(path).is_file() and Path(path).stat().st_size > 0
        if persistent:
            _secure_file(path)
            if live:
                self._take_live_lock()
        try:
            self._db = sqlite3.connect(path, timeout=timeout, isolation_level=None, check_same_thread=False)
            if persistent:
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            old = self._db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rset'").fetchone()
            if old is not None:
                raise HistoryIntegrityError("the history file has the pre-v2 format (filter values and timestamps in "
                                            "clear); archive it securely and run `history recover --new`")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scope (sid TEXT PRIMARY KEY, tenant TEXT NOT NULL, app TEXT NOT NULL,
                    purpose TEXT NOT NULL, dataset TEXT NOT NULL, key_id TEXT NOT NULL, kcv BLOB NOT NULL,
                    created_day INTEGER NOT NULL, gen INTEGER NOT NULL, cells INTEGER NOT NULL,
                    stored_keys INTEGER NOT NULL, queries INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS cell (ord INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT NOT NULL,
                    digest BLOB NOT NULL, n INTEGER NOT NULL, lo INTEGER NOT NULL, hi INTEGER, whole INTEGER NOT NULL,
                    keys BLOB NOT NULL, UNIQUE (sid, digest));
                CREATE TABLE IF NOT EXISTS query (sid TEXT NOT NULL, seq INTEGER NOT NULL, day INTEGER NOT NULL,
                    predicate BLOB NOT NULL, caller BLOB NOT NULL, new INTEGER NOT NULL, PRIMARY KEY (sid, seq));
                CREATE TABLE IF NOT EXISTS rate (sid TEXT NOT NULL, subject BLOB NOT NULL, kind TEXT NOT NULL,
                    per TEXT NOT NULL, win INTEGER NOT NULL, n INTEGER NOT NULL,
                    PRIMARY KEY (sid, subject, kind, per, win));
                CREATE TABLE IF NOT EXISTS admin_log (day INTEGER NOT NULL, action TEXT NOT NULL, sid TEXT,
                    tenant TEXT, dataset TEXT, detail TEXT NOT NULL, reason TEXT NOT NULL);
            """)
            if "operator" not in {r[1] for r in self._db.execute("PRAGMA table_info(admin_log)")}:
                self._db.execute("ALTER TABLE admin_log ADD COLUMN operator TEXT")
            meta = dict(self._db.execute("SELECT k, v FROM meta").fetchall())
        except sqlite3.Error as e:
            raise HistoryUnavailable(type(e).__name__) from None
        if persistent:
            _secure_file(path)
        self.db_id = meta.get("db_id")
        self._meta = meta
        try:
            self._check_integrity(existed and self.db_id is not None, fresh)
        except BaseException:
            self.close()          # a refused start must not keep the live lock (or the file) open
            raise

    # --- liveness lock (operator recovery refuses while a gateway holds the store) -------------------------------------

    def _live_path(self) -> str:
        return self.path + ".live"

    def _take_live_lock(self) -> None:
        import fcntl
        import os

        from ..gateway import rollback
        fd = os.open(self._live_path(), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            rollback.flock_timeout(fd, self.timeout, fcntl.LOCK_SH)    # blocked only by a running `recover`
        except TimeoutError:
            os.close(fd)
            raise HistoryUnavailable("recovery_in_progress") from None
        self._live_fd = fd

    # --- startup integrity ------------------------------------------------------------------------------------------

    def _mac(self, db_id: str, seq: int) -> tuple | None:
        if self.mac_keyfn is None:
            return None
        try:
            kid, key = self.mac_keyfn(None)
        except Exception:   # noqa: BLE001 - any key store failure: fail closed
            raise HistoryUnavailable("key_unavailable") from None
        return kid, seq_mac(key, db_id, seq, kid)

    def _set_seq(self, seq: int) -> None:
        """Inside a transaction: the commit sequence and its MAC (the database cannot be re-labelled without the key)."""
        m = self._mac(self.db_id, seq)
        rows = [("seq", str(seq))] + ([("seq_mac", m[1]), ("seq_kid", m[0])] if m else [])
        self._db.executemany("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", rows)

    def _init_meta(self) -> None:
        import os
        self.db_id = os.urandom(16).hex()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('db_id', ?), ('schema', ?)",
                                 (self.db_id, SCHEMA_VERSION))
                self._set_seq(0)
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def seq(self) -> int:
        r = self._db.execute("SELECT v FROM meta WHERE k = 'seq'").fetchone()
        return int(r[0]) if r else 0

    def _publish_mark(self, seq: int, force: bool = False) -> None:
        """The state-directory mark and the rollback copy for (db_id, seq): mark first, copy second, so the copy
        never runs ahead of the mark."""
        from ..gateway import rollback
        m = self._mac(self.db_id, seq)
        try:
            write_hwm(self.state_dir, self.db_id, seq, m, force)
            if m is not None:
                rollback.record_disclosure(self.state_dir, {"kind": rollback.DISC_KIND, "db_id": self.db_id,
                                                            "seq": seq, "key_id": m[0], "mac": m[1]}, force)
        except (OSError, TimeoutError, rollback.RollbackError) as e:
            self.metrics.inc("store_unavailable")
            log.error("event='disclosure_history_hwm_write_failed' error_type=%s", type(e).__name__)
            raise HistoryUnavailable("hwm_write_failed") from None

    def _bad_mac(self, doc: dict, what: str) -> str | None:
        if self.mac_keyfn is None:
            return None
        if "mac" not in doc:
            return f"{what} is not authenticated (written by a version without MACs): re-bind it with `history recover`"
        try:
            kid, key = self.mac_keyfn(doc["key_id"])
        except Exception:   # noqa: BLE001
            return f"{what}'s MAC key id is not in the key store"
        if kid != doc["key_id"] or not hmac.compare_digest(seq_mac(key, doc["db_id"], doc["seq"], kid), doc["mac"]):
            return f"{what} failed authentication (forged, or the key changed)"
        return None

    def _check_integrity(self, had_db: bool, fresh: bool) -> None:
        if not self.state_dir or not Path(self.state_dir).is_dir() or self.path == ":memory:":
            if self.db_id is None:
                self._init_meta()
            if self.production:
                raise HistoryIntegrityError("production requires the history high-water mark in an existing state "
                                            "directory (PGW_STATE_DIR) and a persistent history file")
            return
        if self.production and self.mac_keyfn is None:
            raise HistoryIntegrityError("production requires the platform key store for the history high-water mark")
        from ..gateway import rollback
        hwm = read_hwm(self.state_dir)
        try:
            copy = rollback.read_disclosure_copy(self.state_dir)
        except rollback.RollbackError as e:
            raise HistoryIntegrityError(str(e)) from None
        problem = None
        if hwm is None and not had_db:
            if fresh or not self.production:
                self._init_meta()
                if self.production:
                    self._publish_mark(0)
                    log.warning("event='disclosure_history_initialised' first_start_of_state_dir=True")
                return
            problem = ("no disclosure-control history and no high-water mark, but this state directory has started "
                       "before: the history was deleted or never initialised")
        elif hwm is None:
            problem = f"the history database has no high-water mark ({HWM_NAME} deleted)"
        elif not had_db:
            problem = "the history database is missing or empty while its high-water mark exists (deleted or replaced)"
        elif hwm["db_id"] != self.db_id:
            problem = "the history database is not the one the high-water mark records (replaced)"
        elif self.seq() < hwm["seq"]:
            problem = "the history database is older than its high-water mark (restored from an older copy)"
        elif self.mac_keyfn is not None:
            problem = self._bad_mac(hwm, "the high-water mark")
            if problem is None:
                m = self._meta
                own = {"db_id": self.db_id, "seq": self.seq(), "key_id": m.get("seq_kid"), "mac": m.get("seq_mac")}
                problem = self._bad_mac(own, "the database's sequence") if own["mac"] else \
                    "the database's sequence is not authenticated: re-bind it with `history recover`"
            if problem is None:
                if copy is None:
                    problem = f"the rollback copy of the sequence ({rollback.DISC_NAME}) is missing"
                else:
                    problem = self._bad_mac(copy, "the rollback copy of the sequence")
                if problem is None and copy["db_id"] != self.db_id:
                    problem = "the rollback copy of the sequence records another database"
                elif problem is None and (self.seq() < copy["seq"] or hwm["seq"] < copy["seq"]):
                    problem = ("the history database and its high-water mark are older than the rollback copy "
                               "(joint rollback of both files)")
        if problem is None:
            return
        if self.production:
            log.error("event='disclosure_history_integrity_failed' reason=%r", problem)
            raise HistoryIntegrityError(f"disclosure-control history: {problem}; startup refused. Investigate, then "
                                        "run `python -m pgw.product.disclosure history recover --operator ID "
                                        "--reason ...` with the gateway stopped (logged)")
        log.warning("event='disclosure_history_integrity_failed_development_only' reason=%r", problem)
        if self.db_id is None:
            self._init_meta()

    # --- scope ------------------------------------------------------------------------------------------------------

    @staticmethod
    def scope_id(tenant: str, app: str, purpose: str, dataset: str) -> str:
        return hashlib.sha256(json.dumps([tenant, app, purpose, dataset]).encode()).hexdigest()

    @staticmethod
    def scope_labels(scope: HistoryScope, dataset: str, cfg: Config) -> tuple:
        return (scope.tenant, scope.app if cfg.separate_apps else SHARED,
                scope.purpose if cfg.separate_purposes else SHARED, dataset)

    def sizes(self) -> dict:
        try:
            with self._guard(min(self.timeout, 0.25)):     # a health probe must not queue behind a stuck writer
                r = self._db.execute("SELECT COUNT(*), COALESCE(SUM(queries), 0), COALESCE(SUM(cells), 0), "
                                     "COALESCE(MAX(cells), 0) FROM scope").fetchone()
        except self._sq.Error as e:
            raise HistoryUnavailable(type(e).__name__) from None
        return {"scopes": r[0], "queries": r[1], "cells": r[2], "max_cells_in_a_scope": r[3]}

    def _scope_key(self, keyfn, sid: str, key_id: str | None) -> tuple[str, bytes, bytes]:
        try:
            kid, master = keyfn(key_id)
        except Exception:   # noqa: BLE001 - KeyUnavailable or any key store failure: fail closed, never another key
            raise HistoryUnavailable("key_unavailable") from None
        sub = hmac.digest(master, _SCOPE + sid.encode(), "sha256")
        return kid, sub, hmac.digest(sub, _KCV, "sha256")[:KEY_BYTES]

    def _auditor(self, sid: str, gen: int, k: int, cfg: Config) -> setaudit.Auditor:
        hit = self._auditors.get(sid)
        if hit is not None and hit[0] == gen and hit[1].k == k:
            hit[1].budget = cfg.audit_budget
            return hit[1]
        # (contracts sharing a scope with different k rebuild on alternation: correct, only slower)
        a = setaudit.Auditor(k, cfg.audit_budget)
        for digest, lo, hi, whole, blob in self._db.execute(
                "SELECT digest, lo, hi, whole, keys FROM cell WHERE sid = ? ORDER BY ord", (sid,)):
            a.add(digest, _unpack(blob), lo, hi, bool(whole))
        a._normalise()
        self._auditors[sid] = (gen, a)
        return a

    # --- check and record -------------------------------------------------------------------------------------------

    def check_and_record(self, scope: HistoryScope, dataset: str, predicate: dict, build_cells, k: int,
                         cfg: Config) -> dict:
        """Audit one response against the scope's whole history and record it, or refuse (DisclosureRefused with
        .code). build_cells(subkey) -> [AuditCell] is called inside the transaction with the scope's key. Returns
        {"suppress": names the history suppresses as well, "repeat": bool, "cells_audited", "atoms"}."""
        labels = self.scope_labels(scope, dataset, cfg)
        sid = self.scope_id(*labels)
        sq = self._sq
        with self._guard():
            try:
                self._db.execute("BEGIN IMMEDIATE")
                pending = None
                try:
                    out, pending = self._check_and_record(scope, labels, sid, predicate, build_cells, k, cfg)
                except BaseException as e:
                    self._db.execute("ROLLBACK")
                    if not isinstance(e, DisclosureRefused):
                        self._auditors.pop(sid, None)     # possibly mid-trial: rebuilt from the database next time
                    raise
                try:
                    self._db.execute("COMMIT")
                except BaseException:
                    if pending is not None:
                        pending.rollback()
                    self._auditors.pop(sid, None)
                    raise
                if pending is not None:
                    pending.commit()
            except DisclosureRefused as e:
                self.metrics.inc({REFUSE_FULL: "refused_capacity", REFUSE_SCOPES: "refused_capacity",
                                  REFUSE_RATE: "refused_rate", REFUSE_KEY: "refused_identity",
                                  REFUSE_AUDIT_BUDGET: "refused_audit_budget"}.get(
                                      e.code, "refused_differencing"))
                ctx = getattr(e, "rate_ctx", None)
                if ctx is not None:      # a refused query is metered too (the transaction above was rolled back)
                    self._meter_refused(ctx, cfg)
                if e.code in (REFUSE_FULL, REFUSE_SCOPES):
                    log.error("event='disclosure_history_full' code=%s tenant=%r dataset=%r", e.code, scope.tenant,
                              dataset)
                elif e.code == REFUSE_RATE:
                    log.warning("event='disclosure_history_rate_limited' tenant=%r app=%r", scope.tenant, scope.app)
                raise
            except HistoryUnavailable:
                self.metrics.inc("store_unavailable")
                self._auditors.pop(sid, None)
                raise
            except sq.Error as e:
                self.metrics.inc("store_unavailable")
                self._auditors.pop(sid, None)
                raise HistoryUnavailable(type(e).__name__) from None
            if out["new"] and self.state_dir and self.production and self.db_id:
                self._publish_mark(out["seq"])
        self.metrics.inc("answered_repeat" if out["repeat"] else "answered")
        if out["suppress"]:
            self.metrics.inc("suppressed_more")
        return out

    @contextlib.contextmanager
    def _guard(self, timeout: float | None = None):
        """The in-process store lock, with a bound: a thread stuck in the database cannot queue every other request."""
        if not self._lock.acquire(timeout=self.timeout if timeout is None else timeout):
            self.metrics.inc("store_unavailable")
            raise HistoryUnavailable("timeout")
        try:
            yield
        finally:
            self._lock.release()

    @staticmethod
    def _callers(sub: bytes, scope: HistoryScope) -> tuple:
        """(app subject, end-user subject or None, subject of the refused-query meter): keyed hashes, never the ids."""
        def h(tail: bytes) -> bytes:
            return hmac.digest(sub, _CALLER + scope.app.encode() + tail, "sha256")[:KEY_BYTES]
        user = h(b"\x00" + scope.user.encode()) if scope.user else None
        return h(b""), user, user or h(b"\x00\x00anon")

    @staticmethod
    def _win(per: str, now: float) -> tuple[int, int]:
        """(window number, seconds left in it) of a fixed minute ('m') or UTC day ('d') window."""
        size = 60 if per == "m" else 86400
        return int(now // size), size - int(now % size)

    def _rate_check(self, sid: str, subs: dict, cfg: Config, now: float, only: str | None = None) -> None:
        """Refuse (429, Retry-After) a NEW question when any limit is used up: per (app, end user) -- the fair share,
        not applied to an app that asserts no user -- per app and per scope; and when the caller has been REFUSED too
        often (probing). Counters are rows of the shared database, read inside the check-and-record transaction."""
        checks = [(subs["app"], "new", "m", cfg.per_minute, "this app"),
                  (subs["app"], "new", "d", cfg.per_day, "this app"),
                  (subs["scope"], "new", "m", cfg.scope_per_minute, "all users of this data"),
                  (subs["scope"], "new", "d", cfg.scope_per_day, "all users of this data"),
                  (subs["refused_user"], "refused", "m", cfg.refused_per_minute, "this user's refused questions"),
                  (subs["refused_user"], "refused", "d", cfg.refused_per_day, "this user's refused questions"),
                  (subs["app"], "refused", "m", cfg.refused_app_per_minute, "this app's refused questions"),
                  (subs["app"], "refused", "d", cfg.refused_app_per_day, "this app's refused questions")]
        if subs["user"] is not None:
            checks += [(subs["user"], "new", "m", cfg.user_per_minute, "this user"),
                       (subs["user"], "new", "d", cfg.user_per_day, "this user")]
        for subject, kind, per, limit, who in checks:
            if only is not None and kind != only:
                continue
            win, left = self._win(per, now)
            r = self._db.execute("SELECT n FROM rate WHERE sid = ? AND subject = ? AND kind = ? AND per = ? AND win = ?",
                                 (sid, subject, kind, per, win)).fetchone()
            if r is not None and r[0] >= limit:
                raise DisclosureRefused(
                    f"query refused: too many {'refused' if kind == 'refused' else 'new'} questions about this data "
                    f"from {who} {'in the last minute' if per == 'm' else 'today'} (questions already answered are "
                    "still answered)", code=REFUSE_RATE, retry_after=left)

    def _bump(self, sid: str, subjects: list, now: float) -> None:
        """Atomic increments of (subject, kind) in the current minute and day windows (inside a transaction)."""
        m, _ = self._win("m", now)
        d, _ = self._win("d", now)
        for subject, kind in subjects:
            for per, win in (("m", m), ("d", d)):
                self._db.execute("INSERT INTO rate (sid, subject, kind, per, win, n) VALUES (?, ?, ?, ?, ?, 1) "
                                 "ON CONFLICT (sid, subject, kind, per, win) DO UPDATE SET n = n + 1",
                                 (sid, subject, kind, per, win))
        self._db.execute("DELETE FROM rate WHERE sid = ? AND ((per = 'm' AND win < ?) OR (per = 'd' AND win < ?))",
                         (sid, m - 1, d - 1))

    def _meter_refused(self, ctx: tuple, cfg: Config) -> None:
        """A refused (not rate-limited) question counts against the caller's refused budget. Own short transaction;
        a failure to meter is logged and does not change the refusal."""
        sid, refused_user, app = ctx
        try:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._bump(sid, [(refused_user, "refused"), (app, "refused")], time.time())
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")
        except self._sq.Error as e:
            log.error("event='disclosure_history_refusal_not_metered' error_type=%s", type(e).__name__)

    @staticmethod
    def _suppress_until_clean(aud, cells, digests, cfg):
        """Audit the response's cells, with history-aware complementary suppression if the response as computed does
        not pass. Steps, all sharing one wall-clock deadline (cfg.audit_max_ms):

          1. every cell as printed: accepted -> done;
          2. the BASE: the cells that must be printed as they are (the answer) with every secondary unit (one cell, or
             every cell of one listing group) in its suppressed form (alt bounds; an OMIT unit left out) -- the least
             the response can say. Not accepted -> refused (no suppression can help);
          3. on top of the base, the minimal search: suppress the unit whose suppression alone suffices (cells
             touching the problem first, then by priority and size), else the likeliest one, and look again -- until
             two thirds of the deadline;
          4. else the base itself (every secondary cell suppressed). Measured: printing as many cells as R1 allows
             here instead (greedy) answered fewer later questions, because more exact counts were printed.

        Steps 2-3 only add information to an accepted state, so the auditor's nested levels undo exactly the step
        that failed. Returns (bounds, suppressed names, None) with the accepted state in one open level of the
        auditor, or (bounds, set(), Violation) with nothing applied."""
        t0 = time.monotonic()
        deadline = t0 + cfg.audit_max_ms / 1000
        soft = t0 + cfg.audit_max_ms / 1500
        printed = {c.name: (c.lo, c.hi) for c in cells}
        batch = [(d, c.keys, c.lo, c.hi, c.whole) for d, c in zip(digests, cells, strict=True)]
        v = aud.trial(batch, deadline)
        if v is None:
            return printed, set(), None
        aud.rollback()
        if v.kind == "budget" and time.monotonic() > deadline:
            return printed, set(), v
        fixed = [i for i, c in enumerate(cells) if c.alt is None]
        units: dict = {}
        for i, c in enumerate(cells):
            if c.alt is not None:
                units.setdefault(("g", c.group) if c.group is not None else ("c", i), []).append(i)
        base_bounds = dict(printed)
        for ix in units.values():
            for i in ix:
                base_bounds[cells[i].name] = cells[i].alt
        aud.begin()                                     # level A: the base and what is printed on top of it
        v = aud.trial([batch[i] for i in fixed] + [
            (digests[i], cells[i].keys, *base_bounds[cells[i].name], cells[i].whole)
            for ix in units.values() for i in ix if base_bounds[cells[i].name] != OMIT], deadline)
        if v is not None:
            aud.rollback()
            aud.rollback()
            return printed, set(), v
        aud.commit()
        all_units = {tuple(ix) for ix in units.values()}

        def names(us):
            return {cells[i].name for u in us for i in u}

        def bounds_for(sup):
            return {**printed, **{cells[i].name: cells[i].alt for u in sup for i in u}}

        def attempt(sup, r2=True):
            """Print every unit not in `sup` on top of the base. The violation's cells index `cells`."""
            idx = [i for u in all_units - sup for i in u]
            v = aud.trial([batch[i] for i in idx], deadline, r2=r2)
            if v is not None:
                v.cells = {idx[i] for i in v.cells}
            return v

        # 3. minimal search
        sup: set = set()
        v = attempt(sup)
        while v is not None:
            aud.rollback()
            if time.monotonic() > soft:
                break
            cand = sorted((u for u in all_units - sup
                           if not all(aud.known(digests[i], len(cells[i].keys), len(cells[i].keys)) for i in u)),
                          key=lambda u: (not any(i in v.cells for i in u), max(cells[i].priority for i in u),
                                         sum(len(cells[i].keys) for i in u), cells[u[0]].name))
            if not cand:
                break
            done = False
            for u in cand[:8]:
                v2 = attempt(sup | {u})
                if v2 is None:
                    sup.add(u)
                    v, done = None, True
                    break
                aud.rollback()
                if time.monotonic() > soft:
                    break
            if done or time.monotonic() > soft:
                break
            sup.add(cand[0])
            v = attempt(sup)
        if v is None:
            aud.commit()
            return bounds_for(sup), names(sup), None
        # 4. the base itself: every secondary cell suppressed (accepted in step 2)
        return bounds_for(all_units), names(all_units), None

    def _check_and_record(self, scope, labels, sid, predicate, build_cells, k, cfg):
        box: list = []
        try:
            return self._check_and_record_inner(scope, labels, sid, predicate, build_cells, k, cfg, box)
        except DisclosureRefused as e:
            if box and e.code != REFUSE_RATE:
                e.rate_ctx = box[0]
            raise

    def _check_and_record_inner(self, scope, labels, sid, predicate, build_cells, k, cfg, box):
        db = self._db
        row = db.execute("SELECT key_id, kcv, gen, cells, stored_keys, queries FROM scope WHERE sid = ?",
                         (sid,)).fetchone()
        if row is None:
            kid, sub, kcv = self._scope_key(scope.keyfn, sid, None)
            app_s, user_s, refused_s = self._callers(sub, scope)
            box.append((sid, refused_s, app_s))
            if db.execute("SELECT COUNT(*) FROM scope").fetchone()[0] >= cfg.max_scopes:
                raise DisclosureRefused("query refused: the disclosure-control history has no room for another "
                                        "scope (an operator must review it)", code=REFUSE_SCOPES)
            gen = ncells = stored = queries = 0
        else:
            kid, sub, kcv = self._scope_key(scope.keyfn, sid, row[0])
            if not hmac.compare_digest(kcv, row[1]):
                log.error("event='disclosure_history_key_mismatch' tenant=%r dataset=%r", scope.tenant, labels[3])
                raise HistoryUnavailable("key_mismatch")
            gen, ncells, stored, queries = row[2], row[3], row[4], row[5]
            app_s, user_s, refused_s = self._callers(sub, scope)
            box.append((sid, refused_s, app_s))
        subs = {"app": app_s, "user": user_s, "scope": ANONYMOUS, "refused_user": refused_s}
        aud = self._auditor(sid, gen, k, cfg)
        try:
            cells = [c for c in build_cells(sub) if c.keys]
        except DisclosureRefused as e:
            if e.code == REFUSE_KEY:       # refused before any audit work: still counts against the refused budget
                self._rate_check(sid, subs, cfg, time.time(), only="refused")
            raise
        digests = [_digest(c.keys) for c in cells]
        caller = app_s
        pred = hmac.digest(sub, _PRED + json.dumps(predicate, sort_keys=True).encode(), "sha256")[:KEY_BYTES]
        now = time.time()
        day = _day(now)
        repeat = all(aud.known(d, c.lo, c.hi) for d, c in zip(digests, cells, strict=True))
        suppress: set = set()
        pending = None
        if not repeat:
            self._rate_check(sid, subs, cfg, now)
            bounds, suppress, violation = self._suppress_until_clean(aud, cells, digests, cfg)
            if violation is not None:
                if violation.kind == "budget":
                    raise DisclosureRefused("query refused: the disclosure-control audit of this question against "
                                            "the answers already given about this data did not finish within its "
                                            "time budget, so it could not be certified (nothing is known to be "
                                            "revealed); a narrower or broader question may be answered",
                                            code=REFUSE_AUDIT_BUDGET)
                raise DisclosureRefused("query refused: together with answers already given about this data (in any "
                                        "conversation, by any app, on any day) it would determine the size of a "
                                        f"group of fewer than {k} records", code=REFUSE_DIFFERENCING)
            pending = aud
            new_keys = 0
            new_cells = 0
            for d, c in zip(digests, cells, strict=True):
                lo, hi = bounds[c.name]
                if (lo, hi) == OMIT:
                    continue                      # not printed: not recorded
                r = db.execute("SELECT lo, hi, whole FROM cell WHERE sid = ? AND digest = ?", (sid, d)).fetchone()
                if r is None:
                    db.execute("INSERT INTO cell (sid, digest, n, lo, hi, whole, keys) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (sid, d, len(c.keys), lo, hi, int(c.whole), b"".join(sorted(c.keys))))
                    new_keys += len(c.keys)
                    new_cells += 1
                else:
                    nhi = hi if r[1] is None else (r[1] if hi is None else min(r[1], hi))
                    db.execute("UPDATE cell SET lo = ?, hi = ?, whole = ? WHERE sid = ? AND digest = ?",
                               (max(r[0], lo), nhi, max(r[2], int(c.whole)), sid, d))
            if (stored + new_keys > cfg.max_keys or ncells + new_cells > cfg.max_cells
                    or aud.n_atoms > cfg.max_atoms):
                aud.rollback()
                raise DisclosureRefused("query refused: the disclosure-control history of this data has no room for "
                                        "new information (questions already answered are still answered; an "
                                        "operator must review it)", code=REFUSE_FULL)
            stored += new_keys
            ncells += new_cells
            self._bump(sid, [(app_s, "new"), (ANONYMOUS, "new")] + ([(user_s, "new")] if user_s else []), now)
            gen += 1
        if row is None:
            db.execute("INSERT INTO scope (sid, tenant, app, purpose, dataset, key_id, kcv, created_day, gen, cells, "
                       "stored_keys, queries) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0)", (sid, *labels, kid, kcv, day))
        queries += 1
        db.execute("INSERT INTO query (sid, seq, day, predicate, caller, new) VALUES (?, ?, ?, ?, ?, ?)",
                   (sid, queries, day, pred, caller, int(not repeat)))
        db.execute("DELETE FROM query WHERE sid = ? AND seq <= ?", (sid, queries - cfg.query_log))
        db.execute("UPDATE scope SET gen = ?, cells = ?, stored_keys = ?, queries = ? WHERE sid = ?",
                   (gen, ncells, stored, queries, sid))
        seq = self.seq()
        if not repeat:
            seq += 1
            self._set_seq(seq)
        if pending is not None:
            self._auditors[sid] = (gen, aud)
        return ({"suppress": suppress, "repeat": repeat, "new": not repeat, "seq": seq, "cells_audited": len(cells),
                 "atoms": aud.n_atoms, "history_queries": queries}, pending)

    # --- operator actions (python -m pgw.product.disclosure) --------------------------------------------------------

    def list_scopes(self) -> list[dict]:
        try:
            with self._lock:
                rows = self._db.execute("SELECT tenant, app, purpose, dataset, key_id, created_day, queries, cells, "
                                        "stored_keys FROM scope ORDER BY tenant, dataset, app, purpose").fetchall()
        except self._sq.Error as e:
            raise HistoryUnavailable(type(e).__name__) from None
        return [dict(zip(("tenant", "app", "purpose", "dataset", "key_id", "created_day", "queries", "cells",
                          "stored_keys"), r, strict=True)) for r in rows]

    def _admin(self, action: str, sid, tenant, dataset, detail: str, reason: str, operator: str | None = None) -> None:
        self._db.execute("INSERT INTO admin_log (day, action, sid, tenant, dataset, detail, reason, operator) VALUES "
                         "(?, ?, ?, ?, ?, ?, ?, ?)", (_day(), action, sid, tenant, dataset, detail, reason, operator))

    def compact(self, tenant: str, dataset: str, reason: str, app: str = SHARED, purpose: str = SHARED,
                operator: str | None = None) -> int:
        """Drop one scope's query log (predicate HMACs, day numbers, callers) and its callers' daily counters: a
        deliberate, logged admin action that frees the per-caller budget. Every revealed record set is KEPT, so no
        answer given before becomes comparable-free: compaction never reopens a differencing gap (unlike the
        former reset). Returns the number of query-log rows dropped."""
        if len(reason.strip()) < 20:
            raise ValueError("compaction needs a written reason (20+ characters)")
        if operator is not None and not OPERATOR_RE.fullmatch(operator):
            raise ValueError("operator: 2-64 characters of [A-Za-z0-9._@-]")
        sid = self.scope_id(tenant, app, purpose, dataset)
        with self._guard():
            try:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    n = self._db.execute("SELECT COUNT(*) FROM query WHERE sid = ?", (sid,)).fetchone()[0]
                    self._db.execute("DELETE FROM query WHERE sid = ?", (sid,))
                    self._db.execute("DELETE FROM rate WHERE sid = ?", (sid,))
                    self._admin("compact", sid, tenant, dataset, f"query_rows_dropped={n}", reason.strip(), operator)
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
                self._db.execute("COMMIT")
            except self._sq.Error as e:
                raise HistoryUnavailable(type(e).__name__) from None
        log.warning("event='disclosure_history_compacted' tenant=%r dataset=%r query_rows_dropped=%d operator=%r",
                    tenant, dataset, n, operator)
        return n

    def recover(self, reason: str, new: bool = False, operator: str | None = None) -> str:
        """Re-bind the high-water mark (and the rollback copy) to this database after an investigated restore (`new`:
        start an EMPTY history under a new id -- every earlier answer stops being compared, a gap the operator accepts in
        writing). Needs an operator identity and a written reason (both in the admin log and the event log), and is
        refused while any gateway process holds the store (shared lock on <db>.live): stop the gateway first."""
        import fcntl
        import os
        if operator is None or not OPERATOR_RE.fullmatch(operator):
            raise ValueError("recovery needs the operator's identity (--operator: 2-64 characters of [A-Za-z0-9._@-])")
        if len(reason.strip()) < 20:
            raise ValueError("recovery needs a written reason (20+ characters)")
        if not self.state_dir or not Path(self.state_dir).is_dir():
            raise ValueError("recovery needs the existing state directory (--state-dir)")
        lockfd = None
        if self.path != ":memory:":
            lockfd = os.open(self._live_path(), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(lockfd)
                raise ValueError("a gateway process holds the history (live lock): stop every gateway process on "
                                 "this host, then recover") from None
        try:
            with self._guard():
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    if new:
                        for stmt in ("DELETE FROM scope", "DELETE FROM cell", "DELETE FROM query",
                                     "DELETE FROM rate"):
                            self._db.execute(stmt)
                        self.db_id = None
                    if self.db_id is None:
                        self.db_id = os.urandom(16).hex()
                        self._db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('db_id', ?), ('schema', ?)",
                                         (self.db_id, SCHEMA_VERSION))
                        self._set_seq(0)
                    seq = self.seq() + 1
                    self._set_seq(seq)
                    self._admin("recover_new" if new else "recover", None, None, None, f"db_id={self.db_id}",
                                reason.strip(), operator)
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
                self._db.execute("COMMIT")
                self._auditors.clear()
            p = Path(self.state_dir) / HWM_NAME
            if p.exists():
                os.replace(p, p.with_name(p.name + f".replaced-{_day()}"))
            self._publish_mark(seq, force=True)
        finally:
            if lockfd is not None:
                os.close(lockfd)
        log.warning("event='disclosure_history_recovered' new_history=%r operator=%r", new, operator)
        return self.db_id

    def admin_log(self) -> list[dict]:
        rows = self._db.execute("SELECT day, action, tenant, dataset, detail, reason, operator FROM admin_log "
                                "ORDER BY rowid").fetchall()
        return [dict(zip(("day", "action", "tenant", "dataset", "detail", "reason", "operator"), r, strict=True))
                for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
        if self._live_fd is not None:
            import os
            os.close(self._live_fd)
            self._live_fd = None


def _cli_mac_keyfn(keys_file: str | None):
    """Key store of the operator's shell (same sources as the gateway): --keys-file / PGW_TENANT_KEYS_FILE, else
    PGW_MASTER_KEY. None when neither is set."""
    import os
    path = keys_file or os.environ.get("PGW_TENANT_KEYS_FILE")
    if path:
        from ..keys import TenantKeyring
        return mac_keyfn_for(TenantKeyring.load(path))
    if os.environ.get("PGW_MASTER_KEY"):
        from ..pipeline import Keys
        return mac_keyfn_for(Keys.from_env())
    return None


def _main(argv: list[str]) -> int:
    """python -m pgw.product.disclosure history list    --db PATH
       python -m pgw.product.disclosure history log     --db PATH
       python -m pgw.product.disclosure history compact --db PATH --tenant T --dataset D [--app A --purpose P]
                                                        --operator ID --reason "ticket and why (20+ characters)"
       python -m pgw.product.disclosure history recover --db PATH --state-dir DIR --operator ID --reason "..." [--new]
                                                        [--keys-file F | PGW_TENANT_KEYS_FILE | PGW_MASTER_KEY]
    compact keeps every record set (no gap reopens); recover re-binds the high-water mark and the rollback copy after
    an investigated restore, or with --new starts an empty history (logged with the operator; earlier answers are no
    longer compared). recover REFUSES while a gateway process holds the history: stop the gateway first. It needs
    the platform key store (the marks are HMACed with it). Prints counts and labels only."""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m pgw.product.disclosure")
    ap.add_argument("group", choices=["history"])
    ap.add_argument("command", choices=["list", "log", "compact", "recover"])
    ap.add_argument("--db", required=True)
    ap.add_argument("--state-dir")
    ap.add_argument("--keys-file")
    ap.add_argument("--no-mac", action="store_true", help="development only: write unauthenticated marks")
    ap.add_argument("--new", action="store_true")
    for a in ("tenant", "app", "purpose", "dataset", "reason", "operator"):
        ap.add_argument(f"--{a}")
    a = ap.parse_args(argv)
    if a.command != "recover" and not Path(a.db).is_file():
        ap.error("--db: no such history file")
    if a.command in ("recover", "compact") and not (a.operator and OPERATOR_RE.fullmatch(a.operator)):
        ap.error(f"{a.command} needs --operator (your identity: 2-64 characters of [A-Za-z0-9._@-])")
    if a.command == "recover":
        if not (a.state_dir and a.reason):
            ap.error("recover needs --state-dir and --reason")
        if not a.new and not Path(a.db).is_file():
            ap.error("--db: no such history file (restore it from backup, or start an empty history with --new)")
        keyfn = None if a.no_mac else _cli_mac_keyfn(a.keys_file)
        if keyfn is None and not a.no_mac:
            ap.error("recover needs the platform key store (--keys-file, PGW_TENANT_KEYS_FILE or PGW_MASTER_KEY)")
        hwm = None
        try:
            hwm = read_hwm(a.state_dir) if Path(a.state_dir).is_dir() else None
        except HistoryIntegrityError:
            pass
        store = HistoryStore(a.db, mac_keyfn=keyfn)    # no state dir: no integrity refusal while recovering
        store.state_dir = a.state_dir
        try:
            if hwm is not None and not a.new and store.db_id == hwm["db_id"] and store.seq() >= hwm["seq"] \
                    and "mac" in hwm and not store._bad_mac(hwm, "mark"):
                print("history and high-water mark agree; nothing to recover")
                return 0
            try:
                db_id = store.recover(a.reason, new=a.new, operator=a.operator)
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
        finally:
            store.close()
        print(f"recovered db_id={db_id} new_history={a.new} operator={a.operator}")
        return 0
    store = HistoryStore(a.db)
    if a.command == "list":
        for s in store.list_scopes():
            print(" ".join(f"{k}={v}" for k, v in s.items()))
    elif a.command == "log":
        for s in store.admin_log():
            print(" ".join(f"{k}={v!r}" for k, v in s.items()))
    else:
        if not all((a.tenant, a.dataset, a.reason)):
            ap.error("compact needs --tenant --dataset --reason")
        n = store.compact(a.tenant, a.dataset, a.reason, a.app or SHARED, a.purpose or SHARED, a.operator)
        print(f"compacted tenant={a.tenant} dataset={a.dataset} query_rows_dropped={n}")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
