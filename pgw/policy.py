"""Decision tables (ADR-0006): (purpose, entity type) -> action. In-process, no network."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

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

    @classmethod
    def load(cls, path: str | Path) -> "PolicyPack":
        raw = yaml.safe_load(Path(path).read_text())
        defaults = {EntityType(k): Action(v) for k, v in raw["defaults"].items()}
        missing = set(EntityType) - set(defaults)
        if missing:  # every type needs an explicit default; no silent ALLOW
            raise ValueError(f"policy pack missing defaults for {sorted(m.value for m in missing)}")
        purposes = {p: {EntityType(k): Action(v) for k, v in (o or {}).items()}
                    for p, o in raw["purposes"].items()}
        return cls(raw["pack"], int(raw["version"]), defaults, purposes,
                   frozenset(EntityType(t) for t in raw.get("never_restore", [])))

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def decide(self, purpose: str, t: EntityType) -> Action:
        if purpose not in self.purposes:
            raise UnknownPurpose(purpose)
        return self.purposes[purpose].get(t, self.defaults[t])


# --- GENERALIZE transforms (not restorable) ----------------------------------

_YEAR = re.compile(r"(19|20)\d{2}")
_UNIT = re.compile(r"(lakhs?|lacs?|lpa|l|crores?|cr|k)\b", re.IGNORECASE)


def _inr_amount(s: str) -> float | None:
    t = s.replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", t)
    if not m:
        return None
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
        if n is None or n <= 0:
            return "[COMPENSATION]"
        lakh = n / 1e5
        if lakh < 1:
            return "[COMPENSATION <₹1L]"
        lo = int(lakh // 5 * 5)
        return f"[COMPENSATION ₹{lo}L–₹{lo + 5}L]"
    return f"[{t.value}]"
