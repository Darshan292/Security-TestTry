"""Model artefacts: one directory per model, pinned by a sha256 manifest digest in the signed deployment profile.

Layout (flat directory, regular files only; no subdirectories, no symlinks):

    model.onnx      ONNX graph (fp32 or int8), weights inline (external-data references are refused at load;
                    repack with scripts/ml/repack_onnx.py)
    tokenizer.json  Hugging Face `tokenizers` tokenizer
    labels.json     label set and decoding parameters (format below)
    MODEL_CARD.md   provenance: base model and licence, training data, export command, evaluation results
    (any further regular file is covered by the digest too)

Manifest digest = sha256 over the canonical JSON {"format": "pgw-ml-artifact/1", "files": {name: sha256(file)}}.
Print it with ``python -m pgw.ml.artifact <dir>`` and pin it as `sha256` in the profile. A different digest, a
missing file, a symlink or a subdirectory stops startup. The bytes that are hashed are the bytes that are loaded
(the model is built from the in-memory copy), so a file swapped on disk after the check is never used.

labels.json (format "pgw-ml-labels/1"), by task:

    {"format": "pgw-ml-labels/1", "task": "token-classification",
     "id2label": ["O", "B-PER", "I-PER", ...], "scheme": "BIO" | "BILOU" | "BIOES" | "IO",
     "max_length": 256, "stride": 64, "aggregation": "first" | "token",
     "special_tokens": {"cls": "[CLS]", "sep": "[SEP]", "pad": "[PAD]"},          (special_tokens optional)
     "viterbi": {"biases": {<six transition biases, below>}}}                     (optional; scheme BIOES only)

    viterbi: constrained Viterbi decoding over log-probabilities instead of a per-token argmax (the decoding that
    openai/privacy-filter documents). Only the allowed BIOES transitions are possible (O->O|B|S, B|I->I|E,
    E|S->O|B|S, same entity type inside a span); each of the six biases (transition_bias_background_stay,
    _background_to_start, _end_to_background, _end_to_start, _inside_to_continue, _inside_to_end; floats) is added
    to the score of the matching transition. All zero = the plain maximum-likelihood valid path.

    {"format": "pgw-ml-labels/1", "task": "span-classification",                   (GLiNER-style)
     "labels": ["person", "email", ...], "max_width": 12, "max_words": 256, "max_length": 512,
     "ent_token": "<<ENT>>", "sep_token": "<<SEP>>", "threshold": 0.5,
     "special_tokens": {"cls": "[CLS]", "sep": "[SEP]", "pad": "[PAD]"}}

    {"format": "pgw-ml-labels/1", "task": "sequence-classification",
     "labels": ["health", "disciplinary", ...], "problem_type": "multi_label" | "single_label",
     "max_length": 256, "stride": 64, "special_tokens": {...}}
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

ARTIFACT_FORMAT = "pgw-ml-artifact/1"
LABELS_FORMAT = "pgw-ml-labels/1"
REQUIRED = ("model.onnx", "tokenizer.json", "labels.json", "MODEL_CARD.md")
MAX_FILES = 32
MAX_TOTAL_BYTES = 2 * 1024 ** 3          # ONNX protobuf limit; larger models need external data (not supported).
#   A model published with external data files is repacked into one model.onnx first (scripts/ml/repack_onnx.py), so
#   the single file is covered by the manifest digest and loaded from the verified bytes.
SCHEMES = ("BIO", "BILOU", "BIOES", "IO")
VITERBI_BIASES = ("transition_bias_background_stay", "transition_bias_background_to_start",
                  "transition_bias_end_to_background", "transition_bias_end_to_start",
                  "transition_bias_inside_to_continue", "transition_bias_inside_to_end")
TASKS = ("token-classification", "span-classification", "sequence-classification")
_PREFIXES = ("B-", "I-", "L-", "U-", "E-", "S-")


class ArtifactError(ValueError):
    """The model directory does not match its pin or is malformed. Startup stops."""


@dataclass(frozen=True)
class Artifact:
    path: str
    digest: str
    labels: dict
    files: dict = field(repr=False, compare=False)    # name -> bytes (the verified bytes; load from these)

    @property
    def task(self) -> str:
        return self.labels["task"]


def manifest_digest(file_hashes: dict[str, str]) -> str:
    doc = json.dumps({"format": ARTIFACT_FORMAT, "files": dict(sorted(file_hashes.items()))},
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(doc.encode("utf-8")).hexdigest()


def _read_dir(path: str | Path) -> dict[str, bytes]:
    d = Path(path)
    try:
        if d.is_symlink() or not d.is_dir():
            raise ArtifactError(f"model artefact {d.name!r} is not a directory")
        entries = sorted(os.scandir(d), key=lambda e: e.name)
    except OSError as e:
        raise ArtifactError(f"model artefact unreadable: {type(e).__name__}") from None
    if len(entries) > MAX_FILES:
        raise ArtifactError("model artefact has too many files")
    out: dict[str, bytes] = {}
    total = 0
    for e in entries:
        st = os.lstat(e.path)
        if not stat.S_ISREG(st.st_mode):
            raise ArtifactError(f"model artefact: {e.name!r} is not a regular file (no symlinks or subdirectories)")
        total += st.st_size
        if total > MAX_TOTAL_BYTES:
            raise ArtifactError("model artefact is larger than 2 GiB")
        with open(e.path, "rb") as fh:
            out[e.name] = fh.read()
    missing = [n for n in REQUIRED if n not in out]
    if missing:
        raise ArtifactError(f"model artefact is missing {missing}")
    if not out["MODEL_CARD.md"].strip():
        raise ArtifactError("model artefact: MODEL_CARD.md is empty")
    return out


def digest_dir(path: str | Path) -> tuple[str, dict[str, str]]:
    files = _read_dir(path)
    hashes = {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
    return manifest_digest(hashes), hashes


def _int(doc: dict, key: str, lo: int, hi: int, default=None) -> int:
    v = doc.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ArtifactError(f"labels.json: {key} must be an integer in [{lo}, {hi}]")
    return v


def _str_list(doc: dict, key: str) -> list[str]:
    v = doc.get(key)
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v) or len(set(v)) != len(v):
        raise ArtifactError(f"labels.json: {key} must be a list of distinct non-empty strings")
    return v


def _specials(doc: dict) -> None:
    sp = doc.get("special_tokens")
    if sp is None:
        return
    if not isinstance(sp, dict) or set(sp) - {"cls", "sep", "pad"} or not all(
            isinstance(v, str) and v for v in sp.values()):
        raise ArtifactError("labels.json: special_tokens must map cls/sep/pad to token strings")


def _viterbi(doc: dict) -> None:
    v = doc.get("viterbi")
    if v is None:
        return
    if doc.get("scheme") != "BIOES":
        raise ArtifactError("labels.json: viterbi decoding needs scheme BIOES")
    b = v.get("biases") if isinstance(v, dict) and set(v) == {"biases"} else None
    if not isinstance(b, dict) or set(b) != set(VITERBI_BIASES) or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and -20 <= x <= 20 for x in b.values()):
        raise ArtifactError("labels.json: viterbi must be {\"biases\": {six transition biases, floats in [-20, 20]}}")
    ents = {lab[2:] for lab in doc["id2label"] if lab != "O"}
    for lab in doc["id2label"]:
        if lab != "O" and (lab[:2] not in ("B-", "I-", "E-", "S-") or len(lab) < 3):
            raise ArtifactError("labels.json: viterbi needs B-/I-/E-/S- labels and O only")
    for e in ents:
        if not all(f"{p}-{e}" in doc["id2label"] for p in "BIES"):
            raise ArtifactError(f"labels.json: viterbi needs B-, I-, E- and S- for every entity ({e!r})")


def validate_labels(doc) -> dict:
    if not isinstance(doc, dict) or doc.get("format") != LABELS_FORMAT or doc.get("task") not in TASKS:
        raise ArtifactError(f"labels.json must declare format {LABELS_FORMAT!r} and a task in {TASKS}")
    task = doc["task"]
    _specials(doc)
    if task == "token-classification":
        id2label = _str_list(doc, "id2label")
        if "O" not in id2label:
            raise ArtifactError("labels.json: id2label needs an 'O' label")
        if doc.get("scheme", "BIO") not in SCHEMES:
            raise ArtifactError(f"labels.json: scheme must be one of {SCHEMES}")
        _viterbi(doc)
        if doc.get("aggregation", "first") not in ("first", "token"):
            raise ArtifactError("labels.json: aggregation must be first or token")
        ml = _int(doc, "max_length", 8, 8192, 512)
        _int(doc, "stride", 0, ml // 2, min(128, ml // 4))
    elif task == "span-classification":
        _str_list(doc, "labels")
        _int(doc, "max_width", 1, 64, 12)
        _int(doc, "max_words", 8, 4096, 256)
        _int(doc, "max_length", 16, 8192, 512)
        for k in ("ent_token", "sep_token"):
            if not isinstance(doc.get(k, "x"), str):
                raise ArtifactError(f"labels.json: {k} must be a string")
        th = doc.get("threshold", 0.5)
        if not isinstance(th, (int, float)) or isinstance(th, bool) or not 0 < th < 1:
            raise ArtifactError("labels.json: threshold must be in (0, 1)")
    else:
        _str_list(doc, "labels")
        if doc.get("problem_type", "multi_label") not in ("multi_label", "single_label"):
            raise ArtifactError("labels.json: problem_type must be multi_label or single_label")
        ml = _int(doc, "max_length", 8, 8192, 512)
        _int(doc, "stride", 0, ml // 2, min(128, ml // 4))
    return doc


def entity_labels(doc: dict) -> list[str]:
    """The labels a model can emit: entity types (BIO prefixes stripped, 'O' dropped) or class labels."""
    if doc["task"] != "token-classification":
        return list(doc["labels"])
    out: dict[str, None] = {}
    for lab in doc["id2label"]:
        if lab == "O":
            continue
        out[lab[2:] if lab[:2] in _PREFIXES else lab] = None
    return list(out)


def load(path: str | Path, expected_sha256: str, task: str | None = None) -> Artifact:
    """Read the directory once, verify its manifest digest against the pin, validate labels.json."""
    files = _read_dir(path)
    hashes = {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
    digest = manifest_digest(hashes)
    if not isinstance(expected_sha256, str) or not hmac.compare_digest(digest, expected_sha256):
        raise ArtifactError(f"model artefact {Path(path).name!r} does not match the sha256 pinned in the signed "
                            "deployment profile (tampered, wrong version or incomplete copy)")
    try:
        doc = json.loads(files["labels.json"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ArtifactError("labels.json is not valid JSON") from None
    doc = validate_labels(doc)
    if task is not None and doc["task"] != task:
        raise ArtifactError(f"model artefact is a {doc['task']} model, expected {task}")
    return Artifact(str(path), digest, doc, files)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m pgw.ml.artifact <model directory>", file=sys.stderr)
        return 2
    try:
        digest, hashes = digest_dir(args[0])
        doc = validate_labels(json.loads(Path(args[0], "labels.json").read_text(encoding="utf-8")))
    except (ArtifactError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"sha256": digest, "task": doc["task"], "labels": entity_labels(doc), "files": hashes},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
