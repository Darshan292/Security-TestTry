"""L0+: extra normalisation views on top of the D0 base view.

Every view carries TWO maps back to the base-view string: ``smap[i]`` (first base index covered by view
char i) and ``emap[i]`` (one past the last base index covered). Edits delete characters or replace a span
by other characters; a replacement char covers the WHOLE replaced span, so a view span that touches it maps
back to the full original span (D0's single-int map only keeps the first char; for entities such as
``&#49;`` that would leave a fragment behind). Maps compose by reading the parent's maps.

Views (all derived from the base view, emitted only when they differ from it):
  unwrapped   a line break inside an identifier-like token or inside a digit group is removed
  unescaped   ``html.unescape`` of numeric and named character references
  ocr_digits  O->0, l/I->1, S->5, B->8 inside digit-dominant tokens
  id_gap      a single space inside a letters-then-digits code (``KAF 18514``) is removed
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class View:
    name: str
    text: str
    smap: list[int]      # first base index covered by each view char
    emap: list[int]      # exclusive end base index covered by each view char

    def to_base(self, start: int, end: int) -> tuple[int, int]:
        if end <= start:
            i = self.smap[start] if start < len(self.smap) else (self.emap[-1] if self.emap else 0)
            return i, i
        return self.smap[start], self.emap[end - 1]


def identity_view(name: str, text: str) -> View:
    return View(name, text, list(range(len(text))), [i + 1 for i in range(len(text))])


def apply_edits(parent: View, name: str, edits: list[tuple[int, int, str]]) -> View:
    """Apply sorted non-overlapping (start, end, replacement) edits (parent coordinates)."""
    out: list[str] = []
    sm: list[int] = []
    em: list[int] = []
    pos = 0
    ps, pe = parent.smap, parent.emap
    for a, b, rep in edits:
        out.append(parent.text[pos:a])
        sm.extend(ps[pos:a])
        em.extend(pe[pos:a])
        if rep and b > a:
            out.append(rep)
            lo, hi = ps[a], pe[b - 1]
            sm.extend([lo] * len(rep))
            em.extend([hi] * len(rep))
        pos = b
    out.append(parent.text[pos:])
    sm.extend(ps[pos:])
    em.extend(pe[pos:])
    return View(name, "".join(out), sm, em)


# ---------------------------------------------------------------------------------------------- unwrap
_BREAK = re.compile(r"(?:(?<=[A-Za-z0-9])[=\\])?[ \t]*\r?\n[ \t]*")
_WRAP_MIN_LINE = 60


_TAIL_DIGITS = re.compile(r"\d[\d \-]*$")
_HEAD_DIGITS = re.compile(r"\d[\d \-]*")


def _digits_in(m) -> int:
    return sum(c.isdigit() for c in m.group()) if m else 0


def unwrap_view(base: View) -> View | None:
    s = base.text
    edits: list[tuple[int, int, str]] = []
    for m in _BREAK.finditer(s):
        a, b = m.start(), m.end()
        if a == 0 or b >= len(s):
            continue
        ls = s.rfind("\n", 0, a) + 1
        left_line = s[ls:a]
        lt = re.search(r"\S+$", left_line)
        rt = re.match(r"\S+", s[b:])
        if not lt or not rt:
            continue
        ltok, rtok = lt.group(), rt.group()
        soft = m.group()[:1] in ("=", "\\")
        ok = soft
        if not ok and len(left_line) >= _WRAP_MIN_LINE and ("@" in ltok or "@" in rtok) and rtok[-1] not in ":=" \
                and (ltok[-1].isalnum() or ltok[-1] in "._-") and (rtok[0].isalnum() or rtok[0] in "._-"):
            ok = True                                          # an address broken at the margin
        if not ok and ltok.isdigit() and rtok[0].isdigit() and rtok[-1] not in ":=":
            ok = _digits_in(_TAIL_DIGITS.search(left_line)) + _digits_in(_HEAD_DIGITS.match(s, b)) >= 9
        if ok:
            edits.append((a, b, ""))
    if not edits:
        return None
    return apply_edits(base, "unwrapped", edits)


# ---------------------------------------------------------------------------------------------- unescape
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")


def unescape_view(base: View) -> View | None:
    edits = []
    for m in _ENTITY.finditer(base.text):
        r = html.unescape(m.group())
        if r != m.group():
            edits.append((m.start(), m.end(), r))
    if not edits:
        return None
    return apply_edits(base, "unescaped", edits)


# ---------------------------------------------------------------------------------------------- OCR digits
_OCR_TOKEN = re.compile(r"[0-9OlISB]{6,}")
_OCR_MAP = str.maketrans({"O": "0", "l": "1", "I": "1", "S": "5", "B": "8"})


def ocr_view(base: View) -> View | None:
    s = base.text
    edits = []
    for m in _OCR_TOKEN.finditer(s):
        tok = m.group()
        if not (m.start() == 0 or not s[m.start() - 1].isalnum()) or (m.end() < len(s) and s[m.end()].isalnum()):
            continue
        nd = sum(c.isdigit() for c in tok)
        if nd == 0 or nd < 0.6 * len(tok) or nd == len(tok):
            continue
        edits.append((m.start(), m.end(), tok.translate(_OCR_MAP)))
    if not edits:
        return None
    return apply_edits(base, "ocr_digits", edits)


# ---------------------------------------------------------------------------------------------- code with a gap
_ID_GAP = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,5})[ ]([0-9]{4,10})(?![A-Za-z0-9])")


def idgap_view(base: View) -> View | None:
    """``KAF 18514`` -> ``KAF18514``: PDF / OCR extraction splits a letters-then-digits code at the glyph run."""
    edits = [(m.end(1), m.start(2), "") for m in _ID_GAP.finditer(base.text)]
    return apply_edits(base, "id_gap", edits) if edits else None


def extra_views(base_text: str) -> list[View]:
    """L0+ views of the D0 base view text; each maps to base-view indices."""
    base = identity_view("base", base_text)
    out = []
    for fn in (unwrap_view, unescape_view, ocr_view, idgap_view):
        v = fn(base)
        if v is not None:
            out.append(v)
    return out
