"""Character spans <-> token BIO labels, plus sliding windows for documents longer than the model's max length.

`offsets` is the list of (start, end) character offsets per token as returned by a HuggingFace fast tokenizer with
`return_offsets_mapping=True`; special tokens have (0, 0) and receive label -100 (ignored by the loss). The functions
only need that list, so tests use a fake tokenizer.
"""
from __future__ import annotations

from typing import Sequence

from .labels import BIO_LABELS, ENTITY_TYPES, LABEL2ID, NEWLINE_OK

IGNORE = -100


class SpanError(ValueError):
    pass


def validate_spans(text: str, spans: Sequence[dict]) -> None:
    """Spans must be in range, non-empty, non-overlapping, of a known type, and must not cross a newline unless the type
    allows it (NEWLINE_OK). A PERSON that crosses a line is almost always two fields glued together."""
    last_end = -1
    for s in sorted(spans, key=lambda s: (s["start"], s["end"])):
        a, b, t = s["start"], s["end"], s["type"]
        if t not in ENTITY_TYPES:
            raise SpanError(f"unknown type {t!r}")
        if not (0 <= a < b <= len(text)):
            raise SpanError(f"span {a}:{b} out of range for text of length {len(text)}")
        if a < last_end:
            raise SpanError(f"overlapping spans at {a} (previous ended at {last_end})")
        if t not in NEWLINE_OK and ("\n" in text[a:b] or "\r" in text[a:b]):
            raise SpanError(f"{t} span {a}:{b} crosses a newline")
        last_end = b


def spans_to_bio(text: str, spans: Sequence[dict], offsets: Sequence[tuple[int, int]]) -> list[int]:
    """Label ids per token. A token is inside a span if it overlaps it; the first overlapping token is B-, the rest I-.
    A token overlapping two spans is an error (the tokenizer glued distinct entities; callers drop or re-split)."""
    validate_spans(text, spans)
    ordered = sorted(spans, key=lambda s: s["start"])
    labels = [IGNORE] * len(offsets)
    si = 0
    started: set[int] = set()
    for i, (a, b) in enumerate(offsets):
        if a == b or not text[a:b].strip():  # special token, empty offset, or a bare "▁" whitespace piece
            continue
        while si < len(ordered) and ordered[si]["end"] <= a:
            si += 1
        hits = [j for j in range(si, len(ordered)) if ordered[j]["start"] < b and ordered[j]["end"] > a]
        # (the loop above skips spans that ended before this token; later spans may start inside it)
        if len(hits) > 1:
            raise SpanError(f"token {a}:{b} overlaps {len(hits)} spans")
        if not hits:
            labels[i] = LABEL2ID["O"]
            continue
        sp = ordered[hits[0]]
        prefix = "I" if hits[0] in started else "B"
        started.add(hits[0])
        labels[i] = LABEL2ID[f"{prefix}-{sp['type']}"]
    return labels


def bio_to_spans(text: str, label_ids: Sequence[int], offsets: Sequence[tuple[int, int]]) -> list[dict]:
    """Decode token labels back to character spans (used to score ONNX output at entity level). Tokens with offset (0,0)
    or label -100 are skipped. An I- without a matching open span starts a new one; a type change starts a new one."""
    spans: list[dict] = []
    cur: dict | None = None
    for lid, (a, b) in zip(label_ids, offsets, strict=True):
        if a == b or lid == IGNORE:
            continue
        lab = BIO_LABELS[lid]
        if lab == "O":
            cur = None
            continue
        pre, typ = lab.split("-", 1)
        if pre == "B" or cur is None or cur["type"] != typ:
            cur = {"start": a, "end": b, "type": typ}
            spans.append(cur)
        else:
            cur["end"] = b
    # trim whitespace a token may carry and drop newline-crossing results of non-newline types
    out = []
    for s in spans:
        seg = text[s["start"]:s["end"]]
        lead = len(seg) - len(seg.lstrip())
        s = {**s, "start": s["start"] + lead, "end": s["start"] + lead + len(seg.strip())}
        if s["end"] > s["start"]:
            out.append(s)
    return out


def bio_strings(label_ids: Sequence[int]) -> list[str]:
    """Label names for seqeval; -100 positions are dropped by the caller via the mask."""
    return [BIO_LABELS[i] for i in label_ids if i != IGNORE]


def make_windows(n_tokens: int, max_len: int, stride: int, specials: int = 2) -> list[tuple[int, int]]:
    """Content-token windows [start, end) so each window plus `specials` special tokens fits in max_len; consecutive
    windows overlap by `stride` content tokens. Covers every token; a short text gives one window."""
    size = max_len - specials
    if size <= 0 or not 0 <= stride < size:
        raise ValueError("need 0 <= stride < max_len - specials")
    if n_tokens <= size:
        return [(0, n_tokens)]
    out, start = [], 0
    while True:
        end = min(start + size, n_tokens)
        out.append((start, end))
        if end == n_tokens:
            return out
        start = end - stride


def merge_window_labels(n_tokens: int, windows: Sequence[tuple[int, int]], window_preds: Sequence[Sequence[int]]) -> list[int]:
    """Merge per-window predictions into one sequence; in an overlap the token keeps the prediction from the window in
    which it is farthest from the window edge (more context)."""
    best = [None] * n_tokens
    for (s, e), preds in zip(windows, window_preds, strict=True):
        for k, p in enumerate(preds[: e - s]):
            i = s + k
            margin = min(k, e - s - 1 - k)
            if best[i] is None or margin > best[i][0]:
                best[i] = (margin, p)
    return [b[1] if b else LABEL2ID["O"] for b in best]
