"""RECORDS route: field-level contracts over HRMS records (docs/HRMS_CONTRACTS.md, SECURITY_BOUNDARY.md §12).

The LOCAL_COMPUTE pattern, extended from a handful of typed fields to whole HRMS exports. A record contract names
its source schema(s) (pgw.product.fieldschema), the fields its purpose SELECTS (projection happens first:
minimisation), one deterministic local computation (pgw.product.hrcompute) with its sealed rules, and a prompt
template. Values are classified by the schema, not by detectors. Only computed results, aliased identities and
sealed KEEP/GENERALIZE values leave; every other value (selected or not, known or unknown) is withheld and checked
for at egress.

Request: one user message whose content is JSON
    {"source": SCHEMA_ID, "format": "json" | "csv" | "xml", "data": <JSON value or CSV/XML text>,
     "params": {...}, "delimiter": ","}
signed by an allowlisted source like every contracted request (pgw.product.attest).

How the prompt is built so that what leaves is exactly what the contract allows:
  * every computed value (counts, codes, band labels, KEEP/generalised values, row numbers) is rendered as one
    private-use sentinel character, so detectors never re-read it and it is substituted back only after the
    withheld-value check;
  * identity values (ALIAS fields) of listed rows are written raw and declared to the pipeline, which aliases them
    exactly like LOCAL_COMPUTE inputs (restored in the answer);
  * everything else in the prompt is the contract's sealed template text.
After protection, the prompt with sentinels still in place, with aliases and the sealed template text blanked, must
contain no withheld value: no withheld string (Aho-Corasick, case-insensitive), no withheld number in any written
form (Indian/western grouping, decimals, units), no withheld date in any common format. Then the sentinels are
replaced by their validated tokens and the regex/identifier detectors run over the final text as defence in depth.
"""
from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import ahocorasick

from ..egress import _EMITTED_ALIAS
from ..types import EgressViolation
from ..types import EntityType as _T
from . import amounts, disclosure, hrcompute, recparse
from .contracts import Contract, ContractError, DataClass
from .fieldschema import (FieldSchema, SchemaError, ValueInvalid, generalize, generalized_pattern, header_key,
                          load_rules, load_schema, parse_datetime, parse_amount, parse_edges, parse_value,
                          band_labels)
from .local_compute import PayloadError

MAX_ROWS_CEILING = 200_000
MAX_BYTES_CEILING = 64 * 1024 * 1024
MAX_PROMPT_CHARS_CEILING = 100_000
MAX_LISTED_CEILING = 1000
MAX_TIMEOUT_S = 120.0
MAX_UNKNOWN_REPORTED = 100
MAX_NEEDLES = 3_000_000
SENTINEL_BASE = 0xF0000            # Supplementary Private Use Area-A: no detector matches it, no input keeps it
_SENTINEL_RE = re.compile("[\U000F0000-\U000FFFFD]")
_ID_CLEAN = re.compile(r"[\x00-\x1f\x7f  -\U000F0000-\U0010FFFF]+")
ENVELOPE_KEYS = {"source", "format", "data", "params", "delimiter"}
T_IDENTIFIER = _T.IDENTIFIER


# =============================================================================================================
# contract block
# =============================================================================================================

@dataclass(frozen=True)
class RecordSpec:
    sources: tuple
    select: tuple
    label: tuple
    show: tuple
    compute: str
    rules: str | None
    max_rows: int
    max_bytes: int
    max_listed: int
    max_prompt_chars: int
    timeout_s: float
    bands: dict = field(default_factory=dict, compare=False, hash=False)   # output name -> (edges, labels)
    min_group_size: int | None = None   # may only raise the sealed disclosure_control k (pgw.product.disclosure)
    dataset: str | None = None          # sealed dataset label: part of the differencing-history scope (with the schema)


def _int(raw, where, lo, hi) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or not lo <= raw <= hi:
        raise ContractError(f"records.{where}: integer between {lo} and {hi}")
    return raw


def _ids(raw, where, lo=1, hi=200) -> tuple:
    if not isinstance(raw, list) or not lo <= len(raw) <= hi or not all(
            isinstance(x, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,47}", x) for x in raw) or len(set(raw)) != len(raw):
        raise ContractError(f"records.{where}: {lo}-{hi} distinct names")
    return tuple(raw)


def parse_spec(raw, outputs_raw) -> RecordSpec:
    """The contract's `records:` block (structure only; pgw.product.records.bind checks it against the schemas,
    the computation and its rules at startup)."""
    if not isinstance(raw, dict):
        raise ContractError("RECORDS contracts need a records block")
    allowed = {"sources", "select", "label", "show", "compute", "rules", "max_rows", "max_bytes", "max_listed",
               "max_prompt_chars", "timeout_s", "min_group_size", "dataset"}
    if set(raw) - allowed:
        raise ContractError(f"records: unknown keys {sorted(set(raw) - allowed)}")
    compute = raw.get("compute")
    if compute not in hrcompute.COMPUTATIONS:
        raise ContractError(f"records.compute: unknown computation (one of {sorted(hrcompute.COMPUTATIONS)})")
    rules = raw.get("rules")
    if rules is not None and not (isinstance(rules, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,47}", rules)):
        raise ContractError("records.rules: a rules file id")
    timeout = raw.get("timeout_s", 30)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_TIMEOUT_S:
        raise ContractError(f"records.timeout_s: 1-{MAX_TIMEOUT_S} seconds")
    dataset = raw.get("dataset")
    if dataset is not None and not (isinstance(dataset, str) and disclosure.DATASET_RE.fullmatch(dataset)):
        raise ContractError("records.dataset: a dataset label ([a-z][a-z0-9_.-], up to 64 characters)")
    bands = {}
    for name, o in (outputs_raw or {}).items():
        if isinstance(o, dict) and "band" in o:
            unit = str(o.get("unit", "₹"))
            if unit not in ("₹", "%"):
                raise ContractError(f"output {name}: unit must be ₹ or %")
            try:
                edges = parse_edges(o["band"], f"output {name}.band", min_width=unit == "₹")
            except SchemaError as e:
                raise ContractError(str(e)) from None
            bands[str(name)] = (edges, tuple(band_labels(list(edges), unit)))
    return RecordSpec(
        sources=_ids(raw.get("sources"), "sources", 1, 10), select=_ids(raw.get("select"), "select"),
        label=_ids(raw.get("label") or [], "label", 0, 4), show=_ids(raw.get("show") or [], "show", 0, 10),
        compute=compute, rules=rules, max_rows=_int(raw.get("max_rows", 50_000), "max_rows", 1, MAX_ROWS_CEILING),
        max_bytes=_int(raw.get("max_bytes", 20_000_000), "max_bytes", 1024, MAX_BYTES_CEILING),
        max_listed=_int(raw.get("max_listed", 200), "max_listed", 0, MAX_LISTED_CEILING),
        max_prompt_chars=_int(raw.get("max_prompt_chars", 30_000), "max_prompt_chars", 500, MAX_PROMPT_CHARS_CEILING),
        timeout_s=float(timeout), bands=bands,
        min_group_size=None if raw.get("min_group_size") is None else _int(raw["min_group_size"], "min_group_size",
                                                                           disclosure.ABSOLUTE_MIN_K,
                                                                           disclosure.MAX_K),
        dataset=dataset)


# =============================================================================================================
# binding (startup)
# =============================================================================================================

@dataclass(frozen=True)
class Plan:
    purpose: str
    contract: Contract
    spec: RecordSpec
    schemas: dict                 # id -> FieldSchema
    computation: hrcompute.Computation
    rules: object
    rules_ref: str | None
    starter_rules: bool
    view: dict                    # selected name -> disposition ("ALIAS", "KEEP", "GENERALIZE", "COMPUTE_ONLY",
    #                               "PRESENCE")
    kinds: dict                   # output name -> kind (the computation's catalog)
    literals: tuple               # the template's literal text (the gateway's own words)
    control: disclosure.Control | None = None   # small-group disclosure control (sealed disclosure_control.yaml)

    @property
    def k(self) -> int:
        return self.control.k if self.control is not None else 1


def schema_files(registry_dir: Path) -> list[str]:
    """Every schema and rules file under the registry directory. All of them are sealed (verified at startup), not
    only the ones a contract uses, so an unsealed file dropped next to them stops startup as well."""
    out = []
    for sub in ("schemas", "rules"):
        out += [str(p) for p in sorted((Path(registry_dir) / sub).glob("*.yaml"))]
    return out


def _view(spec_by_schema: dict, name: str) -> str:
    disps = set()
    for sid, s in spec_by_schema.items():
        f = s.spec(name)
        if f is None:
            raise ContractError(f"records.select: field {name!r} is not defined by schema {sid}")
        if f.presence_flag == name:
            disps.add("PRESENCE")
            continue
        disps.add(f.disposition)
        if f.disposition in ("NEVER", "REMOVE"):
            raise ContractError(f"records.select: {name!r} is {f.disposition} in schema {sid} and can never be "
                                "selected")
    if len(disps) != 1:
        raise ContractError(f"records.select: {name!r} has different dispositions across sources")
    return disps.pop()


def bind(purpose: str, contract: Contract, registry_dir: Path, read) -> Plan:
    """Resolve a RECORDS contract against its sealed schemas and rules. Any inconsistency stops startup."""
    spec: RecordSpec = contract.records
    schemas = {}
    for sid in spec.sources:
        p = Path(registry_dir) / "schemas" / f"{sid}.yaml"
        if not p.is_file():
            raise ContractError(f"purpose {purpose!r}: schema {sid!r} not found in {p.parent}")
        schemas[sid] = load_schema(p, read(p))
    comp = hrcompute.COMPUTATIONS[spec.compute]
    rules, rules_ref, starter_rules = None, None, False
    if spec.rules is None:
        if comp.needs_rules:
            raise ContractError(f"purpose {purpose!r}: {spec.compute} needs a sealed rules file (records.rules)")
        rules = comp.parse_rules({})
    else:
        p = Path(registry_dir) / "rules" / f"{spec.rules}.yaml"
        if not p.is_file():
            raise ContractError(f"purpose {purpose!r}: rules {spec.rules!r} not found in {p.parent}")
        r = load_rules(p, read(p))
        if r.compute != spec.compute:
            raise ContractError(f"purpose {purpose!r}: rules {r.id} are for {r.compute!r}, not {spec.compute!r}")
        rules, rules_ref, starter_rules = comp.parse_rules(r.body), f"{r.id}@{r.version}", r.starter
    view = {n: _view(schemas, n) for n in spec.select}
    for n, allowed in comp.inputs(rules).items():
        optional = "OPTIONAL" in allowed
        if n not in view:
            if optional:
                continue
            raise ContractError(f"purpose {purpose!r}: {spec.compute} reads {n!r}, which records.select does not "
                                "select")
        if view[n] not in allowed:
            raise ContractError(f"purpose {purpose!r}: {spec.compute} needs {n!r} as {'/'.join(allowed)}, the "
                                f"schema makes it {view[n]}")
    for n in spec.label:
        if view.get(n) != "ALIAS":
            raise ContractError(f"records.label: {n!r} must be a selected ALIAS field")
    for n in spec.show:
        if view.get(n) not in ("KEEP", "GENERALIZE"):
            raise ContractError(f"records.show: {n!r} must be a selected KEEP or GENERALIZE field")
    if not spec.label and any(k == "list" for k in comp.catalog.values()) and any(
            o in contract.outputs and contract.outputs[o].egress != "INTERNAL"
            for o, k in comp.catalog.items() if k == "list"):
        raise ContractError("records.label: a contract that lists rows must name the ALIAS fields that label them")
    # outputs: only the computation's catalog; egress by kind
    for name, o in contract.outputs.items():
        kind = comp.catalog.get(name)
        if kind is None:
            raise ContractError(f"output {name}: not an output of {spec.compute} ({sorted(comp.catalog)})")
        if kind in hrcompute.BANDED:
            if o.egress == "RAW":
                raise ContractError(f"output {name}: an {kind} never leaves raw (INTERNAL, or TRANSFORMED with a band)")
            if o.egress == "TRANSFORMED" and name not in spec.bands:
                raise ContractError(f"output {name}: TRANSFORMED {kind} needs sealed band edges (band: [...])")
        elif o.egress == "TRANSFORMED":
            raise ContractError(f"output {name}: {kind} outputs leave RAW (with justification) or not at all")
    literals = tuple(lit for lit, _f, _s, _c in string.Formatter().parse(contract.prompt_template or "") if lit)
    used = {f for _l, f, _s, _c in string.Formatter().parse(contract.prompt_template or "") if f}
    for f in used:
        if f not in contract.outputs or contract.outputs[f].egress == "INTERNAL":
            raise ContractError(f"prompt_template: {f!r} is not a declared non-INTERNAL output")
    if contract.content_gate is not None:
        raise ContractError("RECORDS contracts classify fields by schema; content_gate does not apply")
    if contract.classes[DataClass.C1] not in ("ALIAS", "REDACT"):
        raise ContractError("RECORDS contracts alias (or redact) identities: C1 must be ALIAS or REDACT")
    # small-group disclosure control: mandatory sealed config; a contract may raise k, never lower it
    cp = disclosure.config_path(registry_dir)
    if not cp.is_file():
        raise ContractError(f"purpose {purpose!r}: the disclosure control config {cp.name} is missing (RECORDS "
                            "contracts never run without small-group control)")
    try:
        cfg = disclosure.load_config(cp, read(cp))
        controls = {sid: disclosure.schema_control(cfg, s) for sid, s in schemas.items()}
    except SchemaError as e:
        raise ContractError(str(e)) from None
    for sid in cfg.schemas:
        if not (Path(registry_dir) / "schemas" / f"{sid}.yaml").is_file():
            raise ContractError(f"{cp.name}: schemas.{sid} names no schema in registry/schemas")
    k = cfg.k
    if spec.min_group_size is not None:
        if spec.min_group_size < cfg.k:
            raise ContractError(f"records.min_group_size: may only raise the sealed min_group_size ({cfg.k})")
        k = spec.min_group_size
    control = disclosure.Control(cfg, k, cfg.listing, controls, differencing=cfg.differencing, rounding=cfg.rounding)
    return Plan(purpose, contract, spec, schemas, comp, rules, rules_ref, starter_rules, view, dict(comp.catalog),
                literals, control)


# =============================================================================================================
# withheld values and the egress check
# =============================================================================================================

def _nkey(d: Decimal) -> str:
    d = abs(d)
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")


_NUM_TOKEN = re.compile(r"(?<![\d.,])\d{1,3}(?:[,， ' ]\d{2,3})+(?:\.\d+)?(?![\d,]\d)|\d+(?:\.\d+)?")
_DATE_SCAN = [re.compile(r"\d{4}-\d{1,2}-\d{1,2}"), re.compile(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}"),
              re.compile(r"\d{1,2}[ \-][A-Za-z]{3,9}[ \-,]+\d{4}"), re.compile(r"[A-Za-z]{3,9}\.? \d{1,2},? \d{4}"),
              re.compile(r"\d{8}")]


class Withheld:
    """Every value of the input that is not KEEP: strings (case-insensitive), numbers (by value), dates (by day)."""

    def __init__(self):
        self.strings: set = set()
        self.numbers: set = set()
        self.dates: set = set()
        self.exempt: set = set()      # KEEP values (sealed enumerations): never needles
        self._auto = None
        self._seen: set = set()       # (value, never, hint) already added: exports repeat values a lot

    def add(self, v, never: bool = False, typed=None, hint: str = "") -> None:
        """hint: the field's declared type ("amount", "date", ...) when known, so only that parse is tried."""
        key = (v, never, hint) if isinstance(v, str) else None
        if key is not None:
            if key in self._seen:
                return
            self._seen.add(key)
        if v is None or isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            if isinstance(v, float) and v != v:
                return
            d = Decimal(str(v))
            if abs(d) >= 1000 or (d != d.to_integral_value() and len(d.normalize().as_tuple().digits) >= 4):
                self.numbers.add(_nkey(d))
            if isinstance(v, int) and v > 10**15:
                try:
                    y, m, dd = parse_datetime(v)[:3]
                    self.dates.add((y, m, dd))
                except ValueInvalid:
                    pass
            return
        if not isinstance(v, str):
            return
        s = v.strip()
        if len(s) >= 4:
            self.strings.add(s.casefold()[:256])
            if never:   # a special-category value: its words never leave either ("Oncology" from "Oncology OPD")
                self.strings.update(w.casefold() for w in re.findall(r"[^\W\d_]{4,}", s))
        if typed is not None:
            self._typed(typed)
            return
        if any(c.isdigit() for c in s) and len(s) <= 40:
            if hint in ("amount", "number", "int"):
                try:
                    d = parse_amount(s)
                    if abs(d) >= 1000 or d != d.to_integral_value():
                        self.numbers.add(_nkey(d))
                except ValueInvalid:
                    pass
                return
            try:
                d = parse_amount(s)
                if abs(d) >= 1000 or d != d.to_integral_value():
                    self.numbers.add(_nkey(d))
            except ValueInvalid:
                pass
            try:
                self.dates.add(parse_datetime(s)[:3])
            except ValueInvalid:
                pass

    def _typed(self, typed) -> None:
        if isinstance(typed, Decimal):
            if abs(typed) >= 1000 or typed != typed.to_integral_value():
                self.numbers.add(_nkey(typed))
        elif isinstance(typed, int) and not isinstance(typed, bool) and abs(typed) >= 1000:
            self.numbers.add(str(abs(typed)))
        elif isinstance(typed, tuple) and len(typed) >= 3:
            self.dates.add(tuple(typed[:3]))

    def finalize(self) -> None:
        needles = self.strings - self.exempt
        if len(needles) > MAX_NEEDLES:
            raise PayloadError("too many distinct values for this data contract")
        auto = ahocorasick.Automaton()
        for n in needles:
            auto.add_word(n, n)
        if len(auto):
            auto.make_automaton()
            self._auto = auto

    def check(self, text: str) -> list[str]:
        """Kinds of withheld values found in `text` (already blanked of sanctioned content)."""
        hay = text.casefold()
        hits = []
        if self._auto is not None:
            for _end, _n in self._auto.iter(hay):
                hits.append("string")
                break
        for m in _NUM_TOKEN.finditer(hay):
            tok = re.sub(r"[,， ' ]", "", m.group(0))
            try:
                if _nkey(Decimal(tok)) in self.numbers:
                    hits.append("number")
                    break
            except ArithmeticError:
                continue
        for i in range(0, len(text), 15_000):    # numbers written with units or in words ("2.45 lakh")
            for _s, _e, value, _u in amounts.numbers(text[i:i + 20_000]):
                d = Decimal(str(value))
                if _nkey(d) in self.numbers or (d == d.to_integral_value() and str(int(d)) in self.numbers):
                    hits.append("number")
                    break
        for rx in _DATE_SCAN:
            for m in rx.finditer(text):
                for order in ("DMY", "MDY"):
                    try:
                        if parse_datetime(m.group(0), order)[:3] in self.dates:
                            hits.append("date")
                    except ValueInvalid:
                        pass
        return hits


# =============================================================================================================
# extraction: schema-driven classification
# =============================================================================================================

@dataclass
class _Stats:
    by_path: dict = field(default_factory=dict)       # schema path -> values seen
    unknown_paths: dict = field(default_factory=dict)  # unknown path -> values dropped
    invalid: int = 0
    keep_rejected: int = 0


class _Extractor:
    def __init__(self, schema: FieldSchema, select: tuple, view: dict, withheld: Withheld):
        self.schema, self.select, self.view, self.w = schema, select, view, withheld
        self.stats = _Stats()
        self.selected_specs = {n: schema.spec(n) for n in select}
        self.present = dict.fromkeys(select, 0)
        self.typed_selected = {sp for n, sp in self.selected_specs.items()
                               if view.get(n) in ("COMPUTE_ONLY", "GENERALIZE") and sp.presence_flag != n
                               and not sp.multi}

    # -- leaves ------------------------------------------------------------------------------------------------
    def _drop(self, obj, path: str, never: bool, depth: int, unknown: bool) -> int:
        """Count and withhold every scalar under obj. Returns the number of scalars."""
        n = 0
        stack = [(obj, depth)]
        while stack:
            o, d = stack.pop()
            if d > recparse.MAX_DEPTH:
                raise PayloadError("payload nests too deeply")
            if isinstance(o, dict):
                stack.extend((v, d + 1) for v in o.values())
            elif isinstance(o, list):
                stack.extend((v, d + 1) for v in o)
            elif o is not None and o != "":
                n += 1
                self.w.add(o, never=never)
        if unknown and n:
            key = path[:120]
            if key in self.stats.unknown_paths or len(self.stats.unknown_paths) < 10_000:
                self.stats.unknown_paths[key] = self.stats.unknown_paths.get(key, 0) + n
            else:
                self.stats.unknown_paths["(more)"] = self.stats.unknown_paths.get("(more)", 0) + n
        return n

    def _leaf(self, spec, value, rec: dict, depth: int) -> None:
        if spec.subtree:
            self.stats.by_path[spec.path] = self.stats.by_path.get(spec.path, 0) + self._drop(
                value, spec.path, spec.disposition == "NEVER", depth, False)
            return
        items = value if isinstance(value, list) else [value]
        for it in items:
            if it is None or (isinstance(it, str) and not it.strip()):
                continue
            if isinstance(it, (dict, list)):
                self._drop(it, spec.path, spec.disposition == "NEVER", depth, False)
                rec.setdefault(spec, []).append(_INVALID)
                continue
            self.stats.by_path[spec.path] = self.stats.by_path.get(spec.path, 0) + 1
            rec.setdefault(spec, []).append(it)

    def _walk(self, obj, node, path: str, depth: int, rec: dict) -> None:
        if depth > recparse.MAX_DEPTH:
            raise PayloadError("payload nests too deeply")
        if not isinstance(obj, dict):
            self._drop(obj, path or "(record)", True, depth, True)
            return
        for k, v in obj.items():
            child = node.children.get(k)
            p = f"{path}.{k}" if path else str(k)
            if child is None and node is self.schema.trie and isinstance(k, str):
                # flat exports keyed by their column headings (RaaS / register JSON): the schema's CSV header names
                spec = self.schema.csv_headers.get(header_key(k))
                if spec is not None and len(spec.segments) == 1:
                    self._leaf(spec, v, rec, depth + 1)
                    continue
            if child is None:
                self._drop(v, p, True, depth + 1, True)
            elif child.spec is not None:
                self._leaf(child.spec, v, rec, depth + 1)
            elif child.array:
                for it in (v if isinstance(v, list) else [v]):
                    self._walk(it, child, p + "[]", depth + 1, rec)
            elif isinstance(v, list):
                self._drop(v, p, True, depth + 1, True)
            else:
                self._walk(v, child, p, depth + 1, rec)

    # -- rows ----------------------------------------------------------------------------------------------------
    def row(self, index: int, rec: dict) -> hrcompute.Row:
        """Classify one record: every value is withheld unless KEEP; selected fields become typed row values."""
        row = hrcompute.Row(index)
        for spec, raws in rec.items():
            if spec.disposition == "KEEP" or (spec in self.typed_selected):
                continue                  # selected typed fields are withheld below, from their parsed value
            never = spec.disposition == "NEVER"
            for x in raws:
                if x is not _INVALID:
                    self.w.add(x, never=never, hint=spec.type)
        flags = {}
        for spec, raws in rec.items():
            if spec.presence_flag:
                flags[spec.presence_flag] = any(x is not _INVALID and str(x).strip().casefold() not in spec.work_values
                                                for x in raws)
        for name in self.select:
            spec = self.selected_specs[name]
            disp = self.view[name]
            if disp == "PRESENCE":
                row.values[name] = flags.get(name, False)
                self.present[name] += 1
                continue
            raws = rec.get(spec)
            if not raws:
                row.values[name] = None
                row.errors[name] = "MISSING"
                continue
            self.present[name] += 1
            if _INVALID in raws or (not spec.multi and len(raws) > 1):
                row.values[name], row.errors[name] = None, "INVALID"
                self.stats.invalid += 1
                continue
            try:
                if disp == "ALIAS":
                    vals = [_ID_CLEAN.sub(" ", str(x)).strip()[:200] for x in raws]
                    vals = [x for x in vals if x]
                    row.values[name] = (vals if spec.multi else vals[0]) if vals else None
                    if not vals:
                        row.errors[name] = "MISSING"
                elif disp == "GENERALIZE":
                    self.w.add(raws[0], hint=spec.type)      # the raw form (e.g. "12/04/1987") as a string too
                    typed = parse_value(spec, raws[0])
                    self.w._typed(typed)
                    row.values[name] = generalize(spec, typed)
                else:
                    if disp != "KEEP" and spec.type not in ("amount", "number", "int"):
                        self.w.add(raws[0], hint=spec.type)  # strings/dates/times: the raw form as a needle too
                    typed = parse_value(spec, raws[0])
                    if disp == "KEEP":
                        self.w.exempt.add(str(typed).casefold())
                    else:
                        self.w._typed(typed)
                    row.values[name] = typed
            except ValueInvalid:
                row.values[name], row.errors[name] = None, "INVALID"
                self.stats.invalid += 1
                if disp == "KEEP":
                    self.stats.keep_rejected += 1
                for x in raws:         # not a parsed (or sealed) value: withheld as written
                    self.w.add(x)
        # KEEP values that are not selected are not used; outside the enumeration they are withheld too
        for spec, raws in rec.items():
            if spec.disposition == "KEEP" and spec.name not in self.selected_specs:
                for x in raws:
                    try:
                        self.w.exempt.add(str(parse_value(spec, x)).casefold())
                    except ValueInvalid:
                        self.w.add(x)
        return row


_INVALID = object()


def _records_of(doc, schema: FieldSchema, ex: _Extractor):
    """Navigate to the records array; everything outside it is unknown (dropped, counted)."""
    node_obj = doc
    segs = schema.records_at
    path = ""
    for i, (name, arr) in enumerate(segs):
        last = i == len(segs) - 1
        if name == "$" and i == 0:
            target = node_obj
        else:
            if not isinstance(node_obj, dict) or name not in node_obj:
                raise PayloadError("records not found where the schema expects them")
            for k, v in node_obj.items():          # siblings of the records path: dropped as unknown
                if k != name:
                    ex._drop(v, f"{path}.{k}" if path else str(k), True, i + 1, True)
            target = node_obj[name]
            path = f"{path}.{name}" if path else name
        if arr:
            items = target if isinstance(target, list) else [target]
            if not last:
                if len(items) != 1:
                    raise PayloadError("records path crosses an array of several objects")
                node_obj = items[0]
                continue
            return items, (path + "[]" if path else "[]")
        node_obj = target
    raise PayloadError("records not found where the schema expects them")


def extract(plan: Plan, schema: FieldSchema, fmt: str, data, delimiter: str):
    w = Withheld()
    ex = _Extractor(schema, plan.spec.select, plan.view, w)
    rows: list = []
    max_rows = plan.spec.max_rows
    if fmt == "csv":
        if not isinstance(data, str):
            raise PayloadError("csv data must be a string")
        header, it = recparse.parse_csv(data, max_rows, delimiter)
        cols = []
        seen = set()
        for h in header:
            spec = schema.csv_headers.get(header_key(h))
            if spec is not None and spec in seen:
                raise PayloadError("CSV header names one field twice")
            if spec is not None:
                seen.add(spec)
            cols.append(spec)
        unknown_cols = [i for i, s in enumerate(cols) if s is None]
        for n, cells in enumerate(it, 1):
            rec: dict = {}
            for i, spec in enumerate(cols):
                if spec is None:
                    continue
                cell = cells[i]
                if cell.strip():
                    ex.stats.by_path[spec.path] = ex.stats.by_path.get(spec.path, 0) + 1
                    rec[spec] = [cell]
            for i in unknown_cols:
                if cells[i].strip():
                    ex.stats.unknown_paths[header[i][:120]] = ex.stats.unknown_paths.get(header[i][:120], 0) + 1
                    w.add(cells[i], never=True)
            rows.append(ex.row(n, rec))
        prefix = ""
    else:
        if fmt == "xml":
            if not isinstance(data, str):
                raise PayloadError("xml data must be a string")
            doc = recparse.parse_xml(data)
            env_ = doc.get("Envelope")
            if len(doc) == 1 and isinstance(env_, dict) and isinstance(env_.get("Body"), dict):
                doc = {k: v for k, v in env_["Body"].items() if not k.startswith("@")}   # SOAP envelope
        else:
            if not isinstance(data, (dict, list)):
                raise PayloadError("json data must be an object or an array")
            if not recparse.depth_ok(data):
                raise PayloadError("payload nests too deeply")
            doc = data
        items, prefix = _records_of(doc, schema, ex)
        if len(items) > max_rows:
            raise PayloadError("too many rows for this data contract")
        for n, item in enumerate(items, 1):
            rec = {}
            ex._walk(item, schema.trie, prefix, 1, rec)
            rows.append(ex.row(n, rec))
    for name, n in ex.present.items():
        spec = ex.selected_specs[name]
        if rows and n == 0 and not spec.optional and plan.view[name] != "PRESENCE":
            raise PayloadError(f"required field {name!r} is missing from every record of the input")
    return rows, ex.stats, w, prefix


# =============================================================================================================
# params (roster_question) and rendering
# =============================================================================================================

def _order(spec):
    if spec is None:
        return lambda x: None
    if spec.disposition == "GENERALIZE" and spec.method in ("year", "month"):
        return lambda x: x if isinstance(x, str) else None
    if spec.disposition == "GENERALIZE" and spec.method in ("band", "bitflag"):
        idx = {lab: i for i, lab in enumerate(spec.labels)}
        return lambda x: idx.get(x)
    return lambda x: None


def _filter_value(spec, value):
    """A caller-supplied filter value, accepted only if it is a value the field can take: a member of the sealed
    enumeration, a boolean, a sealed band/bitflag label, or a year/month/PIN-prefix in its fixed format."""
    if spec.disposition == "KEEP":
        try:
            return parse_value(spec, value)
        except ValueInvalid:
            raise PayloadError(f"params.filter: value for {spec.name!r} is not one of the field's values") from None
    if spec.method in ("band", "bitflag"):
        if value in spec.labels:
            return value
    else:
        pat = generalized_pattern(spec)
        if pat is not None and isinstance(value, str) and pat.fullmatch(value):
            return value
    raise PayloadError(f"params.filter: value for {spec.name!r} is not in the field's generalised format")


def parse_params(plan: Plan, raw, schema: FieldSchema) -> dict:
    if plan.spec.compute != "roster_question":
        if raw not in (None, {}):
            raise PayloadError("this data contract takes no params")
        return {}
    if not isinstance(raw, dict) or set(raw) - {"filter", "group_by", "list"}:
        raise PayloadError("params: filter, group_by and list only")
    rules = plan.rules
    clauses = raw.get("filter") or []
    if not isinstance(clauses, list) or len(clauses) > rules["max_filter_clauses"]:
        raise PayloadError("params.filter: a list of at most the allowed number of clauses")
    out, text = [], []
    filterable = {n for n, d in plan.view.items() if d in ("KEEP", "GENERALIZE")}
    for c in clauses:
        if not isinstance(c, dict) or set(c) != {"field", "op", "value"} or c["field"] not in filterable \
                or c["op"] not in hrcompute.OPS:
            raise PayloadError("params.filter: each clause is {field, op, value} on a selected KEEP or GENERALIZE "
                               "field with op eq/ne/in/gte/lte")
        spec = schema.spec(c["field"])
        op = c["op"]
        if op == "in":
            vals = c["value"]
            if not isinstance(vals, list) or not 1 <= len(vals) <= rules["max_in_values"]:
                raise PayloadError("params.filter: in takes a list of allowed length")
            val = tuple(_filter_value(spec, x) for x in vals)
            shown = "[" + ", ".join(str(x) for x in val) + "]"
        else:
            val = _filter_value(spec, c["value"])
            if op in ("gte", "lte") and _order(spec)(val) is None:
                raise PayloadError(f"params.filter: {spec.name!r} has no order (eq/ne/in only)")
            shown = str(val)
        out.append((spec.name, op, val, _order(spec)))
        text.append(f"{spec.name} {({'eq': '=', 'ne': '!=', 'in': 'in', 'gte': '>=', 'lte': '<='})[op]} {shown}")
    gb = raw.get("group_by")
    if gb is not None and gb not in filterable:
        raise PayloadError("params.group_by: a selected KEEP or GENERALIZE field")
    lst = raw.get("list", False)
    if not isinstance(lst, bool):
        raise PayloadError("params.list: true or false")
    return {"filter": out, "group_by": gb, "list": lst,
            "filter_text": " and ".join(text) if text else "all records"}


class _Ctx:
    """What a computation may ask about a row besides its values: a grouping key and the text of `show` fields."""

    def __init__(self, plan: Plan):
        self.plan = plan
        self.k = plan.k
        self.stats: dict = {}
        self._people = tuple(n for n in plan.spec.label
                             if all(s.spec(n) is not None and s.spec(n).entity is not T_IDENTIFIER
                                    for s in plan.schemas.values()))

    def identity_key(self, row) -> tuple:
        key = tuple(str(row.values.get(n)) for n in self.plan.spec.label if row.values.get(n) is not None)
        return key or ("#row", row.index)

    def person_key(self, row) -> tuple:
        """A person (not a work order or other reference): the label fields that name people."""
        key = tuple(str(row.values.get(n)) for n in self._people if row.values.get(n) is not None)
        return key or self.identity_key(row)


@dataclass
class _Disclosure:
    """What disclosure control decided for one response (pgw.product.disclosure): markers that replace printed
    values, per-row attribute text of listings, and counts for the transparency report (never values)."""
    markers: dict = field(default_factory=dict)       # output name -> marker text
    group_marks: dict = field(default_factory=dict)   # groups output -> {position: marker}
    list_withheld: dict = field(default_factory=dict)  # list output -> text replacing the whole list
    show: dict = field(default_factory=dict)          # list output -> {id(row): attribute text}
    prewithhold: dict = field(default_factory=dict)   # group_by field -> values whose count was suppressed
    stats: dict = field(default_factory=lambda: dict.fromkeys(
        ("cells_suppressed", "cells_suppressed_complementary", "totals_suppressed", "groups_merged",
         "groups_suppressed", "rows_coarsened", "rows_attributes_withheld", "lists_withheld", "queries_refused",
         "queries_compared", "history_queries", "counts_rounded"), 0))


def _leaves(plan: Plan, name: str) -> bool:
    o = plan.contract.outputs.get(name)
    return o is not None and o.egress != "INTERNAL"


def record_key_fields(plan: Plan) -> tuple:
    """The fields whose values identify a record across exports (the differencing history's record key): the
    contract's label fields that are employee identifiers in every source schema (stable usernames / employee ids),
    else the label fields that name people, else every label field. Changing such a value between exports makes the
    record count as a leaver plus a joiner."""
    label = plan.spec.label

    def ent(n, e):
        return all(s.spec(n) is not None and s.spec(n).entity is e for s in plan.schemas.values())
    ids = tuple(n for n in label if ent(n, _T.EMPLOYEE_ID))
    people = tuple(n for n in label if not ent(n, T_IDENTIFIER))
    return ids or people or tuple(label)


def _identity(fields: tuple):
    """Row -> the bytes the record key is computed from (never stored): the key fields' values, or one shared
    marker for a record without any (such records count with multiplicity, see disclosure.keyset)."""
    def ident(row) -> bytes:
        vals = [row.values.get(n) for n in fields]
        if all(v is None for v in vals):
            return b'["#no-record-key"]'
        return json.dumps(vals, ensure_ascii=False, default=str).encode("utf-8")
    return ident


def _history_check(plan: Plan, schema: FieldSchema, t, params: dict, rows: list, outs: dict, history,
                   dc: _Disclosure, k: int) -> None:
    """Differencing across the durable history of (tenant, app, purpose, dataset): every count this response
    reveals, as the set of keyed record hashes behind it (pgw.product.disclosure.HistoryStore)."""
    mc = outs["matched_count"]
    flt = params["filter"]
    matched_idx = mc.meta.get("matched", frozenset())
    matched = [r for r in rows if r.index in matched_idx]
    missing = [r for r in rows if r.index not in matched_idx and hrcompute.roster_match(r, flt) == -1]
    missing_idx = {r.index for r in missing}
    cells = t.cells

    def shown(n):
        return cells[n].shown

    def exact(n):
        return cells[n].shown and not cells[n].suppressed
    ident = _identity(record_key_fields(plan))
    gb = params.get("group_by")
    gsizes = outs["group_counts"].meta.get("sizes") or []

    def build(subkey: bytes) -> list:
        cache: dict = {}

        def ks(rs):
            return disclosure.keyset(rs, subkey, ident, cache)
        RS = disclosure.RevealedSet
        out = []
        filtered = bool(flt)
        if shown("total_rows"):
            out.append(RS("total", ks(rows), exact("total_rows"), whole=True))
        if shown("matched_count"):
            out.append(RS("matched", ks(matched), exact("matched_count"), whole=not filtered))
        if shown("rows_missing_filter_field"):
            out.append(RS("missing", ks(missing), exact("rows_missing_filter_field")))
        if shown("total_rows") and shown("matched_count") and filtered:
            out.append(RS("complement", ks([r for r in rows if r.index not in matched_idx]),
                          exact("total_rows") and exact("matched_count")))
            if shown("rows_missing_filter_field") and missing:
                out.append(RS("did_not_match", ks([r for r in rows if r.index not in matched_idx
                                                   and r.index not in missing_idx]),
                              exact("total_rows") and exact("matched_count") and exact("rows_missing_filter_field")))
        if gb and gsizes and _leaves(plan, "group_counts"):
            by: dict = {}
            for r in matched:
                by.setdefault(r.values.get(gb), []).append(r)
            for i, (key, _n) in enumerate(gsizes):
                out.append(RS("group", ks(by.get(key, [])), not cells[f"#group{i}"].suppressed))
        return out
    predicate = {"filter": [[n, op, list(v) if isinstance(v, tuple) else v] for n, op, v, _o in flt],
                 "group_by": gb, "list": bool(params.get("list"))}
    try:
        st = history.store.check_and_record(history, disclosure.dataset_id(schema.id, plan.spec.dataset), predicate,
                                            build, k, plan.control.cfg)
    except disclosure.DisclosureRefused:
        dc.stats["queries_refused"] += 1
        raise
    dc.stats["queries_compared"] = st["queries_compared"]
    dc.stats["history_queries"] = st["history_queries"]


def _roster_control(plan: Plan, outs: dict, params: dict, rows: list, dc: _Disclosure, k: int, history,
                    schema: FieldSchema) -> None:
    """Count table of a roster query: total = matched + missing + did-not-match; matched = sum of the groups."""
    t = disclosure.Table(k)
    mc = outs["matched_count"]
    filtered = mc.meta.get("filtered", True)
    t.add(disclosure.Cell("total_rows", int(outs["total_rows"].value), False, 0, shown=_leaves(plan, "total_rows"),
                          total=True))
    t.add(disclosure.Cell("matched_count", int(mc.value), filtered, 3, shown=_leaves(plan, "matched_count") or (
        bool(params.get("list")) and _leaves(plan, "matched")), total=True, reveals_small=True))
    t.add(disclosure.Cell("rows_missing_filter_field", int(outs["rows_missing_filter_field"].value), True, 1,
                          shown=_leaves(plan, "rows_missing_filter_field")))
    t.add(disclosure.Cell("#rest", int(mc.meta.get("rest", 0)), filtered, 9, shown=False))
    t.eq("total_rows", ["matched_count", "rows_missing_filter_field", "#rest"])
    gc = outs["group_counts"]
    sizes = gc.meta.get("sizes") or []
    gnames = [f"#group{i}" for i in range(len(sizes))]
    if sizes:
        for gn, (_key, n) in zip(gnames, sizes, strict=True):
            t.add(disclosure.Cell(gn, int(n), True, 2, shown=_leaves(plan, "group_counts")))
        t.eq("matched_count", gnames)
    st = t.protect()
    dc.stats["cells_suppressed"] += st["primary"]
    dc.stats["cells_suppressed_complementary"] += st["complementary"]
    dc.stats["totals_suppressed"] += st["totals"]
    for n in ("total_rows", "matched_count", "rows_missing_filter_field"):
        m = t.marker(n)
        if m is not None:
            dc.markers[n] = m
    suppressed_vals = set()
    for i, gn in enumerate(gnames):
        m = t.marker(gn)
        if m is not None:
            dc.group_marks.setdefault("group_counts", {})[i] = m
            suppressed_vals.add(sizes[i][0])
    if t.cells["matched_count"].suppressed and params.get("list"):
        # a listing would print the matched count it withholds
        c = t.cells["matched_count"]
        dc.list_withheld["matched"] = (f"none listed: fewer than {k} records match" if c.primary
                                      else "none listed (withheld by disclosure control)")
        dc.stats["lists_withheld"] += 1
    if history is not None and plan.control is not None and plan.control.differencing:
        _history_check(plan, schema, t, params, rows, outs, history, dc, k)
    dc.prewithhold = {params["group_by"]: suppressed_vals} if params.get("group_by") and suppressed_vals else {}


def _list_control(plan: Plan, schema: FieldSchema, outs: dict, rows: list, dc: _Disclosure, k: int, ctx) -> None:
    """Attributes next to listed aliases: k-anonymous over the population the listing is drawn from."""
    show = list(plan.spec.show)
    if not show or plan.control is None:
        return
    ctrl = plan.control.schemas.get(schema.id)
    pre = dc.prewithhold
    for name, out in outs.items():
        if out.kind != "list" or not _leaves(plan, name) or name in dc.list_withheld or not out.value:
            continue
        pop = out.meta.get("population", rows)
        levels = {f: disclosure.levels_for(schema.spec(f), ctrl, coupled=f in pre) for f in show}
        listed = [r for r, _tok in out.value]
        text, st = disclosure.anonymize_listing(pop, listed, show, levels, k, plan.control.listing, ctx.person_key,
                                                pre)
        dc.show[name] = text
        dc.stats["rows_coarsened"] += st["rows_coarsened"]
        dc.stats["rows_attributes_withheld"] += st["rows_attributes_withheld"]


class _Renderer:
    def __init__(self, plan: Plan, schema: FieldSchema, dc: _Disclosure | None = None):
        self.plan, self.schema = plan, schema
        self.dc = dc or _Disclosure()
        self.raw: dict = {}          # sentinel -> token
        self._by_token: dict = {}
        self.declared: dict = {}     # (entity, value) -> None (ordered set)
        self.listed = 0
        self.not_listed = 0

    def _count(self, v) -> str:
        """A published count: exact, or rounded to the sealed base for roster count tables when rounding is on."""
        base = self.plan.control.rounding if self.plan.control is not None else 0
        if not base or self.plan.spec.compute != "roster_question" or not str(v).isdigit():
            return str(v)
        self.dc.stats["counts_rounded"] += 1
        return disclosure.rounded(int(v), base)

    def sent(self, token: str) -> str:
        s = self._by_token.get(token)
        if s is None:
            if len(self.raw) >= 0xFFFD:
                raise PayloadError("too many distinct output values")
            s = chr(SENTINEL_BASE + len(self.raw))
            self.raw[s] = token
            self._by_token[token] = s
        return s

    def ident(self, row) -> str:
        parts = []
        for n in self.plan.spec.label:
            v = row.values.get(n)
            if v is None:
                continue
            ent = self.schema.spec(n).entity
            for x in (v if isinstance(v, list) else [v]):
                self.declared[(ent, x)] = None
            parts.append(", ".join(v) if isinstance(v, list) else v)
        if not parts:
            return self.sent(f"record {row.index}")
        return parts[0] + (" (" + ", ".join(parts[1:]) + ")" if len(parts) > 1 else "")

    def render(self, name: str, out: hrcompute.Out, budget: list) -> str:
        k, v = out.kind, out.value
        mark = self.dc.markers.get(name)
        if mark is not None:
            return self.sent(mark)
        if k == "count":
            return self.sent(self._count(int(v)))
        if k == "label":
            return self.sent(str(v))
        if k == "codes":
            return self.sent("; ".join(f"{c}: {n}" for c, n in sorted(v.items())) if v else "none")
        if k in hrcompute.BANDED:
            edges, labels = self.plan.spec.bands[name]
            return self.sent(hrcompute.band_of(Decimal(v), edges, labels))
        if k == "groups":
            if not v:
                return self.sent("none")
            marks = self.dc.group_marks.get(name, {})
            return "\n".join(f"- {self.sent(str(key))}: {self.sent(marks.get(i) or self._count(tok))}"
                             for i, (key, tok) in enumerate(v))
        if k == "list":
            if name in self.dc.list_withheld:
                return self.sent(self.dc.list_withheld[name])
            lines = []
            show = self.dc.show.get(name, {})
            for row, tok in v:
                if self.listed >= self.plan.spec.max_listed or budget[0] <= 0:
                    self.not_listed += 1
                    continue
                full = "; ".join(x for x in (tok, show.get(id(row))) if x)
                line = f"- {self.ident(row)}" + (f": {self.sent(full)}" if full else "")
                budget[0] -= len(line) + 1
                lines.append(line)
                self.listed += 1
            if self.not_listed:
                lines.append(f"- {self.sent(f'... and {self.not_listed} more not listed')}")
            return "\n".join(lines) if lines else self.sent("none")
        raise PayloadError("unsupported output kind")


# =============================================================================================================
# run
# =============================================================================================================

@dataclass
class RecordResult:
    prompt: str
    declared: list
    raw: dict
    withheld: Withheld
    literals: tuple
    report: dict
    outputs: dict = field(default_factory=dict)   # (report compatibility: computed outputs are in `report`)
    fields: tuple = ()
    masked: tuple = ()
    instruction_at: tuple | None = None
    left: list = field(default_factory=list)

    def check_protected(self, text: str) -> None:
        """The protected prompt, sentinels still in place: no withheld value may remain anywhere in it."""
        t = _SENTINEL_RE.sub(" ", text)
        t = _EMITTED_ALIAS.sub(lambda m: " " * len(m.group(0)), t)
        for lit in sorted(self.literals, key=len, reverse=True):
            if len(lit) >= 4:
                t = t.replace(lit, " " * len(lit))
        hits = self.withheld.check(t)
        if hits:
            raise EgressViolation(sorted(set(hits)))

    def check_final(self, text: str) -> None:
        """Defence in depth on the exact final text: the regex recognizers and the identifier-shape backstop must
        find no identity or government/financial identifier outside an alias."""
        from ..detect import idshape
        from ..detect.recognizers import detect_d2
        from .contracts import CLASS_OF
        t = _EMITTED_ALIAS.sub(lambda m: " " * len(m.group(0)), text)
        bad = [d.type.value for d in detect_d2(t)
               if CLASS_OF.get(d.type) in (DataClass.C1, DataClass.C3, DataClass.REF)]
        bad += [d.type.value for d in idshape.detect(t)]
        if bad:
            raise EgressViolation(sorted(set(bad)))


def parse_envelope(messages: list, plan: Plan) -> dict:
    if (not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict)
            or messages[0].get("role") != "user" or not isinstance(messages[0].get("content"), str)):
        raise PayloadError("RECORDS expects exactly one user message whose content is the record payload")
    content = messages[0]["content"]
    if len(content) > plan.spec.max_bytes:
        raise PayloadError("payload too large for this data contract")
    env = recparse.parse_json_text(content)
    if not isinstance(env, dict) or set(env) - ENVELOPE_KEYS or not {"source", "format", "data"} <= set(env):
        raise PayloadError("record payload must be {source, format, data[, params, delimiter]}")
    if env["source"] not in plan.spec.sources:
        raise PayloadError("source is not a schema of this data contract")
    if env["format"] not in plan.schemas[env["source"]].formats:
        raise PayloadError("format not accepted by this source schema")
    delim = env.get("delimiter", ",")
    if delim not in recparse.CSV_DELIMITERS:
        raise PayloadError("unsupported CSV delimiter")
    return env


def run(plan: Plan, messages: list, history=None) -> RecordResult:
    """history: the differencing-history scope (disclosure.HistoryScope: store, tenant, app, purpose, key) bound by
    the gateway from the authenticated app; None = no history (unit tests of single responses only)."""
    env = parse_envelope(messages, plan)
    schema = plan.schemas[env["source"]]
    params = parse_params(plan, env.get("params"), schema)
    rows, stats, withheld, prefix = extract(plan, schema, env["format"], env["data"], env.get("delimiter", ","))
    ctx = _Ctx(plan)
    outs = plan.computation.fn(rows, params, plan.rules, ctx)
    contract = plan.contract
    dc = _Disclosure()
    dc.stats["groups_merged"] = ctx.stats.get("groups_merged", 0)
    dc.stats["groups_suppressed"] = ctx.stats.get("groups_suppressed", 0)
    if plan.control is not None:
        try:
            if plan.spec.compute == "roster_question":
                _roster_control(plan, outs, params, rows, dc, plan.k, history, schema)
            _list_control(plan, schema, outs, rows, dc, plan.k, ctx)
        except disclosure.DisclosureRefused as e:
            e.report = _report(plan, schema, env["format"], rows, stats, outs, [], _Renderer(plan, schema, dc),
                               prefix)
            raise
    r = _Renderer(plan, schema, dc)
    fields_ = {}
    budget = [plan.spec.max_prompt_chars - len(contract.prompt_template)]
    left = []
    for name, out in outs.items():
        spec = contract.outputs.get(name)
        if spec is None or spec.egress == "INTERNAL":
            continue
        fields_[name] = r.render(name, out, budget)
        left.append(name)
    prompt = contract.prompt_template.format(**fields_)
    if len(prompt) > plan.spec.max_prompt_chars + 2000:
        raise PayloadError("computed prompt exceeds the data contract's output bound")
    withheld.finalize()
    report = _report(plan, schema, env["format"], rows, stats, outs, left, r, prefix)
    return RecordResult(prompt=prompt, declared=[(t, v) for t, v in r.declared], raw=r.raw, withheld=withheld,
                        literals=plan.literals, report=report, left=left)


def _report(plan, schema, fmt, rows, stats, outs, left, r, prefix) -> dict:
    """The pgw_report `records` section: paths, dispositions and counts; never a value."""
    pre = (prefix + ".") if prefix else ""
    fields_, by_disp = [], {}
    for f in schema.fields:
        n = stats.by_path.get(f.path, 0)
        d = by_disp.setdefault(f.disposition, {"fields": 0, "values": 0})
        if n:
            d["fields"] += 1
            d["values"] += n
        fields_.append({"path": pre + f.path, "name": f.name, "disposition": f.disposition,
                        "selected": f.name in plan.view or (f.presence_flag or "") in plan.view, "values": n})
    unknown = sorted(stats.unknown_paths.items())
    contract = plan.contract
    return {"schema": f"{schema.id}@{schema.version}", "starter_schema": schema.starter, "format": fmt,
            "rows": len(rows), "fields": fields_, "by_disposition": by_disp,
            "unknown": {"fields": len(unknown), "values_dropped": sum(n for _p, n in unknown),
                        "paths": [p for p, _n in unknown[:MAX_UNKNOWN_REPORTED]],
                        "paths_truncated": len(unknown) > MAX_UNKNOWN_REPORTED},
            "invalid_values": stats.invalid, "keep_rejected": stats.keep_rejected,
            "computation": {"name": plan.spec.compute, "rules": plan.rules_ref, "starter_rules": plan.starter_rules},
            "outputs": [{"name": n, "kind": plan.kinds[n],
                         "egress": contract.outputs[n].egress if n in contract.outputs else "INTERNAL"}
                        for n in outs],
            "left": {"outputs": left, "aliased_identities": len(r.declared), "listed_rows": r.listed,
                     "not_listed": r.not_listed},
            "disclosure": _disclosure_report(plan, schema, r.dc)}


def _disclosure_report(plan, schema, dc) -> dict:
    """Counts only: how many cells, groups, rows and queries disclosure control changed; never a value."""
    if plan.control is None:
        return {"enforced": False}
    ctrl = plan.control.schemas.get(schema.id)
    return {"enforced": True, "config": plan.control.cfg.ref, "min_group_size": plan.k,
            "listing": plan.control.listing, "differencing": plan.control.differencing,
            "rounding_base": plan.control.rounding or None,
            "quasi_identifiers": list(ctrl.qis) if ctrl is not None else [], **dc.stats}

