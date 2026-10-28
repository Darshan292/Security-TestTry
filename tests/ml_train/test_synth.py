import json
import random

import pytest
from ml.train import gate, genres, io, structured, synth, values as V
from ml.train.bio import validate_spans
from ml.train.labels import ENTITY_TYPES


def test_offsets_exact_for_filled_templates():
    tpl = ("Dear {{PERSON_1:first}}, PAN {{PAN_1}} and Aadhaar {{AADHAAR_1}} on file for {{PERSON_1}} (id {{EMPLOYEE_ID_1}}). "
           "Mail {{EMAIL_1}}, call {{PHONE_1}}, UPI {{UPI_ID_1}}, account {{BANK_ACCOUNT_1}} {{IFSC_1}}, card {{CARD_1}}, "
           "CTC {{COMPENSATION_1}}, born {{DATE_OF_BIRTH_1}}, doc {{IDENTIFIER_1}}, key {{SECRET_1}}. Ticket {{NEG_TICKET_1}} in {{NEG_PLACE_1}}.")
    for seed in range(40):
        text, spans = synth.fill_template(tpl, random.Random(seed))
        validate_spans(text, spans)
        assert {s["type"] for s in spans} == set(ENTITY_TYPES)
        by_type = {}
        for s in spans:
            by_type.setdefault(s["type"], []).append(text[s["start"]:s["end"]])
        assert len(by_type["PERSON"]) == 2 and by_type["PERSON"][0][0].lower() in by_type["PERSON"][1].lower()
        assert "{{" not in text
        # negative slot text is present but unlabelled
        assert not any(text[s["start"]:s["end"]] == "" for s in spans)


def test_same_index_same_value_and_coherent_pan_email():
    tpl = "{{PERSON_1}} / {{PERSON_1}} / {{PAN_1}} / {{EMAIL_1}} / {{PHONE_1}} / {{UPI_ID_1}}"
    for seed in range(30):
        text, spans = synth.fill_template(tpl, random.Random(seed))
        vals = [text[s["start"]:s["end"]] for s in spans]
        assert vals[0] == vals[1]
        assert len(vals) == 6
        local = vals[3].split("@")[0]
        assert local.isascii()


def test_unknown_and_unbalanced_slots_rejected():
    for bad in ("{{NOPE_1}}", "{{PERSON}}", "{{PERSON_1", "{{PERSON_1:weird}}", "x {{ PERSON_1 }} {{", "[PERSON_1]", "<PERSON_1>"):
        ok, why = synth.validate_template("Hello there " + bad + " lorem ipsum " * 20, None)
        assert not ok, bad
    with pytest.raises(synth.TemplateError):
        synth.parse_slots("{{FOO_1}}")
    with pytest.raises(synth.TemplateError):
        synth.parse_slots("}} stray")


def test_json_braces_do_not_trip_slot_check():
    tpl = '{"emp": {"id": "{{EMPLOYEE_ID_1}}", "name": "{{PERSON_1}}"}}'
    assert synth.parse_slots(tpl)
    text, spans = synth.fill_template(tpl, random.Random(1))
    json.loads(text)


@pytest.mark.parametrize("leak", [
    "Call 9876543210 now", "mail ravi.kumar@gmail.com please", "PAN ABCPD1234E", "IFSC HDFC0001234", "aadhaar 234123412346",
    "card 4111 1111 1111 1111", "salary is Rs. 85,000 per month", "he earns 12 LPA", "paid to ravi@okaxis", "AKIAABCDEFGHIJKLMNOP",
    "DOB: 12/04/1990", "Ask Rajesh Sharma to approve",
])
def test_unslotted_pii_dropped(leak):
    ok, why = synth.validate_template(f"Hi team, {leak}. {{{{PERSON_1}}}} " + "lorem ipsum dolor " * 12, None)
    assert not ok and why


@pytest.mark.parametrize("fine", [
    "Section 80C limit of Rs. 1,50,000 applies", "Call the helpline 1800 123 4567", "write to hr@acme.co.in", "Vijayawada office",
    "joined on 12/04/2019", "ticket #4821", "Asha worker scheme", "the standard deduction up to Rs. 75,000",
])
def test_known_hard_negatives_allowed_in_free_text(fine):
    ok, why = synth.validate_template(f"Hello {{{{PERSON_1}}}}, {fine}. " + "lorem ipsum dolor sit amet " * 12, None)
    assert ok, why


def test_negative_genre_rejects_entity_slots_and_allows_none():
    spec = genres.BY_NAME["policy_faq_negative"]
    body = "Circular: " + "The statutory limit under {{NEG_STATUTORY_1}} applies in {{NEG_PLACE_1}}. " * 12
    assert synth.validate_template(body, spec)[0]
    assert not synth.validate_template(body + "{{PERSON_1}}", spec)[0]
    assert not synth.validate_template("plain text " * 60, genres.BY_NAME["salary_slip"])[0]


def test_hard_negatives_carry_no_spans():
    tpl = "Ref {{NEG_TICKET_1}} {{NEG_STATUTORY_1}} {{NEG_INVOICE_1}} {{NEG_EXT_1}} {{NEG_HELPLINE_1}} {{NEG_DATE_1}} {{NEG_PIN_1}} " \
          "{{NEG_COUNT_1}} {{NEG_ROLE_EMAIL_1}} {{NEG_PLACE_1}} {{NEG_NAMEDPLACE_1}} {{NEG_ORG_1}} {{NEG_NAMEWORD_1}}"
    for seed in range(50):
        text, spans = synth.fill_template(tpl, random.Random(seed))
        assert spans == [] and len(text) > 40


def test_all_programmatic_genres_valid_exact_and_no_person_newline():
    recs = synth.programmatic_records(600, seed=7)
    assert {r["genre"] for r in recs} == {g.name for g in genres.GENRES if g.program}
    for r in recs:
        io.validate_record(r)
        for s in r["spans"]:
            v = r["text"][s["start"]:s["end"]]
            assert v and v == v.strip() or s["type"] == "SECRET"
            if s["type"] == "PERSON":
                assert "\n" not in v
        assert r["source"] == "synthetic" and r["generator"].startswith("programmatic:")


def test_programmatic_structure_checks_payroll_and_slip():
    recs = synth.programmatic_records(300, seed=11, genres=("payroll_fixed_width", "salary_slip", "csv_paste"))
    for r in recs:
        t = {x: [r["text"][s["start"]:s["end"]] for s in r["spans"] if s["type"] == x] for x in ENTITY_TYPES}
        for a in t["AADHAAR"]:
            assert V.verhoeff_valid("".join(c for c in a if c.isdigit()))
        assert all(len(p) == 10 for p in t["PAN"])
        if r["genre"] == "payroll_fixed_width":
            lines = r["text"].split("\n")
            assert lines[0].isupper() or "PAYROLL" in lines[0]
            assert len(t["EMPLOYEE_ID"]) >= 4 and len(t["COMPENSATION"]) >= 8


def test_deterministic_with_seed():
    a = synth.programmatic_records(50, seed=5)
    b = synth.programmatic_records(50, seed=5)
    c = synth.programmatic_records(50, seed=6)
    assert a == b and a != c
    tpl = [{"id": "t1", "genre": "bullet_list", "lang": "en", "template": "- {{PERSON_1}} {{PHONE_1}} {{COMPENSATION_1}} " + "x" * 5}]
    assert synth.fill_records(tpl, 3, 1) == synth.fill_records(tpl, 3, 1)
    assert synth.fill_records(tpl, 3, 1) != synth.fill_records(tpl, 3, 2)
    assert len({r["text"] for r in synth.fill_records(tpl, 5, 1)}) == 5


def test_split_by_template_has_no_leakage():
    ids = [f"t{i:05d}" for i in range(1000)]
    train, dev = synth.split_templates(ids, 0.1, seed=3)
    assert not train & dev and train | dev == set(ids) and 40 < len(dev) < 180
    assert synth.split_templates(ids, 0.1, seed=3) == (train, dev)
    tpl = [{"id": i, "genre": "bullet_list", "template": "- {{PERSON_1}} {{PHONE_1}}"} for i in ids[:50]]
    recs = synth.fill_records(tpl, 4, 0)
    tr, dv = synth.split_templates([t["id"] for t in tpl], 0.2, 1)
    assert not ({r["template_id"] for r in recs if r["template_id"] in tr} & {r["template_id"] for r in recs if r["template_id"] in dv})


def test_parse_llm_output_and_prompt_mentions_all_slots():
    raw = "```text\nTemplate 1:\nHello {{PERSON_1}} " + "a" * 100 + "\n=====\nSecond {{PAN_1}}\n=====\n\n```"
    parts = synth.parse_llm_output(raw)
    assert len(parts) == 2 and parts[0].startswith("Hello") and "```" not in parts[1]
    spec = genres.BY_NAME["salary_slip"]
    msgs = synth.build_messages(spec, genres.sample_controls(random.Random(1), spec), 3)
    body = msgs[1]["content"]
    for t in ENTITY_TYPES:
        assert "{{" + t + "_N}}" in body
    assert "=====" in body and msgs[0]["role"] == "system"


def test_every_genre_spec_is_usable():
    assert len(genres.GENRES) >= 30
    must = {"payroll_fixed_width", "csv_paste", "tsv_paste", "salary_slip", "form16_extract", "email_thread", "whatsapp_chat", "slack_log",
            "hinglish_message", "ocr_noisy_scan", "bullet_list", "markdown_table", "address_block", "kinship_mention", "offer_letter",
            "relieving_letter", "warning_letter", "leave_request", "medical_leave_request", "grievance_ticket", "bank_change_request",
            "reimbursement_claim", "bgv_form"}
    assert must <= set(genres.BY_NAME)
    rng = random.Random(0)
    for g in genres.GENRES:
        assert set(g.typical_slots) <= set(ENTITY_TYPES)
        if g.program:
            assert g.program in structured.PROGRAMS
        synth.build_messages(g, genres.sample_controls(rng, g), 2)
    assert set(structured.PROGRAMS) == {g.program for g in genres.GENRES if g.program}


def test_io_roundtrip_and_validation(tmp_path):
    recs = synth.programmatic_records(20, 1)
    p = tmp_path / "x.jsonl.gz"
    assert io.write_jsonl(p, recs) == 20
    assert list(io.read_jsonl(p)) == recs
    bad = dict(recs[0], source="real")
    with pytest.raises(ValueError):
        io.validate_record(bad)
    with pytest.raises(ValueError):
        io.make_record("x", "g", "en", "abc", [{"start": 0, "end": 2, "type": "NOPE"}], "g")
    assert len(io.sha256_file(p)) == 64


def test_gate_records():
    recs = gate.programmatic_gate_records(300, 2)
    labs = {l for r in recs for l in r["labels"]}
    assert labs == set(gate.GATE_POSITIVE) | {"NONE"}
    for r in recs:
        assert ("NONE" in r["labels"]) == (r["labels"] == ["NONE"])
        assert "{{" not in r["text"]
    assert gate.programmatic_gate_records(10, 2) == gate.programmatic_gate_records(10, 2)
    with pytest.raises(ValueError):
        io.make_gate_record("a", "g", "en", "t", ["NONE", "HEALTH"], "x")
    with pytest.raises(ValueError):
        io.make_gate_record("a", "g", "en", "t", ["BOGUS"], "x")


def test_gate_template_validation_requires_cue():
    assert gate.validate_gate_template("{{PERSON_1}} was hospitalised after surgery and needs medical leave.", ["HEALTH"])[0]
    assert not gate.validate_gate_template("{{PERSON_1}} asks about the canteen menu for next week please.", ["HEALTH"])[0]
    assert gate.validate_gate_template("{{PERSON_1}} asks about the canteen menu for next week please.", ["NONE"])[0]
    for lab, seeds in gate.SEEDS.items():
        for _, s in seeds:
            assert gate.CUES[lab].search(s), (lab, s)


def test_gate_mapping_documented():
    from ml.train import labels
    assert labels.GATE_TO_PURPOSE_DEPENDENT["PERFORMANCE_RATING"] == "performance_rating"
