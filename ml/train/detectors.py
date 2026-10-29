"""Pure-Python helpers for notebook 04 (export of third-party open detectors to the gateway's artefact format):
candidate table, REVIEWED label maps (model label -> gateway type, unmapped -> IGNORE), fp32-vs-int8 agreement, latency
statistics and the bundle card/metrics writers. No torch, no gateway import; unit-tested in tests/ml_train/test_detectors.py.

Nothing here claims a model works: the notebook records a status per candidate (`exported`, `unsupported`, `error`,
`licence_refused`) with the reason, and only `exported` after the gateway's own runtime loaded the bundle and ran it.
"""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from .labels import ENTITY_TYPES

IGNORE = "IGNORE"
GATEWAY_TYPES = frozenset(ENTITY_TYPES)

# GLiNER prompt labels (natural-language label -> gateway type). The model is zero-shot over these strings, so the
# prompt itself is part of the review. Anything the model could return outside this table does not exist: the labels
# in labels.json ARE this list.
GLINER_PII_PROMPTS: dict[str, str] = {
    "person": "PERSON", "email": "EMAIL", "phone number": "PHONE", "credit card number": "CARD",
    "bank account number": "BANK_ACCOUNT", "date of birth": "DATE_OF_BIRTH", "password": "SECRET",
    "employee id": "EMPLOYEE_ID", "salary": "COMPENSATION", "national id number": "AADHAAR",
    "tax id number": "PAN", "upi id": "UPI_ID", "ifsc code": "IFSC",
    # Labels the model knows but that are not gateway types: kept in the prompt so they cannot be mistaken for PII.
    "organization": IGNORE, "location": IGNORE,
}
# Token-classification label maps, keyed by NORMALISED label (see normalise_label). REVIEW BEFORE USE: a label that is
# not in the table maps to IGNORE and is listed in the bundle's MODEL_CARD.md so a human adds it deliberately.
INDICNER_MAP: dict[str, str] = {"per": "PERSON", "loc": IGNORE, "org": IGNORE, "misc": IGNORE}
# openai/privacy-filter: the label set could not be read offline; this table lists the names such a filter plausibly
# emits. The notebook prints the model's real labels and every one missing here goes to IGNORE (and into the card).
PRIVACY_FILTER_MAP: dict[str, str] = {
    "privateperson": "PERSON", "person": "PERSON", "privateemail": "EMAIL", "email": "EMAIL",
    "privatephone": "PHONE", "phone": "PHONE", "accountnumber": "BANK_ACCOUNT", "bankaccount": "BANK_ACCOUNT",
    "secret": "SECRET", "password": "SECRET", "privatedate": IGNORE, "privateaddress": IGNORE, "privateurl": IGNORE,
}

CANDIDATES: dict[str, dict] = {
    "openai_privacy_filter": {"repo": "openai/privacy-filter", "kind": "auto", "map": PRIVACY_FILTER_MAP,
                              "note": "label set unverified offline; architecture decided from the Hub config"},
    "indicner": {"repo": "ai4bharat/IndicNER", "kind": "token", "map": INDICNER_MAP,
                 "note": "PER/LOC/ORG only: a PERSON detector for Indic-language names, nothing else"},
    "gliner_multi_pii_v1": {"repo": "urchade/gliner_multi_pii-v1", "kind": "span", "prompts": GLINER_PII_PROMPTS,
                            "note": "Apache-2.0 card; NOT gliner_multi v0 (CC-BY-NC)"},
    "gliner2_pii": {"repo": "", "kind": "unsupported",
                    "note": "GLiNER2 PII: different library/ONNX signature from the GLiNER v1 runtime slot. Set PGW_GLINER2_REPO to a "
                            "repo whose card states a permissive licence to have the licence + signature checked; it is "
                            "recorded as unsupported unless the exported graph has the six GLiNER v1 inputs"},
}
SPAN_INPUTS = ("input_ids", "attention_mask", "words_mask", "text_lengths", "span_idx", "span_mask")


def normalise_label(label: str) -> str:
    """'B-PER' -> 'per', 'I-PRIVATE_EMAIL' -> 'privateemail'."""
    base = re.sub(r"^[BILUES]-", "", label.strip())
    return re.sub(r"[^a-z0-9]", "", base.lower())


def map_token_labels(id2label: list[str], table: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """-> ({entity label (prefix stripped) : gateway type | IGNORE}, [labels the table did not know]). Unknown -> IGNORE."""
    ents = list(dict.fromkeys(re.sub(r"^[BILUES]-", "", x) for x in id2label if x != "O"))
    out, unknown = {}, []
    for e in ents:
        t = table.get(normalise_label(e))
        if t is None:
            out[e] = IGNORE
            unknown.append(e)
        else:
            assert t == IGNORE or t in GATEWAY_TYPES, f"map target {t!r} is not a gateway type"
            out[e] = t
    return out, unknown


def span_prompt_map(prompts: dict[str, str]) -> dict[str, str]:
    bad = [v for v in prompts.values() if v != IGNORE and v not in GATEWAY_TYPES]
    assert not bad, f"prompt map targets are not gateway types: {bad}"
    return dict(prompts)


# ----------------------------------------------------------------------------------------------------- measurements
def _ov(a, b) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def span_agreement(ref: list[list[tuple]], other: list[list[tuple]]) -> dict:
    """Agreement of `other` (int8) with `ref` (fp32) on the same texts. Spans are (start, end, type[, score]).
    exact: identical (start, end, type); overlap: overlapping same type. Reported as recall of ref spans, precision
    against ref, and the share of documents whose span sets are identical."""
    assert len(ref) == len(other)
    n_ref = n_oth = ex = ov_r = ov_o = same_docs = 0
    for r, o in zip(ref, other, strict=True):
        r3 = [(a, b, t) for a, b, t, *_ in r]
        o3 = [(a, b, t) for a, b, t, *_ in o]
        n_ref += len(r3)
        n_oth += len(o3)
        ex += sum(1 for x in r3 if x in o3)
        ov_r += sum(1 for x in r3 if any(_ov(x, y) and x[2] == y[2] for y in o3))
        ov_o += sum(1 for y in o3 if any(_ov(x, y) and x[2] == y[2] for x in r3))
        same_docs += set(r3) == set(o3)

    def rate(a, b):
        return round(a / b, 4) if b else 1.0
    return {"ref_spans": n_ref, "other_spans": n_oth, "exact_recall": rate(ex, n_ref), "overlap_recall": rate(ov_r, n_ref),
            "overlap_precision": rate(ov_o, n_oth), "identical_docs": rate(same_docs, len(ref)), "docs": len(ref)}


def gold_recall(gold: list[list[tuple]], pred: list[list[tuple]], min_support: int = 1) -> dict:
    """Overlap recall of gold spans by predictions of the same type; {"micro": r, "per_type": {t: (r, support)}}.
    Same shape as ml.train.card.max_recall_drop expects."""
    tot: dict[str, list[int]] = {}
    for g, p in zip(gold, pred, strict=True):
        p3 = [(a, b, t) for a, b, t, *_ in p]
        for a, b, t in ((s[0], s[1], s[2]) for s in g):
            c = tot.setdefault(t, [0, 0])
            c[1] += 1
            c[0] += any(_ov((a, b), y) and y[2] == t for y in p3)
    micro_h = sum(c[0] for c in tot.values())
    micro_n = sum(c[1] for c in tot.values())
    return {"micro": round(micro_h / micro_n, 4) if micro_n else 0.0,
            "per_type": {t: (round(c[0] / c[1], 4), c[1]) for t, c in sorted(tot.items()) if c[1] >= min_support}}


def latency_stats(ms: list[float]) -> dict:
    if not ms:
        return {"n": 0}
    s = sorted(ms)
    q = lambda p: round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 3)  # noqa: E731
    return {"n": len(s), "p50": q(0.5), "p95": q(0.95), "max": round(s[-1], 3), "mean": round(statistics.fmean(s), 3)}


# -------------------------------------------------------------------------------------------------- bundle writers
CARD = """# {title}

**Third-party pretrained detector, exported for evaluation. Not trained by this project; the original training data is the
upstream model card's, not ours.** Evaluated only on synthetic text generated by `ml/train` (dev). **Not validated on real
data.** Do not deploy: this bundle exists so `scripts/ml/bench_detectors.py` can measure it against the current stack.

## Source
`{repo}` (revision: {revision}). Licence read from the model card at export time: **{licence}** (allowlist: {allowed}).

## Export
{export}

## Labels and mapping to gateway types (reviewed table in ml/train/detectors.py)
{mapping}

Labels the table did not know (mapped to IGNORE, review them): {unknown}

## Synthetic-dev results
```json
{metrics}
```
"""


def write_card(path, *, title, repo, revision, licence, allowed, export, mapping: dict[str, str], unknown, metrics) -> None:
    rows = "\n".join(f"- `{k}` -> {v}" for k, v in mapping.items()) or "- (none)"
    Path(path).write_text(CARD.format(title=title, repo=repo, revision=revision or "unknown", licence=licence,
                                      allowed=", ".join(sorted(allowed)), export=export, mapping=rows,
                                      unknown=", ".join(unknown) or "none",
                                      metrics=json.dumps(metrics, indent=2, sort_keys=True)[:6000]), encoding="utf-8")


def bench_meta(label_map: dict[str, str], backend: str, threshold: float | None = None) -> dict:
    """train_meta.json for an EXTERNAL detector bundle: the label map and slot backend scripts/ml/bench_detectors.py and
    scripts/ml/import_artifacts.py (--kind external_detector) read. Not read by the gateway."""
    assert backend in ("token", "span")
    return {"external": True, "backend": backend, "label_map": dict(label_map), "operating_threshold": threshold}
