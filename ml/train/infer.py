"""Runtime-independent document inference for the token classifier: windows, batching, merging.

`logits_fn(input_ids, attention_mask) -> array[batch, seq, n_labels]` wraps either a torch model or an ONNX session, so
the same code scores fp32 and int8 and the tests can use a fake. The tokenizer is a HuggingFace *fast* tokenizer (or any
object with the same call signature)."""
from __future__ import annotations

from typing import Callable

import numpy as np

from .bio import make_windows, merge_window_labels
from .labels import BIO_LABELS, LABEL2ID


def softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def tokenize_doc(tokenizer, text: str) -> tuple[list[int], list[tuple[int, int]]]:
    enc = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, truncation=False)
    return list(enc["input_ids"]), [tuple(o) for o in enc["offset_mapping"]]


def window_inputs(tokenizer, ids: list[int], max_len: int, stride: int) -> list[tuple[int, int, list[int]]]:
    out = []
    for s, e in make_windows(len(ids), max_len, stride):
        out.append((s, e, [tokenizer.cls_token_id, *ids[s:e], tokenizer.sep_token_id]))
    return out


def doc_probs(text: str, tokenizer, logits_fn: Callable, max_len: int = 512, stride: int = 128, batch: int = 8):
    """-> (probs[n_tokens, n_labels], offsets). Overlapping windows are merged by distance from the window edge."""
    ids, offsets = tokenize_doc(tokenizer, text)
    n = len(ids)
    if n == 0:
        return np.zeros((0, len(BIO_LABELS))), offsets
    wins = window_inputs(tokenizer, ids, max_len, stride)
    pad = tokenizer.pad_token_id
    per_window = []
    for b in range(0, len(wins), batch):
        chunk = wins[b:b + batch]
        width = max(len(w[2]) for w in chunk)
        x = np.full((len(chunk), width), pad, dtype=np.int64)
        m = np.zeros((len(chunk), width), dtype=np.int64)
        for r, w in enumerate(chunk):
            x[r, :len(w[2])] = w[2]
            m[r, :len(w[2])] = 1
        p = softmax(np.asarray(logits_fn(x, m), dtype="float64"))
        for r, w in enumerate(chunk):
            per_window.append(p[r, 1:1 + (w[1] - w[0])])
    # merge probabilities: keep the window where the token has the most context
    best = np.zeros((n, len(BIO_LABELS)))
    margin = np.full(n, -1)
    for (s, e, _), p in zip(wins, per_window, strict=True):
        for k in range(e - s):
            mg = min(k, e - s - 1 - k)
            if mg > margin[s + k]:
                margin[s + k] = mg
                best[s + k] = p[k]
    return best, offsets


def argmax_ids(probs: np.ndarray) -> list[int]:
    return [int(i) for i in probs.argmax(axis=-1)] if len(probs) else []


__all__ = ["doc_probs", "softmax", "tokenize_doc", "window_inputs", "argmax_ids", "merge_window_labels", "LABEL2ID"]
