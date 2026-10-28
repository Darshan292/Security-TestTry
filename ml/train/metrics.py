"""Entity-level metrics with recall emphasis (a leaked value is worse than an over-masked one).

Spans are dicts {"start","end","type"} in character offsets. `entity_prf` scores exact-boundary matches (strict) or
any same-type overlap (lenient: a partially masked value still hides most of it, but is reported separately because a
half-masked Aadhaar number still leaks). `decode_with_threshold` turns token probabilities into labels with a
recall-oriented rule: a token is an entity if P(not O) >= tau, so lowering tau trades precision for recall.
"""
from __future__ import annotations

from typing import Sequence

from .bio import IGNORE
from .labels import BIO_LABELS, ENTITY_TYPES, LABEL2ID


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "support": tp + fn}


def entity_prf(gold: Sequence[Sequence[dict]], pred: Sequence[Sequence[dict]], lenient: bool = False) -> dict:
    """Per-type and micro precision/recall/F1 over a list of documents."""
    if len(gold) != len(pred):
        raise ValueError("gold and pred must have the same number of documents")
    counts = {t: [0, 0, 0] for t in ENTITY_TYPES}
    for g_doc, p_doc in zip(gold, pred, strict=True):
        used: set[int] = set()
        for g in g_doc:
            hit = None
            for j, p in enumerate(p_doc):
                if j in used or p["type"] != g["type"]:
                    continue
                if (p["start"] == g["start"] and p["end"] == g["end"]) if not lenient else (p["start"] < g["end"] and p["end"] > g["start"]):
                    hit = j
                    break
            if hit is None:
                counts[g["type"]][2] += 1
            else:
                used.add(hit)
                counts[g["type"]][0] += 1
        for j, p in enumerate(p_doc):
            if j not in used:
                counts[p["type"]][1] += 1
    out = {t: _prf(*c) for t, c in counts.items()}
    tp, fp, fn = (sum(c[i] for c in counts.values()) for i in range(3))
    out["micro"] = _prf(tp, fp, fn)
    present = [out[t] for t in ENTITY_TYPES if out[t]["support"]]
    out["macro_recall"] = round(sum(x["recall"] for x in present) / len(present), 4) if present else 0.0
    return out


def decode_with_threshold(probs, tau: float) -> list[int]:
    """probs: array-like [n_tokens, n_labels] of softmax probabilities in BIO_LABELS order. Returns label ids.
    A token is an entity when 1 - P(O) >= tau; its type is the argmax over per-type (P(B)+P(I)); B/I follows the larger
    of the two, and I only continues a run of the same type."""
    import numpy as np
    p = np.asarray(probs, dtype="float64")
    o = LABEL2ID["O"]
    b_cols = [LABEL2ID[f"B-{t}"] for t in ENTITY_TYPES]
    i_cols = [LABEL2ID[f"I-{t}"] for t in ENTITY_TYPES]
    type_mass = p[:, b_cols] + p[:, i_cols]
    out: list[int] = []
    prev_type = -1
    for k in range(p.shape[0]):
        if 1.0 - p[k, o] >= tau and type_mass[k].sum() > 0:
            ti = int(type_mass[k].argmax())
            as_i = prev_type == ti and p[k, i_cols[ti]] >= p[k, b_cols[ti]]
            out.append(i_cols[ti] if as_i else b_cols[ti])
            prev_type = ti
        else:
            out.append(o)
            prev_type = -1
    return out


def mask_special(label_ids: Sequence[int], offsets: Sequence[tuple[int, int]]) -> list[int]:
    return [IGNORE if a == b else l for l, (a, b) in zip(label_ids, offsets, strict=True)]


def recall_sweep(gold_docs, pred_by_tau: dict[float, Sequence[Sequence[dict]]]) -> dict:
    """{tau: {type: recall, 'micro': {...}}}: the table a privacy owner reads to pick an operating threshold."""
    table = {}
    for tau, preds in sorted(pred_by_tau.items()):
        s = entity_prf(gold_docs, preds)
        table[str(tau)] = {"recall": {t: s[t]["recall"] for t in ENTITY_TYPES if s[t]["support"]},
                           "micro_recall": s["micro"]["recall"], "micro_precision": s["micro"]["precision"], "micro_f1": s["micro"]["f1"]}
    return table


def multilabel_report(y_true, y_prob, labels: Sequence[str], recall_target: float = 0.95) -> dict:
    """Per-label threshold chosen on the given (dev) data: the highest threshold whose recall is >= recall_target (so
    precision is as high as the recall constraint allows). y_true, y_prob: [n_docs][n_labels] (0/1 and probabilities)."""
    n = len(y_true)
    rep: dict = {}
    for j, lab in enumerate(labels):
        pos = [y_prob[i][j] for i in range(n) if y_true[i][j]]
        neg = [y_prob[i][j] for i in range(n) if not y_true[i][j]]
        if not pos:
            rep[lab] = {"threshold": 0.5, "support": 0, "note": "no positives in dev"}
            continue
        cands = sorted({0.5, *pos}, reverse=True)
        chosen = min(pos)
        for th in cands:
            if sum(p >= th for p in pos) / len(pos) >= recall_target:
                chosen = th
                break
        chosen = float(min(chosen, 0.5))  # never stricter than the default 0.5: the gate is recall-first
        tp = sum(p >= chosen for p in pos)
        fp = sum(p >= chosen for p in neg)
        rep[lab] = {"threshold": round(chosen, 6), "recall": round(tp / len(pos), 4),
                    "precision": round(tp / (tp + fp), 4) if tp + fp else 0.0, "support": len(pos),
                    "recall_target_met": tp / len(pos) >= recall_target}
    return rep


def label_name(i: int) -> str:
    return BIO_LABELS[i]
