"""L1-derived view: every parsed field value on its own line (all-caps multi-word values title-cased, same length).

A delimiter or markup around a value (``a#~#Vikas Nagpal#~#b``, ``<Nm>..</Nm>``) degrades statistical NER and
the roster n-gram matcher; the same values one per line are what those detectors were built for. Detections on
this view map back to the field's value span in the base view; a hit that crosses two fields is discarded.
"""
from __future__ import annotations

import re
import unicodedata

from .records import Graph
from .views import View

_MAX_CHARS = 30_000
_UPPER_WORDS = re.compile(r"^[^\W\d_]{2,}(?:[ .'\-][^\W\d_]+)*$")


def _titled(v: str) -> str:
    if len(v.split()) >= 2 and v.isupper() and _UPPER_WORDS.match(v):
        return v.title()
    return v


def field_view(base: str, graph: Graph) -> View | None:
    spans = sorted({(f.start, f.end) for f in graph.fields if f.end - f.start >= 2})
    if not spans:
        return None
    out: list[str] = []
    sm: list[int] = []
    em: list[int] = []
    total = 0
    for a, b in spans:
        v = base[a:b]
        if "\n" in v or not v.strip():
            continue
        t = _titled(v)
        if len(t) != len(v):
            t = v
        out.append(t + "\n")
        sm.extend(range(a, b))
        em.extend(range(a + 1, b + 1))
        sm.append(b)
        em.append(b)
        total += len(t) + 1
        if total > _MAX_CHARS:
            break
    if not out:
        return None
    return View("fields", "".join(out), sm, em)


_ORG_WORDS = frozenset("""ltd limited pvt private inc llc corp corporation co company bank university college institute school
department division branch plant office centre center technologies solutions services industries enterprises associates
group foundation trust society hospital clinic ministry government""".split())
_NAME_PUNCT = " .'\u2019-"


def _is_wordchar(c: str) -> bool:
    return unicodedata.category(c)[0] in "LM"          # letters and combining marks (Indic vowel signs)


def name_shaped_cell(v: str) -> bool:
    """A value that could be a personal name: 2-5 alphabetic words (any script), no digits, no organisation
    words, Title-case or upper-case where the script has case."""
    v = v.strip()
    if not (4 <= len(v) <= 60) or not _is_wordchar(v[0]) or not all(_is_wordchar(c) or c in _NAME_PUNCT for c in v):
        return False
    words = v.replace(".", ". ").split()
    return (2 <= len(words) <= 5 and not any(w.lower().strip(".,") in _ORG_WORDS for w in words)
            and not any(w[0].islower() for w in words))


def name_cells(base: str, graph: Graph, limit: int = 40) -> list[tuple[int, int, str]]:
    """(start, end, text-for-NER) for distinct name-shaped values: field values, key-position labels of
    ``label: text`` lines, and lines that are a name on their own. All-caps values are title-cased."""
    spans: set[tuple[int, int]] = set()
    for f in graph.fields:
        spans.add((f.start, f.end))
        if f.kend > f.kstart:
            spans.add((f.kstart, f.kend))
    pos = 0
    for ln in base.split("\n"):
        a, b = pos, pos + len(ln)
        pos = b + 1
        t = ln.strip().lstrip("\u2022*-\u2013 \t")
        if t and len(ln) <= 64:
            off = a + ln.index(t[0]) if t[0] in ln else a
            spans.add((off, off + len(t)))
    out = []
    for a, b in sorted(spans):
        while a < b and not _is_wordchar(base[a]) and base[a] not in "\"'":      # a comment marker before the value
            a += 1
        v = base[a:b]
        if not name_shaped_cell(v):
            continue
        out.append((a, b, _titled(v)))
        if len(out) >= limit:
            break
    return out
