"""The egress invariant (ADR-0007): sweep the exact outbound bytes for every value the
pipeline classified as sensitive-and-not-ALLOW. Any hit aborts the call.

This is a bug detector, not a recall control: it only knows what was detected (T20).
"""
from __future__ import annotations

import json

import re

import ahocorasick

from .alias import _CK, _TYPE_NAMES, canon
from .types import EgressViolation, EntityType

# Our own alias tokens exactly as the pipeline emits them. They are blanked (same length, so
# offsets are unchanged) before the sweep: a detected value such as "Date" must not match the
# type name inside "[DATE_OF_BIRTH_3QX2MA]" (R1 TEST: 8 false alarms, W9). Only this strict,
# bracketed form is blanked; lenient or partial alias-like text is still swept.
_EMITTED_ALIAS = re.compile(rf"\[(?:{_TYPE_NAMES})_[0-9][{_CK}]{{5}}(?:[{_CK}]{{2}})?\]")
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
    hay = _EMITTED_ALIAS.sub(lambda m: " " * len(m.group(0)), body.decode("utf-8", errors="replace")).casefold()
    hits = [t for _, t in auto.iter(hay)]
    if hits:
        raise EgressViolation(hits)
