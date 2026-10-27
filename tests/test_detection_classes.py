"""Detection classes that leaked on unseen data (auditor's held-out aggregate), each fixed as a class:

1. name words next to a detected or declared name part join it (also kinship "s/o", "d/o", "w/o", "son of", "late",
   and honorific chains);                                                           pgw.detect.names, pgw.pipeline
2. names in table rows and CSV/TSV/Markdown pastes (a name column by header or by majority);   pgw.detect.names
3. lower-case names (roster, declared, repeats, and after a name cue when not an English word);  pgw.detect.names
4. IBAN, mod-97 validated, any grouping;                                                 recognizers._iban
5. addresses: house number with street/locality words, PIN codes in an address context;  pgw.detect.places
6. amounts without a pay word: currency after the number, own-pay clauses, N million rupees;    recognizers
7. short-form and spoken dates of birth in a birth context;                               recognizers._dob
8. letter-for-digit swaps (O->0, l/I->1) in digit-dominant tokens, as a D0 view.            pgw.normalize

Every person and value here is invented for this file and differs from the development set
(tests/fixtures/detect_dev.py). The end-to-end tests read the exact bytes the upstream received.
"""
import json
import os
import time

import pytest
from starlette.testclient import TestClient

from pgw.detect import names
from pgw.detect.edm import RosterIndex
from pgw.detect.lexicon import is_english
from pgw.detect.recognizers import detect_d2, iban_valid
from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.normalize import views
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack
from pgw.product import subjects
from pgw.types import Context, EntityType as T
from tests.sealed import ROOT, attest, production, sha

POLICY = PolicyPack.load(ROOT / "policies/hris-default.yaml")
CTX = Context("t", "t", "integration_debug")     # identifiers REDACTed, names and amounts aliased

ROSTER = RosterIndex(b"C" * 32)
for _rec in ({"employee_id": "ZN-5521", "full_name": "Keshav Ambekar", "first_name": "Keshav",
              "last_name": "Ambekar", "email": "keshav.ambekar@orvana.in", "phone": "+91 98222 40517"},
             {"employee_id": "ZN-5534", "full_name": "Lavanya Thampi", "first_name": "Lavanya",
              "last_name": "Thampi", "email": "lavanya.thampi@orvana.in", "phone": "+91 97425 11893"},
             {"employee_id": "ZN-5560", "full_name": "Soumitra Gorai", "first_name": "Soumitra",
              "last_name": "Gorai", "email": "soumitra.gorai@orvana.in", "phone": "+91 90043 26618"}):
    ROSTER.add_employee(_rec)
PROT = Protector(POLICY, Keys(bytes(32)), None)
PROT_R = Protector(POLICY, Keys(bytes(32)), ROSTER)


def _out(text: str, declared: tuple = (), roster: bool = False) -> str:
    decl = subjects.matchers([("requester", T.PERSON, n) for n in declared]) or None
    p = PROT_R if roster else PROT
    return p.protect([{"role": "user", "content": text}], CTX, decl).messages[0]["content"]


def _iban(country: str, bban: str) -> str:
    n = int("".join(str(int(c, 36)) for c in bban + country + "00"))
    return f"{country}{98 - n % 97:02d}{bban}"


# --- 1. adjacent name words, kinship, honorific chains --------------------------------------------------------------

ADJACENT = [
    ("first name before a declared surname", "Please brief Mridula Sathaye before the audit.", ("Gajanan Sathaye",),
     False, ["Mridula"]),
    ("middle name between declared parts", "The cheque is for Omprakash Bhagwandas Lohia.", ("Omprakash Lohia",),
     False, ["Bhagwandas"]),
    ("surname after a roster first name", "Keshav Pendharkar asked about the gratuity form.", (), True,
     ["Pendharkar"]),
    ("signature surname on the next line", "Warm regards,\nLavanya\nKuriakose", (), True, ["Kuriakose", "Lavanya"]),
    ("s/o with late", "Claimant Arvinder Grewal s/o Late Joginder Grewal", (), False, ["Joginder", "Arvinder"]),
    ("w/o lower case", "Nominee: Kanchan Lokhande w/o sudhakar lokhande", (), False, ["sudhakar"]),
    ("d/o with honorific and initial", "Applicant Smt. Vimala Raghunath d/o Shri T. Venugopal", (), False,
     ["Venugopal", "Vimala"]),
    ("son of", "He is the son of Mr. Harbhajan Dhillon.", (), False, ["Harbhajan", "Dhillon"]),
    ("widow of late", "Pension to the widow of late Narsingh Rao Gaitonde", (), False, ["Narsingh", "Gaitonde"]),
    ("honorific chain", "Prof. Dr. Ritwika Sengupta reviewed the policy.", (), False, ["Ritwika", "Sengupta"]),
    ("parenthesised honorific", "Dr (Ms) Tanvi Ghaisas approved the leave.", (), False, ["Tanvi", "Ghaisas"]),
    ("rank chain", "Brig. Col. Inderjit Bains (retd) heads security.", (), False, ["Inderjit", "Bains"]),
]


@pytest.mark.parametrize("label,text,declared,roster,leak", ADJACENT, ids=[a[0] for a in ADJACENT])
def test_adjacent_and_kinship_names_are_replaced(label, text, declared, roster, leak):
    out = _out(text, declared, roster)
    for v in leak:
        assert v not in out, f"{label}: {out!r}"


@pytest.mark.parametrize("text,declared,keep", [
    ("Gauri Nadkarni Joined The Payroll Team Today", ("Gauri Nadkarni",), "Joined The Payroll Team Today"),
    ("Recently Gauri moved to Treasury.", ("Gauri Nadkarni",), "Recently"),
    ("Signed by Gauri Nadkarni Head of Rewards.", ("Gauri Nadkarni",), "Head of Rewards"),
    ("Arrived Late Tuesday Night from the offsite.", (), "Arrived Late Tuesday Night"),
    ("The Late Login Policy is under review.", (), "Late Login Policy"),
    ("Fill the S/O column in capitals.", (), "S/O column"),
])
def test_ordinary_capitalised_words_next_to_names_stay(text, declared, keep):
    assert keep in _out(text, declared)


def test_grown_name_is_found_everywhere_in_the_request():
    out = _out("Please brief Mridula Sathaye today. Mridula has the file.", ("Gajanan Sathaye",))
    assert "Mridula" not in out


# --- 2. tables ------------------------------------------------------------------------------------------------------

TABLES = [
    ("csv header", "Employee Name,Grade,City\nHemant Ghodke,L3,Pune\nSayali Parab,L4,Thane",
     ["Hemant", "Ghodke", "Sayali", "Parab"], ["Pune", "Thane"]),
    ("tsv two columns", "Manager\tShift\nDarshana Kudalkar\tNight\nPrithvi Mulla\tDay",
     ["Darshana", "Kudalkar", "Prithvi", "Mulla"], ["Night", "Day"]),
    ("markdown", "| Reviewer | Rating |\n|---|---|\n| Anagha Bapat | 4 |\n| Nachiket Rao | 3 |",
     ["Anagha", "Bapat", "Nachiket"], ["Rating"]),
    ("lower-case cells", "Name;Status\nshubhada pimple;active\nrohan zende;exited", ["shubhada", "rohan", "zende"],
     ["active", "exited"]),
    ("quoted csv", '"Nominee","Share"\n"Pushpa Kamble","60"\n"Vilas Kamble","40"', ["Pushpa", "Vilas", "Kamble"], []),
]


@pytest.mark.parametrize("label,text,leak,keep", TABLES, ids=[t[0] for t in TABLES])
def test_name_columns_are_replaced(label, text, leak, keep):
    out = _out(text)
    for v in leak:
        assert v not in out, f"{label}: {out!r}"
    for v in keep:
        assert v in out, f"{label}: {out!r}"


def test_majority_name_column_completes_with_roster():
    text = "Keshav Ambekar,Pune\nLavanya Thampi,Kochi\nSoumitra Gorai,Kolkata\nDhanashree Wable,Nashik"
    out = _out(text, roster=True)
    assert "Dhanashree" not in out and "Wable" not in out
    assert all(c in out for c in ("Pune", "Kochi", "Kolkata", "Nashik"))


@pytest.mark.parametrize("text", [
    "Department Name,Head Count\nPayments,14\nRisk,6",
    "Project Name\tOwner Team\nLedger Cleanup\tFinance\nAudit Prep\tCompliance",
    "| Leave Type | Days |\n|---|---|\n| Casual | 8 |\n| Sick | 7 |",
    "Bank Name,Branch\nState Bank,Kothrud\nUnion Bank,Baner",
])
def test_non_name_tables_are_untouched(text):
    assert _out(text) == text


# --- 3. lower-case names --------------------------------------------------------------------------------------------

LOWER = [
    ("roster single name", "can you ask keshav to send the form", (), True, ["keshav"]),
    ("declared name in lower case", "ritambhara called about the claim", ("Ritambhara Sule",), False, ["ritambhara"]),
    ("case-insensitive repeat", "Mr. Jagdish Sawant wrote in. jagdish sawant wants an update.", (), False,
     ["jagdish", "sawant"]),
    ("greeting", "hi pranoti, Your payslip is attached.", (), False, ["pranoti"]),
    ("sign-off", "Thanks.\nregards,\nvaibhavi chitale", (), False, ["vaibhavi", "chitale"]),
    ("verb cue and list", "Please remind aniket or shardul about the timesheets.", (), False, ["aniket", "shardul"]),
    ("possessive", "why is kalyani's reimbursement stuck?", (), False, ["kalyani"]),
    ("weak cue, two words", "I met with gargi phatak in Finance yesterday.", (), False, ["gargi", "phatak"]),
]


@pytest.mark.parametrize("label,text,declared,roster,leak", LOWER, ids=[x[0] for x in LOWER])
def test_lowercase_names_are_replaced(label, text, declared, roster, leak):
    out = _out(text, declared, roster)
    for v in leak:
        assert v not in out, f"{label}: {out!r}"


@pytest.mark.parametrize("text", [
    "hi team, the HRMS is down again.",
    "hi bhai, kab tak aayega?",
    "Please remind payroll about the timesheets.",
    "I met with finance about the budget.",
    "regards,\nhr helpdesk",
    "Hello sir, kindly approve the request.",
    "Ask someone in admin for the access card.",
    "Thanks all, closing this ticket.",
    "ping github support for the outage",
])
def test_lowercase_prose_is_untouched(text):
    assert _out(text) == text


def test_lexicon_separates_names_from_words():
    assert all(is_english(w) for w in ("Regards", "joined", "Payroll", "Finance", "approvals", "Monday", "hi"))
    assert not any(is_english(w) for w in ("Mridula", "Sathaye", "Kuriakose", "pendharkar", "ritambhara"))


# --- 4. IBAN --------------------------------------------------------------------------------------------------------

IBANS = [_iban("DE", "500105175407324931"), _iban("GB", "BARC20040441234567"), _iban("FR", "30006000011234567890189"),
         _iban("AE", "0260001015555555555"), _iban("NL", "RABO0123456789"), _iban("KW", "CBKU0000000000001234560101")]


@pytest.mark.parametrize("value", IBANS)
@pytest.mark.parametrize("form", ["compact", "spaced", "hyphen", "lower", "odd"])
def test_iban_any_grouping(value, form):
    v = {"compact": value, "spaced": " ".join(value[i:i + 4] for i in range(0, len(value), 4)),
         "hyphen": "-".join(value[i:i + 4] for i in range(0, len(value), 4)), "lower": value.lower(),
         "odd": f"{value[:2]} {value[2:4]} {value[4:9]} {value[9:]}"}[form]
    text = f"Transfer the settlement to {v} by Friday."
    ds = [d for d in detect_d2(text) if d.type is T.BANK_ACCOUNT]
    assert [(text[d.start:d.end], d.source) for d in ds] == [(v, "D2:iban")]
    assert _out(text) == "Transfer the settlement to [BANK_ACCOUNT] by Friday."


def test_iban_checksum_and_length_are_required():
    good = IBANS[0]
    bad = good[:2] + f"{(int(good[2:4]) + 1) % 100:02d}" + good[4:]
    assert iban_valid(good) and not iban_valid(bad)
    assert not [d for d in detect_d2(f"IBAN {bad} please") if d.source == "D2:iban"]
    assert not [d for d in detect_d2(f"IBAN {good}7 please") if d.source == "D2:iban"]   # wrong length for DE
    for text in ("Form DE12 for the German entity", "GB 22 is the page", "ISO 27001 audit"):
        assert not [d for d in detect_d2(text) if d.source == "D2:iban"]


# --- 5. addresses ---------------------------------------------------------------------------------------------------

ADDRESSES = [
    ("Address: Flat 1104, Nilgiri Heights, 6th Main, Jayanagar, Bengaluru 560041",
     ["1104", "Nilgiri Heights", "6th Main", "560041"], ["Bengaluru"]),
    ("Send it to House No 9, Laxmi Colony, Wardha - 442001", ["House No 9", "Laxmi Colony", "442001"], ["Wardha"]),
    ("He stays at 4/12, Krishna Kunj, MG Road, Indore, Madhya Pradesh 452001", ["4/12", "Krishna Kunj", "452001"], []),
    ("Pincode: 302 017", ["302 017"], []),
    ("Door No. 8-3-77, Yousufguda, Hyderabad 500045", ["8-3-77", "500045"], []),
]


@pytest.mark.parametrize("text,leak,keep", ADDRESSES, ids=[a[1][0] for a in ADDRESSES])
def test_addresses_and_pins_are_withheld(text, leak, keep):
    out = _out(text)
    for v in leak:
        assert v not in out, out
    for v in keep:
        assert v in out, out


@pytest.mark.parametrize("text", [
    "Meet in Room 512 on the 2nd floor.",
    "Build 452001 passed all checks.",
    "The Indore team has 140 people.",
    "Block 2 of the induction covers payroll.",
    "Plot attrition by month for 2025.",
    "Update the pincode field before Friday.",
    "Ticket 560041 was closed in Bengaluru today.",
])
def test_non_addresses_are_untouched(text):
    assert _out(text) == text


# --- 6. amounts without a pay word ----------------------------------------------------------------------------------

AMOUNTS = [
    ("Just 41,250/- got credited this month.", "41,250"),
    ("I got 58,900 rs after the deductions.", "58,900"),
    ("They cut 2,750 INR from my settlement.", "2,750"),
    ("He was offered 3.2 million rupees elsewhere.", "3.2 million"),
    ("Rs 67,500 reached her account on Monday.", "67,500"),
    ("I was paid 33,120 for February.", "33,120"),
    ("My take-home is 76,300 after tax.", "76,300"),
]


@pytest.mark.parametrize("text,value", AMOUNTS, ids=[a[1] for a in AMOUNTS])
def test_amounts_without_pay_words_are_compensation(text, value):
    out = _out(text)
    assert value not in out and "[COMPENSATION_" in out, out


@pytest.mark.parametrize("text", [
    "The projector costs 72,000/- plus GST.",
    "We onboarded 3,400 employees this year.",
    "Vendor invoice 5,600 rs for the catering.",
    "Revenue touched 4 billion rupees.",
    "The portal served 18,250 requests today.",
    "The 80C ceiling is 1,50,000 per year.",
    "Room 12/- is a typo in the floor plan.",
])
def test_business_amounts_and_counts_are_untouched(text):
    assert _out(text) == text


# --- 7. short-form dates of birth -----------------------------------------------------------------------------------

DOBS = [
    ("DOB: 17th Oct '88", "17th Oct '88"),
    ("born on 03-Mar-95 at Satara", "03-Mar-95"),
    ("date of birth: nov 9 1990", "nov 9 1990"),
    ("He was born on the 21st of August, 1986.", "21st of August, 1986"),
    ("Birth date - eleventh of May 1979", "eleventh of May 1979"),
    ("d.o.b 6 Sept '97", "6 Sept '97"),
    ("my birthday is January 2nd, '93", "January 2nd, '93"),
    ("born 5th Feb'89", "5th Feb'89"),
]


@pytest.mark.parametrize("text,value", DOBS, ids=[d[1] for d in DOBS])
def test_short_dates_of_birth(text, value):
    ds = [(text[d.start:d.end], d.type) for d in detect_d2(text)]
    assert (value, T.DATE_OF_BIRTH) in ds
    assert value not in _out(text)


@pytest.mark.parametrize("text", ["Joining date 17-Oct-24 for the analyst.", "The review is on the 21st of August.",
                                  "Release 6 Sept '25 notes are out."])
def test_dates_without_birth_context_are_untouched(text):
    assert _out(text) == text


# --- 8. letter-for-digit swaps --------------------------------------------------------------------------------------

SWAPS = [
    ("aadhaar ", "3l47 2O95 6l08", " is linked", "["),
    ("mobile ", "9l2O4 5678O", " (personal)", "[PHONE_"),
    ("salary a/c ", "6O21O4357819", " please", "["),
    ("UAN ", "1OO562839471", " pending", "[IDENTIFIER]"),
]


@pytest.mark.parametrize("prefix,value,suffix,marker", SWAPS, ids=[s[1] for s in SWAPS])
def test_swapped_letters_are_read_as_digits_and_mapped_back_exactly(prefix, value, suffix, marker):
    out = _out(prefix + value + suffix)
    assert out.startswith(prefix) and out.endswith(suffix), out
    middle = out[len(prefix):len(out) - len(suffix)]
    assert middle.startswith(marker) and middle.endswith("]") and value not in out, out


def test_swap_view_keeps_offsets_and_skips_words():
    text = "PAN ABCDE1234F, room 1O1, LOL, OIL, card 4O0O l234 5678 9OI2"
    v = dict((n, (t, om)) for n, t, om in views(text))
    swapped, om = v["digit_swap"]
    assert len(swapped) == len(text) and om == v["base"][1]
    assert "ABCDE1234F" in swapped and "LOL, OIL" in swapped and "4000 1234 5678 9012" in swapped
    assert "room 101" in swapped                     # read as digits, but nothing detects a room number
    assert "digit_swap" not in dict((n, t) for n, t, _ in views("The OIL and LOL tickets, model X1"))


# --- floods for the new views and recognizers (same bound as tests/test_dos_bounds.py) ------------------------------

SIZE = 100_000
LIMIT = 1.5
FLOODS = {
    "swap_tokens": lambda n: "1O1 " * (n // 4),
    "swap_long_run": lambda n: "1O" * (n // 2),
    "iban_starts": lambda n: "DE12 " * (n // 5),
    "iban_chain": lambda n: "GB" + "1 2 " * (n // 4),
    "pins": lambda n: "Pune 411001, " * (n // 13),
    "house_markers": lambda n: "Flat 12, " * (n // 9),
    "kinship": lambda n: "s/o " * (n // 4),
    "honorifics": lambda n: "Dr. " * (n // 4),
    "lower_cues": lambda n: "hi abcd, " * (n // 9),
    "cap_words": lambda n: "Zxq " * (n // 4),
    "table_rows": lambda n: "Name,City\n" + "Qwerty Zxcv,Pune\n" * (n // 17),
    "amount_suffix": lambda n: "my 12,345/- " * (n // 12),
    "dob_short": lambda n: "dob 1st Jan '90 " * (n // 16),
}


def _best(text: str) -> float:
    best = float("inf")
    for _ in range(3):
        t = time.perf_counter()
        PROT.detect_text(text)
        best = min(best, time.perf_counter() - t)
    return best


@pytest.mark.parametrize("name", FLOODS)
def test_new_paths_are_fast_on_floods(name):
    assert _best(FLOODS[name](SIZE)) < LIMIT


@pytest.mark.parametrize("name", ["swap_tokens", "iban_starts", "cap_words", "table_rows"])
def test_new_paths_scale_linearly(name):
    small = _best(FLOODS[name](25_000))
    large = _best(FLOODS[name](100_000))
    assert large < 8 * max(small, 0.02)


def test_full_request_with_grown_names_is_fast():
    text = ("Please brief Mridula Sathaye. " * 2000)[:SIZE]
    decl = subjects.matchers([("requester", T.PERSON, "Gajanan Sathaye")])
    t = time.perf_counter()
    PROT.protect([{"role": "user", "content": text}], CTX, decl)
    assert time.perf_counter() - t < 5 * LIMIT


def test_names_helpers_are_bounded():
    assert names.titled_unknown("ask zaqriv now") == "ask Zaqriv now"
    assert names.titled_unknown("ask payroll now") is None


# --- end to end: production gateway, signed config, outbound bytes ------------------------------------------------

KEY = "classes-key"


def _gateway(tmp_path):
    mock = MockLLMUpstream("echo")
    tasks = {"ticket_summary": str(ROOT / "contracts/ticket_summary.yaml")}
    apps = [{"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY), "purposes": sorted(tasks),
             "restore_output": True, "models": ["mock-1"], "tasks": tasks}]
    s = Settings(**production(tmp_path, apps), ledger_db_path=str(tmp_path / "l.sqlite"),
                 egress_ledger_path=str(tmp_path / "e.jsonl"), master_key=os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s)), mock


def _send(client, text, requester="Gajanan Sathaye"):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": text}],
            "pgw_subjects": {"requester": {"name": requester}}}
    return client.post("/v1/chat/completions", json=body, headers={
        "Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary", **attest("ticket_summary", body)})


def _sent(mock) -> str:
    return "\n".join(m["content"] for m in json.loads(mock.received[-1].decode("utf-8"))["messages"]
                     if m["role"] != "system")


TICKET = (
    "Raised by Gajanan Sathaye. Please brief Mridula Sathaye too.\n"
    "Nominee: Kanchan Lokhande w/o sudhakar lokhande; claimant Arvinder Grewal s/o Late Joginder Grewal.\n"
    "Prof. Dr. Ritwika Sengupta approved it; hi pranoti, pls check.\n"
    "Employee Name,Grade\nHemant Ghodke,L3\nSayali Parab,L4\n\n"
    f"Refund to {' '.join(IBANS[1][i:i + 4] for i in range(0, len(IBANS[1]), 4))}.\n"
    "Address: Flat 1104, Nilgiri Heights, 6th Main, Jayanagar, Bengaluru 560041\n"
    "Just 41,250/- got credited this month. DOB: 17th Oct '88. mobile 9l2O4 5678O.\n"
    "The Late Login Policy and the Payroll Team review are unchanged.")
SECRETS = ["Gajanan", "Mridula", "Sathaye", "Kanchan", "sudhakar", "Arvinder", "Joginder", "Ritwika", "Sengupta",
           "pranoti", "Hemant", "Ghodke", "Sayali", "Parab", IBANS[1][4:8], "Nilgiri Heights", "560041", "41,250",
           "17th Oct", "9l2O4"]
BENIGN = ["The Late Login Policy and the Payroll Team review are unchanged.", "Employee Name,Grade", "Bengaluru",
          "pls check"]


def test_gateway_sends_none_of_the_class_values(tmp_path):
    client, mock = _gateway(tmp_path)
    r = _send(client, TICKET)
    assert r.status_code == 200, r.text
    sent = _sent(mock)
    wire = mock.received[-1].decode("utf-8")
    for v in SECRETS:
        assert v not in sent and v not in wire, f"{v!r} crossed"
    for b in BENIGN:
        assert b in sent, f"benign text {b!r} was masked"


def test_gateway_leaves_benign_class_lookalikes_alone(tmp_path):
    client, mock = _gateway(tmp_path)
    text = ("Gajanan Sathaye asked: hi team, the Late Login Policy, Room 512 on the 2nd floor, build 452001, "
            "3,400 employees, the projector at 72,000/- plus GST, and the review on the 21st of August.")
    r = _send(client, text)
    assert r.status_code == 200, r.text
    sent = _sent(mock)
    assert "Gajanan" not in sent
    tail = text.split("asked: ", 1)[1]
    assert tail in sent, sent
