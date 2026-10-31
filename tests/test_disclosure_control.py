"""Small-group disclosure control on the RECORDS route (pgw.product.disclosure; docs/HRMS_CONTRACTS.md "Small
groups"; SECURITY_BOUNDARY.md §12).

Gateway tests run in production mode (signed config, attested payloads; tests/sealed.py) and read the exact bytes the
mock provider received. Table tests run the computation and rendering directly (records.run) on many random small
populations and check what leaves with an independent brute-force solver. SYNTHETIC data only (tests/hrms_synth.py).
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
from pathlib import Path

import pytest
import yaml

from pgw.gateway.app import create_app
from pgw.product import disclosure, integrity, records
from pgw.product.contracts import ContractError, load_contract
from pgw.product.fieldschema import SchemaError
from tests import hrms_synth as S
from tests.sealed import signing_key
from tests.test_hrms_records import ROOT, _settings, make, post, upstream_text, variant

K = 5
DC = ROOT / "registry/rules/disclosure_control.yaml"


def plan_for(override: dict | None = None, contract: str = "roster_question", registry: Path | None = None):
    raw = yaml.safe_load((ROOT / f"contracts/{contract}.yaml").read_text())
    raw["records"].update(override or {})
    c = load_contract("x.yaml", yaml.safe_dump(raw, allow_unicode=True).encode())
    return records.bind(contract, c, registry or ROOT / "registry", lambda p: Path(p).read_bytes())


def leaving(rr) -> str:
    """The prompt as the provider reads it (computed tokens substituted for their sentinels)."""
    return "".join(rr.raw.get(ch, ch) for ch in rr.prompt)


def run(plan, sy, params, scope=None):
    return records.run(plan, [{"role": "user", "content": sy.envelope(params)}], scope)


def person(dept, enabled=True, created=(2021, 4), pin="560034", logon=None):
    return {"dept": dept, "enabled": enabled, "created": dt.datetime(created[0], created[1], 3, 9, 0), "pin": pin,
            **({"logon": logon} if logon else {})}


# =============================================================================================================
# brute-force solver over what leaves
# =============================================================================================================

def parse_roster(text: str) -> dict:
    """Numbers and markers of a roster prompt: total, matched, missing, groups {label: value-or-marker}."""
    m = re.search(r"Records: (.+?)\. Filter: .+?\. Matching: (.+?) \(records without a value for a filtered "
                  r"field: (.+?)\)\.", text)
    assert m, text
    groups = {}
    block = text.split("Counts by group:\n", 1)[1].split("\nMatching records:", 1)[0]
    for line in block.splitlines():
        if line.startswith("- "):
            lab, val = line[2:].rsplit(": ", 1)
            groups[lab] = val
    return {"total": m.group(1), "matched": m.group(2), "missing": m.group(3), "groups": groups}


def domain(tok: str, k: int, cap: int) -> range:
    """What the reader knows about a printed value: a number exactly; 'fewer than k' -> 1..k-1; 'withheld' (a
    complementary suppression of the matched count, which would otherwise read 'fewer than k') -> k..; any other
    'suppressed' cell -> 1.. (zeros are always printed). Capped at `cap`: a smaller domain can only make the
    solver find a cell pinned, never hide a pin."""
    if re.fullmatch(r"\d+", tok):
        return range(int(tok), int(tok) + 1)
    if tok == f"fewer than {k}":
        return range(1, k)
    if tok == "withheld":
        return range(k, cap + 1)
    assert tok == "suppressed", tok
    return range(1, cap + 1)


def _compositions(total: int, doms: list):
    if not doms:
        if total == 0:
            yield ()
        return
    for v in doms[0]:
        if v > total:
            break
        for rest in _compositions(total - v, doms[1:]):
            yield (v, *rest)


def feasible(parsed: dict, k: int, cap: int) -> dict:
    """Every assignment of the table (total = matched + missing + did-not-match >= 0; matched = sum of groups)
    consistent with what was printed; returns the set of feasible values per cell."""
    tot, mat, mis = (domain(parsed[x], k, cap) for x in ("total", "matched", "missing"))
    glabs = list(parsed["groups"])
    gdoms = [domain(parsed["groups"][g], k, cap) for g in glabs]
    seen = {n: set() for n in ("total", "matched", "missing", "rest", *glabs)}
    for t in tot:
        for m in mat:
            for x in mis:
                rest = t - m - x
                if rest < 0:
                    continue
                if glabs:
                    combos = list(_compositions(m, gdoms))
                    if not combos:
                        continue
                    for c in combos:
                        for g, v in zip(glabs, c, strict=True):
                            seen[g].add(v)
                seen["total"].add(t)
                seen["matched"].add(m)
                seen["missing"].add(x)
                seen["rest"].add(rest)
    return seen


def random_population(rng: random.Random) -> list:
    """2-4 departments with 0-7 enabled, 0-3 disabled and 0-1 'no Enabled attribute' people each."""
    out = []
    for d in rng.sample(["Finance", "Legal", "Sales", "Support"], rng.randrange(2, 5)):
        out += [person(d, True)] * rng.randrange(0, 8)
        out += [person(d, False)] * rng.randrange(0, 4)
        out += [person(d, None)] * rng.randrange(0, 2)
    return out or [person("Finance")]


@pytest.mark.parametrize("k", [5, 7])
def test_complementary_suppression_no_suppressed_cell_is_determined(k):
    """Random small tables: from the numbers that leave, no suppressed cell (and no small 'did not match' cell) is
    uniquely determined, and no printed count is 1..k-1."""
    plan = plan_for({"min_group_size": k} if k != K else None)
    rng = random.Random(1000 + k)
    checked = suppressed_seen = 0
    for _ in range(150):
        people = random_population(rng)
        sy = S.ad_population(people, seed=rng.randrange(10**6))
        rr = run(plan, sy, {"filter": [{"field": "enabled", "op": "eq", "value": True}], "group_by": "department"})
        text = leaving(rr)
        p = parse_roster(text)
        # the record total is the size of the export the caller sent, not a quasi-identifier cell: it may be small
        for tok in [p["matched"], p["missing"], *p["groups"].values()]:
            assert not (tok.isdigit() and 0 < int(tok) < k), text
        cap = len(people) + k + 2
        f = feasible(p, k, cap)
        true_rest = sum(1 for x in people if x["enabled"] is False)
        for name in ("total", "matched", "missing", *p["groups"]):
            tok = p[name] if name in p else p["groups"][name]
            if not tok.isdigit():
                suppressed_seen += 1
                assert len(f[name]) >= 2, (name, text)
        if 0 < true_rest < k:
            assert len(f["rest"]) >= 2, text
        checked += 1
    assert checked == 150 and suppressed_seen > 100


def test_solver_detects_a_pinned_cell():
    """The solver is not vacuous: printing all but one group pins the last one."""
    p = {"total": "10", "matched": "10", "missing": "0", "groups": {"A": "9", "B": "suppressed"}}
    assert feasible(p, 5, 20)["B"] == {1}


def test_table_audit_unit():
    t = disclosure.Table(5)
    for n, v, tot in (("N", 13, True), ("A", 10, False), ("B", 3, False)):
        t.add(disclosure.Cell(n, v, n != "N", 2 if n != "N" else 3, total=tot))
    t.eq("N", ["A", "B"])
    st = t.protect()
    assert t.marker("B") == "suppressed" and t.marker("A") == "suppressed" and t.marker("N") is None
    assert st == {"primary": 1, "complementary": 1, "totals": 0}
    assert not t.pinned()


# =============================================================================================================
# a count of 1 never leaves; filters matching 1..k-1 list nobody
# =============================================================================================================

@pytest.fixture
def gw(tmp_path):
    """A fresh gateway (and so a fresh disclosure-control history) per test: the history is per tenant, app,
    purpose and dataset, not per conversation, so tests sharing one gateway would difference each other's
    synthetic populations (which reuse the same synthetic usernames)."""
    tmp = tmp_path
    t = {"roster_question": str(ROOT / "contracts/roster_question.yaml"),
         "roster_pin": variant(tmp, "roster_pin", {"select": ["display_name", "username", "department", "enabled",
                                                              "created_month", "pin_district"],
                                                   "show": ["department", "created_month", "pin_district"]}),
         "contractor_budget": str(ROOT / "contracts/contractor_budget.yaml"),
         "payroll_register_check": str(ROOT / "contracts/payroll_register_check.yaml")}
    client, mock, _ = make(tmp, t)
    return client, mock


def sent_since(mock, n0) -> str:
    return upstream_text(type("M", (), {"received": mock.received[n0:]})())


def test_a_count_of_one_never_leaves(gw):
    client, mock = gw
    # one person in Legal, one in Sales, the rest in Finance
    people = [person("Finance")] * 9 + [person("Legal"), person("Sales", enabled=False)]
    sy = S.ad_population(people)
    n0 = len(mock.received)
    r = post(client, "roster_question", sy.envelope({"filter": [], "group_by": "department", "list": False}),
             headers={"X-PGW-Conversation-Id": "one-1"})
    assert r.status_code == 200, r.text
    text = sent_since(mock, n0)
    groups = parse_roster(text)["groups"]
    assert groups["Legal"] == "suppressed" and groups["Sales"] == "suppressed"
    assert groups["Finance"] == "suppressed"          # complementary: Finance = 11 - 2 would give the small cells away
    assert not re.search(r": 1\b", text)
    dc = r.json()["pgw_report"]["records"]["disclosure"]
    assert dc["cells_suppressed"] == 2 and dc["cells_suppressed_complementary"] == 1
    # a filter on quasi-identifiers that matches one person: "fewer than k", nobody listed, no group count
    n0 = len(mock.received)
    r = post(client, "roster_question", sy.envelope({"filter": [{"field": "department", "op": "eq", "value": "Legal"}],
                                                     "group_by": "account_state", "list": True}),
             headers={"X-PGW-Conversation-Id": "one-2"})
    assert r.status_code == 200, r.text
    text = sent_since(mock, n0)
    assert "Matching: fewer than 5" in text and "none listed: fewer than 5 records match" in text
    assert not re.search(r"\[PERSON_[A-Z0-9]{4,}\]", text) and not re.search(r": 1\b", text)
    rec = r.json()["pgw_report"]["records"]
    assert rec["left"]["listed_rows"] == 0 and rec["disclosure"]["lists_withheld"] == 1


def test_small_did_not_match_cell_hides_the_total(gw):
    """enabled = true matching 10 of 11: printing 'Records: 11' would give away the one disabled account."""
    client, mock = gw
    sy = S.ad_population([person("Finance")] * 10 + [person("Finance", enabled=False)])
    n0 = len(mock.received)
    r = post(client, "roster_question", sy.envelope({"filter": [{"field": "enabled", "op": "eq", "value": True}]}),
             headers={"X-PGW-Conversation-Id": "rest-1"})
    assert r.status_code == 200, r.text
    p = parse_roster(sent_since(mock, n0))
    assert p["matched"] == "10" and p["total"] == "suppressed"
    assert r.json()["pgw_report"]["records"]["disclosure"]["totals_suppressed"] == 1


# =============================================================================================================
# listings: every listed alias's attributes are shared by >= k people
# =============================================================================================================

ROLLUP = yaml.safe_load(DC.read_text())["schemas"]["ad_user"]["hierarchies"]["department"][0]


def compatible(field: str, shown: str, p: dict) -> bool:
    """Could person p be displayed with `shown` for `field`? (Independent of pgw.product.disclosure.)"""
    if field == "department":
        return shown in (p["dept"], ROLLUP.get(p["dept"]))
    if field == "created_month":
        y = p["created"].year
        m = re.fullmatch(r"(\d{4}) to (\d{4})", shown)
        return shown in (p["created"].strftime("%Y-%m"), str(y)) or bool(m and int(m[1]) <= y <= int(m[2]))
    if field == "pin_district":
        return shown in (p["pin"][:3] + "xxx", p["pin"][:2] + "xxxx")
    raise AssertionError(field)


@pytest.mark.parametrize("seed", [37, 38, 39])
def test_listing_rows_meet_k_after_coarsening(gw, seed):
    client, mock = gw
    people = S.unique_combo_population(60, seed=seed)
    combos = {(p["dept"], p["created"].strftime("%Y-%m"), p["pin"][:3]) for p in people}
    assert len(combos) > 50                               # nearly everyone is unique before control
    sy = S.ad_population(people, seed=seed)
    n0 = len(mock.received)
    r = post(client, "roster_pin", sy.envelope({"filter": [], "list": True}),
             headers={"X-PGW-Conversation-Id": f"list-{seed}"})
    assert r.status_code == 200, r.text
    text = sent_since(mock, n0)
    lines = [ln for ln in text.splitlines() if ln.startswith("- [PERSON_")]
    assert len(lines) == len(people)
    withheld = 0
    for ln in lines:
        attrs = ln.split("): ", 1)[1] if "): " in ln else ""
        if attrs.startswith("attributes withheld"):
            withheld += 1
            continue
        shown = dict(a.split(": ", 1) for a in attrs.split("; ")) if attrs else {}
        cands = [p for p in people if all(compatible(f, v, p) for f, v in shown.items())]
        assert len(cands) >= K, ln
    assert withheld == 0 or withheld >= K
    dc = r.json()["pgw_report"]["records"]["disclosure"]
    assert dc["rows_coarsened"] + dc["rows_attributes_withheld"] > 0
    assert dc["quasi_identifiers"][:2] == ["department", "enabled"]


def test_listing_policy_withhold_and_alias_only_unit():
    people = [person("Finance", created=(2020, m)) for m in range(1, 7)] + [person("Legal", created=(2019, 2))]
    sy = S.ad_population(people)
    plan = plan_for({"show": ["department", "created_month"],
                     "select": ["display_name", "username", "department", "enabled", "created_month"]})
    for policy in ("withhold", "alias_only"):
        plan.control.listing = policy
        text = leaving(run(plan, sy, {"filter": [], "list": True}))
        lines = [ln for ln in text.splitlines() if ln.startswith("- [PERSON_") or ln.startswith("- Asha")
                 or "): " in ln]
        if policy == "alias_only":
            assert "department:" not in text and "attributes withheld" not in text
        else:   # no coarsening: every month is unique -> attributes withheld for everyone (7 >= k: indistinct)
            assert text.count("attributes withheld") == 7, lines


def test_exception_list_with_selected_quasi_identifiers_meets_k():
    """Payroll exception lists carry the alias only; a contract that SHOWS a QI gets the same k rule."""
    plan = plan_for({"show": ["department"], "select": [*plan_for(contract="payroll_register_check").spec.select,
                                                         "department"]}, contract="payroll_register_check")
    sy = S.payroll(40, seed=8, error_rate=0.5)
    text = leaving(run(plan, sy, None))
    rows = [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert rows
    depts = {}
    for ln in rows:
        m = re.search(r"department: ([^;]+?)(;|$)", ln)
        if m:
            depts[m.group(1)] = depts.get(m.group(1), 0) + 1
    # a shown department must be shared by >= k people of the register (not only of the listed rows)
    import csv
    import io
    reg = list(csv.DictReader(io.StringIO(sy.data)))
    col = next(c for c in reg[0] if c.lower() in ("department", "dept"))
    pop = {}
    for r in reg:
        pop[r[col]] = pop.get(r[col], 0) + 1
    for d in depts:
        assert pop.get(d, 0) >= K, d


def test_contractor_cost_centre_bands_need_k_people(gw):
    client, _mock = gw
    from tests.test_hrms_records import fg
    rows = [[f"W{i}", f"Worker {chr(65 + i)}", f"WO-{i}", "100000", "60000", "1000", "1000", "Finance"]
            for i in range(5)]
    rows += [["W9", "Lone Worker", "WO-9", "100000", "120000", "1000", "1000", "Legal"]]
    _by, out = fg(rows)
    assert dict(out["group_utilisation"].value) == {"Finance": "utilisation 50% to under 80%",
                                                    "other cost centres": "suppressed"}


# =============================================================================================================
# differencing: a durable history per (tenant, app, purpose, dataset), never per conversation
# =============================================================================================================
# Threat model: the external provider sees every outbound request of the tenant, from every conversation, user and
# day; conversation ids are chosen by the caller. Each test below reproduces one bypass an external tester found
# against the earlier per-conversation, in-memory, evicting log (population with 2 people in Legal, k = 5).

FIN_LEGAL = {"filter": [{"field": "department", "op": "in", "value": ["Finance", "Legal"]}], "list": False}
FIN = {"filter": [{"field": "department", "op": "eq", "value": "Finance"}], "list": False}


def _pop12():
    return S.ad_population([person("Finance")] * 12 + [person("Legal")] * 2 + [person("Sales")] * 8)


def _refused(r, reason="differencing"):
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["type"] == "disclosure_control_refused" and err["reason"] == reason
    return err


def test_differencing_in_one_conversation_is_refused(gw):
    client, mock = gw
    sy = _pop12()
    h = {"X-PGW-Conversation-Id": "diff-1"}
    assert post(client, "roster_question", sy.envelope(FIN_LEGAL), headers=h).status_code == 200
    n0 = len(mock.received)
    err = _refused(post(client, "roster_question", sy.envelope(FIN), headers=h))   # 14 - 12 = the two Legal people
    assert len(mock.received) == n0                                               # nothing sent
    assert err["pgw_report"]["records"]["disclosure"]["queries_refused"] == 1
    # repeating the first query is fine (the same records: no new information)
    assert post(client, "roster_question", sy.envelope(FIN_LEGAL), headers=h).status_code == 200


def test_bypass_a_differencing_across_conversations_is_refused(gw):
    """a. "Finance+Legal" then "Finance" in two different conversation ids (or none): 14 and 12 -> Legal = 2."""
    client, mock = gw
    sy = _pop12()
    r = post(client, "roster_question", sy.envelope(FIN_LEGAL), headers={"X-PGW-Conversation-Id": "conv-A"})
    assert r.status_code == 200 and "Matching: 14" in sent_since(mock, len(mock.received) - 1)
    n0 = len(mock.received)
    _refused(post(client, "roster_question", sy.envelope(FIN), headers={"X-PGW-Conversation-Id": "conv-B"}))
    _refused(post(client, "roster_question", sy.envelope(FIN)))                      # no conversation id at all
    assert len(mock.received) == n0


def _fixed_settings(tmp: Path, cfg: dict, mock, master: str):
    from pgw.gateway.settings import Settings
    return Settings(**cfg, ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                    master_key=master, upstream=mock)


def test_bypass_b_history_survives_a_restart_and_a_wrong_key_fails_closed(tmp_path):
    """b. A gateway restart between the two questions: the history is durable (state directory), so the second
    question is refused by a NEW app instance. A restart with a different key cannot read it: 503, nothing sent."""
    import os

    from pgw.gateway.upstream import MockLLMUpstream
    from tests.test_hrms_records import TestClient as _TC
    _c, _m, cfg = make(tmp_path, {"roster_question": str(ROOT / "contracts/roster_question.yaml")})
    master = os.urandom(32).hex()
    sy = _pop12()
    mock1 = MockLLMUpstream("summarise")
    c1 = _TC(create_app(_fixed_settings(tmp_path, cfg, mock1, master)))
    assert post(c1, "roster_question", sy.envelope(FIN_LEGAL)).status_code == 200
    db = Path(cfg["state_dir"]) / disclosure.HISTORY_DB_NAME
    assert db.is_file()
    mock2 = MockLLMUpstream("summarise")
    c2 = _TC(create_app(_fixed_settings(tmp_path, cfg, mock2, master)))      # "restart": new process state
    _refused(post(c2, "roster_question", sy.envelope(FIN)))
    assert mock2.received == []
    mock3 = MockLLMUpstream("summarise")
    c3 = _TC(create_app(_fixed_settings(tmp_path, cfg, mock3, os.urandom(32).hex())))
    r = post(c3, "roster_question", sy.envelope(FIN))
    assert r.status_code == 503 and r.json()["error"]["type"] == "disclosure_store_unavailable"
    assert mock3.received == []


def test_bypass_c_history_is_never_evicted(gw):
    """c. 70 harmless questions, then the pair, in one conversation: the old 64-entry log had evicted the first."""
    client, mock = gw
    sy = _pop12()
    h = {"X-PGW-Conversation-Id": "fill"}
    assert post(client, "roster_question", sy.envelope(FIN_LEGAL), headers=h).status_code == 200
    for i in range(70):                          # 70 distinct predicates, all matching everyone (no new information)
        month = f"{2021 + (3 + i) // 12}-{(3 + i) % 12 + 1:02d}"
        q = {"filter": [{"field": "created_month", "op": "lte", "value": month}], "list": False}
        r = post(client, "roster_question", sy.envelope(q), headers=h)
        assert r.status_code == 200, (i, r.text)
    assert r.json()["pgw_report"]["records"]["disclosure"]["history_queries"] == 71
    n0 = len(mock.received)
    _refused(post(client, "roster_question", sy.envelope(FIN), headers=h))
    assert len(mock.received) == n0


def test_bypass_d_refreshed_export_with_a_new_joiner_is_refused(gw):
    """d. The same question on Monday's and Tuesday's export (one new joiner): 6 then 7 gives the joiner's
    department away. The history is keyed by the dataset (schema + sealed label), not the export's content, and
    compares record sets, so Monday's {A..F} vs Tuesday's {A..G} (difference 1) is refused."""
    client, mock = gw
    monday = [person("Finance")] * 6 + [person("Sales")] * 10
    tuesday = monday + [person("Finance", created=(2026, 10))]          # same people (same synthetic ids) + 1
    q = {"filter": [{"field": "department", "op": "eq", "value": "Finance"}], "list": False}
    r = post(client, "roster_question", S.ad_population(monday).envelope(q), headers={"X-PGW-Conversation-Id": "mon"})
    assert r.status_code == 200 and "Matching: 6" in sent_since(mock, len(mock.received) - 1)
    n0 = len(mock.received)
    _refused(post(client, "roster_question", S.ad_population(tuesday).envelope(q),
                  headers={"X-PGW-Conversation-Id": "tue"}))
    assert len(mock.received) == n0
    # the grouped form of the same question as well (Finance 6 -> 7 among the printed group counts)
    g = {"filter": [], "group_by": "department", "list": False}
    assert post(client, "roster_question", S.ad_population(monday).envelope(g)).status_code == 200
    n0 = len(mock.received)
    _refused(post(client, "roster_question", S.ad_population(tuesday).envelope(g)))
    # a joiner in ANOTHER department: Finance is unchanged, but total 17 next to Monday's 16 would give the joiner
    # count (1) and, with Finance 6 on both days, "the joiner is not in Finance". The audit suppresses Tuesday's
    # record total instead (complementary suppression that considers the history): Finance 6 is answered
    other = monday + [person("Sales", created=(2026, 10))]
    r = post(client, "roster_question", S.ad_population(other).envelope(q))
    assert r.status_code == 200, r.text
    p = parse_roster(sent_since(mock, n0))
    assert p["total"] == "suppressed" and p["matched"] == "6"
    assert r.json()["pgw_report"]["records"]["disclosure"]["cells_suppressed_history"] == 1
    n0 = len(mock.received)
    # an unchanged refresh is answered (same records, same counts)
    assert post(client, "roster_question", S.ad_population(monday).envelope(q)).status_code == 200


def test_differencing_against_a_previously_printed_total_is_refused(gw):
    client, _mock = gw
    sy = S.ad_population([person("Finance")] * 10 + [person("Finance", enabled=False)] * 2 + [person("Sales")] * 6)
    assert post(client, "roster_question", sy.envelope({"filter": [], "list": False}),
                headers={"X-PGW-Conversation-Id": "diff-3"}).status_code == 200
    _refused(post(client, "roster_question", sy.envelope({"filter": [{"field": "enabled", "op": "eq", "value": True}]}),
                  headers={"X-PGW-Conversation-Id": "diff-4"}))


def _dc_variant(tmp_path, **dif):
    """A production gateway whose sealed disclosure_control.yaml has the given differencing settings."""
    _c, _m, cfg = make(tmp_path, {"roster_question": str(ROOT / "contracts/roster_question.yaml")})
    p = _dc_path(cfg)
    raw = yaml.safe_load(p.read_text())
    raw["differencing"].update(dif)
    p.write_text(yaml.safe_dump(raw, sort_keys=False))
    _reseal(cfg)
    from pgw.gateway.upstream import MockLLMUpstream
    from tests.test_hrms_records import TestClient as _TC
    mock = MockLLMUpstream("summarise")
    app = create_app(_settings(tmp_path, cfg, mock))
    return _TC(app), mock, app, cfg


def test_store_failure_is_503_and_nothing_is_sent(gw):
    client, mock = gw
    client.app.state.pgw_disclosure_history._db.close()      # the database becomes unusable
    n0 = len(mock.received)
    r = post(client, "roster_question", _pop12().envelope(FIN))
    assert r.status_code == 503 and r.json()["error"]["type"] == "disclosure_store_unavailable"
    assert len(mock.received) == n0
    assert client.app.state.pgw_disclosure_metrics.c["store_unavailable"] == 1


def test_production_requires_a_persistent_history(tmp_path):
    _c, _m, cfg = make(tmp_path, {"roster_question": str(ROOT / "contracts/roster_question.yaml")})
    cfg = {**cfg, "state_dir": None}
    with pytest.raises(Exception, match="state|PGW_STATE_DIR"):
        create_app(_settings(tmp_path, cfg))


def test_history_store_holds_no_personal_data_in_clear(gw, tmp_path):
    client, _mock = gw
    people = S.unique_combo_population(40, seed=11)
    sy = S.ad_population(people, seed=11)
    for q in ({"filter": [], "group_by": "department", "list": True},
              {"filter": [{"field": "enabled", "op": "eq", "value": True}], "list": True},
              {"filter": [{"field": "department", "op": "in", "value": ["Finance", "Sales", "Engineering"]}],
               "group_by": "account_state", "list": True}):
        r = post(client, "roster_question", sy.envelope(q))
        assert r.status_code in (200, 422), r.text
    store = client.app.state.pgw_disclosure_history
    assert store.sizes()["queries"] >= 1
    db = Path(store.path)
    blob = b"".join(p.read_bytes() for p in (db, Path(str(db) + "-wal")) if p.exists())
    values = [v for kind, v in sy.secrets if isinstance(v, str)] + [n for n, _u in sy.people] + \
             [u for _n, u in sy.people] + [f"E{70000 + i}" for i in range(len(people))]
    assert len(values) > 100
    for v in values:
        for form in {v, v.lower(), v.upper()}:
            assert form.encode("utf-8") not in blob, v
            assert form.encode("utf-16-le") not in blob, v
    for kind, v in sy.secrets:
        if kind == "date":
            assert str(v).encode() not in blob


def _history_scope(store, tenant="t", key=b"k" * 32, kid="k1"):
    return disclosure.HistoryScope(store, tenant, "app", "roster_question", lambda key_id: (key_id or kid, key))


def test_history_unit_record_keys_are_stable_and_keyed():
    plan = plan_for()
    assert records.record_key_fields(plan) == ("username",)        # the stable identifier, not the display name
    store = disclosure.HistoryStore(":memory:")
    monday = S.ad_population([person("Finance")] * 6 + [person("Sales")] * 6)
    run(plan, monday, FIN, _history_scope(store))
    # another tenant (different key) has its own scope: no comparison, no shared hashes
    run(plan, S.ad_population([person("Finance")] * 7 + [person("Sales")] * 6), FIN,
        _history_scope(store, tenant="t2", key=b"x" * 32))
    with pytest.raises(disclosure.DisclosureRefused) as e:
        run(plan, S.ad_population([person("Finance")] * 7 + [person("Sales")] * 6), FIN, _history_scope(store))
    assert e.value.code == "differencing"
    rows = store._db.execute("SELECT sid, keys FROM cell").fetchall()
    by_sid: dict = {}
    for sid, keys in rows:
        by_sid.setdefault(sid, set()).update(keys[i:i + 16] for i in range(0, len(keys), 16))
    a, b = by_sid.values()
    assert not a & b                                               # per-scope keys: hashes do not link
    # a retired (unavailable) key id fails closed
    def nokey(key_id):
        raise RuntimeError("retired")
    with pytest.raises(disclosure.HistoryUnavailable):
        run(plan, monday, FIN, disclosure.HistoryScope(store, "t", "app", "roster_question", nokey))
    # the dataset label is part of the scope
    labelled = plan_for({"dataset": "acme_in"})
    run(labelled, S.ad_population([person("Finance")] * 7 + [person("Sales")] * 6), FIN, _history_scope(store))
    with pytest.raises(ContractError, match="records.dataset"):
        plan_for({"dataset": "Bad Label"})


def test_history_unit_capacity_refuses_only_new_information():
    """The stored-key bound refuses a query that adds information; a repeat of an answered query is still answered."""
    plan = plan_for()
    cfg = disclosure.Config(**{**plan.control.cfg.__dict__, "max_keys": 45})
    plan.control.cfg = cfg
    store = disclosure.HistoryStore(":memory:")
    sy = S.ad_population([person(d) for d in ("Finance", "Sales", "Support") for _ in range(6)])
    rr = run(plan, sy, {"filter": [], "group_by": "department"}, _history_scope(store))
    st = rr.report["disclosure"]
    assert st["history_queries"] == 1 and st["cells_audited"] == 5          # total, matched (same set), 3 groups
    rr = run(plan, sy, FIN, _history_scope(store))                         # the Finance group's set: no new keys
    with pytest.raises(disclosure.DisclosureRefused) as e:   # 18 + 3 x 6 stored; 12 more would exceed 45
        run(plan, sy, {"filter": [{"field": "department", "op": "in", "value": ["Finance", "Sales"]}]},
            _history_scope(store))
    assert e.value.code == "history_full"
    run(plan, sy, {"filter": [], "group_by": "department"}, _history_scope(store))   # repeat: answered
    m = store.metrics.snapshot()
    assert m["answered_repeat"] >= 1 and m["refused_capacity"] == 1


def test_rounding_is_deterministic_and_consistent():
    assert [disclosure.rounded(n, 5) for n in (0, 1, 5, 7, 8, 12, 13, 15, 17, 18)] == [
        "0", "about 5", "about 5", "about 5", "about 10", "about 10", "about 15", "about 15", "about 15", "about 20"]
    assert disclosure.rounded(4, 10) == "about 10" and disclosure.rounded(14, 10) == "about 10"
    assert disclosure.load_config(DC).rounding == 0                       # shipped: off (docs: why)
    plan = plan_for()
    plan.control.rounding = 5
    sy = S.ad_population([person("Finance")] * 12 + [person("Sales")] * 12 + [person("Support")] * 7)
    texts = [leaving(run(plan, sy, {"filter": [], "group_by": "department"})) for _ in range(2)]
    assert texts[0] == texts[1]
    p = parse_roster(texts[0])
    assert p["total"] == "about 30" and p["matched"] == "about 30"
    assert p["groups"] == {"Finance": "about 10", "Sales": "about 10", "Support": "about 5"}
    rr = run(plan, sy, {"filter": [{"field": "department", "op": "eq", "value": "Sales"}]})
    assert "Matching: about 10" in leaving(rr) and rr.report["disclosure"]["counts_rounded"] >= 2
    # markers are never rounded away: a small cell still reads "fewer than k"
    small = S.ad_population([person("Finance")] * 12 + [person("Legal")] * 2)
    assert "Matching: fewer than 5" in leaving(run(plan, small, {"filter": [{"field": "department", "op": "eq",
                                                                               "value": "Legal"}]}))


def test_unsafe_history_configs_are_refused(tmp_path):
    for text, msg in (("max_cells_per_scope: 0", "integer between 1"), ("ttl_s: 60", "unknown keys"),
                      ("max_queries_per_scope: 1000", "unknown keys"),
                      ("separate_purposes: true", "written justification")):
        raw = DC.read_text()
        raw = (raw.replace("separate_purposes: false", text) if text.startswith("separate")
               else raw.replace("max_cells_per_scope: 100000", text))
        p = tmp_path / "disclosure_control.yaml"
        p.write_text(raw)
        with pytest.raises(SchemaError, match=msg):
            disclosure.load_config(p)


# =============================================================================================================
# sealed configuration
# =============================================================================================================

def _reseal(cfg):
    integrity.seal(Path(cfg["integrity_manifest"]), sign_key=signing_key()[0])


def _dc_path(cfg) -> Path:
    return Path(cfg["registry_path"]).parent / "rules/disclosure_control.yaml"


def test_tampered_k_stops_startup(tmp_path):
    _c, _m, cfg = make(tmp_path, {"roster_question": str(ROOT / "contracts/roster_question.yaml")})
    p = _dc_path(cfg)
    p.write_text(p.read_text().replace("min_group_size: 5", "min_group_size: 50"))
    with pytest.raises(integrity.IntegrityError, match="differs from its sealed hash"):
        create_app(_settings(tmp_path, cfg))


def test_missing_disclosure_config_stops_startup(tmp_path):
    _c, _m, cfg = make(tmp_path, {"roster_question": str(ROOT / "contracts/roster_question.yaml")})
    _dc_path(cfg).unlink()
    _reseal(cfg)
    with pytest.raises(ContractError, match="disclosure control config"):
        create_app(_settings(tmp_path, cfg))


def test_k_below_3_is_refused_even_when_sealed(tmp_path):
    _c, _m, cfg = make(tmp_path, {"roster_question": str(ROOT / "contracts/roster_question.yaml")})
    p = _dc_path(cfg)
    p.write_text(p.read_text().replace("min_group_size: 5", "min_group_size: 2"))
    _reseal(cfg)
    with pytest.raises(ContractError, match="below 3 needs a sealed min_group_size_override"):
        create_app(_settings(tmp_path, cfg))
    # with a sealed override and a justification it starts, and k = 2 is what is enforced
    p.write_text(p.read_text().replace("min_group_size: 2", "min_group_size: 2\nmin_group_size_override:\n"
                                       "  justification: \"pilot tenant of 12 people; DPO approval DPO-2026-14\""))
    _reseal(cfg)
    app = create_app(_settings(tmp_path, cfg))
    assert app is not None


@pytest.mark.parametrize("text,msg", [
    ("min_group_size: 1", "integer between 2"),
    ("min_group_size: 5\nmin_group_size_override: {justification: 'not needed at all here really'}",
     "applies only to a min_group_size below 3"),
    ("min_group_size: 2\nmin_group_size_override: {justification: short}", "written justification"),
    ("listing: everything", "listing"),
])
def test_unsafe_disclosure_configs_are_refused(tmp_path, text, msg):
    key = text.split(":", 1)[0]
    raw = DC.read_text().replace({"min_group_size": "min_group_size: 5", "listing": "listing: coarsen"}[key], text)
    p = tmp_path / "disclosure_control.yaml"
    p.write_text(raw)
    with pytest.raises(SchemaError, match=msg):
        disclosure.load_config(p)


def test_schema_may_only_add_quasi_identifiers_and_roll_ups_are_checked(tmp_path):
    from pgw.product.fieldschema import load_schema
    schema = load_schema(ROOT / "registry/schemas/ad_user.yaml")
    cfg = disclosure.load_config(DC)
    qis = disclosure.schema_control(cfg, schema).qis
    assert {"department", "enabled", "account_state", "created_month", "last_logon_month", "pin_district"} <= set(qis)
    bad = dict(cfg.schemas)
    for block, msg in (({"quasi_identifiers": ["nope"]}, "not a field"),
                       ({"hierarchies": {"created_month": [{"x": "y"}]}}, "KEEP enumeration"),
                       ({"hierarchies": {"department": [{"Finance": "Unit 12345"}]}}, "4\\+ digits"),
                       ({"hierarchies": {"department": [{"Finance": "Maternity cover"}]}}, "special-category")):
        bad["ad_user"] = block
        with pytest.raises(SchemaError, match=msg):
            disclosure.schema_control(disclosure.Config(**{**cfg.__dict__, "schemas": bad}), schema)


def test_contract_may_raise_k_but_not_lower_it():
    assert plan_for({"min_group_size": 9}).k == 9
    with pytest.raises(ContractError, match="may only raise"):
        plan_for({"min_group_size": 3})


def test_report_counts_carry_no_values(gw):
    client, _mock = gw
    people = S.unique_combo_population(30, seed=5)
    sy = S.ad_population(people, seed=5)
    r = post(client, "roster_pin", sy.envelope({"filter": [], "group_by": "pin_district", "list": True}),
             headers={"X-PGW-Conversation-Id": "rep-1"})
    assert r.status_code == 200
    dc = r.json()["pgw_report"]["records"]["disclosure"]
    blob = json.dumps(dc)
    for p in people:
        assert p["pin"] not in blob and p["pin"][:3] + "xxx" not in blob
    assert set(dc) >= {"min_group_size", "cells_suppressed", "rows_coarsened", "queries_refused"}
