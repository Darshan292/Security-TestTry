"""Post-hoc analysis (CONTAMINATION C7, deviation D-2): L3 dictionary decisions by entry provenance.

    python -m eval.v3_l3_provenance --data eval/data3/dev3build.jsonl
    python -m eval.v3_l3_provenance --data <unsealed test3.jsonl>      # only after all arms' one-shot locks exist
For each dictionary match: is the field a gold value of the same type (typed correctly), another type, or no
gold; for RELEASE matches: does the released field overlap gold (a wrong release) or not. Aggregates only.
"""
import argparse, collections, datetime, json
from pathlib import Path

from eval.run_det import CTX, load
from pgw.pd13 import build_protector
from pgw.pd13 import semantics as S
from pgw.pd13.semantics import RELEASE_CONCEPTS

LOG: list = []
_orig = S.Semantics.analyse


def _wrapped(self, text, graph, shapes, person_spans):
    res = _orig(self, text, graph, shapes, person_spans)
    for f in graph.fields:
        e = self.dictionary.lookup(f.path, f.key)
        if e is not None:
            LOG.append((e.provenance.split(":", 1)[0], e.concept, f.start, f.end))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    results = Path(__file__).resolve().parent / "results"
    if "test3" in a.data and len(list(results.glob("LOCK_v3_*_test3.json"))) < 3:
        raise SystemExit("refusing: test3 analysis only after all three one-shot runs are locked")
    S.Semantics.analyse = _wrapped
    prot = build_protector(Path("eval/data3"), ner="en_core_web_lg", l3=True, today=datetime.date(2026, 10, 1))
    c = collections.Counter()
    for it in load(Path(a.data)):
        LOG.clear()
        prot.analyze([{"role": "user", "content": it["text"]}], CTX)
        seen = set()
        for prov, concept, s, e in LOG:
            if (prov, concept, s, e) in seen:
                continue
            seen.add((prov, concept, s, e))
            gold = [g for g in it["spans"] if g["start"] < e and g["end"] > s]
            if concept in RELEASE_CONCEPTS:
                c[(prov, "release", "WRONG_overlaps_gold" if gold else "correct")] += 1
            else:
                c[(prov, "type", "same_type_gold" if any(g["type"] == concept for g in gold)
                   else "other_gold" if gold else "no_gold")] += 1
    out = {" | ".join(k): v for k, v in sorted(c.items())}
    print(json.dumps(out, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
