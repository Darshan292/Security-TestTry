"""Label decoding for token classifiers: BIO / BILOU / IO tags -> entity runs. Pure Python."""
from __future__ import annotations

_BEGIN = {"B", "U", "S"}
_INSIDE = {"I", "L", "E"}
_END = {"L", "E", "U", "S"}


def split_tag(label: str) -> tuple[str, str]:
    """'B-PER' -> ('B', 'PER'); 'O' -> ('O', ''); 'PER' (IO scheme) -> ('I', 'PER')."""
    if label == "O" or not label:
        return "O", ""
    if len(label) > 2 and label[1] == "-" and label[0] in _BEGIN | _INSIDE:
        return label[0], label[2:]
    return "I", label


def decode(tags: list[str]) -> list[tuple[int, int, str]]:
    """Entity runs (first unit, last unit inclusive, type) from per-unit tags.

    Lenient, as models make boundary errors: an I/L tag that does not continue an open entity of the same type starts
    a new one (it is never dropped: a dropped tag is a leaked token). B/U/S always start a new entity; L/E/U/S close
    it after the unit."""
    out: list[tuple[int, int, str]] = []
    cur: list | None = None           # [start, end, type]
    for i, lab in enumerate(tags):
        p, t = split_tag(lab)
        if p == "O":
            if cur:
                out.append(tuple(cur))
                cur = None
            continue
        if p in _BEGIN or cur is None or cur[2] != t:
            if cur:
                out.append(tuple(cur))
            cur = [i, i, t]
        else:
            cur[1] = i
        if p in _END:
            out.append(tuple(cur))
            cur = None
    if cur:
        out.append(tuple(cur))
    return out
