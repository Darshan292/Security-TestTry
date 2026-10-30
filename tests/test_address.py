"""ADDRESS: a home or postal address is personal data (class C1), detected in free text, aliased upstream and restored
downstream like a name. Synthetic addresses only. Production mode, signed config, attested input, the provider mock's
received bytes inspected.

Rule-based coverage (pgw.detect.places): house marker + street/locality words, a PIN code in an address context, and
a city or state before a PIN; adjacent pieces (also across line breaks) merge into one span. Free-form addresses are the
ML detector's job (its address label is mapped to ADDRESS in the signed profile): tested with the fixture model.
"""
import hashlib
import json
import os

import pytest
import yaml
from starlette.testclient import TestClient

from pgw import egress
from pgw.detect.places import addresses
from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.policy import PolicyPack
from pgw.product.contracts import CLASS_OF, ContractError, DataClass, load_contract
from pgw.types import Action, EgressViolation, EntityType as T
from tests.sealed import ROOT, attest, production

KEY = "address-key"
ONE_LINE = "Flat 507, Lakeview Residency, 2nd Main, Koramangala, Bengaluru 560034"
MULTI = "Flat 507, Lakeview Residency,\n2nd Main, Koramangala,\nBengaluru 560034"
SHIPPED = sorted(p.stem for p in (ROOT / "contracts").glob("*.yaml"))


# --- type, class, contracts, policy ---------------------------------------------------------------------------------

def test_every_entity_type_has_a_data_class():
    assert set(T) <= set(CLASS_OF) and CLASS_OF[T.ADDRESS] is DataClass.C1


@pytest.mark.parametrize("name", SHIPPED)
def test_every_shipped_contract_aliases_or_removes_an_address_and_never_allows_it(name):
    c = load_contract(ROOT / f"contracts/{name}.yaml")
    a = c.actions()[T.ADDRESS]
    assert a in (Action.TOKENIZE, Action.REDACT, Action.BLOCK_REQUEST) and a is c.actions()[T.PERSON]
    assert c.restorable(T.ADDRESS) == c.restorable(T.PERSON)


def test_a_contract_that_omits_the_identity_class_is_refused(tmp_path):
    raw = yaml.safe_load((ROOT / "contracts/ticket_summary.yaml").read_text())
    del raw["data_classes"]["C1"]                       # ADDRESS is C1: no C1 action, no ADDRESS action
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ContractError, match="C1"):
        load_contract(p)
    raw["data_classes"]["C1"] = "ALLOW"                 # raw addresses would cross
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ContractError, match="not allowed"):
        load_contract(p)
    raw["data_classes"]["C1"] = "ALIAS"
    raw["data_classes"]["ADDRESS"] = "ALLOW"            # types are not named in contracts: classes are
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ContractError):
        load_contract(p)


def test_a_type_without_a_data_class_is_redacted_not_allowed(monkeypatch):
    monkeypatch.delitem(CLASS_OF, T.ADDRESS)
    c = load_contract(ROOT / "contracts/ticket_summary.yaml")
    assert c.actions()[T.ADDRESS] is Action.REDACT


def test_policy_pack_needs_an_address_default(tmp_path):
    raw = yaml.safe_load((ROOT / "policies/hris-default.yaml").read_text())
    assert PolicyPack.load(ROOT / "policies/hris-default.yaml").defaults[T.ADDRESS] is Action.TOKENIZE
    del raw["defaults"]["ADDRESS"]
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="ADDRESS"):
        PolicyPack.load(p)


# --- rule-based detection (conservative) -----------------------------------------------------------------------------

@pytest.mark.parametrize("text,covered", [
    (f"Please ship the laptop to {ONE_LINE} by Friday.", ["Flat 507, Lakeview Residency, 2nd Main, Koramangala", "560034"]),
    (f"Send it to {MULTI} please.", ["Flat 507, Lakeview Residency,\n2nd Main, Koramangala", "560034"]),
    ("Address: 12, MG Road, Pune - 411001.", ["12, MG Road", "411001"]),
    ("His pin code 560034 is on file", ["560034"]),
])
def test_rules_detect_the_street_part_and_the_pin_but_not_the_city(text, covered):
    spans = addresses(text)
    assert [text[s:e] for s, e, *_ in spans] == covered and {x[2] for x in spans} == {T.ADDRESS}


@pytest.mark.parametrize("text", ["Room 204 on the 3rd floor is free", "Paid Rs 560034 in total", "Ticket 560034 closed",
                                  "The Bengaluru office opens at nine"])
def test_rules_do_not_flag_ordinary_text(text):
    assert addresses(text) == []


# --- end to end ----------------------------------------------------------------------------------------------------

def _gateway(tmp_path, contract="ticket_summary", mode="echo", deployment=None, transparency=True):
    mock = MockLLMUpstream(mode)
    tasks = {contract: str(ROOT / f"contracts/{contract}.yaml")}
    apps = [{"app_id": "pilot", "tenant": "acme", "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(),
             "purposes": sorted(tasks), "restore_output": True, "models": ["mock-1"], "tasks": tasks,
             "transparency": transparency}]
    cfg = production(tmp_path, apps, deployment=deployment)
    s = Settings(**cfg, ledger_db_path=str(tmp_path / "l.sqlite"), egress_ledger_path=str(tmp_path / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s), raise_server_exceptions=False), mock


ROLE = {"hr_email_draft": "recipient"}


def _send(client, text, purpose="ticket_summary"):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": text}],
            "pgw_subjects": {ROLE.get(purpose, "requester"): {"name": "Ritika Banerjee"}}}
    return client.post("/v1/chat/completions", json=body, headers={
        "Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose, **attest(purpose, body)})


@pytest.mark.parametrize("address", [ONE_LINE, MULTI], ids=["single-line", "multi-line"])
@pytest.mark.parametrize("contract", ["ticket_summary", "hr_email_draft"])
def test_address_is_aliased_upstream_and_restored_exactly_downstream(tmp_path, address, contract):
    client, mock = _gateway(tmp_path, contract)
    r = _send(client, f"Ritika asks us to courier the offer letter to {address} before the 5th.", contract)
    assert r.status_code == 200, r.text
    wire = mock.received[-1].decode("utf-8")
    for part in ("Lakeview", "Koramangala", "560034", "2nd Main", "Flat 507"):
        assert part not in wire, part
    sent = json.loads(wire)["messages"][-1]["content"]
    assert 1 <= sent.count("[ADDRESS_") <= 2 and "Bengaluru" in sent          # street part, PIN; the city stays
    ans = r.json()["choices"][0]["message"]["content"]
    assert address in ans and "[ADDRESS" not in ans                  # exact, including the line breaks
    rep = r.json()["pgw_report"]
    d = [x for x in rep["detected"] if x["type"] == "ADDRESS"]
    assert len(d) == 2 and {(x["action"], x["class"]) for x in d} == {("alias", "C1")}
    assert rep["counts"]["by_type"]["ADDRESS"] == 2


def test_the_same_address_written_twice_shares_one_alias(tmp_path):
    client, mock = _gateway(tmp_path)
    text = f"Deliver to {MULTI}. Again, for the record: {ONE_LINE.replace(', ', ',  ')}."
    r = _send(client, text)
    assert r.status_code == 200, r.text
    sent = json.loads(mock.received[-1])["messages"][-1]["content"]
    import re
    assert len(set(re.findall(r"\[ADDRESS_\w+\]", sent))) == 2 and "Koramangala" not in sent


def test_an_address_is_never_restored_when_the_contract_withholds_it(tmp_path):
    raw = yaml.safe_load((ROOT / "contracts/ticket_summary.yaml").read_text())
    raw["data_classes"]["C1"] = "REDACT"
    raw["restore"] = ["C2", "REF"]
    p = tmp_path / "src" / "ticket_summary.yaml"
    p.parent.mkdir()
    p.write_text(yaml.safe_dump(raw, sort_keys=False))
    c = load_contract(p)
    assert c.actions()[T.ADDRESS] is Action.REDACT and not c.restorable(T.ADDRESS)


def test_an_address_in_a_record_structured_message_is_refused_not_sent(tmp_path):
    client, mock = _gateway(tmp_path)
    rows = [{"employee": f"Emp {i}", "name": n, "address": a} for i, (n, a) in enumerate([
        ("Asha Verma", ONE_LINE), ("Imran Ansari", "House No. 17, Gandhi Nagar, Nagpur - 440013"),
        ("Dev Patel", "Plot 44, Sector 9, Noida 201301")])]
    r = _send(client, "Summarise this export:\n" + json.dumps(rows, indent=1))
    assert r.status_code == 422 and r.json()["error"]["type"] == "structured_data_in_free_text"
    assert not mock.received and "Koramangala" not in r.text


def test_the_egress_sweep_finds_an_address_re_wrapped_on_the_wire():
    sensitive = [(T.ADDRESS, MULTI)]
    egress.check(b'{"m": "deliver to [ADDRESS_3QX2MA]"}', sensitive)                       # alias only: clean
    for leaked in (MULTI.replace("\n", " "), MULTI.replace("\n", "\n   "), MULTI.replace(" ", "\t"),
                   ONE_LINE.upper()):
        with pytest.raises(EgressViolation):
            egress.check(json.dumps({"m": "see " + leaked}).encode(), sensitive)
        with pytest.raises(EgressViolation):
            egress.check(("see " + leaked).encode(), sensitive)


# --- the ML detector's address label ---------------------------------------------------------------------------------

def test_a_model_address_label_is_aliased_and_restored(tmp_path_factory):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx")
    pytest.importorskip("tokenizers")
    from tests import ml_fixtures as F
    base = tmp_path_factory.mktemp("addr")
    d, sha = F.make_token_model(base / "pii")
    det = F.detector_profile(d, sha, labels={"PER": "PERSON", "LOC": "ADDRESS"})     # the model's location label
    client, mock = _gateway(base, deployment={"ml_detector": det, "ner_off_justification": None,
                                              "legacy_heuristics": False})
    r = _send(client, "Please courier the file to mumbai and tell priya nair it arrived.")
    assert r.status_code == 200, r.text
    wire = mock.received[-1].decode().lower()
    assert "mumbai" not in wire and "priya" not in wire and "[address_" in wire
    ans = r.json()["choices"][0]["message"]["content"]
    assert "mumbai" in ans and "[ADDRESS" not in ans
    assert {x["type"] for x in r.json()["pgw_report"]["detected"]} >= {"ADDRESS", "PERSON"}


def test_ml_labels_accept_address_and_refuse_unknown_types():
    from pgw.ml.config import MlConfigError, parse_detector
    base = {"path": "/x", "sha256": "a" * 64, "backend": "token", "min_score_by_type": {"DEFAULT": 0.5},
            "workers": 0, "queue_max": 4, "timeout_s": 5.0, "intra_op_threads": 1, "inter_op_threads": 1,
            "batch_size": 4}
    assert parse_detector({**base, "labels": {"private_address": "ADDRESS", "private_person": "PERSON"}}).labels
    with pytest.raises(MlConfigError):
        parse_detector({**base, "labels": {"private_address": "HOME_ADDRESS"}})
