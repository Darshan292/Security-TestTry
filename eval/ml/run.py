"""Backend-agnostic span evaluation (docs/ML_EVALUATION.md).

    # model selection (dev data only; any number of runs)
    python -m eval.ml.run --role dev --data eval/data3/dev3build.jsonl --format pgw --backend current
    python -m eval.ml.run --role dev --data dev.jsonl --format pgw --backend ml_only --model models/pii-v1.yaml

    # release gate (external tester's sets, human-labelled real sample): never for tuning
    python -m eval.ml.run --role release --data /secure/real_sample.jsonl --format human \
        --backend ml_checksum --model models/pii-v1.yaml --gate eval/ml/release_gate.yaml

Protocol (enforced here, documented in docs/ML_EVALUATION.md):
  * --role dev refuses the held-out corpora of the regex releases (test*, ner_test*, dev3val, anything sealed) and
    every path listed under `release_sets` in the gate config: those are never used for model selection.
  * --role release prints aggregates only (no per-example output), records the run (dataset sha256, backend, model
    sha256, timestamp, verdict) in eval/ml/results/release_runs.jsonl, and judges the result against the gate config.
    Thresholds left null there are NOT invented: the verdict is then UNDECIDED.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .backends import BACKENDS, Backend, load_model_config
from .datasets import load
from .metrics import Scorer

ROOT = Path(__file__).resolve().parent
HELD_OUT = re.compile(r"(?:^|/)(?:test\d*|ner_test\d*|dev3val|sealed[^/]*)(?:\.jsonl(?:\.gz)?|\.tar\.enc)?$")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_gate(path: Path | None) -> dict:
    if path is None:
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise SystemExit("gate config must be a mapping")
    return doc


def judge(result: dict, gate: dict) -> dict:
    """Compare a result with the human-set thresholds. A null threshold is never filled in: verdict UNDECIDED."""
    th = gate.get("thresholds") or {}
    checks, undecided = [], []

    def cmp(name, value, limit, kind):
        if limit is None:
            undecided.append(name)
            return
        ok = value <= limit if kind == "max" else value >= limit
        checks.append({"metric": name, "value": value, "limit": limit, "kind": kind, "pass": ok})

    cmp("leak_rate.overall", result["leak_rate"]["overall"]["rate"], th.get("max_leak_rate"), "max")
    for t, lim in (th.get("max_leak_rate_by_type") or {}).items():
        cmp(f"leak_rate.{t}", result["leak_rate"]["by_type"].get(t, {}).get("rate", 0.0), lim, "max")
    for t, lim in (th.get("min_recall_overlap_by_type") or {}).items():
        cmp(f"recall.{t}", result["overlap"]["by_type"].get(t, {}).get("recall", 0.0), lim, "min")
    for t, lim in (th.get("min_precision_overlap_by_type") or {}).items():
        cmp(f"precision.{t}", result["overlap"]["by_type"].get(t, {}).get("precision", 0.0), lim, "min")
    om = result["over_masking"]["benign_spans_masked"]
    if om is not None:
        cmp("over_masking.benign_spans_masked", om, th.get("max_benign_spans_masked"), "max")
    cmp("latency_ms.p95", result["latency_ms"]["p95"], th.get("max_latency_p95_ms"), "max")
    mn = th.get("min_examples")
    if mn is None:
        undecided.append("min_examples")
    else:
        checks.append({"metric": "examples", "value": result["examples"], "limit": mn, "kind": "min",
                       "pass": result["examples"] >= mn})
    verdict = "FAIL" if any(not c["pass"] for c in checks) else ("UNDECIDED" if undecided else "PASS")
    return {"verdict": verdict, "checks": checks, "thresholds_not_set": undecided}


def evaluate(backend: Backend, examples) -> dict:
    sc = Scorer()
    for ex in examples:
        t0 = time.perf_counter()
        pred, protected = backend.run(ex.text)
        ms = (time.perf_counter() - t0) * 1000
        sc.add(ex.text, ex.spans, pred, ex.genre, protected, ex.benign, ms)
    return sc.result()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role", choices=("dev", "release"), required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--format", choices=("pgw", "bio", "human"), default="pgw")
    ap.add_argument("--backend", choices=BACKENDS, default="current")
    ap.add_argument("--model", help="ml_detector mapping (YAML/JSON) as in the deployment profile")
    ap.add_argument("--roster", help="roster JSONL (as the deployment would have)")
    ap.add_argument("--ner", help="spaCy model to run alongside (current/pipeline_ml)")
    ap.add_argument("--gate", type=Path, default=ROOT / "release_gate.yaml")
    ap.add_argument("--label-names", help="comma-separated BIO label names for integer ner_tags")
    ap.add_argument("--type-map", help="comma-separated MODEL=GATEWAY type renames for BIO data, e.g. PER=PERSON")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", type=Path, help="write the JSON result here")
    a = ap.parse_args(argv)

    data = Path(a.data)
    gate = load_gate(a.gate if a.gate and a.gate.exists() else None)
    release_sets = {str(Path(p).resolve()) for p in (gate.get("release_sets") or []) if p}
    if a.role == "dev" and (HELD_OUT.search(data.as_posix()) or str(data.resolve()) in release_sets):
        print("refused: this dataset is held out / a release-gate set; it is never used for model selection "
              "(use --role release)", file=sys.stderr)
        return 2
    kw = {}
    if a.label_names:
        kw["label_names"] = a.label_names.split(",")
    if a.type_map:
        kw["type_map"] = dict(x.split("=", 1) for x in a.type_map.split(","))
    examples, stats = load(data, a.format, **kw)
    if a.limit and a.role == "dev":
        examples = examples[:a.limit]
    backend = Backend(a.backend, load_model_config(a.model) if a.model else None, a.roster, a.ner)
    result = evaluate(backend, examples)
    result["dataset"] = {"name": data.name, "format": a.format, "sha256": sha256_file(data), **stats}
    result["backend"] = {"name": a.backend, "model_sha256": getattr(backend, "model_sha256", None), "ner": a.ner,
                         "roster": bool(a.roster)}
    result["role"] = a.role
    if a.role == "release":
        result["gate"] = judge(result, gate)
        log = ROOT / "results" / "release_runs.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "dataset_sha256": result["dataset"]["sha256"], "dataset": data.name,
                                 "backend": result["backend"], "verdict": result["gate"]["verdict"]}) + "\n")
    text = json.dumps(result, indent=1, sort_keys=True)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
