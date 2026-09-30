"""The egress invariant (ADR-0007): sweep the exact outbound bytes for every value the
pipeline classified as sensitive-and-not-ALLOW. Any hit aborts the call.

This is a bug detector, not a recall control: it only knows what was detected (T20).
"""
from __future__ import annotations

import json

import ahocorasick

from .alias import canon
from .types import EgressViolation, EntityType

MIN_NEEDLE = 4  # shorter needles would fire on ordinary text; such values are rare and covered by D5 tests
_NUMERIC = (EntityType.AADHAAR, EntityType.CARD, EntityType.BANK_ACCOUNT, EntityType.PHONE)


def _needles(t: EntityType, value: str) -> set[str]:
    forms = {value, json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1]}
    if t in _NUMERIC:
        forms.add(canon(t, value))
    return {f.casefold() for f in forms if len(f) >= MIN_NEEDLE}


def check(body: bytes, sensitive: list[tuple[EntityType, str]]) -> None:
    if not sensitive:
        return
    auto = ahocorasick.Automaton()
    for t, v in sensitive:
        for n in _needles(t, v):
            auto.add_word(n, t.value)
    if len(auto) == 0:
        return
    auto.make_automaton()
    hay = body.decode("utf-8", errors="replace").casefold()
    hits = [t for _, t in auto.iter(hay)]
    if hits:
        raise EgressViolation(hits)
