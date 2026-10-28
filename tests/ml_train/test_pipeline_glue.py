import json

import numpy as np
import pytest
from ml.train import card, infer, jobs, licences, synth, trainprep
from ml.train.labels import BIO_LABELS
from ml.train.metrics import decode_with_threshold, entity_prf
from ml.train.bio import bio_to_spans

from tests.ml_train.test_bio import FakeTokenizer


class FastFake:
    """Mimics a HF fast tokenizer closely enough for the glue code."""
    cls_token_id, sep_token_id, pad_token_id = 0, 2, 1

    def __init__(self):
        self.t = FakeTokenizer(piece=5)

    def __call__(self, text, add_special_tokens=False, return_offsets_mapping=True, truncation=False):
        offs = self.t.offsets(text)[1:-1]
        return {"input_ids": [3 + (hash(text[a:b]) % 50) for a, b in offs], "offset_mapping": offs}


def oracle_logits_fn(rec, tok):
    """A fake model that outputs the gold BIO labels as confident logits, found by matching the token ids it is given."""
    ids, offs = infer.tokenize_doc(tok, rec["text"])
    from ml.train.bio import spans_to_bio
    lab = spans_to_bio(rec["text"], rec["spans"], offs)

    def fn(x, m):
        out = np.full((x.shape[0], x.shape[1], len(BIO_LABELS)), -5.0)
        for r in range(x.shape[0]):
            n = int(m[r].sum()) - 2
            body = list(x[r, 1:1 + n])
            for s in range(len(ids) - n + 1):
                if ids[s:s + n] == body:
                    for k in range(n):
                        out[r, 1 + k, lab[s + k] if lab[s + k] >= 0 else 0] = 5.0
                    break
        return out
    return fn


def test_roundtrip_through_windows_recovers_gold_spans():
    tok = FastFake()
    recs = synth.programmatic_records(15, seed=21)
    for rec in recs:
        probs, offs = infer.doc_probs(rec["text"], tok, oracle_logits_fn(rec, tok), max_len=64, stride=16)
        pred = bio_to_spans(rec["text"], decode_with_threshold(probs, 0.5), offs)
        s = entity_prf([rec["spans"]], [pred])
        assert s["micro"]["fn"] == 0 and s["micro"]["fp"] == 0, rec["genre"]


def test_encode_records_labels_align_and_specials_ignored():
    tok = FastFake()
    recs = synth.programmatic_records(10, seed=4)
    rows, dropped = trainprep.encode_records(recs, tok, max_len=64, stride=16)
    assert rows and dropped == 0
    for r in rows:
        assert len(r["input_ids"]) == len(r["labels"]) <= 64
        assert r["labels"][0] == -100 and r["labels"][-1] == -100
    w = trainprep.class_weights(rows, len(BIO_LABELS))
    assert w[0] == 1.0 and len(w) == len(BIO_LABELS) and all(1.0 <= x <= 10.0 for x in w)


def test_jobs_are_deterministic_and_templates_accepted_or_rejected():
    a, b = jobs.template_job(5, 17), jobs.template_job(5, 17)
    assert a == b and a != jobs.template_job(5, 18)
    assert jobs.gate_job(5, 3)["labels"] and json.dumps(a)
    raw = ("Dear {{PERSON_1:first}}, PAN {{PAN_1}} account {{BANK_ACCOUNT_1}} salary {{COMPENSATION_1}} please confirm the details soon. "
           "Regards HR " * 3 + "\n=====\nCall 9876543210 {{PERSON_1}} now please " * 5)
    job = dict(jobs.template_job(5, 1), genre="bullet_list")
    ok, why = jobs.accept(job, raw, "m")
    assert len(ok) == 1 and ok[0]["id"].startswith("t000001") and why
    gj = dict(jobs.gate_job(1, 1), labels=["HEALTH"])
    ok, why = jobs.accept(gj, "{{PERSON_1}} was hospitalised after surgery and needs medical leave.", "m")
    assert ok and ok[0]["labels"] == ["HEALTH"]


def test_licence_check():
    assert licences.check_model("FacebookAI/xlm-roberta-base", lambda r: "MIT") == "mit"
    with pytest.raises(AssertionError):
        licences.check_model("FacebookAI/xlm-roberta-base", lambda r: "cc-by-nc-4.0")
    for bad in ("meta-llama/Llama-3.1-8B", "google/gemma-2-9b", "Qwen/Qwen2.5-3B-Instruct", "ai4privacy/x", "iiiorg/piiranha-v1"):
        with pytest.raises(AssertionError):
            licences.check_model(bad, lambda r: "apache-2.0")

    def boom(r):
        raise OSError("blocked")
    with pytest.raises(OSError):
        licences.check_model("Qwen/Qwen2.5-7B-Instruct", boom)
    assert licences.check_model("Qwen/Qwen2.5-7B-Instruct", boom, allow_table_fallback=True) == "apache-2.0"


def test_card_and_sums(tmp_path):
    card.write_card(tmp_path / "MODEL_CARD.md", "t", "g", "i", "b", "mit", ["PAN"], {"a": 1})
    (tmp_path / "model.onnx").write_bytes(b"x")
    sums = card.write_sha256sums(tmp_path)
    assert set(sums) == {"MODEL_CARD.md", "model.onnx"}
    text = (tmp_path / "MODEL_CARD.md").read_text().lower()
    assert "synthetic" in text and "not validated on real" in text
    assert card.max_recall_drop({"micro": 0.99, "per_type": {"PAN": (1.0, 100), "X": (1.0, 3)}},
                                {"micro": 0.985, "per_type": {"PAN": (0.97, 100), "X": (0.0, 3)}}) == 0.03
