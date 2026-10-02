"""NerPool: equivalence with NerDetector, fail-closed queue/timeout/crash behaviour, gateway 503. Synthetic text."""
import os
import threading
import time

import pytest

spacy = pytest.importorskip("spacy")

from pgw.detect.ner import NerDetector, NerUnavailable  # noqa: E402
from pgw.detect.ner_pool import NerPool  # noqa: E402

MODEL = next((m for m in ("en_core_web_sm", "en_core_web_lg") if spacy.util.is_package(m)), None)
pytestmark = pytest.mark.skipif(MODEL is None, reason="no spaCy model installed")

TEXTS = [
    "Café ☕ — Later, Angela Merkel met Barack Obama in Berlin.",
    "Ticket PAY-1 | Raised by: Imran Ansari. Approved by Dr. Suresh Menon, sir.",
    "",
    "   \n",
    "bad \ud800 surrogate Priya Nair wrote this.\n" * 3,
    "x" * 150_000 + " Mr. Rahul Verma joined.",
    "no names here, just payroll.",
]


@pytest.fixture(scope="module")
def pool():
    p = NerPool(MODEL, workers=2, max_queued=4, timeout_s=30)
    yield p
    p.close()


def test_equivalence_with_in_process(pool):
    ref = NerDetector(MODEL)
    for t in TEXTS:
        assert pool.detect(t) == ref.detect(t)
    assert pool.detect_many(TEXTS) == [ref.detect(t) for t in TEXTS]
    # duplicates in a batch are answered consistently
    dup = [TEXTS[0], TEXTS[1], TEXTS[0]]
    assert pool.detect_many(dup) == [ref.detect(t) for t in dup]
    assert ref.detect_many(TEXTS) == [ref.detect(t) for t in TEXTS]


def test_non_str_fails_closed(pool):
    with pytest.raises(NerUnavailable):
        pool.detect_many([123])  # type: ignore[list-item]


def test_queue_full_fails_closed_immediately():
    p = NerPool(MODEL, workers=1, max_queued=0, timeout_s=30)
    try:
        slow = ("Angela Merkel met Barack Obama. " * 400 + "\n") * 15
        t = threading.Thread(target=lambda: p.detect(slow))
        t.start()
        time.sleep(0.3)
        t0 = time.monotonic()
        with pytest.raises(NerUnavailable, match="queue full"):
            p.detect("Priya Nair")
        assert time.monotonic() - t0 < 0.5
        t.join()
        assert p.detect("Angela Merkel spoke.")      # capacity returns
    finally:
        p.close()


def test_timeout_fails_closed_and_worker_is_replaced():
    p = NerPool(MODEL, workers=1, max_queued=2, timeout_s=0.05)
    try:
        slow = ("Angela Merkel met Barack Obama. " * 400 + "\n") * 15
        with pytest.raises(NerUnavailable, match="timed out"):
            p.detect(slow)
        p.timeout_s = 30
        assert p.detect("Angela Merkel spoke.") == NerDetector(MODEL).detect("Angela Merkel spoke.")
        assert p.replacements >= 1
    finally:
        p.close()


def test_worker_crash_fails_closed_and_recovers():
    p = NerPool(MODEL, workers=1, max_queued=2, timeout_s=30)
    try:
        (w,) = list(p._live)
        os.kill(w.proc.pid, 9)
        w.proc.join(5)
        with pytest.raises(NerUnavailable):
            p.detect("Angela Merkel spoke.")
        assert p.detect("Angela Merkel spoke.") == NerDetector(MODEL).detect("Angela Merkel spoke.")  # replacement serves (waits for it within the timeout)
        assert p.replacements >= 1
    finally:
        p.close()


def test_closed_pool_fails_closed():
    p = NerPool(MODEL, workers=1)
    p.close()
    with pytest.raises(NerUnavailable):
        p.detect("Priya Nair")


def test_bad_model_stops_startup():
    with pytest.raises(NerUnavailable):
        NerPool("no_such_model_xyz", workers=1, start_timeout_s=60)


def test_gateway_503_and_nothing_sent_when_pool_rejects(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from pgw.gateway.app import create_app
    from pgw.gateway.upstream import MockLLMUpstream
    from tests.test_gateway_hardening import TICKET, call, settings
    mock = MockLLMUpstream("summarise")
    app = create_app(settings(tmp_path, mock, ner_model=MODEL, ner_workers=1, ner_queue_max=0))
    client = TestClient(app)
    ok = call(client, TICKET)
    assert ok.status_code == 200 and len(mock.received) == 1

    def reject(self, texts):
        raise NerUnavailable("NER queue full")
    monkeypatch.setattr(NerPool, "detect_many", reject)
    r = call(client, TICKET)
    assert r.status_code == 503 and "detector_unavailable" in r.text
    assert len(mock.received) == 1            # nothing was sent upstream
