"""Optional cross-check of the repo's own span metrics against nervaluate (MIT; SemEval-2013 task 9.1 schemes).

nervaluate reports four schemes; this module reports them per entity type and overall:

  strict   same boundaries AND same type                        repo: ``exact``   (agrees, see below)
  exact    same boundaries, type ignored                        repo: none (a cross-type confusion still counts)
  partial  any overlap, type ignored; partial credit 0.5        repo: none
  type     any overlap AND same type (nervaluate: ``ent_type``) repo: ``overlap``  (agrees when matches are 1:1)

Where the definitions match (tests/test_nervaluate_crosscheck.py pins it on fixed fixtures):
  * repo ``exact`` per type and overall = nervaluate ``strict`` (precision, recall, F1);
  * repo ``overlap`` = nervaluate ``type`` when every prediction overlaps at most one gold span of its type and vice
    versa (one-to-one).
Where they differ (documented, and pinned by a fixture each):
  * nervaluate matches one prediction to one gold span (greedy, in prediction order); the repo's overlap metric is
    many-to-one: a long gold span covered by two short predictions counts both predictions correct and the gold found.
    nervaluate counts the second prediction spurious. Real data with split names / long values shows this.
  * a prediction of the right boundaries and a wrong type is "incorrect" in nervaluate (counted in the ``exact`` and
    ``partial`` schemes as a hit, since those ignore the type) and simply a false positive plus a miss in the repo.
  * with no predictions of a type, the repo reports precision 1.0 if the type has no gold either, nervaluate 0.
  * nervaluate offsets are INCLUSIVE at the end; the repo's are exclusive. ``to_nerval`` converts (end - 1).
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass

SCHEMES = {"strict": "strict", "exact": "exact", "partial": "partial", "type": "ent_type"}


class NervaluateUnavailable(RuntimeError):
    pass


def _import():
    try:
        import nervaluate
    except ImportError as e:  # optional dependency (requirements/eval.in)
        raise NervaluateUnavailable("nervaluate is not installed (pip install -r requirements/eval.txt)") from e
    return nervaluate


def to_nerval(spans: list[tuple[int, int, str]]) -> list[dict]:
    """(start, end exclusive, TYPE) -> nervaluate dicts (end inclusive)."""
    return [{"label": t, "start": s, "end": e - 1} for s, e, t in spans]


def _flat(res) -> dict:
    d = asdict(res) if is_dataclass(res) else dict(vars(res))
    keep = ("correct", "incorrect", "partial", "missed", "spurious", "actual", "possible", "precision", "recall", "f1")
    return {k: (round(float(d[k]), 4) if k in ("precision", "recall", "f1") else d[k]) for k in keep}


def evaluate(golds: list[list[tuple[int, int, str]]], preds: list[list[tuple[int, int, str]]],
             tags: list[str] | None = None, min_overlap_percentage: float = 1.0) -> dict:
    """golds/preds: one list of (start, end, TYPE) per document. -> {scheme: {"overall": {...}, "by_type": {...}}}."""
    nv = _import()
    if len(golds) != len(preds):
        raise ValueError("golds and preds must have one entry per document")
    if tags is None:
        tags = sorted({t for d in (*golds, *preds) for *_, t in d})
    ev = nv.Evaluator([to_nerval(g) for g in golds], [to_nerval(p) for p in preds], tags=list(tags), loader="dict",
                      min_overlap_percentage=min_overlap_percentage)
    raw = ev.evaluate()
    out = {}
    for name, key in SCHEMES.items():
        out[name] = {"overall": _flat(raw["overall"][key]),
                     "by_type": {t: _flat(v[key]) for t, v in sorted(raw["entities"].items())}}
    return out


class Collector:
    """Accumulates (gold, pred) per document during an eval run (eval.ml.run --nervaluate)."""

    def __init__(self) -> None:
        self.golds: list = []
        self.preds: list = []

    def add(self, gold, pred) -> None:
        self.golds.append(list(gold))
        self.preds.append(list(pred))

    def result(self) -> dict:
        return evaluate(self.golds, self.preds)
