"""Span-level scoring, backend-agnostic (docs/ML_EVALUATION.md).

- exact: a prediction counts only with the same (start, end, type) as a gold span;
- overlap: a prediction is correct when it overlaps a gold span of the same type, and a gold span is found when a
  prediction of its type overlaps it;
- leak rate: share of gold VALUES (the gold surface string) that still occur in the protected text (exact substring,
  or the same digits for digit-bearing values written with other separators). Type-agnostic: a value masked under the
  wrong type is not a leak;
- over-masking: share of annotated benign spans touched by a prediction, and share of characters outside every gold
  span that were masked;
- latency: per-example wall time of the backend (p50 / p95 / max, ms).
"""
from __future__ import annotations

import re
from collections import defaultdict


def _prf(tp_p: int, n_pred: int, tp_g: int, n_gold: int) -> dict:
    p = tp_p / n_pred if n_pred else (1.0 if not n_gold else 0.0)
    r = tp_g / n_gold if n_gold else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "pred": n_pred, "gold": n_gold}


def _ov(a, b) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def leaked(value: str, protected: str) -> bool:
    if value and value in protected:
        return True
    d = _digits(value)
    return len(d) >= 6 and d in _digits(protected)


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[k], 3)


class Scorer:
    def __init__(self):
        self.c = {m: defaultdict(lambda: [0, 0, 0, 0]) for m in ("exact", "overlap")}   # key -> tp_p, pred, tp_g, gold
        self.leak = defaultdict(lambda: [0, 0])          # key -> leaked, total
        self.benign = [0, 0]                              # touched, total
        self.chars = [0, 0]                               # masked outside gold, chars outside gold
        self.lat: list[float] = []
        self.n = 0

    def add(self, text: str, gold: list, pred: list, genre: str, protected: str, benign: list, ms: float) -> None:
        self.n += 1
        self.lat.append(ms)
        keys = lambda t: (("ALL", "*"), (t, "*"), ("ALL", genre), (t, genre))  # noqa: E731
        types = {t for *_, t in gold} | {t for *_, t in pred}
        for t in types:
            g = [x for x in gold if x[2] == t]
            p = [x for x in pred if x[2] == t]
            ex_p = sum(1 for x in p if x in g)
            ov_p = sum(1 for x in p if any(_ov(x, y) for y in g))
            ov_g = sum(1 for y in g if any(_ov(x, y) for x in p))
            for k in keys(t)[1::2]:          # (t, *) and (t, genre)
                self._acc("exact", k, ex_p, len(p), ex_p, len(g))
                self._acc("overlap", k, ov_p, len(p), ov_g, len(g))
        ex_all = sum(1 for x in pred if x in gold)
        ov_p = sum(1 for x in pred if any(_ov(x, y) and x[2] == y[2] for y in gold))
        ov_g = sum(1 for y in gold if any(_ov(x, y) and x[2] == y[2] for x in pred))
        for k in (("ALL", "*"), ("ALL", genre)):
            self._acc("exact", k, ex_all, len(pred), ex_all, len(gold))
            self._acc("overlap", k, ov_p, len(pred), ov_g, len(gold))
        for s, e, t in gold:
            hit = leaked(text[s:e], protected)
            for k in (("ALL", "*"), (t, "*"), ("ALL", genre)):
                self.leak[k][0] += hit
                self.leak[k][1] += 1
        for b in benign:
            self.benign[1] += 1
            self.benign[0] += any(_ov(b, x) for x in pred)
        in_gold = [False] * len(text)
        for s, e, _ in gold:
            in_gold[s:e] = [True] * (e - s)
        masked = [False] * len(text)
        for s, e, _ in pred:
            masked[s:e] = [True] * (e - s)
        self.chars[1] += in_gold.count(False)
        self.chars[0] += sum(1 for i in range(len(text)) if masked[i] and not in_gold[i])

    def _acc(self, mode, key, tp_p, n_p, tp_g, n_g):
        c = self.c[mode][key]
        c[0] += tp_p
        c[1] += n_p
        c[2] += tp_g
        c[3] += n_g

    def result(self) -> dict:
        def table(mode, by):
            out = {}
            for (t, g), v in sorted(self.c[mode].items()):
                if (by == "type" and g == "*" and t != "ALL") or (by == "genre" and t == "ALL" and g != "*"):
                    out[t if by == "type" else g] = _prf(*v)
            return out
        return {
            "examples": self.n,
            "exact": {"overall": _prf(*self.c["exact"][("ALL", "*")]), "by_type": table("exact", "type"),
                      "by_genre": table("exact", "genre")},
            "overlap": {"overall": _prf(*self.c["overlap"][("ALL", "*")]), "by_type": table("overlap", "type"),
                        "by_genre": table("overlap", "genre")},
            "leak_rate": {"overall": self._rate(("ALL", "*")),
                          "by_type": {t: self._rate((t, "*")) for t, g in sorted(self.leak) if g == "*" and t != "ALL"},
                          "by_genre": {g: self._rate(("ALL", g)) for t, g in sorted(self.leak)
                                       if t == "ALL" and g != "*"}},
            "over_masking": {"benign_spans_masked": round(self.benign[0] / self.benign[1], 4) if self.benign[1]
                             else None, "benign_spans": self.benign[1],
                             "non_pii_chars_masked": round(self.chars[0] / self.chars[1], 5) if self.chars[1] else 0.0},
            "latency_ms": {"p50": percentile(self.lat, 0.5), "p95": percentile(self.lat, 0.95),
                           "max": round(max(self.lat), 3) if self.lat else 0.0},
        }

    def _rate(self, k) -> dict:
        lk, tot = self.leak.get(k, [0, 0])
        return {"rate": round(lk / tot, 4) if tot else 0.0, "leaked": lk, "values": tot}
