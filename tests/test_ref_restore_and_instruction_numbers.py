"""Re-audit N4 and N6. Fresh synthetic values, written for this file only.

N4: an identifier-shaped value of no recognised scheme (IDENTIFIER, data class REF) is aliased on the contracted
free-text purposes and the reader gets the exact value back; AADHAAR_LIKE and every recognised C3 type are removed
and never restored. Company reference formats and the organisation's own registration numbers cross raw.

N6: a LOCAL_COMPUTE instruction cannot restate a withheld value in any written form, or rounded within 0.5%.
"""
import json

import pytest

import pgw.product.local_compute as lc
from pgw.product import amounts
from pgw.product.contracts import ContractError, DataClass, load_contract
from pgw.types import EntityType as T
from tests.sealed import ROOT
from tests.test_product_slice import answer, call, make, sent

TICKET_CONTRACT = ROOT / "contracts/ticket_summary.yaml"


# --- N4: the REF class in the contract loader -----------------------------------------------------------------

def _variant(tmp_path, edit) -> object:
    src = TICKET_CONTRACT.read_text()
    p = tmp_path / "c.yaml"
    p.write_text(edit(src))
    return load_contract(p)


def test_ref_is_aliased_and_restored_c3_is_not():
    c = load_contract(TICKET_CONTRACT)
    assert c.classes[DataClass.REF] == "ALIAS" and DataClass.REF in c.restore
    assert c.restorable(T.IDENTIFIER) and not c.restorable(T.AADHAAR_LIKE)
    assert all(not c.restorable(t) for t in (T.AADHAAR, T.PAN, T.BANK_ACCOUNT, T.CARD, T.UPI_ID, T.SECRET))


def test_unstated_ref_is_as_strict_as_c3(tmp_path):
    c = _variant(tmp_path, lambda s: s.replace("  REF: ALIAS", "  # REF unstated").replace(", REF]", "]"))
    assert c.classes[DataClass.REF] == c.classes[DataClass.C3] == "REDACT"
    assert c.actions()[T.IDENTIFIER].value == "REDACT" and not c.restorable(T.IDENTIFIER)


@pytest.mark.parametrize("edit,why", [
    (lambda s: s.replace("  REF: ALIAS", "  REF: ALLOW"), "not allowed"),
    (lambda s: s.replace("  C3: REDACT", "  C3: ALIAS"), "not allowed"),             # C3 is never aliased out
    (lambda s: s.replace("restore: [C1, C2, REF]", "restore: [C1, C2, REF, C3]"), "never restored"),
    (lambda s: s.replace("  REF: ALIAS", "  REF: ALIAS\n  C9: ALIAS"), "malformed"),
])
def test_unsafe_ref_or_c3_settings_do_not_load(tmp_path, edit, why):
    with pytest.raises((ContractError, ValueError), match=why):
        _variant(tmp_path, edit)


@pytest.mark.parametrize("entry", [
    "ravi.k@example.org", "123456789012", "29aakck5714d1zh", "ABC", "AB--1234567890", "A" * 40, "ZZZZZZZZZZZZ",
])
def test_org_identifiers_accept_only_registration_shaped_entries(tmp_path, entry):
    with pytest.raises(ContractError, match="org_identifiers"):
        _variant(tmp_path, lambda s: s.replace('org_identifiers: ["29AAKCK5714D1ZH"',
                                               f'org_identifiers: ["{entry}", "29AAKCK5714D1ZH"'))


# --- N4: end to end, production gateway, exact bytes -----------------------------------------------------------

# Fresh reference values of the kinds the re-audit saw over-masked, plus personal identifiers of unrecognised schemes.
REFS = {"servicenow": "INC0073156", "change": "CHG0052204", "kb": "KB0019382", "jira": "HRIS-7741",
        "po": "PO-61842037", "invoice": "INV-2026-0388"}
FRESH_IDENTIFIERS = {"sap_document": "5100038264", "passport": "M4062918", "member_id": "QRT4417730825"}
ORG = "29AAKCK5714D1ZH"


def test_references_cross_identifiers_are_aliased_and_come_back_exact(tmp_path):
    client, mock = make(tmp_path, "echo")
    text = (f"Imran Ansari: payroll correction is tracked in {REFS['servicenow']} (change {REFS['change']}, "
            f"article {REFS['kb']}, Jira {REFS['jira']}). Vendor {REFS['po']} / {REFS['invoice']}. SAP document "
            f"{FRESH_IDENTIFIERS['sap_document']} was posted; passport {FRESH_IDENTIFIERS['passport']} on file; "
            f"insurer member {FRESH_IDENTIFIERS['member_id']}. Our GSTIN is {ORG}.")
    r = call(client, "ticket_summary", text)
    assert r.status_code == 200, r.text
    wire = sent(mock)
    for v in [*REFS.values(), ORG]:
        assert v in wire, f"business reference {v} was masked"
    for v in FRESH_IDENTIFIERS.values():
        assert v not in wire                              # the provider sees a scoped placeholder ...
    assert wire.count("[IDENTIFIER_") >= len(FRESH_IDENTIFIERS)
    out = answer(r)
    for v in [*REFS.values(), *FRESH_IDENTIFIERS.values(), ORG]:
        assert v in out, f"{v} not given back to the reader"   # ... and the reader gets the exact value
    assert "[IDENTIFIER" not in out


def test_c3_values_in_the_same_request_are_still_never_returned(tmp_path):
    client, mock = make(tmp_path, "echo")
    pan, acct = "QWRPK7305L", "40912285736601"
    r = call(client, "ticket_summary", f"Imran Ansari: PAN {pan}, salary a/c {acct}, ticket {REFS['servicenow']}.")
    assert r.status_code == 200, r.text
    wire, out = sent(mock), answer(r)
    assert pan not in wire and acct not in wire and pan not in out and acct not in out
    assert REFS["servicenow"] in out


# --- N6: amounts in any unit ----------------------------------------------------------------------------------

@pytest.mark.parametrize("text,value", [
    ("3185000", 3185000), ("31,85,000", 3185000), ("3,185,000", 3185000), ("31.85 lakh", 3185000),
    ("31.85L", 3185000), ("31.85 LPA", 3185000), ("3.185 million", 3185000), ("3.185mn", 3185000),
    ("0.3185 crore", 3185000), ("3185k", 3185000), ("thirty one lakh eighty five thousand", 3185000),
    ("three point one eight five million", 3185000), ("31 lakh 85 thousand", 3185000), ("a million", 1e6),
    ("one crore twenty lakh", 12000000), ("2.4 bn", 2.4e9), ("two hundred five", 205),
])
def test_amount_values(text, value):
    found = amounts.numbers(f"pay is {text} now")
    assert [round(v, 6) for _, _, v, _ in found] == [value], found


def test_amounts_keep_separate_numbers_apart():
    vals = [v for _, _, v, _ in amounts.numbers("2 lakh 3 lakh, 24 50, FY 2026-27, four twenty, 1.5L and 2.5L")]
    assert vals == [2e5, 3e5, 24, 50, 2026, 27, 4, 20, 1.5e5, 2.5e5]


def test_amount_parsing_is_linear():
    import time
    for flood in ("one lakh " * 2000, "1,00,000 " * 2000, "2.5L and " * 2000, "point five " * 2000):
        t = time.perf_counter()
        amounts.numbers(flood)
        assert time.perf_counter() - t < 0.5


SALARY = load_contract(ROOT / "contracts/salary_comparison.yaml")
EMPLOYEES = [{"name": "Nikhil Varadarajan", "employee_id": "KD-12407", "annual_ctc": 3185000},
             {"name": "Farah Lokhandwala", "employee_id": "KD-12588", "annual_ctc": 2764000}]
# Ways a caller could restate a withheld CTC (or the aliased difference, ₹4,21,000) in the instruction.
WITHHELD_FORMS = [
    "3185000", "31,85,000", "3,185,000", "Rs 31.85 lakh", "31.85L", "31.85 LPA", "3.185 million", "3.185mn",
    "0.3185 crore", "3185k", "thirty one lakh eighty five thousand", "three point one eight five million",
    "31 lakh 85 thousand", "₹3,185,000.00", "31.85",
    "3.19 million", "32 lakh", "3.17 million",                  # rounded: within ±0.5% of 31,85,000
    "27.64 lakh", "2.76 million", "twenty seven lakh sixty four thousand",   # the other employee
    "4.21 lakh", "421k",                                        # the difference (aliased output)
]
# Numbers unrelated to any withheld value stay as written.
UNRELATED = ["compare these 2 employees", "for FY 2026-27", "within 30 days", "an offer of 3.3 million",
             "a band of 40 lakh", "under Section 80C", "grade P4 and 12 reports", "1.5 crore budget"]


def _prompt(instruction: str) -> str:
    payload = {"instruction": instruction, "employees": EMPLOYEES}
    return lc.run(SALARY, [{"role": "user", "content": json.dumps(payload)}]).prompt


def test_the_form_set_is_as_wide_as_required():
    assert len(WITHHELD_FORMS) >= 10 and len(UNRELATED) >= 5


@pytest.mark.parametrize("form", WITHHELD_FORMS)
def test_instruction_cannot_restate_a_withheld_value(form):
    prompt = _prompt(f"Nikhil is on {form} rupees; who earns more?")
    instruction = prompt.split("\n\nFacts computed")[0]
    assert form not in instruction and "[" in instruction, instruction
    assert "who earns more?" in instruction                   # the rest of the instruction is kept


@pytest.mark.parametrize("text", UNRELATED)
def test_unrelated_numbers_stay(text):
    instruction = _prompt(f"Please {text}; who earns more?").split("\n\nFacts computed")[0]
    assert instruction == f"Please {text}; who earns more?"


def test_mask_marks_the_withheld_type():
    out, n = lc.mask_withheld_numbers("A is on 2.45 million, B on 21.8 lakh, 3 reports", [
        (T.COMPENSATION, 2450000.0), (T.DERIVED, 2180000.0)])
    assert out == "A is on [COMPENSATION], B on [DERIVED], 3 reports" and n == 2


def test_esi_instruction_cannot_restate_the_monthly_gross():
    esi = load_contract(ROOT / "contracts/esi_letter.yaml")
    payload = {"instruction": "Her gross is 19.35k a month, about 19,400; explain ESI in two lines.",
               "employee": [{"name": "Reema Kulkarni", "employee_id": "KD-12611", "monthly_gross": 19350}]}
    res = lc.run(esi, [{"role": "user", "content": json.dumps(payload)}])
    head = res.prompt.split("\n")[0]
    assert "19.35k" not in head and "19,400" not in head and "two lines" in head


def test_gateway_path_b_instruction_restating_a_ctc_does_not_cross(tmp_path):
    client, mock = make(tmp_path)
    payload = {"instruction": "Nikhil is on 3.185 million rupees and Farah on 27.6 lakh. Who earns more?",
               "employees": EMPLOYEES}
    r = call(client, "salary_comparison", json.dumps(payload))
    assert r.status_code == 200, r.text
    wire = sent(mock)
    for v in ("3.185 million", "27.6 lakh", "3185000", "31,85,000", "2764000", "27,64,000"):
        assert v not in wire
    assert "Who earns more?" in wire
