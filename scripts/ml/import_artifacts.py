#!/usr/bin/env python3
"""Verify a model bundle downloaded from Kaggle/Colab and print the hashes to pin in the signed profile.

A bundle directory (from notebook 02 or 03) is a flat directory of regular files: model.onnx (int8, weights inline),
tokenizer.json, labels.json (format "pgw-ml-labels/1"), MODEL_CARD.md, metrics.json, train_meta.json, thresholds.json
(gate only) and `sha256sums` (lines "<hex>  <filename>"). This script:
  1. checks every file listed in sha256sums exists and matches, and that no unlisted file is present;
  2. checks the required files exist, the model card states synthetic-only training, and the int8 gate recorded in
     metrics.json passed (int8 recall drop <= 1 point);
  3. runs the gateway's own artefact validation (pgw.ml.artifact: flat directory, no symlinks/subdirectories, required
     files, labels.json format and task, no external ONNX data) and prints the runtime MANIFEST DIGEST: that is THE value
     to pin as `sha256` in the signed profile. `model_sha256` / `bundle_sha256` are informational only;
  4. prints a ready-to-paste profile snippet (ml_detector with labels map and min_score_by_type, or content_gate_model
     plus the contract's content_gate with the single recommended threshold);
  5. with --dest, copies the verified bundle (read-only afterwards is the caller's job).

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

REQUIRED = {"token_classifier": ("model.onnx", "tokenizer.json", "labels.json", "metrics.json", "train_meta.json", "MODEL_CARD.md",
                                 "sha256sums"),
            "content_gate": ("model.onnx", "tokenizer.json", "labels.json", "metrics.json", "train_meta.json", "MODEL_CARD.md",
                             "thresholds.json", "sha256sums")}
# external_detector: a third-party open detector exported by notebook 04 (token- or span-classification); not trained by
# us, so the "synthetic-only training" checks are replaced by a licence + not-validated-on-real check.
REQUIRED["external_detector"] = ("model.onnx", "tokenizer.json", "labels.json", "metrics.json", "train_meta.json", "MODEL_CARD.md",
                                 "sha256sums")
TASK_FOR_KIND = {"token_classifier": "token-classification", "content_gate": "sequence-classification"}
ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_PATH = "models/<name>"
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


def _pgw():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pgw.ml import artifact, config
    return artifact, config


def runtime_check(d: Path, kind: str, problems: list[str]) -> dict:
    """Validate the bundle with the gateway's own loader code; returns {manifest_digest, labels_doc} or appends problems."""
    try:
        artifact, _ = _pgw()
    except ImportError as e:
        problems.append(f"cannot import pgw.ml.artifact (run from the repository): {e}")
        return {}
    try:
        digest, _hashes = artifact.digest_dir(d)
        doc = artifact.validate_labels(json.loads((d / "labels.json").read_text(encoding="utf-8")))
    except (artifact.ArtifactError, ValueError, OSError) as e:
        problems.append(f"runtime artefact validation failed: {e}")
        return {}
    if kind == "external_detector":
        if doc["task"] not in ("token-classification", "span-classification"):
            problems.append(f"labels.json task {doc['task']!r} is not a detector task")
    elif doc["task"] != TASK_FOR_KIND[kind]:
        problems.append(f"labels.json task is {doc['task']!r}; a {kind} needs {TASK_FOR_KIND[kind]!r}")
    extra = [p.name for p in d.iterdir() if p.name.endswith((".onnx_data", ".onnx.data")) or (p.suffix == ".onnx" and p.name != "model.onnx")]
    if extra:
        problems.append(f"extra model files (external ONNX data or a second model) are not supported: {extra}")
    return {"manifest_digest": digest, "labels_doc": doc}


def _load_json(d: Path, name: str) -> dict:
    return json.loads((d / name).read_text(encoding="utf-8"))


def token_snippet(d: Path, res: dict) -> tuple[dict, list[str]]:
    """ml_detector profile entry. Labels map model entity label -> gateway type (identical names; IGNORE if the gateway has
    no such type). min_score_by_type: per type from the dev recall sweep (card.min_score_by_type), DEFAULT = operating tau."""
    artifact, config = _pgw()
    from ml.train import card
    notes: list[str] = []
    ents = artifact.entity_labels(res["labels_doc"])
    labels = {}
    for e in ents:
        if e in config.ML_TYPES:
            labels[e] = e
        else:
            labels[e] = config.IGNORE
            notes.append(f"label {e!r} is not a gateway type: mapped to IGNORE (edit if it should map to one)")
    tau = _load_json(d, "train_meta.json").get("operating_tau")
    if not isinstance(tau, (int, float)):
        raise ValueError("train_meta.json has no operating_tau")
    sweep = (_load_json(d, "metrics.json").get("int8") or {}).get("recall_sweep")
    if sweep:
        mins = card.min_score_by_type(sweep, tau, [t for t in labels.values() if t != config.IGNORE])
    else:
        mins = {"DEFAULT": float(tau)}
        notes.append("no recall sweep in metrics.json: min_score_by_type uses the global operating tau only")
    snippet = {"ml_detector": {"path": PLACEHOLDER_PATH, "sha256": res["manifest_digest"], "backend": "token", "labels": labels,
                               "min_score_by_type": mins}}
    return snippet, notes


def external_snippet(d: Path, res: dict) -> tuple[dict, list[str]]:
    """ml_detector entry for an external detector: label map from train_meta.json (reviewed table), DEFAULT score = the
    labels.json threshold (span) or 0.5 (token). Written for BENCHMARKING; the notes say so."""
    _artifact, config = _pgw()
    meta = _load_json(d, "train_meta.json")
    lm = meta.get("label_map")
    if not isinstance(lm, dict) or not lm:
        raise ValueError("train_meta.json has no label_map")
    backend = meta.get("backend") or ("span" if res["labels_doc"]["task"] == "span-classification" else "token")
    thr = meta.get("operating_threshold") or 0.5
    snippet = {"ml_detector": {"path": PLACEHOLDER_PATH, "sha256": res["manifest_digest"], "backend": backend, "labels": lm,
                               "min_score_by_type": {"DEFAULT": float(thr)}}}
    notes = ["external third-party detector: for scripts/ml/bench_detectors.py (dev measurement) only; adopting it into a "
             "profile is a separate, reviewed decision",
             "labels mapped to IGNORE: " + (", ".join(k for k, v in lm.items() if v == config.IGNORE) or "none")]
    return snippet, notes


def gate_snippet(d: Path, res: dict) -> tuple[dict, list[str]]:
    """content_gate_model profile entry + the contract's content_gate. The contract field has ONE threshold for all of its
    labels, so the recommendation is the MINIMUM of the per-label thresholds (most conservative: recall-preserving)."""
    _pgw()
    from ml.train import card
    labels = list(res["labels_doc"]["labels"])
    thr = _load_json(d, "thresholds.json")
    notes: list[str] = []
    if set(thr) != set(labels):
        raise ValueError("thresholds.json labels differ from labels.json labels")
    positive = [lab for lab in labels if lab != "NONE"]
    if "NONE" in labels:
        notes.append("label NONE is the 'nothing found' class: leave it out of the contract's content_gate.labels")
    one = card.contract_gate_threshold({lab: thr[lab] for lab in positive})
    notes.append("The runtime contract supports ONE threshold per content_gate; the recommended value is the MINIMUM of the "
                 f"per-label thresholds ({one:.4f}), the most conservative choice (every label keeps recall >= its recall target "
                 "on the dev split; more false positives on labels whose own threshold is higher). Per-label thresholds: "
                 + json.dumps({k: round(float(v), 4) for k, v in thr.items()}))
    snippet = {"content_gate_model": {"path": PLACEHOLDER_PATH, "sha256": res["manifest_digest"], "labels": labels},
               "contract.content_gate": {"labels": positive, "threshold": round(one, 4), "action": "PRIVATE  # or REFUSE"}}
    return snippet, notes


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
        if kind == "token_classifier" and (d / "train_meta.json").exists() and \
                json.loads((d / "train_meta.json").read_text(encoding="utf-8")).get("external"):
            kind = "external_detector"
    for name in REQUIRED[kind]:
        if not (d / name).exists():
            problems.append(f"required file missing: {name}")
    metrics = {}
    if (d / "metrics.json").exists():
        metrics = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
        drop = metrics.get("int8_vs_fp32", {}).get("max_recall_drop")
        if kind == "external_detector":
            if metrics.get("shipped_precision") == "int8" and drop is not None and drop > MAX_INT8_RECALL_DROP + 1e-9:
                problems.append(f"int8 recall drop {drop} exceeds {MAX_INT8_RECALL_DROP}")
            if not str(metrics.get("training_data", "")).startswith("third-party"):
                problems.append("metrics.json training_data must start with 'third-party' for an external detector")
            if not metrics.get("licence"):
                problems.append("metrics.json has no licence")
        elif drop is None:
            problems.append("metrics.json has no int8_vs_fp32.max_recall_drop")
        elif drop > MAX_INT8_RECALL_DROP + 1e-9:
            problems.append(f"int8 recall drop {drop} exceeds {MAX_INT8_RECALL_DROP}")
        if kind != "external_detector" and metrics.get("training_data") != "synthetic-only":
            problems.append("metrics.json training_data must be 'synthetic-only'")
    if (d / "MODEL_CARD.md").exists():
        card = (d / "MODEL_CARD.md").read_text(encoding="utf-8").lower()
        if kind == "external_detector":
            if "licence" not in card or "not validated on real" not in card:
                problems.append("MODEL_CARD.md must state the licence and that it is not validated on real data")
        elif "synthetic" not in card or "not validated on real" not in card:
            problems.append("MODEL_CARD.md must state synthetic-only training and that it is not validated on real data")
    runtime = {}
    if not problems:
        runtime = runtime_check(d, kind, problems)
    bundle = hashlib.sha256("".join(f"{n}:{h}\n" for n, h in sorted(sums.items())).encode()).hexdigest()
    return {"ok": not problems, "problems": problems, "kind": kind, "model_sha256": sums.get("model.onnx"),
            "bundle_sha256": bundle, "files": sorted(sums), **runtime, "metrics_summary": {k: metrics.get(k) for k in ("micro_recall", "macro_recall", "labels") if k in metrics}}


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
    print(f"PIN (profile sha256 = runtime manifest digest): {res['manifest_digest']}")
    print(f"model_sha256 (informational):  {res['model_sha256']}")
    print(f"bundle_sha256 (informational): {res['bundle_sha256']}")
    try:
        snippet, notes = {"token_classifier": token_snippet, "content_gate": gate_snippet,
                          "external_detector": external_snippet}[res["kind"]](Path(a.dir), res)
    except (ValueError, KeyError, OSError) as e:
        print(f"cannot build the profile snippet: {e}", file=sys.stderr)
        return 1
    print("\nProfile snippet (set path; add workers/queue_max/timeout_s/... as needed):")
    print(json.dumps(snippet, indent=2))
    for n in notes:
        print("note: " + n)
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
