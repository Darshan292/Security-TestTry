"""Transparency report (pgw_report v1, docs/TRANSPARENCY_REPORT.md, SECURITY_BOUNDARY.md "Transparency report").

Truth: everything in the report is checked against what really happened: the outbound messages and sha256 against the
exact bytes MockLLMUpstream received, the spans against the caller's own input (input[start:end] is the value, and
applying the placeholders at the reported offsets rebuilds the exact text that went upstream).
Safety: the report goes only to an opted-in app, never to logs, the ledger or the provider, carries no value from
another request or conversation, is bounded, carries no text in error responses, and changes nothing for an app
without transparency (byte-for-byte). Synthetic data only.
"""
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from starlette.testclient import TestClient

from pgw import private_llm
from pgw.gateway.app import create_app, load_registry
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product import integrity
from pgw.gateway.report import ACTION
from pgw.gateway.report import CHECKS as LABELS_BY_ID
from pgw.product.contracts import DataClass
from pgw.types import EntityType
from tests.sealed import attest, production, sha
from tests.test_private_route import HINDI, LocalModel
from tests.test_product_slice import PAYLOAD, SALARY_FORMS, SUMMARY_ASK, TICKET_RAW

ROOT = Path(__file__).resolve().parents[1]
ON, OFF = "transparent-app-key", "plain-app-key"
PURPOSES = ("ticket_summary", "salary_comparison", "hr_email_draft", "ticket_summary_any_script")
REQUESTER = {"requester": {"name": "Imran Ansari"}}
TOP_KEYS = ["version", "request_id", "purpose", "contract", "route", "outcome", "input_source", "stages", "detected",
            "truncated", "counts", "outbound", "restore", "checks", "error"]
STAGE_IDS = ["received", "verify_source", "compute", "protect", "egress_check", "provider", "restore"]
CHECK_IDS = ["egress_clean", "declared_absent", "source_verified", "c3_not_returned"]
LABELS = list(LABELS_BY_ID.values())


def app_entry(key, app_id, transparency):
    tasks = {p: str(ROOT / f"contracts/{p}.yaml") for p in PURPOSES}
    a = {"app_id": app_id, "tenant": "acme", "key_sha256": sha(key), "purposes": sorted(tasks),
         "restore_output": True, "models": ["mock-1"], "tasks": tasks}
    if transparency is not None:
        a["transparency"] = transparency
    return a


def gateway(tmp, behaviour="summarise", upstream=None, private=None, mode=None, master=None):
    """One gateway, two apps: app "pilot" (key ON, transparency true) and app "plain" (key OFF, flag absent)."""
    tmp.mkdir(parents=True, exist_ok=True)
    mock = upstream or MockLLMUpstream(behaviour)
    kw = {} if private is None else {"private_upstream": private}
    cfg = production(tmp, [app_entry(ON, "pilot", True), app_entry(OFF, "plain", None)],
                     roster=ROOT / "tests/fixtures/slice_roster.jsonl", **kw)
    if mode:
        cfg["mode"] = mode
    s = Settings(**cfg, ledger_db_path=str(tmp / "ledger.sqlite"), egress_ledger_path=str(tmp / "egress.jsonl"),
                 master_key=master or os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s)), mock


def post(client, purpose, messages, key=ON, subjects=None, signed=True, conv=None, app_id=None, **extra):
    body = {"model": "mock-1", "messages": messages, **extra}
    if purpose in ("ticket_summary", "ticket_summary_any_script"):
        body["pgw_subjects"] = subjects or REQUESTER
    elif purpose == "hr_email_draft":
        body["pgw_subjects"] = subjects or {"recipient": {"name": "Imran Ansari"}}
    h = {"Authorization": f"Bearer {key}", "X-PGW-Purpose": purpose}
    if signed:
        h.update(attest(purpose, body, app_id=app_id or ("pilot" if key == ON else "plain")))
    if conv:
        h["X-PGW-Conversation-Id"] = conv
    return client.post("/v1/chat/completions", json=body, headers=h)


def user(text):
    return [{"role": "user", "content": text}]


def sse_events(text):
    return [line[6:] for line in text.split("\n") if line.startswith("data: ")]


def stream_report(r):
    reps = [json.loads(e)["pgw_report"] for e in sse_events(r.text) if e.startswith('{"pgw_report"')]
    assert len(reps) == 1
    return reps[0]


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


def rebuild(original: str, entries) -> str:
    """Apply the reported placeholders at the reported offsets to the caller's original string."""
    out = original
    for d in sorted(entries, key=lambda d: d["start"], reverse=True):
        if d["placeholder"] is not None:
            out = out[:d["start"]] + d["placeholder"] + out[d["end"]:]
    return out


def received(mock, i=-1):
    return json.loads(mock.received[i])


# ------------------------------------------------------------------------------------------------ registry opt-in

def test_transparency_is_a_registry_flag_defaulting_to_false(tmp_path):
    reg = tmp_path / "apps.yaml"
    base = {"app_id": "a", "tenant": "t", "key_sha256": "00", "purposes": [], "models": []}
    reg.write_text(yaml.safe_dump({"apps": [base, {**base, "app_id": "b", "transparency": True}]}))
    a, b = load_registry(str(reg))
    assert a.transparency is False and b.transparency is True
    for bad in ("yes", 1, "true", None):     # a non-boolean never turns the report on (or silently off)
        reg.write_text(yaml.safe_dump({"apps": [{**base, "transparency": bad}]}))
        with pytest.raises(ValueError, match="transparency"):
            load_registry(str(reg))


def test_shipped_pilot_app_has_it_on_and_the_registry_is_resealed():
    raw = yaml.safe_load((ROOT / "registry/apps.yaml").read_text())
    assert raw["version"] >= 3
    pilot = {a.app_id: a for a in load_registry(str(ROOT / "registry/apps.yaml"))}["hr-helpdesk-pilot"]
    assert pilot.transparency is True
    integrity.verify([ROOT / "registry/apps.yaml"], ROOT / "policies/MANIFEST.json")   # sealed with the change


def test_editing_the_flag_without_resealing_stops_startup(tmp_path):
    cfg = production(tmp_path, [app_entry(ON, "pilot", False)])
    reg = Path(cfg["registry_path"])
    reg.write_text(reg.read_text().replace("transparency: false", "transparency: true"))
    with pytest.raises(Exception, match="differs from its sealed hash"):
        create_app(Settings(**cfg, ledger_db_path=str(tmp_path / "l.sqlite"),
                            egress_ledger_path=str(tmp_path / "e.jsonl"), master_key=os.urandom(32).hex(),
                            upstream=MockLLMUpstream()))


# ------------------------------------------------------------------------------------------- EXT_PSEUDO: truth

def test_non_stream_report_shape_and_outbound_is_exactly_what_went_upstream(tmp_path):
    client, mock = gateway(tmp_path)
    r = post(client, "ticket_summary", user(SUMMARY_ASK))
    assert r.status_code == 200, r.text
    body = r.json()
    rep = body["pgw_report"]
    assert list(body)[-1] == "pgw_report" and list(rep) == TOP_KEYS
    assert rep["version"] == 1 and rep["request_id"] == r.headers["X-PGW-Request-Id"]
    assert (rep["purpose"], rep["contract"], rep["route"], rep["outcome"]) == \
        ("ticket_summary", "ticket_summary@8", "EXT_PSEUDO", "sent")
    assert rep["input_source"] == {"id": "helpdesk-ticket-export@1", "verified": True}
    assert [s["id"] for s in rep["stages"]] == STAGE_IDS
    assert {s["id"]: s["status"] for s in rep["stages"]}["compute"] == "skipped"
    assert all(s["status"] == "done" for s in rep["stages"] if s["id"] != "compute")
    assert all(isinstance(s["ms"], float) and s["ms"] >= 0 for s in rep["stages"])
    out = rep["outbound"]
    assert out["destination"] == "external" and out["provider"] == "mock" and out["model"] == "mock-1"
    assert out["messages"] == received(mock)["messages"]                       # exactly what went upstream
    assert out["sha256"] == hashlib.sha256(mock.received[-1]).hexdigest()      # over the exact body bytes
    assert out["bytes"] == len(mock.received[-1])
    assert out["gateway_note_included"] is True and out["messages"][0]["role"] == "system"
    assert [c["id"] for c in rep["checks"]] == CHECK_IDS
    assert all(c["status"] == "pass" and c["label"] for c in rep["checks"])
    assert rep["restore"]["restored"] >= 2 and rep["restore"]["kept_withheld"] >= 1   # [PAN]/[BANK_ACCOUNT]
    assert rep["error"] is None and rep["truncated"] is False
    # the egress ledger records the same body hash as the report (both are the real upstream body)
    row = json.loads((tmp_path / "egress.jsonl").read_text().splitlines()[-1])
    assert row["body_sha256"] == out["sha256"]


def test_spans_are_offsets_into_the_original_input_with_the_placeholders_that_left(tmp_path):
    client, mock = gateway(tmp_path)
    rep = post(client, "ticket_summary", user(SUMMARY_ASK)).json()["pgw_report"]
    got = {}
    for d in rep["detected"]:
        assert d["message"] == 0 and d["part"] is None and "field" not in d
        got.setdefault(d["type"], set()).add(SUMMARY_ASK[d["start"]:d["end"]])
    assert got["PERSON"] == {"Imran Ansari"}
    assert got["EMPLOYEE_ID"] == {"KD-11914"}
    assert got["COMPENSATION"] == {"Rs 4,500"}
    assert got["PAN"] == {"ZXWPA3889P"} and got["BANK_ACCOUNT"] == {"644748074748"}
    assert got["EMAIL"] == {"imran.ansari@kestrel-dyn.in"}
    actions = {d["type"]: (d["action"], d["class"]) for d in rep["detected"]}
    assert actions["PAN"] == ("remove", "C3") and actions["PERSON"] == ("alias", "C1")
    assert actions["COMPENSATION"] == ("alias", "C2")
    sent = received(mock)["messages"][1]["content"]
    assert rebuild(SUMMARY_ASK, rep["detected"]) == sent      # the report fully explains the outbound text
    for d in rep["detected"]:
        if d["placeholder"]:
            assert d["placeholder"] in sent
    by_action = [d["action"] for d in rep["detected"]]
    assert rep["counts"]["replaced"] == sum(a in ("alias", "generalize") for a in by_action)
    assert rep["counts"]["removed"] == sum(a in ("remove", "withhold") for a in by_action)
    assert sum(rep["counts"]["by_type"].values()) == len(rep["detected"])


def test_multi_message_and_list_form_content(tmp_path):
    client, mock = gateway(tmp_path)
    parts = ["Ticket from Imran Ansari (KD-11914).",
             "My October TDS projection shows an extra deduction of Rs 4,500. PAN ZXWPA3889P; reach him at imran.ansari@kestrel-dyn.in."]
    messages = [{"role": "system", "content": "You summarise HR tickets for Kestrel Dynamics."},
                {"role": "user", "content": [{"type": "text", "text": t} for t in parts]},
                {"role": "user", "content": "Also: salary account 644748074748, query raised by IMRAN ANSARI."}]
    r = post(client, "ticket_summary", messages)
    assert r.status_code == 200, r.text
    rep = r.json()["pgw_report"]
    sent = received(mock)["messages"][1:]                       # [0] is the gateway's placeholder note

    def original(d):
        c = messages[d["message"]]["content"]
        return c[d["part"]]["text"] if d["part"] is not None else c
    seen = set()
    for d in rep["detected"]:
        surface = original(d)[d["start"]:d["end"]]
        seen.add((d["message"], d["part"], d["type"], surface))
    assert (1, 0, "PERSON", "Imran Ansari") in seen and (1, 0, "EMPLOYEE_ID", "KD-11914") in seen
    assert (1, 1, "COMPENSATION", "Rs 4,500") in seen and (1, 1, "PAN", "ZXWPA3889P") in seen
    assert (1, 1, "EMAIL", "imran.ansari@kestrel-dyn.in") in seen
    assert (2, None, "BANK_ACCOUNT", "644748074748") in seen and (2, None, "PERSON", "IMRAN ANSARI") in seen
    for mi, m in enumerate(messages):                           # every string rebuilds exactly
        if isinstance(m["content"], str):
            ents = [d for d in rep["detected"] if d["message"] == mi]
            assert rebuild(m["content"], ents) == sent[mi]["content"]
        else:
            for pi, p in enumerate(m["content"]):
                ents = [d for d in rep["detected"] if d["message"] == mi and d["part"] == pi]
                assert rebuild(p["text"], ents) == sent[mi]["content"][pi]["text"]


def test_declared_subjects_are_reported_as_declared_at_their_original_offsets(tmp_path):
    client, mock = gateway(tmp_path)
    text = "Ticket: imran ansari cannot see his payslip. Requester Imran A. Ansari, emp KD-11914."
    subj = {"requester": {"name": "Imran Ansari", "employee_id": "KD-11914"}}
    rep = post(client, "ticket_summary", user(text), subjects=subj).json()["pgw_report"]
    declared = [(d["type"], text[d["start"]:d["end"]]) for d in rep["detected"] if d["source"] == "declared"]
    assert ("EMPLOYEE_ID", "KD-11914") in declared
    names = [s for t, s in declared if t == "PERSON"]
    assert names and all("imran" in s.lower() or "ansari" in s.lower() for s in names)
    assert rebuild(text, rep["detected"]) == received(mock)["messages"][1]["content"]
    assert {c["id"]: c["status"] for c in rep["checks"]}["declared_absent"] == "pass"


def test_offsets_stay_exact_around_held_out_organisational_contacts(tmp_path):
    # hr_email_draft holds its org contacts out of detection (one sentinel character each): offsets after them
    # would shift by the contact's length if the report used the internal text.
    client, mock = gateway(tmp_path)
    text = ("Write to Imran Ansari (KD-11914): contact HR-OPS@kestrel-dyn.in or +91 80 4000 1234. "
            "My October TDS projection shows an extra deduction of Rs 4,500. His email is imran.ansari@kestrel-dyn.in.")
    r = post(client, "hr_email_draft", user(text))
    assert r.status_code == 200, r.text
    rep = r.json()["pgw_report"]
    got = {(d["type"], text[d["start"]:d["end"]]) for d in rep["detected"]}
    assert ("EMAIL", "imran.ansari@kestrel-dyn.in") in got and ("COMPENSATION", "Rs 4,500") in got
    assert ("PERSON", "Imran Ansari") in got
    assert not any("kestrel-dyn.in" in s and "imran" not in s for _t, s in got)   # org contact not detected
    # a held-out organisational contact (C0, not listed) leaves in the contract's own spelling
    assert rebuild(text, rep["detected"]).replace("HR-OPS@", "hr-ops@") == received(mock)["messages"][1]["content"]


def test_held_out_tracking_produces_the_same_text_and_exact_maps():
    from pgw.gateway.report import held_out
    s0 = 0xE100
    text = "a Mail HR-ops@x.in, call 1800 22 33 or hr-ops@x.in. Ref 1800 22 334."
    held = ("hr-ops@x.in", "1800 22 33")
    plain, none = held_out(text, held, 1, s0, False)
    tracked, (starts, ends) = held_out(text, held, 1, s0, True)
    assert none is None and plain == tracked
    assert len(starts) == len(ends) == len(tracked)
    for i, c in enumerate(tracked):
        if s0 <= ord(c) < s0 + 0x100:
            assert text[starts[i]:ends[i]].lower() == held[ord(c) - s0].lower()
        else:
            assert text[starts[i]:ends[i]] == c


# ------------------------------------------------------------------------------------------------------ stream

def test_stream_report_is_one_event_after_the_content_and_before_done(tmp_path):
    client, mock = gateway(tmp_path)
    r = post(client, "ticket_summary", user(SUMMARY_ASK), stream=True)
    assert r.status_code == 200
    events = sse_events(r.text)
    assert events[-1] == "[DONE]" and events[-2].startswith('{"pgw_report"')
    assert sum(e.startswith('{"pgw_report"') for e in events) == 1
    assert all('"chat.completion.chunk"' in e for e in events[:-2])
    rep = stream_report(r)
    assert list(rep) == TOP_KEYS and rep["outcome"] == "sent"
    assert [s["id"] for s in rep["stages"]] == STAGE_IDS
    assert rep["outbound"]["messages"] == received(mock)["messages"]
    assert rep["outbound"]["sha256"] == hashlib.sha256(mock.received[-1]).hexdigest()
    assert rebuild(SUMMARY_ASK, rep["detected"]) == received(mock)["messages"][1]["content"]
    assert rep["restore"]["restored"] >= 2 and all(c["status"] == "pass" for c in rep["checks"])


def test_stream_interrupted_mid_way_reports_an_error_without_text(tmp_path):
    class Breaks(MockLLMUpstream):
        async def stream(self, body):
            n = 0
            async for x in super().stream(body):
                n += 1
                if n == 3:
                    raise RuntimeError("connection dropped")
                yield x
    client, mock = gateway(tmp_path, upstream=Breaks("summarise"))
    r = post(client, "ticket_summary", user(SUMMARY_ASK), stream=True)
    events = sse_events(r.text)
    assert json.loads(events[-1])["error"]["type"] == "stream_interrupted"
    rep = json.loads(events[-2])["pgw_report"]
    assert rep["outcome"] == "error" and rep["error"]["type"] == "stream_interrupted"
    assert rep["stages"][-1] == {"id": "provider", "status": "failed", "ms": rep["stages"][-1]["ms"]}
    assert rep["outbound"]["messages"] is None                  # sent, but an error never carries text
    assert rep["outbound"]["sha256"] == hashlib.sha256(mock.received[-1]).hexdigest()
    for v in TICKET_RAW + ["Rs 4,500", "PAY-88231"]:            # placeholders and offsets only, never text
        assert v not in events[-2]


# --------------------------------------------------------------------------------------------- LOCAL_COMPUTE

def test_local_compute_reports_fields_not_offsets(tmp_path):
    client, mock = gateway(tmp_path)
    payload = {**PAYLOAD, "instruction": "Is the gap above 2450000? Write a two-line note."}
    r = post(client, "salary_comparison", user(json.dumps(payload)))
    assert r.status_code == 200, r.text
    rep = r.json()["pgw_report"]
    assert rep["route"] == "LOCAL_COMPUTE" and rep["contract"] == "salary_comparison@5"
    assert {s["id"]: s["status"] for s in rep["stages"]}["compute"] == "done"
    assert all("start" not in d and "end" not in d and d["field"] for d in rep["detected"])
    by = {d["field"]: d for d in rep["detected"]}
    sent = received(mock)["messages"][1]["content"]
    for i in (0, 1):
        assert by[f"employees[{i}].name"]["action"] == "alias" and by[f"employees[{i}].name"]["placeholder"] in sent
        assert by[f"employees[{i}].employee_id"]["placeholder"] in sent
        assert by[f"employees[{i}].annual_ctc"] == {
            "message": 0, "part": None, "field": f"employees[{i}].annual_ctc", "type": "COMPENSATION", "class": "C2",
            "action": "withhold", "placeholder": None, "source": "declared"}
    assert by["outputs.difference"]["source"] == "computed" and by["outputs.difference"]["placeholder"] in sent
    assert by["outputs.ranking"]["action"] == "allow" and by["outputs.difference_value"]["action"] == "withhold"
    assert by["instruction"]["placeholder"] == "[COMPENSATION]" and "[COMPENSATION]" in sent
    assert rep["outbound"]["messages"] == received(mock)["messages"]
    text = json.dumps(rep, ensure_ascii=False)
    for v in SALARY_FORMS + ["Amit Trivedi", "Hetal Shah", "KD-10989", "KD-11063", "2,70,000", "270000"]:
        assert v not in text                                    # values never appear: fields, types, placeholders


# --------------------------------------------------------------------------------------------- PRIVATE_LOCAL

def test_private_route_reports_in_house_destination_honestly(tmp_path):
    local = LocalModel()
    client, ext = gateway(tmp_path, private=local)
    r = post(client, "ticket_summary", user("Imran Ansari (KD-11914) asks about his refund."), signed=False)
    assert r.status_code == 200 and r.headers["X-PGW-Route"] == "PRIVATE_LOCAL", r.text
    rep = r.json()["pgw_report"]
    assert not ext.received
    assert (rep["route"], rep["outcome"]) == ("PRIVATE_LOCAL", "kept_inside")
    assert rep["input_source"] == {"id": None, "verified": False}
    st = {s["id"]: s["status"] for s in rep["stages"]}
    assert [s["id"] for s in rep["stages"]] == STAGE_IDS
    assert st == {"received": "done", "verify_source": "failed", "compute": "skipped", "protect": "skipped",
                  "egress_check": "skipped", "provider": "done", "restore": "done"}
    out = rep["outbound"]
    assert (out["destination"], out["provider"], out["gateway_note_included"]) == ("in_house", "in_house", False)
    assert out["model"] == "test-inboundary-model"               # what the in-boundary server reports
    # what went in-house is the caller's UNPROTECTED text: it is not echoed (the gateway never returns C3), but the
    # hash and size are those of the exact bytes the in-boundary model received
    assert out["messages"] is None
    assert out["sha256"] == hashlib.sha256(local.received[-1]).hexdigest() and out["bytes"] == len(local.received[-1])
    assert "Imran" not in json.dumps(rep) and "KD-11914" not in json.dumps(rep)
    assert rep["detected"] == [] and rep["restore"]["restored"] == 0
    checks = {c["id"]: c["status"] for c in rep["checks"]}
    assert checks == {"egress_clean": "n/a", "declared_absent": "n/a", "source_verified": "fail",
                      "c3_not_returned": "pass"}


def test_private_route_for_c4_content_after_a_valid_signature(tmp_path):
    local = LocalModel()
    client, ext = gateway(tmp_path, private=local)
    r = post(client, "ticket_summary_any_script", user(HINDI),
             subjects={"requester": {"name": "Rahul Sharma", "employee_id": "KD-11914"}})
    assert r.status_code == 200 and not ext.received, r.text
    rep = r.json()["pgw_report"]
    st = {s["id"]: s["status"] for s in rep["stages"]}
    assert st["verify_source"] == "done" and st["protect"] == "skipped" and st["provider"] == "done"
    assert rep["input_source"]["verified"] is True and rep["route"] == "PRIVATE_LOCAL"
    assert rep["restore"]["kept_withheld"] >= 1 and "644748074748" not in r.text     # C3 never returned
    assert "राहुल" not in json.dumps(rep, ensure_ascii=False)


def test_private_route_names_the_stand_in_as_the_server_reports_it(tmp_path):
    # development only (production refuses the stand-in): the report must not dress it up as a model
    client, ext = gateway(tmp_path, private=LocalModel(backend=private_llm._Extractive()), mode="development")
    r = post(client, "ticket_summary", user("Imran Ansari asks about his refund."), signed=False)
    assert r.status_code == 200 and r.headers["X-PGW-Private-Model"] == "standin"
    assert r.json()["pgw_report"]["outbound"]["model"] == "extractive-standin"


def test_private_route_refusal_carries_stages_up_to_the_stop(tmp_path):
    client, ext = gateway(tmp_path, private=LocalModel())
    r = post(client, "ticket_summary", user("Imran Ansari asks."), signed=False, stream=True)
    assert r.status_code == 400
    rep = r.json()["error"]["pgw_report"]
    assert rep["outcome"] == "refused" and rep["route"] == "PRIVATE_LOCAL" and rep["outbound"] is None
    assert "provider" not in [s["id"] for s in rep["stages"]]


# ------------------------------------------------------------------------------------------------ refusals

def test_refusal_reports_stages_up_to_the_stop_and_never_echoes_content(tmp_path):
    client, mock = gateway(tmp_path)
    # unattested input on a contract that refuses it (salary_comparison: unattested REFUSE)
    r = post(client, "salary_comparison", user(json.dumps(PAYLOAD)), signed=False)
    assert r.status_code == 422 and not mock.received
    err = r.json()["error"]
    rep = err["pgw_report"]
    assert list(rep) == TOP_KEYS and rep["outcome"] == "refused"
    assert rep["error"] == {"type": err["type"], "message": err["message"]}
    assert [(s["id"], s["status"]) for s in rep["stages"]] == [("received", "done"), ("verify_source", "failed")]
    assert rep["outbound"] is None and rep["detected"] == [] and rep["restore"] is None
    assert {c["id"]: c["status"] for c in rep["checks"]}["source_verified"] == "fail"
    for v in SALARY_FORMS + ["Amit", "Hetal", "KD-10989"]:
        assert v not in r.text


def test_c4_refusal_stops_at_protect(tmp_path):
    client, mock = gateway(tmp_path)
    r = post(client, "salary_comparison", user(json.dumps({**PAYLOAD, "instruction": "नॉमिनी रामलाल तिवारी"})))
    assert r.status_code == 422 and r.json()["error"]["type"] == "content_requires_private_route"
    rep = r.json()["error"]["pgw_report"]
    assert [(s["id"], s["status"]) for s in rep["stages"]] == [
        ("received", "done"), ("verify_source", "done"), ("compute", "skipped"), ("protect", "failed")]
    assert "रामलाल" not in r.text and "Amit" not in r.text


@pytest.mark.parametrize("case", ["bad_json", "forbidden_purpose", "bad_parameter"])
def test_early_refusals_after_authentication_carry_a_report(tmp_path, case):
    client, _ = gateway(tmp_path)
    h = {"Authorization": f"Bearer {ON}", "X-PGW-Purpose": "ticket_summary"}
    if case == "bad_json":
        r = client.post("/v1/chat/completions", content=b"{Imran Ansari", headers=h)
    elif case == "forbidden_purpose":
        r = client.post("/v1/chat/completions", json={"model": "mock-1", "messages": user("Imran")},
                        headers={**h, "X-PGW-Purpose": "Imran Ansari KD-11914"})
    else:
        r = client.post("/v1/chat/completions", json={"model": "mock-1", "messages": user("x"),
                                                      "temperature": "Imran Ansari"}, headers=h)
    assert 400 <= r.status_code < 500
    rep = r.json()["error"]["pgw_report"]
    assert rep["stages"] == [{"id": "received", "status": "failed", "ms": rep["stages"][0]["ms"]}]
    assert rep["purpose"] in (None, "ticket_summary") and rep["outbound"] is None
    assert "Imran" not in r.text and "KD-11914" not in r.text


def test_egress_violation_is_refused_with_the_failed_check_and_no_text(tmp_path, monkeypatch):
    import pgw.gateway.app as gw
    from pgw.types import EgressViolation

    def violated(wire, values):
        raise EgressViolation(["PERSON"])
    monkeypatch.setattr(gw, "egress_check", violated)
    client, mock = gateway(tmp_path)
    r = post(client, "ticket_summary", user(SUMMARY_ASK))
    assert r.status_code == 500 and not mock.received
    rep = r.json()["error"]["pgw_report"]
    assert rep["outcome"] == "refused" and rep["outbound"] is None
    assert rep["stages"][-1]["id"] == "egress_check" and rep["stages"][-1]["status"] == "failed"
    assert {c["id"]: c["status"] for c in rep["checks"]}["egress_clean"] == "fail"
    for v in TICKET_RAW:
        assert v not in r.text


def test_upstream_failure_after_sending_is_an_error_with_hash_but_no_text(tmp_path):
    class Down(MockLLMUpstream):
        async def complete(self, body):
            self._prepare(body)
            raise RuntimeError("provider down")
    client, mock = gateway(tmp_path, upstream=Down())
    r = post(client, "ticket_summary", user(SUMMARY_ASK))
    assert r.status_code == 502
    rep = r.json()["error"]["pgw_report"]
    assert rep["outcome"] == "error" and rep["stages"][-1] == {**rep["stages"][-1], "id": "provider",
                                                               "status": "failed"}
    assert rep["outbound"]["messages"] is None
    assert rep["outbound"]["sha256"] == hashlib.sha256(mock.received[-1]).hexdigest()
    for v in TICKET_RAW + ["Rs 4,500", "PAY-88231"]:
        assert v not in r.text


@pytest.mark.parametrize("auth", [None, "Bearer wrong-key", "Basic abc", "Bearer "])
def test_401_and_pre_auth_failures_carry_no_report(tmp_path, auth):
    client, _ = gateway(tmp_path)
    h = {"X-PGW-Purpose": "ticket_summary"} | ({"Authorization": auth} if auth else {})
    r = client.post("/v1/chat/completions", json={"model": "mock-1", "messages": user(SUMMARY_ASK)}, headers=h)
    assert r.status_code == 401 and "pgw_report" not in r.text


# ------------------------------------------------------------------------- never logged, ledgered or sent upstream

def test_report_never_reaches_logs_ledger_or_upstream(tmp_path, caplog):
    client, mock = gateway(tmp_path)
    reports = []
    with caplog.at_level(logging.DEBUG):
        reports.append(post(client, "ticket_summary", user(SUMMARY_ASK)).json()["pgw_report"])
        reports.append(stream_report(post(client, "ticket_summary", user(SUMMARY_ASK), stream=True)))
        reports.append(post(client, "salary_comparison", user(json.dumps(PAYLOAD))).json()["pgw_report"])
        reports.append(post(client, "salary_comparison", user(json.dumps(PAYLOAD)), signed=False)
                       .json()["error"]["pgw_report"])
    logs = caplog.text + "\n".join(str(r.args) for r in caplog.records)
    ledger = (tmp_path / "egress.jsonl").read_text()
    upstream = b"\n".join(mock.received).decode("utf-8")
    placeholders = {d["placeholder"] for rep in reports for d in rep["detected"] if d["placeholder"]}
    assert placeholders
    for where in (logs, ledger):
        assert "pgw_report" not in where and '"detected"' not in where and '"outbound"' not in where
        assert "kept_withheld" not in where and "egress_clean" not in where
        for p in placeholders:
            assert p not in where
    assert "pgw_report" not in upstream and "request_id" not in upstream and "kept_withheld" not in upstream


# ----------------------------------------------------------- no values beyond what the caller sent in THIS request

def test_multi_turn_report_holds_no_value_from_earlier_turns_or_other_conversations(tmp_path):
    client, mock = gateway(tmp_path)
    turn1 = ("Imran Ansari (KD-11914, imran.ansari@kestrel-dyn.in, +91 98450 12345) asks about Rs 4,500. "
             "PAN ZXWPA3889P.")
    r1 = post(client, "ticket_summary", user(turn1), conv="conv-1")
    assert r1.status_code == 200, r1.text
    earlier_only = ["Ansari", "KD-11914", "imran.ansari", "98450", "4,500", "ZXWPA3889P", "Imran Ansari"]
    turn2 = "Imran asked again; please chase payroll. Raised by Hetal Shah."
    for conv in ("conv-1", "conv-2", None):
        r = post(client, "ticket_summary", user(turn2), conv=conv, subjects={"requester": {"name": "Hetal Shah"}})
        assert r.status_code == 200, r.text
        rep = r.json()["pgw_report"]
        text = json.dumps(rep, ensure_ascii=False)
        for v in earlier_only:
            assert v not in text, (conv, v)
        for d in rep["detected"]:                                   # every span is this request's own text
            assert turn2[d["start"]:d["end"]] in ("Imran", "Hetal Shah")
        # every string outside `outbound` is a fixed vocabulary word, an id of this request, or a placeholder
        allowed = ({d["placeholder"] for d in rep["detected"]} | set(STAGE_IDS) | set(CHECK_IDS) | set(LABELS)
                   | {t.value for t in EntityType} | {c.value for c in DataClass} | set(ACTION.values())
                   | {"declared", "detected", "done", "skipped", "failed", "pass", "fail", "n/a", "sent",
                      "EXT_PSEUDO", "ticket_summary", "ticket_summary@8", "helpdesk-ticket-export@1",
                      rep["request_id"]})
        for s in strings({k: v for k, v in rep.items() if k != "outbound"}):
            assert s in allowed, s
    # in the same conversation "Imran" alone is linked to the earlier full name's alias (the conversation's memory
    # is used for protection) but only the alias, never the remembered value, is in the report
    alias1 = {d["placeholder"] for d in r1.json()["pgw_report"]["detected"] if d["type"] == "PERSON"}
    r2 = post(client, "ticket_summary", user(turn2), conv="conv-1", subjects={"requester": {"name": "Hetal Shah"}})
    imran = [d for d in r2.json()["pgw_report"]["detected"] if turn2[d["start"]:d["end"]] == "Imran"]
    assert imran and imran[0]["placeholder"] in alias1


# -------------------------------------------------------------------------------- transparency off: unchanged

def _strip_report_event(text: str) -> str:
    return re.sub(r'data: \{"pgw_report".*?\n\n', "", text)


def test_transparency_off_responses_are_byte_identical(tmp_path):
    master = os.urandom(32).hex()
    on_client, _ = gateway(tmp_path / "on", master=master)
    off_client, _ = gateway(tmp_path / "off", master=master)
    cases = [("ticket_summary", user(SUMMARY_ASK), {}), ("ticket_summary", user(SUMMARY_ASK), {"stream": True}),
             ("salary_comparison", user(json.dumps(PAYLOAD)), {}),
             ("salary_comparison", user(json.dumps(PAYLOAD)), {"signed": False})]
    for purpose, messages, kw in cases:
        on = post(on_client, purpose, messages, key=ON, **kw)
        off = post(off_client, purpose, messages, key=OFF, **kw)
        assert on.status_code == off.status_code
        assert "pgw_report" not in off.text
        assert {k: v for k, v in off.headers.items() if k.lower() not in ("x-pgw-request-id", "content-length")} == \
            {k: v for k, v in on.headers.items() if k.lower() not in ("x-pgw-request-id", "content-length")}
        if kw.get("stream"):
            assert off.content == _strip_report_event(on.text).encode()
            continue
        j = on.json()
        if "error" in j:
            j["error"].pop("pgw_report")
        else:
            j.pop("pgw_report")
        assert off.content == json.dumps(j, ensure_ascii=False, separators=(",", ":")).encode()


# Responses of the gateway BEFORE this feature (commit 275cd59) for the requests below, by sha256 of the body bytes:
# computed by running golden_bodies() against a checkout of that commit. An app without transparency must still get
# exactly these bytes. If an unrelated, intended change alters them, recompute them the same way at its parent.
GOLDEN = {
    "non_stream": "776764b258c054965c5fa6ebf22d984086be22aa1f5be876eae09ead96b6950f",
    "stream": "ff9c3b8cff1c413be4ace10d1e89ab6eeaaa0499b9228b45a7b0b1c53f9a8f9c",
    "refused": "c067cb1953c656c1076b406a7a973eace774f97ed8cc6eb5e246cb77876fe2ad",
}


def golden_bodies(tmp_path) -> dict:
    client, _ = gateway(tmp_path, master="11" * 32)
    return {"non_stream": post(client, "ticket_summary", user(SUMMARY_ASK), key=OFF).content,
            "stream": post(client, "ticket_summary", user(SUMMARY_ASK), key=OFF, stream=True).content,
            "refused": post(client, "salary_comparison", user(json.dumps(PAYLOAD)), key=OFF, signed=False).content}


def test_transparency_off_matches_the_pre_feature_gateway_bytes(tmp_path):
    got = {k: hashlib.sha256(v).hexdigest() for k, v in golden_bodies(tmp_path).items()}
    assert got == GOLDEN


def test_transparency_off_never_builds_a_report(tmp_path, monkeypatch):
    import pgw.gateway.report as report

    def forbidden(*a, **k):
        raise AssertionError("a report was built for an app without transparency")
    monkeypatch.setattr(report.Report, "__init__", forbidden)
    client, _ = gateway(tmp_path)
    for kw in ({}, {"stream": True}):
        assert post(client, "ticket_summary", user(SUMMARY_ASK), key=OFF, **kw).status_code == 200
    assert post(client, "salary_comparison", user(json.dumps(PAYLOAD)), key=OFF, signed=False).status_code == 422


# ----------------------------------------------------------------------------------------- size and performance

def test_detected_is_capped_and_counts_stay_true(tmp_path, monkeypatch):
    import pgw.gateway.report as report
    assert report.MAX_DETECTED == 2000
    client, _ = gateway(tmp_path)
    full = post(client, "ticket_summary", user(SUMMARY_ASK)).json()["pgw_report"]
    monkeypatch.setattr(report, "MAX_DETECTED", 3)
    cut = post(client, "ticket_summary", user(SUMMARY_ASK)).json()["pgw_report"]
    assert len(full["detected"]) > 3 and full["truncated"] is False
    assert len(cut["detected"]) == 3 and cut["truncated"] is True
    assert cut["counts"] == full["counts"]                      # counted over everything, not only what is listed


def _synthetic(n):
    """A Protected-shaped object with n detections in one 3n-character segment (for the builder alone)."""
    from pgw.alias import canon
    from pgw.types import Action, Detection, Entity, EntityType
    text = "".join(f"{i % 10}AB" for i in range(n))
    dets = [Detection(3 * i, 3 * i + 3, EntityType.IDENTIFIER, 0.5, "D2:x") for i in range(n)]
    ents = {(EntityType.IDENTIFIER, canon(EntityType.IDENTIFIER, text[3 * i:3 * i + 3])):
            Entity(EntityType.IDENTIFIER, text[3 * i:3 * i + 3], Action.TOKENIZE, "[IDENTIFIER_X]", "[IDENTIFIER_X]")
            for i in range(10)}
    seg = SimpleNamespace(text=text, loc=(0, None, "content"))
    return SimpleNamespace(segments=[seg], detections=[dets], entities=ents)


def test_size_is_bounded_for_a_huge_number_of_spans_and_building_is_fast():
    from pgw.gateway.report import MAX_DETECTED, Report
    rep = Report("req_x", time.perf_counter())
    rep.prot = _synthetic(50_000)
    t = time.perf_counter()
    out = rep.build("sent")
    ms = (time.perf_counter() - t) * 1000
    assert len(out["detected"]) == MAX_DETECTED and out["truncated"] is True
    assert out["counts"]["by_type"] == {"IDENTIFIER": 50_000} and out["counts"]["replaced"] == 50_000
    assert len(json.dumps(out)) < 500_000                       # bounded, whatever the request held
    assert ms < 500, ms                                         # 50k spans; a real request has far fewer


def test_building_the_report_adds_no_measurable_latency(tmp_path, monkeypatch):
    import pgw.gateway.report as report
    spent = []
    real = report.Report.build

    def timed(self, *a, **k):
        t = time.perf_counter()
        try:
            return real(self, *a, **k)
        finally:
            spent.append((time.perf_counter() - t) * 1000)
    monkeypatch.setattr(report.Report, "build", timed)
    client, _ = gateway(tmp_path)
    big = (SUMMARY_ASK + "\n") * 15                              # ~10k characters, ~150 spans
    t = time.perf_counter()
    r = post(client, "ticket_summary", user(big))
    total = (time.perf_counter() - t) * 1000
    assert r.status_code == 200 and len(r.json()["pgw_report"]["detected"]) >= 100
    assert spent and spent[0] < max(5.0, 0.05 * total), (spent, total)
