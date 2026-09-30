"""D4 NER model-selection benchmark (PERSON only).

    python -m eval.run_ner --data eval/data/dev.jsonl --model en_core_web_sm [--limit N]

Precision/recall use the same overlap definition as eval/run_det.py: a gold PERSON is
found if any predicted PERSON span overlaps it; a prediction is correct if it overlaps
any gold PERSON. Latency is warm, single thread, reported per 1 KB of text.
Writes eval/results/ner_<model>_<datasetname>.json.
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import json
import math
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path

from pgw.detect.ner import NerDetector

ROOT = Path(__file__).resolve().parent
FORBIDDEN = ("test2.jsonl", "ner_test2", "adversarial_heldout")
_TOKEN = re.compile(r"\w+", re.UNICODE)


# Same definitions as eval/run_det.py (copied so this runner does not import the whole pipeline).
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


def overlaps(a, b) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else float("nan")


def evaluate(items: list[dict], det: NerDetector, miss_cap: int = 400) -> dict:
    hit_src = defaultdict(lambda: [0, 0])
    tp_r = n_gold = tp_p = n_pred = 0
    neg_fp = neg_tokens = neg_items = 0
    per_kb: list[float] = []
    fp_samples, misses = [], []
    for it in items:
        text = it["text"]
        t0 = time.perf_counter()
        preds = [(d.start, d.end) for d in det.detect(text)]
        dt = (time.perf_counter() - t0) * 1000
        if len(text) >= 200:                       # tiny texts make the per-KB figure meaningless
            per_kb.append(dt / (len(text) / 1024))
        gold = [(g["start"], g["end"]) for g in it["spans"] if g["type"] == "PERSON"]
        src = it.get("name_source") or "unknown"
        for g in gold:
            hit = any(overlaps(g, p) for p in preds)
            n_gold += 1; tp_r += hit
            hit_src[src][0] += hit; hit_src[src][1] += 1
            if not hit and len(misses) < miss_cap:
                misses.append({"id": it["id"], "name_source": src, "name": text[g[0]:g[1]],
                               "context": text[max(0, g[0] - 40):g[1] + 40]})
        for p in preds:
            n_pred += 1
            tp_p += any(overlaps(g, p) for g in gold)
        if it.get("slice") == "negatives":
            neg_items += 1
            neg_tokens += len(_TOKEN.findall(text))
            allgold = [(g["start"], g["end"]) for g in it["spans"]]
            fps = [p for p in preds if not any(overlaps(g, p) for g in allgold)]
            neg_fp += len(fps)
            for p in fps[:2]:
                if len(fp_samples) < 30:
                    fp_samples.append(text[max(0, p[0] - 30):p[1] + 30].replace("\n", " "))
    ext = hit_src.get("external", [0, 0])
    return {
        "items": len(items),
        "person_gold": n_gold, "person_pred": n_pred,
        "recall_overall": tp_r / n_gold if n_gold else None,
        "recall_overall_ci95": wilson(tp_r, n_gold),
        "precision": tp_p / n_pred if n_pred else None,
        "recall_external": ext[0] / ext[1] if ext[1] else None,
        "recall_external_ci95": wilson(*ext) if ext[1] else None,
        "recall_by_name_source": {k: {"recall": v[0] / v[1], "n": v[1], "ci95": wilson(v[0], v[1])}
                                  for k, v in sorted(hit_src.items())},
        "negatives": {"items": neg_items, "tokens": neg_tokens, "false_positives": neg_fp,
                      "fp_per_1k_tokens": 1000 * neg_fp / neg_tokens if neg_tokens else None,
                      "samples": fp_samples},
        "latency_ms_per_kb": {"p50": pct(per_kb, .5), "p95": pct(per_kb, .95), "n": len(per_kb),
                              "mean": statistics.fmean(per_kb) if per_kb else None},
        "sample_misses": misses,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args()
    if any(f in a.data for f in FORBIDDEN):
        raise SystemExit("refusing: held-out file")
    items = load(Path(a.data))
    if a.limit:
        items = items[:a.limit]
    det = NerDetector(a.model)
    det.warm()
    for it in items[:20]:          # extra warm-up, results discarded
        det.detect(it["text"])
    res = evaluate(items, det)
    name = Path(a.data).stem
    res.update(model=a.model, dataset=name, threads=os.environ.get("OMP_NUM_THREADS"))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / f"ner_{a.model}_{name}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    f = lambda x: "n/a" if x is None else f"{x:.3f}"
    print(f"{a.model} {name}: ext recall {f(res['recall_external'])} overall {f(res['recall_overall'])} "
          f"prec {f(res['precision'])} negFP/1k {res['negatives']['fp_per_1k_tokens']:.2f} "
          f"p50 {res['latency_ms_per_kb']['p50']:.1f} p95 {res['latency_ms_per_kb']['p95']:.1f} ms/KB")


if __name__ == "__main__":
    main()
