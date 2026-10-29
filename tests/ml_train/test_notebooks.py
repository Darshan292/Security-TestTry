import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_notebooks", ROOT / "scripts" / "ml" / "build_notebooks.py")
bn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bn)
NBS = sorted((ROOT / "notebooks" / "kaggle").glob("*/*.ipynb"))


def test_notebooks_exist_and_are_current():
    assert [p.stem for p in NBS] == ["01_generate_templates", "02_train_token_classifier", "03_train_content_gate",
                                     "04_export_detectors"]
    assert bn.main(["--check"]) == 0


@pytest.mark.parametrize("nb", NBS, ids=lambda p: p.stem)
def test_every_code_cell_compiles(nb):
    data = json.loads(nb.read_text(encoding="utf-8"))
    code = [c for c in data["cells"] if c["cell_type"] == "code"]
    assert len(code) >= 8
    for i, c in enumerate(code):
        compile("".join(c["source"]), f"{nb.stem}:cell{i}", "exec")


@pytest.mark.parametrize("nb", NBS, ids=lambda p: p.stem)
def test_valid_nbformat(nb):
    nbformat = pytest.importorskip("nbformat")
    nbformat.validate(nbformat.read(str(nb), as_version=4))


@pytest.mark.parametrize("nb", NBS, ids=lambda p: p.stem)
def test_notebook_safety_and_structure(nb):
    src = "\n".join("".join(c["source"]) for c in json.loads(nb.read_text())["cells"])
    for must in ("ON_KAGGLE", "ON_COLAB", "drive.mount", "ml_train.zip"):
        assert must in src
    for banned in ("ai4privacy", "piiranha", "heldout", "eval/data", "TODO", "FIXME", "git clone"):
        assert banned not in src, banned
    assert "licences.check_model" in src
    meta = json.loads((nb.parent / "kernel-metadata.json").read_text())
    assert meta["code_file"] == nb.name and meta["kernel_type"] == "notebook" and meta["enable_gpu"] == "true"
    assert any(d.endswith("/pgw-ml-train") for d in meta["dataset_sources"])


def test_training_notebooks_enforce_gates():
    for name in ("02_train_token_classifier", "03_train_content_gate"):
        src = (ROOT / "notebooks" / "kaggle" / name / f"{name}.ipynb").read_text()
        assert "assert drop <= 0.01" in src and "quantize_dynamic" in src and "per_channel=True" in src and "sha256sums" in src
    nb2 = (ROOT / "notebooks" / "kaggle" / "02_train_token_classifier" / "02_train_token_classifier.ipynb").read_text()
    assert "split_templates" in nb2 and "EarlyStoppingCallback" in nb2 and "recall_sweep" in nb2 and "seqeval" in nb2


def test_export_notebook_records_failures_and_checks_licences():
    from ml.train import detectors, licences
    src = (ROOT / "notebooks" / "kaggle" / "04_export_detectors" / "04_export_detectors.ipynb").read_text()
    for must in ("licences.check_model", "ALLOWED_DETECTORS", "Unsupported", "unsupported", "licence_refused", "quantize_dynamic",
                 "sha256sums", "span_agreement", "OnnxSpanModel", "OnnxTokenClassifier", "detectors.CANDIDATES"):
        assert must in src, must
    repos = {c["repo"] for c in detectors.CANDIDATES.values() if c["repo"]}
    assert repos == {"openai/privacy-filter", "ai4bharat/IndicNER", "urchade/gliner_multi_pii-v1"}
    assert repos <= set(licences.ALLOWED_DETECTORS)        # every candidate has an expected licence in the allowlist table
    assert all(v in licences.ALLOWED_LICENCES for v in licences.ALLOWED_DETECTORS.values())
    assert "urchade/gliner_multi\"" not in " ".join(licences.ALLOWED_DETECTORS)       # the CC-BY-NC v0 is not a candidate
