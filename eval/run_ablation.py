"""Component ablation study (measurement only). Writes eval/results/ablation_r2.{json,md}.

    python -m eval.run_ablation [--workers 4] [--repro-only]

Configs are expressed as pgw.ablation switches (+ whether a roster / NER model is attached).
D2-off removes the pattern recognisers in detect_d2 (email, upi, phone, aadhaar, pan, ifsc, card, bank,
secret, employee_id, compensation, dob, bank_file_names). D1-off drops the field-scan candidates in detect_d2
(JSON/XML/CSV/kv/SQL scanners + the recognizers-internal person cues that live inside the field scan) and the
pipeline's JSON _field_values step; the CSV negative-span suppression of D2 hits is kept. Held-out splits are
analysed read-only; nothing here tunes anything.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from multiprocessing import Pool
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from pgw import ablation  # noqa: E402
from pgw.ablation import AblationConfig  # noqa: E402
from eval.run_det import build_protector, evaluate, load  # noqa: E402

DATA = ROOT / "data2"
NER = "en_core_web_lg"
DATASETS = ["dev2", "test2", "ner_test2"]

# name -> (switches off, roster attached, ner attached)
CONFIGS = {
    "FULL_B": ((), True, True),
    "B-D0": (("D0",), True, True),
    "B-D1": (("D1",), True, True),
    "B-D2": (("D2",), True, True),
    "B-CUES": (("CUES",), True, True),
    "B-D3": (("D3",), False, True),
    "B-D4": (("D4",), True, False),
    "B-D5": (("D5",), True, True),
    "ENTITY_ONLY": (("D1", "D2", "CUES"), True, True),
    "RULES_ONLY": (("D3", "D4"), True, False),
    "D4_ONLY": (("D0", "D1", "D2", "CUES", "D3", "D5"), False, True),
    "FULL_A": ((), True, False),          # reproduction check only
}
ADV_CONFIGS = ["FULL_B", "B-D0", "ENTITY_ONLY"]
R1_KINDS = ("homoglyph", "zero_width", "spaced", "obfuscated_email", "case", "nbsp")
COLS = ["PERSON", "AADHAAR", "DATE_OF_BIRTH", "SECRET", "COMPENSATION", "BANK_ACCOUNT"]

_prot: dict = {}


def protector(name: str):
    off, roster, ner = CONFIGS[name]
    key = (roster, ner)
    if key not in _prot:
        _prot[key] = build_protector(DATA, roster, ner=NER if ner else None)
    return _prot[key]


def cfg_of(name: str) -> AblationConfig:
    return AblationConfig().without(*CONFIGS[name][0])


def slim(res: dict) -> dict:
    res = json.loads(json.dumps(res, default=str))
    for r in res["per_type"].values():
        r.pop("sample_misses", None)
    return res


def run_clean(task):
    name, ds = task
    items = load(DATA / f"{ds}.jsonl")
    with ablation.use(cfg_of(name)):
        res = evaluate(items, protector(name), stream_check=False)
    return ("clean", name, ds, slim(res))


def adv_sample():
    nonneg = [it for it in load(DATA / "test2.jsonl") if it.get("slice") != "negatives"]
    return random.Random(7).sample(nonneg, 400)


def run_adv(task):
    name, kind = task
    from eval.synth.adversarial import transform as t1
    from eval.synth2.adversarial_heldout import transform as t2
    sample = adv_sample()
    if kind in R1_KINDS:
        rng = random.Random(99); items = [t1(it, kind, rng) for it in sample]
    else:
        rng = random.Random(1234); items = [t2(it, kind, rng) for it in sample]
    with ablation.use(cfg_of(name)):
        res = evaluate(items, protector(name), stream_check=False)
    return ("adv", name, kind, slim(res))


def fmt(x, p=1):
    return "—" if x is None else f"{100 * x:.{p}f}"


def row(name, r):
    pt = r["per_type"]
    g = lambda t, k: (pt.get(t) or {}).get(k)
    de = r["document_exposure"]
    neg = r["negatives"]
    cells = [f"{100 * r['leak_rate_chars']:.2f}", f"{de['docs_with_raw_gold_value_in_body']}/{de['docs']} ({100 * de['rate']:.1f})",
             f"{fmt(g('PERSON', 'recall'))}/{fmt(g('PERSON', 'precision'))}"]
    cells += [fmt(g(t, "recall")) for t in COLS[1:]]
    cells += [f"{neg['items_with_fp']}/{neg['items']}" if neg["items"] else "n/a", f"{r['latency_ms']['p95']:.1f}"]
    return f"| {name} | " + " | ".join(cells) + " |"


def metrics(r):
    pt = r["per_type"]
    m = {"leak_pp": 100 * r["leak_rate_chars"], "exposed_docs": r["document_exposure"]["docs_with_raw_gold_value_in_body"],
         "exposure_pp": 100 * r["document_exposure"]["rate"]}
    for t in COLS:
        for k in ("recall", "precision"):
            v = (pt.get(t) or {}).get(k)
            if k == "recall" or t == "PERSON":
                m[f"{t}_{k[0].upper()}_pp"] = None if v is None else 100 * v
    m["neg_fp_items"] = r["negatives"]["items_with_fp"]
    return m


def write_md(R, adv, path):
    L = ["# Component ablation, R2 (measurement only)", "",
         "Configs: FULL_B = D0+D1+D2+CUES+D3+D4(en_core_web_lg)+D5. Leave-one-out `B-X`. ENTITY_ONLY = D0+D3+D4+D5; "
         "RULES_ONLY = D0+D1+D2+CUES+D5; D4_ONLY = D4 alone (base normalisation only). Recall/precision in %, overlap definition of eval/run_det.py. "
         "Latency measured with 4 configs running in parallel processes (OMP_NUM_THREADS=1), so absolute ms are inflated vs the frozen serial figures; compare relatively.", "",
         "D2-off removes all pattern recognisers in detect_d2 (EMAIL, UPI, PHONE, AADHAAR, PAN, IFSC, CARD, BANK, SECRET, EMPLOYEE_ID, COMPENSATION, DOB, bank-file-names). "
         "D1-off removes the field-scan candidates (JSON/XML/CSV/kv/SQL, plus the person cues inside the field scan) and the pipeline JSON field-value step; CSV negative-span suppression of D2 stays. "
         "D4_ONLY also turns D0 off (D0 is not in that set).", ""]
    hdr = ("| Config | char leak % | doc exposure | PERSON R/P | AADHAAR R | DOB R | SECRET R | COMPENSATION R | BANK R | neg items w/ FP | p95 ms |\n"
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    names = [n for n in CONFIGS if n != "FULL_A"]
    for ds in DATASETS:
        L += [f"## {ds}", "", hdr] + [row(n, R[n][ds]) for n in names] + [""]
    L += ["## Marginal contribution of each component (metric(B-X) - metric(FULL_B)); positive leak/exposure = component helps", ""]
    keys = [("leak_pp", "leak pp"), ("exposed_docs", "exposed docs"), ("PERSON_R_pp", "PERSON R pp"), ("PERSON_P_pp", "PERSON P pp"),
            ("AADHAAR_R_pp", "AADHAAR R pp"), ("DATE_OF_BIRTH_R_pp", "DOB R pp"), ("SECRET_R_pp", "SECRET R pp"),
            ("COMPENSATION_R_pp", "COMP R pp"), ("BANK_ACCOUNT_R_pp", "BANK R pp"), ("neg_fp_items", "neg FP items")]
    for ds in DATASETS:
        L += [f"### {ds}", "", "| Removed | " + " | ".join(k[1] for k in keys) + " |", "|---|" + "---:|" * len(keys)]
        base = metrics(R["FULL_B"][ds])
        for n in [n for n in names if n.startswith("B-")]:
            m = metrics(R[n][ds])
            cells = []
            for k, _ in keys:
                cells.append("—" if m[k] is None or base[k] is None else f"{m[k] - base[k]:+.2f}")
            L.append(f"| {n[2:]} | " + " | ".join(cells) + " |")
        L.append("")
    if adv:
        kinds = list(R1_KINDS) + [k for k in __import__("eval.synth2.adversarial_heldout", fromlist=["KINDS"]).KINDS]
        L += ["## Adversarial (400 random non-negative test2 items, seed 7): char leak % / docs exposed of 400", "",
              "| Kind | " + " | ".join(ADV_CONFIGS) + " |", "|---|" + "---:|" * len(ADV_CONFIGS)]
        for k in kinds:
            cells = []
            for n in ADV_CONFIGS:
                r = adv[n][k]
                cells.append(f"{100 * r['leak_rate_chars']:.1f} / {r['document_exposure']['docs_with_raw_gold_value_in_body']}")
            L.append(f"| {k}{' (held-out)' if k not in R1_KINDS else ''} | " + " | ".join(cells) + " |")
        L.append("")
    path.write_text("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--repro-only", action="store_true")
    a = ap.parse_args()
    out = ROOT / "results"
    if a.repro_only:
        for t in [("FULL_B", "test2"), ("FULL_A", "test2")]:
            _, n, ds, r = run_clean(t)
            print(n, ds, repr(r["leak_rate_chars"]), r["document_exposure"])
        return
    from eval.synth2.adversarial_heldout import KINDS as HK
    tasks_c = [(n, ds) for ds in ("test2", "dev2", "ner_test2") for n in CONFIGS if not (n == "FULL_A" and ds != "test2")]
    tasks_a = [(n, k) for n in ADV_CONFIGS for k in list(R1_KINDS) + list(HK)]
    R: dict = {}; adv: dict = {}
    with Pool(a.workers) as pool:
        jobs = [pool.apply_async(run_clean, (t,)) for t in tasks_c] + [pool.apply_async(run_adv, (t,)) for t in tasks_a]
        for j in jobs:
            kind, n, k, res = j.get()
            (R if kind == "clean" else adv).setdefault(n, {})[k] = res
            print("done", kind, n, k, flush=True)
    out.mkdir(exist_ok=True)
    (out / "ablation_r2.json").write_text(json.dumps({"clean": R, "adversarial": adv,
        "configs": {n: {"off": list(v[0]), "roster": v[1], "ner": NER if v[2] else None} for n, v in CONFIGS.items()}}, indent=1))
    write_md(R, adv, out / "ablation_r2.md")


if __name__ == "__main__":
    main()
