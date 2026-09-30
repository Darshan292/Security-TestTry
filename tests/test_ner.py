import pytest

spacy = pytest.importorskip("spacy")

from pgw.detect import ner as ner_mod  # noqa: E402
from pgw.detect.ner import NerDetector, NerUnavailable, _chunks, _trim  # noqa: E402
from pgw.types import EntityType  # noqa: E402

MODEL = next((m for m in ("en_core_web_sm", "en_core_web_lg", "xx_ent_wiki_sm") if spacy.util.is_package(m)), None)
needs_model = pytest.mark.skipif(MODEL is None, reason="no spaCy model installed")


@needs_model
def test_offsets_on_unicode_text():
    text = "Café ☕ — 日本語 テスト. Later, Angela Merkel met Barack Obama in Berlin."
    dets = NerDetector(MODEL).detect(text)
    assert dets, "expected at least one PERSON"
    for d in dets:
        assert d.type is EntityType.PERSON and d.source == f"D4:ner:{MODEL}" and d.score == 0.75
        assert 0 <= d.start < d.end <= len(text)
    surfaces = [text[d.start:d.end] for d in dets]
    assert any("Merkel" in s for s in surfaces)


@needs_model
def test_empty_and_odd_input_never_raise():
    det = NerDetector(MODEL)
    assert det.detect("") == [] and det.detect("   \n") == []
    det.detect("\x00\x01\x02\ufffd" * 50)
    det.detect("bad \ud800 surrogate Priya Nair")
    det.detect("a" * 50_000)
    det.detect("\n" * 1000)


@needs_model
def test_min_score_above_fixed_score_returns_nothing():
    assert NerDetector(MODEL, min_score=0.9).detect("Angela Merkel met Barack Obama.") == []


def test_honorific_stripping():
    def strip(t):
        s, e = _trim(t, 0, len(t))
        return t[s:e]
    assert strip("Dr. Priya Nair") == "Priya Nair"
    assert strip("Mr Rahul Sharma") == "Rahul Sharma"
    assert strip("Shri Ramesh Kumar") == "Ramesh Kumar"
    assert strip("Smt. Dr. Anita Rao") == "Anita Rao"
    assert strip("Ramesh Sir") == "Ramesh"
    assert strip("Dr.") == ""
    assert strip("Priya Nair,") == "Priya Nair"
    assert strip("Mr.") == ""
    assert strip("Drew Barrymore") == "Drew Barrymore"   # 'Drew' is not 'Dr'
    text = "Hello Dr. Priya Nair here"
    assert text[text.index("Priya"):][:10] == "Priya Nair"


class _FakeEnt:
    def __init__(self, s, e, label="PERSON"):
        self.start_char, self.end_char, self.label_ = s, e, label


class _FakeNlp:
    """Finds every 'Zed Quux' (optionally after 'Dr. ') and also emits an ORG that must be ignored."""
    pipe_names = ["ner"]

    def __call__(self, chunk):
        ents, i = [], 0
        while (j := chunk.find("Zed Quux", i)) != -1:
            k = chunk.rfind("Dr. ", 0, j)
            ents.append(_FakeEnt(k if k != -1 and k + 4 == j else j, j + 8))
            i = j + 8
        if "Acme" in chunk:
            ents.append(_FakeEnt(chunk.index("Acme"), chunk.index("Acme") + 4, "ORG"))
        return type("Doc", (), {"ents": ents})()


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setitem(ner_mod._models, "fake", _FakeNlp())
    return NerDetector("fake")


def test_chunking_offsets_over_100k(fake):
    line = "filler line with nothing interesting é日本 " * 20 + "\n"     # ~ 700 chars
    parts, expect = [], []
    pos = 0
    while pos < 250_000:
        chunk_text = line
        if len(parts) % 31 == 0:
            chunk_text = "Acme note Dr. Zed Quux said hi\n"
        parts.append(chunk_text)
        pos += len(chunk_text)
    text = "".join(parts)
    assert len(text) > 200_000
    pieces = list(_chunks(text))
    assert len(pieces) >= 3 and all(len(c) <= ner_mod.MAX_CHUNK for _, c in pieces)
    assert "".join(c for _, c in pieces) == text and all(text[o:o + len(c)] == c for o, c in pieces)
    dets = fake.detect(text)
    n = text.count("Zed Quux")
    assert len(dets) == n and n > 5
    assert all(text[d.start:d.end] == "Zed Quux" for d in dets)      # honorific stripped, offsets absolute


def test_single_giant_line_is_split_without_loss(fake):
    text = "word " * 60_000 + "Zed Quux" + " tail" * 10
    dets = fake.detect(text)
    assert [text[d.start:d.end] for d in dets] == ["Zed Quux"]
    assert dets[0].start == text.index("Zed Quux")


def test_forced_failure_raises_ner_unavailable(monkeypatch):
    class Boom:
        def __call__(self, chunk):
            raise RuntimeError("model exploded")
    monkeypatch.setitem(ner_mod._models, "boom", Boom())
    with pytest.raises(NerUnavailable):
        NerDetector("boom").detect("Zed Quux")


def test_missing_model_raises_ner_unavailable():
    with pytest.raises(NerUnavailable):
        NerDetector("no_such_model_xyz").detect("Some text")


def test_non_str_raises_ner_unavailable():
    with pytest.raises(NerUnavailable):
        NerDetector("no_such_model_xyz").detect(b"bytes")  # type: ignore[arg-type]
