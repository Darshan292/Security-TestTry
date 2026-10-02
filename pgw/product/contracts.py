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
            DataClass.C4: {"REFUSE"}}                    # slice: no private route yet, so refuse
ROUTES = {"EXT_PSEUDO", "LOCAL_COMPUTE"}
EGRESS = {"RAW", "TRANSFORMED", "INTERNAL"}

# Purpose-dependent personal attributes (PRODUCT_ARCHITECTURE §2): sensitivity depends on the purpose and the
# current detectors cannot recognise them, so every contract must state its stance on each one explicitly.
PURPOSE_DEPENDENT = ("leave_balance", "attendance", "performance_rating", "health_or_leave_reason",
                     "disciplinary_outcome", "eligibility_result", "derived_compensation")
STANCES = {"PSEUDONYMISED_RAW",   # may cross raw only next to aliased identities (still personal data)
           "NOT_EXPECTED",        # the app must not send it for this purpose (undetectable: an app obligation)
           "LOCAL_ONLY"}          # only through LOCAL_COMPUTE inputs/outputs under their egress rules


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class Output:
    name: str
    derived_from: DataClass
    egress: str
    entity: T = T.DERIVED
    justification: str = ""


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


def _parse_schema(schema: dict, path: str = "input_schema") -> None:
    for name, spec in schema.items():
        if not isinstance(spec, dict) or spec.get("type") not in ("str", "int", "list"):
            raise ContractError(f"{path}.{name}: type must be str, int or list")
        if spec["type"] == "list":
            _parse_schema(spec.get("items") or {}, f"{path}.{name}.items")
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
        if "entity" in spec:
            T(spec["entity"])


def load_contract(path: str | Path) -> Contract:
    raw = yaml.safe_load(Path(path).read_text())
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
        _parse_schema(schema)
        c = Contract(name=raw["contract"], version=int(raw["version"]), task_type=raw["task_type"], route=route,
                     classes=classes, restore=restore, purpose_dependent=pdep, compute=raw.get("compute"),
                     input_schema=schema, outputs=outputs, prompt_template=raw.get("prompt_template"),
                     max_chars=int(raw.get("max_chars", 20000)))
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError) as e:
        raise ContractError(f"malformed contract: {type(e).__name__} {e}") from None
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
