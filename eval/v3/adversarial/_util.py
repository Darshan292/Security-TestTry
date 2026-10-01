"""Mechanism-neutral helpers shared by known.py and heldout.py.

A kind "core" is `core(item, rng) -> (new_item, info) | None` where
info = {"orig_idx": [original index of each surviving span], "changed": [bool per surviving span],
        "dropped": int}.  `make_entry` wraps it into the public `fn(item, rng) -> item | None`
plus `fn_ex` (the same call, but also returning info) so tests can audit.
"""
import copy
import datetime as _dt
import re

DROP = "__drop__"  # marker class below


class Drop:
    """New surface for a span whose value a human could no longer recover: the text is
    rewritten but the span is not kept as gold."""
    __slots__ = ("surface",)

    def __init__(self, surface):
        self.surface = surface


def span_texts(item):
    t = item["text"]
    return [t[s["start"]:s["end"]] for s in item["spans"]]


def _finish(item, kind, text, spans, orig_idx, changed, dropped):
    new = {k: copy.deepcopy(v) for k, v in item.items() if k not in ("text", "spans", "id")}
    new["id"] = "%s~%s" % (item["id"], kind)
    new["text"] = text
    new["spans"] = spans
    # keep dict key order close to the input
    ordered = {}
    for k in item:
        ordered[k] = new[k]
    return ordered, {"orig_idx": orig_idx, "changed": changed, "dropped": dropped}


def assemble(item, kind, span_new, gap_new=None):
    """Rebuild text from gaps and spans.
    span_new[i]: str (new surface, span kept) | Drop(surface) (text rewritten, span removed).
    gap_new: optional list of len(spans)+1 replacement gap strings (None entries keep the gap)."""
    text, spans = item["text"], item["spans"]
    out, new_spans, idx, changed, dropped = [], [], [], [], 0
    pos, cur = 0, 0
    gaps_changed = False
    for i, s in enumerate(spans):
        gap = text[cur:s["start"]]
        if gap_new is not None and gap_new[i] is not None:
            if gap_new[i] != gap:
                gaps_changed = True
            gap = gap_new[i]
        out.append(gap)
        pos += len(gap)
        new = span_new[i]
        old = text[s["start"]:s["end"]]
        if isinstance(new, Drop):
            out.append(new.surface)
            pos += len(new.surface)
            dropped += 1
        else:
            d = dict(s)
            d["start"], d["end"] = pos, pos + len(new)
            new_spans.append(d)
            idx.append(i)
            changed.append(new != old)
            out.append(new)
            pos += len(new)
        cur = s["end"]
    gap = text[cur:]
    if gap_new is not None and gap_new[len(spans)] is not None:
        if gap_new[len(spans)] != gap:
            gaps_changed = True
        gap = gap_new[len(spans)]
    out.append(gap)
    new_text = "".join(out)
    if new_text == text:
        return None
    return _finish(item, kind, new_text, new_spans, idx, changed, dropped)


def rewrite_chars(item, kind, pieces, ins=None, drop=()):
    """Per-character rewrite of the whole text.
    pieces[i]: replacement string for char i (may be "" or longer); ins: dict {i: str} inserted
    before char i (i == len(text) allowed). An insertion strictly inside a span widens it; at a
    boundary it stays outside. `drop`: indices of spans to remove from gold."""
    text, spans = item["text"], item["spans"]
    ins = ins or {}
    n = len(text)
    out, p_start, p_end, pos = [], [0] * (n + 1), [0] * (n + 1), 0
    for i in range(n + 1):
        s = ins.get(i, "")
        out.append(s)
        pos += len(s)
        p_start[i] = pos
        if i < n:
            out.append(pieces[i])
            pos += len(pieces[i])
        p_end[i] = pos
    new_text = "".join(out)
    if new_text == text:
        return None
    new_spans, idx, changed, dropped = [], [], [], 0
    for k, s in enumerate(spans):
        if k in drop:
            dropped += 1
            continue
        a, b = p_start[s["start"]], p_end[s["end"] - 1]
        d = dict(s)
        d["start"], d["end"] = a, b
        new_spans.append(d)
        idx.append(k)
        changed.append(new_text[a:b] != text[s["start"]:s["end"]])
    return _finish(item, kind, new_text, new_spans, idx, changed, dropped)


def make_entry(kind, core, cls, process, scope="value", recoverable=True):
    def fn_ex(item, rng):
        if not item.get("spans"):
            return None
        r = core(item, rng)
        if r is None:
            return None
        new, info = r
        if scope == "value" and not any(info["changed"]):
            return None
        return new, info

    def fn(item, rng):
        r = fn_ex(item, rng)
        return None if r is None else r[0]

    fn.__name__ = kind
    return {"fn": fn, "fn_ex": fn_ex, "cls": cls, "recoverable": recoverable, "process": process,
            "scope": scope}


# ---- small mechanism-neutral value helpers -------------------------------------------------
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
_MON = {m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)}


def parse_date(s):
    """Parse a date string with a 4-digit year in common layouts; returns (date, layout) or None.
    Day-first is assumed for numeric layouts with '/', '-', '.' (Indian convention)."""
    s = s.strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{2})[-/](\d{2})", s)
    if m:
        return _mk(m.group(1), m.group(2), m.group(3), "ymd")
    m = re.fullmatch(r"(\d{1,2})([/.-])(\d{1,2})\2(\d{4})", s)
    if m:
        return _mk(m.group(4), m.group(3), m.group(1), "dmy")
    m = re.fullmatch(r"(\d{1,2})[- ]([A-Za-z]{3})[- ](\d{4})", s)
    if m and m.group(2).lower() in _MON:
        return _mk(m.group(3), _MON[m.group(2).lower()], m.group(1), "dMy")
    m = re.fullmatch(r"([A-Za-z]+) (\d{1,2}), (\d{4})", s)
    if m and m.group(1)[:3].lower() in _MON:
        return _mk(m.group(3), _MON[m.group(1)[:3].lower()], m.group(2), "Mdy")
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th) ([A-Za-z]+) (\d{4})", s)
    if m and m.group(2)[:3].lower() in _MON:
        return _mk(m.group(3), _MON[m.group(2)[:3].lower()], m.group(1), "dMy")
    return None


def _mk(y, mo, d, layout):
    try:
        return _dt.date(int(y), int(mo), int(d)), layout
    except ValueError:
        return None


def excel_serial(d):
    return (d - _dt.date(1899, 12, 30)).days


def ordinal(n):
    return "%d%s" % (n, "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def choose_positions(rng, n, k):
    """k distinct interior positions 1..n-1 (sorted)."""
    if n < 2:
        return []
    k = min(k, n - 1)
    return sorted(rng.sample(range(1, n), k))
