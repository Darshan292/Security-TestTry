"""pgw.ml: decoding, offsets, windows, int8, artefact integrity pin, config validation, worker pool (timeouts, queue,
crash, start failure: fail closed), GLiNER-style span model, content gate, and the pipeline integration (precedence,
heuristics switch, no change without a model). Tiny models are generated at test time (tests/ml_fixtures.py)."""
import json
import os
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("onnxruntime")
pytest.importorskip("onnx")
pytest.importorskip("tokenizers")

from pgw.ml import artifact  # noqa: E402
from pgw.ml.artifact import ArtifactError  # noqa: E402
from pgw.ml.base import MlUnavailable, Span  # noqa: E402
from pgw.ml.config import MlConfigError, parse_contract_gate, parse_detector, parse_gate  # noqa: E402
from pgw.ml.decode import decode  # noqa: E402
from pgw.ml.gate import OnnxSequenceClassifier, triggered  # noqa: E402
from pgw.ml.onnx_util import windows  # noqa: E402
from pgw.ml.pool import DetectorPool, GatePool  # noqa: E402
from pgw.ml.runtime import MlEntityStage, build_detector, open_detector, open_gate  # noqa: E402
from pgw.ml.span_model import OnnxSpanModel  # noqa: E402
from pgw.ml.token_classifier import OnnxTokenClassifier  # noqa: E402
from pgw.pipeline import Keys, Protector  # noqa: E402
from pgw.policy import PolicyPack  # noqa: E402
from pgw.types import Context, Detection, EntityType  # noqa: E402
from tests import ml_fixtures as F  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEXT = "Hello Priya Nair 🙂 met Rahul Sharma in Mumbai. Raghunathan said hi."


@pytest.fixture(scope="module")
def tok_model(tmp_path_factory):
    return F.make_token_model(tmp_path_factory.mktemp("m") / "tok")


@pytest.fixture(scope="module")
def clf(tok_model):
    return OnnxTokenClassifier(artifact.load(*tok_model))


def found(text, spans):
    return [(text[s.start:s.end], s.type) for s in spans]


# --- decoding ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("tags,want", [
    (["O", "B-PER", "I-PER", "O"], [(1, 2, "PER")]),
    (["B-PER", "B-PER"], [(0, 0, "PER"), (1, 1, "PER")]),
    (["I-PER", "I-PER", "O", "I-LOC"], [(0, 1, "PER"), (3, 3, "LOC")]),          # lenient: I without B starts one
    (["B-PER", "I-LOC"], [(0, 0, "PER"), (1, 1, "LOC")]),                        # type change splits
    (["U-PER", "B-LOC", "I-LOC", "L-LOC", "I-LOC"], [(0, 0, "PER"), (1, 3, "LOC"), (4, 4, "LOC")]),   # BILOU
    (["PER", "PER", "O", "LOC"], [(0, 1, "PER"), (3, 3, "LOC")]),                # IO
    ([], []), (["O", "O"], []),
])
def test_decode(tags, want):
    assert decode(tags) == want


def test_windows_cover_and_overlap():
    assert windows(0, 8, 2) == [(0, 0)] and windows(5, 8, 2) == [(0, 5)]
    ws = windows(30, 8, 3)
    assert ws[0] == (0, 8) and ws[-1][1] == 30
    assert all(b - a <= 8 for a, b in ws) and all(ws[i][1] - ws[i + 1][0] == 3 for i in range(len(ws) - 1))


# --- token classifier ----------------------------------------------------------------------------------------------

def test_spans_and_character_offsets(clf):
    spans = clf.detect(TEXT)
    assert found(TEXT, spans) == [("Priya Nair", "PER"), ("Rahul Sharma", "PER"), ("Mumbai", "LOC"),
                                  ("Raghunathan", "PER")]        # sub-word "rag ##hu ##nathan": first-token label
    assert all(0 < s.score <= 1 for s in spans)


def test_offsets_survive_surrogates_accents_and_case(clf):
    t = "bad \ud800 PRÍYA NAIR wrote"                  # lone surrogate, accent, upper case: offsets in the input
    assert found(t, clf.detect(t)) == [("PRÍYA NAIR", "PER")]


def test_sliding_windows_give_the_same_spans_as_a_short_text(clf):
    filler = "the team meeting today and " * 40
    long = filler + TEXT + " " + filler + TEXT
    got = found(long, clf.detect(long))
    assert got == found(TEXT, clf.detect(TEXT)) * 2
    assert clf.max_length == 16                        # many windows were needed


def test_batching_and_dedup_match_single_calls(clf):
    texts = [TEXT, "", "   ", "no names here", TEXT, "rahul sharma"]
    assert clf.detect_many(texts) == [clf.detect(t) for t in texts]


def test_token_aggregation_and_no_special_tokens(tmp_path):
    a = OnnxTokenClassifier(artifact.load(*F.make_token_model(tmp_path / "a", aggregation="token")))
    assert found("Raghunathan", a.detect("Raghunathan")) == [("Rag", "PER")]   # sub-tokens decoded on their own
    b = OnnxTokenClassifier(artifact.load(*F.make_token_model(tmp_path / "b", specials=False)))
    assert found(TEXT, b.detect(TEXT))[:2] == [("Priya Nair", "PER"), ("Rahul Sharma", "PER")]


def test_bilou_labels(tmp_path):
    lab = ["O", "U-PER", "L-PER", "B-LOC", "I-LOC"]       # same graph, BILOU names: "priya"=U, "nair"=L
    m = OnnxTokenClassifier(artifact.load(*F.make_token_model(tmp_path / "bilou", scheme="BILOU", id2label=lab)))
    assert found("priya nair", m.detect("priya nair")) == [("priya", "PER"), ("nair", "PER")]


def test_int8_model(tmp_path, clf):
    q = OnnxTokenClassifier(artifact.load(*F.make_token_model(tmp_path / "q", quantize=True)))
    assert found(TEXT, q.detect(TEXT)) == found(TEXT, clf.detect(TEXT))
    ops = {n.op_type for n in __import__("onnx").load_from_string(
        (tmp_path / "q" / "model.onnx").read_bytes()).graph.node}
    assert ops & {"DynamicQuantizeLinear", "MatMulInteger", "DequantizeLinear"}


def test_random_model_invariants(tmp_path):
    m = OnnxTokenClassifier(artifact.load(*F.make_token_model(tmp_path / "r", random_only=True)))
    t = ("Rahul met priya at the meeting, salary 5 lakh. " * 9) + "😀 ünïcode"
    for s in m.detect(t):
        assert 0 <= s.start < s.end <= len(t) and s.type in ("PER", "LOC") and 0 <= s.score <= 1
        assert t[s.start:s.end] == t[s.start:s.end].strip()


def test_threads_are_configurable(tok_model):
    m = OnnxTokenClassifier(artifact.load(*tok_model), intra_op_threads=3, inter_op_threads=2)
    so = m.session.get_session_options()
    assert so.intra_op_num_threads == 3 and so.inter_op_num_threads == 2
    assert m.session.get_providers() == ["CPUExecutionProvider"]


def test_non_str_and_inference_failure_fail_closed(clf, monkeypatch):
    with pytest.raises(MlUnavailable):
        clf.detect_many([123])  # type: ignore[list-item]
    monkeypatch.setattr(clf.session, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(MlUnavailable, match="RuntimeError"):
        clf.detect(TEXT)


# --- artefact integrity ------------------------------------------------------------------------------------------

def test_pin_mismatch_and_tampering_stop_loading(tmp_path):
    d, sha = F.make_token_model(tmp_path / "t")
    artifact.load(d, sha)
    with pytest.raises(ArtifactError, match="does not match the sha256"):
        artifact.load(d, "0" * 64)
    raw = bytearray((d / "model.onnx").read_bytes())
    raw[-1] ^= 1
    (d / "model.onnx").write_bytes(bytes(raw))
    with pytest.raises(ArtifactError, match="does not match"):
        artifact.load(d, sha)


@pytest.mark.parametrize("mutate", ["missing_card", "extra_file", "subdir", "symlink", "empty_card"])
def test_artifact_layout_rules(tmp_path, mutate):
    d, sha = F.make_token_model(tmp_path / "t")
    if mutate == "missing_card":
        (d / "MODEL_CARD.md").unlink()
        with pytest.raises(ArtifactError, match="missing"):
            artifact.load(d, sha)
    elif mutate == "extra_file":                       # covered by the digest: an added file changes it
        (d / "notes.txt").write_text("x")
        with pytest.raises(ArtifactError, match="does not match"):
            artifact.load(d, sha)
        artifact.load(d, artifact.digest_dir(d)[0])
    elif mutate == "subdir":
        (d / "sub").mkdir()
        with pytest.raises(ArtifactError, match="regular file"):
            artifact.load(d, sha)
    elif mutate == "symlink":
        os.symlink(d / "labels.json", d / "link.json")
        with pytest.raises(ArtifactError, match="regular file"):
            artifact.load(d, sha)
    else:
        (d / "MODEL_CARD.md").write_text("  \n")
        with pytest.raises(ArtifactError, match="empty"):
            artifact.load(d, artifact.digest_dir(d)[0])


def test_bad_labels_json_and_graph_mismatch(tmp_path):
    d, _ = F.make_token_model(tmp_path / "t")
    (d / "labels.json").write_text(json.dumps({"format": "x"}))
    with pytest.raises(ArtifactError, match="labels.json"):
        artifact.load(d, artifact.digest_dir(d)[0])
    d3, _ = F.make_token_model(tmp_path / "t3")          # graph emits 5 labels; labels.json now lists 6
    doc = json.loads((d3 / "labels.json").read_text())
    doc["id2label"] = doc["id2label"] + ["B-X"]
    (d3 / "labels.json").write_text(json.dumps(doc))
    with pytest.raises(ArtifactError, match="emits 5 labels"):
        OnnxTokenClassifier(artifact.load(d3, artifact.digest_dir(d3)[0]))
    with pytest.raises(ArtifactError, match="sequence-classification"):
        artifact.load(*F.make_token_model(tmp_path / "t4"), task="sequence-classification")


def test_cli_prints_the_digest(tmp_path, capsys):
    d, sha = F.make_token_model(tmp_path / "t")
    assert artifact.main([str(d)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sha256"] == sha and out["labels"] == ["PER", "LOC"] and out["task"] == "token-classification"
    assert artifact.main([str(tmp_path / "nope")]) == 1


# --- configuration -----------------------------------------------------------------------------------------------

def test_detector_config_validation(tmp_path):
    ok = F.detector_profile(tmp_path, "a" * 64)
    assert parse_detector(ok).threshold("PERSON") == 0.5
    for bad in ({"sha256": "A" * 64}, {"sha256": "abc"}, {"backend": "torch"}, {"labels": {"PER": "NAME"}},
                {"labels": {"PER": "IGNORE"}}, {"labels": {}}, {"min_score_by_type": {"PERSON": 1.5}},
                {"min_score_by_type": {"EMAIL": 0.5}}, {"workers": -1}, {"intra_op_threads": 0},
                {"timeout_s": 0}, {"path": "../x"}, {"surprise": 1}, {"labels": {"PER": "DERIVED"}}):
        with pytest.raises(MlConfigError):
            parse_detector({**ok, **bad})


def test_gate_config_validation(tmp_path):
    ok = F.gate_profile(tmp_path, "b" * 64)
    assert parse_gate(ok).labels == tuple(F.GATE_LABELS)
    for bad in ({"labels": []}, {"labels": ["a", "a"]}, {"min_score_by_type": {}}):
        with pytest.raises(MlConfigError):
            parse_gate({**ok, **bad})
    assert parse_contract_gate({"labels": ["health"], "threshold": 0.5, "action": "private"})["action"] == "PRIVATE"
    for bad in ({"labels": ["health"], "threshold": 1.0, "action": "PRIVATE"},
                {"labels": ["health"], "threshold": 0.5, "action": "ALLOW"}, {"labels": [], "threshold": 0.5},
                {"labels": ["health"], "threshold": 0.5, "action": "REFUSE", "x": 1}, ["health"]):
        with pytest.raises(MlConfigError):
            parse_contract_gate(bad)


def test_profile_labels_must_match_the_model(tmp_path, tok_model):
    d, sha = tok_model
    build_detector(F.detector_profile(d, sha))
    with pytest.raises(ArtifactError, match="exactly the model's entity labels"):
        build_detector(F.detector_profile(d, sha, labels={"PER": "PERSON"}))
    with pytest.raises(ArtifactError, match="backend"):
        build_detector(F.detector_profile(d, sha, backend="span"))


def test_stage_maps_labels_and_applies_per_type_thresholds(tok_model):
    d, sha = tok_model
    st = build_detector(F.detector_profile(d, sha, labels={"PER": "PERSON", "LOC": "EMAIL"},
                                           min_score_by_type={"PERSON": 0.5, "EMAIL": 0.99}))
    dets = st.detect(TEXT)
    assert {(TEXT[x.start:x.end], x.type) for x in dets} == {("Priya Nair", EntityType.PERSON),
                                                            ("Rahul Sharma", EntityType.PERSON),
                                                            ("Raghunathan", EntityType.PERSON)}
    assert all(x.source == "D4:ml:token" for x in dets)

    class Rogue:
        def warm(self):
            pass

        def detect_many(self, texts):
            return [[Span(0, 3, "ORG", 0.9)] for _ in texts]
    with pytest.raises(MlUnavailable, match="not pinned"):
        MlEntityStage(Rogue(), st.cfg).detect("abcdef")


# --- span model (GLiNER-style) and content gate --------------------------------------------------------------------

def test_span_model_decodes_spans_with_offsets_and_windows(tmp_path):
    m = OnnxSpanModel(artifact.load(*F.make_span_model(tmp_path / "s")))
    t = "hello priya, nair met rahul"
    assert found(t, m.detect(t)) == [("hello priya", "person"), (", nair", "person"), ("met rahul", "person")]
    long = " ".join(f"w{i}" for i in range(21))          # max_words 8: several word windows
    got = found(long, m.detect(long))
    assert got == [(f"w{i} w{i + 1}", "person") for i in range(0, 20, 2)]
    assert m.detect("") == [] and m.detect_many(["a b", "a b"]) == [m.detect("a b")] * 2


def test_span_model_refuses_a_token_classifier(tok_model):
    with pytest.raises(ArtifactError, match="span-classification"):
        OnnxSpanModel(artifact.load(*tok_model))


def test_content_gate_scores_and_windows(tmp_path):
    g = OnnxSequenceClassifier(artifact.load(*F.make_gate_model(tmp_path / "g")))
    calm = g.classify("leave request for the team meeting")
    assert set(calm) == set(F.GATE_LABELS) and max(calm.values()) < 0.01
    hot = g.classify(("the team meeting today " * 30) + "cancer diagnosis")   # beyond the first window
    assert hot["health"] > 0.99 and hot["union_membership"] < 0.01
    assert triggered(hot, ["health"], 0.5) == ["health"] and triggered(calm, ["health"], 0.5) == []
    assert triggered({}, ["health"], 0.5) == ["health"]     # missing score: fail closed
    q = OnnxSequenceClassifier(artifact.load(*F.make_gate_model(tmp_path / "gq", quantize=True)))
    assert q.classify("union meeting")["union_membership"] > 0.99


# --- worker pool -----------------------------------------------------------------------------------------------------

def test_pool_matches_in_process_and_survives_concurrency(tok_model):
    d, sha = tok_model
    cfg = parse_detector(F.detector_profile(d, sha, workers=2, intra_op_threads=1))
    pool = open_detector(cfg)
    try:
        ref = build_detector(cfg.as_mapping())
        texts = [TEXT, "", "rahul sharma", TEXT]
        assert pool.detect_many(texts) == ref.detect_many(texts)
        res = []
        ths = [threading.Thread(target=lambda: res.append(pool.detect(TEXT))) for _ in range(6)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        assert all(r == ref.detect(TEXT) for r in res) and len(res) == 6
    finally:
        pool.close()
    with pytest.raises(MlUnavailable, match="closed"):
        pool.detect(TEXT)


def test_pool_timeout_fails_closed_and_worker_is_replaced():
    p = DetectorPool("tests.ml_fixtures:slow_detector", (5.0,), workers=1, max_queued=2, timeout_s=0.3)
    try:
        t0 = time.monotonic()
        with pytest.raises(MlUnavailable, match="timed out"):
            p.detect("slow text")
        assert time.monotonic() - t0 < 3
        deadline = time.monotonic() + 60
        while p._idle.qsize() < 1 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert p.detect("fast") == [] and p.replacements >= 1
    finally:
        p.close()


def test_pool_queue_full_fails_closed_immediately():
    p = GatePool("tests.ml_fixtures:slow_detector", (1.5,), workers=1, max_queued=0, timeout_s=10)
    try:
        th = threading.Thread(target=lambda: p.classify("slow"))
        th.start()
        time.sleep(0.3)
        t0 = time.monotonic()
        with pytest.raises(MlUnavailable, match="queue full"):
            p.classify("x")
        assert time.monotonic() - t0 < 0.5
        th.join()
        assert p.classify("x") == {"health": 0.0}
    finally:
        p.close()


def test_pool_crash_and_inference_error_fail_closed():
    p = DetectorPool("tests.ml_fixtures:crash_detector", (), workers=1, max_queued=2, timeout_s=10)
    try:
        with pytest.raises(MlUnavailable, match="inference failed"):
            p.detect("raise please")
        assert p.detect("fine") == []                  # an inference error does not kill the worker
        with pytest.raises(MlUnavailable, match="crashed|timed out"):
            p.detect("crash now")
    finally:
        p.close()


def test_pool_that_cannot_start_fails_closed(tmp_path, tok_model):
    with pytest.raises(MlUnavailable, match="failed to start"):
        DetectorPool("tests.ml_fixtures:failing_factory", (), workers=1)
    d, sha = tok_model
    with pytest.raises(ArtifactError):             # the parent checks the pin before any worker starts
        open_detector(parse_detector(F.detector_profile(d, "f" * 64, workers=1)))
    with pytest.raises(ArtifactError):
        open_gate(parse_gate(F.gate_profile(d, sha, workers=1)))


def test_gate_pool(tmp_path):
    d, sha = F.make_gate_model(tmp_path / "g")
    g = open_gate(parse_gate(F.gate_profile(d, sha, workers=1)))
    try:
        assert g.classify("cancer")["health"] > 0.99 and g.classify("team")["health"] < 0.01
    finally:
        g.close()


# --- pipeline integration ---------------------------------------------------------------------------------------------

POLICY = PolicyPack.load(ROOT / "policies/hris-default.yaml")
CTX = Context("eval", "eval-harness", "eval_tokenize_all")


class FakeMl:
    """An ML stage returning fixed detections (by substring), for precedence tests."""
    def __init__(self, rules):
        self.rules, self.calls = rules, 0

    def detect_many(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            ds = []
            for sub, typ, sc in self.rules:
                i = t.find(sub)
                if i >= 0:
                    ds.append(Detection(i, i + len(sub), typ, sc, "D4:ml:token"))
            out.append(ds)
        return out


def spans(prot, text):
    return [(text[d.start:d.end], d.type.value) for d in prot.analyze([{"role": "user", "content": text}], CTX)[0]]


def test_checksummed_id_regex_beats_ml():
    text = "My Aadhaar is 2345 6789 0124 thanks"
    base = spans(Protector(POLICY, Keys(bytes(32))), text)
    assert ("2345 6789 0124", "AADHAAR") in base
    ml = FakeMl([("Aadhaar is 2345 6789 0124", EntityType.PERSON, 0.99)])
    got = spans(Protector(POLICY, Keys(bytes(32)), ml=ml), text)
    assert ("2345 6789 0124", "AADHAAR") in got and not any(t == "PERSON" for _, t in got)


def test_ml_beats_heuristics():
    text = "Please check: Dear Kavya Raman Head, thanks"
    base = spans(Protector(POLICY, Keys(bytes(32))), text)
    assert any(t == "PERSON" for _, t in base)
    ml = FakeMl([("Kavya Raman Head", EntityType.EMPLOYEE_ID, 0.6)])     # deliberately different type and span
    got = spans(Protector(POLICY, Keys(bytes(32)), ml=ml), text)
    assert ("Kavya Raman Head", "EMPLOYEE_ID") in got and not any(t == "PERSON" for _, t in got)


def test_heuristics_switch_off_needs_ml_and_removes_cues():
    text = "Dear Kavya Raman,\nyour salary of Rs 85,000 per month is revised.\nRegards,\nSuresh Iyer"
    on = spans(Protector(POLICY, Keys(bytes(32))), text)
    assert any(t == "PERSON" for _, t in on) and any(t == "COMPENSATION" for _, t in on)
    assert Protector(POLICY, Keys(bytes(32)), heuristics=False).heuristics is True   # no ML: never off
    off = spans(Protector(POLICY, Keys(bytes(32)), ml=FakeMl([]), heuristics=False), text)
    assert not any(t in ("PERSON", "COMPENSATION") for _, t in off)
    ml_only = spans(Protector(POLICY, Keys(bytes(32)), ml=FakeMl([("Kavya Raman", EntityType.PERSON, 0.9)]),
                              heuristics=False), text)
    assert ("Kavya Raman", "PERSON") in ml_only


def test_ml_failure_propagates_fail_closed():
    class Broken:
        def detect_many(self, texts):
            raise MlUnavailable("down")
    with pytest.raises(MlUnavailable):
        Protector(POLICY, Keys(bytes(32)), ml=Broken()).protect([{"role": "user", "content": "hi Rahul"}], CTX)


def test_real_tiny_model_in_the_pipeline(tok_model):
    st = build_detector(F.detector_profile(*tok_model))
    text = "please summarise: priya nair met the team"     # lower case: no heuristic cue, the model finds it
    prot = Protector(POLICY, Keys(bytes(32)), ml=st, heuristics=False).protect(
        [{"role": "user", "content": text}], CTX)
    out = prot.messages[0]["content"]
    assert "priya" not in out.lower() and "nair" not in out.lower()


def _corpus():
    texts = ["Dear Kavya Raman,\nsalary Rs 85,000 pm. PAN ABCPK1234F, call 9876543210, kavya.r@acme.in",
             "hi tejas, ask devraj about EMP-04585", "Payment of Wages Act, 1936 applies to Priya Raghunathan"]
    p = ROOT / "eval/data/dev.jsonl"
    if p.exists():
        texts += [json.loads(line)["text"] for line in p.read_text().splitlines()[:150] if line.strip()]
    return texts


def test_no_ml_configured_means_unchanged_behaviour():
    """Without a model the new code paths are inert: identical detections and identical protected text."""
    a = Protector(POLICY, Keys(bytes(32)))
    b = Protector(POLICY, Keys(bytes(32)), ner=None, ml=None, heuristics=False)
    for t in _corpus():
        msgs = [{"role": "user", "content": t}]
        assert a.analyze(msgs, CTX) == b.analyze(msgs, CTX)
        ctx = Context("eval", "eval-harness", "eval_tokenize_all", conversation_id="c1")
        assert a.protect(msgs, ctx).messages == b.protect(msgs, ctx).messages
