"""Identifier-shape backstop (pgw.detect.idshape): generality, benign shapes, business ids from the signed contract.

A fresh synthetic set, designed for this test and not taken from any corpus: 14 identifier schemes, each written
plain, spaced and hyphenated, embedded in ticket-like sentences WITHOUT the cue words the scheme recognizers use.
Every value is synthetic.
"""
import json
import os
import re

import pytest
from starlette.testclient import TestClient

from pgw.detect import idshape
from pgw.detect.recognizers import verhoeff_check_digit
from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack
from pgw.product.contracts import ContractError, load_contract
from pgw.types import Context, EntityType
from tests.sealed import ROOT, attest, production, sha

POLICY = PolicyPack.load(ROOT / "policies/hris-default.yaml")
PROT = Protector(POLICY, Keys(bytes(32)), None)
CTX = Context("t", "t", "integration_debug")     # REDACTs identifiers instead of blocking: shows what crosses

AADHAAR = "73529184063" + verhoeff_check_digit("73529184063")


def _variants(plain: str, groups: list[int]) -> list[str]:
    """plain, spaced and hyphenated forms of one value, grouped as the scheme is usually written."""
    parts, i = [], 0
    for g in groups:
        parts.append(plain[i:i + g])
        i += g
    assert i == len(plain)
    return [plain, " ".join(parts), "-".join(parts)]


# scheme -> (plain value, grouping used for the spaced / hyphenated forms)
SCHEMES = {
    "esic_ip_number": ("3104567892", [4, 4, 2]),
    "uan": ("100987654321", [4, 4, 4]),
    "aadhaar_no_cue": (AADHAAR, [4, 4, 4]),
    "bank_account_no_cue": ("50100234567812", [4, 4, 4, 2]),
    "nps_pran": ("110045678912", [4, 4, 4]),
    "driving_licence": ("MH1220110012345", [4, 11]),
    "gstin": ("29AAGCB7383J1Z4", [2, 5, 4, 1, 3]),
    "vehicle_registration": ("KA51AB4471", [2, 2, 2, 4]),
    "iban_like": ("NO9386011117947", [4, 4, 4, 3]),
    "insurance_policy": ("HLT4402918377", [3, 4, 4, 2]),
    "foreign_passport": ("X93821047", [1, 4, 4]),
    "indian_passport": ("Z4872631", [1, 7]),
    "voter_id": ("TNK4821937", [3, 7]),
    "pan_spaced": ("BQRPS4417K", [5, 4, 1]),
}
TEMPLATES = [
    "hi, my number is {v}, please update it in the system",
    "Updated value: {v}. Kindly confirm once done.",
    "the new one is {v} and the old one was wrong",
]
IDENTIFIER_CASES = [(scheme, form, TEMPLATES[k % 3])
                    for k, (scheme, (plain, groups)) in enumerate(SCHEMES.items())
                    for form in _variants(plain, groups)]

BENIGN = [
    "12/03/2024", "2024-03-12", "12-Mar-2024", "12 MAR 2024 10:30", "26.10.2026 10.30 AM", "2026-01-04T20:56:55",
    "20240315103000", "FY 2024-25", "Q3FY25", "v2.10.3", "release 15.2.1158.16", "Section 80C", "Section 80CCD(1B)",
    "Form 16", "Form 12BB", "ISO 9001", "HDFC0001234", "HDFC 0001234", "1800-266-4412",
    "4c55c22e-6da7-4121-aaa6-535ef4d0cd02", "IALM 2012-14", "10.169.237.237",
]
# Money is the COMPENSATION detector's job (aliased with pay context): never an IDENTIFIER.
MONEY = ["Rs 15,00,000", "INR 1500000", "Rs 12500000.00", "USD 142,000", "1,12,400 pm", "18.5 LPA"]
BUSINESS = ["PAY-88231", "OFR-2291", "CLM-5521", "IT-20417"]


def _leaks(value: str, out: str) -> bool:
    """The value crosses if it is in the output as written or, for values with >= 6 digits, as a digit sequence."""
    if value.lower() in out.lower():
        return True
    digits = re.sub(r"\D", "", value)
    return len(digits) >= 6 and digits in re.sub(r"\D", "", out)


def _protect(text: str, ctx: Context = CTX) -> str:
    return PROT.protect([{"role": "user", "content": text}], ctx).messages[0]["content"]


# --- generality: 14 schemes x 3 forms, no cue words ---------------------------------------------------------------

def test_the_set_is_as_wide_as_required():
    assert len(SCHEMES) >= 12 and len(IDENTIFIER_CASES) == 3 * len(SCHEMES) and len(BENIGN) >= 15


@pytest.mark.parametrize("scheme,value,template", IDENTIFIER_CASES)
def test_identifier_is_withheld_in_every_form(scheme, value, template):
    out = _protect(template.format(v=value))
    assert not _leaks(value, out), out
    assert "please update" in out or "Kindly confirm" in out or "the old one" in out   # the prose still crosses


@pytest.mark.parametrize("value", BENIGN)
def test_benign_shapes_cross_unchanged(value):
    text = f"As discussed, see {value} for the details."
    assert not any(d.type is EntityType.IDENTIFIER for d in PROT.detect_text(text))
    assert value in _protect(text)


@pytest.mark.parametrize("value", BUSINESS)
def test_business_ids_need_the_signed_contract(value):
    # Without a contract a company reference is shaped like an identifier and is withheld (fail safe) ...
    assert value not in _protect(f"Ticket {value} is pending")
    # ... the contracts' business_identifiers let it cross (see the gateway test below).
    bids = load_contract(ROOT / "contracts/ticket_summary.yaml").business_identifiers
    assert any(rx.fullmatch(value) for rx in bids)


@pytest.mark.parametrize("value", MONEY)
def test_money_is_never_an_identifier(value):
    for text in (f"see {value} for the details", f"my salary is {value} now"):
        assert not any(d.type is EntityType.IDENTIFIER for d in PROT.detect_text(text))


def test_known_types_keep_their_recognizer():
    # a recognised phone or PAN keeps its own type; the backstop only adds what nothing else found
    text = "call +91 98200 12345 or use PAN ABCPK1234F"
    types = {d.type for d in PROT.analyze([{"role": "user", "content": text}], CTX)[0]}
    assert EntityType.PHONE in types and EntityType.PAN in types and EntityType.IDENTIFIER not in types


def test_labels_stay_outside_the_span():
    out = _protect("UAN 1009 8765 4321 and ESIC IP 3104567892 pending")
    assert "UAN [IDENTIFIER]" in out and "ESIC IP [IDENTIFIER]" in out


def test_aadhaar_with_wide_gaps_is_caught_by_its_checksum():
    a = AADHAAR
    out = _protect(f"number {a[:4]} - {a[4:8]} - {a[8:]} please")
    assert not _leaks(a, out)
    bad = a[:11] + str((int(a[11]) + 1) % 10)      # checksum-invalid: only the general shape rules apply
    assert [d for d in idshape.detect(f"x {bad[:4]} - {bad[4:8]} - {bad[8:]} y")] == []


def test_shape_bounds():
    det = lambda s: [s[d.start:d.end] for d in idshape.detect(s)]   # noqa: E731
    assert det("ref 12345678 ok") == []                  # 8 digits: below the bound
    assert det("ref 123456789 ok") == ["123456789"]      # 9 digits
    assert det("ref 1234567890123456789 ok") == []       # 19 digits: a table row or a flood, not one identifier
    assert det("code AB123 ok") == [] and det("code AB1234 ok") == []   # under 7 characters
    assert det("code ABC1234 ok") == ["ABC1234"]
    assert det("Hello World 2024 again") == []           # Title-case words never join a chain


def test_backstop_is_linear_on_floods():
    import time
    for flood in ("1 " * 50_000, "A1-" * 33_000, "a=1&" * 25_000, "X1 " * 33_000):
        t = time.perf_counter()
        idshape.detect(flood)
        assert time.perf_counter() - t < 0.5


# --- business_identifiers: anchored, bounded, and unable to switch the backstop off --------------------------------

@pytest.mark.parametrize("pattern", [
    r"^\d{12}$", r"^\d{9,12}$", r"^[A-Z]{5}\d{4}[A-Z]$", r"^[A-Z]\d{7}$", r"^[A-Z]{3}\d{7}$",
    r"^(?:ABC|TNK)\d{7}$", r"^(?:KA)\d{2}[A-Z]{2}\d{4}$",
])
def test_generic_patterns_are_refused(pattern):
    with pytest.raises(idshape.BusinessPatternError):
        idshape.compile_business_identifiers([pattern])


@pytest.mark.parametrize("pattern", [
    r"PAY-\d{5}", r"^PAY-\d{5}", r"^PAY-\d+$", r"^PAY-.*$", r"^PAY-\d{5,}$", r"^PAY-\d{1,40}$", r"^PAY(?:-\d{2}){3}$",
    r"^pay-\d{5}$", r"^P-\d{5}$", r"^PAY-\d{5}$" + "0" * 70,
])
def test_unanchored_unbounded_or_prefixless_patterns_are_refused(pattern):
    with pytest.raises(idshape.BusinessPatternError):
        idshape.compile_business_identifiers([pattern])


def test_contract_with_a_generic_pattern_does_not_load(tmp_path):
    src = (ROOT / "contracts/ticket_summary.yaml").read_text()
    bad = tmp_path / "c.yaml"
    bad.write_text(src.replace("business_identifiers:\n", "business_identifiers:\n  - '^\\d{12}$'\n", 1))
    with pytest.raises(ContractError):
        load_contract(bad)


def test_shipped_contracts_declare_their_ticket_formats():
    for name in ("ticket_summary", "ticket_summary_any_script", "hr_case_explanation", "hr_email_draft"):
        c = load_contract(ROOT / f"contracts/{name}.yaml")
        assert all(any(rx.fullmatch(b) for rx in c.business_identifiers) for b in BUSINESS + ["DC-2026-041"])
        assert c.actions()[EntityType.IDENTIFIER].value == "REDACT"


# --- end to end: production gateway, signed config, outbound bytes ------------------------------------------------

KEY = "idshape-key"


def _gateway(tmp_path):
    mock = MockLLMUpstream("echo")
    tasks = {"ticket_summary": str(ROOT / "contracts/ticket_summary.yaml")}
    apps = [{"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY), "purposes": sorted(tasks),
             "restore_output": True, "models": ["mock-1"], "tasks": tasks}]
    s = Settings(**production(tmp_path, apps), ledger_db_path=str(tmp_path / "l.sqlite"),
                 egress_ledger_path=str(tmp_path / "e.jsonl"), master_key=os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s)), mock


def _send(client, text):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": text}],
            "pgw_subjects": {"requester": {"name": "Ritika Banerjee"}}}
    return client.post("/v1/chat/completions", json=body, headers={
        "Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary", **attest("ticket_summary", body)})


@pytest.mark.parametrize("form", [0, 1, 2], ids=["plain", "spaced", "hyphenated"])
def test_gateway_sends_no_identifier_and_keeps_benign_text_and_business_ids(tmp_path, form):
    client, mock = _gateway(tmp_path)
    values = {s: _variants(p, g)[form] for s, (p, g) in SCHEMES.items()}
    lines = [f"Ticket {BUSINESS[i % len(BUSINESS)]}: for {s.replace('_', ' ')} the value is {v}."
             for i, (s, v) in enumerate(values.items())]
    benign = "Context: " + "; ".join(BENIGN) + "."
    r = _send(client, "Raised by Ritika Banerjee.\n" + "\n".join(lines) + "\n" + benign)
    assert r.status_code == 200, r.text
    wire = mock.received[-1].decode("utf-8")
    sent = "\n".join(m["content"] for m in json.loads(wire)["messages"] if m["role"] != "system")
    for s, v in values.items():
        assert not _leaks(v, sent), f"{s} crossed"
        assert not _leaks(v, wire)
    for b in BENIGN + BUSINESS:
        assert b in sent, f"benign text {b!r} was masked"
    assert "[IDENTIFIER]" in sent and "Ritika" not in wire
    # never restored: the echoing model gets the marker back, and the client sees no identifier either
    ans = r.json()["choices"][0]["message"]["content"]
    assert not any(_leaks(v, ans) for v in values.values())
