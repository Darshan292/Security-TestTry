import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from ml.train import synth

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "ml" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cc = load("contamination_check")
pk = load("package_for_kaggle")
ia = load("import_artifacts")


def write(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


def test_contamination_passes_on_independent_data(tmp_path):
    gen = synth.programmatic_records(200, seed=1)
    held = synth.programmatic_records(200, seed=2)
    # make held-out identifiers unique to it by regenerating until there is no overlap by construction (random => none)
    write(tmp_path / "gen.jsonl", gen)
    write(tmp_path / "held.jsonl", held)
    rc = cc.main(["--generated", str(tmp_path / "gen.jsonl"), "--heldout", str(tmp_path / "held.jsonl"), "--max-name-frac", "1.0",
                  "--doc-ratio", "0.9", "--max-doc-frac", "0.5"])
    assert rc == 0


def test_contamination_detects_copied_text_and_values(tmp_path, capsys):
    held = synth.programmatic_records(50, seed=2)
    write(tmp_path / "held.jsonl", held)
    write(tmp_path / "gen.jsonl", held[:20] + synth.programmatic_records(30, seed=9))
    rc = cc.main(["--generated", str(tmp_path / "gen.jsonl"), "--heldout", str(tmp_path / "held.jsonl")])
    assert rc == 1
    out = capsys.readouterr()
    assert "near-duplicates" in out.err or "identifier values" in out.err


def test_contamination_detects_single_leaked_value(tmp_path):
    held = [{"id": "h", "text": "Aadhaar number 234123412346 on file for the employee", "spans": [{"start": 15, "end": 27, "type": "AADHAAR"}]}]
    gen = [{"id": "g", "text": "completely different words here 2341 2341 2346 ok", "spans": [{"start": 32, "end": 46, "type": "AADHAAR"}]}]
    write(tmp_path / "h.jsonl", held)
    write(tmp_path / "g.jsonl", gen)
    assert cc.main(["--generated", str(tmp_path / "g.jsonl"), "--heldout", str(tmp_path / "h.jsonl")]) == 1


def test_contamination_fails_closed_without_heldout(tmp_path, monkeypatch):
    write(tmp_path / "g.jsonl", [{"id": "g", "text": "x", "spans": []}])
    monkeypatch.setattr(cc, "discover", lambda root=cc.ROOT: [])
    assert cc.main(["--generated", str(tmp_path / "g.jsonl")]) == 2
    assert cc.main(["--generated", str(tmp_path / "nope.jsonl"), "--heldout", str(tmp_path / "g.jsonl")]) == 2


def test_contamination_reads_templates_and_gz(tmp_path):
    import gzip
    held = tmp_path / "heldout.jsonl.gz"
    text = "please update the salary account for the employee who joined last week and share confirmation"
    with gzip.open(held, "wt") as f:
        f.write(json.dumps({"text": text, "spans": []}) + "\n")
    write(tmp_path / "t.jsonl", [{"id": "t", "template": "{{PERSON_1}} " + text}])
    assert cc.main(["--generated", str(tmp_path / "t.jsonl"), "--heldout", str(held)]) == 1


def test_package_contains_only_python_and_is_deterministic(tmp_path):
    a = pk.build(tmp_path / "a", "someone", "pgw-ml-train")
    b = pk.build(tmp_path / "b", "someone", "pgw-ml-train")
    assert a["zip_sha256"] == b["zip_sha256"]
    with zipfile.ZipFile(tmp_path / "a" / "ml_train.zip") as z:
        names = z.namelist()
    assert "ml/__init__.py" in names and "ml/train/synth.py" in names and "ml/train/values.py" in names
    assert all(n.endswith(".py") for n in names)
    meta = json.loads((tmp_path / "a" / "dataset-metadata.json").read_text())
    assert meta["id"] == "someone/pgw-ml-train"
    with pytest.raises(SystemExit):
        pk.build(tmp_path / "c", "Bad Owner", "x")


def make_bundle(d: Path, drop=0.004, card="Trained on synthetic data only. Not validated on real data."):
    d.mkdir()
    (d / "model.onnx").write_bytes(b"onnx-bytes")
    (d / "labels.json").write_text(json.dumps({"format": "pgw-ml-labels/1", "task": "token-classification",
                                               "id2label": ["O", "B-PERSON", "I-PERSON"], "max_length": 512, "stride": 128}))
    (d / "train_meta.json").write_text(json.dumps({"operating_tau": 0.3}))
    (d / "metrics.json").write_text(json.dumps({"training_data": "synthetic-only", "int8_vs_fp32": {"max_recall_drop": drop}}))
    (d / "MODEL_CARD.md").write_text(card)
    (d / "tokenizer.json").write_text("{}")
    (d / "sha256sums").write_text("".join(f"{hashlib.sha256((d / n).read_bytes()).hexdigest()}  {n}\n"
                                          for n in ("model.onnx", "labels.json", "metrics.json", "MODEL_CARD.md", "tokenizer.json",
                                                                 "train_meta.json")))


def test_import_artifacts_verifies_and_detects_tampering(tmp_path, capsys):
    d = tmp_path / "ok"
    make_bundle(d)
    assert ia.main([str(d)]) == 0
    out = capsys.readouterr().out
    assert hashlib.sha256(b"onnx-bytes").hexdigest() in out
    from pgw.ml import artifact
    assert f"PIN (profile sha256 = runtime manifest digest): {artifact.digest_dir(d)[0]}" in out
    (d / "model.onnx").write_bytes(b"tampered")
    assert ia.main([str(d)]) == 1
    bad = tmp_path / "drop"
    make_bundle(bad, drop=0.02)
    assert ia.main([str(bad)]) == 1
    nocard = tmp_path / "card"
    make_bundle(nocard, card="a model")
    assert ia.main([str(nocard)]) == 1
    extra = tmp_path / "extra"
    make_bundle(extra)
    (extra / "evil.bin").write_bytes(b"x")
    assert ia.main([str(extra)]) == 1
