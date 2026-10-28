"""eval/ml: span scoring (exact/overlap, per type and genre), leak rate, over-masking, dataset adapters, release-gate
judging with human-set (null) thresholds, and the dev/release protocol guard. Synthetic data only."""
import json
from pathlib import Path

import pytest

from eval.ml import run as R
from eval.ml.datasets import DatasetError, load
from eval.ml.metrics import Scorer, leaked


def test_scoring_exact_overlap_leak_and_over_masking():
    text = "Mail priya.nair@acme.in about Rahul Sharma, ticket PAY-12"
    gold = [(5, 23, "EMAIL"), (30, 42, "PERSON")]
    benign = [(51, 57)]
    pred = [(5, 23, "EMAIL"), (30, 35, "PERSON"), (51, 57, "IDENTIFIER")]
    prot = "Mail [EMAIL] about [PERSON] Sharma, ticket [IDENTIFIER]"
    sc = Scorer()
    sc.add(text, gold, pred, "hr", prot, benign, 12.0)
    r = sc.result()
    assert r["exact"]["by_type"]["EMAIL"]["f1"] == 1.0 and r["exact"]["by_type"]["PERSON"]["recall"] == 0.0
    assert r["overlap"]["by_type"]["PERSON"]["recall"] == 1.0
    assert r["overlap"]["overall"]["precision"] == round(2 / 3, 4)
    assert r["overlap"]["by_genre"]["hr"]["gold"] == 2
    assert r["leak_rate"]["overall"] == {"rate": 0.0, "leaked": 0, "values": 2}   # "Rahul Sharma" not whole
    assert r["over_masking"]["benign_spans_masked"] == 1.0 and r["latency_ms"]["p50"] == 12.0
    assert leaked("2345 6789 0124", "id 234567890124 here") and not leaked("Rahul", "R. S.")


def test_adapters(tmp_path):
    bio = tmp_path / "bio.jsonl"
    bio.write_text(json.dumps({"id": "a", "tokens": ["Hi", "Priya", "Nair", "!"], "ner_tags": [0, 1, 2, 0]}) + "\n")
    ex, _ = load(bio, "bio", label_names=["O", "B-PER", "I-PER"], type_map={"PER": "PERSON"})
    assert ex[0].text == "Hi Priya Nair !" and ex[0].spans == [(3, 13, "PERSON")]
    hum = tmp_path / "h.jsonl"
    rows = [{"id": "1", "genre": "email", "text": "Dear Asha", "spans": [{"start": 5, "end": 9, "type": "PERSON"}],
             "benign": [{"start": 0, "end": 4}], "labeller": "L1", "reviewer": "L2", "status": "agreed"},
            {"id": "2", "genre": "chat", "text": "x", "spans": [], "labeller": "L1", "status": "disputed"}]
    hum.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    ex, stats = load(hum, "human")
    assert len(ex) == 1 and stats == {"rows": 2, "agreed": 1, "disputed": 1} and ex[0].benign == [(0, 4)]
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"id": "1", "text": "ab", "spans": [{"start": 0, "end": 9, "type": "X"}]}) + "\n")
    with pytest.raises(DatasetError):
        load(bad, "pgw")


def test_gate_thresholds_are_never_invented():
    r = {"examples": 10, "leak_rate": {"overall": {"rate": 0.0}, "by_type": {}}, "overlap": {"by_type": {}},
         "over_masking": {"benign_spans_masked": None}, "latency_ms": {"p95": 5.0}}
    shipped = R.load_gate(R.ROOT / "release_gate.yaml")
    assert all(v in (None, {}) for v in shipped["thresholds"].values())
    assert R.judge(r, shipped)["verdict"] == "UNDECIDED"
    set_ = {"thresholds": {"max_leak_rate": 0.0, "max_latency_p95_ms": 1.0, "min_examples": 5}}
    assert R.judge(r, set_)["verdict"] == "FAIL"


def test_dev_role_refuses_held_out_and_release_sets(tmp_path, capsys):
    for name in ("test.jsonl", "test3.jsonl", "ner_test2.jsonl", "dev3val.jsonl"):
        p = tmp_path / name
        p.write_text("")
        assert R.main(["--role", "dev", "--data", str(p)]) == 2
    rel = tmp_path / "real_sample.jsonl"
    rel.write_text("")
    gate = tmp_path / "gate.yaml"
    gate.write_text(f"thresholds: {{}}\nrelease_sets: [{rel}]\n")
    assert R.main(["--role", "dev", "--data", str(rel), "--gate", str(gate)]) == 2


def test_end_to_end_with_a_tiny_model(tmp_path, monkeypatch):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx")
    from tests import ml_fixtures as F
    d, sha = F.make_token_model(tmp_path / "m")
    model = tmp_path / "model.json"
    model.write_text(json.dumps(F.detector_profile(d, sha)))
    data = tmp_path / "dev.jsonl"
    data.write_text(json.dumps({"id": "1", "genre": "chat", "text": "pls ask priya nair today",
                                "spans": [{"start": 8, "end": 18, "type": "PERSON"}]}) + "\n")
    out = tmp_path / "r.json"
    for backend in ("ml_only", "ml_checksum", "pipeline_ml", "current"):
        assert R.main(["--role", "dev", "--data", str(data), "--backend", backend, "--model", str(model),
                       "--out", str(out)]) == 0
        r = json.loads(out.read_text())
        if backend != "current":
            assert r["leak_rate"]["overall"]["rate"] == 0.0 and r["overlap"]["by_type"]["PERSON"]["recall"] == 1.0
    monkeypatch.setattr(R, "ROOT", tmp_path)               # release runs are logged (not in the repo here)
    assert R.main(["--role", "release", "--data", str(data), "--backend", "ml_only", "--model", str(model),
                   "--gate", str(Path(__file__).resolve().parents[1] / "eval/ml/release_gate.yaml")]) == 0
    log = [json.loads(x) for x in (tmp_path / "results/release_runs.jsonl").read_text().splitlines()]
    assert log[-1]["verdict"] == "UNDECIDED" and log[-1]["backend"]["model_sha256"] == sha
