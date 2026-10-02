"""E-UTIL harness: scorers, MAX transform, restore round trip, blocked handling (synthetic data only)."""
import json
from pathlib import Path

import pytest

from eval.eutil import run_eutil as H

FIX = Path(__file__).parent / "fixtures"
TASKS = FIX / "eutil_fixture.jsonl"
ROSTER = FIX / "eutil_roster_fixture.jsonl"


def task(**kw):
    t = {"id": "x", "task_type": "t", "output_format": "text", "labels": [],
         "gold": {"label": None, "json": None, "facts": [], "numbers": {}, "required_entities": [],
                  "required_sections": [], "forbidden": []}}
    g = kw.pop("gold", {})
    t["gold"].update(g)
    t.update(kw)
    return t


def test_label_scoring():
    t = task(output_format="label", gold={"label": "Access"})
    assert H.score_item(t, " access. ")["correct"]
    assert H.score_item(t, "`ACCESS`")["correct"]
    assert not H.score_item(t, "access or payroll")["correct"]
    assert not H.score_item(t, None)["correct"]


def test_json_scoring_fenced_and_partial():
    t = task(output_format="json", gold={"json": {"amount": 45000, "name": "Asha Menon"}})
    ok = 'Here:\n```json\n{"amount": "Rs. 45,000", "name": "asha  menon"}\n```'
    assert H.score_item(t, ok)["correct"]
    s = H.score_item(t, '{"amount": 45000, "name": "Other"}')
    assert s["score"] == 0.5 and not s["correct"]
    assert H.score_item(t, "not json")["score"] == 0.0


def test_facts_numbers_and_folding():
    t = task(gold={"facts": ["Overtime", "grievance"], "numbers": {"ctc": "₹18,50,000"}})
    s = H.score_item(t, "The  OVERTIME\ngrievance was about CTC 1850000.")
    assert s["score"] == 1.0 and s["correct"]
    s = H.score_item(t, "overtime only, ctc 1,850,001")
    assert abs(s["score"] - 1 / 3) < 1e-3 and not s["correct"]
    assert H.digits(5.0) == "5" and H.digits("1,20,000") == "120000"


def test_entities_sections_forbidden():
    t = task(gold={"required_entities": ["Priya Nair", "Rahul"], "required_sections": ["Summary", "Next steps"],
                   "forbidden": ["terminated"]})
    s = H.score_item(t, "summary: priya nair was TERMINATED")
    assert s["entity_preservation"] == 0.5 and s["section_completeness"] == 0.5 and s["forbidden_hits"] == 1


def test_max_transform_deterministic_and_roundtrip():
    text = "Meeting with Dana Quill on Monday. Budget 1,20,000 for Quill Corp [PERSON_1ABCDE]. Then Tuesday."
    r1, f1, r2, f2 = {}, {}, {}, {}
    a = H.max_transform(text, r1, f1)
    b = H.max_transform(text, r2, f2)
    assert a == b and r1 == r2
    assert "[PERSON_1ABCDE]" in a and "Monday" in a and "Dana" not in a and "1,20,000" not in a
    assert a.count("[VALUE_") >= 4
    back = H.VALUE_RE.sub(lambda m: r1[f"VALUE_{m.group(1)}"], a)
    assert back == text


def _prep(cond):
    prot = None if cond == "raw" else H.build_protector(ROSTER)
    return [H.prepare_item(it, cond, prot) for it in H.load_jsonl(TASKS)]


def test_gateway_blocks_and_counts():
    recs = {r["id"]: r for r, _ in _prep("gateway")}
    assert recs["F3"]["blocked"] and recs["F3"]["prompt"] is None
    assert {"AADHAAR", "PAN"} <= set(recs["F3"]["blocked_types"])
    assert not recs["F1"]["blocked"] and recs["F1"]["system"] == H.SYSTEM_NOTE
    assert recs["F1"]["sensitive_in_prompt"] == 0 and recs["F1"]["critical_masked"] == 0
    raw = {r["id"]: r for r, _ in _prep("raw")}
    assert raw["F1"]["sensitive_in_prompt"] == 3 and raw["F1"]["system"] is None


def test_max_does_not_block_and_masks_more():
    recs = {r["id"]: r for r, _ in _prep("max")}
    assert not recs["F3"]["blocked"]
    assert recs["F3"]["sensitive_in_prompt"] == 0
    assert recs["F1"]["critical_masked"] >= 1      # "12 March" digits are aliased


def test_finish_restore_roundtrip(tmp_path):
    out = tmp_path
    for cond in ("raw", "gateway", "max"):
        prot = None if cond == "raw" else H.build_protector(ROSTER)
        recs, rest = [], {}
        for it in H.load_jsonl(TASKS):
            r, rs = H.prepare_item(it, cond, prot)
            recs.append(r)
            rest[it["id"]] = rs
        (out / f"prompts_{cond}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        (out / f"restore_{cond}.json").write_text(json.dumps(rest))
        answers = []
        for r in recs:
            if r["blocked"]:
                continue
            if r["id"] == "F1":
                import re
                toks = re.findall(r"\[(?:PERSON|VALUE)_\w+\]", r["prompt"] or "")
                answers.append({"id": "F1", "answer": "Summary: " + " ".join(toks[:2]) + " [PERSON_9ZZZZZ] overtime"})
            else:
                answers.append({"id": r["id"], "answer": "access"})
        (out / f"answers_{cond}.jsonl").write_text("".join(json.dumps(a) + "\n" for a in answers))
        H.main(["finish", "--condition", cond, "--out", str(out), "--roster", str(ROSTER)])
    fin = H.load_finals(out)
    assert fin["gateway"]["F3"]["blocked"] and fin["gateway"]["F3"]["restored"] is None
    g = fin["gateway"]["F1"]
    assert "Priya Nair" in g["restored"] or "Rahul Verma" in g["restored"]
    assert "[PERSON_9ZZZZZ]" in g["fabricated"] and "9ZZZZZ" not in g["restored"]
    assert fin["raw"]["F1"]["fabricated"] == []
    m = fin["max"]["F1"]
    assert "[VALUE_" not in m["restored"] and m["restore_ms"] >= 0
    tasks = H.load_jsonl(TASKS)
    res = H.score_all(tasks, fin)
    assert res["items"]["F3"]["gateway"]["blocked"] and not res["items"]["F3"]["gateway"]["correct"]
    assert res["aggregate"]["gateway"]["overall"]["blocked"] == 1
    assert H.render_scores_md(res) and H.render_review(tasks, fin, {}, res)


def test_cli_prepare_writes_files(tmp_path):
    H.main(["prepare", "--condition", "gateway", "--tasks", str(TASKS), "--roster", str(ROSTER), "--out", str(tmp_path)])
    assert (tmp_path / "prompts_gateway.jsonl").exists() and (tmp_path / "restore_gateway.json").exists()
