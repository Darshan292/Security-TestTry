"""D0 normalisation with an offset map back to the original string.

Detectors run on the normalised view; every span is mapped back to the original
text before transformation, so the original surface (including any zero-width
characters an attacker inserted) is what gets replaced.
"""
from __future__ import annotations

import unicodedata

ZERO_WIDTH = frozenset("​‌‍⁠﻿­᠎")

# Minimal confusables fold (Cyrillic/Greek look-alikes → Latin). Detection view only.
_CONFUSABLES = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j",
    "ѕ": "s", "һ": "h", "ԁ": "d", "ɡ": "g", "ⅼ": "l",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
    "α": "a", "ο": "o", "ρ": "p", "ν": "v", "τ": "t", "ι": "i", "κ": "k",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M",
    "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
}


def normalize(text: str) -> tuple[str, list[int]]:
    """Return (normalised_text, omap) where omap[i] is the original index of normalised char i.

    omap has one extra trailing entry equal to len(text), so end offsets map cleanly.
    """
    out: list[str] = []
    omap: list[int] = []
    for i, ch in enumerate(text):
        if ch in ZERO_WIDTH:
            continue
        n = unicodedata.normalize("NFKC", ch)
        for c in n:
            c = _CONFUSABLES.get(c, c)
            if c.isspace():
                c = " "
            out.append(c)
            omap.append(i)
    omap.append(len(text))
    return "".join(out), omap


def to_original(omap: list[int], start: int, end: int) -> tuple[int, int]:
    """Map a normalised [start, end) span to the original string."""
    if end <= start:
        return omap[start], omap[start]
    return omap[start], omap[end - 1] + 1
