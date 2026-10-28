import pytest
from ml.train import bio
from ml.train.labels import BIO_LABELS, LABEL2ID


class FakeTokenizer:
    """Whitespace + punctuation tokenizer with a subword split of long words, like a sentencepiece vocabulary."""

    def __init__(self, piece=4):
        self.piece = piece

    def offsets(self, text):
        out = [(0, 0)]  # <s>
        i = 0
        while i < len(text):
            if text[i].isspace():
                i += 1
                continue
            j = i
            while j < len(text) and not text[j].isspace() and (text[j].isalnum() == text[i].isalnum()):
                j += 1
            for k in range(i, j, self.piece):
                out.append((k, min(j, k + self.piece)))
            i = j
        out.append((0, 0))  # </s>
        return out


def lab(ids):
    return [BIO_LABELS[i] if i != -100 else "-" for i in ids]


def test_subword_alignment_b_then_i():
    text = "Name: Venkataraman Iyer PAN ABCPD1234E"
    spans = [{"start": 6, "end": 23, "type": "PERSON"}, {"start": 28, "end": 38, "type": "PAN"}]
    offs = FakeTokenizer().offsets(text)
    got = lab(bio.spans_to_bio(text, spans, offs))
    assert got[0] == "-" and got[-1] == "-"
    person = [g for g, (a, b) in zip(got, offs, strict=True) if 6 <= a < 23 and a != b]
    assert person[0] == "B-PERSON" and set(person[1:]) == {"I-PERSON"} and len(person) >= 4
    pan = [g for g, (a, b) in zip(got, offs, strict=True) if 28 <= a < 38 and a != b]
    assert pan[0] == "B-PAN" and set(pan[1:]) == {"I-PAN"}
    assert all(g == "O" for g, (a, b) in zip(got, offs, strict=True) if a != b and not (6 <= a < 23 or 28 <= a < 38))


def test_roundtrip_bio_to_spans():
    text = "Mr Ramesh Kumar paid Rs. 45,000/- to ABCPD1234E\nnext line"
    spans = [{"start": 3, "end": 15, "type": "PERSON"}, {"start": 26, "end": 36, "type": "COMPENSATION"},
             {"start": 40, "end": 50, "type": "PAN"}]
    # fix the PAN offset to the real text
    spans[2] = {"start": text.index("ABCPD"), "end": text.index("ABCPD") + 10, "type": "PAN"}
    spans[1] = {"start": text.index("Rs."), "end": text.index("/-") + 2, "type": "COMPENSATION"}
    offs = FakeTokenizer(piece=3).offsets(text)
    ids = bio.spans_to_bio(text, spans, offs)
    back = bio.bio_to_spans(text, ids, offs)
    assert [(s["start"], s["end"], s["type"]) for s in back] == [(s["start"], s["end"], s["type"]) for s in spans]


def test_overlapping_spans_rejected():
    text = "abcdefghij"
    with pytest.raises(bio.SpanError):
        bio.validate_spans(text, [{"start": 0, "end": 5, "type": "PERSON"}, {"start": 4, "end": 8, "type": "PAN"}])


def test_person_cannot_cross_newline_but_secret_can():
    text = "Ramesh\nKumar"
    with pytest.raises(bio.SpanError):
        bio.validate_spans(text, [{"start": 0, "end": 12, "type": "PERSON"}])
    key = "-----BEGIN KEY-----\nabc\n-----END KEY-----"
    bio.validate_spans(key, [{"start": 0, "end": len(key), "type": "SECRET"}])


def test_out_of_range_empty_and_unknown_rejected():
    for sp in ({"start": 5, "end": 5, "type": "PAN"}, {"start": 0, "end": 99, "type": "PAN"}, {"start": 0, "end": 3, "type": "AADHAAR_LIKE"}):
        with pytest.raises(bio.SpanError):
            bio.validate_spans("abcdefgh", [sp])


def test_token_straddling_two_spans_is_error():
    text = "ab cd"
    offs = [(0, 5)]  # one giant token covering both entities
    with pytest.raises(bio.SpanError):
        bio.spans_to_bio(text, [{"start": 0, "end": 2, "type": "PERSON"}, {"start": 3, "end": 5, "type": "PAN"}], offs)


def test_adjacent_same_type_spans_each_start_with_b():
    text = "Anil Raj Sunil"
    spans = [{"start": 0, "end": 8, "type": "PERSON"}, {"start": 9, "end": 14, "type": "PERSON"}]
    offs = [(0, 4), (5, 8), (9, 14)]
    assert lab(bio.spans_to_bio(text, spans, offs)) == ["B-PERSON", "I-PERSON", "B-PERSON"]


def test_windows_cover_everything_with_overlap():
    for n in (0, 1, 10, 510, 511, 1200, 5000):
        w = bio.make_windows(n, 512, 64)
        assert w[0][0] == 0 and w[-1][1] == n
        for (a, b), (c, _d) in zip(w, w[1:], strict=False):
            assert c < b and c > a  # overlap, progress
        assert all(b - a <= 510 for a, b in w)
    with pytest.raises(ValueError):
        bio.make_windows(100, 512, 600)


def test_merge_window_labels_prefers_central_context():
    w = [(0, 6), (4, 10)]
    preds = [[1] * 6, [2] * 6]
    merged = bio.merge_window_labels(10, w, preds)
    assert merged[:4] == [1] * 4 and merged[6:] == [2] * 4 and len(merged) == 10
    assert merged[4] == 1 and merged[5] == 2  # each overlap token keeps the window where it is farther from the edge


def test_label_ids_cover_all_bio_labels():
    assert len(LABEL2ID) == len(BIO_LABELS)
