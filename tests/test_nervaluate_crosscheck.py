"""The repo's span metrics (eval/ml/metrics.py) against nervaluate (eval/ml/nerval.py) on fixed fixtures.

Where the definitions match the numbers must agree to the 4th decimal; where they differ the difference is pinned by a
fixture so the documentation in eval/ml/nerval.py cannot rot. Skipped when nervaluate is not installed."""
import pytest

pytest.importorskip("nervaluate")

from eval.ml import nerval  # noqa: E402
from eval.ml.metrics import Scorer  # noqa: E402

# one-to-one fixtures: every prediction overlaps at most one gold span of its type and vice versa; no cross-type overlap
ONE_TO_ONE = [
    # (gold, pred) per document; spans are (start, end exclusive, TYPE)
    ([(0, 5, "PERSON"), (10, 20, "PAN"), (30, 38, "PHONE")], [(0, 5, "PERSON"), (10, 20, "PAN"), (30, 38, "PHONE")]),
    ([(2, 12, "PERSON"), (20, 30, "EMAIL")], [(3, 12, "PERSON"), (20, 31, "EMAIL")]),                 # boundary off by one
    ([(0, 8, "AADHAAR"), (15, 25, "PERSON")], [(15, 25, "PERSON")]),                                  # a miss
    ([(5, 9, "PERSON")], [(5, 9, "PERSON"), (40, 50, "PHONE")]),                                      # a false positive
    ([], [(1, 4, "PERSON")]),                                                                         # spurious only
    ([(1, 4, "PERSON"), (6, 9, "PERSON")], [(1, 4, "PERSON"), (6, 9, "PERSON")]),                     # adjacent, same type
    ([(0, 5, "PERSON")], [(5, 9, "PERSON")]),                                                         # touching, no overlap
    ([(0, 12, "PAN")], [(4, 20, "PAN")]),                                                             # partial overlap
]


def repo_scores(docs):
    sc = Scorer()
    for gold, pred in docs:
        sc.add("x" * 100, gold, pred, "g", "x" * 100, [], 0.0)
    return sc.result()


def prf(d):
    return (d["precision"], d["recall"], d["f1"])


def test_strict_equals_repo_exact_and_type_equals_repo_overlap_one_to_one():
    golds, preds = zip(*ONE_TO_ONE, strict=True)
    nv = nerval.evaluate(list(golds), list(preds))
    repo = repo_scores(ONE_TO_ONE)
    assert prf(nv["strict"]["overall"]) == pytest.approx(prf(repo["exact"]["overall"]), abs=1e-4)
    assert prf(nv["type"]["overall"]) == pytest.approx(prf(repo["overlap"]["overall"]), abs=1e-4)
    for t, row in repo["exact"]["by_type"].items():
        if t in nv["strict"]["by_type"]:
            assert prf(nv["strict"]["by_type"][t]) == pytest.approx(prf(row), abs=1e-4), t
    for t, row in repo["overlap"]["by_type"].items():
        if t in nv["type"]["by_type"]:
            assert prf(nv["type"]["by_type"][t]) == pytest.approx(prf(row), abs=1e-4), t


def test_perfect_prediction_is_perfect_in_every_scheme():
    golds, preds = zip(*ONE_TO_ONE[:1], strict=True)
    nv = nerval.evaluate(list(golds), list(preds))
    for scheme in ("strict", "exact", "partial", "type"):
        assert prf(nv[scheme]["overall"]) == (1.0, 1.0, 1.0)


def test_exclusive_ends_are_converted_touching_spans_do_not_overlap():
    assert nerval.to_nerval([(0, 5, "A")]) == [{"label": "A", "start": 0, "end": 4}]
    nv = nerval.evaluate([[(0, 5, "A")]], [[(5, 9, "A")]])
    assert nv["type"]["overall"]["correct"] == 0 and nv["partial"]["overall"]["partial"] == 0


# ------------------------------------------------------------------------------------------ documented differences
def test_difference_many_to_one_overlap():
    """Two short predictions inside one long gold span: the repo's overlap metric credits both and the gold; nervaluate
    matches one prediction to one gold span and counts the second prediction spurious."""
    docs = [([(0, 10, "PERSON")], [(0, 3, "PERSON"), (4, 6, "PERSON")])]
    repo = repo_scores(docs)["overlap"]["overall"]
    nv = nerval.evaluate([docs[0][0]], [docs[0][1]])["type"]["overall"]
    assert (repo["precision"], repo["recall"]) == (1.0, 1.0)
    assert (nv["precision"], nv["recall"]) == (0.5, 1.0) and nv["spurious"] == 1


def test_difference_cross_type_confusion():
    """Right boundaries, wrong type: both strict and the repo's exact count a miss and a false positive; nervaluate's
    type-blind `exact` scheme counts it correct (the repo has no such scheme)."""
    gold, pred = [(0, 8, "PHONE")], [(0, 8, "BANK_ACCOUNT")]
    repo = repo_scores([(gold, pred)])["exact"]["overall"]
    nv = nerval.evaluate([gold], [pred])
    assert (repo["precision"], repo["recall"]) == (0.0, 0.0)
    assert nv["strict"]["overall"]["recall"] == 0.0 and nv["exact"]["overall"]["recall"] == 1.0
    assert nv["partial"]["overall"]["recall"] == 1.0 and nv["type"]["overall"]["recall"] == 0.0


def test_difference_partial_credit_has_no_repo_analogue():
    nv = nerval.evaluate([[(0, 10, "PAN")]], [[(0, 5, "PAN")]])
    assert nv["partial"]["overall"]["recall"] == 0.5 and nv["strict"]["overall"]["recall"] == 0.0


def test_difference_precision_with_no_predictions():
    """A type with gold but no predictions: the repo reports precision 0.0 only when gold exists (1.0 when neither);
    nervaluate reports 0 whenever there is nothing predicted."""
    repo = repo_scores([([(0, 5, "PAN")], [])])["exact"]["by_type"]["PAN"]
    nv = nerval.evaluate([[(0, 5, "PAN")]], [[]])["strict"]["by_type"]["PAN"]
    assert repo["precision"] == 0.0 and nv["precision"] == 0 and nv["missed"] == 1


def test_collector_and_run_integration(tmp_path):
    from eval.ml.backends import Backend
    from eval.ml.datasets import Example
    from eval.ml.run import evaluate
    ex = [Example("1", "Aadhaar 2341 2341 2346 and PAN ABCPE1234F here.", [(8, 22, "AADHAAR"), (31, 41, "PAN")], "g")]
    res = evaluate(Backend("current"), ex, nervaluate=True)
    assert set(res["nervaluate"]) == {"strict", "exact", "partial", "type"}
    assert res["nervaluate"]["strict"]["overall"]["possible"] == 2
