"""Shared contracts. Every module and every agent codes against these.

Offsets are Python str indices (code points), `end` exclusive, always relative
to the exact string handed to the detector.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EntityType(str, Enum):
    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    AADHAAR = "AADHAAR"
    PAN = "PAN"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    IFSC = "IFSC"
    CARD = "CARD"
    UPI_ID = "UPI_ID"
    SECRET = "SECRET"
    EMPLOYEE_ID = "EMPLOYEE_ID"
    COMPENSATION = "COMPENSATION"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    DERIVED = "DERIVED"     # a value computed inside the gateway (LOCAL_COMPUTE); never produced by detectors


class Action(str, Enum):
    TOKENIZE = "TOKENIZE"
    REDACT = "REDACT"
    GENERALIZE = "GENERALIZE"
    ALLOW = "ALLOW"
    BLOCK_REQUEST = "BLOCK_REQUEST"


@dataclass(frozen=True)
class Detection:
    start: int
    end: int
    type: EntityType
    score: float          # 0..1
    source: str           # "D1:field", "D2:regex", "D3:edm", "D3b:restore_ledger", "D5:propagation"


@dataclass(frozen=True)
class Context:
    """Who is asking and why. Resolved from the app registry, never from the prompt (ADR-0004)."""
    tenant: str
    app: str
    purpose: str
    conversation_id: str | None = None
    restore_output: bool = True


@dataclass
class Entity:
    """A resolved sensitive value inside one request."""
    type: EntityType
    value: str                      # exact surface form in the original text
    action: Action
    alias: str | None = None        # set when action == TOKENIZE
    replacement: str | None = None  # text that goes to the provider
    sources: set[str] = field(default_factory=set)


class BlockedRequest(Exception):
    """Policy says this request must not leave (422). Carries types only, never values."""

    def __init__(self, types: list[EntityType]):
        self.types = sorted({t.value for t in types})
        super().__init__(f"blocked: {', '.join(self.types)}")


class EgressViolation(Exception):
    """A value the pipeline classified as sensitive-and-not-ALLOW is in the outbound bytes (ADR-0007)."""

    def __init__(self, types: list[str]):
        self.types = sorted(set(types))
        super().__init__(f"egress invariant violated: {', '.join(self.types)}")
