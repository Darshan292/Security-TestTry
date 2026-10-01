"""Evaluate pre-registered gates G1-G6 (EVALUATION_PLAN amendment 2026-10-01) from run_v3 result files.

    python -m eval.v3_gates            # needs v3_{A,B,C}_test3_adv-all.json and v3_{A,B,C}_dev3build.json
dev3 in the generalisation gap and in G6 means dev3build (the split development was done on).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

R = Path(__file__).resolve().parent / "results"


def ld(arm, split):
    for name in (f"v3_{arm}_{split}_adv-all.json", f"v3_{arm}_{split}.json"):
        if (R / name).exists():
            return json.loads((R / name).read_text())
    raise SystemExit(f"missing result for arm {arm} on {split}")


def bootstrap_diff(fa, fb, n=10_000, seed=0):
    fams = sorted(fa)
    rng = random.Random(seed)
    diffs = []
    for _ in range(n):
        s = [rng.choice(fams) for _ in fams]
        docs = sum(fa[f]["docs"] for f in s)
        diffs.append((sum(fb[f].get("csve_docs", 0) for f in s) - sum(fa[f].get("csve_docs", 0) for f in s)) / docs)
    diffs.sort()
    return diffs[int(0.025 * n)], diffs[int(0.975 * n) - 1]


def smoothed(c):
    return (c["csve"] * c["docs"] + 1) / (c["docs"] + 1)


def main():
    t = {a: ld(a, "test3") for a in "ABC"}
    d = {a: ld(a, "dev3build") for a in "ABC"}
    A, B, C = (t[a]["clean"] for a in "ABC")
    lo, hi = bootstrap_diff(A["per_family"], B["per_family"])
    g = {}
    g["G1"] = {"pass": B["csve"] <= 0.5 * A["csve"] and hi < 0, "csve_A": A["csve"], "csve_B": B["csve"],
               "diff_ci95": [lo, hi]}
    g["G2"] = {"pass": B["neg_fp_item_rate"] <= 1.5 * A["neg_fp_item_rate"] and
               B["collateral_masking"] <= 2 * A["collateral_masking"] and B["collateral_masking"] <= 0.05,
               "negfp_A": A["neg_fp_item_rate"], "negfp_B": B["neg_fp_item_rate"],
               "collateral_A": A["collateral_masking"], "collateral_B": B["collateral_masking"]}
    gaps = {a: smoothed(t[a]["clean"]) / smoothed(d[a]["clean"]) for a in "ABC"}
    g["G3"] = {"pass": gaps["B"] <= 2.0, "gap": gaps}
    adv = {k: (t["A"]["adversarial"][k]["csve"], t["B"]["adversarial"][k]["csve"])
           for k, v in t["B"].get("adversarial", {}).items() if v["cls"] == "heldout" and v.get("docs")}
    na = sorted(k for k, v in t["B"].get("adversarial", {}).items() if v["cls"] == "heldout" and not v.get("docs"))
    g["G4"] = {"pass": bool(adv) and all(b <= a + 0.02 for a, b in adv.values()), "heldout_csve_A_B": adv, "not_applicable_on_test3": na}
    g["G5"] = {"pass": B["latency_ms"]["p95"] <= 250, "p95_B": B["latency_ms"]["p95"]}
    d_fp_test = C["neg_fp_item_rate"] - B["neg_fp_item_rate"]
    d_fp_dev = d["C"]["clean"]["neg_fp_item_rate"] - d["B"]["clean"]["neg_fp_item_rate"]
    a_ok = abs(C["csve"] - B["csve"]) <= 0.02
    b_ok = d_fp_dev > 0 and d_fp_test >= 0.5 * d_fp_dev
    g["G6"] = {"pass": a_ok and b_ok, "a_recall_independent": a_ok, "b_precision_transfers": b_ok,
               "csve_C_minus_B_test": C["csve"] - B["csve"], "dFP_test": d_fp_test, "dFP_dev": d_fp_dev,
               "collateral_C_minus_B_test": C["collateral_masking"] - B["collateral_masking"],
               "verdict": ("L3 generalises" if a_ok and b_ok else
                           "PD-13 central claim refuted: recall depends on L3" if not a_ok else
                           "L3 has no measurable dev benefit" if d_fp_dev <= 0 else "L3 is vocabulary overfitting")}
    g["adopt_PD13"] = all(g[k]["pass"] for k in ("G1", "G2", "G3", "G4", "G5", "G6"))
    (R / "v3_gates.json").write_text(json.dumps(g, indent=1))
    print(json.dumps(g, indent=1))


if __name__ == "__main__":
    main()
