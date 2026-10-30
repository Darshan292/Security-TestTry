#!/usr/bin/env python3
"""Build a bench bundle (external_detector kind) from a third-party token-classification model that is already ONNX,
without torch: model.onnx (single file, weights inline: see repack_onnx.py), tokenizer.json, the model's Hugging Face
config.json (id2label) and, for BIOES models with constrained decoding, viterbi_calibration.json.

    python scripts/ml/build_external_bundle.py OUT_DIR --model model.onnx --tokenizer tokenizer.json --config config.json \\
        --viterbi viterbi_calibration.json --repo openai/privacy-filter --revision <sha> --licence apache-2.0 \\
        --table privacy_filter [--max-length 512 --stride 64 --aggregation token --precision int8]

Writes labels.json (format pgw-ml-labels/1), MODEL_CARD.md, metrics.json, train_meta.json (reviewed label map from
ml/train/detectors.py; unknown labels -> IGNORE and listed in the card) and sha256sums, then verifies the result with
scripts/ml/import_artifacts.py and prints the runtime manifest digest. The bundle is for scripts/ml/bench_detectors.py.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ml.train import card, detectors  # noqa: E402

TABLES = {"privacy_filter": detectors.PRIVACY_FILTER_MAP, "indicner": detectors.INDICNER_MAP}
SPAN_TABLES = {"gliner_pii": detectors.GLINER_PII_PROMPTS}       # prompt label -> gateway type (zero-shot span model)
ALLOWED = ("apache-2.0", "mit", "bsd-3-clause")


def build(out: Path, *, model: Path, tokenizer: Path, config: Path, viterbi: Path | None, repo: str, revision: str,
          licence: str, table: str, max_length: int, stride: int, aggregation: str, precision: str,
          export_note: str, threshold: float = 0.5, specials: dict | None = None) -> dict:
    if licence.lower() not in ALLOWED:
        raise SystemExit(f"licence {licence!r} is not on the allowlist {ALLOWED}")
    if out.exists():
        raise SystemExit(f"{out} exists")
    if table in SPAN_TABLES:
        return _build_span(out, model=model, tokenizer=tokenizer, repo=repo, revision=revision, licence=licence, table=table,
                           max_length=max_length, precision=precision, export_note=export_note, threshold=threshold)
    cfg = json.loads(config.read_text(encoding="utf-8"))
    id2label = [cfg["id2label"][str(i)] for i in range(len(cfg["id2label"]))]
    kinds = {x[:2] for x in id2label if x != "O"}
    scheme = "BIOES" if kinds == {"B-", "I-", "E-", "S-"} else "BILOU" if kinds == {"B-", "I-", "L-", "U-"} else "BIO"
    labels = {"format": "pgw-ml-labels/1", "task": "token-classification", "id2label": id2label, "scheme": scheme,
              "max_length": max_length, "stride": stride, "aggregation": aggregation}
    if specials:
        labels["special_tokens"] = specials
    if viterbi is not None:
        if scheme != "BIOES":
            raise SystemExit("viterbi calibration needs a BIOES model")
        op = json.loads(viterbi.read_text(encoding="utf-8"))["operating_points"]["default"]["biases"]
        labels["viterbi"] = {"biases": {k: float(v) for k, v in op.items()}}
    label_map, unknown = detectors.map_token_labels(id2label, TABLES[table])
    out.mkdir(parents=True)
    shutil.copyfile(model, out / "model.onnx")
    shutil.copyfile(tokenizer, out / "tokenizer.json")
    (out / "labels.json").write_text(json.dumps(labels, indent=1) + "\n", encoding="utf-8")
    metrics = {"training_data": "third-party pretrained (not ours); no evaluation recorded in this bundle", "licence": licence,
               "shipped_precision": precision}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1) + "\n", encoding="utf-8")
    detectors.write_card(out / "MODEL_CARD.md", title=repo, repo=repo, revision=revision, licence=licence, allowed=ALLOWED,
                         export=export_note, mapping=label_map, unknown=unknown, metrics=metrics)
    card.write_train_meta(out / "train_meta.json", detectors.bench_meta(label_map, "token", threshold))
    card.write_sha256sums(out)
    return {"label_map": label_map, "scheme": scheme, "unknown": unknown}


def _build_span(out, *, model, tokenizer, repo, revision, licence, table, max_length, precision, export_note, threshold):
    prompts = detectors.span_prompt_map(SPAN_TABLES[table])
    labels = {"format": "pgw-ml-labels/1", "task": "span-classification", "labels": list(prompts), "max_width": 12,
              "max_words": 128, "max_length": max_length, "ent_token": "<<ENT>>", "sep_token": "<<SEP>>",
              "threshold": threshold, "special_tokens": {"cls": "[CLS]", "sep": "[SEP]", "pad": "[PAD]"}}
    out.mkdir(parents=True)
    shutil.copyfile(model, out / "model.onnx")
    shutil.copyfile(tokenizer, out / "tokenizer.json")
    (out / "labels.json").write_text(json.dumps(labels, indent=1) + "\n", encoding="utf-8")
    metrics = {"training_data": "third-party pretrained (not ours); no evaluation recorded in this bundle", "licence": licence,
               "shipped_precision": precision}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1) + "\n", encoding="utf-8")
    detectors.write_card(out / "MODEL_CARD.md", title=repo, repo=repo, revision=revision, licence=licence, allowed=ALLOWED,
                         export=export_note, mapping=prompts, unknown=[], metrics=metrics)
    card.write_train_meta(out / "train_meta.json", detectors.bench_meta(prompts, "span", threshold))
    card.write_sha256sums(out)
    return {"label_map": prompts, "scheme": "span", "unknown": []}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    for k in ("model", "tokenizer"):
        ap.add_argument(f"--{k}", type=Path, required=True)
    ap.add_argument("--config", type=Path, help="HF config.json (token-classification only)")
    ap.add_argument("--viterbi", type=Path)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--revision", default="unknown")
    ap.add_argument("--licence", required=True)
    ap.add_argument("--table", choices=sorted({**TABLES, **SPAN_TABLES}), required=True)
    ap.add_argument("--max-length", type=int, default=512)
    ap.add_argument("--stride", type=int, default=64)
    ap.add_argument("--aggregation", choices=("first", "token"), default="token")
    ap.add_argument("--precision", choices=("fp32", "int8", "q4"), default="int8")
    ap.add_argument("--bert-specials", action="store_true", help="token model: add [CLS]/[SEP] around each window")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--export-note", default="Official ONNX from the upstream repository, repacked to one file with "
                                             "scripts/ml/repack_onnx.py (weights inline, pinned by sha256).")
    a = ap.parse_args(argv)
    info = build(a.out, model=a.model, tokenizer=a.tokenizer, config=a.config, viterbi=a.viterbi, repo=a.repo,
                 revision=a.revision, licence=a.licence, table=a.table, max_length=a.max_length, stride=a.stride,
                 aggregation=a.aggregation, precision=a.precision, export_note=a.export_note, threshold=a.threshold,
                 specials={"cls": "[CLS]", "sep": "[SEP]", "pad": "[PAD]"} if a.bert_specials else None)
    print(json.dumps(info, indent=1))
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location("import_artifacts", ROOT / "scripts" / "ml" / "import_artifacts.py")
    ia = module_from_spec(spec)
    spec.loader.exec_module(ia)
    return ia.main([str(a.out)])


if __name__ == "__main__":
    sys.exit(main())
