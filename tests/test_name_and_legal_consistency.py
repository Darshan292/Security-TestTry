"""One alias per person, legal-instrument titles are not people, statutory amounts are not pay (audit C3/C4).

Rules under test (pgw/pipeline.py, pgw/detect/recognizers.py; SECURITY_BOUNDARY.md §7):
  1. A partial mention of a detected or declared full name (a name word, honorific + surname, initial + surname,
     any case) gets the full name's alias, in one request and across the turns of a conversation. A partial that
     two different full names contain keeps an alias of its own (still replaced, never guessed), and stays so.
  2. A capitalised sequence ending in an instrument head word (Act, Rules, Code, Scheme, Policy, ...), with an
     optional year, is a title: it is neither a PERSON nor a source or target of partial-name propagation.
  3. An amount governed in its clause by a limit or statutory construction is a public constant, unless a specific
     person anchors it in the same clause. Contracts may also seal exact `public_constants`.

Synthetic data only. End-to-end tests read the bytes the mock provider received and the restored answer.
"""
from __future__ import annotations

import json
import os
import re

import pytest
import yaml
from starlette.testclient import TestClient

from pgw.alias import ALIAS_RE
from pgw.detect import recognizers
from pgw.detect.edm import RestoreLedger
from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack
from pgw.product.contracts import ContractError, load_contract
from pgw.stream import Restorer
from pgw.types import Context, EntityType as T
from tests.sealed import ROOT, attest, production, sha

POLICY = PolicyPack.load(ROOT / "policies/hris-default.yaml")


def protector(ledger=None) -> Protector:
    return Protector(POLICY, Keys(b"K" * 32), ledger=ledger)


def ctx(conv: str | None = None) -> Context:
    return Context("acme", "hr-app", "eval_tokenize_all", conversation_id=conv)


def protect(text: str, conv: str | None = None, pr: Protector | None = None, history=()):
    msgs = [*history, {"role": "user", "content": text}]
    return (pr or protector()).protect(msgs, ctx(conv))


def person_aliases(text: str) -> list[str]:
    return [m.group(0) for m in ALIAS_RE.finditer(text) if m.group(1).upper() == "PERSON"]


def restore(prot, text: str) -> str:
    r = Restorer(prot.alias_map)
    return r.feed(text) + r.finish()


# ==== 1. partial names share the full name's alias ===============================================================

@pytest.mark.parametrize("text, parts", [
    # given name only
    ("Please send the brief to Anirudh Venkataraman. Anirudh starts in Pune on Monday.", ["Anirudh"]),
    # surname only (after the full name)
    ("Escalation from Farida Kazmi about late arrears. Kazmi has asked twice.", ["Kazmi"]),
    # honorific + surname
    ("Call with Devika Ramaswamy at noon. Ms Ramaswamy wants the letter today.", ["Ramaswamy"]),
    # initial + surname, written before the full name
    ("T. Oberoi raised a query. A meeting with Tarun Oberoi is booked.", ["T. Oberoi", "Oberoi"]),
    # lowercase repeat of the given name and of the whole name
    ("Note from Lalitha Subramanian: lalitha wants a callback; LALITHA SUBRAMANIAN signed it.",
     ["lalitha", "LALITHA SUBRAMANIAN"]),
    # possessive keeps its "'s" outside the alias
    ("Feedback for Joydeep Chakraborty. Chakraborty's manager approved it.", ["Chakraborty"]),
])
def test_partial_mentions_share_the_full_name_alias(text, parts):
    p = protect(text)
    out = p.messages[0]["content"]
    assert len(set(person_aliases(out))) == 1, out
    for w in parts:
        assert w not in out
    back = restore(p, out)
    full = next(e.value for e in p.alias_map.values() if e.type is T.PERSON)
    assert len(full.split()) == 2 and full in back                 # restores to the person's full name
    assert not ALIAS_RE.search(back)


def test_possessive_stays_after_the_restored_name():
    p = protect("Thanks to Ishaan Bhattacharya for the report. Bhattacharya's numbers are final.")
    out = p.messages[0]["content"]
    assert re.search(r"\[PERSON_\w+\]'s numbers", out)
    assert "Ishaan Bhattacharya's numbers" in restore(p, out)


def test_two_people_sharing_a_given_name_are_not_merged():
    text = ("Meeting with Rekha Pillai and Rekha Dsouza today. Rekha asked for the minutes. "
            "Ms Pillai will chair it.")
    p = protect(text)
    out = p.messages[0]["content"]
    al = person_aliases(out)
    assert "Rekha" not in out and "Pillai" not in out and "Dsouza" not in out
    full_a, full_b, lone, pillai = al[0], al[1], al[2], al[3]
    assert full_a != full_b
    assert lone not in (full_a, full_b)          # ambiguous "Rekha": its own alias, never guessed
    assert pillai == full_a                      # "Ms Pillai" is unambiguous: Rekha Pillai
    back = restore(p, out)
    assert "Rekha asked" in back and "Ms Rekha Pillai will chair" in back


def test_declared_full_name_covers_a_request_that_only_writes_part_of_it():
    from pgw.product import subjects
    from pgw.product.contracts import SubjectSpec
    spec = SubjectSpec({"recipient": 1}, ("recipient",), {"name": T.PERSON})
    ms = subjects.matchers(subjects.parse({"recipient": {"name": "Gurpreet Sandhu"}}, spec))
    p = protector().protect([{"role": "user", "content": "Remind Gurpreet that Sandhu's form is due."}], ctx(), ms)
    out = p.messages[0]["content"]
    assert len(set(person_aliases(out))) == 1 and "Gurpreet" not in out and "Sandhu" not in out
    assert restore(p, out) == "Remind Gurpreet Sandhu that Gurpreet Sandhu's form is due."


def test_conversation_turns_share_aliases_without_history():
    ledger = RestoreLedger(b"L" * 32)
    pr = protector(ledger)
    p1 = protect("Draft a reply to Saurabh Kulshreshtha about his relocation.", "conv-1", pr)
    full = person_aliases(p1.messages[0]["content"])[0]
    # turn 2: the app sends only the new message; each partial form gets turn 1's alias
    for text in ("Saurabh also wants the hotel details.", "Has Mr Kulshreshtha signed?", "Tell S. Kulshreshtha."):
        p2 = protect(text, "conv-1", pr)
        out = p2.messages[0]["content"]
        assert person_aliases(out) == [full], (text, out)
        assert "Saurabh" not in out and "Kulshreshtha" not in out
    # another conversation is another alias scope
    p3 = protect("Saurabh also wants the hotel details.", "conv-2", pr)
    assert person_aliases(p3.messages[0]["content"]) != [full]


def test_conversation_with_history_restores_coherently():
    ledger = RestoreLedger(b"L" * 32)
    pr = protector(ledger)
    p1 = protect("Summarise the ticket from Nandini Hegde. Nandini needs the payslip.", "conv-h", pr)
    out1 = p1.messages[0]["content"]
    a = person_aliases(out1)
    assert len(set(a)) == 1
    r = Restorer(p1.alias_map)
    reply = r.feed(f"{a[0]} needs her payslip.") + r.finish()
    assert reply == "Nandini Hegde needs her payslip."
    ledger.record(ctx("conv-h"), r.restored)
    hist = [{"role": "user", "content": "Summarise the ticket from Nandini Hegde. Nandini needs the payslip."},
            {"role": "assistant", "content": reply}]
    p2 = protect("What did hegde ask for?", "conv-h", pr, history=hist)
    sent = json.dumps(p2.messages)
    assert "Nandini" not in sent and "Hegde" not in sent and "hegde" not in sent
    assert set(person_aliases(sent)) == {a[0]}


def test_ambiguity_is_remembered_across_turns():
    ledger = RestoreLedger(b"L" * 32)
    pr = protector(ledger)
    first = person_aliases(protect("Email to Harini Bose about leave.", "conv-a", pr).messages[0]["content"])[0]
    second = person_aliases(protect("Email to Harini Menon about leave.", "conv-a", pr).messages[0]["content"])[0]
    assert first != second
    out = protect("Harini called again.", "conv-a", pr).messages[0]["content"]
    assert "Harini" not in out
    assert person_aliases(out)[0] not in (first, second)       # two Harinis in this conversation: not guessed


# ==== 2. legal-instrument titles are not people ===================================================================

TITLES = [
    "Minimum Wages Act, 1948",
    "Maternity Benefit (Amendment) Act, 2017",
    "Contract Labour (Regulation and Abolition) Act, 1970",
    "Employees' Provident Funds and Miscellaneous Provisions Act, 1952",
    "Workmen's Compensation Act",
    "Karnataka Shops and Commercial Establishments Act, 1961",
    "Industrial Employment (Standing Orders) Central Rules, 1946",
    "Code on Social Security, 2020",
    "Industrial Relations Code",
    "Employees' Pension Scheme, 1995",
    "Prevention of Sexual Harassment Policy",
    "Sexual Harassment of Women at Workplace (Prevention, Prohibition and Redressal) Act, 2013",
    "Payment of Wages (Procedure) Rules",
    "Labour Codes (Amendment) Bill",
    "Leave Encashment Regulations 2019",
]
FRAMES = [
    "Compliance with {t} is reviewed by Gaurav Malhotra. Gaurav will confirm.",
    "Please read the sections of {t} before the call with Gaurav Malhotra.",
    "Questions about {t}? Write to Gaurav Malhotra.",
]


@pytest.mark.parametrize("title", TITLES)
@pytest.mark.parametrize("frame", FRAMES)
def test_legal_titles_are_not_people(title, frame):
    text = frame.format(t=title)
    p = protect(text)
    out = p.messages[0]["content"]
    assert title in out, out                                    # the title crosses intact
    assert "Gaurav" not in out and "Malhotra" not in out         # the person in the same text is still protected
    assert len(set(person_aliases(out))) == 1
    assert {e.value for e in p.entities.values() if e.type is T.PERSON} <= {"Gaurav Malhotra", "Gaurav", "Malhotra"}


def test_title_words_are_not_propagated_as_names():
    p = protect("Under the Payment of Gratuity Act, Ritika Sen is eligible; her gratuity and the act's rules apply.")
    out = p.messages[0]["content"]
    assert "Payment of Gratuity Act" in out and "her gratuity and the act's rules" in out


def test_owner_in_front_of_a_title_is_still_a_person():
    from pgw.pipeline import legal_titles
    from pgw.types import Detection
    text = "Arvind Kulkarni Leave Policy exception was approved."
    ner = Detection(0, 15, T.PERSON, 0.75, "D4:ner:test")                # NER: "Arvind Kulkarni"
    spans, kept = legal_titles(text, [ner])
    assert kept == [ner] and [text[s:e] for s, e in spans] == ["Leave Policy"]
    # a NER hit on a title word itself is dropped
    text = "Read the Maternity Benefit Act today."
    spans, kept = legal_titles(text, [Detection(9, 26, T.PERSON, 0.75, "D4:ner:test")])
    assert kept == [] and [text[s:e] for s, e in spans] == ["Maternity Benefit Act"]
    # a singular possessive in front of a title is an owner, not part of the title
    p = protect("Escalation from Kiran Bedarkar: Kiran's Leave Policy question is open.")
    out = p.messages[0]["content"]
    assert "Kiran" not in out and "Leave Policy question" in out
    # a name written after "Code on" stays a name
    p = protect("Notes on the Code on Wages from Kiran Bedarkar; Kiran agrees.")
    out = p.messages[0]["content"]
    assert "Code on Wages" in out and "Kiran" not in out


def test_of_inside_a_proper_phrase_is_not_a_name_cue():
    p = protect("The Head of Talent Acquisition and the Director of Shared Services will attend.")
    assert not person_aliases(p.messages[0]["content"])
    # "of" as a preposition still introduces a person
    p = protect("This is the appraisal of Mohan Krishnamurthy for review.")
    assert "Mohan" not in p.messages[0]["content"]


def test_smoke_audit_examples():
    p = protect("Check the Payment of Gratuity Act and the Employees' State Insurance Act, 1948. The Act applies.")
    assert not person_aliases(p.messages[0]["content"])
    p = protect("The bonus ceiling under the Payment of Bonus Act is Rs 7,000 per month.")
    assert "Rs 7,000" in p.messages[0]["content"]
    p = protect("Her salary of Rs 21,00,000 exceeds the ceiling.")
    assert "21,00,000" not in p.messages[0]["content"]
    p = protect("Email from Priya Raghunathan: Priya and Ms Raghunathan are the same person.")
    assert len(set(person_aliases(p.messages[0]["content"]))) == 1


# ==== 3. statutory constants vs personal pay =====================================================================

STATUTORY = [
    ("The PF wage ceiling is Rs 15,000 per month for mandatory contribution.", "Rs 15,000"),
    ("Under the Payment of Gratuity Act, 1972 the maximum gratuity payable is Rs 20,00,000.", "Rs 20,00,000"),
    ("The ESI threshold for coverage stands at ₹21,000 per month of wages.", "₹21,000"),
    ("As per the Act, the minimum bonus is 8.33% of salary or Rs 100, whichever is higher.", "Rs 100"),
    ("The minimum wage for unskilled workers in Karnataka is Rs 14,500 per month.", "Rs 14,500"),
    ("Rs 1,800 per month is the maximum employer PF contribution on basic pay.", "Rs 1,800"),
    ("Bonus eligibility applies to employees drawing a basic salary not exceeding Rs 21,000 per month.", "Rs 21,000"),
    ("The statutory monthly stipend under the apprenticeship rules is Rs 9,000.", "Rs 9,000"),
    ("The income-tax exemption limit on gratuity is ₹20 lakh.", "₹20 lakh"),
    ("The notified floor wage for the stipend is Rs 178 per day.", "Rs 178"),
    ("As per the Act, the employer's PF contribution is limited to Rs 1,800 per month on wages.", "Rs 1,800"),
    ("Gratuity is capped at Rs 20,00,000 under the gratuity rules.", "Rs 20,00,000"),
]
PERSONAL = [
    ("His CTC of Rs 32,00,000 is well above the ceiling.", "32,00,000"),
    ("Meera's basic salary of Rs 48,000 per month exceeds the PF wage ceiling.", "48,000"),
    ("The employee's current salary is Rs 19,800, under the ESI limit.", "19,800"),
    ("Her bonus is capped at Rs 60,000 this year.", "60,000"),
    ("Expected CTC is at least Rs 14,00,000 for this candidate.", "14,00,000"),
    ("Rohan Kapoor's gross pay of Rs 22,500 crossed the threshold last month.", "22,500"),
    ("Under the Payment of Bonus Act, his bonus works out to Rs 16,800.", "16,800"),
    ("Salary for Rohan Kapoor under the Act is Rs 26,400 per month.", "26,400"),
]


def comp(text: str) -> list[str]:
    return [text[d.start:d.end] for d in recognizers.detect_d2(text) if d.type is T.COMPENSATION]


@pytest.mark.parametrize("text, amount", STATUTORY)
def test_statutory_constants_are_not_compensation(text, amount):
    assert amount in text and comp(text) == []
    assert amount in protect(text).messages[0]["content"]


@pytest.mark.parametrize("text, amount", PERSONAL)
def test_personal_pay_next_to_a_limit_word_stays_protected(text, amount):
    assert any(amount in c for c in comp(text))
    assert amount not in protect(text).messages[0]["content"]


def test_two_amounts_in_one_clause_are_judged_separately():
    text = "Her basic of Rs 48,000 per month exceeds the PF wage ceiling of Rs 15,000."
    assert comp(text) == ["Rs 48,000"]


# ==== end to end through the gateway =============================================================================

KEY = "pilot-key"


def gateway(tmp, contract_path=None):
    mock = MockLLMUpstream("echo")
    task = str(contract_path or ROOT / "contracts/hr_email_draft.yaml")
    cfg = production(tmp, [{"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY), "restore_output": True,
                            "models": ["mock-1"], "purposes": ["hr_email_draft"],
                            "tasks": {"hr_email_draft": task}}])
    s = Settings(**cfg, ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s)), mock


def send(client, messages, conv=None, recipient="Kavya Nair"):
    body = {"model": "mock-1", "pgw_subjects": {"recipient": {"name": recipient}}, "messages": messages}
    hdr = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "hr_email_draft", **attest("hr_email_draft", body)}
    if conv:
        hdr["X-PGW-Conversation-Id"] = conv
    r = client.post("/v1/chat/completions", json=body, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()["choices"][0]["message"]["content"]


def last_user(mock) -> str:
    sent = json.loads(mock.received[-1])
    return [m for m in sent["messages"] if m["role"] == "user"][-1]["content"]


def test_e2e_partial_names_one_alias_and_coherent_restore(tmp_path):
    client, mock = gateway(tmp_path)
    text = ("Draft an email to Kavya Nair about Siddharth Raghavan. Siddharth asked whether "
            "Mr Raghavan's transfer under the Industrial Employment (Standing Orders) Act is approved.")
    out = send(client, [{"role": "user", "content": text}])
    wire = last_user(mock)
    for w in ("Siddharth", "Raghavan", "Kavya", "Nair"):
        assert w not in wire
    al = person_aliases(wire)
    assert len(al) == 4 and al[1] == al[2] == al[3] and al[0] != al[1]
    assert "Industrial Employment (Standing Orders) Act" in wire
    assert out == ("Draft an email to Kavya Nair about Siddharth Raghavan. Siddharth Raghavan asked whether "
                   "Mr Siddharth Raghavan's transfer under the Industrial Employment (Standing Orders) Act is approved.")


def test_e2e_two_turns_of_one_conversation(tmp_path):
    client, mock = gateway(tmp_path)
    t1 = "Write to Kavya Nair about Pranav Deshmukh and his notice period."
    out1 = send(client, [{"role": "user", "content": t1}], conv="c-77")
    full = person_aliases(last_user(mock))[1]
    # turn 2 with history
    t2 = "Also mention that Pranav may serve a shorter notice."
    out2 = send(client, [{"role": "user", "content": t1}, {"role": "assistant", "content": out1},
                         {"role": "user", "content": t2}], conv="c-77")
    wire2 = mock.received[-1].decode()
    assert "Pranav" not in wire2 and "Deshmukh" not in wire2
    assert person_aliases(last_user(mock)) == [full]
    assert out2 == "Also mention that Pranav Deshmukh may serve a shorter notice."
    # turn 3 without history: initial + surname still maps to the same person
    out3 = send(client, [{"role": "user", "content": "Copy P. Deshmukh on the reply."}], conv="c-77")
    assert person_aliases(last_user(mock)) == [full] and "Deshmukh" not in last_user(mock)
    # only hashes of earlier turns are kept, so a turn that writes only a part restores that part
    assert out3 == "Copy P. Deshmukh on the reply."


def test_e2e_ambiguous_given_name(tmp_path):
    client, mock = gateway(tmp_path)
    text = "Tell Kavya Nair about Arjun Rao and Arjun Mehta: both applied. Arjun withdrew later."
    out = send(client, [{"role": "user", "content": text}])
    al = person_aliases(last_user(mock))
    assert len(set(al)) == 4                     # Kavya, two Arjuns, and a lone "Arjun" that is neither
    assert "Arjun" not in last_user(mock)
    assert out == text


def test_e2e_statutory_constant_crosses_salary_does_not(tmp_path):
    client, mock = gateway(tmp_path)
    text = ("Draft an email to Kavya Nair: the bonus wage ceiling under the Payment of Bonus Act, 1965 is Rs 7,000 "
            "per month, and her salary of Rs 21,00,000 exceeds the ceiling.")
    out = send(client, [{"role": "user", "content": text}])
    wire = mock.received[-1].decode()
    assert "Rs 7,000" in wire and "21,00,000" not in wire
    assert "Payment of Bonus Act, 1965" in wire and "Kavya" not in wire
    assert out == text


def _contract_with_constants(tmp_path, constants) -> str:
    raw = yaml.safe_load((ROOT / "contracts/hr_email_draft.yaml").read_text())
    raw["public_constants"] = constants
    p = tmp_path / "hr_email_draft.yaml"
    p.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return str(p)


def test_e2e_contract_public_constants(tmp_path):
    text = "Draft an email to Kavya Nair: each employee's meal allowance is Rs 2,200 per month, hers was Rs 2,200,000."
    client, mock = gateway(tmp_path / "a")
    send(client, [{"role": "user", "content": text}])
    assert "Rs 2,200 " not in mock.received[-1].decode()          # without the constant: treated as pay
    client, mock = gateway(tmp_path / "b", _contract_with_constants(tmp_path, ["Rs 2,200"]))
    out = send(client, [{"role": "user", "content": text}])
    wire = mock.received[-1].decode()
    assert "Rs 2,200 per month" in wire and "2,200,000" not in wire   # exact, whole-number match only
    assert out == text


@pytest.mark.parametrize("bad", [["Priya Raghunathan"], ["ABCDE1234F"], ["98450 12345"], ["123456789012"],
                                 ["x" * 41], [""], ["Rs 1"] * 21])
def test_public_constants_must_be_amounts(tmp_path, bad):
    with pytest.raises(ContractError):
        load_contract(_contract_with_constants(tmp_path, bad))


def test_public_constants_parse(tmp_path):
    c = load_contract(_contract_with_constants(tmp_path, ["Rs 2,200", "₹1,500 per day", "8.33%"]))
    assert c.public_constants == ("Rs 2,200", "₹1,500 per day", "8.33%")
