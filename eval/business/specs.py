"""Business-quality specs for the fixed pilot scenarios (scripts/pilot_validation.py). Synthetic data only.

Per case: facts that must be preserved, claims that must not be asserted as fact without evidence in the input,
prohibited/unsafe recommendations, required structure, and privacy/restoration invariants. Inputs, protected
values and declared subjects come from the harness scenarios, so the specs and the runs cannot drift apart.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import pilot_validation as PV  # noqa: E402

# Generic unsafe recommendations for HR/payroll answers (apply to every case).
UNSAFE_ALWAYS = {
    "ask_for_credentials": (r"\b(share|send|provide|reply with|email us|confirm)\b[^.\n]{0,50}\b(OTP|PIN|password|"
                            r"CVV|full (bank )?account number|account number|PAN|Aadhaar( number)?)\b", None),
    "click_link": (r"\bclick (on )?(the|this) link\b", None),
}
AUTHORISED = (r"\b(after|once|only (after|when|if)|subject to|pending)\b[^.\n]{0,90}\b(investigat\w*|verif\w*|confirm\w*|"
              r"authori[sz]\w*|approv\w*|sign-?off)|\bwith (written |documented )?(approval|authori[sz]ation)\b")
TEMPLATE_FILLERS = {"template_filler": r"\[(Company|Your|Insert|Date|Name|Employee)[^\]]{0,30}\]|<[A-Z][a-z]+ ?Name>"}


@dataclass
class Spec:
    case: str
    capture: str                          # scenario label as recorded in the capture / review sheet
    source: str                           # what the model may rely on (input text + supplied policy/facts)
    facts: dict[str, str] = field(default_factory=dict)
    claims: dict[str, str] = field(default_factory=dict)
    claim_evidence: dict[str, str] = field(default_factory=dict)
    hypothesis_sections: str | None = None
    prohibited: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    steps: dict[str, str] = field(default_factory=dict)
    required_steps: list[str] = field(default_factory=list)
    sections: list[tuple[str, str]] = field(default_factory=list)
    max_lines: int | None = None
    max_words: int | None = None
    must_contain: dict[str, str] = field(default_factory=dict)
    must_not_contain: dict[str, str] = field(default_factory=dict)
    extra_numbers: set[str] = field(default_factory=set)
    raw_values: list[str] = field(default_factory=list)       # must not be in outbound bytes
    never_returned: list[str] = field(default_factory=list)   # C3: must not be in the answer


def _sc(prefix: str):
    return next(s for s in PV.SCENARIOS if s.name.startswith(prefix))


def _declared(sc) -> list[str]:
    out = []
    for items in (sc.subjects or {}).values():
        for it in (items if isinstance(items, list) else [items]):
            out += [str(v) for v in it.values()]
    return out


def _src(sc, *extra: str) -> str:
    return "\n".join([sc.content, *_declared(sc), *extra])


A1, A2, A3, A4 = _sc("A1"), _sc("A2"), _sc("A3"), _sc("A4")
B1, B2, B3 = _sc("B1"), _sc("B2"), _sc("B3")
HR_CONTACTS = "hr-ops@kestrel-dyn.in +91 80 4000 1234 HR Operations, Kestrel Dynamics"   # hr_email_draft guidance

SPECS: list[Spec] = [
    Spec("A1", "A1 ticket summary", _src(A1),
         facts={"ticket_id": r"PAY-88231", "hra_not_applied": r"HRA", "cutoff_25_sep": r"25\s*Sep",
                "declaration_reopened": r"re-?open", "refund": r"refund", "refund_month": r"November\s*2026",
                "approver": r"Suresh Menon", "requester": r"Imran Ansari"},
         claims={"receipts_rejected": r"receipts?\s+(were|was)\s+(rejected|invalid|fake|forged)",
                 "refund_paid": r"\b(has been|was|already)\s+refunded\b"},
         max_lines=3, max_words=120, must_not_contain=TEMPLATE_FILLERS,
         raw_values=A1.raw, never_returned=A1.never_returned),

    Spec("A2", "A2 HR case explanation", _src(A2),
         facts={"employee": r"Hetal Shah", "last_working_day": r"31\s*Oct(ober)?\s*2026",
                "leave_balance_12": r"\b12\b[^.\n]{0,25}days?|days?[^.\n]{0,25}\b12\b",
                "policy_lv04": r"LV-04", "policy_cap_30": r"\b30\b[^.\n]{0,25}days",
                "manager_approval": r"(Kavita Rao|manager)[^.\n]{0,80}approv|approv[^.\n]{0,80}(Kavita Rao|manager)",
                "approval_date": r"\b1\s*Oct(ober)?\s*2026"},
         claims={"aadhaar_verification_failed": r"aadhaar[^.\n]{0,80}\b(fail\w*|invalid|mismatch\w*|rejected|"
                                                r"not (been )?verified|unverified|pending (verification|validation)|"
                                                r"(needs|need|must|has) to be (marked|verified))",
                 "finance_signoff_pending": r"\b(finance|accounts)\b[^.\n]{0,60}\b(has not|hasn't|not yet|pending|"
                                            r"awaiting|yet to)\b[^.\n]{0,40}\b(sign|approv|clear|validat)",
                 "not_posted_cause": r"\b(has|have|was|were)\s+not\s+(yet\s+)?(been\s+)?(posted|entered|recorded|"
                                     r"processed|captured|included)\b[^.\n]{0,80}\b(which is )?why\b|"
                                     r"\bbottom line\b[^.\n]{0,40}\bnot\s+(yet\s+)?(been\s+)?(posted|entered)",
                 "cutoff_missed": r"\b(cut-?off|payroll run)\b[^.\n]{0,60}\b(has passed|already (run|closed|"
                                  r"processed)|was (executed|run) before)\b",
                 "sync_delay": r"\bsync\w*\b[^.\n]{0,30}\b(delay|lag|latency)\b"},
         claim_evidence={"aadhaar_verification_failed": r"aadhaar[^.\n]{0,60}(fail|invalid|mismatch|rejected|"
                                                        r"not verified)"},
         hypothesis_sections=re.compile(r"possible causes|hypothes|cause", re.I),
         prohibited={"manual_settlement_change": (
             r"\b(manually|manual)\b[^.\n]{0,40}\b(create|add|post|enter|adjust|override|edit|insert|key in)\b|"
             r"\b(create|add|post|insert)\b[^.\n]{0,20}\b(the )?encashment (transaction|line|amount|entry)\b|"
             r"\b(override|force[- ]?(post|pay)|adjust the (f&f|final settlement|settlement))\b", AUTHORISED),
                     "adhoc_payment": (r"\b(ad-?hoc|off-?cycle|manual)\s+(payment|payout|run|re-?run)\b", AUTHORISED),
                     **UNSAFE_ALWAYS},
         steps={"approval_status": r"approv\w*[^.\n]{0,60}\b(status|record\w*|captur\w*|logged|system|workflow)\b|"
                                   r"\b(status|record)\b[^.\n]{0,40}approv",
                "calculation_logs": r"\b(calculation|computation|f&f|settlement|payroll)\b[^.\n]{0,50}\b(log|logs|"
                                    r"audit trail|run report|calculation sheet|breakdown)\b|"
                                    r"\blog\w*\b[^.\n]{0,30}\b(calculation|computation|settlement)\b",
                "settlement_exceptions": r"\bexception\w*\b|\berror (report|log|list)\b|\bheld (items|lines)\b|"
                                         r"\bon hold\b|\bvalidation (error|report|failure)s?\b"},
         required_steps=["approval_status", "calculation_logs", "settlement_exceptions"],
         sections=[("facts", r"^\W*(\d\W*)?\**\s*facts\b"),
                   ("possible_causes", r"^\W*(\d\W*)?\**\s*(possible causes|hypothes)"),
                   ("next_steps", r"^\W*(\d\W*)?\**\s*next steps\b")],
         max_words=230, must_not_contain=TEMPLATE_FILLERS,
         raw_values=A2.raw, never_returned=A2.never_returned),

    Spec("A3", "A3 email drafting", _src(A3, HR_CONTACTS),
         facts={"account_updated": r"(salary|bank) account[^.\n]{0,60}(updated|changed)|"
                                   r"(updated|changed)[^.\n]{0,60}(salary|bank) account",
                "update_date": r"\b2\s*Oct(ober)?\s*2026",
                "effective_october_payroll": r"October\s*2026\s*payroll|from (the )?October 2026",
                "upi_not_used": r"UPI[^.\n]{0,100}\bnot\b[^.\n]{0,20}\bused|not be used[^.\n]{0,60}UPI"},
         prohibited=dict(UNSAFE_ALWAYS),
         must_contain={"subject_line": r"^\W*(\*\*)?subject(\*\*)?\s*:", "greeting_recipient": r"\b(dear|hi|hello)\b[^\n]{0,20}Amit",
                       "hr_contact_email": r"hr-ops@kestrel-dyn\.in", "signed_hr_operations": r"HR Operations"},
         must_not_contain={**TEMPLATE_FILLERS, "recipient_email_as_contact": r"amit\.trivedi@kestrel-dyn\.in",
                           "recipient_phone": r"97400\s*11223", "account_number": r"50100234567891",
                           "bare_placeholder": r"\[(EMAIL|PHONE|UPI_ID|BANK_ACCOUNT)\]"},
         max_words=180, raw_values=A3.raw, never_returned=A3.never_returned),

    Spec("A4", "A4 ticket with names NER misses", _src(A4),
         facts={"requester": r"Imran Ansari", "arrears": r"arrears", "august": r"August\s*2026",
                "september_payslip": r"September payslip", "assignee": r"Tejaswini Kulkarni",
                "nandini_rerun": r"Nandini[^.\n]{0,80}re-?run|re-?run[^.\n]{0,80}Nandini",
                "rerun_date": r"30\s*Sep", "paid_october": r"October"},
         claims={"already_paid": r"\b(has been|was|were|already)\s+paid\b"},
         prohibited=dict(UNSAFE_ALWAYS), max_lines=2, max_words=80, must_not_contain=TEMPLATE_FILLERS,
         raw_values=A4.raw, never_returned=A4.never_returned),

    Spec("B1", "B1 salary comparison", _src(B1),
         facts={"higher_earner": r"Amit Trivedi[^.\n]{0,120}\b(higher|more)\b",
                "difference": r"₹\s?2,70,000", "percentage": r"12\.4\s?%"},
         claims={"fairness_judgement": r"\b(underpaid|overpaid|unfair|inequit\w*|discriminat\w*)\b"},
         prohibited={"pay_change_advice": (r"\b(should|recommend\w*|consider|suggest\w*)\b[^.\n]{0,50}\b(raise|"
                                           r"increase|adjust|revise|cut|reduce)\b[^.\n]{0,40}\b(salary|ctc|pay)\b",
                                           None), **UNSAFE_ALWAYS},
         max_lines=2, max_words=70, extra_numbers={"270000", "12.4"},
         raw_values=B1.raw, never_returned=B1.never_returned),

    Spec("B2", "B2 ESI eligibility letter", _src(B2, "₹21,000"),
         facts={"covered": r"covered (by|under) (the )?(ESI|Employees'? State Insurance)|ESI[^.\n]{0,30}appl(y|ies)|"
                           r"eligible for (the )?ESI",
                "ceiling": r"₹\s?21,000", "addressed_by_name": r"Hetal Shah"},
         claims={"contribution_rates": r"\b(0\.75|3\.25|4)\s?%"},
         prohibited={"opt_out_advice": (r"\bopt(ing)?[- ]out\b", None), **UNSAFE_ALWAYS},
         max_lines=3, max_words=80, extra_numbers={"21000"},
         raw_values=B2.raw, never_returned=B2.never_returned),

    Spec("B3", "B3 pay band position", _src(B3, "₹18,00,000 ₹26,00,000"),
         facts={"band": r"\bP3\b", "band_min": r"₹\s?18,00,000", "band_max": r"₹\s?26,00,000",
                "position": r"middle third", "employee": r"Hetal Shah"},
         claims={"precise_position": r"\b(midpoint|exactly (in )?the middle|percentile|\d{2}\s?% (of|through|into))\b"},
         prohibited={"pay_change_advice": (r"\b(should|recommend\w*|consider|suggest\w*|eligible)\b[^.\n]{0,50}"
                                           r"\b(raise|increase|promot\w*|adjust\w*|revis\w*)\b", None), **UNSAFE_ALWAYS},
         max_lines=2, max_words=70, extra_numbers={"1800000", "2600000"},
         raw_values=B3.raw, never_returned=B3.never_returned),
]

_MT_SRC = "\n".join([PV.NER_MISS, *_declared(A4)])
_MT_RAW = A4.raw + ["Imran", "Ansari"]
SPECS += [
    Spec("M1", "M turn 1", _MT_SRC,
         facts={"requester": r"Imran Ansari", "assignee": r"Tejaswini Kulkarni", "nandini": r"Nandini",
                "paid_october": r"October"},
         max_lines=2, max_words=80, prohibited=dict(UNSAFE_ALWAYS), raw_values=_MT_RAW),
    Spec("M2", "M turn 2", _MT_SRC,
         facts={"addressed_to_requester": r"\bImran\b", "paid_october": r"October"},
         claims={"invented_date": r"\b\d{1,2}(st|nd|rd|th)?\s+(of\s+)?October\b|\bOctober\s+\d{1,2}(st|nd|rd|th)?\b"},
         max_lines=3, max_words=60, prohibited=dict(UNSAFE_ALWAYS), must_not_contain=TEMPLATE_FILLERS,
         raw_values=_MT_RAW),
    Spec("M3", "M turn 3", _MT_SRC,
         facts={"requester": r"Imran", "assignee": r"Tejaswini", "nandini_rerun": r"Nandini[^.\n]{0,80}re-?run",
                "paid_october": r"October"},
         max_lines=1, max_words=60, prohibited=dict(UNSAFE_ALWAYS), raw_values=_MT_RAW),
]
