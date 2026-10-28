"""Turn labelled records into model-ready windows (token classifier) and dev/train datasets, without torch."""
from __future__ import annotations

from .bio import SpanError, make_windows, spans_to_bio
from .infer import tokenize_doc


def encode_record(rec: dict, tokenizer, max_len: int = 512, stride: int = 128) -> list[dict]:
    """One record -> a list of {"input_ids","attention_mask","labels"} windows (special tokens get label -100).
    Raises SpanError if a token straddles two entities; callers count and drop those records."""
    ids, offsets = tokenize_doc(tokenizer, rec["text"])
    labels = spans_to_bio(rec["text"], rec["spans"], offsets)
    out = []
    for s, e in make_windows(len(ids), max_len, stride):
        ii = [tokenizer.cls_token_id, *ids[s:e], tokenizer.sep_token_id]
        out.append({"input_ids": ii, "attention_mask": [1] * len(ii), "labels": [-100, *labels[s:e], -100]})
    return out


def encode_records(records, tokenizer, max_len: int = 512, stride: int = 128) -> tuple[list[dict], int]:
    rows, dropped = [], 0
    for r in records:
        try:
            rows.extend(encode_record(r, tokenizer, max_len, stride))
        except SpanError:
            dropped += 1
    return rows, dropped


def class_weights(rows, n_labels: int, floor: float = 1.0, cap: float = 10.0) -> list[float]:
    """Inverse-sqrt-frequency weights relative to O, clipped to [floor, cap]; O gets 1.0."""
    counts = [0] * n_labels
    for r in rows:
        for l in r["labels"]:
            if l >= 0:
                counts[l] += 1
    o = max(counts[0], 1)
    w = [1.0] + [min(cap, max(floor, (o / max(c, 1)) ** 0.5 / 10)) for c in counts[1:]]
    return w
