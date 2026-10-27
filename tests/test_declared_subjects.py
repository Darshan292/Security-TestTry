"""PATH A declared subjects: identities the app declares are protected regardless of detection, verified at egress.

Synthetic data only. Assertions about what left read the exact bytes the upstream received.
"""
import hashlib
import json
import os
import re
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from pgw.gateway.app import create_app
from tests.sealed import attest, production
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product.contracts import ContractError

ROOT = Path(__file__).resolve().parents[1]
KEY = "pilot-key"
ALIAS = re.compile(r"\[[A-Z_]+_[0-9][0-9A-Z]{5}(?:[0-9A-Z]{2})?\]")

# Names the existing detectors (roster, cues, en_core_web_lg) do not find in this text: lowercase, single given name.
TICKET = ("Ticket PAY-90412 | Raised by: Imran Ansari\n"
          "Imran Ansari: my arrears for August are missing.\n"
          "Payroll Ops: please loop in tejaswini kulkarni from payroll before closing.\n"
          "Reply from Nandini: the arrears file was re-run on 30 Sep 2026.\n")
SUBJECTS = {"requester": {"name": "Imran Ansari", "employee_id": "KD-11914"},
            "assignee": {"name": "Tejaswini Kulkarni"},
            "mentioned": [{"name": "Nandini"}]}

def sha(k):
    return hashlib.sha256(k.encode()).hexdigest()


def make(tmp, behaviour="summarise", ner=None, tasks=None):
    tasks = tasks or {p: str(ROOT / f"contracts/{p}.yaml")
                      for p in ("ticket_summary", "hr_case_explanation", "salary_comparison")}
    cfg = production(tmp, [{
        "app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY), "restore_output": True, "models": ["mock-1"],
        "purposes": sorted(tasks), "tasks": tasks}], roster=ROOT / "tests/fixtures/slice_roster.jsonl",
        deployment={"ner_model": ner})
    mock = MockLLMUpstream(behaviour)
    s = Settings(**cfg,
                 ledger_db_path=str(tmp / "ledger.sqlite"), egress_ledger_path=str(tmp / "egress.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s)), mock


def call(client, content, subjects=None, purpose="ticket_summary", conv=None):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": content}]}
    if subjects is not None:
        body["pgw_subjects"] = subjects
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose, **attest(purpose, body)}
    if conv:
        h["X-PGW-Conversation-Id"] = conv
    return client.post("/v1/chat/completions", headers=h, json=body)


def wire(mock, i=-1) -> str:
    """Decoded outbound text (JSON strings), so escapes cannot hide a value."""
    acc = []

    def walk(x):
        if isinstance(x, str):
            acc.append(x)
        elif isinstance(x, (list, dict)):
            for v in (x.values() if isinstance(x, dict) else x):
                walk(v)
    walk(json.loads(mock.received[i]))
    return "\n".join(acc)


def drop_declarations(monkeypatch):
    """Simulate a pipeline bug: the protector ignores declared subjects (the egress sweep keeps its matchers)."""
    import pgw.pipeline as P
    orig = P.Protector.protect
    monkeypatch.setattr(P.Protector, "protect", lambda self, messages, ctx, declared=None: orig(self, messages, ctx))


spacy = pytest.importorskip("spacy")
needs_lg = pytest.mark.skipif(not spacy.util.is_package("en_core_web_lg"), reason="en_core_web_lg not installed")


@needs_lg
def test_declared_subjects_are_protected_where_ner_misses_them(tmp_path):
    client, mock = make(tmp_path, ner="en_core_web_lg")
    assert call(client, TICKET, {"requester": SUBJECTS["requester"]}).status_code == 200
    w = wire(mock).lower()
    assert "tejaswini" in w and "nandini" in w            # NER (secondary net) misses them: this is the gap
    assert call(client, TICKET, SUBJECTS).status_code == 200
    w = wire(mock).lower()
    for v in ("imran", "ansari", "tejaswini", "kulkarni", "nandini", "kd-11914"):
        assert v not in w
    assert len(set(ALIAS.findall(wire(mock)))) >= 4


def test_written_variants_of_declared_values_never_leave(tmp_path):
    client, mock = make(tmp_path)
    text = ("TEJASWINI KULKARNI raised it. Tejaswini  Kulkarni (handle tejaswini.kulkarni) and Kulkarni ji agreed. "
            "IDs seen: kd12007, KD 12007, Kd-12007. Phone 9845012345, +91-98450 12345, 098450-12345. "
            "Mail TEJASWINI.K@KESTREL-DYN.IN.")
    subj = {"requester": {"name": "Tejaswini Kulkarni", "employee_id": "KD-12007", "phone": "+91 98450 12345",
                          "email": "tejaswini.k@kestrel-dyn.in"}}
    assert call(client, text, subj).status_code == 200
    w = wire(mock).lower()
    for v in ("tejaswini", "kulkarni", "12007", "98450", "12345", "kestrel-dyn"):
        assert v not in w
    ids = {a for a in ALIAS.findall(wire(mock)) if a.startswith("[EMPLOYEE_ID_")}
    assert len(ids) == 1                                  # all ID spellings share one alias
    out = call(client, text, subj).json()["choices"][0]["message"]["content"]
    assert "Tejaswini Kulkarni" in out and "TEJASWINI KULKARNI" not in out   # restored as the app declared it


def test_declared_check_is_independent_of_the_pipeline(tmp_path, monkeypatch):
    """Simulate a pipeline bug: declarations are not applied. The declared-subject egress check still stops it."""
    client, mock = make(tmp_path)
    drop_declarations(monkeypatch)
    r = call(client, TICKET, SUBJECTS)
    assert r.status_code == 500 and r.json()["error"]["type"] == "PGW_EGRESS_VIOLATION" and not mock.received
    assert "Nandini" not in r.text and "tejaswini" not in r.text.lower()


def test_short_declared_names_are_covered_by_the_declared_check(tmp_path, monkeypatch):
    """The generic egress invariant ignores needles under 4 characters; declared subjects do not."""
    client, mock = make(tmp_path)
    assert call(client, "Escalated by Om to Rao.", {"requester": {"name": "Om Rao"}}).status_code == 200
    w = wire(mock)
    assert not re.search(r"\bOm\b", w) and not re.search(r"\bRao\b", w)
    drop_declarations(monkeypatch)
    assert call(client, "Escalated by Rao.", {"requester": {"name": "Om Rao"}}).status_code == 500


def test_declared_identities_are_restored_for_the_app(tmp_path):
    client, _ = make(tmp_path)
    r = call(client, TICKET, SUBJECTS)
    out = r.json()["choices"][0]["message"]["content"]
    assert not ALIAS.findall(out)
    assert "Nandini" in out and re.search("tejaswini kulkarni", out, re.I)


def test_only_authorised_classes_are_restored(tmp_path):
    c = yaml.safe_load((ROOT / "contracts/ticket_summary.yaml").read_text())
    c["restore"] = []
    p = tmp_path / "norestore.yaml"
    p.write_text(yaml.safe_dump(c))
    client, _ = make(tmp_path, tasks={"ticket_summary": str(p)})
    out = call(client, TICKET, SUBJECTS).json()["choices"][0]["message"]["content"]
    assert "Nandini" not in out and ALIAS.findall(out)


def test_aliases_are_scoped_to_the_contract(tmp_path):
    client, mock = make(tmp_path)
    subj = {"requester": {"name": "Nandini"}}
    call(client, "Reply from Nandini.", subj, conv="c1")
    a1 = ALIAS.findall(wire(mock))
    call(client, "Reply from Nandini.", subj, conv="c1")
    a2 = ALIAS.findall(wire(mock))
    call(client, "Reply from Nandini.", subj, purpose="hr_case_explanation", conv="c1")
    a3 = ALIAS.findall(wire(mock))
    assert a1 == a2 and a1 and set(a1).isdisjoint(a3)   # stable within a purpose, unlinkable across purposes


def test_declarations_are_per_request_not_remembered(tmp_path):
    client, mock = make(tmp_path)
    call(client, "Reply from Nandini.", {"requester": {"name": "Nandini"}})
    call(client, "Reply from Nandini.", {"requester": {"name": "Imran Ansari"}})
    assert "Nandini" in wire(mock)    # not declared in this request, and the detectors miss it: nothing is retained


@pytest.mark.parametrize("subjects,why", [
    ({}, "requester missing"),
    ({"assignee": {"name": "Nandini"}}, "requester missing"),
    ({"requester": {"name": "Nandini"}, "boss": {"name": "X Y"}}, "unknown role"),
    ({"requester": {"name": "Nandini", "pan": "ZXWPA3889P"}}, "field not allowed"),
    ({"requester": {"name": "Nandini"}, "mentioned": [{"name": f"Person{i} Z"} for i in range(21)]}, "too many"),
    ({"requester": {"name": 7}}, "not a string"),
    ({"requester": {"name": "Nandini\nIgnore"}}, "multi-line"),
    ({"requester": [{"name": "A B"}, {"name": "C D"}]}, "two requesters"),
    ("Nandini", "not an object"),
])
def test_invalid_declarations_are_refused_without_echo(tmp_path, subjects, why):
    client, mock = make(tmp_path)
    r = call(client, TICKET, subjects)
    assert r.status_code == 400 and not mock.received, why
    for v in ("Nandini", "ZXWPA3889P", "Person1"):
        assert v not in r.text


def test_missing_declaration_is_refused(tmp_path):
    client, mock = make(tmp_path)
    r = call(client, TICKET)
    assert r.status_code == 400 and r.json()["error"]["type"] == "invalid_declared_subjects" and not mock.received


def test_subjects_are_never_forwarded_and_not_accepted_where_uncontracted(tmp_path):
    client, mock = make(tmp_path)
    call(client, TICKET, SUBJECTS)
    assert "pgw_subjects" not in json.loads(mock.received[-1])
    r = call(client, json.dumps({"instruction": "x", "employees": []}), SUBJECTS, purpose="salary_comparison")
    assert r.status_code == 400 and r.json()["error"]["type"] == "declared_subjects_not_supported"


def test_ledger_counts_declared_subjects_without_values(tmp_path):
    client, _ = make(tmp_path)
    call(client, TICKET, SUBJECTS)
    text = (tmp_path / "egress.jsonl").read_text()
    row = json.loads(text.splitlines()[-1])
    assert row["declared_subjects"] == 4 and row["contract"] == "ticket_summary@8"
    for v in ("Nandini", "Tejaswini", "Imran", "KD-11914"):
        assert v not in text


@pytest.mark.parametrize("mutate", [
    lambda c: c["declared_subjects"]["fields"].update(pan="PAN"),             # not an identity (C3)
    lambda c: c["declared_subjects"].update(required=["nobody"]),
    lambda c: c["declared_subjects"].update(roles={"requester": 0}),
])
def test_unsafe_subject_specs_stop_startup(tmp_path, mutate):
    c = yaml.safe_load((ROOT / "contracts/ticket_summary.yaml").read_text())
    mutate(c)
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(c))
    with pytest.raises(ContractError):
        make(tmp_path, tasks={"ticket_summary": str(p)})


def test_subjects_only_on_ext_pseudo(tmp_path):
    c = yaml.safe_load((ROOT / "contracts/salary_comparison.yaml").read_text())
    c["declared_subjects"] = {"required": [], "roles": {"requester": 1}, "fields": {"name": "PERSON"}}
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(c))
    with pytest.raises(ContractError):
        make(tmp_path, tasks={"salary_comparison": str(p)})


# --- regressions from the adversarial review (each reproduced a leak, a DoS or a false positive) ---------------

TK = {"requester": {"name": "Tejaswini Kulkarni", "employee_id": "KD-11914", "phone": "+91 98450 12345"}}


@pytest.mark.parametrize("text", [
    "user tejaswini_kulkarni opened it", "ping @tejaswini today", "TejaswiniKulkarni wrote", "see ?n=tejaswini_kulkarni",
    "ｔｅｊａｓｗｉｎｉ ｋｕｌｋａｒｎｉ wrote", "teja\u200bswini wrote", "teja\u00adswini wrote", "teja\u0301swini wrote",
    "employee 11914 asked", "KD\u201311914 asked", "KD - 11914 asked", "KD:11914 asked", "ext 98450 called",
    "98450-x-12345 called",
])
def test_variant_forms_never_leave(tmp_path, text):
    client, mock = make(tmp_path)
    text = text.encode().decode("unicode_escape") if "\\u" in text else text
    r = call(client, text, TK)
    assert r.status_code == 200, r.text
    w = wire(mock).lower()
    for v in ("tejaswini", "kulkarni", "11914", "98450", "12345", "ｔｅｊａ"):
        assert v not in w, v


@pytest.mark.parametrize("name,text,bad", [
    ("Mary-Jane Pinto", "Mary Jane and Mary and Jane called", ["Mary", "Jane"]),
    ("Anita D'Souza", "D’Souza, DSouza, D Souza and Souza agreed", ["Souza"]),
    ("José Fernández", "Jose Fernandez and Jose\u0301 replied", ["Jose", "Fernandez"]),
])
def test_hyphen_apostrophe_and_accent_names(tmp_path, name, text, bad):
    client, mock = make(tmp_path)
    text = text.encode().decode("unicode_escape") if "\\u" in text else text
    assert call(client, text, {"requester": {"name": name}}).status_code == 200
    w = wire(mock)
    for v in bad:
        assert v not in w


def test_names_inside_tool_arguments_and_list_parts(tmp_path):
    client, mock = make(tmp_path)
    body = {"model": "mock-1", "pgw_subjects": TK, "messages": [
        {"role": "user", "content": [{"type": "text", "text": "Tejaswini Kulkarni asked."}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "lookup", "arguments": json.dumps({"who": "Tejaswini Kulkarni", "u": "Jos\u00e9"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "found tejaswini"}]}
    r = client.post("/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {KEY}",
                    "X-PGW-Purpose": "ticket_summary", "X-PGW-Conversation-Id": "conv-tool",
                                                         **attest("ticket_summary", body)})
    assert r.status_code == 200 and "tejaswini" not in mock.received[-1].decode().lower()


def test_c4_and_size_limits_cover_list_form_content(tmp_path):
    client, mock = make(tmp_path)
    body = {"model": "mock-1", "pgw_subjects": TK,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "राहुल शर्मा का वेतन"}]}]}
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary"}
    assert client.post("/v1/chat/completions", json=body,
                       headers={**h, **attest("ticket_summary", body)}).status_code == 422
    body["messages"][0]["content"] = [{"type": "text", "text": "x" * 15000}] * 2
    assert client.post("/v1/chat/completions", json=body,
                       headers={**h, **attest("ticket_summary", body)}).status_code == 413
    assert not mock.received


def test_variant_flood_is_bounded(tmp_path):
    import random
    import time
    rng = random.Random(7)
    words = ["".join(c.upper() if rng.random() < .5 else c for c in "tejaswini kulkarni").replace(" ", " " * rng.randint(1, 3))
             for _ in range(1200)]
    client, mock = make(tmp_path)
    t = time.perf_counter()
    r = call(client, " ".join(words)[:19900], TK)
    assert time.perf_counter() - t < 5 and r.status_code in (200, 413)
    if r.status_code == 200:
        assert "tejaswini" not in wire(mock).lower() and "kulkarni" not in wire(mock).lower()
    big = call(client, " ".join(["Tejaswini Kulkarni,"] * 1100), TK)     # far past the span cap
    assert big.status_code == 413


@pytest.mark.parametrize("name", ["Minh Do", "Hye In", "Asha Such", "Claude Martin"])
def test_gateway_text_does_not_trigger_false_violations(tmp_path, name):
    client, mock = make(tmp_path)
    r = call(client, f"Ticket raised by {name}. Please do check what is in such cases.", {"requester": {"name": name}})
    assert r.status_code == 200, r.text
    assert "do check" in wire(mock) and "in such" in wire(mock)    # common words are not masked as name parts
    assert name.split()[-1] not in wire(mock).split("Ticket raised by")[1][:30]


def test_common_word_parts_are_not_masked_in_prose(tmp_path):
    client, mock = make(tmp_path)
    assert call(client, "Will Pinto said I will check. WILL PINTO agreed.", {"requester": {"name": "Will Pinto"}}).status_code == 200
    w = wire(mock)
    assert "I will check" in w and "Will" not in w and "WILL" not in w and "Pinto" not in w


def test_declared_values_in_tools_or_stop_are_a_400(tmp_path):
    client, mock = make(tmp_path)
    body = {"model": "mock-1", "pgw_subjects": TK, "stop": ["Tejaswini"],
            "messages": [{"role": "user", "content": "hello"}]}
    r = client.post("/v1/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary",
                             **attest("ticket_summary", body)})
    assert r.status_code == 400 and not mock.received and "Tejaswini" not in r.text


def test_identifier_with_underscore_prefix_is_masked_not_500(tmp_path):
    client, mock = make(tmp_path)
    r = call(client, "ref emp_KD11914 and KD11914_old", TK)
    assert r.status_code == 200 and "11914" not in wire(mock)


@pytest.mark.parametrize("text", ["@tkulkarni", "tejaswini99", "tejaswinik", "kulkarnit", "tejaswini2026",
                                  "kulkarni.tejaswini"])
def test_handles_glued_to_letters_or_digits(tmp_path, text):
    client, mock = make(tmp_path)
    assert call(client, f"login {text} reset", TK).status_code == 200
    assert "kulkarni" not in wire(mock).lower() and "tejaswini" not in wire(mock).lower()


def test_letters_nfkd_does_not_decompose(tmp_path):
    client, mock = make(tmp_path)
    assert call(client, "Søren Ørsted and Lukasz wrote", {"requester": {"name": "Soren Orsted"},
                                                          "mentioned": [{"name": "Łukasz Nowak"}]}).status_code == 200
    w = wire(mock)
    assert "Søren" not in w and "Ørsted" not in w and "Lukasz" not in w


def test_email_containing_a_declared_name_stays_an_email(tmp_path):
    client, mock = make(tmp_path)
    assert call(client, "cc tejaswini.kulkarni@acme.example", TK).status_code == 200
    assert "[EMAIL_" in wire(mock) and "acme.example" not in wire(mock)


def test_quoted_thread_is_not_refused(tmp_path):
    client, mock = make(tmp_path)
    sig = "Tejaswini Kulkarni | KD-11914 | +91 98450 12345\n> Tejaswini wrote: ok\n"
    assert call(client, sig * 60, TK).status_code == 200


def test_conversation_ledger_failure_before_send_fails_closed(tmp_path, monkeypatch):
    import pgw.detect.edm as E
    monkeypatch.setattr(E.RestoreLedger, "record", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    client, mock = make(tmp_path)
    r = call(client, TICKET, SUBJECTS, conv="c-led")
    assert r.status_code == 503 and r.json()["error"]["type"] == "ledger_unavailable" and not mock.received


def test_later_turn_reprotects_names_not_declared_again(tmp_path):
    client, mock = make(tmp_path)
    assert call(client, TICKET, SUBJECTS, conv="c-mt").status_code == 200
    hist = [{"role": "user", "content": TICKET}, {"role": "assistant", "content": "Nandini re-ran it."},
            {"role": "user", "content": "Thanks, please loop in tejaswini kulkarni and NANDINI again."}]
    body = {"model": "mock-1", "messages": hist, "pgw_subjects": {"requester": SUBJECTS["requester"]}}
    r = client.post("/v1/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary",
                             **attest("ticket_summary", body), "X-PGW-Conversation-Id": "c-mt"})
    assert r.status_code == 200
    w = wire(mock).lower()
    assert "nandini" not in w and "tejaswini" not in w and "kulkarni" not in w


def test_raw_c0_outputs_are_not_regeneralised(tmp_path):
    client, mock = make(tmp_path, tasks={"band_position": str(ROOT / "contracts/band_position.yaml")})
    body = {"instruction": "Explain.", "employee": [{"name": "Hetal Shah", "employee_id": "KD-11063",
            "annual_ctc": 2180000}], "band": [{"name": "P3"}]}
    r = call(client, json.dumps(body), purpose="band_position")
    assert r.status_code == 200, r.text
    w = wire(mock)
    assert "₹18,00,000" in w and "₹26,00,000" in w and "middle third" in w
    for v in ("21,80,000", "2180000", "4,20,000", "48%", "Hetal"):
        assert v not in w
