"""BIOES decoding and constrained Viterbi, external-data ONNX (refused by the runtime, repacked by scripts/ml/repack_onnx.py).
Tiny generated models only (tests/ml_fixtures.py); nothing is downloaded."""
import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("onnxruntime")
pytest.importorskip("onnx")
pytest.importorskip("tokenizers")
import numpy as np  # noqa: E402
import onnx  # noqa: E402

from pgw.ml import artifact  # noqa: E402
from pgw.ml.artifact import VITERBI_BIASES, ArtifactError  # noqa: E402
from pgw.ml.decode import decode, split_tag, viterbi_labels  # noqa: E402
from pgw.ml.onnx_util import reject_external_data  # noqa: E402
from pgw.ml.token_classifier import OnnxTokenClassifier  # noqa: E402
from tests import ml_fixtures as F  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("repack_onnx", ROOT / "scripts" / "ml" / "repack_onnx.py")
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)

BIOES = ["O", "B-PER", "E-PER", "S-PER", "I-PER"]       # same graph as ID2LABEL: priya/rahul=B, nair/sharma=E, mumbai=S
ZERO = dict.fromkeys(VITERBI_BIASES, 0.0)
TEXT = "Hello Priya Nair met Rahul Sharma in Mumbai."


def found(text, spans):
    return [(text[s.start:s.end], s.type) for s in spans]


# --- decoding ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("tags,want", [
    (["O", "B-PER", "I-PER", "E-PER", "O"], [(1, 3, "PER")]),
    (["S-PER", "S-PER"], [(0, 0, "PER"), (1, 1, "PER")]),
    (["B-PER", "E-PER", "S-LOC", "B-LOC", "E-LOC"], [(0, 1, "PER"), (2, 2, "LOC"), (3, 4, "LOC")]),
    (["E-PER", "O"], [(0, 0, "PER")]),                         # lenient: a stray E is never dropped
    (["B-PER", "O"], [(0, 0, "PER")]),                         # lenient: an unfinished B is never dropped
    (["B-PER", "E-LOC"], [(0, 0, "PER"), (1, 1, "LOC")]),
])
def test_decode_bioes(tags, want):
    assert decode(tags) == want


def _valid(tags):
    """BIOES grammar: B/I continue into I/E of the same type; everything else starts clean."""
    prev = "O-"
    for lab in tags:
        p, t = split_tag(lab)
        ok_prev = prev[0] in ("B", "I")
        if ok_prev and (p not in ("I", "E") or t != prev[2:]):
            return False
        if not ok_prev and p in ("I", "E"):
            return False
        prev = lab
    return split_tag(tags[-1])[0] not in ("B", "I")


def test_viterbi_equals_argmax_when_argmax_is_valid():
    lp = np.log(np.full((4, 5), 0.01))
    for t, k in enumerate((0, 1, 2, 0)):          # O B E O
        lp[t, k] = np.log(0.96)
    assert viterbi_labels(lp, BIOES, ZERO) == [0, 1, 2, 0]


def test_viterbi_repairs_an_invalid_argmax():
    lp = np.log(np.full((3, 5), 0.02))
    for t, k in enumerate((1, 0, 2)):             # argmax: B O E  (invalid)
        lp[t, k] = np.log(0.92)
    out = [BIOES[i] for i in viterbi_labels(lp, BIOES, ZERO)]
    assert out != ["B-PER", "O", "E-PER"] and _valid(out)


def test_viterbi_always_valid_on_random_logits():
    rng = np.random.default_rng(3)
    for T in (1, 2, 5, 17):
        lp = np.log(rng.dirichlet(np.ones(5), size=T))
        assert _valid([BIOES[i] for i in viterbi_labels(lp, BIOES, ZERO)])


def test_viterbi_biases_move_the_operating_point():
    lp = np.log(np.full((3, 5), 0.2))
    lp[:, 0] = np.log(0.4)                         # O slightly ahead everywhere
    base = viterbi_labels(lp, BIOES, ZERO)
    assert set(base) == {0}
    more = dict(ZERO, transition_bias_background_to_start=3.0, transition_bias_inside_to_end=3.0)
    assert any(k != 0 for k in viterbi_labels(lp, BIOES, more))          # recall-leaning: opens a span
    stay = dict(ZERO, transition_bias_background_stay=3.0)
    assert set(viterbi_labels(lp, BIOES, stay)) == {0}


def test_viterbi_window_edges():
    lp = np.log(np.full((2, 5), 0.01))
    lp[0, 2] = lp[1, 2] = np.log(0.96)             # E E: only legal in the middle of a text
    mid = viterbi_labels(lp, BIOES, ZERO, first=False, last=False)
    assert mid[0] == 2                              # a window that does not start the text may start inside a span
    start = viterbi_labels(lp, BIOES, ZERO, first=True, last=True)
    assert start[0] != 2                            # at the text start a span cannot begin with E


# --- labels.json -------------------------------------------------------------------------------------------------

def _labels(**kw):
    d = {"format": "pgw-ml-labels/1", "task": "token-classification", "id2label": BIOES, "scheme": "BIOES",
         "max_length": 16, "stride": 4}
    d.update(kw)
    return d


def test_labels_bioes_and_viterbi_validation():
    artifact.validate_labels(_labels())
    artifact.validate_labels(_labels(viterbi={"biases": ZERO}))
    assert artifact.entity_labels(_labels()) == ["PER"]
    for bad in (_labels(scheme="BIO", viterbi={"biases": ZERO}),
                _labels(viterbi={"biases": {k: 0 for k in list(ZERO)[:5]}}),
                _labels(viterbi={"biases": dict(ZERO, extra=1)}),
                _labels(viterbi={"biases": dict(ZERO, transition_bias_background_stay=99)}),
                _labels(viterbi={"biases": dict(ZERO, transition_bias_background_stay=True)}),
                _labels(viterbi={"biases": ZERO, "x": 1}),
                _labels(viterbi=ZERO),
                _labels(id2label=["O", "B-PER", "E-PER", "S-PER"], viterbi={"biases": ZERO}),     # no I-PER
                _labels(id2label=["O", "B-PER", "E-PER", "S-PER", "U-PER"], viterbi={"biases": ZERO}),
                _labels(scheme="BIOX")):
        with pytest.raises(ArtifactError):
            artifact.validate_labels(copy.deepcopy(bad))


# --- the token classifier with BIOES -------------------------------------------------------------------------------

def _clf(d, **kw):
    return OnnxTokenClassifier(artifact.load(*F.make_token_model(d, scheme="BIOES", id2label=BIOES, **kw)))


def test_bioes_argmax_and_viterbi_agree_on_clean_text(tmp_path):
    a = _clf(tmp_path / "a")
    b = OnnxTokenClassifier(artifact.load(*_viterbi_model(tmp_path / "b")))
    want = [("Priya Nair", "PER"), ("Rahul Sharma", "PER"), ("Mumbai", "PER")]
    assert found(TEXT, a.detect(TEXT)) == want
    assert found(TEXT, b.detect(TEXT)) == want
    assert b.viterbi is not None and a.viterbi is None


def _viterbi_model(d, biases=ZERO):
    model = F.token_model(n_labels=len(BIOES))
    labels = {"format": "pgw-ml-labels/1", "task": "token-classification", "id2label": BIOES, "scheme": "BIOES",
              "max_length": 16, "stride": 4, "aggregation": "first", "viterbi": {"biases": biases},
              "special_tokens": {"cls": "[CLS]", "sep": "[SEP]", "pad": "[PAD]"}}
    return F._write(d, model, labels)


def test_viterbi_over_many_windows_matches_a_short_text(tmp_path):
    m = OnnxTokenClassifier(artifact.load(*_viterbi_model(tmp_path / "v")))
    filler = "the team meeting today and " * 40
    long = filler + TEXT + " " + filler + TEXT
    assert found(long, m.detect(long)) == found(TEXT, m.detect(TEXT)) * 2


# --- external data -----------------------------------------------------------------------------------------------

def _external_model(d: Path, name="model.onnx", data="model.onnx_data"):
    d.mkdir(parents=True, exist_ok=True)
    onnx.save_model(F.token_model(), str(d / name), save_as_external_data=True, all_tensors_to_one_file=True,
                    location=data, size_threshold=0)
    return d


def test_reject_external_data_detects_references_and_passes_inline():
    inline = F.token_model().SerializeToString()
    reject_external_data(inline)
    with pytest.raises(ArtifactError, match="not a valid"):
        reject_external_data(b"\xff\xff\xff")
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        d = _external_model(Path(t) / "x")
        with pytest.raises(ArtifactError, match="external data"):
            reject_external_data((d / "model.onnx").read_bytes())


def test_runtime_refuses_external_data_even_when_the_file_is_in_the_cwd(tmp_path, monkeypatch):
    """onnxruntime resolves external data of a model built from bytes against the cwd: that file is not in the manifest
    digest, so the runtime must refuse the model before ORT sees it."""
    ext = _external_model(tmp_path / "ext")
    d, _ = F.make_token_model(tmp_path / "bundle")
    (d / "model.onnx").write_bytes((ext / "model.onnx").read_bytes())
    (d / "model.onnx_data").write_bytes((ext / "model.onnx_data").read_bytes())
    digest = artifact.digest_dir(d)[0]
    art = artifact.load(d, digest)                      # the digest covers both files, the artefact format allows it
    monkeypatch.chdir(ext)                              # ...but a data file next to the process would be used by ORT
    with pytest.raises(ArtifactError, match="external data"):
        OnnxTokenClassifier(art)


def test_repack_roundtrip_and_digest_property(tmp_path):
    ext = _external_model(tmp_path / "ext")
    packed = rp.repack(ext, "model.onnx")
    m = onnx.load_from_string(packed)
    assert all(t.data_location != onnx.TensorProto.EXTERNAL for t in m.graph.initializer)
    assert rp.parity(ext, "model.onnx", packed) < 1e-6
    d, _ = F.make_token_model(tmp_path / "bundle")
    (d / "model.onnx").write_bytes(packed)
    clf = OnnxTokenClassifier(artifact.load(d, artifact.digest_dir(d)[0]))
    assert found(TEXT, clf.detect(TEXT))[0] == ("Priya Nair", "PER")
    # a swapped model.onnx on disk after pinning no longer loads
    pinned = artifact.digest_dir(d)[0]
    (d / "model.onnx").write_bytes(packed + b"\x00")
    with pytest.raises(ArtifactError, match="does not match"):
        artifact.load(d, pinned)


def test_repack_pins_every_file_and_refuses_tampering(tmp_path):
    import hashlib
    ext = _external_model(tmp_path / "ext")
    h = {n: hashlib.sha256((ext / n).read_bytes()).hexdigest() for n in ("model.onnx", "model.onnx_data")}
    assert rp.repack(ext, "model.onnx", h)
    with pytest.raises(rp.RepackError, match="not match"):
        rp.repack(ext, "model.onnx", dict(h, **{"model.onnx_data": "0" * 64}))
    with pytest.raises(rp.RepackError, match="not pinned"):
        rp.repack(ext, "model.onnx", {"model.onnx": h["model.onnx"]})
    with pytest.raises(rp.RepackError, match="does not reference"):
        rp.repack(ext, "model.onnx", dict(h, other=h["model.onnx"]))


def test_repack_refuses_paths_symlinks_and_bad_ranges(tmp_path):
    ext = _external_model(tmp_path / "ext")
    with pytest.raises(rp.RepackError, match="plain file name"):
        rp.repack(ext, "../ext/model.onnx")
    # traversal in the location field
    m = onnx.load(str(ext / "model.onnx"), load_external_data=False)
    for t in m.graph.initializer:
        for e in t.external_data:
            if e.key == "location":
                e.value = "../secret.bin"
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "model.onnx").write_bytes(m.SerializeToString())
    (tmp_path / "secret.bin").write_bytes(b"x" * 4096)
    with pytest.raises(rp.RepackError, match="plain file name"):
        rp.repack(bad, "model.onnx")
    # symlinked data file
    link = tmp_path / "link"
    link.mkdir()
    (link / "model.onnx").write_bytes((ext / "model.onnx").read_bytes())
    os.symlink(ext / "model.onnx_data", link / "model.onnx_data")
    with pytest.raises(rp.RepackError, match="symlink"):
        rp.repack(link, "model.onnx")
    # data file too short for the declared range
    short = tmp_path / "short"
    short.mkdir()
    (short / "model.onnx").write_bytes((ext / "model.onnx").read_bytes())
    (short / "model.onnx_data").write_bytes(b"\x00" * 8)
    with pytest.raises(rp.RepackError, match="outside"):
        rp.repack(short, "model.onnx")


def test_repack_refuses_over_the_protobuf_limit(tmp_path, monkeypatch):
    ext = _external_model(tmp_path / "ext")
    monkeypatch.setattr(rp, "LIMIT", 100)
    with pytest.raises(rp.RepackError, match="2 GiB"):
        rp.repack(ext, "model.onnx")


def test_repack_cli(tmp_path):
    ext = _external_model(tmp_path / "ext")
    out = tmp_path / "out.onnx"
    assert rp.main([str(ext), "model.onnx", str(out), "--parity"]) == 0
    assert out.exists() and rp.main([str(ext), "model.onnx", str(out)]) == 2      # never overwrites


def test_build_external_bundle_for_a_bioes_model(tmp_path):
    """The privacy-filter path end to end on a tiny graph: repack -> bundle -> import_artifacts verify -> runtime."""
    bb = importlib.util.module_from_spec(s := importlib.util.spec_from_file_location(
        "build_external_bundle", ROOT / "scripts" / "ml" / "build_external_bundle.py"))
    s.loader.exec_module(bb)
    ia_spec = importlib.util.spec_from_file_location("import_artifacts", ROOT / "scripts" / "ml" / "import_artifacts.py")
    ia = importlib.util.module_from_spec(ia_spec)
    ia_spec.loader.exec_module(ia)
    ext = _external_model(tmp_path / "ext")
    (tmp_path / "model.onnx").write_bytes(rp.repack(ext, "model.onnx"))
    (tmp_path / "tokenizer.json").write_text(F.tokenizer_json(), encoding="utf-8")
    names = ["O"] + [f"{p}-{t}" for t in ("private_person", "account_number") for p in "BIES"]
    (tmp_path / "config.json").write_text(json.dumps({"id2label": {str(i): n for i, n in enumerate(names)}}))
    (tmp_path / "vit.json").write_text(json.dumps({"operating_points": {"default": {"biases": ZERO}}}))
    info = bb.build(tmp_path / "bundle", model=tmp_path / "model.onnx", tokenizer=tmp_path / "tokenizer.json",
                    config=tmp_path / "config.json", viterbi=tmp_path / "vit.json", repo="x/y", revision="r", licence="apache-2.0",
                    table="privacy_filter", max_length=16, stride=4, aggregation="token", precision="int8",
                    export_note="test")
    assert info["scheme"] == "BIOES" and info["label_map"] == {"private_person": "PERSON", "account_number": "BANK_ACCOUNT"}
    res = ia.verify(tmp_path / "bundle")
    assert res["ok"], res["problems"]
    assert res["labels_doc"]["viterbi"]["biases"] == ZERO
    with pytest.raises(SystemExit):
        bb.build(tmp_path / "bad", model=tmp_path / "model.onnx", tokenizer=tmp_path / "tokenizer.json",
                 config=tmp_path / "config.json", viterbi=None, repo="x/y", revision="r", licence="cc-by-nc-4.0",
                 table="privacy_filter", max_length=16, stride=4, aggregation="token", precision="int8", export_note="t")
