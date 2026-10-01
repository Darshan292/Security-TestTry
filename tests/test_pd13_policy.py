"""PD-13 L3 semantics and L4 release policy, end to end through the protector (no NER; tiny roster)."""
from __future__ import annotations

import datetime as dt
import inspect

import pytest

from pgw import ablation as abl
from pgw.detect.edm import RosterIndex
from pgw.detect.recognizers import verhoeff_check_digit
from pgw.egress import check as egress_check
from pgw.pd13 import PD13Protector
from pgw.pd13.dictionary import Dictionary
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack
from pgw.types import Context, EntityType as T
from pathlib import Path

CTX = Context("t", "t", "eval_tokenize_all", conversation_id=None, restore_output=True)
POLICY = PolicyPack.load(Path(__file__).resolve().parents[1] / "policies/hris-default.yaml")
TODAY = dt.date(2026, 10, 1)


def valid_aadhaar(payload="23456789012") -> str:
    return payload + verhoeff_check_digit(payload)


def make(l3: bool, extra=None, roster=True) -> PD13Protector:
    keys = Keys(bytes(32))
    r = None
    if roster:
        r = RosterIndex(keys.derive("t", "edm"))
        r.add_employee({"employee_id": "KAF10001", "full_name": "Asha Rao", "first_name": "Asha", "last_name": "Rao",
                        "email": "asha.rao@acme.example", "phone": "+91 98765 43210"})
    d = None
    if extra is not None:
        d = Dictionary([{"key": k, "concept": c, "provenance": "standard:test schema.org Person"} for k, c in extra])
    return PD13Protector(POLICY, keys, r, ner=None, l3=l3, dictionary=d, today=TODAY)


def spans(prot, text):
    return [(text[d.start:d.end], d.type.value, d.source.split("@")[0]) for d in prot.detect_text(text)]


def masked(prot, text, value):
    return any(value in s or s in value for s, *_ in spans(prot, text) if len(s) >= 2)


# --- rule 1: D2 validator hits are masked exactly as today --------------------------------------

@pytest.mark.parametrize("l3", [True, False])
def test_d2_hits_are_unchanged_by_l3_and_l4(l3):
    base = Protector(POLICY, Keys(bytes(32)), None)
    t = "Contact asha.rao@acme.example, PAN ABCPE1234F, aadhaar " + valid_aadhaar()[:4] + " " + valid_aadhaar()[4:8] + " " + valid_aadhaar()[8:]
    with abl.use(abl.get().without("D1")):
        want = {(d.start, d.end, d.type) for d in base.detect_text(t) if d.source.startswith("D2:") and d.type is not T.PERSON}
    got = {(d.start, d.end, d.type) for d in make(l3).detect_text(t)}
    assert want <= got


def test_l3_never_releases_a_validator_hit():
    a = valid_aadhaar()
    pan = "ABCPE1234F"
    t = f'{{"name": "Asha Rao", "reference": "{pan}", "ref2": "{a[:4]} {a[4:8]} {a[8:]}", "mail": "asha.rao@acme.example"}}'
    p = make(True, extra=[("reference", "RELEASE_ID"), ("ref2", "RELEASE_ID"), ("mail", "RELEASE_TEXT")])
    got = spans(p, t)
    assert any(v == pan and ty == "PAN" for v, ty, _ in got)
    assert any(ty == "AADHAAR" for _, ty, _ in got)
    assert any(ty == "EMAIL" for _, ty, _ in got)


def test_l3_never_retypes_a_validator_hit():
    pan = "ABCPE1234F"
    t = f'{{"name": "Asha Rao", "salary": "{pan}"}}'
    got = spans(make(True, extra=[("salary", "COMPENSATION")]), t)
    assert (pan, "PAN", "D2:pan") in got
    assert not any(v == pan and ty == "COMPENSATION" for v, ty, _ in got)


# --- rule 3: HIGH_ENTROPY everywhere unless L3 releases it ----------------------------------------

TOKEN = "f3A9x8Lq2ZpR7vT1mWk4"


@pytest.mark.parametrize("l3", [True, False])
def test_high_entropy_is_masked_in_a_document_without_any_person(l3):
    t = f"build log\nartifact: {TOKEN}\nstatus ok\n"
    assert any(v == TOKEN and ty == "SECRET" for v, ty, _ in spans(make(l3), t))


def test_high_entropy_is_released_only_by_l3_with_a_release_concept():
    t = f'{{"trace_id": "{TOKEN}"}}'
    assert not any(v == TOKEN for v, *_ in spans(make(True, extra=[("trace_id", "RELEASE_ID")]), t))
    assert any(v == TOKEN for v, *_ in spans(make(False, extra=[("trace_id", "RELEASE_ID")]), t))   # arm C: no release
    assert any(v == TOKEN for v, *_ in spans(make(True, extra=[("other", "RELEASE_ID")]), t))      # key not in dictionary


def test_release_concept_must_be_compatible_with_the_shape():
    t = f'{{"created": "{TOKEN}"}}'                                              # RELEASE_DATE cannot release an entropy token
    assert any(v == TOKEN for v, *_ in spans(make(True, extra=[("created", "RELEASE_DATE")]), t))


# --- rule 2: shapes in person-bearing scopes ----------------------------------------------------

def test_date_is_masked_only_in_a_person_bearing_document():
    assert not masked(make(False), "Report generated on 05/03/2026 by the system.", "05/03/2026")
    assert masked(make(False), "Dear Asha Rao, the date 05/03/1990 is on file.", "05/03/1990")


def test_person_bearing_is_decided_per_record_in_structured_data():
    t = ('{"header": {"printed": "05/03/2026"}, "rows": [{"name": "Asha Rao", "joined": "07/08/2019"},'
         ' {"name": "Zed Zed", "joined": "09/10/2018"}]}')
    p = make(False)
    assert not masked(p, t, "05/03/2026")                  # header record has no person anchor
    assert masked(p, t, "07/08/2019")
    assert masked(p, t, "09/10/2018")                       # the whole rows table is person-bearing


def test_employee_id_and_personal_mobile_are_anchors_but_role_mailbox_and_landline_are_not():
    p = make(False)
    assert masked(p, "Employee ID: KAF10001 dated 05/03/1990", "05/03/1990")
    assert masked(p, "call +91 98765 43211 on 05/03/1990", "05/03/1990")
    assert not masked(p, "write to payroll@acme.example on 05/03/1990", "05/03/1990")
    assert not masked(p, "helpline 033-4061 8820 on 05/03/1990", "05/03/1990")


def test_amount_follows_the_same_rule():
    got = spans(make(False), "Dear Asha Rao, the figure 52,620.50 is final")
    assert any(v == "52,620.50" and ty == "COMPENSATION" and src.startswith("L2") for v, ty, src in got)
    assert not any(v == "52,620.50" for v, *_ in spans(make(False), "Invoice figure 52,620.50 is final"))


def test_long_digits_follow_the_same_rule():
    got = spans(make(False), "Dear Asha Rao, tracking 98765432101234 filed")
    assert any(v == "98765432101234" and ty == "BANK_ACCOUNT" and src.startswith("L2") for v, ty, src in got)
    assert not any(v == "98765432101234" for v, *_ in spans(make(False), "Tracking 98765432101234 filed"))


# --- rule 5: no release in arm C; L3 typing and release in arm B ----------------------------------

def test_arm_c_releases_nothing_and_arm_b_releases_by_key():
    t = '{"name": "Asha Rao", "created": "05/03/2026", "dob": "05/03/1990"}'
    b = make(True, extra=[("created", "RELEASE_DATE"), ("dob", "DATE_OF_BIRTH")])
    c = make(False, extra=[("created", "RELEASE_DATE"), ("dob", "DATE_OF_BIRTH")])
    assert not masked(b, t, "05/03/2026") and masked(b, t, "05/03/1990")
    assert masked(c, t, "05/03/2026") and masked(c, t, "05/03/1990")


def test_l3_types_an_untyped_number_under_a_salary_key_and_c_does_not():
    t = '{"emp": "Asha Rao", "ANSAL": 450}'
    assert any(v == "450" and ty == "COMPENSATION" for v, ty, _ in spans(make(True), t))
    assert not any(v == "450" for v, *_ in spans(make(False), t))


def test_l3_name_column_types_names_the_ner_missed():
    t = "Name,Score\nKiran Kumar,5\nMeera Iyer,6\nTara Singh,7\n"
    got = spans(make(True), t)
    assert {"Kiran Kumar", "Meera Iyer", "Tara Singh"} <= {v for v, ty, _ in got if ty == "PERSON"}
    assert not any(ty == "PERSON" for _, ty, _ in spans(make(False), t))


def test_l3_column_statistics_type_a_verhoeff_column_as_aadhaar():
    a1, a2, a3 = valid_aadhaar("34567890123"), valid_aadhaar("45678901234"), valid_aadhaar("56789012345")
    t = f"Member,Doc\nAsha Rao,{a1}\nRavi Nair,{a2}\nMeera Iyer,{a3}\n"
    got = spans(make(True), t)
    assert {a1, a2, a3} <= {v for v, ty, _ in got if ty == "AADHAAR"}


def test_date_column_is_dob_like_or_release_like_by_age_window():
    dobs = "Name,When\nAsha Rao,12/05/1990\nRavi Nair,01/11/1985\nMeera Iyer,03/03/1978\n"
    recent = "Name,When\nAsha Rao,12/05/2025\nRavi Nair,01/11/2025\nMeera Iyer,03/03/2026\n"
    b = make(True)
    assert any(v == "12/05/1990" and ty == "DATE_OF_BIRTH" and src.startswith("L3") for v, ty, src in spans(b, dobs))
    got = spans(b, recent)
    assert not any(v == "12/05/2025" for v, *_ in got)                           # released: not a plausible birth date
    assert any(v == "12/05/2025" for v, *_ in spans(make(False), recent))        # arm C masks it


def test_two_digit_years_resolve_against_the_latest_date():
    t = "EMPCODE,NAME,DOB\nKAF10001,Asha Rao,21-MAR-93\nKAF10002,Ravi Nair,15-MAY-94\nKAF10003,Meera Iyer,19-APR-74\n"
    got = spans(make(True), t)
    assert {"21-MAR-93", "15-MAY-94", "19-APR-74"} <= {v for v, ty, _ in got if ty == "DATE_OF_BIRTH"}


# --- plumbing: signatures, D1 off, egress invariant, aliasing, propagation -------------------------

def test_analyze_and_protect_are_the_inherited_methods():
    assert PD13Protector.analyze is Protector.analyze and PD13Protector.protect is Protector.protect
    assert list(inspect.signature(PD13Protector.analyze).parameters) == ["self", "messages", "ctx"]
    assert list(inspect.signature(PD13Protector.protect).parameters) == ["self", "messages", "ctx"]


def test_d1_is_off_inside_and_restored_after():
    p = make(True)
    assert abl.get().D1 is True
    t = '{"employee_name": "Zed Zed", "salary": "52000"}'
    assert not any(src.startswith("D1:") for *_, src in spans(p, t))
    p.analyze([{"role": "user", "content": t}], CTX)
    assert abl.get().D1 is True
    assert abl.get() == abl.DEFAULT


@pytest.mark.parametrize("l3", [True, False])
def test_egress_invariant_and_aliasing_and_propagation(l3):
    t = ("Name,Joined,Email\nAsha Rao,05/03/2019,asha.rao@acme.example\nRavi Nair,06/04/2018,ravi@acme.example\n"
         "Please tell Asha Rao that Asha will hear on 05/03/2019.")
    p = make(l3)
    res = p.protect([{"role": "user", "content": t}], CTX)
    out = res.messages[0]["content"]
    egress_check(out.encode(), res.sensitive_values)                       # raises EgressViolation on a leaked value
    assert "Asha Rao" not in out and "asha.rao@acme.example" not in out
    assert out.count("[EMAIL_") >= 2
    # the same entity gets the same alias everywhere (aliasing), and a partial mention is propagated (D5)
    import re
    aliases = re.findall(r"\[PERSON_[0-9A-Z]+\]", out)
    assert len(aliases) >= 3 and aliases.count(aliases[0]) >= 2
    assert "Asha " not in out


def test_empty_and_plain_text_are_handled():
    p = make(True)
    assert p.detect_text("") == []
    assert p.detect_text("Hello there, nothing sensitive here.") == []
