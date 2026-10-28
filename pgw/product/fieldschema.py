"""Field schemas for HRMS records (docs/HRMS_CONTRACTS.md): what every field of a source object is, and what may
happen to its value.

HRMS exports (Workday, UKG/Kronos, SAP Fieldglass, SuccessFactors, payroll registers, Active Directory) arrive as
TYPED FIELDS WITH KNOWN NAMES. A field schema states, per field path, one disposition; the gateway classifies values
by the schema, never by guessing from the text:

  ALIAS          identities (names, employee ids, usernames, manager references): scoped alias, restored in the answer
  REMOVE         dropped (addresses, phone numbers, government/bank identifiers, free text)
  GENERALIZE     only a coarse form may be used: date -> year / month, amount -> band (sealed edges), PIN -> 3-digit
                 sorting-district prefix or dropped, bit flag -> label
  COMPUTE_ONLY   the exact value is used by local computations only and never leaves
  NEVER          special-category / protected attributes (health, absence reason, leave type, background-check status
                 and result, gender, marital status, nationality, religion, caste, disability, union membership,
                 biometrics, criminal record): dropped at parse time, never available to any output, not even
                 generalised. A NEVER field may declare a presence flag (a COMPUTE_ONLY boolean "this row carries a
                 non-work value"), never the value itself.
  KEEP           genuinely non-personal values only: an enumeration from a sealed reference table or a boolean, each
                 with a written justification. Free text can never be KEEP.

Any path the schema does not name is UNKNOWN and treated as NEVER: dropped, counted, and (path names only) reported
to the caller, never sent upstream.

Schemas live in registry/schemas/*.yaml and statutory/computation rules in registry/rules/*.yaml. Both are sealed by
policies/MANIFEST.json like every other configuration file (pgw.product.integrity): a modified or unsealed file
stops startup.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from ..types import EntityType as T
from .contracts import CLASS_OF, ContractError, DataClass

DISPOSITIONS = ("ALIAS", "REMOVE", "GENERALIZE", "COMPUTE_ONLY", "NEVER", "KEEP")
TYPES = ("string", "amount", "number", "int", "date", "datetime", "time", "bool", "enum", "any")
FORMATS = ("json", "csv", "xml")
NEVER_CATEGORIES = ("health", "absence_reason", "leave_type", "background_check", "gender", "marital_status",
                    "nationality", "religion", "caste", "disability", "union_membership", "biometrics",
                    "criminal_record", "sexual_orientation", "political_opinion", "ethnicity", "photo",
                    "pregnancy", "other_special_category")
GENERALIZE_METHODS = {"year": ("date", "datetime"), "month": ("date", "datetime"), "band": ("amount", "number", "int"),
                      "pin_prefix3": ("string", "int"), "drop": tuple(TYPES), "bitflag": ("int",)}
# Identity entity types an ALIAS field may name (C1, plus REF: an identifier-shaped reference such as a work order).
ALIAS_ENTITIES = frozenset({t for t, c in CLASS_OF.items() if c is DataClass.C1} | {T.IDENTIFIER})

# Words that mark a special-category attribute in a vendor field path or canonical name. A field whose path or name
# carries one of them must be NEVER: a schema author cannot classify "Gender" or "Leave_Type" as anything else by
# mistake. Matched against whole words of the path (split on punctuation and camelCase), so "Union_Territory" or
# "accountDisabled" do not trip it; bigrams catch the ambiguous ones ("leave type", "trade union").
SPECIAL_WORDS = frozenset({
    "gender", "sex", "marital", "religion", "religious", "caste", "tribe", "nationality", "citizenship", "ethnicity",
    "ethnic", "race", "disability", "handicap", "handicapped", "biometric", "biometrics", "fingerprint",
    "diagnosis", "illness", "pregnancy", "pregnant", "maternity", "paternity", "sick", "bgv", "criminal",
    "political", "photo", "thumbnailphoto", "jpegphoto", "veteran"})
SPECIAL_BIGRAMS = frozenset({("leave", "type"), ("leave", "reason"), ("absence", "reason"), ("absence", "type"),
                             ("trade", "union"), ("union", "member"), ("union", "membership"), ("background", "check"),
                             ("background", "verification"), ("police", "verification"), ("blood", "group"),
                             ("medical", "condition"), ("medical", "history"), ("medical", "certificate"),
                             ("medical", "leave"), ("health", "condition"), ("health", "status"),
                             ("sexual", "orientation")})

MAX_FIELDS = 400
MAX_ENUM = 500
_NAME = re.compile(r"[a-z][a-z0-9_]{0,47}")
_SEGMENT = re.compile(r"[^.\[\]\x00-\x1f]{1,96}")
_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\b|_|\d)|[A-Z]?[a-z]+|\d+")
_HEADER_WS = re.compile(r"\s+")


class SchemaError(ContractError):
    """A field schema or rules file is unsafe or malformed: startup stops (fail closed)."""


def header_key(h: str) -> str:
    """CSV header comparison key: case-insensitive, whitespace collapsed, BOM stripped."""
    return _HEADER_WS.sub(" ", str(h).replace("﻿", "")).strip().casefold()


def path_words(path: str) -> list[str]:
    out = []
    for part in re.split(r"[^A-Za-z0-9]+", path):
        out += [w.lower() for w in _WORDS.findall(part)] if part else []
    return out


def special_hint(*texts: str) -> str | None:
    """The special-category word a field path/name carries, or None."""
    for t in texts:
        if not t:
            continue
        words = path_words(t)
        for w in words:
            if w in SPECIAL_WORDS:
                return w
        for a, b in zip(words, words[1:], strict=False):
            if (a, b) in SPECIAL_BIGRAMS:
                return f"{a} {b}"
    return None


def parse_path(path: str) -> tuple[tuple[str, bool], ...]:
    """'Worker[].Personal_Data.Name' -> (('Worker', True), ('Personal_Data', False), ('Name', False)).
    '$[]' is the top-level array; '$' alone the top-level object."""
    if not isinstance(path, str) or not path or len(path) > 512:
        raise SchemaError("field path: 1-512 characters")
    out = []
    for seg in path.split("."):
        arr = seg.endswith("[]")
        name = seg[:-2] if arr else seg
        if not _SEGMENT.fullmatch(name):
            raise SchemaError(f"field path {path!r}: invalid segment")
        out.append((name, arr))
    return tuple(out)


def band_labels(edges: list, unit: str = "₹") -> list[str]:
    """Labels of the bands [e0, e1), [e1, e2), ..., [en, inf)."""
    def fmt(x):
        from .local_compute import inr
        if unit == "₹":
            return inr(int(x))
        return f"{x}{unit}"
    labels = []
    for i, lo in enumerate(edges):
        if i + 1 < len(edges):
            hi = edges[i + 1]
            labels.append(f"under {fmt(hi)}" if i == 0 and lo == 0 else f"{fmt(lo)} to under {fmt(hi)}")
        else:
            labels.append(f"{fmt(lo)} and above")
    return labels


def parse_edges(raw, where: str, *, min_width: bool = True, percent: bool = False) -> tuple:
    """Increasing non-negative integer band edges. Amount bands must be at least 20% wide (a narrow band would
    disclose the value it brackets); the first band may start at 0."""
    if (not isinstance(raw, list) or not 2 <= len(raw) <= 30
            or not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in raw)):
        raise SchemaError(f"{where}: band edges must be 2-30 non-negative integers")
    if any(b <= a for a, b in zip(raw, raw[1:], strict=False)):
        raise SchemaError(f"{where}: band edges must increase")
    if min_width and any(a > 0 and b * 5 < a * 6 for a, b in zip(raw, raw[1:], strict=False)):
        raise SchemaError(f"{where}: every band must be at least 20% wide")
    return tuple(raw)


@dataclass(frozen=True, eq=False)   # identity semantics: a spec is a dict key on the per-value hot path
class FieldSpec:
    path: str
    segments: tuple
    name: str
    type: str
    disposition: str
    entity: T | None = None
    method: str | None = None
    edges: tuple = ()
    labels: tuple = ()
    bit: int = 0
    enum: tuple = ()
    justification: str = ""
    category: str | None = None
    presence_flag: str | None = None
    work_values: frozenset = frozenset()
    csv: tuple = ()
    subtree: bool = False
    date_order: str = "DMY"
    optional: bool = False

    @property
    def multi(self) -> bool:
        return any(a for _n, a in self.segments)

    def generalized_labels(self) -> tuple:
        """The finite set of values GENERALIZE can produce for band/bitflag (empty for open formats)."""
        return self.labels


@dataclass
class _Node:
    children: dict = field(default_factory=dict)
    array: bool = False
    spec: FieldSpec | None = None


@dataclass(frozen=True)
class FieldSchema:
    id: str
    version: int
    title: str
    starter: bool
    formats: tuple
    records_at: tuple            # parsed path to the records array ('$[]' -> (('$', True),))
    fields: tuple                # FieldSpec, in file order
    by_name: dict                # canonical name -> FieldSpec (presence flags map to their NEVER field)
    csv_headers: dict            # header_key -> FieldSpec
    trie: _Node = field(compare=False, hash=False, repr=False)
    path: str = ""

    def spec(self, name: str) -> FieldSpec | None:
        return self.by_name.get(name)


def _field(raw: dict, i: int) -> FieldSpec:
    where = f"fields[{i}]"
    if not isinstance(raw, dict):
        raise SchemaError(f"{where}: must be a mapping")
    allowed = {"path", "name", "type", "disposition", "entity", "method", "edges", "labels", "bit", "enum",
               "justification", "category", "presence_flag", "csv", "subtree", "date_order", "optional", "note"}
    extra = set(raw) - allowed
    if extra:
        raise SchemaError(f"{where}: unknown keys {sorted(extra)}")
    path = raw.get("path")
    segs = parse_path(path)
    disp = str(raw.get("disposition", "")).upper()
    if disp not in DISPOSITIONS:
        raise SchemaError(f"{where} ({path}): disposition must be one of {DISPOSITIONS}")
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise SchemaError(f"{where} ({path}): name must match [a-z][a-z0-9_]{{0,47}}")
    typ = str(raw.get("type", "any" if disp in ("NEVER", "REMOVE") else "")).lower()
    if typ not in TYPES:
        raise SchemaError(f"{where} ({path}): type must be one of {TYPES}")
    subtree = raw.get("subtree", False)
    if not isinstance(subtree, bool) or (subtree and disp not in ("NEVER", "REMOVE")):
        raise SchemaError(f"{where} ({path}): only NEVER or REMOVE may cover a whole subtree")
    hint = special_hint(path, name, *[h for h in (raw.get("csv") or []) if isinstance(h, str)])
    if hint and disp != "NEVER":
        raise SchemaError(f"{where} ({path}): looks like a special-category attribute ({hint!r}); it must be NEVER")
    kw: dict = {}
    if disp == "ALIAS":
        try:
            ent = T(raw.get("entity"))
        except ValueError:
            raise SchemaError(f"{where} ({path}): ALIAS needs an identity entity type") from None
        if ent not in ALIAS_ENTITIES:
            raise SchemaError(f"{where} ({path}): ALIAS entity must be an identity type "
                              f"({sorted(t.value for t in ALIAS_ENTITIES)})")
        if typ not in ("string", "int"):
            raise SchemaError(f"{where} ({path}): ALIAS fields are strings")
        kw["entity"] = ent
    elif "entity" in raw:
        raise SchemaError(f"{where} ({path}): only ALIAS fields name an entity")
    if disp == "GENERALIZE":
        method = str(raw.get("method", ""))
        if method not in GENERALIZE_METHODS:
            raise SchemaError(f"{where} ({path}): GENERALIZE method must be one of {sorted(GENERALIZE_METHODS)}")
        if typ not in GENERALIZE_METHODS[method]:
            raise SchemaError(f"{where} ({path}): method {method} does not apply to type {typ}")
        kw["method"] = method
        if method == "band":
            kw["edges"] = parse_edges(raw.get("edges"), f"{where} ({path}).edges")
            kw["labels"] = tuple(band_labels(list(kw["edges"])))
        elif method == "bitflag":
            bit, labels = raw.get("bit"), raw.get("labels")
            if not isinstance(bit, int) or isinstance(bit, bool) or not 0 <= bit < 64 or not (
                    isinstance(labels, list) and len(labels) == 2 and all(
                        isinstance(x, str) and re.fullmatch(r"[a-z][a-z _-]{0,31}", x) for x in labels)):
                raise SchemaError(f"{where} ({path}): bitflag needs bit (0-63) and two short lower-case labels")
            kw["bit"], kw["labels"] = bit, tuple(labels)
    elif "method" in raw:
        raise SchemaError(f"{where} ({path}): only GENERALIZE fields take a method")
    if typ == "enum" or disp == "KEEP":
        enum = raw.get("enum")
        if disp == "KEEP" and typ not in ("enum", "bool"):
            raise SchemaError(f"{where} ({path}): KEEP is only for an enumeration from a reference table or a "
                              "boolean (free text can never be KEEP)")
        if typ == "enum":
            if (not isinstance(enum, list) or not 1 <= len(enum) <= MAX_ENUM or len(set(map(str, enum))) != len(enum)
                    or not all(isinstance(x, str) and 1 <= len(x) <= 80 and x.strip() == x
                               and not any(ord(c) < 32 for c in x) for x in enum)):
                raise SchemaError(f"{where} ({path}): enum must be 1-{MAX_ENUM} distinct short strings")
            if disp == "KEEP" and any(sum(c.isdigit() for c in x) >= 4 for x in enum):
                raise SchemaError(f"{where} ({path}): KEEP enumeration values may not carry 4+ digits")
            kw["enum"] = tuple(enum)
    if disp == "KEEP":
        just = str(raw.get("justification", "")).strip()
        if len(just) < 20:
            raise SchemaError(f"{where} ({path}): KEEP needs a written justification (at least 20 characters)")
        kw["justification"] = just
    if disp == "NEVER":
        cat = raw.get("category")
        if cat not in NEVER_CATEGORIES:
            raise SchemaError(f"{where} ({path}): NEVER fields name their category ({NEVER_CATEGORIES})")
        kw["category"] = cat
        pf = raw.get("presence_flag")
        if pf is not None:
            if (not isinstance(pf, dict) or set(pf) - {"name", "work_values"} or not isinstance(pf.get("name"), str)
                    or not _NAME.fullmatch(pf["name"])):
                raise SchemaError(f"{where} ({path}): presence_flag needs a name (and optional work_values)")
            wv = pf.get("work_values") or []
            if not isinstance(wv, list) or not all(isinstance(x, str) and len(x) <= 40 for x in wv) or len(wv) > 100:
                raise SchemaError(f"{where} ({path}): presence_flag.work_values: up to 100 short strings")
            kw["presence_flag"] = pf["name"]
            kw["work_values"] = frozenset(x.strip().casefold() for x in wv)
    elif "presence_flag" in raw or "category" in raw:
        raise SchemaError(f"{where} ({path}): category/presence_flag belong to NEVER fields")
    csv = raw.get("csv") or []
    if not isinstance(csv, list) or not all(isinstance(h, str) and 1 <= len(h) <= 120 for h in csv) or len(csv) > 12:
        raise SchemaError(f"{where} ({path}): csv must list up to 12 header names")
    order = str(raw.get("date_order", "DMY")).upper()
    if order not in ("DMY", "MDY"):
        raise SchemaError(f"{where} ({path}): date_order must be DMY or MDY")
    opt = raw.get("optional", False)
    if not isinstance(opt, bool):
        raise SchemaError(f"{where} ({path}): optional must be true or false")
    return FieldSpec(path=path, segments=segs, name=name, type=typ, disposition=disp, csv=tuple(csv),
                     subtree=subtree, date_order=order, optional=opt, **kw)


def load_schema(path: str | Path, data: bytes | None = None) -> FieldSchema:
    try:
        raw = yaml.safe_load(Path(path).read_text() if data is None else data.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        raise SchemaError(f"{Path(path).name}: not valid YAML") from None
    if not isinstance(raw, dict) or raw.get("kind") != "pgw-field-schema":
        raise SchemaError(f"{Path(path).name}: kind must be pgw-field-schema")
    extra = set(raw) - {"kind", "schema", "version", "title", "starter", "formats", "records_at", "fields", "note"}
    if extra:
        raise SchemaError(f"{Path(path).name}: unknown keys {sorted(extra)}")
    sid = raw.get("schema")
    if not isinstance(sid, str) or not _NAME.fullmatch(sid) or Path(path).stem != sid:
        raise SchemaError(f"{Path(path).name}: schema id must match the file name")
    try:
        version = int(raw["version"])
    except (KeyError, TypeError, ValueError):
        raise SchemaError(f"{sid}: version is required") from None
    formats = tuple(raw.get("formats") or ())
    if not formats or set(formats) - set(FORMATS):
        raise SchemaError(f"{sid}: formats must be a non-empty subset of {FORMATS}")
    records_at = parse_path(raw.get("records_at", "$[]"))
    if not records_at[-1][1]:
        raise SchemaError(f"{sid}: records_at must end at an array ('...[]')")
    if not isinstance(raw.get("fields"), list) or not 1 <= len(raw["fields"]) <= MAX_FIELDS:
        raise SchemaError(f"{sid}: fields must list 1-{MAX_FIELDS} entries")
    fields = tuple(_field(f, i) for i, f in enumerate(raw["fields"]))
    by_name: dict = {}
    headers: dict = {}
    root = _Node()
    for f in fields:
        for n in filter(None, (f.name, f.presence_flag)):
            if n in by_name:
                raise SchemaError(f"{sid}: field name {n!r} is used twice")
            by_name[n] = f
        for h in f.csv or ((f.segments[-1][0],) if len(f.segments) == 1 else ()):
            k = header_key(h)
            if k in headers:
                raise SchemaError(f"{sid}: CSV header {h!r} maps to two fields")
            headers[k] = f
        node = root
        for seg, arr in f.segments:
            if node.spec is not None:
                raise SchemaError(f"{sid}: {f.path} lies inside the field {node.spec.path}")
            child = node.children.get(seg)
            if child is None:
                child = node.children[seg] = _Node(array=arr)
            elif child.array != arr:
                raise SchemaError(f"{sid}: {f.path}: segment {seg!r} is declared both as array and as object")
            node = child
        if node.spec is not None or node.children:
            raise SchemaError(f"{sid}: {f.path} is declared twice or is both a field and an object")
        node.spec = f
    if "csv" in formats and not headers:
        raise SchemaError(f"{sid}: a CSV format needs header names")
    return FieldSchema(id=sid, version=version, title=str(raw.get("title", sid))[:120],
                       starter=bool(raw.get("starter", False)), formats=formats, records_at=records_at,
                       fields=fields, by_name=by_name, csv_headers=headers, trie=root, path=str(path))


# --- value parsing (types) -------------------------------------------------------------------------------------

class ValueInvalid(ValueError):
    """A value does not parse as its declared type (the row gets INVALID:<field>; the message has no value)."""


_AMOUNT_STRIP = re.compile(r"(?i)^\s*(?:₹|rs\.?|inr)\s*|\s*(?:₹|rs\.?|inr)\s*$")
_AMOUNT = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                        "dec"), 1)}
_D_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?(?:Z|[+-]\d{2}:?\d{2})?")
_D_NUM = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_D_MON = re.compile(r"(\d{1,2})[ \-]([A-Za-z]{3,9})[ \-,]+(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_D_MON2 = re.compile(r"([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})")
_D_AD = re.compile(r"(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2})(?:\.\d+)?Z?)?")


def parse_amount(v) -> Decimal:
    if isinstance(v, bool):
        raise ValueInvalid("bool")
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueInvalid("nan")
        return Decimal(str(v))
    if not isinstance(v, str):
        raise ValueInvalid("type")
    s = _AMOUNT_STRIP.sub("", v.strip()).replace(" ", "")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    if not _AMOUNT.fullmatch(s) or len(s) > 24:
        raise ValueInvalid("amount")
    try:
        d = Decimal(s.replace(",", ""))
    except InvalidOperation:
        raise ValueInvalid("amount") from None
    return -d if neg else d


def parse_int(v) -> int:
    if isinstance(v, bool):
        raise ValueInvalid("bool")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and re.fullmatch(r"[+-]?\d{1,20}", v.strip()):
        return int(v.strip())
    raise ValueInvalid("int")


def _month(word: str) -> int:
    m = _MONTHS.get(word[:3].lower())
    if m is None:
        raise ValueInvalid("month")
    return m


def parse_datetime(v, order: str = "DMY") -> tuple:
    """(year, month, day, hour, minute, second) from the date forms HR exports use: ISO 8601, DD/MM/YYYY (or
    MM/DD/YYYY with order MDY), DD-Mon-YYYY, Mon DD, YYYY, AD generalized time (YYYYMMDDHHMMSS.0Z) and Windows
    FILETIME (100 ns ticks since 1601, as AD's lastLogonTimestamp)."""
    import datetime as _dt
    if isinstance(v, int) and not isinstance(v, bool) and v > 10**15:   # FILETIME
        try:
            t = _dt.datetime(1601, 1, 1) + _dt.timedelta(microseconds=v // 10)
        except OverflowError:
            raise ValueInvalid("filetime") from None
        return (t.year, t.month, t.day, t.hour, t.minute, t.second)
    if not isinstance(v, str):
        raise ValueInvalid("date")
    s = v.strip()
    if re.fullmatch(r"\d{17,18}", s):
        return parse_datetime(int(s), order)
    for rx, kind in ((_D_ISO, "iso"), (_D_NUM, "num"), (_D_MON, "mon"), (_D_MON2, "mon2"), (_D_AD, "ad")):
        m = rx.fullmatch(s)
        if m is None:
            continue
        g = m.groups()
        if kind in ("iso", "ad"):
            y, mo, d = int(g[0]), int(g[1]), int(g[2])
            hms = g[3:6]
        elif kind == "num":
            a, b, y = int(g[0]), int(g[1]), int(g[2])
            d, mo = (a, b) if order == "DMY" else (b, a)
            hms = g[3:6]
        elif kind == "mon":
            d, mo, y = int(g[0]), _month(g[1]), int(g[2])
            hms = g[3:6]
        else:
            mo, d, y = _month(g[0]), int(g[1]), int(g[2])
            hms = (None, None, None)
        h, mi, sec = (int(x) if x else 0 for x in hms)
        try:
            _dt.datetime(y, mo, d, h, mi, sec)
        except ValueError:
            raise ValueInvalid("date") from None
        return (y, mo, d, h, mi, sec)
    raise ValueInvalid("date")


_TIME = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?")


def parse_time(v):
    """datetime.time from "09:02", "9:02 AM", "21:15:30". A full date-time is accepted too (its time part)."""
    import datetime as _dt
    if not isinstance(v, str):
        raise ValueInvalid("time")
    m = _TIME.fullmatch(v.strip())
    if m is None:
        t = parse_datetime(v)
        return _dt.time(t[3], t[4], t[5])
    h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if m.group(4):
        if not 1 <= h <= 12:
            raise ValueInvalid("time")
        h = h % 12 + (12 if m.group(4).lower() == "pm" else 0)
    try:
        return _dt.time(h, mi, sec)
    except ValueError:
        raise ValueInvalid("time") from None


_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


def parse_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().casefold()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    raise ValueInvalid("bool")


def parse_value(spec: FieldSpec, v):
    """The typed value of a field (used for COMPUTE_ONLY / KEEP / GENERALIZE inputs). Raises ValueInvalid."""
    t = spec.type
    if t in ("amount", "number"):
        return parse_amount(v)
    if t == "int":
        return parse_int(v)
    if t in ("date", "datetime"):
        return parse_datetime(v, spec.date_order)
    if t == "time":
        return parse_time(v)
    if t == "bool":
        return parse_bool(v)
    if t == "enum":
        if not isinstance(v, str):
            raise ValueInvalid("enum")
        k = v.strip().casefold()
        for e in spec.enum:
            if e.casefold() == k:
                return e
        raise ValueInvalid("enum")
    if t == "string":
        if isinstance(v, bool) or not isinstance(v, (str, int)):
            raise ValueInvalid("string")
        s = str(v).strip()
        if not s or len(s) > 512:
            raise ValueInvalid("string")
        return s
    return v


def generalize(spec: FieldSpec, typed) -> str | None:
    """The coarse value a GENERALIZE field may be used as. None = dropped."""
    m = spec.method
    if m == "drop":
        return None
    if m == "year":
        return f"{typed[0]:04d}"
    if m == "month":
        return f"{typed[0]:04d}-{typed[1]:02d}"
    if m == "band":
        x = typed
        if x < 0:
            return None
        idx = 0
        for i, e in enumerate(spec.edges):
            if x >= e:
                idx = i
        return spec.labels[idx] if x >= spec.edges[0] else None
    if m == "bitflag":
        return spec.labels[1 if (int(typed) >> spec.bit) & 1 else 0]
    if m == "pin_prefix3":
        s = re.sub(r"\s", "", str(typed))
        if not re.fullmatch(r"[1-9]\d{5}", s):
            return None
        return s[:3] + "xxx"
    return None


def generalized_pattern(spec: FieldSpec) -> re.Pattern | None:
    """Every value GENERALIZE can produce for open formats (year, month, PIN prefix); None for finite label sets."""
    return {"year": re.compile(r"\d{4}"), "month": re.compile(r"\d{4}-\d{2}"),
            "pin_prefix3": re.compile(r"[1-9]\d{2}xxx")}.get(spec.method or "")


# --- rules files -----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Rules:
    id: str
    version: int
    compute: str
    starter: bool
    body: dict
    path: str = ""


def load_rules(path: str | Path, data: bytes | None = None) -> Rules:
    try:
        raw = yaml.safe_load(Path(path).read_text() if data is None else data.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        raise SchemaError(f"{Path(path).name}: not valid YAML") from None
    if not isinstance(raw, dict) or raw.get("kind") != "pgw-compute-rules":
        raise SchemaError(f"{Path(path).name}: kind must be pgw-compute-rules")
    rid = raw.get("rules")
    if not isinstance(rid, str) or not _NAME.fullmatch(rid) or Path(path).stem != rid:
        raise SchemaError(f"{Path(path).name}: rules id must match the file name")
    try:
        version = int(raw["version"])
    except (KeyError, TypeError, ValueError):
        raise SchemaError(f"{rid}: version is required") from None
    body = {k: v for k, v in raw.items() if k not in ("kind", "rules", "version", "compute", "starter", "note")}
    return Rules(rid, version, str(raw.get("compute", "")), bool(raw.get("starter", False)), body, str(path))
