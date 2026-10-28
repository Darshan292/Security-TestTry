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


# Amounts are compared as whole numbers: "Rs 20" inside the different amount "Rs 200" or "Rs 20,000" is not the value
# "Rs 20". Without this, aliasing one amount while a longer, different one stayed in the text (correctly, or not)
# failed the whole request with a 500. Identifiers keep plain substring matching: an id inside a longer digit run
# still leaks it.
_AMOUNT_TYPES = frozenset({EntityType.COMPENSATION})


def _digit_extends(hay: str, i: int, step: int) -> bool:
    """True if the number ending (step=+1) or starting (step=-1) next to hay[i] continues there: a digit, or a
    thousands/decimal separator followed by a digit."""
    c = hay[i] if 0 <= i < len(hay) else ""
    if c.isdigit():
        return True
    j = i + step
    return c in ",." and 0 <= j < len(hay) and hay[j].isdigit()


def _own_forms(own_text: tuple[str, ...]) -> list[str]:
    forms: set[str] = set()
    for o in own_text:
        if o:
            forms |= {o, json.dumps(o)[1:-1], json.dumps(o, ensure_ascii=False)[1:-1]}
    return sorted(forms, key=len, reverse=True)


def blank_own(body: bytes, own_text: tuple[str, ...]) -> bytes:
    """`body` with the strings the gateway itself put in the payload (its placeholder note, the contract's guidance,
    the model name) blanked, same length, as subjects.check_outbound does: a detected value that is an ordinary word
    ("EMPLOYEE" in a header row) must not fail the request because the gateway's own instructions use that word. User
    text is never blanked, so a real leak is still caught."""
    text = body.decode("utf-8", errors="replace")
    for o in _own_forms(own_text):
        text = text.replace(o, " " * len(o))
    return text.encode("utf-8")


def check(body: bytes, sensitive: list[tuple[EntityType, str]], own_text: tuple[str, ...] = ()) -> None:
    """`own_text`: see blank_own."""
    if own_text:
        body = blank_own(body, own_text)
    if not sensitive:
        return
    auto = ahocorasick.Automaton()
    bounded: dict[str, bool] = {}
    for t, v in sensitive:
        for n in _needles(t, v):
            whole = t in _AMOUNT_TYPES and (n[0].isdigit() or n[-1].isdigit())
            bounded[n] = bounded.get(n, True) and whole        # bounded only if every type behind it is an amount
            auto.add_word(n, (t.value, n))
    if len(auto) == 0:
        return
    auto.make_automaton()
    text = body.decode("utf-8", errors="replace")
    hay = _EMITTED_ALIAS.sub(lambda m: " " * len(m.group(0)), text).casefold()
    hits = []
    for end, (t, n) in auto.iter(hay):
        if bounded[n]:
            start = end - len(n) + 1
            if (n[-1].isdigit() and _digit_extends(hay, end + 1, 1)) or \
                    (n[0].isdigit() and _digit_extends(hay, start - 1, -1)):
                continue
        hits.append(t)
    if hits:
        raise EgressViolation(hits)
