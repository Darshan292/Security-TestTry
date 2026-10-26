"""Regressions for the independent audit (2026-10): side-channel parameters, salary ties, foreign currency.

Synthetic data only; mock provider; every "nothing was sent" assertion reads what the provider received.
"""
import json
from pathlib import Path
import os

import pytest
import yaml
from starlette.testclient import TestClient

from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.policy import generalize
from pgw.product.contracts import ContractError, load_contract
from pgw.types import EntityType
from tests.sealed import ROOT, legacy_dev, production, sha, source

KEY = "pilot-key"
AADHAAR = "386091390995"
PURPOSES = ("ticket_summary", "salary_comparison", "band_position")


def make(tmp):
    mock = MockLLMUpstream("summarise")
    cfg = production(tmp, [{"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY), "restore_output": True,
                            "models": ["mock-1"], "purposes": list(PURPOSES),
                            "tasks": {p: str(ROOT / f"contracts/{p}.yaml") for p in PURPOSES}}])
    s = Settings(**cfg, ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s)), mock


def post(client, extra, purpose="ticket_summary", content="Payroll arrears missing for October."):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": content}], **extra}
    if purpose == "ticket_summary":
        body["pgw_subjects"] = {"requester": {"name": "Priya Raghunathan"}}
    return client.post("/v1/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose, **source(purpose)})


# --- S1: every non-message field is strictly typed; contracted purposes take no free-text parameters ------------

@pytest.mark.parametrize("extra", [
    {"temperature": f"Aadhaar {AADHAAR}"}, {"max_tokens": "HIV positive"}, {"top_p": [AADHAAR]},
    {"max_completion_tokens": {"x": AADHAAR}}, {"n": AADHAAR}, {"stream": AADHAAR},
    {"stream_options": {"include_usage": True, "note": AADHAAR}}, {"stream_options": AADHAAR},
    {"temperature": True}, {"temperature": 9}, {"max_tokens": 0}, {"top_p": 1.5}, {"n": 2},
    {"stop": ["HIV positive"]}, {"stop": AADHAAR},
    {"tools": [{"type": "function", "function": {"name": "f", "description": f"Aadhaar {AADHAAR}"}}]},
    {"tool_choice": "auto"},
], ids=lambda e: next(iter(e)) + ":" + type(next(iter(e.values()))).__name__)
def test_parameters_cannot_carry_data(tmp_path, extra):
    client, mock = make(tmp_path)
    r = post(client, extra)
    assert r.status_code == 400 and mock.received == []
    assert AADHAAR not in r.text and "HIV" not in r.text


def test_valid_parameters_are_still_forwarded(tmp_path):
    client, mock = make(tmp_path)
    r = post(client, {"temperature": 0.2, "top_p": 1, "max_tokens": 512, "n": 1, "stream": False})
    assert r.status_code == 200
    sent = json.loads(mock.received[-1])
    assert sent["temperature"] == 0.2 and sent["max_tokens"] == 512


def test_legacy_development_path_is_typed_too(tmp_path):
    reg = tmp_path / "legacy.yaml"
    reg.write_text(yaml.safe_dump({"apps": [{"app_id": "legacy", "tenant": "acme", "key_sha256": sha(KEY),
                                             "purposes": ["email_draft"], "models": ["mock-1"]}]}))
    mock = MockLLMUpstream("summarise")
    s = Settings(policy_path=str(ROOT / "policies/hris-default.yaml"), **legacy_dev(reg),
                 ledger_db_path=str(tmp_path / "l.sqlite"), egress_ledger_path=str(tmp_path / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    client = TestClient(create_app(s))
    r = client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "email_draft"},
                    json={"model": "mock-1", "temperature": f"Aadhaar {AADHAAR}",
                          "messages": [{"role": "user", "content": "hello"}]})
    assert r.status_code == 400 and mock.received == []


# --- C1: equal salaries are reported as equal ---------------------------------------------------------------------

def _salary(a, b):
    return json.dumps({"instruction": "Who earns more, and by how much?",
                       "employees": [{"name": "Amit Trivedi", "employee_id": "KD-10989", "annual_ctc": a},
                                     {"name": "Hetal Shah", "employee_id": "KD-11063", "annual_ctc": b}]})


def test_equal_salaries_are_not_ranked(tmp_path):
    client, mock = make(tmp_path)
    assert post(client, {}, "salary_comparison", _salary(2450000, 2450000)).status_code == 200
    wire = mock.received[-1].decode()
    assert "Both have exactly the same annual CTC." in wire and "higher annual CTC" not in wire
    assert post(client, {}, "salary_comparison", _salary(2450000, 2180000)).status_code == 200
    assert "The first of these two has the higher annual CTC." in mock.received[-1].decode()


# --- C2: amounts in another currency get no (wrong) INR band ------------------------------------------------------

@pytest.mark.parametrize("v", ["USD 142,000", "$142,000", "EUR 90,000", "£50,000"])
def test_foreign_currency_is_not_banded_in_inr(v):
    assert generalize(EntityType.COMPENSATION, v) == "[COMPENSATION]"


def test_inr_amounts_still_banded():
    assert generalize(EntityType.COMPENSATION, "Rs 7,20,000") == "[COMPENSATION ₹5L–₹10L]"


# --- S2: no caller-supplied "public" value crosses unchecked; band limits come from the sealed contract -----------

def _band(band):
    return json.dumps({"instruction": "Where does this employee sit in the band?",
                       "employee": [{"name": "Hetal Shah", "employee_id": "KD-11063", "annual_ctc": 2180000}],
                       "band": [band]})


@pytest.mark.parametrize("band", [
    {"name": "ABCPK1234F"}, {"name": "Asha 24.5L"}, {"name": "P9"}, {"name": "HIV"},
    {"name": "P3", "min_ctc": 2179999, "max_ctc": 2180001},
], ids=["pan", "free-text", "unknown", "word", "caller-limits"])
def test_band_payload_cannot_carry_data(tmp_path, band):
    client, mock = make(tmp_path)
    r = post(client, {}, "band_position", _band(band))
    assert r.status_code == 400 and mock.received == []
    assert "ABCPK1234F" not in r.text and "2179999" not in r.text


def test_band_limits_come_from_the_sealed_table(tmp_path):
    client, mock = make(tmp_path)
    assert post(client, {}, "band_position", _band({"name": "P3"})).status_code == 200
    wire = mock.received[-1].decode()
    assert "₹18,00,000" in wire and "₹26,00,000" in wire and "2180000" not in wire


@pytest.mark.parametrize("mutate,match", [
    (lambda c: c["input_schema"]["band"]["items"].update(cap={"type": "int", "class": "C0"}), "C0 inputs"),
    (lambda c: c["input_schema"]["band"]["items"]["name"].pop("one_of"), "C0 inputs"),
    (lambda c: c["reference"]["pay_bands"].update(P5=[2180000, 2180001]), "20% wide"),
])
def test_contracts_refuse_unchecked_public_inputs(tmp_path, mutate, match):
    c = yaml.safe_load((ROOT / "contracts/band_position.yaml").read_text())
    mutate(c)
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(c, allow_unicode=True))
    with pytest.raises(ContractError, match=match):
        load_contract(p)


# --- S4: the roster is sealed in production -------------------------------------------------------------------------

def test_swapped_or_unsealed_roster_stops_production_startup(tmp_path):
    from pgw.product.integrity import IntegrityError
    roster = ROOT / "tests/fixtures/slice_roster.jsonl"
    cfg = production(tmp_path, [{"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY), "models": ["mock-1"],
                                 "purposes": ["ticket_summary"],
                                 "tasks": {"ticket_summary": str(ROOT / "contracts/ticket_summary.yaml")}}],
                     roster=roster)
    base = dict(ledger_db_path=str(tmp_path / "l.sqlite"), egress_ledger_path=str(tmp_path / "e.jsonl"),
                master_key=os.urandom(32).hex(), upstream=MockLLMUpstream("summarise"))
    create_app(Settings(**cfg, **base))                              # sealed roster starts
    with pytest.raises(IntegrityError):                              # a roster outside the sealed config
        create_app(Settings(**{**cfg, "roster_path": str(roster)}, **base))
    Path(cfg["roster_path"]).write_text("")                          # emptied after signing
    with pytest.raises(IntegrityError):
        create_app(Settings(**cfg, **base))
