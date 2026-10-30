#!/usr/bin/env python3
"""Dev-only detector matrix through the gateway's own backends (eval.ml.backends): for every corpus, score
  current | current+spaCy | <each bundle alone> | current+<bundle> | current+spaCy+<bundle>
("+" = the gateway pipeline with the ML slot, i.e. its merge precedence: ID regex > ML > heuristics; spaCy = the NER stage).
Per-type overlap recall/precision, leak rate, latency. Refuses held-out paths from the name (as bench_detectors.py).

    python scripts/ml/bench_matrix.py --bundle pf=/home/user/models/pf_int8 [--ner en_core_web_lg] [--sample N]
        [--ml-train-docs N] [--threads 4] --out matrix.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_detectors as bd  # noqa: E402
from eval.ml.backends import Backend  # noqa: E402
from eval.ml.datasets import Example, load  # noqa: E402
from eval.ml.run import evaluate  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", action="append", required=True, metavar="NAME=DIR")
    ap.add_argument("--data", action="append")
    ap.add_argument("--ner", default="en_core_web_lg")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--ml-train-docs", type=int, default=0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--min-score", type=float)
    ap.add_argument("--cache-ml", action="store_true",
                    help="memoise detector output per (model, text) across configs: the FIRST config that runs a model has the real "
                         "latency, later configs reuse its spans (their ML latency is not meaningful). ONE bundle per process")
    ap.add_argument("--cache-file", type=Path, help="with --cache-ml: load/save the memo (JSON of detector output per text) so "
                    "variants (thresholds, label maps) reuse the detector output")
    ap.add_argument("--label-override", help="comma list MODEL_LABEL=TYPE|IGNORE applied on top of the bundle's label map")
    ap.add_argument("--suffix", default="", help="appended to the bundle name in config names (e.g. '[acct ignored]')")
    ap.add_argument("--only", help="comma list of config names to run")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    corpora = [Path(p) if Path(p).is_absolute() else ROOT / p for p in (a.data or bd.DEFAULT_CORPORA)]
    for p in corpora:
        bd.refuse_held_out(p)
    sets: dict[str, list[Example]] = {}
    for p in corpora:
        rows = load(p, "pgw")[0]
        if a.sample and len(rows) > a.sample:
            step = len(rows) / a.sample
            rows = [rows[int(i * step)] for i in range(a.sample)]
        sets[p.name] = rows
    if a.ml_train_docs:
        from ml.train import synth
        sets[f"ml_train_synth_{a.ml_train_docs}_seed7"] = [
            Example(r["id"], r["text"], [(s["start"], s["end"], s["type"]) for s in r["spans"]], r.get("genre", "all"))
            for r in synth.programmatic_records(a.ml_train_docs, 7)]
    cfgs: dict[str, callable] = {"current": lambda: Backend("current"),
                                 "current+spacy": lambda: Backend("current", ner=a.ner)}
    digests = {}
    for spec in a.bundle:
        name, _, d = spec.partition("=")
        cfg, res = bd.model_config(Path(d), a.min_score)
        cfg.update({"intra_op_threads": a.threads})
        if a.label_override:
            for kv in a.label_override.split(","):
                k, _, v = kv.partition("=")
                if k not in cfg["labels"]:
                    raise SystemExit(f"--label-override: {k!r} is not a label of the bundle")
                cfg["labels"][k] = v
        name += a.suffix
        digests[name] = res["manifest_digest"]
        cfgs[f"{name} alone"] = lambda cfg=cfg: Backend("ml_only", cfg)
        cfgs[f"current+{name}"] = lambda cfg=cfg: Backend("pipeline_ml", cfg)
        cfgs[f"current+spacy+{name}"] = lambda cfg=cfg: Backend("pipeline_ml", cfg, ner=a.ner)
    memos: dict = {}
    if a.cache_ml:
        from pgw.ml import span_model, token_classifier
        for cls in (token_classifier.OnnxTokenClassifier, span_model.OnnxSpanModel):
            orig = cls.detect_many
            memo: dict = {}

            def cached(self, texts, _orig=orig, _memo=memo):
                miss = [t for t in dict.fromkeys(texts) if (self.session_key, t) not in _memo]
                if miss:
                    for t, r in zip(miss, _orig(self, miss), strict=True):
                        _memo[(self.session_key, t)] = r
                return [list(_memo[(self.session_key, t)]) for t in texts]
            cls.detect_many = cached
            cls.session_key = 0      # one model per process when --cache-ml is used
            if a.cache_file and a.cache_file.exists():
                from pgw.ml.base import Span
                for t, sp in json.loads(a.cache_file.read_text(encoding="utf-8")).get(cls.__name__, {}).items():
                    memo[(0, t)] = [Span(*x) for x in sp]
            memos[cls.__name__] = memo
    only = set(a.only.split(",")) if a.only else None
    out = {"role": "dev", "note": "dev only, not a release number", "bundles": digests, "ner": a.ner,
           "threads": a.threads, "docs": {k: len(v) for k, v in sets.items()}, "results": {}}
    for cname, build in cfgs.items():
        if only and cname not in only:
            continue
        b = build()
        for sname, exs in sets.items():
            print(f"== {cname} / {sname}", file=sys.stderr, flush=True)
            out["results"].setdefault(sname, {})[cname] = evaluate(b, exs)
        if a.cache_ml and a.cache_file:
            a.cache_file.write_text(json.dumps({k: {t: [[x.start, x.end, x.type, x.score] for x in sp]
                                                    for (_, t), sp in m.items()} for k, m in memos.items()}), encoding="utf-8")
        a.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
