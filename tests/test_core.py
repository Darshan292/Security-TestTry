import json
import random

import pytest

from pgw.alias import AliasAllocator, canon, make_alias, parse_alias, scope_key
from pgw.detect.edm import RestoreLedger, RosterIndex
from pgw.egress import check
from pgw.normalize import normalize, to_original
from pgw.pipeline import ConversationIdRequired, Keys, Protector, UnsupportedContent
from pgw.policy import PolicyPack, generalize
from pgw.stream import Restorer
from pgw.types import BlockedRequest, Context, EgressViolation, EntityType as T

KEYS = Keys(bytes(range(32)))
POLICY = PolicyPack.load("policies/hris-default.yaml")
CTX = Context("acme", "hr-assistant", "email_draft", conversation_id="conv-1")


def protector(roster=None, ledger=None):
    return Protector(POLICY, KEYS, roster, ledger)


def user(text):
    return [{"role": "user", "content": text}]


# --- alias ------------------------------------------------------------------------

def test_alias_is_scoped_and_deterministic():
    k1, k2 = scope_key(b"k" * 32, "conv-1"), scope_key(b"k" * 32, "conv-2")
    assert make_alias(k1, T.PERSON, "Rahul Sharma") == make_alias(k1, T.PERSON, "rahul  SHARMA")
    assert make_alias(k1, T.PERSON, "Rahul Sharma") != make_alias(k2, T.PERSON, "Rahul Sharma")
    assert make_alias(k1, T.PHONE, "+91-98200 12345") == make_alias(k1, T.PHONE, "9820012345")


def test_alias_collision_resolved_deterministically(monkeypatch):
    import pgw.alias as A
    monkeypatch.setattr(A, "_b32", lambda d, n: "A" * n)   # every value collides
    alloc = AliasAllocator(b"k" * 32)
    alloc.get(T.PERSON, "alice"), alloc.get(T.PERSON, "bob")
    a, b = alloc.get(T.PERSON, "alice"), alloc.get(T.PERSON, "bob")
    assert a != b and {len(a), len(b)} == {len("[PERSON_AAAAAA]"), len("[PERSON_AAAAAAAA]")}
    assert (alloc.get(T.PERSON, "alice"), alloc.get(T.PERSON, "bob")) == (a, b)   # stable


def test_parse_alias_lenient():
    assert parse_alias("[PERSON_7QX2MA]") == ("PERSON", "7QX2MA")
    assert parse_alias("person_7qx2ma") == ("PERSON", "7QX2MA")
    assert parse_alias("[EMPLOYEE_ID_7QX2MA]") == ("EMPLOYEE_ID", "7QX2MA")
    assert parse_alias("PERSON_7QX2M") is None
    # ordinary identifiers must never look like aliases (regression: XML tags were rewritten)
    for word in ("Phone_Number", "Bank_Account_Number", "EMAIL_STATUS", "PERSON_STREET", "PHONE_TYPE_A"):
        assert parse_alias(word) is None, word


# --- normalize ----------------------------------------------------------------------

def test_normalize_zero_width_and_homoglyph_map_back():
    raw = "mail r​ahul@acmе.in now"      # zero-width + Cyrillic 'е'
    norm, omap = normalize(raw)
    assert "rahul@acme.in" in norm
    s = norm.index("rahul@acme.in")
    os_, oe = to_original(omap, s, s + len("rahul@acme.in"))
    assert raw[os_:oe] == "r​ahul@acmе.in"


# --- pipeline --------------------------------------------------------------------------

REPORT_EXAMPLE = ("Draft an email to Rahul Sharma at rahul.sharma@company.com saying that his "
                  "salary revision from ₹18,40,000 to ₹20,10,000 will be effective from 1 October.")


def test_report_example_round_trip():
    p = protector().protect(user(REPORT_EXAMPLE), CTX)
    out = p.messages[0]["content"]
    for raw in ("Rahul Sharma", "rahul.sharma@company.com", "₹18,40,000", "₹20,10,000"):
        assert raw not in out
    assert "1 October" in out
    check(json.dumps({"messages": p.messages}).encode(), p.sensitive_values)   # no violation
    r = Restorer(p.alias_map)
    assert r.feed(out) + r.finish() == REPORT_EXAMPLE


@pytest.mark.xfail(strict=True, reason="Phase 1 has no NER: a non-roster name with no cue is missed (DESIGN §9.1)")
def test_bare_external_name_without_cue_is_known_gap():
    p = protector().protect(user("Rahul Sharma raised a grievance yesterday."), CTX)
    assert "Rahul Sharma" not in p.messages[0]["content"]


def test_partial_name_propagates():
    p = protector().protect(user("Dear Rahul Sharma, ... Rahul confirmed on call."), CTX)
    assert "Rahul" not in p.messages[0]["content"]


def test_propagation_across_messages_and_tool_args():
    msgs = [
        {"role": "system", "content": "Employee: rahul.sharma@acme.in"},
        {"role": "user", "content": "ping rahul.sharma@acme.in again"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "send", "arguments": '{"to": "rahul.sharma@acme.in"}'}}]},
    ]
    p = protector().protect(msgs, CTX)
    assert "rahul.sharma@acme.in" not in json.dumps(p.messages)


def test_high_risk_blocks_without_leaking_value():
    with pytest.raises(BlockedRequest) as ei:
        protector().protect(user("Aadhaar 2341 2341 2346 for Rahul"), CTX)
    assert "2341" not in str(ei.value)


def test_integration_debug_redacts_secret_and_never_restores():
    ctx = Context("acme", "it", "integration_debug")
    p = protector().protect(user("curl -H 'Authorization: Bearer sk-proj-abcdefghijklmnopqrstuvwxyz0123'"), ctx)
    assert "sk-proj" not in p.messages[0]["content"]


def test_conversation_id_required_for_multi_turn_restore():
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "more"}]
    with pytest.raises(ConversationIdRequired):
        protector().protect(msgs, Context("acme", "app", "email_draft"))


def test_unknown_content_part_rejected():
    with pytest.raises(UnsupportedContent):
        protector().protect([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}], CTX)
    with pytest.raises(UnsupportedContent):
        protector().protect([{"role": "user", "content": "x", "audio": "..."}], CTX)


def test_generalize():
    assert generalize(T.DATE_OF_BIRTH, "12 March 1984") == "[born 1980s]"
    assert generalize(T.COMPENSATION, "₹18,40,000") == "[COMPENSATION ₹15L–₹20L]"
    assert generalize(T.COMPENSATION, "38 lakh") == "[COMPENSATION ₹35L–₹40L]"


# --- egress invariant --------------------------------------------------------------------

def test_egress_invariant_catches_bug_leak_in_any_encoding():
    sens = [(T.PERSON, "Rahul Sharma"), (T.COMPENSATION, "₹18,40,000"), (T.PHONE, "98200 12345")]
    with pytest.raises(EgressViolation):
        check(json.dumps({"x": "RAHUL SHARMA"}).encode(), sens)
    with pytest.raises(EgressViolation):
        check(json.dumps({"x": "₹18,40,000"}).encode(), sens)          # ensure_ascii -> ₹
    with pytest.raises(EgressViolation):
        check(b'{"x": "call 9820012345"}', sens)                         # reformatted number
    check(b'{"x": "[PERSON_7QX2MA]"}', sens)


# --- streaming ---------------------------------------------------------------------------

def _stream(restorer, text, cuts):
    out, prev = [], 0
    for c in sorted(cuts) + [len(text)]:
        out.append(restorer.feed(text[prev:c]))
        prev = c
    out.append(restorer.finish())
    return "".join(out)


def test_stream_restore_every_split_offset():
    p = protector().protect(user(REPORT_EXAMPLE), CTX)
    aliased = "Hi " + p.messages[0]["content"] + " Thanks."
    r0 = Restorer(p.alias_map); expected = r0.feed(aliased) + r0.finish()
    for i in range(1, len(aliased)):
        assert _stream(Restorer(p.alias_map), aliased, [i]) == expected, i
    rng = random.Random(7)
    for _ in range(300):
        cuts = rng.sample(range(1, len(aliased)), k=rng.randint(2, 40))
        assert _stream(Restorer(p.alias_map), aliased, cuts) == expected


def test_output_gate_fabricated_alias_and_split_raw_aadhaar():
    r = Restorer({})
    out = _stream(r, "See [PERSON_9ZZZZZ] and id 2341 2341 2346 ok", [8, 30, 33])
    assert "[unknown PERSON]" in out and "2341 2341 2346" not in out
    assert {e.kind for e in r.events} == {"fabricated_alias", "raw_pii"}


def test_gate_leaves_ordinary_identifiers_alone():
    r = Restorer({})
    xml = "<wd:Phone_Number>[PHONE_3QX2MA]</wd:Phone_Number> <Bank_Account_Number/>"
    out = r.feed(xml) + r.finish()
    assert "<wd:Phone_Number>" in out and "<Bank_Account_Number/>" in out
    assert [e.kind for e in r.events] == ["fabricated_alias"]      # only the real alias-shaped token


def test_never_restore_secret():
    ctx = Context("acme", "it", "eval_tokenize_all")
    p = protector().protect(user("key sk-proj-abcdefghijklmnopqrstuvwxyz0123 here"), ctx)
    r = Restorer(p.alias_map, never_restore=POLICY.never_restore)
    out = r.feed(p.messages[0]["content"]) + r.finish()
    assert "sk-proj" not in out


# --- T21: restored values re-sent in truncated history (ADR-0010) --------------------------

def test_t21_truncated_history_caught_by_restore_ledger():
    ledger = RestoreLedger(b"L" * 32)
    pr = protector(ledger=ledger)
    # turn 1: name found by the greeting cue
    p1 = pr.protect(user("Dear Anika Deshpande, please summarise the grievance."), CTX)
    r = Restorer(p1.alias_map)
    alias = next(a for a, e in p1.alias_map.items() if e.value == "Anika Deshpande")
    reply = r.feed(f"[{alias}] raised a grievance about shift allocation.") + r.finish()
    assert reply.startswith("Anika Deshpande")
    ledger.record(CTX, r.restored)
    # turn 6: client dropped turn 1's user message; the name now has no cue at all
    history = [{"role": "assistant", "content": reply}, {"role": "user", "content": "Shorten it."}]
    p2 = pr.protect(history, CTX)
    assert "Anika" not in json.dumps(p2.messages)
    check(json.dumps({"messages": p2.messages}).encode(), p2.sensitive_values)
    # without the ledger the same request leaks: this is the T21 hole
    p3 = protector().protect(history, CTX)
    assert "Anika Deshpande" in json.dumps(p3.messages)


def test_roster_edm_catches_known_employee_without_cues():
    roster = RosterIndex(b"E" * 32)
    roster.add_employee({"full_name": "Venkatesh Iyer", "first_name": "Venkatesh", "last_name": "Iyer",
                         "email": "v.iyer@acme.in", "employee_id": "EMP-00412", "phone": "9820012345"})
    p = protector(roster=roster).protect(user("escalation from venkatesh iyer re: late payroll"), CTX)
    assert "venkatesh" not in p.messages[0]["content"].lower()


def test_edm_numeric_match_does_not_swallow_neighbours():
    """Regression: digit-canonicalised roster phones matched any window containing the digits."""
    roster = RosterIndex(b"E" * 32)
    roster.add_employee({"full_name": "Nayana Gaikwad", "first_name": "Nayana", "last_name": "Gaikwad",
                         "email": "nayana.gaikwad@acme.in", "employee_id": "EMP-1", "phone": "9756513174"})
    text = '"primaryWorkEmail": "nayana.gaikwad@acme.in",\n "mobilePhone": "9756513174"'
    dets = {(text[d.start:d.end], d.type) for d in roster.detect(text)}
    assert ("nayana.gaikwad@acme.in", T.EMAIL) in dets
    assert ("9756513174", T.PHONE) in dets
    assert all(t is not T.PHONE or "@" not in v for v, t in dets)


def test_roster_single_name_rules():
    roster = RosterIndex(b"E" * 32)
    roster.add_employee({"full_name": "Payal Mehta", "first_name": "Payal", "last_name": "Mehta",
                         "email": "p@acme.in", "employee_id": "EMP-2", "phone": ""})
    roster.add_employee({"full_name": "Will Price", "first_name": "Will", "last_name": "Price",
                         "email": "w@acme.in", "employee_id": "EMP-3", "phone": ""})
    hits = lambda t: {t[d.start:d.end] for d in roster.detect(t)}
    assert "Payal" in hits("Thanks,\nPayal")
    assert "payal" in hits("[12:24] payal: portal is down")
    assert hits("We will review the price list in May.") == set()
    assert "Will" in hits("Dear Will, please review.")
