#!/usr/bin/env python3
"""Dev-only: what do a token detector's RAW labels (before the gateway label map) overlap in the gold annotation?

For each model label: hits, and what the hit overlaps: a gold span of type T, or no gold span (a candidate false
positive or an unlabelled value). Also, per gold type, the share of gold spans covered by each model label. Used to
decide mappings (private_date vs DATE_OF_BIRTH, account_number vs BANK_ACCOUNT/CARD/AADHAAR, private_address vs ADDRESS).
When the corpora carry no ADDRESS gold (the dev corpora do not), address hits are compared with the gateway's own
rule-based ADDRESS detections (pgw.detect.places) instead, and sample hits are printed so a human can judge them.

    python scripts/ml/bench_label_analysis.py --bundle pf=/home/user/models/pf_int8_v2 --sample 500 --out la.json
Dev corpora only (refuses held-out paths from the name).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_detectors as bd  # noqa: E402
from eval.ml.backends import Backend  # noqa: E402
from eval.ml.datasets import load  # noqa: E402


def _ov(a, b):
    return a[0] < b[1] and b[0] < a[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", required=True, metavar="NAME=DIR")
    ap.add_argument("--data", action="append")
    ap.add_argument("--sample", type=int, default=500)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--examples", type=int, default=12)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    corpora = [Path(p) if Path(p).is_absolute() else ROOT / p for p in (a.data or bd.DEFAULT_CORPORA)]
    for p in corpora:
        bd.refuse_held_out(p)
    name, _, d = a.bundle.partition("=")
    cfg, res = bd.model_config(Path(d), a.threshold)
    cfg["intra_op_threads"] = a.threads
    from pgw.ml import artifact
    from pgw.ml.span_model import OnnxSpanModel
    from pgw.ml.token_classifier import OnnxTokenClassifier
    art = artifact.load(d, artifact.digest_dir(d)[0])
    model = (OnnxSpanModel if art.task == "span-classification" else OnnxTokenClassifier)(art, intra_op_threads=a.threads)
    stack = Backend("current")
    out: dict = {"role": "dev", "bundle": res["manifest_digest"], "threshold": a.threshold, "corpora": {}}
    for p in corpora:
        rows = load(p, "pgw")[0]
        if a.sample and len(rows) > a.sample:
            step = len(rows) / a.sample
            rows = [rows[int(i * step)] for i in range(a.sample)]
        hit_over = defaultdict(Counter)           # model label -> Counter(gold type or "(no gold)")
        gold_cov = defaultdict(Counter)           # gold type -> Counter(model label covering it, or "(none)")
        addr = Counter()
        examples = defaultdict(list)
        for ex in rows:
            spans = [s for s in model.detect(ex.text) if s.score >= a.threshold]
            gold = [(g[0], g[1], g[2]) for g in ex.spans]
            for s in spans:
                types = sorted({g[2] for g in gold if _ov((s.start, s.end), g)}) or ["(no gold)"]
                for t in types:
                    hit_over[s.type][t] += 1
                hit_over[s.type]["__total__"] += 1
                if len(examples[s.type]) < a.examples and (s.type in ("private_address", "private_date")):
                    examples[s.type].append({"text": ex.text[s.start:s.end], "gold": types, "score": s.score})
            for g in gold:
                cov = sorted({s.type for s in spans if _ov((s.start, s.end), g)}) or ["(none)"]
                for c in cov:
                    gold_cov[g[2]][c] += 1
                gold_cov[g[2]]["__total__"] += 1
            sp_stack, _ = stack.run(ex.text)
            st_addr = [s for s in sp_stack if s[2] == "ADDRESS"]
            pf_addr = [s for s in spans if s.type == "private_address"]
            addr["stack_address_spans"] += len(st_addr)
            addr["pf_address_spans"] += len(pf_addr)
            addr["pf_overlapping_stack"] += sum(any(_ov((s.start, s.end), t[:2]) for t in st_addr) for s in pf_addr)
            addr["stack_overlapping_pf"] += sum(any(_ov(t[:2], (s.start, s.end)) for s in pf_addr) for t in st_addr)
            addr["docs_with_pf_address"] += bool(pf_addr)
            addr["docs_with_stack_address"] += bool(st_addr)
        out["corpora"][p.name] = {"docs": len(rows), "hits_overlap_gold": {k: dict(v) for k, v in hit_over.items()},
                                  "gold_covered_by": {k: dict(v) for k, v in gold_cov.items()}, "address": dict(addr),
                                  "examples": dict(examples)}
        print(f"done {p.name}", file=sys.stderr, flush=True)
        a.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
