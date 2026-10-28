"""Record-structured data on the free-text external path: refused or kept in-house, never sent (SECURITY_BOUNDARY §11).

These tests assert PROTECTION, not only status codes: production mode, a valid source attestation, the mock provider's
received bytes inspected for every synthetic value. Exports come from the seeded generator tests/structured_exports.py
(every format, vendor-neutral and vendor-like field names, ALL-CAPS names, 2-500 records, alone or inside prose).
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from ml.train import structured as synth_genres
from ml.train.synth import derive_rng
from pgw.detect import structure
from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack
from pgw.product.contracts import ContractError, load_contract
from pgw.types import Context, Detection, EntityType as T
from tests.sealed import ROOT, attest, production, sha
from tests.structured_exports import FORMATS, case, cases

KEY = "structure-key"
P = "hr_email_draft"
CTX = Context("acme", "hr-assistant", "email_draft", conversation_id="s-1")
BIG = {"max_content_chars": 400_000, "max_body_bytes": 2_000_000}


def contract(tmp: Path, name: str = "c", **changes) -> Path:
    """hr_email_draft with a larger size limit (so a 500-record export reaches the structure check) plus `changes`."""
    raw = yaml.safe_load((ROOT / f"contracts/{P}.yaml").read_text())
    raw["max_chars"] = 300_000
    raw.update(changes)
    p = tmp / "src" / f"{name}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(raw, sort_keys=False))
    return p


class Gw:
    def __init__(self, tmp: Path, contract_path: Path, private=..., deployment=None):
        self.ext = MockLLMUpstream("echo")
        self.priv = MockLLMUpstream("summarise") if private is ... else private
        cfg = production(tmp, [{"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY), "restore_output": True,
                                "models": ["mock-1"], "purposes": [P], "transparency": True,
                                "tasks": {P: str(contract_path)}}],
                         deployment={**BIG, **(deployment or {})}, private_upstream=self.priv)
        s = Settings(**cfg, ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                     master_key=os.urandom(32).hex(), upstream=self.ext)
        self.client = TestClient(create_app(s), raise_server_exceptions=False)

    def send(self, text: str, signed: bool = True):
        body = {"model": "mock-1", "pgw_subjects": {"recipient": {"name": "Priya Raghunathan"}},
                "messages": [{"role": "user", "content": text}]}
        h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": P}
        if signed:
            h.update(attest(P, body))
        return self.client.post("/v1/chat/completions", json=body, headers=h)

    def external_bytes(self) -> str:
        return b"\n".join(self.ext.received).decode("utf-8", "replace")


@pytest.fixture(scope="module")
def refuse_gw(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("refuse")
    return Gw(tmp, contract(tmp))


@pytest.fixture(scope="module")
def private_gw(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("private")
    return Gw(tmp, contract(tmp, structured_content={"action": "PRIVATE"}))


def _no_leak(gw: Gw, values) -> None:
    sent = gw.external_bytes()
    unescaped = sent.encode().decode("unicode_escape", "replace") if "\\u" in sent else sent
    for v in values:
        assert v not in sent and v not in unescaped, f"{v!r} reached the external provider"


# --- the generator covers what it claims ---------------------------------------------------------------------------

def test_generator_covers_every_format_size_and_embedding():
    cs = cases(len(FORMATS) * 12)
    assert {c.fmt for c in cs} == set(FORMATS)
    assert any(c.records >= 200 for c in cs) and any(c.records == 2 for c in cs)
    assert any(c.embedded for c in cs) and any(not c.embedded for c in cs)
    assert any(re.search(r"\b[A-Z]{3,} [A-Z]{3,}\b", c.text) for c in cs)        # ALL-CAPS names
    assert all(c.values for c in cs)


@pytest.mark.parametrize("chunk", range(4))
def test_detector_finds_every_generated_export(chunk):
    for c in cases(1200)[chunk::4]:
        assert structure.find(c.text), (c.fmt, c.records, c.text[:400])


# --- gateway: refused (default) or in-house (PRIVATE); nothing reaches the external provider ----------------------

GATEWAY_CASES = cases(len(FORMATS) * 6, seed=4242)


@pytest.mark.parametrize("i", range(len(GATEWAY_CASES)))
def test_signed_export_is_refused_and_nothing_is_sent(refuse_gw, i):
    c = GATEWAY_CASES[i]
    r = refuse_gw.send(c.text)
    assert r.status_code == 422, (c.fmt, r.text[:300])
    err = r.json()["error"]
    assert err["type"] == "structured_data_in_free_text" and "field-contract" in err["message"]
    assert err["regions"] and all(set(x) == {"format", "records", "fields"} for x in err["regions"])
    for v in c.values:                                         # the error never echoes content
        assert v not in r.text
    rep = err["pgw_report"]
    assert {s["id"]: s["status"] for s in rep["stages"]}["structure_check"] == "failed"
    assert {c_["id"]: c_["status"] for c_ in rep["checks"]}["structure_check"] == "fail"
    assert rep["outbound"] is None
    _no_leak(refuse_gw, c.values)
    assert not refuse_gw.ext.received


@pytest.mark.parametrize("i", range(0, len(GATEWAY_CASES), 3))
def test_private_action_keeps_the_export_in_house(private_gw, i):
    c = GATEWAY_CASES[i]
    before = len(private_gw.priv.received)
    r = private_gw.send(c.text)
    assert r.status_code == 200, r.text[:300]
    assert r.headers["X-PGW-Route"] == "PRIVATE_LOCAL"
    assert len(private_gw.priv.received) == before + 1
    assert not private_gw.ext.received
    _no_leak(private_gw, c.values)


def test_private_action_without_an_in_house_model_refuses(tmp_path):
    # unattested: REFUSE so the deployment needs no private endpoint at all
    ext_in = yaml.safe_load((ROOT / f"contracts/{P}.yaml").read_text())["external_input"]
    gw = Gw(tmp_path, contract(tmp_path, structured_content={"action": "PRIVATE"},
                               external_input={**ext_in, "unattested": "REFUSE"}), private=None)
    c = case(7, "csv")
    r = gw.send(c.text)
    assert r.status_code == 422 and r.json()["error"]["type"] == "structured_data_in_free_text"
    assert not gw.ext.received
    _no_leak(gw, c.values)


def test_the_signature_does_not_make_an_export_safe_and_unsigned_goes_in_house(refuse_gw):
    c = case(11, "fixed_header", 5)
    assert refuse_gw.send(c.text).status_code == 422
    r = refuse_gw.send(c.text, signed=False)        # this contract keeps unattested input in-house
    assert r.status_code == 200 and r.headers["X-PGW-Route"] == "PRIVATE_LOCAL"
    assert not refuse_gw.ext.received


# --- the tester's regression: a fixed-width payroll export, with and without header -------------------------------

ROWS = ["48211 SHANTANU DESHMUKH 41250.00 16500.00 52318.00", "48212 MEERA KULKARNI 39100.00 15640.00 49920.00"]
NAMES = ("SHANTANU", "DESHMUKH", "MEERA", "KULKARNI")


@pytest.mark.parametrize("header", [None, "EMP_ID  EMPLOYEE NAME  BASIC  HRA  NET_PAY",
                                    "EMP NO EMPLOYEE NAME BASIC HRA GROSS", "EMP_ID,EMPLOYEE NAME,BASIC,HRA,NET"])
@pytest.mark.parametrize("sep", [" ", "  ", "\t"])
@pytest.mark.parametrize("prose", [False, True])
def test_fixed_width_payroll_export_is_never_sent_with_names(refuse_gw, header, sep, prose, caplog):
    caplog.set_level(logging.INFO)
    rows = [r.replace(" ", sep) if sep != " " else r for r in ROWS]
    rows = [re.sub(r"(?<=[A-Z])" + re.escape(sep) + r"(?=[A-Z])", " ", r) for r in rows]   # names keep one space
    text = "\n".join(([header] if header else []) + rows)
    if prose:
        text = "Hi, salary for these two is wrong, please draft a note:\n" + text + "\nThanks"
    r = refuse_gw.send(text)
    assert r.status_code == 422 and r.json()["error"]["type"] == "structured_data_in_free_text"
    assert not refuse_gw.ext.received
    _no_leak(refuse_gw, NAMES + ("41250.00", "52318.00", "48211"))
    assert not any(rec.exc_info or "Traceback" in rec.getMessage() for rec in caplog.records)


# --- the earlier bug fixes stay fixed: no 500 ----------------------------------------------------------------------

def _rs_texts(n: int, seed: int = 99):
    r = random.Random(seed)
    for _ in range(n):
        a = r.randint(2, 999)
        b = int(str(a)[: r.randint(1, len(str(a)))])           # a prefix of the first amount ("Rs 200 ... Rs 20")
        yield (f"Rs {a}{r.choice(['', '0', '00'])} per {r.choice(['month', 'week'])}{r.choice([' and', '.', ', then'])} "
               f"Rs {b} per {r.choice(['year', 'day'])}{r.choice(['', ' for the canteen card', ' as arrears'])}")


def _header_texts(n: int, seed: int = 7):
    r = random.Random(seed)
    words = ["EMP_ID", "EMPLOYEE NAME", "BASIC", "HRA", "NET_PAY", "Gross", "TDS", "PF", "UAN", "Dept"]
    for _ in range(n):
        hdr = (r.choice(["  ", "\t", " | ", ","])).join(r.sample(words, r.randint(3, 7)))
        row = f"{r.randint(10000, 99999)} {r.choice(['VARSHA GOKHALE', 'Nitin Apte'])} " + " ".join(
            f"{r.randint(1000, 99999)}.{r.randint(0, 99):02d}" for _ in range(r.randint(1, 4)))
        yield hdr + "\n" + row if r.random() < 0.7 else hdr


@pytest.mark.parametrize("text", list(_rs_texts(25)) + list(_header_texts(25)))
def test_amount_prefixes_and_payroll_headers_never_500(refuse_gw, text, caplog):
    caplog.set_level(logging.INFO)
    r = refuse_gw.send(text)
    assert r.status_code in (200, 422), r.text[:300]
    if r.status_code == 422:
        assert r.json()["error"]["type"] == "structured_data_in_free_text"
    assert not any(rec.exc_info or "Traceback" in rec.getMessage() or "egress_violation" in rec.getMessage()
                   for rec in caplog.records)


# --- ordinary tickets are NOT refused ------------------------------------------------------------------------------

ORDINARY = [
    "Hi Priya,\nPlease share the revised offer letter.\n\nThanks,\nRavi Menon\nPhone: +91 98450 12345 / Email: "
    "ravi.menon@example.in",
    "Regards,\nAnil Kapoor\nSenior HR Executive, Kestrel Dynamics\nPhone: +91 80 4123 4567\nMobile: 98450 12345\n"
    "Email: anil.kapoor@example.in",
    "Hi team, my salary breakup is Basic Rs 20,000 and HRA Rs 8,000 per month. Please confirm the arrears.",
    "Salary breakup:\nBasic: Rs 20,000\nHRA: Rs 8,000\nPlease check.",
    "Basic 20,000 (40%)\nHRA 8,000 (20%)",
    "Please note:\n- joining date 12/03/2024\n- laptop pending\n- ID card issued on 15/03/2024",
    "1. Submit Form 16 by 15 June 2025\n2. Upload rent receipts\n3. Declare 80C investments",
    "From: Ravi Menon <ravi.menon@example.in>\nTo: Priya Raghunathan <priya.r@example.in>\nSubject: Leave\n"
    "Date: 12 March 2025\n\nHi Priya, I will be on leave from 14/03/2025 to 18/03/2025.\n\nRegards,\nRavi",
    "Leave request: 14 Mar to 18 Mar 2025 (3 days CL, 2 days EL). Manager: Sunil Rao. Ticket HR-2231.",
    "Rs 300 per month and Rs 30 per year",
    "Can you check why my reimbursement of Rs 4,520 (claim CLM-2231) and Rs 1,200 (claim CLM-2240) are pending?",
]


@pytest.mark.parametrize("text", ORDINARY)
def test_ordinary_tickets_are_not_refused(refuse_gw, text):
    assert not structure.find(text)
    r = refuse_gw.send(text)
    assert r.status_code == 200, r.text[:300]
    assert {c["id"]: c["status"] for c in r.json()["pgw_report"]["checks"]}["structure_check"] == "pass"


TICKET_GENRES = ("email_thread", "chat_log", "address_block", "bank_change_form", "ocr_form", "salary_slip",
                 "form16_extract")


@pytest.mark.parametrize("genre", TICKET_GENRES)
def test_synthetic_ticket_genres_are_not_structured(genre):
    texts = [synth_genres.PROGRAMS[genre](derive_rng(31, "t", genre, i), "en")[0] for i in range(150)]
    refused = [t for t in texts if structure.find(t)]
    assert not refused, refused[0][:500]


# --- fail closed: a structure check that errors or runs out of time refuses -----------------------------------------

def test_structure_check_error_fails_closed(tmp_path, monkeypatch):
    gw = Gw(tmp_path, contract(tmp_path))

    def boom(texts):
        raise RuntimeError("detector bug")
    monkeypatch.setattr(structure, "find_in", boom)
    r = gw.send(ORDINARY[0])
    assert r.status_code == 503 and r.json()["error"]["type"] == "structure_check_unavailable"
    assert "detector bug" not in r.text and not gw.ext.received and not gw.priv.received


def test_structure_check_timeout_fails_closed(tmp_path, monkeypatch):
    gw = Gw(tmp_path, contract(tmp_path), deployment={"protect_timeout_s": 0.5, "ner_timeout_s": 0.2})

    def slow(texts):
        time.sleep(2)
        return []
    monkeypatch.setattr(structure, "find_in", slow)
    r = gw.send(ORDINARY[0])
    assert r.status_code == 503 and r.json()["error"]["type"] == "protection_timeout"
    assert not gw.ext.received


# --- header rows: their cells are labels, never entities ------------------------------------------------------------

class _CapsNer:
    """A stand-in NER that tags every capitalised word run as a person (what made "Gross" a PERSON)."""

    def detect(self, text):
        return [Detection(m.start(), m.end(), T.PERSON, 0.8, "D4:ner") for m in re.finditer(r"[A-Z][A-Za-z]+", text)]


@pytest.mark.parametrize("sep", ["  ", "\t", " | ", ","])
def test_table_header_cells_are_never_entities(sep):
    prot = Protector(PolicyPack.load(ROOT / "policies/hris-default.yaml"), Keys(bytes(range(32))), ner=_CapsNer())
    header = sep.join(["Emp No", "Name", "Basic", "Gross", "Manager", "TDS"])
    rows = [sep.join(["48211", "Shantanu Deshmukh", "41250.00", "57750.00", "Ravi Rao", "2100.00"]),
            sep.join(["48212", "Meera Kulkarni", "39100.00", "54740.00", "Anita Shah", "1900.00"])]
    text = "\n".join([header] + rows)
    assert structure.header_rows(text) == [(0, len(header))]
    (dets,) = prot.analyze([{"role": "user", "content": text}], CTX)
    assert not [text[d.start:d.end] for d in dets if d.start < len(header)]
    persons = {text[d.start:d.end] for d in dets if d.type is T.PERSON}
    assert {"Shantanu Deshmukh", "Meera Kulkarni"} <= persons


def test_a_row_of_values_is_never_taken_for_a_header():
    for text in ("SHANTANU DESHMUKH  PUNE  ENGINEER\nMEERA KULKARNI  NAGPUR  ANALYST",
                 "Please find below\n48211  SHANTANU DESHMUKH  41250.00\n48212  MEERA KULKARNI  39100.00"):
        assert structure.header_rows(text) == []


# --- contracts ------------------------------------------------------------------------------------------------------

def test_contract_structured_content_field(tmp_path):
    assert load_contract(ROOT / f"contracts/{P}.yaml").structured_content == "REFUSE"      # the default
    assert load_contract(contract(tmp_path, "a", structured_content={"action": "private"})).structured_content == \
        "PRIVATE"
    for bad in ({"action": "ALLOW"}, {"action": "REFUSE", "threshold": 3}, "REFUSE", {}):
        with pytest.raises(ContractError, match="structured_content"):
            load_contract(contract(tmp_path, "b", structured_content=bad))
    raw = yaml.safe_load((ROOT / "contracts/salary_comparison.yaml").read_text())
    raw["structured_content"] = {"action": "PRIVATE"}
    p = tmp_path / "lc.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ContractError, match="EXT_PSEUDO"):
        load_contract(p)


def test_local_compute_payloads_are_not_structure_checked(tmp_path):
    # a LOCAL_COMPUTE purpose takes a JSON payload by design: the free-text check does not apply there
    raw = yaml.safe_load((ROOT / "contracts/salary_comparison.yaml").read_text())
    assert raw["route"] == "LOCAL_COMPUTE"
    assert load_contract(ROOT / "contracts/salary_comparison.yaml").structured_content == "REFUSE"


# --- cost: linear on 1 MB -------------------------------------------------------------------------------------------

ADVERSARIAL = {
    "csv_rows": lambda n: "1,A,B,2\n" * (n // 8),
    "newlines": lambda n: "\n" * n,
    "brackets": lambda n: "[" * (n // 2) + "]" * (n // 2),
    "braces_keys": lambda n: '{"a":' * (n // 5),
    "tags": lambda n: "<a>" * (n // 6) + "</b>" * (n // 8),
    "kv_lines": lambda n: "key: value\n" * (n // 11),
    "record_rows": lambda n: "48211 SHANTANU DESHMUKH 41250.00 16500.00\n" * (n // 42),
    "quotes": lambda n: '"' * n,
    "pipes": lambda n: "|" * n,
    "gaps": lambda n: "a  b  c\n" * (n // 8),
}


def _best(text: str) -> float:
    b = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        structure.scan(text)
        structure.header_rows(text)
        b = min(b, time.perf_counter() - t0)
    return b


@pytest.mark.parametrize("name", ADVERSARIAL)
def test_one_megabyte_is_scanned_in_linear_time(name):
    gen = ADVERSARIAL[name]
    small, large = _best(gen(250_000)), _best(gen(1_000_000))
    assert large < 6.0
    assert large < 8 * max(small, 0.05)


def test_json_report_never_echoes_content(refuse_gw):
    c = case(3, "json_pretty", 4)
    r = refuse_gw.send(c.text)
    assert r.status_code == 422
    body = json.dumps(r.json())
    for v in c.values:
        assert v not in body
