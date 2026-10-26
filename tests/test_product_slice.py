"""Product slice (PRODUCT_ARCHITECTURE.md): contracts + routing + local compute, end to end through the HTTP app.

PATH A: ticket summary (EXT_PSEUDO). PATH B: salary comparison (LOCAL_COMPUTE). Synthetic data only.
Every assertion about what "left the building" reads the exact bytes the upstream received.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

import pgw.product.local_compute as lc
from pgw.gateway.app import create_app
from tests.sealed import attest, legacy_dev, production
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.pipeline import Protector
from pgw.product.contracts import ContractError

ROOT = Path(__file__).resolve().parents[1]
KEY, LEGACY_KEY = "pilot-key", "legacy-key"

TICKET = (
    "Ticket PAY-88231 | Category: Payroll | Raised by: Imran Ansari (KD-11914) | Status: Resolved\n"
    "[28 Sep 2026] Imran Ansari: My October TDS projection shows an extra deduction of Rs 4,500. I had uploaded "
    "rent receipts but HRA exemption was not applied. PAN ZXWPA3889P.\n"
    "[29 Sep 2026] Payroll Ops: Rent receipts were uploaded after the 25 September cut-off. Declaration re-opened.\n"
    "[30 Sep 2026] Payroll Ops: The excess TDS of Rs 4,500 will be refunded in the November 2026 payroll to salary "
    "account 644748074748 (IFSC SBIN0095056). Contact imran.ansari@kestrel-dyn.in if needed.\n")
TICKET_RAW = ["Imran Ansari", "KD-11914", "ZXWPA3889P", "644748074748", "imran.ansari@kestrel-dyn.in"]
SUMMARY_ASK = "Summarise ticket PAY-88231 in three lines for the weekly payroll review.\n---\n" + TICKET

PAYLOAD = {"instruction": "Who earns more among these two, and by how much? Write a two-line note.",
           "employees": [{"name": "Amit Trivedi", "employee_id": "KD-10989", "annual_ctc": 2450000},
                         {"name": "Hetal Shah", "employee_id": "KD-11063", "annual_ctc": 2180000}]}
SALARY_FORMS = ["2450000", "2180000", "24,50,000", "21,80,000", "2,450,000", "2,180,000"]


def sha(k):
    return hashlib.sha256(k.encode()).hexdigest()


def apps(tasks=None):
    tasks = tasks or {"ticket_summary": str(ROOT / "contracts/ticket_summary.yaml"),
                      "salary_comparison": str(ROOT / "contracts/salary_comparison.yaml"),
                      "esi_letter": str(ROOT / "contracts/esi_letter.yaml")}
    return [{"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY), "purposes": sorted(tasks),
             "restore_output": True, "models": ["mock-1"], "tasks": tasks}]


def base(tmp, mock, tasks=None):
    """Production mode: a sealed, signed copy of the config (tests/sealed.py)."""
    return Settings(**production(tmp, apps(tasks), roster=ROOT / "tests/fixtures/slice_roster.jsonl"),
                    ledger_db_path=str(tmp / "ledger.sqlite"), egress_ledger_path=str(tmp / "egress.jsonl"),
                    master_key=os.urandom(32).hex(), upstream=mock)


def make(tmp, behaviour="summarise", tasks=None):
    mock = MockLLMUpstream(behaviour)
    return TestClient(create_app(base(tmp, mock, tasks))), mock


# Path A contracts require the app to declare the requester; only the requester is declared here, so the other
# values in these tests are still protected by detection.
REQUESTER = {"requester": {"name": "Imran Ansari"}}


def call(client, purpose, content, key=KEY, subjects=None):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": content}]}
    if purpose == "ticket_summary":
        body["pgw_subjects"] = subjects or REQUESTER
    return client.post("/v1/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {key}", "X-PGW-Purpose": purpose, **attest(purpose, body)})


def sent(mock) -> str:
    assert mock.received, "nothing was sent upstream"
    return mock.received[-1].decode("utf-8")


def answer(r) -> str:
    return r.json()["choices"][0]["message"]["content"]


# --- PATH A ------------------------------------------------------------------------------------------------

def test_path_a_raw_values_never_leave_and_high_ids_do_not_block(tmp_path):
    client, mock = make(tmp_path)
    r = call(client, "ticket_summary", SUMMARY_ASK)
    assert r.status_code == 200, r.text
    wire = sent(mock)
    for v in TICKET_RAW:
        assert v not in wire, f"raw value left the gateway: {v[:4]}..."
    assert "[PAN]" in wire and "[BANK_ACCOUNT]" in wire          # removed, not blocked
    assert "[PERSON_" in wire and "[EMPLOYEE_ID_" in wire and "[EMAIL_" in wire   # aliased
    assert "PAY-88231" in wire and "25 September" in wire        # C0 business content crosses unchanged
    assert "placeholders for confidential values" in wire        # gateway's instruction to the model


def test_path_a_same_request_is_blocked_by_the_legacy_presence_rule(tmp_path):
    # Comparison with the pre-contract behaviour: reachable only through the explicit development-only legacy mode.
    reg = tmp_path / "legacy.yaml"
    reg.write_text(yaml.safe_dump({"apps": [{"app_id": "legacy", "tenant": "acme", "key_sha256": sha(LEGACY_KEY),
                                             "purposes": ["hr_case_summary"], "models": ["mock-1"]}]}))
    mock = MockLLMUpstream("summarise")
    s = Settings(policy_path=str(ROOT / "policies/hris-default.yaml"), **legacy_dev(reg),
                 roster_path=str(ROOT / "tests/fixtures/slice_roster.jsonl"),
                 ledger_db_path=str(tmp_path / "l.sqlite"), egress_ledger_path=str(tmp_path / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    client = TestClient(create_app(s))
    r = call(client, "hr_case_summary", SUMMARY_ASK, key=LEGACY_KEY)
    assert r.status_code == 422 and not mock.received        # the behaviour the contract replaces


def test_path_a_identities_restored_c3_never_returned(tmp_path):
    client, mock = make(tmp_path)
    out = answer(call(client, "ticket_summary", SUMMARY_ASK))
    assert "Imran Ansari" in out and "KD-11914" in out          # authorised identities restored
    assert "[unknown" not in out                                # the gateway's own note never reads as an alias
    assert "[PERSON_" not in out and "[EMPLOYEE_ID_" not in out
    assert "ZXWPA3889P" not in out and "644748074748" not in out


def test_path_a_non_latin_script_is_refused_not_sent(tmp_path):
    client, mock = make(tmp_path)
    r = call(client, "ticket_summary", "Summarise: नॉमिनी रामलाल तिवारी ने क्लेम भेजा है।")
    assert r.status_code == 422 and r.json()["error"]["type"] == "content_requires_private_route"
    assert not mock.received


# --- PATH B ------------------------------------------------------------------------------------------------

def test_path_b_exact_salaries_stay_inside_and_only_derived_result_is_sent(tmp_path):
    client, mock = make(tmp_path)
    r = call(client, "salary_comparison", json.dumps(PAYLOAD))
    assert r.status_code == 200, r.text
    wire = sent(mock)
    for v in SALARY_FORMS + ["Amit Trivedi", "Hetal Shah", "KD-10989", "KD-11063", "2,70,000", "270000"]:
        assert v not in wire, f"value left the gateway: {v[:4]}..."
    assert "12.4" not in wire                                    # derived from pay: TRANSFORMED, never raw
    assert "[PERSON_" in wire and "[DERIVED_" in wire            # identities and derived values aliased


def test_path_b_answer_restores_names_and_exact_difference(tmp_path):
    client, mock = make(tmp_path)
    out = answer(call(client, "salary_comparison", json.dumps(PAYLOAD)))
    assert "Amit Trivedi" in out and "Hetal Shah" in out and "₹2,70,000" in out and "12.4%" in out
    for v in SALARY_FORMS:                                        # the inputs themselves are never echoed back
        assert v not in out


def test_local_compute_function_is_exact():
    r = lc.salary_difference(PAYLOAD)
    assert (r["higher_name"], r["difference_value"], r["pct_of_lower"], r["difference"]) == \
        ("Amit Trivedi", 270000, "12.4%", "₹2,70,000")


# --- fail closed -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "not json",
    json.dumps({**PAYLOAD, "extra": 1}),
    json.dumps({**PAYLOAD, "employees": PAYLOAD["employees"][:1]}),
    json.dumps({**PAYLOAD, "employees": [{**PAYLOAD["employees"][0], "annual_ctc": "24 lakh"},
                                         PAYLOAD["employees"][1]]}),
])
def test_invalid_contract_payload_is_rejected_before_sending(tmp_path, payload):
    client, mock = make(tmp_path)
    r = call(client, "salary_comparison", payload)
    assert r.status_code == 400 and r.json()["error"]["type"] == "invalid_contract_payload"
    assert "Amit" not in r.text and "2450000" not in r.text      # error never echoes values
    assert not mock.received


def test_compute_failure_fails_closed(tmp_path, monkeypatch):
    def boom(payload, contract=None):
        raise RuntimeError("bug")
    monkeypatch.setitem(lc.FUNCTIONS, "salary_difference", boom)
    client, mock = make(tmp_path)
    r = call(client, "salary_comparison", json.dumps(PAYLOAD))
    assert r.status_code == 503 and not mock.received


def test_detector_failure_fails_closed(tmp_path, monkeypatch):
    def boom(self, text):
        raise RuntimeError("detector down")
    monkeypatch.setattr(Protector, "detect_text", boom)
    client, mock = make(tmp_path)
    for purpose, content in (("ticket_summary", SUMMARY_ASK), ("salary_comparison", json.dumps(PAYLOAD))):
        r = call(client, purpose, content)
        assert r.status_code == 503 and not mock.received


def test_egress_backstop_catches_a_buggy_compute_that_leaks_an_exact_salary(tmp_path, monkeypatch):
    real = lc.run

    def leaky(contract, messages):
        res = real(contract, messages)
        res.prompt += "\n(debug: inputs were 2450000 and 2180000)"   # a bug that would leak exact values
        return res
    monkeypatch.setattr(lc, "run", leaky)
    client, mock = make(tmp_path)
    r = call(client, "salary_comparison", json.dumps(PAYLOAD))
    assert r.status_code == 500 and r.json()["error"]["type"] == "PGW_EGRESS_VIOLATION"
    assert not mock.received


def test_uncontracted_purpose_is_refused(tmp_path):
    a = apps()
    a[0]["purposes"] = [*a[0]["purposes"], "uncontracted"]
    s = Settings(**production(tmp_path, a), ledger_db_path=str(tmp_path / "l.sqlite"),
                 egress_ledger_path=str(tmp_path / "e.jsonl"), master_key=os.urandom(32).hex(),
                 upstream=MockLLMUpstream("summarise"))
    with pytest.raises(ContractError, match="without a data contract"):
        create_app(s)          # an uncontracted purpose stops startup (request-level checks: test_mandatory_contracts)
    client, mock = make(tmp_path / "ok")
    r = call(client, "uncontracted", "hello")
    assert r.status_code == 403 and r.json()["error"]["type"] == "purpose_forbidden" and not mock.received


def test_upstream_failure_returns_no_content(tmp_path):
    class Down(MockLLMUpstream):
        async def complete(self, body):
            raise RuntimeError("provider down")
    r = call(TestClient(create_app(base(tmp_path, Down("summarise")))), "ticket_summary", SUMMARY_ASK)
    assert r.status_code == 502 and "Imran" not in r.text


@pytest.mark.parametrize("mutate,why", [
    (lambda c: c["data_classes"].update(C1="ALLOW"), "raw identities would cross"),
    (lambda c: c["data_classes"].update(C3="ALIAS"), "C3 may only be removed or refused"),
    (lambda c: c.update(restore=["C1", "C3"]), "C3 is never restored"),
    (lambda c: c.update(route="SOMEWHERE"), "unknown route"),
    (lambda c: c["data_classes"].pop("C2"), "every class needs an explicit action"),
])
def test_unsafe_contracts_stop_the_gateway_from_starting(tmp_path, mutate, why):
    c = yaml.safe_load((ROOT / "contracts/ticket_summary.yaml").read_text())
    mutate(c)
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(c))
    with pytest.raises(ContractError):
        make(tmp_path, tasks={"ticket_summary": str(bad)})


def test_contract_cannot_override_an_existing_policy_purpose(tmp_path):
    cfg = production(tmp_path, [{"app_id": "x", "tenant": "acme", "key_sha256": sha(KEY),
                                 "purposes": ["hr_case_summary"], "models": ["mock-1"],
                                 "tasks": {"hr_case_summary": str(ROOT / "contracts/ticket_summary.yaml")}}])
    s = Settings(**cfg, ledger_db_path=str(tmp_path / "l.sqlite"), egress_ledger_path=str(tmp_path / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=MockLLMUpstream("summarise"))
    with pytest.raises(ContractError):
        create_app(s)


def test_ledger_records_route_and_contract_without_content(tmp_path):
    client, mock = make(tmp_path)
    call(client, "ticket_summary", SUMMARY_ASK)
    call(client, "salary_comparison", json.dumps(PAYLOAD))
    rows = [json.loads(line) for line in (tmp_path / "egress.jsonl").read_text().splitlines()]
    assert [(r["route"], r["contract"], r["decision"]) for r in rows] == [
        ("EXT_PSEUDO", "ticket_summary@5", "SENT"), ("LOCAL_COMPUTE", "salary_comparison@5", "SENT")]
    text = (tmp_path / "egress.jsonl").read_text()
    for v in TICKET_RAW + SALARY_FORMS + ["Amit Trivedi"]:
        assert v not in text


# --- derived-value semantics (contract outputs: RAW / TRANSFORMED / INTERNAL) ---------------------------------

ESI = {"instruction": "Explain to the employee whether ESI applies to them, in two lines.",
       "employee": [{"name": "Hetal Shah", "employee_id": "KD-11063", "monthly_gross": 19800}]}


def test_esi_raw_output_crosses_only_by_contract_and_wage_never_does(tmp_path):
    client, mock = make(tmp_path)
    r = call(client, "esi_letter", json.dumps(ESI))
    assert r.status_code == 200, r.text
    wire = sent(mock)
    assert "covered by ESI" in wire and "₹21,000" in wire        # RAW outputs, as the contract justifies
    for v in ("19800", "19,800", "1200", "1,200", "Hetal Shah", "KD-11063"):   # wage, INTERNAL margin, identity
        assert v not in wire
    assert "Hetal Shah" in answer(r)


def test_unlisted_compute_output_is_internal_and_checked_at_egress(tmp_path, monkeypatch):
    real = lc.FUNCTIONS["salary_difference"]
    monkeypatch.setitem(lc.FUNCTIONS, "salary_difference", lambda p, c=None: {**real(p, c), "secret_note": "budget code ZZ-9931"})
    from pgw.product.contracts import load_contract
    res = lc.run(load_contract(ROOT / "contracts/salary_comparison.yaml"),
                 [{"role": "user", "content": json.dumps(PAYLOAD)}])
    assert "ZZ-9931" not in res.prompt and any("ZZ-9931" in v for _, v in res.withheld)


@pytest.mark.parametrize("mutate", [
    lambda c: c["outputs"]["pct_of_lower"].update(egress="RAW"),                  # RAW from C2, no justification
    lambda c: c["outputs"]["difference_value"].update(egress="INTERNAL") or
              c.update(prompt_template=c["prompt_template"] + " {difference_value}"),  # template uses INTERNAL
    lambda c: c["outputs"].update(leak={"derived_from": "C3", "egress": "TRANSFORMED"}),  # C3-derived leaving
    lambda c: c["purpose_dependent"].pop("performance_rating"),                   # stance missing
    lambda c: c["input_schema"]["employees"]["items"]["annual_ctc"].pop("class"),  # unclassified input
])
def test_unsafe_derived_value_contracts_stop_startup(tmp_path, mutate):
    c = yaml.safe_load((ROOT / "contracts/salary_comparison.yaml").read_text())
    mutate(c)
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(c))
    with pytest.raises(ContractError):
        make(tmp_path, tasks={"salary_comparison": str(bad)})
