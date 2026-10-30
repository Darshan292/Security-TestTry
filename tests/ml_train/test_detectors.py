import pytest
from ml.train import detectors as D
from ml.train import licences
from ml.train.labels import ENTITY_TYPES


def test_every_reviewed_map_targets_gateway_types_or_ignore():
    for table in (D.INDICNER_MAP, D.PRIVACY_FILTER_MAP, D.GLINER_PII_PROMPTS):
        assert all(v == D.IGNORE or v in ENTITY_TYPES or v == "ADDRESS" for v in table.values())
    assert D.span_prompt_map(D.GLINER_PII_PROMPTS)["phone number"] == "PHONE"
    with pytest.raises(AssertionError):
        D.span_prompt_map({"x": "NOT_A_TYPE"})


def test_unmapped_labels_become_ignore_and_are_reported():
    m, unknown = D.map_token_labels(["O", "B-PER", "I-PER", "B-LOC", "I-LOC", "B-WEIRD", "I-WEIRD"], D.INDICNER_MAP)
    assert m == {"PER": "PERSON", "LOC": "IGNORE", "WEIRD": "IGNORE"} and unknown == ["WEIRD"]
    m, unknown = D.map_token_labels(["O", "B-PRIVATE_EMAIL", "I-PRIVATE_EMAIL"], D.PRIVACY_FILTER_MAP)
    assert m == {"PRIVATE_EMAIL": "EMAIL"} and not unknown


def test_candidates_exclude_the_noncommercial_gliner_v0_and_all_have_licence_entries():
    repos = [c["repo"] for c in D.CANDIDATES.values() if c["repo"]]
    assert "urchade/gliner_multi" not in repos and "urchade/gliner_multi-v2.1" not in repos
    assert set(repos) <= set(licences.ALLOWED_DETECTORS)


def test_check_model_with_detector_table():
    ok = licences.check_model("ai4bharat/IndicNER", lambda r: "MIT", table=licences.ALLOWED_DETECTORS)
    assert ok == "mit"
    with pytest.raises(AssertionError):                            # card says non-commercial: refused
        licences.check_model("urchade/gliner_multi_pii-v1", lambda r: "cc-by-nc-4.0", table=licences.ALLOWED_DETECTORS)
    with pytest.raises(AssertionError):                            # not in the table
        licences.check_model("urchade/gliner_multi", lambda r: "apache-2.0", table=licences.ALLOWED_DETECTORS)
    with pytest.raises(OSError):                                   # hub unreachable and no fallback allowed: propagates
        licences.check_model("ai4bharat/IndicNER", lambda r: (_ for _ in ()).throw(OSError("offline")),
                             table=licences.ALLOWED_DETECTORS)


def test_span_agreement():
    ref = [[(0, 5, "PERSON", 0.9), (10, 20, "PAN", 0.9)], [(0, 3, "EMAIL", 0.9)]]
    oth = [[(0, 5, "PERSON", 0.8), (11, 20, "PAN", 0.8)], []]
    a = D.span_agreement(ref, oth)
    assert a["ref_spans"] == 3 and a["exact_recall"] == round(1 / 3, 4) and a["overlap_recall"] == round(2 / 3, 4)
    assert a["overlap_precision"] == 1.0 and a["identical_docs"] == 0.0
    same = D.span_agreement(ref, ref)
    assert same["exact_recall"] == same["identical_docs"] == 1.0


def test_gold_recall_shape_works_with_card_max_recall_drop():
    from ml.train import card
    gold = [[(0, 5, "PERSON"), (10, 20, "PAN")]]
    full = D.gold_recall(gold, [[(0, 5, "PERSON", 1.0), (10, 20, "PAN", 1.0)]])
    half = D.gold_recall(gold, [[(0, 5, "PERSON", 1.0)]])
    assert full["micro"] == 1.0 and half["micro"] == 0.5
    assert card.max_recall_drop(full, half, min_support=1) == 1.0   # largest per-type drop (PAN 1.0 -> 0.0)


def test_latency_and_card(tmp_path):
    s = D.latency_stats([1.0, 2.0, 3.0, 4.0, 100.0])
    assert s["n"] == 5 and s["p50"] == 3.0 and s["max"] == 100.0 and D.latency_stats([]) == {"n": 0}
    D.write_card(tmp_path / "MODEL_CARD.md", title="t", repo="r/x", revision=None, licence="mit", allowed=["mit"], export="e",
                 mapping={"PER": "PERSON"}, unknown=["ODD"], metrics={"a": 1})
    t = (tmp_path / "MODEL_CARD.md").read_text()
    assert "not validated on real" in t.lower()
    assert "`PER` -> PERSON" in t and "ODD" in t and "Third-party pretrained" in t
    assert D.bench_meta({"PER": "PERSON"}, "token")["external"] is True
