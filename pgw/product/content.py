"""Content-class checks that can only make a route stricter (PRODUCT_ARCHITECTURE §2, class C4)."""
from __future__ import annotations

import unicodedata


def unreliable_script(text: str) -> bool:
    """True if the text contains letters outside the Latin script.

    The deterministic detectors (and the roster) cannot reliably find personal data in other scripts (R3 M-41,
    E-UTIL: Devanagari names left raw), so such content is class C4. Hinglish written in Latin script is fine.
    """
    for ch in text:
        if ch.isalpha() and ord(ch) > 0x24F and "LATIN" not in unicodedata.name(ch, ""):
            return True
    return False
