"""Replay protect() + egress.check() over a split (read-only measurement for SECURITY_EVALUATION.md).

    python -m eval.replay_egress --split dev|test

Reports: documents whose outbound body still contains >= 1 raw gold value (len >= 4),
and how often the egress invariant fires. Evaluating an already-locked split with
unchanged code is a measurement, not tuning; do not change code based on its output
without regenerating a fresh test split.
"""
import argparse
import json
from pathlib import Path

from eval.run_det import CTX, build_protector, load
from pgw.egress import check
from pgw.types import EgressViolation

ap = argparse.ArgumentParser()
ap.add_argument("--split", default="dev")
a = ap.parse_args()
data = Path(__file__).resolve().parent / "data"
prot = build_protector(data, True)
n = fired = leaked = 0
for it in load(data / f"{a.split}.jsonl"):
    p = prot.protect([{"role": "user", "content": it["text"]}], CTX)
    body = json.dumps({"model": "m", "messages": p.messages}).encode()
    try:
        check(body, p.sensitive_values)
    except EgressViolation:
        fired += 1
    out = p.messages[0]["content"]
    leaked += any(it["text"][g["start"]:g["end"]] in out for g in it["spans"] if g["end"] - g["start"] >= 4)
    n += 1
print(json.dumps({"split": a.split, "items": n, "egress_fired": fired, "items_with_raw_gold_value_in_body": leaked}))
