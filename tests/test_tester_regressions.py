"""Regressions for the external tester's reports (2026-10): payroll exports, header rows, sibling amounts, names that
ran on to the next line. Production mode, mock provider, synthetic values; every wire assertion reads what the
provider received."""
import json
import logging
import os

import pytest
from starlette.testclient import TestClient

from pgw import egress
from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.normalize import views
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack
from pgw.types import Context, EgressViolation, EntityType as T
from tests.sealed import ROOT, attest, production, sha

KEY = "tester-key"
P = "hr_email_draft"
PROT = Protector(PolicyPack.load(ROOT / "policies/hris-default.yaml"), Keys(bytes(range(32))))
CTX = Context("acme", "hr-assistant", "email_draft", conversation_id="t-1")


@pytest.fixture
def gw(tmp_path):
    mock = MockLLMUpstream("echo")
    cfg = production(tmp_path, [{"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY), "restore_output": True,
                                 "models": ["mock-1"], "purposes": [P], "tasks": {P: str(ROOT / f"contracts/{P}.yaml")}}])
    s = Settings(**cfg, ledger_db_path=str(tmp_path / "l.sqlite"), egress_ledger_path=str(tmp_path / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    client = TestClient(create_app(s), raise_server_exceptions=False)

    def send(text):
        mock.received.clear()
        body = {"model": "mock-1", "pgw_subjects": {"recipient": {"name": "Priya Raghunathan"}},
                "messages": [{"role": "user", "content": text}]}
        r = client.post("/v1/chat/completions", json=body,
                        headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": P, **attest(P, body)})
        wire = json.loads(mock.received[-1])["messages"][-1]["content"] if mock.received else None
        return r, wire
    return send


def _no_traceback(caplog):
    assert not any(r.exc_info or "Traceback" in r.getMessage() for r in caplog.records)
    assert not any("egress_violation" in r.getMessage() for r in caplog.records)


# --- 1. a fixed-width payroll line is not a card --------------------------------------------------------------------

def test_payroll_line_amounts_are_not_joined_into_a_card(gw, caplog):
    caplog.set_level(logging.INFO)
    line = "57304 VARSHA GOKHALE 38420.00 15368.00 49102.00"     # the three salaries joined pass Luhn
    assert any(n == "digits_joined" for n, *_ in views("x 4111 1111.1111 1111"))   # the evasion view still exists
    r, wire = gw(line)
    assert r.status_code == 200
    assert "[CARD" not in wire and "38420.00" in wire and "49102.00" in wire
    _no_traceback(caplog)


@pytest.mark.parametrize("text", ["57304 VARSHA GOKHALE 38420.00 15368.00 49102.00",
                                  "net 41733.50 15022.00 63120.75 paid", "38420.00  15368.00  49102.00"])
def test_digit_joining_never_crosses_a_decimal_amount_or_a_column_gap(text):
    joined = dict((n, v) for n, v, _ in views(text)).get("digits_joined", text)
    for amt in ("38420.00", "15368.00", "49102.00", "41733.50", "15022.00", "63120.75"):
        if amt in text:
            assert amt in joined
    assert not any(d.type is T.CARD for d in PROT.detect_text(text))


@pytest.mark.parametrize("card", ["4539 5781 2633 4082", "4539-5781 2633.4082", "4539578126334082"])
def test_real_card_groupings_are_still_cards(card):
    text = f"card {card} on file"
    assert any(d.type is T.CARD and text[d.start:d.end] == card for d in PROT.detect_text(text))


def test_short_digit_groups_joined_by_the_view_are_not_a_card():
    text = "ref 45 39 57 81 26 33 40 82"                        # Luhn-valid only once glued
    assert not any(d.type is T.CARD for d in PROT.detect_text(text))


# --- 2. the header row of a fixed-width export: no 500; the columns classify the rows -----------------------------

HEADER = "EMP_ID  EMPLOYEE NAME  BASIC  HRA  NET_PAY"


def test_header_line_does_not_fail_the_request(gw, caplog):
    # A header plus a payroll row is a table: no 500, and (since the structure check) never sent externally.
    caplog.set_level(logging.INFO)
    r, wire = gw(HEADER + "\n57304 VARSHA GOKHALE 38420.00 15368.00 49102.00")
    assert r.status_code == 422 and r.json()["error"]["type"] == "structured_data_in_free_text"
    assert wire is None and "Traceback" not in r.text and "VARSHA" not in r.text
    _no_traceback(caplog)


def test_header_words_are_field_names_not_values():
    text = HEADER + "\n57304 VARSHA GOKHALE 38420.00 15368.00 49102.00"
    (dets,) = PROT.analyze([{"role": "user", "content": text}], CTX)
    assert all(d.start >= len(HEADER) for d in dets)


def test_space_aligned_table_is_read_by_column(gw):
    text = (HEADER + "\n57304  VARSHA GOKHALE  38420.00  15368.00  49102.00\n"
                     "57311  NITIN APTE      29950.00  11980.00  38870.00")
    r, wire = gw(text)
    assert r.status_code == 422 and wire is None             # a table never crosses on the free-text route
    (dets,) = PROT.analyze([{"role": "user", "content": text}], CTX)   # the detector still reads it by column
    found = {text[d.start:d.end] for d in dets}
    assert {"VARSHA GOKHALE", "NITIN APTE", "57304", "38420.00", "49102.00"} <= found
    assert all(d.start >= len(HEADER) for d in dets)


def test_gateway_text_never_trips_the_egress_check():
    note = "Draft a reply to the employee."
    wire = json.dumps({"messages": [{"role": "system", "content": note}, {"role": "user", "content": "[x]"}]}).encode()
    with pytest.raises(EgressViolation):
        egress.check(wire, [(T.EMPLOYEE_ID, "employee")])
    egress.check(wire, [(T.EMPLOYEE_ID, "employee")], (note,))          # own text is blanked
    leak = json.dumps({"messages": [{"role": "system", "content": note},
                                    {"role": "user", "content": "employee"}]}).encode()
    with pytest.raises(EgressViolation):                                 # user text is still swept
        egress.check(leak, [(T.EMPLOYEE_ID, "employee")], (note,))


# --- 3. sibling amounts: no 500, one decision per clause ------------------------------------------------------------

@pytest.mark.parametrize("text", ["Rs 300 per month and Rs 30 per year", "Rs 300 per month. Then Rs 30 per year.",
                                  "Rs 450 per month and Rs 45 per year for the canteen card"])
def test_amount_prefixes_do_not_fail_the_request(gw, caplog, text):
    caplog.set_level(logging.INFO)
    r, _ = gw(text)
    assert r.status_code == 200
    _no_traceback(caplog)


def test_amounts_in_one_clause_are_handled_alike(gw):
    r, wire = gw("Rs 300 per month and Rs 30 per year")
    assert r.status_code == 200 and "Rs" not in wire and wire.count("[COMPENSATION_") == 2


def test_egress_compares_amounts_as_whole_numbers():
    wire = b'{"content": "Rs 300 per month, Rs 30,000 total"}'
    egress.check(wire, [(T.COMPENSATION, "Rs 30")])
    with pytest.raises(EgressViolation):
        egress.check(b'{"content": "Rs 30 per year"}', [(T.COMPENSATION, "Rs 30")])
    with pytest.raises(EgressViolation):                                 # identifiers keep substring matching
        egress.check(b'{"content": "x 4539578126334082 1"}', [(T.CARD, "453957812633408")])


# --- 4. a person span never crosses a line break --------------------------------------------------------------------

@pytest.mark.parametrize("text,name,nxt", [
    ("Please ask Mr. Tarun Bhosale\nMother of the employee called.", "Tarun Bhosale", "Mother"),
    ("Regards,\nKavita Rane\nFather", "Kavita Rane", "Father"),
    ("Dear Omkar\nSister is admitted.", "Omkar", "Sister"),
])
def test_person_span_stops_at_the_line_break(gw, text, name, nxt):
    (dets,) = PROT.analyze([{"role": "user", "content": text}], CTX)
    persons = [text[d.start:d.end] for d in dets if d.type is T.PERSON]
    assert persons == [name]
    r, wire = gw(text)
    assert r.status_code == 200 and name not in wire and f"\n{nxt}" in wire


def test_strong_detections_keep_every_line_piece():
    text = "Nominee Ritu\nSawant joined."
    prot = Protector(PROT.policy, PROT.keys)
    (dets,) = prot._detect_segments(*_seg(text), declared=[(T.PERSON, "Ritu\nSawant")])
    pieces = sorted(text[d.start:d.end] for d in dets if d.type is T.PERSON)
    assert pieces == ["Ritu", "Sawant"]


def _seg(text):
    from pgw.pipeline import _segments
    return _segments([{"role": "user", "content": text}]), CTX
