"""Decision tables (ADR-0006): (purpose, entity type) -> action. In-process, no network."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .detect.numwords import amount_value
from .types import Action, EntityType


class UnknownPurpose(Exception):
    pass


@dataclass(frozen=True)
class PolicyPack:
    name: str
    version: int
    defaults: dict[EntityType, Action]
    purposes: dict[str, dict[EntityType, Action]]
    never_restore: frozenset[EntityType]
    data_class: dict[str, str] = None
    stream_modes: dict[str, str] = None

    @classmethod
    def load(cls, path: str | Path, data: bytes | None = None) -> "PolicyPack":
        raw = yaml.safe_load(Path(path).read_text() if data is None else data.decode("utf-8"))
        defaults = {EntityType(k): Action(v) for k, v in raw["defaults"].items()}
        missing = set(EntityType) - set(defaults)
        if missing:  # every type needs an explicit default; no silent ALLOW
            raise ValueError(f"policy pack missing defaults for {sorted(m.value for m in missing)}")
        purposes = {p: {EntityType(k): Action(v) for k, v in (o or {}).items()}
                    for p, o in raw["purposes"].items()}
        modes = raw.get("stream_mode") or {}
        if any(v not in ("buffered", "incremental") for v in modes.values()):
            raise ValueError("stream_mode values must be 'buffered' or 'incremental'")
        return cls(raw["pack"], int(raw["version"]), defaults, purposes,
                   frozenset(EntityType(t) for t in raw.get("never_restore", [])),
                   dict(raw.get("data_class") or {}), dict(modes))

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def data_class_of(self, purpose: str) -> str:
        return (self.data_class or {}).get(purpose, "RESTRICTED")

    def stream_mode(self, purpose: str) -> str:
        """Output mode for a purpose; anything not explicitly incremental is buffered."""
        mode = (self.stream_modes or {}).get(self.data_class_of(purpose), "buffered")
        return "incremental" if mode == "incremental" else "buffered"

    def decide(self, purpose: str, t: EntityType) -> Action:
        if purpose not in self.purposes:
            raise UnknownPurpose(purpose)
        return self.purposes[purpose].get(t, self.defaults[t])


# --- GENERALIZE transforms (not restorable) ----------------------------------

_YEAR = re.compile(r"(19|20)\d{2}")
_UNIT = re.compile(r"(lakhs?|lacs?|lpa|l|crores?|cr|k)\b", re.IGNORECASE)
# Bands are INR bands; an amount in another currency gets no band rather than a wrong one (audit: "USD 142,000"
# was banded as ₹0–5L).
_FOREIGN = re.compile(r"\b(?:usd|eur|gbp|aed|sgd|cad|aud|jpy|chf)\b|[$€£¥]", re.IGNORECASE)


def _inr_amount(s: str) -> float | None:
    t = s.replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", t)
    if not m or re.search(r"(?i)\d[ \t]*(?:lakhs?|lacs?|crores?|crs?)\b.*\S", t):
        return amount_value(s)          # in words, or several units ("twenty two lakh", "12 lakh 50 thousand")
    n = float(m.group())
    u = _UNIT.search(t[m.end():])
    if u:
        unit = u.group(1).lower()
        n *= {"k": 1e3, "cr": 1e7}.get(unit, 1e7 if unit.startswith("crore") else 1e5)
    return n


def generalize(t: EntityType, value: str) -> str:
    if t is EntityType.DATE_OF_BIRTH:
        y = _YEAR.search(value)
        return f"[born {y.group()[:3]}0s]" if y else "[DATE_OF_BIRTH]"
    if t is EntityType.COMPENSATION:
        n = _inr_amount(value)
        if n is None or n <= 0 or _FOREIGN.search(value):
            return "[COMPENSATION]"
        lakh = n / 1e5
        if lakh < 1:
            return "[COMPENSATION <₹1L]"
        lo = int(lakh // 5 * 5)
        return f"[COMPENSATION ₹{lo}L–₹{lo + 5}L]"
    return f"[{t.value}]"
