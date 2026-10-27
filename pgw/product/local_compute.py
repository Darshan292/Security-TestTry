"""LOCAL-COMPUTE route (PRODUCT_ARCHITECTURE §3-I): exact values stay inside the gateway.

The app sends a structured payload that must match the contract's input schema exactly. Every input field has a
declared class: C1 values are protected wherever they appear (aliased, independent of detection); C2/C3 values are
withheld, so the egress invariant proves they never leave. A deterministic function computes outputs from exact
values, and each output obeys its contract egress rule (RAW / TRANSFORMED / INTERNAL, default INTERNAL). Only the
contract's prompt template, filled with non-INTERNAL outputs, is sent to the external model.

The free-text `instruction` is the one caller-written part of the prompt. Every number in it is parsed to a value in
any written unit (pgw.product.amounts) and compared numerically with every withheld C2/C3 input and every numeric
output that does not leave raw; a number equal to one of them, or within ±0.5%, is replaced by a marker, so neither
another written form ("2.45 million" for ₹24,50,000) nor rounding carries the value out (SECURITY_BOUNDARY §6 N6).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ..types import EntityType as T
from . import amounts
from .contracts import Contract, DataClass, Output


class PayloadError(ValueError):
    """The request does not match the data contract. Messages never contain values."""


@dataclass
class ComputeResult:
    prompt: str                                   # what the external LLM is asked (identities still raw here)
    declared: list[tuple[T, str]]                 # values the pipeline must alias wherever they occur
    withheld: list[tuple[T, str]]                 # exact values that must never appear in egress bytes
    outputs: dict                                 # all computed outputs (stays inside; for tests/audit only)
    raw: dict = None                              # sentinel -> RAW output, put back after detection (see run)
    # For the transparency report (stays inside; the report carries paths, types and classes, never these values):
    fields: list = None        # (path, DataClass, entity, value) for every classified input field
    masked: list = None        # entity type of every number masked in the instruction
    instruction_at: tuple = None   # (start, end) of the masked instruction inside `prompt`


_SENTINEL_BASE = 0xE010    # Unicode private-use characters: no detector matches them, no user text needs them


def restore_raw(text: str, raw: dict) -> str:
    for k, v in (raw or {}).items():
        text = text.replace(k, v)
    return text


# --- payload validation ------------------------------------------------------------------------------------

def _check(value, spec: dict, path: str, reference: dict | None = None):
    t = spec.get("type")
    if t == "str":
        if not isinstance(value, str) or not value.strip() or len(value) > int(spec.get("max_len", 200)):
            raise PayloadError(f"{path}: expected a non-empty string within the length limit")
        if "one_of" in spec and value not in (reference or {}).get(spec["one_of"], {}):
            raise PayloadError(f"{path}: not a value from the contract's reference table")
    elif t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise PayloadError(f"{path}: expected an integer")
        if not int(spec.get("min", 0)) <= value <= int(spec.get("max", 10**12)):
            raise PayloadError(f"{path}: integer out of the allowed range")
    elif t == "list":
        if not isinstance(value, list) or not int(spec.get("min", 0)) <= len(value) <= int(spec.get("max", 100)):
            raise PayloadError(f"{path}: expected a list of the allowed length")
        for i, item in enumerate(value):
            _check_object(item, spec["items"], f"{path}[{i}]", reference)
    else:
        raise PayloadError(f"{path}: unsupported schema type")


def _check_object(obj, schema: dict, path: str = "payload", reference: dict | None = None):
    if not isinstance(obj, dict) or set(obj) != set(schema):
        raise PayloadError(f"{path}: fields do not match the data contract")
    for k, spec in schema.items():
        _check(obj[k], spec, f"{path}.{k}", reference)


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
    _check_object(payload, contract.input_schema, reference=contract.reference)
    return payload


def _classified_fields(obj: dict, schema: dict, prefix: str = ""):
    """Yield (path, class, entity, value) for every leaf field that declares a class ("employees[0].annual_ctc")."""
    for k, spec in schema.items():
        if spec["type"] == "list":
            for i, item in enumerate(obj[k]):
                yield from _classified_fields(item, spec["items"], f"{prefix}{k}[{i}].")
        elif spec.get("class") and spec["class"] != "C0":
            yield f"{prefix}{k}", DataClass(spec["class"]), T(spec["entity"]), obj[k]


def _classified_inputs(obj: dict, schema: dict):
    """Yield (class, entity, value) for every leaf field that declares a class."""
    for _path, cls, entity, value in _classified_fields(obj, schema):
        yield cls, entity, value


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

def salary_difference(payload: dict, contract=None) -> dict:
    a, b = payload["employees"]
    hi, lo = (a, b) if a["annual_ctc"] >= b["annual_ctc"] else (b, a)
    diff = hi["annual_ctc"] - lo["annual_ctc"]
    pct = (Decimal(diff) * 100 / Decimal(lo["annual_ctc"])).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    # Equal CTCs are reported as equal, never as one person "having the higher CTC" (audit finding).
    ranking = ("Both have exactly the same annual CTC." if diff == 0
               else "The first of these two has the higher annual CTC.")
    return {"higher_name": hi["name"], "higher_id": hi["employee_id"], "lower_name": lo["name"],
            "lower_id": lo["employee_id"], "difference": inr(diff), "pct_of_lower": f"{pct}%", "ranking": ranking,
            "difference_value": diff, "higher_ctc": hi["annual_ctc"], "lower_ctc": lo["annual_ctc"]}


ESI_WAGE_CEILING = 21000   # statutory monthly gross ceiling for ESI coverage (public, C0)


def esi_eligibility(payload: dict, contract=None) -> dict:
    e = payload["employee"][0]
    eligible = e["monthly_gross"] <= ESI_WAGE_CEILING
    return {"employee_name": e["name"], "employee_id": e["employee_id"],
            "eligibility": "covered by ESI" if eligible else "not covered by ESI",
            "threshold": inr(ESI_WAGE_CEILING), "margin": abs(e["monthly_gross"] - ESI_WAGE_CEILING),
            "monthly_gross": e["monthly_gross"]}


def band_position(payload: dict, contract=None) -> dict:
    """Where an employee's CTC sits in their pay band. Band limits are company policy (C0) and come from the
    contract's sealed `reference.pay_bands`, never from the caller (audit S2: caller-supplied limits crossed raw)."""
    e, b = payload["employee"][0], payload["band"][0]
    lo, hi = contract.reference["pay_bands"][b["name"]]
    ctc = e["annual_ctc"]
    if hi <= lo:
        raise ValueError("band maximum must exceed band minimum")
    if ctc < lo:
        position = "below the band minimum"
    elif ctc > hi:
        position = "above the band maximum"
    else:
        third = (ctc - lo) * 3 // (hi - lo + 1)
        position = ("in the lower third of the band", "in the middle third of the band",
                    "in the upper third of the band")[min(third, 2)]
    pct = (Decimal(ctc - lo) * 100 / Decimal(hi - lo)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return {"employee_name": e["name"], "employee_id": e["employee_id"], "band_name": b["name"],
            "band_min": inr(lo), "band_max": inr(hi), "position": position, "pct_through_band": f"{pct}%",
            "annual_ctc": ctc, "headroom": hi - ctc}


# --- the free-text instruction ------------------------------------------------------------------------------

def _value_of(v) -> float | None:
    """The number a withheld input or a computed output stands for (an int, or one amount in a string)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        found = amounts.numbers(v)
        if len(found) == 1:
            return found[0][2]
    return None


def mask_withheld_numbers(instruction: str, protected: list[tuple[T, float]]) -> tuple[str, int]:
    """Replace every number in `instruction` that equals a protected value or lies within ±0.5% of it (in any written
    unit, an implied one included) by "[TYPE]". Returns the new text and the number of replacements."""
    instruction, hits = _mask_numbers(instruction, protected)
    return instruction, len(hits)


def _mask_numbers(instruction: str, protected: list[tuple[T, float]]) -> tuple[str, list[T]]:
    if not protected:
        return instruction, []
    hits = []
    for s, e, value, unit in amounts.numbers(instruction):
        for x in amounts.candidates(value, unit, instruction[s:e]):
            t = next((t for t, v in protected if amounts.near(x, v)), None)
            if t is not None:
                hits.append((s, e, t))
                break
    for s, e, t in reversed(hits):
        instruction = instruction[:s] + f"[{t.value}]" + instruction[e:]
    return instruction, [t for _s, _e, t in hits]


FUNCTIONS = {"salary_difference": salary_difference, "esi_eligibility": esi_eligibility,
             "band_position": band_position}


def run(contract: Contract, messages: list) -> ComputeResult:
    payload = parse_payload(messages, contract)
    declared: list[tuple[T, str]] = []
    withheld: list[tuple[T, str]] = []
    protected: list[tuple[T, float]] = []                    # numbers the instruction must not restate
    classified = list(_classified_fields(payload, contract.input_schema))
    for _path, cls, entity, value in classified:
        if cls is DataClass.C1:
            declared.append((entity, str(value)))
        else:                                                # C2/C3 inputs never leave in any form
            withheld += [(entity, f) for f in surface_forms(value)]
            if (n := _value_of(value)) is not None:
                protected.append((entity, n))
    outputs = FUNCTIONS[contract.compute](payload, contract)  # function name validated at startup
    fields, raw = {}, {}
    for name, value in outputs.items():
        spec = contract.outputs.get(name) or Output(name, DataClass.D, "INTERNAL")
        if spec.egress != "RAW" and spec.derived_from not in (DataClass.C0, DataClass.C1):
            if (n := _value_of(value)) is not None:          # an aliased or internal amount, count or percentage
                protected.append((spec.entity, n))
        if spec.egress == "INTERNAL":
            withheld += [(T.DERIVED, f) for f in surface_forms(value) if len(f) >= 4]
            continue
        if spec.egress == "RAW":
            # The contract decided this value crosses as is. It must not be re-classified by detectors (a C0 band
            # limit such as "₹18,00,000" would otherwise be generalised and the model would get wrong facts), so
            # it is held behind a sentinel during protection and put back afterwards. Withheld values are still
            # checked at egress against the final bytes.
            sentinel = chr(_SENTINEL_BASE + len(raw))
            raw[sentinel] = str(value)
            fields[name] = sentinel
            continue
        fields[name] = str(value)
        if spec.egress == "TRANSFORMED":
            declared.append((spec.entity, str(value)))
    instruction = "".join(c for c in payload["instruction"] if not 0xE000 <= ord(c) <= 0xF8FF)
    instruction, masked = _mask_numbers(instruction, protected)
    prompt = contract.prompt_template.format(instruction=instruction, **fields)
    # where the instruction sits in the prompt (private-use characters were stripped from it, so one marks the spot)
    at = contract.prompt_template.format(instruction="\uE0FF", **fields).find("\uE0FF")
    return ComputeResult(prompt=prompt, declared=declared, withheld=withheld, outputs=outputs, raw=raw,
                         fields=classified, masked=masked,
                         instruction_at=(at, at + len(instruction)) if at >= 0 else None)
