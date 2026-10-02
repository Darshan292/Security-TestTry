"""Data contracts and data classes (PRODUCT_ARCHITECTURE.md §2).

A contract is declared per purpose by the integrating app's owners and loaded at startup. It fixes the route
(EXT_PSEUDO / LOCAL_COMPUTE) and what happens to each data class. Contracts can only choose actions under which
the raw value does not leave (ALIAS, GENERALIZE, REDACT, REFUSE); an unsafe or malformed contract stops the
gateway from starting (fail closed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from ..types import Action, EntityType as T


class DataClass(str, Enum):
    C0 = "C0"   # non-personal business content: crosses raw
    C1 = "C1"   # identities: scoped aliases, restorable
    C2 = "C2"   # personal quantities/attributes: never exact
    C3 = "C3"   # government/financial identifiers and credentials: never
    C4 = "C4"   # content the gateway cannot reliably read (non-Latin script personal text, ...)


CLASS_OF: dict[T, DataClass] = {
    T.PERSON: DataClass.C1, T.EMAIL: DataClass.C1, T.PHONE: DataClass.C1, T.EMPLOYEE_ID: DataClass.C1,
    T.COMPENSATION: DataClass.C2, T.DATE_OF_BIRTH: DataClass.C2,
    T.AADHAAR: DataClass.C3, T.PAN: DataClass.C3, T.BANK_ACCOUNT: DataClass.C3, T.CARD: DataClass.C3,
    T.UPI_ID: DataClass.C3, T.SECRET: DataClass.C3,
    T.IFSC: DataClass.C0,   # a bank branch, not a person (DESIGN §5.2)
}

# Contract action -> pipeline action. ALLOW is deliberately absent for C1-C3: raw personal data never crosses.
_ACTION = {"ALIAS": Action.TOKENIZE, "GENERALIZE": Action.GENERALIZE, "REDACT": Action.REDACT,
           "REFUSE": Action.BLOCK_REQUEST}
_ALLOWED = {DataClass.C1: {"ALIAS", "REDACT", "REFUSE"},
            DataClass.C2: {"ALIAS", "GENERALIZE", "REDACT", "REFUSE"},
            DataClass.C3: {"REDACT", "REFUSE"},          # never aliased to an external model either
            DataClass.C4: {"REFUSE"}}                    # slice: no private route yet, so refuse
ROUTES = {"EXT_PSEUDO", "LOCAL_COMPUTE"}


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class Contract:
    name: str
    version: int
    task_type: str
    route: str
    classes: dict[DataClass, str]
    restore: frozenset[DataClass]
    compute: str | None = None
    input_schema: dict = field(default_factory=dict)
    prompt_template: str | None = None
    max_chars: int = 20000

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def actions(self) -> dict[T, Action]:
        """Per-entity-type actions for the detection pipeline."""
        return {t: _ACTION[self.classes[c]] for t, c in CLASS_OF.items() if c in self.classes}

    def restorable(self, t: T) -> bool:
        return CLASS_OF.get(t, DataClass.C0) in self.restore


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
        c = Contract(name=raw["contract"], version=int(raw["version"]), task_type=raw["task_type"], route=route,
                     classes=classes, restore=restore, compute=raw.get("compute"),
                     input_schema=raw.get("input_schema") or {}, prompt_template=raw.get("prompt_template"),
                     max_chars=int(raw.get("max_chars", 20000)))
    except (KeyError, TypeError) as e:
        raise ContractError(f"malformed contract: missing {e}") from None
    if c.route == "LOCAL_COMPUTE" and (not c.compute or not c.input_schema or not c.prompt_template):
        raise ContractError("LOCAL_COMPUTE contracts need compute, input_schema and prompt_template")
    return c
