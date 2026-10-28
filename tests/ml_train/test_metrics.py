import pytest
from ml.train import metrics as M
from ml.train.labels import BIO_LABELS, LABEL2ID


def sp(a, b, t):
    return {"start": a, "end": b, "type": t}


def test_strict_and_lenient_prf():
    gold = [[sp(0, 10, "PAN"), sp(20, 32, "AADHAAR")], [sp(5, 9, "PERSON")]]
    pred = [[sp(0, 10, "PAN"), sp(20, 28, "AADHAAR"), sp(40, 45, "PERSON")], [sp(5, 9, "PERSON")]]
    s = M.entity_prf(gold, pred)
    assert s["PAN"]["recall"] == 1.0 and s["AADHAAR"]["recall"] == 0.0 and s["PERSON"]["precision"] == 0.5
    l = M.entity_prf(gold, pred, lenient=True)
    assert l["AADHAAR"]["recall"] == 1.0
    assert s["micro"]["tp"] == 2 and s["micro"]["fn"] == 1


def test_wrong_type_counts_as_miss_and_false_positive():
    s = M.entity_prf([[sp(0, 5, "PAN")]], [[sp(0, 5, "IFSC")]])
    assert s["PAN"]["fn"] == 1 and s["IFSC"]["fp"] == 1


def test_length_mismatch():
    with pytest.raises(ValueError):
        M.entity_prf([[]], [])


def test_threshold_decoding_trades_precision_for_recall():
    np = pytest.importorskip("numpy")
    n = len(BIO_LABELS)
    p = np.full((3, n), 0.0)
    p[:, LABEL2ID["O"]] = [0.9, 0.6, 0.2]
    p[0, LABEL2ID["B-PAN"]] = 0.1
    p[1, LABEL2ID["B-PAN"]] = 0.4
    p[2, LABEL2ID["I-PAN"]] = 0.8
    strict = [BIO_LABELS[i] for i in M.decode_with_threshold(p, 0.7)]
    loose = [BIO_LABELS[i] for i in M.decode_with_threshold(p, 0.3)]
    assert strict == ["O", "O", "I-PAN"] or strict[:2] == ["O", "O"]
    assert loose == ["O", "B-PAN", "I-PAN"]


def test_multilabel_threshold_meets_recall_target():
    y_true = [[1, 0]] * 20 + [[0, 1]] * 20 + [[0, 0]] * 40
    y_prob = [[0.9 - i * 0.01, 0.1] for i in range(20)] + [[0.1, 0.9]] * 20 + [[0.05, 0.05]] * 40
    rep = M.multilabel_report(y_true, y_prob, ["A", "B"], 0.95)
    assert rep["A"]["recall"] >= 0.95 and rep["A"]["recall_target_met"] and rep["B"]["precision"] == 1.0
