"""A bundle built the way notebooks 02/03 build it must pass import_artifacts and load in the gateway runtime."""
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
pytest.importorskip("tokenizers")

from ml.train import card  # noqa: E402
from pgw.ml import artifact  # noqa: E402
from pgw.ml.config import parse_contract_gate, parse_detector, parse_gate  # noqa: E402
from pgw.ml.gate import OnnxSequenceClassifier  # noqa: E402
from pgw.ml.token_classifier import OnnxTokenClassifier  # noqa: E402
from tests import ml_fixtures as F  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("import_artifacts", ROOT / "scripts" / "ml" / "import_artifacts.py")
ia = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ia)


class FakeTok:
    cls_token, sep_token, pad_token = "[CLS]", "[SEP]", "[PAD]"


def notebook_bundle(d: Path, kind: str) -> Path:
    """What the notebooks write: model + tokenizer from the fixtures, labels via card writers, then card + sums."""
    d.mkdir(parents=True)
    if kind == "token_classifier":
        src, _ = F.make_token_model(d.parent / "_src_t", quantize=True)
        cfg_id2label = {str(i): lab for i, lab in enumerate(F.ID2LABEL)}          # JSON-style string keys
        doc = card.token_labels_doc(cfg_id2label, FakeTok(), 16, 4)
        meta = {"operating_tau": 0.3, "base_model": "tiny", "entity_types": ["PER", "LOC"], "recall_target": 0.95}
    else:
        src, _ = F.make_gate_model(d.parent / "_src_g", quantize=True)
        doc = card.sequence_labels_doc(dict(enumerate(F.GATE_LABELS)), FakeTok(), 16, stride=4)
        meta = {"recall_target": 0.95, "base_model": "tiny"}
    (d / "model.onnx").write_bytes((src / "model.onnx").read_bytes())
    (d / "tokenizer.json").write_text(F.tokenizer_json(), encoding="utf-8")
    card.write_labels(d / "labels.json", doc)
    card.write_train_meta(d / "train_meta.json", meta)
    sweep = {"0.5": {"recall": {"PER": 0.9, "LOC": 0.99}}, "0.3": {"recall": {"PER": 0.97, "LOC": 0.99}},
             "0.1": {"recall": {"PER": 0.99, "LOC": 1.0}}}
    metrics = {"training_data": "synthetic-only", "int8_vs_fp32": {"max_recall_drop": 0.0}, "micro_recall": 0.99,
               "labels": ["PER", "LOC"], "int8": {"recall_sweep": sweep}}
    (d / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    if kind == "content_gate":
        (d / "thresholds.json").write_text(json.dumps({"health": 0.4, "union_membership": 0.2, "disciplinary": 0.6}))
    card.write_card(d / "MODEL_CARD.md", "t", "gen", "use", "tiny", "MIT", doc.get("labels") or ["PER", "LOC"], {"x": 1})
    card.check_bundle_dir(d)
    card.write_sha256sums(d)
    return d


def test_token_bundle_roundtrip(tmp_path, capsys):
    d = notebook_bundle(tmp_path / "token_classifier", "token_classifier")
    res = ia.verify(d)
    assert res["ok"], res["problems"]
    assert res["manifest_digest"] == artifact.digest_dir(d)[0]
    assert ia.main([str(d)]) == 0
    out = capsys.readouterr().out
    assert res["manifest_digest"] in out and "ml_detector" in out
    snippet, _ = ia.token_snippet(d, res)
    assert snippet["ml_detector"]["labels"] == {"PER": "IGNORE", "LOC": "IGNORE"}      # fixture labels are not gateway types
    # the runtime accepts the snippet (fixture labels remapped to real gateway types)
    cfg = parse_detector({**snippet["ml_detector"], "path": str(d), "workers": 0, "labels": {"PER": "PERSON", "LOC": "IGNORE"}})
    assert cfg.min_score_by_type["DEFAULT"] == 0.3
    clf = OnnxTokenClassifier(artifact.load(d, res["manifest_digest"], task="token-classification"))
    text = "hello priya nair in mumbai"
    assert [(text[s.start:s.end]) for s in clf.detect(text)]


def test_gate_bundle_roundtrip(tmp_path, capsys):
    d = notebook_bundle(tmp_path / "content_gate", "content_gate")
    res = ia.verify(d)
    assert res["ok"], res["problems"]
    assert res["manifest_digest"] == artifact.digest_dir(d)[0]
    snippet, notes = ia.gate_snippet(d, res)
    assert snippet["contract.content_gate"]["threshold"] == 0.2          # minimum of the per-label thresholds
    parse_gate({**snippet["content_gate_model"], "path": str(d), "workers": 0})
    parse_contract_gate({**snippet["contract.content_gate"], "action": "PRIVATE"})
    assert any("MINIMUM" in n for n in notes)
    g = OnnxSequenceClassifier(artifact.load(d, res["manifest_digest"], task="sequence-classification"))
    scores = g.classify("hello the diagnosis cancer")
    assert set(scores) == set(F.GATE_LABELS) and scores["health"] > 0.9 and scores["disciplinary"] < 0.1
    assert ia.main([str(d), "--kind", "content_gate"]) == 0


def test_min_score_by_type_per_type_from_sweep():
    sweep = {"0.5": {"recall": {"PERSON": 0.9, "PAN": 0.99}}, "0.3": {"recall": {"PERSON": 0.94, "PAN": 0.99}},
             "0.1": {"recall": {"PERSON": 0.99, "PAN": 1.0}}}
    assert card.min_score_by_type(sweep, 0.3, ["PERSON", "PAN"]) == {"DEFAULT": 0.3, "PERSON": 0.1}


def test_labels_come_from_model_config_order():
    doc = card.token_labels_doc({"2": "I-X", "0": "O", "1": "B-X"}, FakeTok(), 512, 128)
    assert doc["id2label"] == ["O", "B-X", "I-X"] and doc["special_tokens"]["cls"] == "[CLS]"
    artifact.validate_labels(doc)
    with pytest.raises(ValueError):
        card.token_labels_doc({"0": "O", "2": "B-X"}, FakeTok(), 512, 128)


def test_old_adhoc_labels_rejected(tmp_path, capsys):
    d = notebook_bundle(tmp_path / "token_classifier", "token_classifier")
    (d / "labels.json").write_text(json.dumps({"bio_labels": ["O", "B-PER"], "entity_types": ["PER"], "max_len": 512,
                                               "stride": 128, "operating_tau": 0.3, "base_model": "x"}))
    card.write_sha256sums(d)
    assert ia.main([str(d)]) == 1
    assert "runtime artefact validation failed" in capsys.readouterr().err
    with pytest.raises(artifact.ArtifactError):
        artifact.load(d, artifact.digest_dir(d)[0])


def test_subdir_external_data_and_tamper_rejected(tmp_path):
    d = notebook_bundle(tmp_path / "token_classifier", "token_classifier")
    (d / "model.onnx_data").write_bytes(b"x")
    card.write_sha256sums(d)
    assert not ia.verify(d)["ok"]
    (d / "model.onnx_data").unlink()
    card.write_sha256sums(d)
    assert ia.verify(d)["ok"]
    (d / "sub").mkdir()
    assert not ia.verify(d)["ok"]
    (d / "sub").rmdir()
    (d / "model.onnx").write_bytes(b"tampered")
    assert any("hash mismatch" in p for p in ia.verify(d)["problems"])
