"""LOCAL-COMPUTE route (PRODUCT_ARCHITECTURE §3-I): exact values stay inside the gateway.

The app sends a structured payload that must match the contract's input schema exactly. Every input field has a
declared class: C1 values are protected wherever they appear (aliased, independent of detection); C2/C3 values are
withheld, so the egress invariant proves they never leave. A deterministic function computes outputs from exact
values, and each output obeys its contract egress rule (RAW / TRANSFORMED / INTERNAL, default INTERNAL). Only the
contract's prompt template, filled with non-INTERNAL outputs, is sent to the external model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ..types import EntityType as T
from .contracts import Contract, DataClass, Output


class PayloadError(ValueError):
    """The request does not match the data contract. Messages never contain values."""


@dataclass
class ComputeResult:
    prompt: str                                   # what the external LLM is asked (identities still raw here)
    declared: list[tuple[T, str]]                 # values the pipeline must alias wherever they occur
    withheld: list[tuple[T, str]]                 # exact values that must never appear in egress bytes
    outputs: dict                                 # all computed outputs (stays inside; for tests/audit only)


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


def _classified_inputs(obj: dict, schema: dict):
    """Yield (class, entity, value) for every leaf field that declares a class."""
    for k, spec in schema.items():
        if spec["type"] == "list":
            for item in obj[k]:
                yield from _classified_inputs(item, spec["items"])
        elif spec.get("class") and spec["class"] != "C0":
            yield DataClass(spec["class"]), T(spec["entity"]), obj[k]


# --- formatting ---------------------------------------------------------------------------------------------

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
    sign = "-" if n < 0 else ""
    return sign + "₹" + ",".join(groups + [tail]) if groups else sign + "₹" + tail


def surface_forms(v) -> list[str]:
    """Every written form an exact value could take in egress bytes (for withholding)."""
    if isinstance(v, bool):
        return []
    if isinstance(v, int):
        return [str(v), f"{v:,}", inr(v), inr(v)[1:]]
    return [str(v)]


# --- functions (each returns {output name: value}; only contract-declared outputs can ever leave) ----------

def salary_difference(payload: dict) -> dict:
    a, b = payload["employees"]
    hi, lo = (a, b) if a["annual_ctc"] >= b["annual_ctc"] else (b, a)
    diff = hi["annual_ctc"] - lo["annual_ctc"]
    pct = (Decimal(diff) * 100 / Decimal(lo["annual_ctc"])).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return {"higher_name": hi["name"], "higher_id": hi["employee_id"], "lower_name": lo["name"],
            "lower_id": lo["employee_id"], "difference": inr(diff), "pct_of_lower": f"{pct}%",
            "difference_value": diff, "higher_ctc": hi["annual_ctc"], "lower_ctc": lo["annual_ctc"]}


ESI_WAGE_CEILING = 21000   # statutory monthly gross ceiling for ESI coverage (public, C0)


def esi_eligibility(payload: dict) -> dict:
    e = payload["employee"][0]
    eligible = e["monthly_gross"] <= ESI_WAGE_CEILING
    return {"employee_name": e["name"], "employee_id": e["employee_id"],
            "eligibility": "covered by ESI" if eligible else "not covered by ESI",
            "threshold": inr(ESI_WAGE_CEILING), "margin": abs(e["monthly_gross"] - ESI_WAGE_CEILING),
            "monthly_gross": e["monthly_gross"]}


FUNCTIONS = {"salary_difference": salary_difference, "esi_eligibility": esi_eligibility}


def run(contract: Contract, messages: list) -> ComputeResult:
    payload = parse_payload(messages, contract)
    declared: list[tuple[T, str]] = []
    withheld: list[tuple[T, str]] = []
    for cls, entity, value in _classified_inputs(payload, contract.input_schema):
        if cls is DataClass.C1:
            declared.append((entity, str(value)))
        else:                                                # C2/C3 inputs never leave in any form
            withheld += [(entity, f) for f in surface_forms(value)]
    outputs = FUNCTIONS[contract.compute](payload)           # function name validated at startup
    fields = {}
    for name, value in outputs.items():
        spec = contract.outputs.get(name) or Output(name, DataClass.D, "INTERNAL")
        if spec.egress == "INTERNAL":
            withheld += [(T.DERIVED, f) for f in surface_forms(value) if len(f) >= 4]
            continue
        fields[name] = str(value)
        if spec.egress == "TRANSFORMED":
            declared.append((spec.entity, str(value)))
    prompt = contract.prompt_template.format(instruction=payload["instruction"], **fields)
    return ComputeResult(prompt=prompt, declared=declared, withheld=withheld, outputs=outputs)
