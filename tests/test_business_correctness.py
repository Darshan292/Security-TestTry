"""Contract-level business-correctness mechanisms: task guidance, organisational contacts, marker rendering.

Synthetic data only.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from pgw.gateway.app import PLACEHOLDER_NOTE, create_app, render_markers
from tests.sealed import production, source
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product.contracts import ContractError, load_contract

ROOT = Path(__file__).resolve().parents[1]
KEY = "pilot-key"
EMAIL = ("Draft an email to Amit Trivedi (KD-10989, amit.trivedi@kestrel-dyn.in) confirming his salary account "
         "50100234567891 was updated. Copy hr-ops@kestrel-dyn.in.")
SUBJ = {"recipient": {"name": "Amit Trivedi", "employee_id": "KD-10989", "email": "amit.trivedi@kestrel-dyn.in"}}


def make(tmp, behaviour="echo", purpose="hr_email_draft"):
    cfg = production(tmp, [{
        "app_id": "pilot", "tenant": "acme", "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(),
        "restore_output": True, "models": ["mock-1"], "purposes": [purpose],
        "tasks": {purpose: str(ROOT / f"contracts/{purpose}.yaml")}}])
    mock = MockLLMUpstream(behaviour)
    s = Settings(**cfg, ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s)), mock


def call(client, text, purpose="hr_email_draft", subjects=SUBJ):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": text}]}
    if subjects is not None:
        body["pgw_subjects"] = subjects
    return client.post("/v1/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose, **source(purpose, body)})


def test_guidance_and_sender_contacts_reach_the_model_recipient_contacts_do_not(tmp_path):
    client, mock = make(tmp_path)
    r = call(client, EMAIL)
    assert r.status_code == 200, r.text
    sent = json.loads(mock.received[-1])
    system = sent["messages"][0]["content"]
    assert system.startswith(PLACEHOLDER_NOTE) and "hr-ops@kestrel-dyn.in" in system
    user = sent["messages"][1]["content"]
    assert "hr-ops@kestrel-dyn.in" in user                    # organisational C0 contact: not aliased
    assert "amit.trivedi@kestrel-dyn.in" not in user and "50100234567891" not in user


def test_marker_text_never_reaches_the_restored_answer(tmp_path):
    client, mock = make(tmp_path)                             # echo: the model "copies" the [BANK_ACCOUNT] marker
    out = call(client, EMAIL).json()["choices"][0]["message"]["content"]
    assert "[BANK_ACCOUNT]" not in out and "(withheld)" in out
    assert "50100234567891" not in out                         # rendering never restores a value


def test_default_marker_rendering():
    c = load_contract(ROOT / "contracts/ticket_summary.yaml")
    assert render_markers("refund to [BANK_ACCOUNT]; PAN [PAN]", c) == "refund to (withheld); PAN (withheld)"
    assert render_markers("[PERSON_1ABCDE] stays", c) == "[PERSON_1ABCDE] stays"


def test_case_explanation_and_esi_contracts_carry_their_fixes():
    assert "hypothesis" in load_contract(ROOT / "contracts/hr_case_explanation.yaml").guidance
    assert "by name" in load_contract(ROOT / "contracts/esi_letter.yaml").prompt_template


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(redaction_text={"PERSON": "someone"}),          # only C3 markers may be rendered
    lambda c: c.update(redaction_text={"PAN": "[PAN]"}),
    lambda c: c.update(guidance="x" * 2001),
    lambda c: c.update(org_contacts=["a\\nb"]),
])
def test_unsafe_business_fields_are_refused(tmp_path, mutate):
    c = yaml.safe_load((ROOT / "contracts/hr_email_draft.yaml").read_text())
    mutate(c)
    if "org_contacts" in c and c["org_contacts"] == ["a\\nb"]:
        c["org_contacts"] = ["a\nb"]
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(c))
    with pytest.raises(ContractError):
        load_contract(p)


def test_org_contact_in_model_answer_survives_but_other_raw_emails_are_gated(tmp_path):
    class Writer(MockLLMUpstream):     # a model that writes contacts it was never given
        async def complete(self, body):
            out = await super().complete(body)
            out["choices"][0]["message"]["content"] = "Write to hr-ops@kestrel-dyn.in or rogue.person@other.example."
            return out
    client, mock = make(tmp_path)
    mock.__class__ = Writer
    text = call(client, EMAIL).json()["choices"][0]["message"]["content"]
    assert "hr-ops@kestrel-dyn.in" in text and "rogue.person@other.example" not in text
