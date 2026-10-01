"""Release-3 harness on corpus v3 (EVALUATION_PLAN amendment 2026-10-01). Aggregates only: no item text is written.

    python -m eval.run_v3 --arm A --split dev3build
    python -m eval.run_v3 --arm B --split dev3val            # logged, at most 5 runs (SEPARATION.md)
    python -m eval.run_v3 --arm A --split test3 --one-shot-test --test-data /path/to/test3.jsonl

Arms: A = R2 Config B (current architecture); B = PD-13 minimum; C = PD-13 without L3.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import re
import statistics
import subprocess
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

from eval.run_det import CTX, build_protector, load, overlaps, wilson

ROOT = Path(__file__).resolve().parent
DATA3 = ROOT / "data3"
RESULTS = ROOT / "results"
NER = "en_core_web_lg"
HIGH = {"AADHAAR", "PAN", "BANK_ACCOUNT", "CARD", "SECRET", "DATE_OF_BIRTH", "COMPENSATION"}
DIGIT_TYPES = {"AADHAAR", "PAN", "BANK_ACCOUNT", "CARD", "PHONE", "EMPLOYEE_ID", "COMPENSATION", "DATE_OF_BIRTH"}
TAXONOMY = {"PERSON", "EMAIL", "PHONE", "AADHAAR", "PAN", "BANK_ACCOUNT", "IFSC", "CARD", "UPI_ID", "SECRET",
            "EMPLOYEE_ID", "COMPENSATION", "DATE_OF_BIRTH"}
DEVVAL_MAX_RUNS = 5


def make_protector(arm: str, data: Path):
    if arm == "A":
        return build_protector(data, True, ner=NER)
    import datetime
    from pgw.pd13 import build_protector as pd13_build      # implemented after the corpus freeze
    # L3's DOB-window reference date is pinned so results do not drift with the wall clock (review fix).
    return pd13_build(data, ner=NER, l3=(arm == "B"), today=datetime.date(2026, 10, 1))


# ----------------------------------------------------------------------------- exposure definitions
def norm_key(k: str) -> str:
    """Case-folded letters, combining marks and digits only (keeps Indic vowel signs)."""
    return "".join(ch for ch in k.casefold() if unicodedata.category(ch)[0] in "LMN")


def _fold(s: str) -> str:
    return re.sub(r"\s+", " ", s.casefold()).strip()


def complete_exposed(value: str, typ: str, out: str, out_fold: str) -> bool:
    """CSVE completeness: verbatim, or digit sequence with only [ -./NBSP] between digits, or case/space-folded."""
    if value in out:
        return True
    if typ in DIGIT_TYPES:
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 4:
            sep = r"[ \-./ ]?"
            rx = r"(?<!\d)" + sep.join(map(re.escape, digits)) + r"(?!\d)"
            if re.search(rx, out):
                return True
        if typ in ("PAN",) and _fold(value) in out_fold:
            return True
        return False
    return _fold(value) in out_fold


def partial_identifying(value: str, typ: str, out: str) -> bool:
    """PIE: >= 8 digits of a >= 12-digit identifier, or >= 50 % of a SECRET, outbound contiguously."""
    if typ == "SECRET":
        k = max(4, (len(value) + 1) // 2)
        return any(value[i:i + k] in out for i in range(0, len(value) - k + 1))
    digits = re.sub(r"\D", "", value)
    if typ in HIGH and len(digits) >= 12:
        outd = re.sub(r"[ \-./ ]", "", out)
        return any(digits[i:i + 8] in outd for i in range(0, len(digits) - 7))
    return False


# ----------------------------------------------------------------------------- evaluation
def new_acc():
    return defaultdict(float)


def evaluate(items: list[dict], prot, seen_keys: set[str] | None = None) -> dict:
    fam = defaultdict(new_acc)
    tp_r = defaultdict(int); n_gold = defaultdict(int); tp_p = defaultdict(int); n_pred = defaultdict(int)
    strata = defaultdict(lambda: [0, 0])          # key seen/unseen/none -> [gold values exposed complete, total]
    lat = []
    for it in items:
        text = it["text"]; f = fam[it["family"]]
        msgs = [{"role": "user", "content": text}]
        t0 = time.perf_counter()
        det = prot.analyze(msgs, CTX)[0]
        lat.append((time.perf_counter() - t0) * 1000)
        preds = [(d.start, d.end, d.type.value) for d in det]
        out = prot.protect(msgs, CTX).messages[0]["content"]
        out_fold = _fold(out)
        gold_all = it["spans"]
        gold = [g for g in gold_all if g["type"] in TAXONOMY]
        oot = [g for g in gold_all if g["type"] == "OOT"]
        f["docs"] += 1
        covered_any = set()
        for p in preds:
            covered_any.update(range(p[0], p[1]))
        gold_chars_set = set()
        for g in gold_all:
            gold_chars_set.update(range(g["start"], g["end"]))
        exposed = exposed_high = pie = False
        for g in gold:
            s, e, t = g["start"], g["end"], g["type"]
            n_gold[t] += 1
            tp_r[t] += any(overlaps((s, e, t), p) and p[2] == t for p in preds)
            cov = len(set(range(s, e)) & covered_any)
            f["gold_chars"] += e - s; f["leaked_chars"] += (e - s) - cov
            v = text[s:e]
            comp = len(v) >= 4 and complete_exposed(v, t, out, out_fold)
            exposed |= comp
            if t in HIGH:
                exposed_high |= comp
                pie |= (not comp) and partial_identifying(v, t, out)
            k = g.get("key")
            stratum = "none" if not k else ("seen" if seen_keys is not None and norm_key(k) in seen_keys else "unseen")
            strata[stratum][0] += comp; strata[stratum][1] += 1
        for p in preds:
            n_pred[p[2]] += 1
            tp_p[p[2]] += any(overlaps((g["start"], g["end"], g["type"]), p) and p[2] == g["type"] for g in gold)
        f["csve_docs"] += exposed; f["csve_high_docs"] += exposed_high; f["pie_docs"] += pie
        if oot:
            f["oot_docs"] += 1
            f["oot_exposed_docs"] += any(len(text[g["start"]:g["end"]]) >= 4 and
                                         complete_exposed(text[g["start"]:g["end"]], "OOT", out, out_fold) for g in oot)
        if it.get("slice") == "negative":
            f["neg_items"] += 1; f["neg_chars"] += len(text)
            fps = [p for p in preds if not any(overlaps((g["start"], g["end"], g["type"]), p) for g in gold_all)]
            f["neg_fp_items"] += bool(fps); f["neg_fp_spans"] += len(fps)
        else:
            nongold = len(text) - len(gold_chars_set)
            f["nongold_chars"] += nongold
            f["collateral_chars"] += len(covered_any - gold_chars_set)
    return summarise(fam, tp_r, n_gold, tp_p, n_pred, strata, lat)


def summarise(fam, tp_r, n_gold, tp_p, n_pred, strata, lat) -> dict:
    tot = defaultdict(float)
    for f in fam.values():
        for k, v in f.items():
            tot[k] += v
    pos_docs = tot["docs"] - tot["neg_items"]
    r = lambda a, b: a / b if b else None
    if not lat:                       # e.g. an adversarial kind that applies to no item
        return {"docs": 0, "csve": None, "csve_high": None, "char_leak": None, "per_family": {}}
    q = statistics.quantiles(lat, n=100) if len(lat) >= 100 else [max(lat)] * 99
    return {
        "docs": int(tot["docs"]),
        "csve": r(tot["csve_docs"], tot["docs"]), "csve_high": r(tot["csve_high_docs"], tot["docs"]),
        "pie": r(tot["pie_docs"], tot["docs"]), "csve_oot": r(tot["oot_exposed_docs"], tot["oot_docs"]),
        "char_leak": r(tot["leaked_chars"], tot["gold_chars"]),
        "char_leak_family_macro": (sum(f["leaked_chars"] / f["gold_chars"] for f in fam.values() if f["gold_chars"]) /
                                   max(1, sum(1 for f in fam.values() if f["gold_chars"]))),
        "neg_fp_item_rate": r(tot["neg_fp_items"], tot["neg_items"]),
        "neg_fp_spans_per_1k_chars": r(1000 * tot["neg_fp_spans"], tot["neg_chars"]),
        "collateral_masking": r(tot["collateral_chars"], tot["nongold_chars"]),
        "positive_docs": int(pos_docs),
        "per_type": {t: {"n_gold": n_gold[t], "recall": r(tp_r[t], n_gold[t]), "recall_ci95": wilson(tp_r[t], n_gold[t]),
                         "n_pred": n_pred[t], "precision": r(tp_p[t], n_pred[t])} for t in sorted(set(n_gold) | set(n_pred))},
        "key_strata": {k: {"exposed": v[0], "n": v[1], "rate": r(v[0], v[1])} for k, v in strata.items()},
        "latency_ms": {"p50": q[49], "p95": q[94], "p99": q[98], "max": max(lat)},
        "per_family": {k: dict(v) for k, v in sorted(fam.items())},
    }


# ----------------------------------------------------------------------------- guards
def git_head_clean() -> str:
    dirty = subprocess.run(["git", "status", "--porcelain", "pgw", "policies", "eval/run_v3.py", "eval/run_det.py"],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        raise SystemExit("refusing: code under test has uncommitted changes")
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def guard(split: str, arm: str, path: Path, one_shot: bool, adv: str | None) -> None:
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = json.loads((DATA3 / "MANIFEST.json").read_text())
    if sha not in json.dumps(manifest):
        raise SystemExit(f"refusing: {path.name} sha256 {sha[:16]} not in eval/data3/MANIFEST.json")
    if split == "test3":
        if not one_shot:
            raise SystemExit("refusing: test3 is held out; pass --one-shot-test deliberately")
        lock = RESULTS / f"LOCK_v3_{arm}{'_' + adv if adv else ''}_test3.json"
        if lock.exists():
            raise SystemExit(f"refusing: test3 already evaluated for arm {arm}: {lock.read_text()[:200]}")
        head = git_head_clean()
        others = [json.loads(p.read_text())["code_commit"] for p in RESULTS.glob("LOCK_v3_*_test3.json")]
        if others and any(c != head for c in others):
            raise SystemExit("refusing: all arms must run from one commit (CONTAMINATION C9)")
        lock.write_text(json.dumps({"split": split, "arm": arm, "adv": adv, "code_commit": head, "split_sha256": sha,
                                    "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1))
    elif split == "dev3val":
        log = RESULTS / "v3_devval_log.jsonl"
        n = len(log.read_text().splitlines()) if log.exists() else 0
        if n >= DEVVAL_MAX_RUNS:
            raise SystemExit(f"refusing: dev3val already read {n} times (max {DEVVAL_MAX_RUNS})")
        with log.open("a") as fh:
            fh.write(json.dumps({"arm": arm, "adv": adv, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                                          text=True).stdout.strip()}) + "\n")


def load_adv(module_path: Path):
    spec = importlib.util.spec_from_file_location("v3_adversarial", module_path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.KINDS


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=("A", "B", "C"), required=True)
    ap.add_argument("--split", choices=("dev3build", "dev3val", "test3"), required=True)
    ap.add_argument("--one-shot-test", action="store_true")
    ap.add_argument("--test-data", help="path to the unsealed test3.jsonl (one-shot only)")
    ap.add_argument("--adv-module", help="adversarial transforms module (KINDS dict)")
    ap.add_argument("--adv-class", choices=("known", "heldout", "all"))
    a = ap.parse_args()
    path = Path(a.test_data) if a.split == "test3" else DATA3 / f"{a.split}.jsonl"
    adv = a.adv_class
    if adv in ("heldout", "all") and a.split != "test3":
        raise SystemExit("refusing: held-out adversarial kinds run on test3 only")
    guard(a.split, a.arm, path, a.one_shot_test, adv)
    items = load(path)
    seen_keys = set(json.loads((DATA3 / "v12_keys.json").read_text())) if (DATA3 / "v12_keys.json").exists() else None
    prot = make_protector(a.arm, DATA3)
    res = {"arm": a.arm, "split": a.split, "clean": evaluate(items, prot, seen_keys)}
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"v3_{a.arm}_{a.split}{'_adv-' + adv if adv else ''}.json"
    if adv:                           # persist the clean result before the long adversarial passes
        out.write_text(json.dumps({**res, "status": "clean_only_adversarial_running"}, indent=1, default=float))
        kinds = {k: v for k, v in load_adv(Path(a.adv_module)).items() if adv == "all" or v["cls"] == adv}
        res["adversarial"] = {}
        for k, spec in kinds.items():
            rng = random.Random(f"v3-adv|{k}")
            tx = [y for y in (spec["fn"](it, rng) for it in items if it.get("slice") != "negative") if y is not None]
            r = evaluate(tx, prot, None)
            res["adversarial"][k] = {"cls": spec["cls"], **{key: r.get(key) for key in ("docs", "csve", "csve_high", "char_leak", "per_family")}}
    out.write_text(json.dumps({**res, "status": "complete"}, indent=1, default=float))
    c = res["clean"]
    print(f"arm {a.arm} {a.split}: docs {c['docs']} CSVE {c['csve']:.4f} CSVE-HIGH {c['csve_high']:.4f} "
          f"char leak {c['char_leak']:.4f} negFP {c['neg_fp_item_rate']} collateral {c['collateral_masking']} "
          f"p95 {c['latency_ms']['p95']:.1f} ms -> {out}")


if __name__ == "__main__":
    main()
