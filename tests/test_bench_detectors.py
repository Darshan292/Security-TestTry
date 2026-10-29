"""scripts/ml/bench_detectors.py and the external_detector kind of import_artifacts, on tiny generated ONNX bundles."""
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
pytest.importorskip("tokenizers")
from ml.train import card, detectors  # noqa: E402

from tests import ml_fixtures as F  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "ml" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bench, ia = _load("bench_detectors"), _load("import_artifacts")


def external_bundle(d: Path, drop=0.0) -> Path:
    F.make_token_model(d, max_length=32, stride=8)
    detectors.write_card(d / "MODEL_CARD.md", title="t", repo="x/y", revision="r1", licence="mit", allowed=["mit"], export="e",
                         mapping={"PER": "PERSON", "LOC": "IGNORE"}, unknown=[], metrics={})
    card.write_train_meta(d / "train_meta.json", detectors.bench_meta({"PER": "PERSON", "LOC": "IGNORE"}, "token", 0.5))
    (d / "metrics.json").write_text(json.dumps({"training_data": "third-party pretrained (not ours); synthetic-dev evaluation only",
                                                "licence": "mit", "shipped_precision": "int8",
                                                "int8_vs_fp32": {"max_recall_drop": drop}}))
    card.write_sha256sums(d)
    return d


def dev_file(tmp_path: Path) -> Path:
    rows = [{"id": "1", "genre": "g", "text": "met priya nair in mumbai today", "spans": [{"start": 4, "end": 14, "type": "PERSON"}]},
            {"id": "2", "genre": "g", "text": "email rahul sharma at the team", "spans": [{"start": 6, "end": 18, "type": "PERSON"}]}]
    p = tmp_path / "dev_small.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_external_bundle_verifies_and_gives_a_snippet(tmp_path):
    d = external_bundle(tmp_path / "b")
    res = ia.verify(d)
    assert res["ok"], res["problems"]
    assert res["kind"] == "external_detector"
    snippet, notes = ia.external_snippet(d, res)
    assert snippet["ml_detector"]["labels"] == {"PER": "PERSON", "LOC": "IGNORE"} and snippet["ml_detector"]["backend"] == "token"
    assert any("IGNORE" in n or "LOC" in n for n in notes)


def test_external_bundle_rejects_bad_int8_and_missing_licence(tmp_path):
    d = external_bundle(tmp_path / "b", drop=0.05)
    assert not ia.verify(d)["ok"]
    d2 = external_bundle(tmp_path / "c")
    m = json.loads((d2 / "metrics.json").read_text())
    m["licence"] = ""
    (d2 / "metrics.json").write_text(json.dumps(m))
    card.write_sha256sums(d2)
    assert any("licence" in p for p in ia.verify(d2)["problems"])


def test_bench_runs_through_the_runtime_slot_and_prints_a_table(tmp_path, capsys):
    d = external_bundle(tmp_path / "b")
    out = tmp_path / "r.json"
    rc = bench.main(["--bundle", f"tiny={d}", "--data", str(dev_file(tmp_path)), "--out", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "| PERSON |" in text and "tiny R" in text and "dev only" in text
    res = json.loads(out.read_text())
    tiny = res["corpora"]["dev_small.jsonl"]["tiny"]
    assert tiny["overlap"]["by_type"]["PERSON"]["recall"] == 1.0
    assert res["note"] == "dev only, not a release number"


@pytest.mark.parametrize("name", ["heldout_2026.jsonl", "eval/results/heldout_x.json", "test3.jsonl", "test2.jsonl", "ner_test2.jsonl",
                                  "eval/data3/dev3val.jsonl", "tester_set.jsonl", "sealed_v3.tar.enc", "a/held_out/x.jsonl"])
def test_bench_refuses_held_out_paths_without_opening_them(tmp_path, name):
    d = external_bundle(tmp_path / "b")
    missing = tmp_path / name                         # never created: a refusal must not even try to open it
    with pytest.raises(SystemExit) as e:
        bench.main(["--bundle", f"tiny={d}", "--data", str(missing)])
    assert "refused" in str(e.value)
    assert not missing.exists()


def test_bench_refuses_unverifiable_bundle(tmp_path):
    d = external_bundle(tmp_path / "b")
    (d / "labels.json").write_text((d / "labels.json").read_text() + " ")        # hash mismatch
    with pytest.raises(SystemExit) as e:
        bench.main(["--bundle", f"tiny={d}", "--data", str(dev_file(tmp_path))])
    assert "verification failed" in str(e.value)


def test_default_corpora_are_dev_only():
    from eval.ml.run import is_held_out
    assert not any(is_held_out(ROOT / p) for p in bench.DEFAULT_CORPORA)
