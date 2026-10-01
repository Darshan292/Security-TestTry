"""Post-hoc, aggregate-only analysis of the FROZEN test3 results (no tuning): which entity types make
documents CSVE-exposed, per arm. Refuses to run unless all three arms' one-shot locks exist.

    python -m eval.v3_posthoc --data <unsealed test3.jsonl> --arms A B C
"""
import argparse, collections, json
from pathlib import Path
from eval.run_det import CTX, load
from eval.run_v3 import TAXONOMY, _fold, complete_exposed, make_protector, DATA3, RESULTS


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--arms", nargs="+", default=["A", "B", "C"])
    a = ap.parse_args()
    if len(list(RESULTS.glob("LOCK_v3_?_all_test3.json"))) < 3:
        raise SystemExit("refusing: post-hoc analysis only after all three one-shot runs are locked")
    items = load(Path(a.data)); out = {}
    for arm in a.arms:
        prot = make_protector(arm, DATA3)
        vals = collections.Counter(); exp = collections.Counter(); only = collections.Counter(); ndocs = 0
        for it in items:
            t = it["text"]; o = prot.protect([{"role": "user", "content": t}], CTX).messages[0]["content"]; of = _fold(o)
            types = set()
            for g in it["spans"]:
                if g["type"] not in TAXONOMY: continue
                v = t[g["start"]:g["end"]]; vals[g["type"]] += 1
                if len(v) >= 4 and complete_exposed(v, g["type"], o, of):
                    exp[g["type"]] += 1; types.add(g["type"])
            if types:
                ndocs += 1
                only["+".join(sorted(types)) if len(types) <= 2 else "3+ types"] += 1
        out[arm] = {"exposed_docs": ndocs, "values_exposed_by_type": {k: [exp[k], vals[k], round(exp[k] / vals[k], 3)] for k in sorted(vals)},
                    "exposed_doc_type_combos": dict(only.most_common(12))}
        print(arm, json.dumps(out[arm]))
    Path(RESULTS / "v3_posthoc_test3.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
