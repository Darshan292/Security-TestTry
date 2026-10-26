"""Data contracts and data classes (PRODUCT_ARCHITECTURE.md §2).

A contract is declared per purpose by the integrating app's owners and loaded at startup. It fixes the route
(EXT_PSEUDO / LOCAL_COMPUTE), what happens to each data class, and, for LOCAL_COMPUTE, what may happen to every
input field and every computed output. Contracts can only choose treatments under which a protected raw value
does not leave; an unsafe or malformed contract stops the gateway from starting (fail closed).

Derived values are NOT assumed safe. Every LOCAL_COMPUTE output must declare its egress:
  RAW          leaves as is            only for outputs derived from C0, or with a written justification
  TRANSFORMED  leaves only as an alias (restorable if the contract restores its class)
  INTERNAL     never leaves (the default for any output the contract does not list); checked at egress
"""
from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from ..types import Action, EntityType as T


class DataClass(str, Enum):
    C0 = "C0"   # non-personal business content: crosses raw
    C1 = "C1"   # identities: scoped aliases, restorable
    C2 = "C2"   # personal quantities/attributes: never exact (PURPOSE_DEPENDENT lists the contextual kind)
    C3 = "C3"   # government/financial identifiers and credentials: never
    C4 = "C4"   # content the gateway cannot reliably read (non-Latin script personal text, ...)
    D = "D"     # values computed inside the gateway (LOCAL_COMPUTE outputs): per-output egress rule


CLASS_OF: dict[T, DataClass] = {
    T.PERSON: DataClass.C1, T.EMAIL: DataClass.C1, T.PHONE: DataClass.C1, T.EMPLOYEE_ID: DataClass.C1,
    T.COMPENSATION: DataClass.C2, T.DATE_OF_BIRTH: DataClass.C2,
    T.AADHAAR: DataClass.C3, T.PAN: DataClass.C3, T.BANK_ACCOUNT: DataClass.C3, T.CARD: DataClass.C3,
    T.UPI_ID: DataClass.C3, T.SECRET: DataClass.C3,
    T.IFSC: DataClass.C0,   # a bank branch, not a person (DESIGN §5.2)
    T.DERIVED: DataClass.D,
}

# Contract action -> pipeline action. ALLOW is deliberately absent for C1-C3: raw personal data never crosses.
_ACTION = {"ALIAS": Action.TOKENIZE, "GENERALIZE": Action.GENERALIZE, "REDACT": Action.REDACT,
           "REFUSE": Action.BLOCK_REQUEST}
_ALLOWED = {DataClass.C1: {"ALIAS", "REDACT", "REFUSE"},
            DataClass.C2: {"ALIAS", "GENERALIZE", "REDACT", "REFUSE"},
            DataClass.C3: {"REDACT", "REFUSE"},          # never aliased to an external model either
            DataClass.C4: {"REFUSE", "PRIVATE"}}         # PRIVATE: only to the in-boundary model endpoint
ROUTES = {"EXT_PSEUDO", "LOCAL_COMPUTE"}
EGRESS = {"RAW", "TRANSFORMED", "INTERNAL"}

# Purpose-dependent personal attributes (PRODUCT_ARCHITECTURE §2): sensitivity depends on the purpose and the
# current detectors cannot recognise them, so every contract must state its stance on each one explicitly.
PURPOSE_DEPENDENT = ("leave_balance", "attendance", "performance_rating", "health_or_leave_reason",
                     "disciplinary_outcome", "eligibility_result", "derived_compensation")
# The stances are DECLARATIONS, validated for completeness but not detected or removed at runtime: the gateway has
# no detector for these attributes in free text. What the gateway enforces is the input boundary below
# (ExternalInput): free text crosses only if the app attests it came from an allowlisted source-side sanitizer.
STANCES = {"PSEUDONYMISED_RAW",   # may cross raw only next to aliased identities (still personal data)
           "NOT_EXPECTED",        # the source must not send it for this purpose (not detected by the gateway)
           "LOCAL_ONLY"}          # only through LOCAL_COMPUTE inputs/outputs under their egress rules
_SANITIZER_ID = re.compile(r"[a-z0-9][a-z0-9._@-]{0,63}")


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class ExternalInput:
    """Source-side boundary for free text that reaches an external model (SECURITY_BOUNDARY.md §4).
    sanitizers: ids of the source-side exports/sanitizers whose output this purpose accepts (X-PGW-Input-Source).
    unattested: REFUSE (422, nothing sent) or PRIVATE (in-boundary model only; EXT_PSEUDO contracts)."""
    sanitizers: frozenset
    unattested: str = "REFUSE"


@dataclass(frozen=True)
class Output:
    name: str
    derived_from: DataClass
    egress: str
    entity: T = T.DERIVED
    justification: str = ""


@dataclass(frozen=True)
class SubjectSpec:
    """PATH A declared subjects: people the app already knows are involved (requester, assignee, ...).
    roles: role -> max count; fields: field name -> C1 entity type."""
    roles: dict[str, int]
    required: tuple[str, ...]
    fields: dict[str, T]


@dataclass(frozen=True)
class Contract:
    name: str
    version: int
    task_type: str
    route: str
    classes: dict[DataClass, str]
    restore: frozenset[DataClass]
    purpose_dependent: dict[str, str]
    compute: str | None = None
    input_schema: dict = field(default_factory=dict)
    outputs: dict[str, Output] = field(default_factory=dict)
    prompt_template: str | None = None
    max_chars: int = 20000
    subjects: SubjectSpec | None = None
    guidance: str | None = None                   # task guidance for the model (system message, sealed)
    org_contacts: tuple[str, ...] = ()            # organisational C0 contacts (role mailbox, helpdesk number)
    redaction_text: dict = field(default_factory=dict)   # C3 type -> words shown instead of a "[TYPE]" marker
    reference: dict = field(default_factory=dict)         # sealed public tables, e.g. pay_bands (C0)
    external_input: ExternalInput = ExternalInput(frozenset())   # required in every contract file

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def actions(self) -> dict[T, Action]:
        """Per-entity-type actions for the detection pipeline. DERIVED values are only ever aliased."""
        acts = {t: _ACTION[self.classes[c]] for t, c in CLASS_OF.items() if c in self.classes}
        acts[T.DERIVED] = Action.TOKENIZE
        return acts

    def restorable(self, t: T) -> bool:
        return CLASS_OF.get(t, DataClass.C0) in self.restore


def template_fields(template: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(template) if f}


_REF_KEY = re.compile(r"[A-Za-z0-9-]{1,8}")


def _parse_reference(raw) -> dict:
    """Sealed reference tables (company policy, C0), e.g. pay_bands: {P3: [min, max]}. Public constants live here,
    in the signed contract, so callers never supply a number or label that crosses raw (audit finding S2)."""
    out = {}
    for table, rows in (raw or {}).items():
        if not isinstance(rows, dict) or not 1 <= len(rows) <= 100:
            raise ContractError(f"reference.{table}: 1-100 entries")
        parsed = {}
        for k, v in rows.items():
            if not _REF_KEY.fullmatch(str(k)) or not (isinstance(v, list) and len(v) == 2 and all(
                    isinstance(x, int) and not isinstance(x, bool) and x >= 1 for x in v)):
                raise ContractError(f"reference.{table}.{k}: short key and [min, max] integers")
            lo, hi = v
            if hi * 5 < lo * 6:    # a narrow range would disclose the value it brackets
                raise ContractError(f"reference.{table}.{k}: range must be at least 20% wide")
            parsed[str(k)] = (lo, hi)
        out[str(table)] = parsed
    return out


def _parse_schema(schema: dict, path: str = "input_schema", reference: dict | None = None) -> None:
    for name, spec in schema.items():
        if not isinstance(spec, dict) or spec.get("type") not in ("str", "int", "list"):
            raise ContractError(f"{path}.{name}: type must be str, int or list")
        if spec["type"] == "list":
            _parse_schema(spec.get("items") or {}, f"{path}.{name}.items", reference)
            continue
        cls = spec.get("class")
        if cls is None:
            if name != "instruction":
                raise ContractError(f"{path}.{name}: every data field must declare its class")
            continue
        c = DataClass(cls)
        if c not in (DataClass.C0, DataClass.C1, DataClass.C2, DataClass.C3):
            raise ContractError(f"{path}.{name}: invalid input class {cls}")
        if c is not DataClass.C0 and "entity" not in spec:
            raise ContractError(f"{path}.{name}: C1-C3 fields must name their entity type")
        if c is DataClass.C0:
            # A caller-supplied "public" value is never inspected, so it must not be able to carry anything:
            # C0 inputs are only keys of a sealed reference table.
            if spec["type"] != "str" or spec.get("one_of") not in (reference or {}):
                raise ContractError(f"{path}.{name}: C0 inputs must be `one_of` a sealed reference table "
                                    "(caller-supplied public numbers or free text would cross unchecked)")
        if "entity" in spec:
            T(spec["entity"])


def _parse_subjects(raw) -> SubjectSpec | None:
    if raw is None:
        return None
    roles = {str(k): int(v) for k, v in (raw.get("roles") or {}).items()}
    if not roles or any(not 1 <= n <= 50 for n in roles.values()):
        raise ContractError("declared_subjects: roles must map each role to a maximum count between 1 and 50")
    required = tuple(str(r) for r in raw.get("required") or ())
    if set(required) - set(roles):
        raise ContractError("declared_subjects: required roles must be declared roles")
    fields = {str(k): T(v) for k, v in (raw.get("fields") or {}).items()}
    if not fields or any(CLASS_OF.get(t) is not DataClass.C1 for t in fields.values()):
        raise ContractError("declared_subjects: every field must be a C1 identity type")
    return SubjectSpec(roles, required, fields)


def _parse_guidance(raw) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 2000:
        raise ContractError("guidance must be non-empty text of at most 2000 characters")
    return raw.strip()


def _parse_org_contacts(raw) -> list[str]:
    """Organisational contacts the contract owner declares non-personal (C0). Sealed and code-owned: a personal
    address listed here would cross raw, so each entry must be a role mailbox or a switchboard/helpdesk number."""
    out = [str(x).strip() for x in (raw or [])]
    if len(out) > 10 or any(not x or len(x) > 120 or any(ord(c) < 32 for c in x) for x in out):
        raise ContractError("org_contacts: at most 10 single-line entries")
    return out


def _parse_redaction_text(raw) -> dict:
    out = {str(k).upper(): str(v) for k, v in (raw or {}).items()}
    bad = [k for k in out if k != "DEFAULT" and CLASS_OF.get(T(k) if k in T.__members__ else None) is not DataClass.C3]
    if bad or any(not v.strip() or len(v) > 80 or "[" in v for v in out.values()):
        raise ContractError("redaction_text: keys must be C3 types (or DEFAULT); values short plain text")
    return out


def _parse_external_input(raw) -> ExternalInput:
    if not isinstance(raw, dict):
        raise ContractError("contract must declare external_input (source-side sanitizers for free text that reaches "
                            "an external model; NOT_EXPECTED attributes are not detected by the gateway)")
    ids = raw.get("sanitizers")
    if not isinstance(ids, list) or not ids or len(ids) > 10 or not all(
            isinstance(x, str) and _SANITIZER_ID.fullmatch(x) for x in ids):
        raise ContractError("external_input.sanitizers: 1-10 ids ([a-z0-9._@-], at most 64 chars)")
    un = str(raw.get("unattested", "REFUSE")).upper()
    if un not in ("REFUSE", "PRIVATE"):
        raise ContractError("external_input.unattested must be REFUSE or PRIVATE")
    if set(raw) - {"sanitizers", "unattested"}:
        raise ContractError("external_input: unknown keys")
    return ExternalInput(frozenset(ids), un)


def load_contract(path: str | Path, data: bytes | None = None) -> Contract:
    raw = yaml.safe_load(Path(path).read_text() if data is None else data.decode("utf-8"))
    try:
        route = raw["route"]
        if route not in ROUTES:
            raise ContractError(f"unknown route {route!r}")
        classes = {DataClass(k): str(v).upper() for k, v in raw["data_classes"].items()}
        for c in (DataClass.C1, DataClass.C2, DataClass.C3, DataClass.C4):
            if c not in classes:
                raise ContractError(f"contract must state an action for {c.value}")
            if classes[c] not in _ALLOWED[c]:
                raise ContractError(f"{c.value}: action {classes[c]} is not allowed (raw value would cross)")
        restore = frozenset(DataClass(c) for c in raw.get("restore", []))
        if restore & {DataClass.C3, DataClass.C4}:
            raise ContractError("C3/C4 values are never restored from model output")
        pdep = {str(k): str(v).upper() for k, v in (raw.get("purpose_dependent") or {}).items()}
        missing = [k for k in PURPOSE_DEPENDENT if k not in pdep]
        if missing:
            raise ContractError(f"contract must state its stance on purpose-dependent attributes: {missing}")
        bad = sorted(k for k, v in pdep.items() if v not in STANCES or k not in PURPOSE_DEPENDENT)
        if bad:
            raise ContractError(f"invalid purpose-dependent stances: {bad}")
        outputs = {}
        for name, o in (raw.get("outputs") or {}).items():
            out = Output(name, DataClass(o["derived_from"]), str(o["egress"]).upper(),
                         T(o.get("entity", "DERIVED")), str(o.get("justification", "")).strip())
            if out.egress not in EGRESS:
                raise ContractError(f"output {name}: egress must be RAW, TRANSFORMED or INTERNAL")
            if out.egress == "RAW" and out.derived_from is not DataClass.C0 and len(out.justification) < 20:
                raise ContractError(f"output {name}: RAW egress of a value derived from {out.derived_from.value} "
                                    "needs a written justification")
            if out.derived_from in (DataClass.C3, DataClass.C4) and out.egress != "INTERNAL":
                raise ContractError(f"output {name}: values derived from C3/C4 never leave")
            outputs[name] = out
        schema = raw.get("input_schema") or {}
        reference = _parse_reference(raw.get("reference"))
        _parse_schema(schema, reference=reference)
        c = Contract(name=raw["contract"], version=int(raw["version"]), task_type=raw["task_type"], route=route,
                     classes=classes, restore=restore, purpose_dependent=pdep, compute=raw.get("compute"),
                     input_schema=schema, outputs=outputs, prompt_template=raw.get("prompt_template"),
                     max_chars=int(raw.get("max_chars", 20000)),
                     subjects=_parse_subjects(raw.get("declared_subjects")),
                     guidance=_parse_guidance(raw.get("guidance")),
                     org_contacts=tuple(_parse_org_contacts(raw.get("org_contacts"))),
                     redaction_text=_parse_redaction_text(raw.get("redaction_text")),
                     external_input=_parse_external_input(raw.get("external_input")), reference=reference)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError) as e:
        raise ContractError(f"malformed contract: {type(e).__name__} {e}") from None
    if c.external_input.unattested == "PRIVATE" and c.route != "EXT_PSEUDO":
        raise ContractError("external_input.unattested: PRIVATE applies to EXT_PSEUDO contracts only")
    if c.classes[DataClass.C4] == "PRIVATE" and c.route != "EXT_PSEUDO":
        raise ContractError("C4: PRIVATE applies to EXT_PSEUDO contracts only")
    if c.subjects is not None and (c.route != "EXT_PSEUDO" or c.classes[DataClass.C1] == "REFUSE"):
        raise ContractError("declared_subjects need an EXT_PSEUDO route whose C1 action replaces identities")
    if c.route == "LOCAL_COMPUTE":
        if not c.compute or not c.input_schema or not c.prompt_template or not c.outputs:
            raise ContractError("LOCAL_COMPUTE contracts need compute, input_schema, outputs and prompt_template")
        used = template_fields(c.prompt_template) - {"instruction"}
        unknown = used - set(c.outputs)
        if unknown:
            raise ContractError(f"prompt_template uses fields that are not declared outputs: {sorted(unknown)}")
        internal = sorted(n for n in used if c.outputs[n].egress == "INTERNAL")
        if internal:
            raise ContractError(f"prompt_template uses INTERNAL outputs: {internal}")
    return c
