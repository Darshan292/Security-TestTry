"""E-DET / E-ADV / E-PERF / E-ROUNDTRIP runner over the synthetic gold corpus.

    python -m eval.run_det --split test [--adversarial] [--no-roster]

Gold spans are compared with the pipeline's final resolved spans (after D5).
Metrics per EVALUATION_PLAN.md: per-type overlap precision/recall with Wilson 95 %
intervals and minimum-n power check, PERSON split by name source, character leak
rate, false positives on negatives, latency percentiles, stream round-trip.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

from pgw.detect.edm import RosterIndex
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack
from pgw.egress import check as egress_check
from pgw.stream import Restorer
from pgw.types import EgressViolation
from pgw.types import Context, EntityType

ROOT = Path(__file__).resolve().parent
CTX = Context("eval", "eval-harness", "eval_tokenize_all", conversation_id=None, restore_output=True)

# Pre-registered gates (EVALUATION_PLAN.md, E-DET). (min recall, min precision, min positives in test)
HIGH = ["AADHAAR", "PAN", "BANK_ACCOUNT", "CARD", "SECRET", "UPI_ID"]
GATES = {**{t: (0.98, 0.90, 150) for t in HIGH},
         "EMAIL": (0.98, 0.95, 150), "PHONE": (0.98, 0.95, 150),
         "EMPLOYEE_ID": (0.98, 0.95, 150), "COMPENSATION": (0.95, 0.85, 200),
         "PERSON": (0.90, 0.85, 400)}
LEAK_GATE = 0.01


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


def overlaps(a, b) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def build_protector(data: Path, use_roster: bool, ner: str | None = None) -> Protector:
    keys = Keys(bytes(32))
    roster = None
    if use_roster and (data / "roster.jsonl").exists():
        roster = RosterIndex(keys.derive("eval", "edm"))
        for rec in load(data / "roster.jsonl"):
            roster.add_employee(rec)
    ner_det = None
    if ner:
        from pgw.detect.ner import NerDetector
        ner_det = NerDetector(ner)
    return Protector(PolicyPack.load(ROOT.parent / "policies/hris-default.yaml"), keys, roster, ner=ner_det)


def is_heldout(split: str) -> bool:
    return split == "test" or split.startswith("test") or split.startswith("ner_test")


def guard_split(split: str, one_shot: bool, out: Path, data: Path, tag: str = "det") -> None:
    """Held-out splits are evaluated once per release, on a clean commit; the lock records both hashes."""
    if not is_heldout(split):
        return
    lock = out / ("TEST_LOCK.json" if (split == "test" and tag == "det") else f"LOCK_{tag}_{split}.json")
    if not one_shot:
        raise SystemExit(f"refusing: {split} is held out; pass --one-shot-test deliberately")
    if lock.exists():
        raise SystemExit(f"refusing: {split} already evaluated for {tag} ({lock.read_text().strip()[:200]})")
    import hashlib
    import subprocess
    dirty = subprocess.run(["git", "status", "--porcelain", "pgw", "policies", "eval/run_det.py", "eval/run_stream.py"],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        raise SystemExit("refusing: code under test has uncommitted changes; the held-out run must be tied to a commit")
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    file_sha = hashlib.sha256((data / f"{split}.jsonl").read_bytes()).hexdigest()
    manifest = json.loads((data / "MANIFEST.json").read_text())
    listed = json.dumps(manifest)
    if file_sha not in listed:
        raise SystemExit(f"refusing: {split}.jsonl sha256 {file_sha[:16]} is not in MANIFEST.json (corpus changed?)")
    out.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"split": split, "tag": tag, "code_commit": head, "split_sha256": file_sha,
                                "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1))


def evaluate(items: list[dict], prot: Protector, stream_check: bool = True, seed: int = 0) -> dict:
    tp_r = defaultdict(int); n_gold = defaultdict(int)
    tp_p = defaultdict(int); n_pred = defaultdict(int)
    person_src = defaultdict(lambda: [0, 0])          # name_source -> [hit, total]
    gold_chars = leaked_chars = 0
    neg_fp = defaultdict(int); neg_items = neg_items_fp = 0
    lat_ms: list[float] = []
    rt_ok = rt_ci = rt_n = 0
    docs = docs_exposed = egress_fires = egress_true = 0
    misses: dict[str, list[str]] = defaultdict(list)
    rng = random.Random(seed)

    for it in items:
        text = it["text"]
        msgs = [{"role": "user", "content": text}]
        t0 = time.perf_counter()
        pred = prot.analyze(msgs, CTX)[0]
        lat_ms.append((time.perf_counter() - t0) * 1000)
        gold = [(g["start"], g["end"], g["type"]) for g in it["spans"]]
        preds = [(d.start, d.end, d.type.value) for d in pred]

        for g in gold:
            n_gold[g[2]] += 1
            hit = any(overlaps(g, p) and p[2] == g[2] for p in preds)
            tp_r[g[2]] += hit
            if g[2] == "PERSON":
                src = it.get("name_source") or "unknown"
                person_src[src][0] += hit; person_src[src][1] += 1
            if not hit and len(misses[g[2]]) < 8:
                misses[g[2]].append(text[max(0, g[0] - 30):g[1] + 30].replace("\n", " "))
            covered = set()
            for p in preds:
                covered.update(range(max(g[0], p[0]), min(g[1], p[1])))
            gold_chars += g[1] - g[0]
            leaked_chars += (g[1] - g[0]) - len(covered)
        for p in preds:
            n_pred[p[2]] += 1
            tp_p[p[2]] += any(overlaps(g, p) and p[2] == g[2] for g in gold)
        if it.get("slice") == "negatives":
            neg_items += 1
            fps = [p for p in preds if not any(overlaps(g, p) for g in gold)]
            neg_items_fp += bool(fps)
            for p in fps:
                neg_fp[p[2]] += 1

        # document exposure + egress invariant on the replayed outbound body (R2 metrics)
        pr0 = prot.protect(msgs, CTX)
        out0 = pr0.messages[0]["content"]
        docs += 1
        docs_exposed += any(text[g[0]:g[1]] in out0 for g in gold if g[1] - g[0] >= 4)
        try:
            egress_check(json.dumps({"model": "m", "messages": pr0.messages}).encode(), pr0.sensitive_values)
        except EgressViolation:
            egress_fires += 1
            egress_true += any(v in out0 for _, v in pr0.sensitive_values)   # a detected value really is present
        if stream_check:
            pr = pr0
            aliased = pr.messages[0]["content"]
            r = Restorer(pr.alias_map)
            cuts = sorted(rng.sample(range(1, len(aliased)), k=min(len(aliased) - 1, rng.randint(1, 30)))) if len(aliased) > 2 else []
            out, prev = [], 0
            for c in cuts + [len(aliased)]:
                out.append(r.feed(aliased[prev:c])); prev = c
            out.append(r.finish())
            restored = "".join(out)
            rt_n += 1
            rt_ok += restored == text
            rt_ci += restored.casefold() == text.casefold()

    types = sorted(set(n_gold) | set(n_pred))
    per_type = {}
    for t in types:
        rec = tp_r[t] / n_gold[t] if n_gold[t] else None
        prec = tp_p[t] / n_pred[t] if n_pred[t] else None
        rlo, rhi = wilson(tp_r[t], n_gold[t])
        gate = GATES.get(t)
        status = "no gate"
        if gate:
            if n_gold[t] < gate[2]:
                status = "UNDERPOWERED"
            elif rec is not None and rec >= gate[0] and (prec or 0) >= gate[1] and rlo >= gate[0] - 0.03:
                status = "PASS"
            else:
                status = "FAIL"
        per_type[t] = {"n_gold": n_gold[t], "recall": rec, "recall_ci95": [rlo, rhi],
                       "n_pred": n_pred[t], "precision": prec, "gate": gate, "status": status,
                       "sample_misses": misses.get(t, [])}
    q = statistics.quantiles(lat_ms, n=100) if len(lat_ms) >= 100 else [max(lat_ms)] * 99
    return {
        "items": len(items),
        "per_type": per_type,
        "person_by_source": {k: {"recall": v[0] / v[1] if v[1] else None, "n": v[1],
                                 "ci95": wilson(v[0], v[1])} for k, v in person_src.items()},
        "leak_rate_chars": leaked_chars / gold_chars if gold_chars else 0.0,
        "leak_gate": LEAK_GATE,
        "negatives": {"items": neg_items, "items_with_fp": neg_items_fp, "fp_by_type": dict(neg_fp)},
        "latency_ms": {"p50": q[49], "p95": q[94], "p99": q[98], "max": max(lat_ms)},
        "stream_roundtrip": {"n": rt_n, "exact": rt_ok, "case_insensitive": rt_ci},
        "document_exposure": {"docs": docs, "docs_with_raw_gold_value_in_body": docs_exposed,
                              "rate": docs_exposed / docs if docs else 0.0},
        "egress_invariant": {"fires": egress_fires, "true_catches": egress_true,
                             "false_alarms": egress_fires - egress_true},
    }


def render(res: dict, title: str) -> str:
    L = [f"## {title}", "", f"Items: {res['items']}", "",
         "| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |", "|---|---:|---:|---|---:|---|---|"]
    f = lambda x: "—" if x is None else f"{x:.3f}"
    for t, r in sorted(res["per_type"].items()):
        g = r["gate"]
        L.append(f"| {t} | {r['n_gold']} | {f(r['recall'])} | {r['recall_ci95'][0]:.3f}–{r['recall_ci95'][1]:.3f} | "
                 f"{f(r['precision'])} | {'—' if not g else f'{g[0]}/{g[1]}/{g[2]}'} | {r['status']} |")
    L += ["", "PERSON recall by name source: " + ", ".join(
        f"{k} {f(v['recall'])} (n={v['n']}, CI {v['ci95'][0]:.3f}–{v['ci95'][1]:.3f})"
        for k, v in sorted(res["person_by_source"].items())),
        f"Character leak rate: **{res['leak_rate_chars']:.4f}** (gate ≤ {res['leak_gate']})",
        f"Negatives: {res['negatives']['items_with_fp']}/{res['negatives']['items']} items with ≥1 false positive; by type {res['negatives']['fp_by_type']}",
        f"Latency (detect only, CPU, ms): p50 {res['latency_ms']['p50']:.2f} · p95 {res['latency_ms']['p95']:.2f} · p99 {res['latency_ms']['p99']:.2f} · max {res['latency_ms']['max']:.1f}",
        f"Stream round-trip (random chunking): exact {res['stream_roundtrip']['exact']}/{res['stream_roundtrip']['n']}, "
        f"case-insensitive {res['stream_roundtrip']['case_insensitive']}/{res['stream_roundtrip']['n']}",
        f"Document exposure (>= 1 raw gold value in outbound body): **{res['document_exposure']['rate']:.2%}** "
        f"({res['document_exposure']['docs_with_raw_gold_value_in_body']}/{res['document_exposure']['docs']})",
        f"Egress invariant: fires {res['egress_invariant']['fires']}, true catches {res['egress_invariant']['true_catches']}, "
        f"false alarms {res['egress_invariant']['false_alarms']} (gate: 0 false alarms; never a recall metric)", ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--split", default="dev")
    ap.add_argument("--one-shot-test", action="store_true",
                    help="required to evaluate the test split; allowed exactly once (eval/results/TEST_LOCK.json)")
    ap.add_argument("--adversarial", action="store_true")
    ap.add_argument("--no-roster", action="store_true")
    ap.add_argument("--ner", default=None, help="spaCy model name for the D4 NER layer")
    ap.add_argument("--heldout-adversarial", action="store_true",
                    help="also run eval.synth2.adversarial_heldout kinds (one-shot runs only)")
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args()
    data = Path(a.data)
    guard_split(a.split, a.one_shot_test, Path(a.out), data)
    if a.heldout_adversarial and not a.one_shot_test:
        raise SystemExit("refusing: held-out adversarial transforms run only inside the one-shot held-out evaluation")
    items = load(data / f"{a.split}.jsonl")
    prot = build_protector(data, not a.no_roster, ner=a.ner)
    tag = f"{a.split}{'-noroster' if a.no_roster else ''}{'-ner_' + a.ner if a.ner else ''}"
    results = {"clean": evaluate(items, prot)}
    md = [render(results["clean"], f"E-DET — {tag}")]
    if a.adversarial:
        from eval.synth.adversarial import transform
        rng = random.Random(99)
        for kind in ("homoglyph", "zero_width", "spaced", "obfuscated_email", "case", "nbsp"):
            adv = [transform(it, kind, rng) for it in items if it.get("slice") != "negatives"]
            results[kind] = evaluate(adv, prot, stream_check=False)
            md.append(render(results[kind], f"E-ADV — {kind}"))
    if a.heldout_adversarial:
        from eval.synth2.adversarial_heldout import KINDS, transform as ho_transform
        rng = random.Random(1234)
        for kind in KINDS:
            adv = [ho_transform(it, kind, rng) for it in items if it.get("slice") != "negatives"]
            results[f"heldout:{kind}"] = evaluate(adv, prot, stream_check=False)
            md.append(render(results[f"heldout:{kind}"], f"E-ADV held-out — {kind}"))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / f"det_{tag}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    (out / f"det_{tag}.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
