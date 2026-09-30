"""E-UTIL: does the protected prompt still produce equivalent business output?

Paired design (EVALUATION_PLAN.md E-UTIL): the same item, task and model, run once on
RAW text and once through the gateway pipeline (protect -> model -> gate -> restore).
Both arms are scored against gold values that the corpus knows by construction, so
no LLM judge is involved:

  extract   list the people, emails and money amounts -> recall/precision vs gold spans
  classify  pick the document type from the template families -> accuracy

Also reported for the protected arm: fabricated aliases, raw PII the gate had to mask,
and aliases the model mangled beyond the lenient forms.

    ANTHROPIC_API_KEY=... python -m eval.run_util --split dev --n 100 [--model claude-opus-5-5]

It costs money: n items x 2 arms x 2 tasks calls. The estimate is printed before anything runs.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from pydantic import BaseModel

from eval.run_det import ROOT, build_protector, load
from pgw.stream import Restorer
from pgw.types import BlockedRequest, Context

CTX = Context("eval", "util-harness", "hr_case_summary", conversation_id=None, restore_output=True)
SYSTEM = "You are an HR operations assistant. Follow the instructions exactly."


class Extraction(BaseModel):
    people: list[str]
    emails: list[str]
    amounts: list[str]


class Label(BaseModel):
    label: str


def _norm(s: str) -> str:
    return " ".join(s.casefold().replace("₹", "").replace("rs.", "").replace(",", "").split())


def _prf(pred: set[str], gold: set[str]) -> tuple[int, int, int]:
    return len(pred & gold), len(pred), len(gold)


def call(client, model: str, prompt: str, schema):
    resp = client.messages.parse(model=model, max_tokens=2000, system=SYSTEM,
                                 messages=[{"role": "user", "content": prompt}], output_format=schema)
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        return None
    return resp.parsed_output


def run_arm(client, model, prot, item, labels, protected: bool) -> dict:
    text = item["text"]
    prompts = {
        "extract": "List every person named, every email address and every money amount in the "
                   f"document, copied exactly as written.\n\n<document>\n{text}\n</document>",
        "classify": f"Classify the document into exactly one of: {', '.join(labels)}. "
                    f"Answer with the label only.\n\n<document>\n{text}\n</document>",
    }
    out = {"events": 0, "fabricated": 0}
    for task, prompt in prompts.items():
        amap = {}
        if protected:
            p = prot.protect([{"role": "user", "content": prompt}], CTX)
            prompt, amap = p.messages[0]["content"], p.alias_map
        res = call(client, model, prompt, Extraction if task == "extract" else Label)
        if res is None:
            out[task] = None
            continue
        if protected:   # every string the model returned goes through the gate and restorer
            def restore(s: str) -> str:
                r = Restorer(amap)
                v = r.feed(s) + r.finish()
                out["events"] += len(r.events)
                out["fabricated"] += sum(e.kind == "fabricated_alias" for e in r.events)
                return v
            if task == "extract":
                res = Extraction(people=[restore(x) for x in res.people], emails=[restore(x) for x in res.emails],
                                 amounts=[restore(x) for x in res.amounts])
            else:
                res = Label(label=restore(res.label))
        out[task] = res
    return out


def score(item, arm, labels) -> dict:
    gold = {t: {_norm(item["text"][s["start"]:s["end"]]) for s in item["spans"] if s["type"] == t}
            for t in ("PERSON", "EMAIL", "COMPENSATION")}
    gold["PERSON"] = {g for g in gold["PERSON"] if len(g.split()) >= 2} or gold["PERSON"]
    sc = {}
    ex = arm.get("extract")
    if ex is not None:
        for field, t in (("people", "PERSON"), ("emails", "EMAIL"), ("amounts", "COMPENSATION")):
            sc[field] = _prf({_norm(x) for x in getattr(ex, field)}, gold[t])
    lab = arm.get("classify")
    truth = item["template_id"].split(".", 1)[-1]
    sc["classify"] = None if lab is None else int(lab.label.strip().casefold() == truth.casefold())
    return sc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    a = ap.parse_args()

    data = ROOT / "data"
    items = [i for i in load(data / f"{a.split}.jsonl") if i["slice"] == "hr_free_text"]
    random.Random(a.seed).shuffle(items)
    items = items[: a.n]
    labels = sorted({i["template_id"].split(".", 1)[-1] for i in items})
    calls = len(items) * 4
    print(f"E-UTIL: {len(items)} items x 2 arms x 2 tasks = {calls} calls to {a.model}.", file=sys.stderr)
    if not a.yes and input("Proceed? [y/N] ").strip().lower() != "y":
        return

    import anthropic
    client = anthropic.Anthropic()
    prot = build_protector(data, True)
    agg = {arm: {"people": [0, 0, 0], "emails": [0, 0, 0], "amounts": [0, 0, 0], "classify": [0, 0], "failed": 0}
           for arm in ("raw", "protected")}
    gate = {"events": 0, "fabricated": 0}
    blocked = 0
    for n, it in enumerate(items, 1):
        results = {}
        for arm, protected in (("raw", False), ("protected", True)):
            try:
                results[arm] = run_arm(client, a.model, prot, it, labels, protected)
            except BlockedRequest:
                blocked += 1          # a utility cost in itself: the task could not run at all
            except Exception as e:    # API errors: counted, never dropped silently
                agg[arm]["failed"] += 1
                print(f"  item {it['id']} {arm}: {type(e).__name__}", file=sys.stderr)
        if len(results) < 2:          # paired design: score only items both arms completed
            continue
        gate["events"] += results["protected"]["events"]
        gate["fabricated"] += results["protected"]["fabricated"]
        for arm, res in results.items():
            sc = score(it, res, labels)
            for f in ("people", "emails", "amounts"):
                if f in sc:
                    agg[arm][f] = [x + y for x, y in zip(agg[arm][f], sc[f])]
            if sc.get("classify") is not None:
                agg[arm]["classify"][0] += sc["classify"]; agg[arm]["classify"][1] += 1
        print(f"  {n}/{len(items)}", file=sys.stderr, end="\r")

    def f1(tp, np_, ng):
        p = tp / np_ if np_ else 0.0
        r = tp / ng if ng else 0.0
        return 2 * p * r / (p + r) if p + r else 0.0

    rows = ["| Metric | Raw | Protected | Relative change | Gate (≤ 5 % degradation) |", "|---|---:|---:|---:|---|"]
    result = {"model": a.model, "split": a.split, "n": len(items), "gate_events": gate, "metrics": {}}
    for f in ("people", "emails", "amounts"):
        r, p = f1(*agg["raw"][f]), f1(*agg["protected"][f])
        rel = (p - r) / r if r else 0.0
        result["metrics"][f"{f}_f1"] = {"raw": r, "protected": p, "relative": rel}
        rows.append(f"| {f} F1 | {r:.3f} | {p:.3f} | {rel:+.1%} | {'PASS' if rel >= -0.05 else 'FAIL'} |")
    ra = agg["raw"]["classify"]; pa = agg["protected"]["classify"]
    r, p = (ra[0] / ra[1] if ra[1] else 0), (pa[0] / pa[1] if pa[1] else 0)
    rel = (p - r) / r if r else 0.0
    result["metrics"]["classify_acc"] = {"raw": r, "protected": p, "relative": rel}
    rows.append(f"| classify accuracy | {r:.3f} | {p:.3f} | {rel:+.1%} | {'PASS' if rel >= -0.05 else 'FAIL'} |")
    rows.append(f"\nBlocked by policy (task impossible under this purpose): {blocked}/{len(items)} items.")
    result["blocked"] = blocked
    rows.append(f"Failed calls: raw {agg['raw']['failed']}, protected {agg['protected']['failed']}. "
                f"Gate events in protected arm: {gate['events']} (fabricated aliases {gate['fabricated']}).")
    md = f"## E-UTIL — {a.model}, {a.split}, n={len(items)}\n\n" + "\n".join(rows)
    out = ROOT / "results"; out.mkdir(exist_ok=True)
    (out / f"util_{a.split}.json").write_text(json.dumps(result, indent=1))
    (out / f"util_{a.split}.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
