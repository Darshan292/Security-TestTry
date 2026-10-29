"""Shape candidates for the validator slot in shadow mode.

Validators run only on candidates: short substrings that have the SHAPE of a scheme (right length and character
classes), whether or not they pass any checksum. This is what lets a comparison see disagreements in both directions
(a value the built-in accepts and the plugin rejects, and the reverse) without running a validator over the whole
text. Every pattern is linear (bounded repetition, alnum lookarounds) and the number of candidates per text is capped.
"""
from __future__ import annotations

import re

MAX_CANDIDATES = 256

_L = r"(?<![A-Za-z0-9])"
_R = r"(?![A-Za-z0-9])"
SHAPES: tuple[tuple[str, re.Pattern], ...] = (
    ("AADHAAR", re.compile(_L + r"\d{4}[ \-]?\d{4}[ \-]?\d{4}" + _R)),
    ("VID", re.compile(_L + r"\d{4}[ \-]?\d{4}[ \-]?\d{4}[ \-]?\d{4}" + _R)),
    ("CARD", re.compile(_L + r"\d(?:[ \-]?\d){12,18}" + _R)),
    ("PAN", re.compile(_L + r"[A-Za-z]{5}\d{4}[A-Za-z]" + _R)),
    ("IFSC", re.compile(_L + r"[A-Za-z]{4}0[A-Za-z0-9]{6}" + _R)),
    ("GSTIN", re.compile(_L + r"\d{2}[A-Za-z]{5}\d{4}[A-Za-z][0-9A-Za-z]{3}" + _R)),
    ("EPIC", re.compile(_L + r"[A-Za-z]{3}\d{7}" + _R)),
    ("IBAN", re.compile(_L + r"[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}" + _R)),
    ("UPI_ID", re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._\-]{2,64}@[A-Za-z][A-Za-z0-9]{1,30}(?![A-Za-z0-9.\-@])")),
    ("PHONE", re.compile(r"(?<![\w+])(?:\(\+\d{1,3}\)[ \-]?|\+\d{1,3}[ \-]?)?\(?\d(?:[ \-)]?\d){7,13}" + _R)),
)


def candidates(text: str, types: frozenset | None = None) -> tuple[list[tuple[str, int, int]], bool]:
    """[(entity_type, start, end)] in text order per type, and whether the cap cut the list."""
    out: list[tuple[str, int, int]] = []
    for t, rx in SHAPES:
        if types is not None and t not in types:
            continue
        for m in rx.finditer(text):
            if len(out) >= MAX_CANDIDATES:
                return out, True
            out.append((t, m.start(), m.end()))
    return out, False
