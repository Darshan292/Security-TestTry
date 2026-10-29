"""Presidio as an evaluation backend (eval/ml/presidio_backend.py): India recognizers, mapping, no network.
Skipped when presidio-analyzer or the spaCy model en_core_web_lg is not installed."""
import socket

import pytest

pytest.importorskip("presidio_analyzer")
spacy_util = pytest.importorskip("spacy.util")
if not spacy_util.is_package("en_core_web_lg"):
    pytest.skip("spaCy model en_core_web_lg is not installed", allow_module_level=True)

from eval.ml import presidio_backend as pb  # noqa: E402
from pgw.types import EntityType  # noqa: E402

TEXT = ("Aadhaar number 2341 2341 2346 for Rahul Sharma at Infosys, Mumbai. PAN: ABCPE1234F, GSTIN 27ABCPE1234F1Z5, "
        "passport J8369854, voter ID ABC1234567, vehicle MH12AB1234, mail rahul@gmail.com, phone +91 98765 43210, "
        "card 4111 1111 1111 1111.")


@pytest.fixture(scope="module")
def presidio():
    return pb.Presidio(min_score=0.0)


def test_mapping_targets_are_gateway_types_and_cover_india_recognizers():
    assert all(isinstance(v, EntityType) for v in pb.ENTITY_MAP.values())
    assert set(pb.INDIA_ENTITIES) <= set(pb.ENTITY_MAP)
    assert EntityType.DERIVED not in pb.ENTITY_MAP.values()


def test_india_recognizers_are_enabled_and_mapped(presidio):
    got = {(TEXT[s:e], t) for s, e, t, _ in presidio.detect(TEXT)}
    assert ("2341 2341 2346", "AADHAAR") in got
    assert ("ABCPE1234F", "PAN") in got
    for value in ("27ABCPE1234F1Z5", "J8369854", "ABC1234567", "MH12AB1234"):
        assert (value, "IDENTIFIER") in got, value
    assert ("rahul@gmail.com", "EMAIL") in got and ("Rahul Sharma", "PERSON") in got
    assert any(t == "CARD" for _, t in got) and any(t == "PHONE" for _, t in got)


def test_unmapped_entities_are_ignored_and_counted():
    p = pb.Presidio(min_score=0.0, engine=None)
    spans = p.spans(TEXT)
    assert not any(TEXT[s:e] in ("Infosys", "Mumbai") for s, e, _ in spans)
    notes = p.notes()
    assert notes["ignored_total"] >= 2 and "LOCATION" in notes["ignored_entities"]
    assert set(notes["mapped_entities"]) <= set(pb.ENTITY_MAP)


def test_min_score_filters_and_counts():
    p = pb.Presidio(min_score=0.95)
    kept = p.spans(TEXT)
    assert kept and all(sc >= 0.95 for *_, sc in p.detect(TEXT))
    assert p.notes()["below_threshold"] > 0
    assert len(kept) <= len(pb.Presidio(min_score=0.0).spans(TEXT))


def test_no_network_io(monkeypatch):
    """Building the engine and analysing text (an e-mail forces tldextract) must not open a single socket connection."""
    calls = []

    def blocked(*a, **k):
        calls.append(a)
        raise AssertionError("network access attempted: " + repr(a)[:80])

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    p = pb.Presidio(min_score=0.0)                       # fresh engine inside the block
    spans = p.spans("Mail priya.nair@example.co.in or rahul@gmail.com; PAN ABCPE1234F. See https://example.org/x")
    assert {t for _, _, t in spans} >= {"EMAIL", "PAN"}
    assert not calls


def test_email_recognizer_is_the_offline_subclass():
    eng = pb.build_engine()
    names = [type(r).__name__ for r in eng.registry.recognizers]
    assert names.count("OfflineEmailRecognizer") == 1 and "EmailRecognizer" not in names


def test_presidio_and_union_backends_via_harness():
    from eval.ml.backends import Backend
    from eval.ml.datasets import Example
    from eval.ml.run import evaluate
    ex = [Example("1", "Employee Rahul Sharma, PAN ABCPE1234F, mail rahul@gmail.com.",
                  [(9, 21, "PERSON"), (27, 37, "PAN"), (44, 59, "EMAIL")], "g")]
    for name in ("presidio", "union"):
        res = evaluate(Backend(name), ex)
        assert res["overlap"]["overall"]["recall"] == 1.0, name
    assert "ignored_total" in evaluate(Backend("presidio"), ex)["backend_notes"]


def test_upstream_default_tldextract_would_touch_the_network(monkeypatch, tmp_path):
    """Why the offline subclass exists: tldextract's default extractor tries to download the public-suffix list."""
    import logging

    import tldextract
    calls = []

    def blocked(*a, **k):
        calls.append(a)
        raise OSError("blocked")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    logging.disable(logging.CRITICAL)
    try:
        tldextract.TLDExtract(cache_dir=str(tmp_path), suffix_list_urls=("https://publicsuffix.org/list/public_suffix_list.dat",))("a@example.co.in")
    finally:
        logging.disable(logging.NOTSET)
    assert calls
