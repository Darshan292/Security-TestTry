#!/usr/bin/env python3
"""Verify a model bundle downloaded from Kaggle/Colab and print the hashes to pin in the signed profile.

A bundle directory (from notebook 02 or 03) contains: model.onnx (int8), labels.json, metrics.json, MODEL_CARD.md,
tokenizer files, and `sha256sums` (lines "<hex>  <filename>"). This script:
  1. checks every file listed in sha256sums exists and matches, and that no unlisted model/tokenizer file is present;
  2. checks the required files exist, the model card states synthetic-only training, and the int8 gate recorded in
     metrics.json passed (int8 recall drop <= 1 point);
  3. prints `model_sha256` (hash of model.onnx) and `bundle_sha256` (hash over the sorted "name:sha256" lines) for pinning.
     The runtime package (pgw/ml) pins the model by the sha256 of its ONNX file; if its profile uses another field name,
     use model_sha256 for the model file and bundle_sha256 for the whole directory.
  4. with --dest, copies the verified bundle (read-only afterwards is the caller's job).

Usage: python scripts/ml/import_artifacts.py DIR [--kind token_classifier|content_gate] [--dest models/ner_v1]
Exit codes: 0 verified, 1 verification failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REQUIRED = {"token_classifier": ("model.onnx", "labels.json", "metrics.json", "MODEL_CARD.md", "sha256sums"),
            "content_gate": ("model.onnx", "labels.json", "metrics.json", "MODEL_CARD.md", "thresholds.json", "sha256sums")}
MAX_INT8_RECALL_DROP = 0.01


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sums(p: Path) -> dict[str, str]:
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        name = name.strip().lstrip("*")
        if len(digest) != 64 or not name or "/" in name or name.startswith("."):
            raise ValueError(f"malformed sha256sums line: {line!r}")
        out[name] = digest.lower()
    return out


def verify(d: Path, kind: str | None = None) -> dict:
    problems: list[str] = []
    if not d.is_dir():
        raise SystemExit(f"not a directory: {d}")
    sums_file = d / "sha256sums"
    if not sums_file.exists():
        return {"ok": False, "problems": ["sha256sums missing"]}
    try:
        sums = parse_sums(sums_file)
    except ValueError as e:
        return {"ok": False, "problems": [str(e)]}
    for name, digest in sorted(sums.items()):
        f = d / name
        if not f.is_file():
            problems.append(f"listed but missing: {name}")
        elif sha256_file(f) != digest:
            problems.append(f"hash mismatch: {name}")
    for f in sorted(d.iterdir()):
        if f.is_file() and f.name != "sha256sums" and f.name not in sums:
            problems.append(f"present but not in sha256sums: {f.name}")
    if kind is None:
        kind = "content_gate" if (d / "thresholds.json").exists() else "token_classifier"
    for name in REQUIRED[kind]:
        if not (d / name).exists():
            problems.append(f"required file missing: {name}")
    metrics = {}
    if (d / "metrics.json").exists():
        metrics = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
        drop = metrics.get("int8_vs_fp32", {}).get("max_recall_drop")
        if drop is None:
            problems.append("metrics.json has no int8_vs_fp32.max_recall_drop")
        elif drop > MAX_INT8_RECALL_DROP + 1e-9:
            problems.append(f"int8 recall drop {drop} exceeds {MAX_INT8_RECALL_DROP}")
        if metrics.get("training_data") != "synthetic-only":
            problems.append("metrics.json training_data must be 'synthetic-only'")
    if (d / "MODEL_CARD.md").exists():
        card = (d / "MODEL_CARD.md").read_text(encoding="utf-8").lower()
        if "synthetic" not in card or "not validated on real" not in card:
            problems.append("MODEL_CARD.md must state synthetic-only training and that it is not validated on real data")
    bundle = hashlib.sha256("".join(f"{n}:{h}\n" for n, h in sorted(sums.items())).encode()).hexdigest()
    return {"ok": not problems, "problems": problems, "kind": kind, "model_sha256": sums.get("model.onnx"),
            "bundle_sha256": bundle, "files": sorted(sums), "metrics_summary": {k: metrics.get(k) for k in ("micro_recall", "macro_recall", "labels") if k in metrics}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--kind", choices=sorted(REQUIRED), default=None)
    ap.add_argument("--dest", default=None)
    a = ap.parse_args(argv)
    res = verify(Path(a.dir), a.kind)
    if not res["ok"]:
        print("VERIFICATION FAILED", file=sys.stderr)
        for p in res["problems"]:
            print("  - " + p, file=sys.stderr)
        return 1
    print(f"VERIFIED {res['kind']} bundle: {len(res['files'])} files")
    print(f"model_sha256:  {res['model_sha256']}")
    print(f"bundle_sha256: {res['bundle_sha256']}")
    if a.dest:
        dest = Path(a.dest)
        if dest.exists():
            print(f"destination exists: {dest}", file=sys.stderr)
            return 2
        shutil.copytree(a.dir, dest)
        print(f"copied to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
