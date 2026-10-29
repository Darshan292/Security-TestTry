#!/usr/bin/env python3
"""Measure imported detector bundles (notebook 04 output) against the current stack on DEV corpora, through the gateway's
own runtime slot (pgw.ml OnnxTokenClassifier / OnnxSpanModel via eval.ml.backends `ml_only`): per-type recall/precision.

    python scripts/ml/bench_detectors.py --bundle indicner=models/indicner --bundle gliner=models/gliner_pii \\
        [--data eval/data/dev.jsonl ...] [--ml-train-docs 600] [--sample 1000] [--min-score 0.5] [--out result.json]

A bundle directory is first verified exactly as `scripts/ml/import_artifacts.py` does (sha256sums, runtime validation,
licence + "not validated on real data" in the card); its label map comes from train_meta.json. Nothing is adopted: this
prints numbers on dev data only ("dev only, not a release number").

REFUSES held-out data: any path that eval.ml.run.is_held_out recognises (test*/ner_test*/dev3val, sealed archives, tester
sets, anything named heldout, the gate's release_sets) is rejected from its name, before it is opened. Held-out scoring is
a separate, logged, human-triggered step on the evaluation machine only (docs/ML_TRAINING.md section "Held-out scoring").
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.ml.backends import Backend  # noqa: E402
from eval.ml.datasets import Example, load  # noqa: E402
from eval.ml.run import evaluate, is_held_out, load_gate  # noqa: E402

DEFAULT_CORPORA = ("eval/data/dev.jsonl", "eval/data2/dev2.jsonl", "eval/data3/dev3build.jsonl")


def _ia():
    spec = importlib.util.spec_from_file_location("import_artifacts", ROOT / "scripts" / "ml" / "import_artifacts.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def refuse_held_out(path: Path) -> None:
    if is_held_out(path, load_gate(ROOT / "eval" / "ml" / "release_gate.yaml")):
        raise SystemExit(f"refused: {path} looks held-out / release-gate; bench_detectors scores dev data only")


def model_config(bundle: Path, min_score: float | None) -> tuple[dict, dict]:
    """(ml_detector mapping for Backend, verification result). Raises SystemExit if the bundle does not verify."""
    ia = _ia()
    res = ia.verify(bundle)
    if not res["ok"]:
        raise SystemExit(f"{bundle}: bundle verification failed: " + "; ".join(res["problems"]))
    if res["kind"] != "external_detector":
        snippet, _ = ia.token_snippet(bundle, res)
    else:
        snippet, _ = ia.external_snippet(bundle, res)
    cfg = dict(snippet["ml_detector"])
    cfg["path"] = str(bundle)
    cfg.update({"workers": 0, "queue_max": 4, "timeout_s": 60.0})
    if min_score is not None:
        cfg["min_score_by_type"] = {"DEFAULT": float(min_score)}
    return cfg, res


def table(base: dict, others: dict[str, dict]) -> str:
    names = list(others)
    head = "| type | gold | current R | current P | " + " | ".join(f"{n} R | {n} P" for n in names) + " |"
    lines = [head, "|" + "---|" * (4 + 2 * len(names))]
    for t, row in base["overlap"]["by_type"].items():
        if not row["gold"]:
            continue
        cells = []
        for n in names:
            r = others[n]["overlap"]["by_type"].get(t)
            cells += [f"{r['recall']:.3f}", f"{r['precision']:.3f}"] if r else ["-", "-"]
        lines.append(f"| {t} | {row['gold']} | {row['recall']:.3f} | {row['precision']:.3f} | " + " | ".join(cells) + " |")
    o = base["overlap"]["overall"]
    cells = []
    for n in names:
        r = others[n]["overlap"]["overall"]
        cells += [f"{r['recall']:.3f}", f"{r['precision']:.3f}"]
    lines.append(f"| ALL | {o['gold']} | {o['recall']:.3f} | {o['precision']:.3f} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", action="append", required=True, metavar="NAME=DIR")
    ap.add_argument("--data", action="append", help="dev corpus, pgw JSONL format (default: the three dev corpora)")
    ap.add_argument("--ml-train-docs", type=int, default=0, help="also score N synthetic ml/train documents (seed 7)")
    ap.add_argument("--sample", type=int, default=None, help="at most N rows per corpus (every k-th)")
    ap.add_argument("--min-score", type=float, default=None, help="override the bundle's DEFAULT score threshold")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    corpora = [Path(p) if Path(p).is_absolute() else ROOT / p for p in (a.data or DEFAULT_CORPORA)]
    for p in corpora:                      # decided from the path, before anything is opened
        refuse_held_out(p)
    bundles = {}
    for spec in a.bundle:
        name, _, d = spec.partition("=")
        if not name or not d:
            raise SystemExit("--bundle needs NAME=DIR")
        bundles[name] = Path(d)
    sets: dict[str, list[Example]] = {}
    for p in corpora:
        rows = load(p, "pgw")[0]
        if a.sample and len(rows) > a.sample:
            step = len(rows) / a.sample
            rows = [rows[int(i * step)] for i in range(a.sample)]
        sets[p.name] = rows
    if a.ml_train_docs:
        from ml.train import synth
        sets[f"ml/train synthetic ({a.ml_train_docs}, seed 7)"] = [
            Example(r["id"], r["text"], [(s["start"], s["end"], s["type"]) for s in r["spans"]], r.get("genre", "all"))
            for r in synth.programmatic_records(a.ml_train_docs, 7)]
    base_backend = Backend("current")
    runners = {}
    for name, d in bundles.items():
        cfg, res = model_config(d, a.min_score)
        runners[name] = (Backend("ml_only", cfg), res["manifest_digest"])
    out = {"role": "dev", "note": "dev only, not a release number", "bundles": {n: r[1] for n, r in runners.items()}, "corpora": {}}
    for cname, exs in sets.items():
        print(f"== {cname}: {len(exs)} documents", file=sys.stderr)
        base = evaluate(base_backend, exs)
        others = {n: evaluate(b, exs) for n, (b, _) in runners.items()}
        out["corpora"][cname] = {"current": base, **others}
        print(f"\n**{cname}** ({len(exs)} documents; overlap recall R / precision P; dev only)\n")
        print(table(base, others))
        for n, r in others.items():
            print(f"\n{n}: p50 {r['latency_ms']['p50']} ms, p95 {r['latency_ms']['p95']} ms per document; leak rate "
                  f"{r['leak_rate']['overall']['rate']} (current {base['leak_rate']['overall']['rate']})")
    if a.out:
        a.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
