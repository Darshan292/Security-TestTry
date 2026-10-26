"""Business-quality evaluator (eval/business): deterministic semantics, category separation, judge isolation.

Fixtures are restored answers captured from real Groq runs on synthetic data (pre-fix and current contracts).
"""
import json
import re
from pathlib import Path

import httpx

from eval.business import checks as C
from eval.business.judge import judge
from eval.business.run import evaluate, load_captures
from eval.business.specs import SPECS

FIX = Path(__file__).parent / "fixtures/business"
SPEC = {s.case: s for s in SPECS}


def run_fixture(name, tmp_path):
    d = tmp_path / name
    d.mkdir()
    (d / "human_review.md").write_text((FIX / name).read_text())
    caps = load_captures(d)
    return {s.case: evaluate(s, caps.get(s.capture)) for s in SPECS}


def failed(rep):
    return {r.check for r in rep.results if r.status == "FAIL"}


# --- the evaluator finds the failures the human review found --------------------------------------------------

def test_prefix_answers_fail_for_the_reviewed_reasons(tmp_path):
    reps = run_fixture("review_prefix.md", tmp_path)
    a2 = failed(reps["A2"])
    assert {"prohibited:manual_settlement_change", "unsupported:not_posted_cause", "unsupported:numbers",
            "next_step:calculation_logs", "format:word_limit"} <= a2
    assert "unsupported:sync_delay" not in a2        # listed under a hedged heading: a hypothesis, not a claim
    a3 = failed(reps["A3"])
    assert {"must_not_contain:recipient_email_as_contact", "must_contain:hr_contact_email",
            "must_not_contain:template_filler"} <= a3
    assert "fact:addressed_by_name" in failed(reps["B2"])
    assert reps["A3"].category("PRIVACY") == "FAIL"   # raw [UPI_ID] marker in a deliverable


def test_current_answers_findings(tmp_path):
    reps = run_fixture("review_current.md", tmp_path)
    assert reps["A3"].overall != "FAIL" and reps["B2"].overall != "FAIL"
    assert "fact:requester" in failed(reps["M1"])        # "Raised" (NER false positive) shown as the requester
    assert {"next_step:calculation_logs", "next_step:settlement_exceptions"} <= failed(reps["A2"])
    # review sheets carry no outbound bytes: privacy cannot be judged, so nothing can pass overall
    assert all(r.overall in ("FAIL", "NOT_EVALUABLE") for r in reps.values())


# --- deterministic semantics ------------------------------------------------------------------------------------

HYP = re.compile("possible causes", re.I)
AADHAAR = {"aadhaar_verification_failed": SPEC["A2"].claims["aadhaar_verification_failed"]}


def test_hedged_or_hypothesis_section_claims_pass_asserted_claims_fail():
    hedged = "**2) Possible causes to verify**\n- Aadhaar verification may have failed."
    assert C.check_claims(hedged, AADHAAR, "case text", HYP)[0].status == "PASS"
    under_heading = "**2) Possible causes to verify**\n- Aadhaar not verified in the system."
    assert C.check_claims(under_heading, AADHAAR, "case text", HYP)[0].status == "PASS"
    asserted = "**1) Facts**\n- The Aadhaar was not verified, so the F&F is blocked."
    r = C.check_claims(asserted, AADHAAR, "case text", HYP)[0]
    assert r.status == "FAIL" and "not verified" in r.evidence


def test_claim_allowed_when_the_input_has_evidence():
    src = "Aadhaar verification failed for the F&F form on 2 Oct."
    asserted = "The Aadhaar was not verified."
    assert C.check_claims(asserted, AADHAAR, src, HYP, SPEC["A2"].claim_evidence)[0].status == "PASS"


def test_manual_change_needs_authorisation_and_descriptions_are_not_advice():
    rule = {"m": SPEC["A2"].prohibited["manual_settlement_change"]}
    assert C.check_prohibited("Create the encashment transaction manually.", rule)[0].status == "FAIL"
    assert C.check_prohibited("Once the discrepancy is investigated and approved by the payroll head, "
                              "post the encashment entry manually.", rule)[0].status == "PASS"
    assert C.check_prohibited("The HRIS may require a manual entry step.", rule)[0].status == "PASS"
    assert C.check_prohibited("You may manually adjust the settlement.", rule)[0].status == "FAIL"


def test_numbers_and_codes_must_come_from_the_input():
    assert C.check_numbers("Sync takes 24-48 hours; 12 days balance.", "12 days of leave").status == "FAIL"
    assert C.check_numbers("1. Check approval\n2. Check the 12 days", "12 days").status == "PASS"
    assert C.check_codes("Use code LEAVE_ENCASH under LV-04.", "Policy LV-04").detail.endswith("LEAVE_ENCASH")


def test_sections_order_format_and_truncation():
    sec = SPEC["A2"].sections
    ok = "**1) Facts**\nx.\n**2) Possible causes to verify (hypotheses)**\ny.\n**3) Next steps**\nz."
    assert all(r.status == "PASS" for r in C.check_sections(ok, sec))
    swapped = "**3) Next steps**\nz.\n**1) Facts**\nx.\n**2) Possible causes**\ny."
    assert any(r.status == "FAIL" for r in C.check_sections(swapped, sec))
    assert C.check_format("Regards,\nHR Operations, Kestrel Dynamics", None, None)[0].status == "PASS"
    cut = C.check_format("The encashment will be included once the payroll team confirms the", None, None)
    assert cut[0].status == "FAIL"
    t = C.check_format("| a | b |\n|---|---|\n| 1 | 2 |\n\n| x |\n|---|\n| y |", None, None)
    assert t[0].status == "PASS"


def test_privacy_and_business_are_separate_and_both_required():
    rep = C.Report("x", [C.Result("PRIVACY", "p", "FAIL"), C.Result("BUSINESS", "b", "PASS")])
    assert rep.category("BUSINESS") == "PASS" and rep.overall == "FAIL"
    rep = C.Report("x", [C.Result("PRIVACY", "p", "PASS"), C.Result("BUSINESS", "b", "FAIL")])
    assert rep.overall == "FAIL"
    rep = C.Report("x", [C.Result("PRIVACY", "p", "NOT_EVALUABLE"), C.Result("BUSINESS", "b", "PASS")])
    assert rep.overall == "NOT_EVALUABLE"


def test_privacy_checks_on_outbound_and_answer():
    out = json.dumps({"messages": [{"role": "user", "content": "[PERSON_1ABCDE] asked"}]})
    rs = {r.check: r.status for r in C.check_privacy(out, "Imran asked; ref [PERSON_2ABCDE]", ["Imran Ansari"],
                                                       ["ZXWPA3889P"])}
    assert rs["outbound:raw_values_absent"] == "PASS" and rs["answer:no_unrestored_alias"] == "FAIL"
    leak = json.dumps({"messages": [{"role": "user", "content": "Imran Ansari asked"}]})
    assert C.check_privacy(leak, "", ["Imran Ansari"], [])[0].status == "FAIL"


# --- the LLM judge is secondary and sees only aliased text ------------------------------------------------------

def test_judge_is_secondary_and_failures_are_reported_not_fatal(monkeypatch):
    sent = {}

    def handler(req):
        sent["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            {"items": [{"id": "r1", "verdict": "pass", "reason": "ok"}]})}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    v = judge("Task for [PERSON_1ABCDE]", "Answer about [PERSON_1ABCDE]", [{"id": "r1", "question": "q?"}],
              key="k", client=client)
    assert v == {"r1": {"verdict": "pass", "reason": "ok"}}
    assert "Imran" not in json.dumps(sent["body"])          # only aliased text is sent to the judge
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert judge("t", "a", [{"id": "r1", "question": "q"}], key="k", client=down)["r1"]["verdict"] == "unavailable"
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert judge("t", "a", [{"id": "r1", "question": "q"}], key=None) == {}      # no key: no judge, no verdict


# --- capture handling: privacy never passes on a missing, unsupported or partial capture ------------------------

OK_EX = json.dumps({"messages": [{"role": "user", "content": "[PERSON_1ABCDE] asked"}]})
LEAK_EX = json.dumps({"messages": [{"role": "user", "content": "Imran Ansari asked again"}]})


def outbound_status(outbound, answer="Imran asked."):
    return {r.check: r.status for r in C.check_privacy(outbound, answer, ["Imran Ansari"], [])}


def test_privacy_is_not_evaluable_unless_every_exchange_is_inspected():
    assert outbound_status(None)["outbound:raw_values_absent"] == "NOT_EVALUABLE"
    assert outbound_status([])["outbound:raw_values_absent"] == "NOT_EVALUABLE"
    assert outbound_status("")["outbound:raw_values_absent"] == "NOT_EVALUABLE"
    assert outbound_status("Imran-free plain text, not JSON")["outbound:raw_values_absent"] == "NOT_EVALUABLE"
    assert outbound_status(json.dumps({"prompt": "x"}))["outbound:raw_values_absent"] == "NOT_EVALUABLE"
    assert outbound_status([OK_EX, None])["outbound:raw_values_absent"] == "NOT_EVALUABLE"
    assert outbound_status([OK_EX, "garbled"])["outbound:raw_values_absent"] == "NOT_EVALUABLE"
    assert outbound_status([OK_EX, OK_EX])["outbound:raw_values_absent"] == "PASS"


def test_a_leak_in_a_later_exchange_fails():
    assert outbound_status([OK_EX, LEAK_EX])["outbound:raw_values_absent"] == "FAIL"


def test_empty_answer_is_not_evaluable_not_a_privacy_pass():
    rs = outbound_status([OK_EX], answer="")
    assert rs["answer:captured"] == "NOT_EVALUABLE" and "answer:no_unrestored_alias" not in rs


def test_load_captures_keeps_every_outbound_exchange(tmp_path):
    rec = {"scenario": "A1 ticket summary", "restored_response": "ok", "outbound_http": [OK_EX, LEAK_EX],
           "inbound_http": [None, None]}
    (tmp_path / "A1_ticket_summary.json").write_text(json.dumps(rec))
    cap = load_captures(tmp_path)["A1 ticket summary"]
    assert cap["outbound"] == [OK_EX, LEAK_EX]
    rep = evaluate(SPEC["A1"], cap)
    priv = {r.check: r.status for r in rep.results if r.category == "PRIVACY"}
    assert priv["outbound:raw_values_absent"] == "FAIL"        # the leak is only in the second exchange


def test_response_without_a_captured_request_is_not_evaluable(tmp_path):
    rec = {"scenario": "A1 ticket summary", "restored_response": "ok", "outbound_http": [OK_EX],
           "inbound_http": [None, None]}
    (tmp_path / "A1_ticket_summary.json").write_text(json.dumps(rec))
    cap = load_captures(tmp_path)["A1 ticket summary"]
    assert outbound_status(cap["outbound"])["outbound:raw_values_absent"] == "NOT_EVALUABLE"
