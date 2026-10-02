"""LOCAL-COMPUTE route (PRODUCT_ARCHITECTURE §3-I): exact values stay inside the gateway.

The app sends a structured payload that must match the contract's input schema exactly. A deterministic function
computes the answer from exact values; only the fields the contract's prompt template names (identities, which
are then aliased, and derived, non-identifying results) are written into the external prompt. Exact C2 inputs are
returned separately so the egress invariant can prove they never leave.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ..types import EntityType as T
from .contracts import Contract


class PayloadError(ValueError):
    """The request does not match the data contract. Messages never contain values."""


@dataclass
class ComputeResult:
    prompt: str                                   # what the external LLM is asked (identities still raw here)
    declared: list[tuple[T, str]]                 # values that must be protected wherever they occur
    withheld: list[tuple[T, str]]                 # exact values that must never appear in egress bytes


# --- payload validation ------------------------------------------------------------------------------------

def _check(value, spec: dict, path: str):
    t = spec.get("type")
    if t == "str":
        if not isinstance(value, str) or not value.strip() or len(value) > int(spec.get("max_len", 200)):
            raise PayloadError(f"{path}: expected a non-empty string within the length limit")
    elif t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise PayloadError(f"{path}: expected an integer")
        if not int(spec.get("min", 0)) <= value <= int(spec.get("max", 10**12)):
            raise PayloadError(f"{path}: integer out of the allowed range")
    elif t == "list":
        if not isinstance(value, list) or not int(spec.get("min", 0)) <= len(value) <= int(spec.get("max", 100)):
            raise PayloadError(f"{path}: expected a list of the allowed length")
        for i, item in enumerate(value):
            _check_object(item, spec["items"], f"{path}[{i}]")
    else:
        raise PayloadError(f"{path}: unsupported schema type")


def _check_object(obj, schema: dict, path: str = "payload"):
    if not isinstance(obj, dict) or set(obj) != set(schema):
        raise PayloadError(f"{path}: fields do not match the data contract")
    for k, spec in schema.items():
        _check(obj[k], spec, f"{path}.{k}")


def parse_payload(messages: list, contract: Contract) -> dict:
    if (not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict)
            or messages[0].get("role") != "user" or not isinstance(messages[0].get("content"), str)):
        raise PayloadError("LOCAL_COMPUTE expects exactly one user message whose content is the contract payload")
    if len(messages[0]["content"]) > contract.max_chars:
        raise PayloadError("payload too large")
    try:
        payload = json.loads(messages[0]["content"])
    except ValueError:
        raise PayloadError("payload is not valid JSON") from None
    _check_object(payload, contract.input_schema)
    return payload


# --- functions ----------------------------------------------------------------------------------------------

def inr(n: int) -> str:
    """Indian digit grouping: 270000 -> ₹2,70,000."""
    s = str(abs(int(n)))
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ("-" if n < 0 else "") + "₹" + ",".join(groups + [tail]) if groups else ("-" if n < 0 else "") + "₹" + tail


def salary_difference(payload: dict) -> dict:
    a, b = payload["employees"]
    if a["annual_ctc"] == b["annual_ctc"]:
        hi, lo = a, b
        equal = True
    else:
        hi, lo = (a, b) if a["annual_ctc"] > b["annual_ctc"] else (b, a)
        equal = False
    diff = hi["annual_ctc"] - lo["annual_ctc"]
    pct = (Decimal(diff) * 100 / Decimal(lo["annual_ctc"])).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return {"higher_name": hi["name"], "higher_id": hi["employee_id"], "lower_name": lo["name"],
            "lower_id": lo["employee_id"], "difference": inr(diff), "difference_value": diff,
            "pct_of_lower": f"{pct}", "equal": equal}


FUNCTIONS = {"salary_difference": salary_difference}


def run(contract: Contract, messages: list) -> ComputeResult:
    payload = parse_payload(messages, contract)
    fn = FUNCTIONS[contract.compute]            # validated at startup
    r = fn(payload)
    fields = {k: v for k, v in r.items() if isinstance(v, str)}
    prompt = contract.prompt_template.format(instruction=payload["instruction"], **fields)
    declared: list[tuple[T, str]] = []
    withheld: list[tuple[T, str]] = []
    for emp in payload["employees"]:
        declared += [(T.PERSON, emp["name"]), (T.EMPLOYEE_ID, emp["employee_id"])]
        ctc = int(emp["annual_ctc"])
        withheld += [(T.COMPENSATION, v) for v in (str(ctc), f"{ctc:,}", inr(ctc), inr(ctc)[1:])]
    declared.append((T.COMPENSATION, r["difference"]))     # derived C2 value: aliased, restorable per contract
    return ComputeResult(prompt=prompt, declared=declared, withheld=withheld)
