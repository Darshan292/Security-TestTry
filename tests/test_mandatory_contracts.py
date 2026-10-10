"""Security-boundary invariants (SECURITY_BOUNDARY.md): mandatory data contracts, no production legacy path,
unambiguous purpose -> contract resolution, and the source-side input boundary for NOT_EXPECTED attributes.

Synthetic data only. Every "nothing was sent" assertion reads what the mock provider actually received.
"""
import json
import logging
import os
import re
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

import pgw.gateway.app as gw
from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product.contracts import ContractError, load_contract
from tests.sealed import ROOT, legacy_dev, production, sha, source

KEY = "pilot-key"
CANARY = "Zorawar Pemberton-Okafor"           # synthetic; must never appear in a refusal, log or ledger
CANARY_TEXT = f"Ticket for {CANARY}: payroll arrears missing, please check the October run."
TICKET = str(ROOT / "contracts/ticket_summary.yaml")


def app_entry(purposes, tasks=None, key=KEY, app_id="pilot", tenant="acme"):
    return {"app_id": app_id, "tenant": tenant, "key_sha256": sha(key), "purposes": purposes,
            "restore_output": True, "models": ["mock-1"], **({"tasks": tasks} if tasks else {})}


def settings(tmp, apps, mock=None, **kw):
    return Settings(**{**production(tmp, apps), **kw}, ledger_db_path=str(tmp / "l.sqlite"),
                    egress_ledger_path=str(tmp / "e.jsonl"), master_key=os.urandom(32).hex(),
                    upstream=mock or MockLLMUpstream("summarise"))


def post(client, purpose, text=CANARY_TEXT, headers=None, subjects=True):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": text}]}
    if subjects and purpose.startswith("ticket_summary"):
        body["pgw_subjects"] = {"requester": {"name": CANARY}}
    return client.post("/v1/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose, **(headers or {})})


def ledger(tmp) -> str:
    p = tmp / "e.jsonl"
    return p.read_text() if p.exists() else ""


# --- 1. mandatory contracts -------------------------------------------------------------------------------------

def test_production_registry_has_no_uncontracted_purpose():
    raw = yaml.safe_load((ROOT / "registry/apps.yaml").read_text())
    assert raw["apps"], "registry is empty"
    for a in raw["apps"]:
        assert a.get("tasks"), f"{a['app_id']} has no data contracts"
        assert set(a["purposes"]) == set(a["tasks"]), f"{a['app_id']}: uncontracted purposes"
    assert "hr-assistant" not in {a["app_id"] for a in raw["apps"]}


@pytest.mark.parametrize("apps", [
    [app_entry(["ticket_summary", "uncontracted"], {"ticket_summary": TICKET})],   # one purpose lacks a contract
    [app_entry(["email_draft"])],                                                 # an app with no contracts at all
], ids=["purpose", "app"])
def test_uncontracted_purpose_or_app_stops_startup(tmp_path, apps):
    with pytest.raises(ContractError, match="without a data contract"):
        create_app(settings(tmp_path, apps))
    with pytest.raises(ContractError, match="without a data contract"):   # development without the legacy flag too
        create_app(settings(tmp_path, apps, mode="development"))


def test_request_path_refuses_uncontracted_purpose_even_if_startup_check_is_bypassed(tmp_path, monkeypatch, caplog):
    # Defence in depth: simulate a broken startup check; the request path must still refuse before any send.
    monkeypatch.setattr(gw, "check_startup_mode", lambda s, r: None)
    mock = MockLLMUpstream("summarise")
    client = TestClient(create_app(settings(tmp_path, [app_entry(["ticket_summary", "email_draft"],
                                                                  {"ticket_summary": TICKET})], mock)))
    caplog.set_level(logging.DEBUG)
    r = post(client, "email_draft", headers={"X-PGW-Input-Source": "helpdesk-ticket-export@1"})
    assert r.status_code == 403 and r.json()["error"]["type"] == "no_data_contract"
    assert mock.received == []                                   # no provider call
    assert CANARY not in r.text and "payroll" not in r.text      # no request content in the response
    assert CANARY not in caplog.text and CANARY not in ledger(tmp_path)


def test_uncontracted_purpose_is_refused_before_the_body_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(gw, "check_startup_mode", lambda s, r: None)
    mock = MockLLMUpstream("summarise")
    client = TestClient(create_app(settings(tmp_path, [app_entry(["ticket_summary", "email_draft"],
                                                                  {"ticket_summary": TICKET})], mock)))
    r = client.post("/v1/chat/completions", content=b"{not json " + CANARY.encode(),
                    headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "email_draft",
                             "content-type": "application/json"})
    assert r.status_code == 403 and r.json()["error"]["type"] == "no_data_contract" and not mock.received


def test_legacy_path_cannot_be_enabled_in_production(tmp_path):
    reg = tmp_path / "legacy.yaml"
    reg.write_text(yaml.safe_dump({"apps": [app_entry(["email_draft"])]}))
    base = dict(policy_path=str(ROOT / "policies/hris-default.yaml"), ledger_db_path=str(tmp_path / "l.sqlite"),
                egress_ledger_path=str(tmp_path / "e.jsonl"), master_key=os.urandom(32).hex(),
                upstream=MockLLMUpstream("summarise"))
    with pytest.raises(ValueError, match="development-only"):
        create_app(Settings(**{**legacy_dev(reg), "mode": "production"}, **base))
    signed = production(tmp_path / "p", [app_entry(["ticket_summary"], {"ticket_summary": TICKET})])
    with pytest.raises(ValueError, match="development-only"):      # even with a valid signed config
        create_app(Settings(**{**base, **signed}, legacy_uncontracted=True))
    create_app(Settings(**legacy_dev(reg), **base))               # explicit development opt-in still works


def test_legacy_requests_are_refused_if_mode_changes_after_startup(tmp_path):
    reg = tmp_path / "legacy.yaml"
    reg.write_text(yaml.safe_dump({"apps": [app_entry(["email_draft"])]}))
    mock = MockLLMUpstream("summarise")
    s = Settings(policy_path=str(ROOT / "policies/hris-default.yaml"), **legacy_dev(reg),
                 ledger_db_path=str(tmp_path / "l.sqlite"), egress_ledger_path=str(tmp_path / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    client = TestClient(create_app(s))
    assert post(client, "email_draft").status_code == 200
    s.mode = "production"
    r = post(client, "email_draft")
    assert r.status_code == 403 and len(mock.received) == 1 and CANARY not in r.text


def test_defaults_and_environment_cannot_enable_the_legacy_path():
    assert Settings().mode == "production" and Settings().legacy_uncontracted is False
    for env in ({}, {"PGW_MODE": "development"}, {"PGW_LEGACY_UNCONTRACTED": "1", "PGW_LEGACY": "1",
                                                 "PGW_DEV_LEGACY_UNCONTRACTED": "1", "LEGACY_UNCONTRACTED": "true"}):
        s = Settings.from_env(env)
        assert s.legacy_uncontracted is False and s.integrity_manifest is not None
    assert Settings.from_env({}).mode == "production"
    assert Settings.from_env({"PGW_TRUSTED_CONFIG_PUBKEY": "ab" * 32}).trusted_config_pubkey == "ab" * 32


def test_only_explicit_development_helpers_enable_the_legacy_path():
    """Compatibility tests reach the legacy path only through tests/sealed.legacy_dev (or the labelled legacy demo);
    production code never sets the flag."""
    allowed = {"tests/sealed.py", "tests/test_mandatory_contracts.py", "demo/demo_gateway.py"}
    hits = set()
    for p in [*ROOT.glob("pgw/**/*.py"), *ROOT.glob("tests/*.py"), *ROOT.glob("demo/*.py"), *ROOT.glob("scripts/*.py")]:
        if re.search(r"legacy_uncontracted\s*=\s*True|\"legacy_uncontracted\":\s*True", p.read_text()):
            hits.add(p.relative_to(ROOT).as_posix())
    assert hits <= allowed, hits - allowed


# --- 3. unambiguous purpose -> contract resolution ------------------------------------------------------------

def test_same_purpose_same_ref_different_content_stops_startup(tmp_path):
    variant = tmp_path / "variant" / "ticket_summary.yaml"
    variant.parent.mkdir()
    text = Path(TICKET).read_text()
    variant.write_text(text.replace("C2: GENERALIZE", "C2: REDACT"))      # same contract name and version string
    assert load_contract(variant).ref == load_contract(TICKET).ref
    apps = [app_entry(["ticket_summary"], {"ticket_summary": TICKET}),
            app_entry(["ticket_summary"], {"ticket_summary": str(variant)}, key="other", app_id="b", tenant="initech")]
    with pytest.raises(ContractError, match="different content"):
        create_app(settings(tmp_path, apps))


def test_same_purpose_identical_content_is_accepted(tmp_path):
    copy = tmp_path / "copy" / "ticket_summary.yaml"
    copy.parent.mkdir()
    copy.write_bytes(Path(TICKET).read_bytes())
    apps = [app_entry(["ticket_summary"], {"ticket_summary": TICKET}),
            app_entry(["ticket_summary"], {"ticket_summary": str(copy)}, key="other", app_id="b", tenant="initech")]
    create_app(settings(tmp_path, apps))


def test_contract_map_is_global_not_tenant_scoped(tmp_path):
    """Documented limitation: one purpose means one contract for every tenant (no tenant-specific contracts)."""
    reg = gw.load_registry(production(tmp_path, [
        app_entry(["ticket_summary"], {"ticket_summary": TICKET}),
        app_entry(["ticket_summary"], {"ticket_summary": TICKET}, key="o", app_id="b", tenant="initech")])
        ["registry_path"])
    from pgw.policy import PolicyPack
    contracts, _ = gw.load_contracts(reg, PolicyPack.load(str(ROOT / "policies/hris-default.yaml")))
    assert list(contracts) == ["ticket_summary"]


# --- 4. NOT_EXPECTED: source-side input boundary -----------------------------------------------------------------

MEDICAL = ("Ticket for Zorawar Pemberton-Okafor: leave without pay query. Note from manager: employee is on "
           "chemotherapy and had a disciplinary warning in July.")


def ticket_client(tmp, mock=None, purpose="ticket_summary", **kw):
    mock = mock or MockLLMUpstream("summarise")
    path = str(ROOT / f"contracts/{purpose}.yaml")
    return TestClient(create_app(settings(tmp, [app_entry([purpose], {purpose: path})], mock, **kw))), mock


@pytest.mark.parametrize("hdr", [{}, {"X-PGW-Input-Source": "free-text@1"}, {"X-PGW-Input-Source": ""},
                                 {"X-PGW-Input-Source": "hris-case-export@1"}],      # another contract's sanitizer
                         ids=["missing", "unknown", "empty", "other-contract"])
def test_unattested_input_is_refused_before_any_send(tmp_path, hdr, caplog):
    client, mock = ticket_client(tmp_path)
    caplog.set_level(logging.DEBUG)
    r = post(client, "ticket_summary", MEDICAL, hdr)
    assert r.status_code == 422 and r.json()["error"]["type"] == "input_not_source_sanitized"
    assert mock.received == []
    for v in (CANARY, "chemotherapy", "disciplinary"):
        assert v not in r.text and v not in caplog.text and v not in ledger(tmp_path)
    row = json.loads(ledger(tmp_path).splitlines()[-1])
    assert row["decision"] == "BLOCKED" and row["reason"] == "input_not_sanitized"


def test_local_compute_free_text_needs_the_attestation_too(tmp_path):
    client, mock = ticket_client(tmp_path, purpose="salary_comparison")
    payload = {"instruction": "Compare; note one of them is on medical leave.",
               "employees": [{"name": "Amit Trivedi", "employee_id": "KD-10989", "annual_ctc": 2450000},
                             {"name": "Hetal Shah", "employee_id": "KD-11063", "annual_ctc": 2180000}]}
    r = post(client, "salary_comparison", json.dumps(payload))
    assert r.status_code == 422 and not mock.received
    assert post(client, "salary_comparison", json.dumps(payload), source("salary_comparison")).status_code == 200


def test_attested_input_is_sent_and_recorded(tmp_path):
    client, mock = ticket_client(tmp_path)
    r = post(client, "ticket_summary", CANARY_TEXT, source("ticket_summary"))
    assert r.status_code == 200 and len(mock.received) == 1
    row = json.loads(ledger(tmp_path).splitlines()[-1])
    assert row["decision"] == "SENT" and row["input_source"] == "helpdesk-ticket-export@1"


def test_not_expected_content_is_not_removed_when_attested(tmp_path):
    """LIMITATION, asserted so it cannot be claimed away: the gateway does not detect or remove medical or
    disciplinary detail from free text. If the app attests a sanitizer that let it through, it is sent."""
    client, mock = ticket_client(tmp_path)
    assert post(client, "ticket_summary", MEDICAL, source("ticket_summary")).status_code == 200
    wire = mock.received[-1].decode()
    assert "chemotherapy" in wire and "disciplinary warning" in wire and CANARY not in wire


def test_unattested_input_can_be_kept_in_boundary(tmp_path):
    from tests.test_private_route import LocalModel
    local = LocalModel()
    client, ext = ticket_client(tmp_path, purpose="ticket_summary_any_script", private_upstream=local)
    r = post(client, "ticket_summary_any_script", MEDICAL)
    assert r.status_code == 200 and r.headers["X-PGW-Route"] == "PRIVATE_LOCAL"
    assert ext.received == [] and len(local.received) == 1
    row = json.loads(ledger(tmp_path).splitlines()[-1])
    assert row["decision"] == "SENT_PRIVATE" and row["reason"] == "input_not_sanitized"


@pytest.mark.parametrize("mutate,match", [
    (lambda c: c.pop("external_input"), "must declare external_input"),
    (lambda c: c["external_input"].update(sanitizers=[]), "sanitizers"),
    (lambda c: c["external_input"].update(sanitizers=["Bad Id!"]), "sanitizers"),
    (lambda c: c["external_input"].update(unattested="ALLOW"), "REFUSE or PRIVATE"),
    (lambda c: c["external_input"].update(trust_app=True), "unknown keys"),
])
def test_contracts_must_declare_a_valid_input_boundary(tmp_path, mutate, match):
    c = yaml.safe_load(Path(TICKET).read_text())
    mutate(c)
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(c))
    with pytest.raises(ContractError, match=match):
        load_contract(p)


def test_private_unattested_needs_ext_pseudo_and_a_private_endpoint(tmp_path):
    c = yaml.safe_load((ROOT / "contracts/salary_comparison.yaml").read_text())
    c["external_input"]["unattested"] = "PRIVATE"
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(c))
    with pytest.raises(ContractError, match="EXT_PSEUDO"):
        load_contract(p)
    with pytest.raises(ContractError, match="private endpoint"):
        ticket_client(tmp_path, purpose="ticket_summary_any_script")


def test_every_shipped_contract_declares_its_input_boundary():
    for p in sorted((ROOT / "contracts").glob("*.yaml")):
        c = load_contract(p)
        assert c.external_input.sanitizers and c.external_input.unattested in ("REFUSE", "PRIVATE"), p.name
        assert "NOT_EXPECTED" in c.purpose_dependent.values()


def test_health_reports_mode(tmp_path):
    client, _ = ticket_client(tmp_path)
    assert client.get("/healthz").json() == {"status": "ok", "mode": "production"}
