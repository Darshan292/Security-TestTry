#!/usr/bin/env python3
"""Dev-only comparison of detection backends (current stack vs Presidio vs their union) on dev corpora and on the
synthetic genres of ml/train. NOT a release number: dev data is what the stack was tuned on (docs/ML_EVALUATION.md).

    python scripts/ml/compare_dev_backends.py [--sample N] [--ml-train-docs N] [--out FILE.json] [--markdown FILE.md]

Corpora: eval/data/dev.jsonl, eval/data2/dev2.jsonl, eval/data3/dev3build.jsonl (all dev/build splits) and
`--ml-train-docs` programmatic documents from ml.train.synth (seeded). `--sample N` takes every k-th row so at most N
rows per corpus are scored (deterministic). Held-out / release sets are refused (same rule as eval.ml.run --role dev).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.ml.backends import Backend  # noqa: E402
from eval.ml.datasets import Example, load  # noqa: E402
from eval.ml.run import evaluate, is_held_out, load_gate  # noqa: E402

CORPORA = ("eval/data/dev.jsonl", "eval/data2/dev2.jsonl", "eval/data3/dev3build.jsonl")
BACKEND_ROWS = (("current", "current stack"), ("current_ner", "current + spaCy NER"), ("presidio", "Presidio"),
                ("union", "union (current + Presidio)"))
BACKEND_ARGS = {"current": ("current", None), "current_ner": ("current", "en_core_web_lg"),
                "presidio": ("presidio", None), "union": ("union", None)}
TYPES = ("PERSON", "EMAIL", "PHONE", "AADHAAR", "PAN", "BANK_ACCOUNT", "IFSC", "CARD", "UPI_ID", "SECRET",
         "EMPLOYEE_ID", "COMPENSATION", "DATE_OF_BIRTH", "IDENTIFIER")


def refuse_if_held_out(path: Path) -> None:
    if is_held_out(path, load_gate(ROOT / "eval" / "ml" / "release_gate.yaml")):
        raise SystemExit(f"refused: {path} is a held-out / release-gate set (dev comparison only)")


def sample(rows: list[Example], n: int | None) -> list[Example]:
    if not n or len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def ml_train_examples(n: int, seed: int = 7) -> list[Example]:
    from ml.train import synth
    out = []
    for r in synth.programmatic_records(n, seed):
        spans = [(s["start"], s["end"], s["type"]) for s in r["spans"]]
        out.append(Example(str(r.get("id")), r["text"], spans, r.get("genre", "all")))
    return out


def summarise(res: dict) -> dict:
    return {"overlap": res["overlap"]["overall"], "exact": res["exact"]["overall"], "leak": res["leak_rate"]["overall"],
            "over_masking": res["over_masking"], "latency_ms": res["latency_ms"],
            "by_type_overlap": res["overlap"]["by_type"], "notes": res.get("backend_notes", {})}


def markdown(out: dict) -> str:
    lines = []
    for corpus, per in out["corpora"].items():
        lines += [f"**{corpus}** ({per['examples']} documents)", "",
                  "| backend | overlap P | overlap R | exact P | exact R | leak rate | non-PII chars masked | p50 ms |",
                  "|---|---|---|---|---|---|---|---|"]
        for key, label in BACKEND_ROWS:
            s = per["backends"][key]
            lines.append(f"| {label} | {s['overlap']['precision']:.3f} | {s['overlap']['recall']:.3f} | "
                         f"{s['exact']['precision']:.3f} | {s['exact']['recall']:.3f} | {s['leak']['rate']:.3f} | "
                         f"{s['over_masking']['non_pii_chars_masked']:.4f} | {s['latency_ms']['p50']:.1f} |")
        lines += ["", "Overlap recall / precision by type (current | current + NER | Presidio | union):", "",
                  "| type | gold | current R | +NER R | Presidio R | union R | current P | +NER P | Presidio P | union P |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        cur = per["backends"]["current"]["by_type_overlap"]
        for t in TYPES:
            if t not in cur and all(t not in per["backends"][k]["by_type_overlap"] for k in ("presidio", "union")):
                continue
            row = [per["backends"][k]["by_type_overlap"].get(t) for k in ("current", "current_ner", "presidio", "union")]
            gold = max((r["gold"] for r in row if r), default=0)
            if not gold:
                continue
            f = lambda r, k: f"{r[k]:.3f}" if r else "-"  # noqa: E731
            lines.append(f"| {t} | {gold} | " + " | ".join(f(r, "recall") for r in row) + " | "
                         + " | ".join(f(r, "precision") for r in row) + " |")
        lines.append("")
    notes = out["corpora"][next(iter(out["corpora"]))]["backends"]["presidio"]["notes"]
    lines.append(f"Presidio min score {notes.get('presidio_min_score')}; unmapped Presidio entities are ignored and "
                 "counted in the JSON output (`notes.ignored_entities`).")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=None, help="score at most N rows per corpus (every k-th)")
    ap.add_argument("--ml-train-docs", type=int, default=600)
    ap.add_argument("--data", action="append", help="dev corpus (pgw format); default: the three dev corpora")
    ap.add_argument("--presidio-min-score", type=float, default=0.4)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--markdown", type=Path)
    a = ap.parse_args(argv)
    sets: dict[str, list[Example]] = {}
    for rel in a.data or CORPORA:
        p = (ROOT / rel) if not Path(rel).is_absolute() else Path(rel)
        refuse_if_held_out(p)
        sets[Path(rel).name] = sample(load(p, "pgw")[0], a.sample)
    if a.ml_train_docs:
        sets[f"ml/train synthetic ({a.ml_train_docs} docs, seed 7)"] = ml_train_examples(a.ml_train_docs)
    backends = {k: Backend(BACKEND_ARGS[k][0], ner=BACKEND_ARGS[k][1], presidio_min_score=a.presidio_min_score)
                for k, _ in BACKEND_ROWS}
    out = {"role": "dev", "note": "dev only, not a release number", "corpora": {}}
    for name, exs in sets.items():
        print(f"== {name}: {len(exs)} documents", file=sys.stderr)
        per = {}
        for key, _ in BACKEND_ROWS:
            per[key] = summarise(evaluate(backends[key], exs))
            print(f"   {key:9s} overlap R {per[key]['overlap']['recall']:.3f} P {per[key]['overlap']['precision']:.3f}",
                  file=sys.stderr)
            if backends[key].presidio is not None:
                backends[key].presidio.ignored.clear()
                backends[key].presidio.mapped.clear()
                backends[key].presidio.below_threshold = 0
        out["corpora"][name] = {"examples": len(exs), "backends": per}
    if a.out:
        a.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    md = markdown(out)
    if a.markdown:
        a.markdown.write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
