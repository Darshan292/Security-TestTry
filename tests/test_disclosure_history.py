"""The disclosure-control history, round 2 (pgw.product.disclosure.HistoryStore + pgw.product.setaudit;
docs/HRMS_CONTRACTS.md "Small groups", SECURITY_BOUNDARY.md §12).

Each test reproduces a bypass an external tester found against the previous round (pairwise comparison of exactly
printed counts only; raw usernames as record keys; a per-scope question cap that locked the whole tenant out and a
reset that reopened every gap; filter values and timestamps in clear; a deletable history) and asserts that it is
now refused, or answered with nothing derivable. Production mode, signed config, attested payloads (tests/sealed.py);
the assertions read the exact bytes the mock provider received. SYNTHETIC data only (tests/hrms_synth.py).
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from pathlib import Path

import pytest

from pgw.gateway.app import create_app
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product import disclosure
from tests import hrms_synth as S
from tests.test_disclosure_control import (FIN, FIN_LEGAL, ROOT, _dc_variant, _fixed_settings, _pop12, _refused,
                                           gw, parse_roster, person, sent_since)
from tests.test_hrms_records import TestClient, make, post

__all__ = ["gw"]   # the fixture, re-exported for this module


def _q(*depts, op="in", group_by=None):
    v = list(depts) if op == "in" else depts[0]
    return {"filter": [{"field": "department", "op": op, "value": v}], "group_by": group_by, "list": False}


def _answer(client, mock, purpose, sy, q):
    n0 = len(mock.received)
    r = post(client, purpose, sy.envelope(q))
    assert r.status_code == 200, r.text
    return parse_roster(sent_since(mock, n0)), r.json()["pgw_report"]["records"]["disclosure"]


# =============================================================================================================
# 1. a suppressed cell recovered by subtraction across queries (and 5: three-question combinations)
# =============================================================================================================

def test_suppressed_cell_recovered_by_subtraction_is_refused(gw):
    """Tester: (Finance, Legal, Sales) by department -> 22, Finance 12, Legal and Sales suppressed; then
    "Sales" -> 8; Legal = 22 - 12 - 8 = 2. The history now keeps the suppressed cells' record sets as well and audits
    every answer as linear constraints over record sets: the second question is refused, nothing is sent."""
    client, mock = gw
    sy = _pop12()
    p, _ = _answer(client, mock, "roster_question", sy, _q("Finance", "Legal", "Sales", group_by="department"))
    assert p["matched"] == "22" and p["groups"] == {"Finance": "12", "Legal": "suppressed", "Sales": "suppressed"}
    n0 = len(mock.received)
    _refused(post(client, "roster_question", sy.envelope(_q("Sales", op="eq"))))
    _refused(post(client, "roster_question", sy.envelope(_q("Finance", "Sales"))))       # 20: 22 - 20 = 2 as well
    assert len(mock.received) == n0
    # a question that adds nothing about Legal is still answered
    p, _ = _answer(client, mock, "roster_question", sy, _q("Legal", op="eq"))
    assert p["matched"] == "fewer than 5"


def test_reverse_order_suppresses_more_instead_of_leaking(gw):
    """Sales first (8), then the grouped question: printing Finance 12 next to 22 would give Legal = 2 with the
    earlier 8, so complementary suppression now considers the history and withholds Finance as well."""
    client, mock = gw
    sy = _pop12()
    p, _ = _answer(client, mock, "roster_question", sy, _q("Sales", op="eq"))
    assert p["matched"] == "8"
    p, dc = _answer(client, mock, "roster_question", sy, _q("Finance", "Legal", "Sales", group_by="department"))
    assert p["matched"] == "22" and p["groups"]["Legal"] == "suppressed"
    assert p["groups"]["Finance"] == "suppressed" and dc["cells_suppressed_history"] == 1


def test_the_total_is_not_printed_when_the_subtraction_is_that_easy(gw):
    """(Finance, Sales) -> 20 with the total withheld (22 - 20 = Legal); then "Finance": its total 22 next to the
    earlier 20 would give Legal = 2, so the record total of the second answer is suppressed by the history."""
    client, mock = gw
    sy = _pop12()
    p, _ = _answer(client, mock, "roster_question", sy, _q("Finance", "Sales"))
    assert p["matched"] == "20" and p["total"] == "suppressed"
    p, dc = _answer(client, mock, "roster_question", sy, _q("Finance", op="eq"))
    assert p["matched"] == "12" and p["total"] == "suppressed" and dc["cells_suppressed_history"] == 1


def test_three_question_combination_is_refused(gw):
    """Legal 2, Sales 3, Support 3: (Legal, Sales) 5, (Sales, Support) 6, (Legal, Support) 5. No two answers differ
    by fewer than k records; the three together give Legal = 2. The third is refused."""
    client, mock = gw
    sy = S.ad_population([person("Finance")] * 12 + [person("Legal")] * 2 + [person("Sales")] * 3
                         + [person("Support")] * 3)
    assert _answer(client, mock, "roster_question", sy, _q("Legal", "Sales"))[0]["matched"] == "5"
    assert _answer(client, mock, "roster_question", sy, _q("Sales", "Support"))[0]["matched"] == "6"
    n0 = len(mock.received)
    _refused(post(client, "roster_question", sy.envelope(_q("Legal", "Support"))))
    assert len(mock.received) == n0


def test_second_contract_over_the_same_data_shares_the_history(gw):
    """roster_question and roster_pin are two purposes (two contracts) over the same ad_user dataset of the same
    tenant: one history, so the pair split across them is refused."""
    client, mock = gw
    sy = _pop12()
    assert post(client, "roster_question", sy.envelope(FIN_LEGAL)).status_code == 200
    n0 = len(mock.received)
    _refused(post(client, "roster_pin", sy.envelope(FIN)))
    assert len(mock.received) == n0


# =============================================================================================================
# 2. record identity normalisation
# =============================================================================================================

VARIANTS = {"upper": str.upper, "domain": lambda u: "CORP\\" + u, "upn": lambda u: u + "@corp.kestrel.example",
            "spaces": lambda u: f"  {u} ", "fullwidth": lambda u: "".join(chr(ord(c) + 0xFEE0) for c in u),
            "zero_width": lambda u: u[:2] + "​" + u[2:], "mixed": lambda u: "Corp\\" + u.title() + "@X.Example"}


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_username_variants_are_the_same_people(gw, variant):
    """Tester: upper-casing the usernames (or adding CORP\\) made the same people look new, so the differencing pair
    over two exports went through. The record key is now the sealed, normalised identity."""
    client, mock = gw
    sy = _pop12()
    assert post(client, "roster_question", sy.envelope(FIN_LEGAL)).status_code == 200
    f = VARIANTS[variant]
    for rec in sy.data:
        rec["sAMAccountName"] = f(rec["sAMAccountName"])
    n0 = len(mock.received)
    _refused(post(client, "roster_question", sy.envelope(FIN)))
    assert len(mock.received) == n0


def test_normalise_identity_unit():
    d = disclosure.DEFAULT_NORM
    base = disclosure.normalise_identity("asha.r", d)
    for v in ("ASHA.R", "CORP\\asha.r", "asha.r@corp.example", " asha.r\t", "ａｓｈａ.ｒ", "as​ha.r",
              "CORP\\ASHA.R@corp.example", "asha.r﻿"):
        assert disclosure.normalise_identity(v, d) == base, v
    assert disclosure.normalise_identity("asha.s", d) != base
    # numeric ids: leading zeros are significant unless the sealed config says otherwise
    assert disclosure.normalise_identity("00123", d) != disclosure.normalise_identity("123", d)
    z = {**d, "strip_leading_zeros": True}
    assert disclosure.normalise_identity("00123", z) == disclosure.normalise_identity("123", z) == "123"
    assert disclosure.normalise_identity("000", z) == "0"
    # every option can be switched off in the sealed config (exact ids)
    off = dict.fromkeys(disclosure.NORM_OPTIONS, False)
    assert disclosure.normalise_identity("CORP\\Asha", off) == "CORP\\Asha"


def test_record_key_config_is_checked():
    from pgw.product.fieldschema import SchemaError, load_schema
    schema = load_schema(ROOT / "registry/schemas/ad_user.yaml")
    cfg = disclosure.load_config(ROOT / "registry/rules/disclosure_control.yaml")
    rk = disclosure.schema_control(cfg, schema).record_key
    assert rk.fields == ("username",) and rk.norm["username"]["casefold"] is True
    for block, msg in (({"record_key": {"fields": ["department"]}}, "not an identity"),
                       ({"record_key": {"fields": ["username"], "normalise": {"username": {"lower": True}}}},
                        "unknown keys"),
                       ({"record_key": {"fields": []}}, "non-empty list")):
        bad = disclosure.Config(**{**cfg.__dict__, "schemas": {"ad_user": block}})
        with pytest.raises(SchemaError, match=msg):
            disclosure.schema_control(bad, schema)


# =============================================================================================================
# 3. capacity: per-caller rate limits, no tenant lockout, compaction that reopens nothing
# =============================================================================================================

def _distinct(i: int) -> dict:
    """Distinct questions about the same 22 people that add information (each a new created-month cut)."""
    return {"filter": [{"field": "created_month", "op": "lte", "value": f"20{10 + i:02d}-06"}], "list": False}


def _months_population():
    return S.ad_population([person("Finance", created=(2010 + i // 2, 3)) for i in range(30)])


def test_rate_limit_refuses_only_new_questions_of_that_caller(tmp_path):
    """Tester: 1,000 scripted questions (~17 s) filled the history and locked the tenant out until a reset. Now a
    caller is limited in NEW questions per minute and per day; questions already answered are still answered, and
    nothing stops the tenant as a whole."""
    client, mock, app, _cfg = _dc_variant(tmp_path, per_caller={"new_queries_per_minute": 3,
                                                                "new_queries_per_day": 1000})
    sy = _months_population()
    for i in range(3):
        assert post(client, "roster_question", sy.envelope(_distinct(i))).status_code == 200
    n0 = len(mock.received)
    r = post(client, "roster_question", sy.envelope(_distinct(3)))
    assert r.status_code == 429 and r.json()["error"]["reason"] == "rate_limited"
    assert len(mock.received) == n0
    for i in range(3):        # repeats: answered (no new information), never counted
        assert post(client, "roster_question", sy.envelope(_distinct(i))).status_code == 200
    m = app.state.pgw_disclosure_metrics.snapshot()
    assert m["refused_rate"] == 1 and m["answered"] == 3 and m["answered_repeat"] == 3


def test_daily_limit_and_compaction_that_reopens_no_gap(tmp_path):
    client, mock, app, cfg = _dc_variant(tmp_path, per_caller={"new_queries_per_minute": 100,
                                                               "new_queries_per_day": 2})
    sy = _pop12()
    assert post(client, "roster_question", sy.envelope(FIN_LEGAL)).status_code == 200
    assert post(client, "roster_question", sy.envelope(_q("Sales", op="eq"))).status_code == 200
    r = post(client, "roster_question", sy.envelope(_q("Legal", op="eq")))
    assert r.status_code == 429 and r.json()["error"]["reason"] == "rate_limited"
    assert post(client, "roster_question", sy.envelope(FIN_LEGAL)).status_code == 200      # a repeat: answered
    # the deliberate, logged admin action: compaction drops the query log and the callers' counters, KEEPS every
    # record set: the caller may ask again, and the differencing pair is still refused (no gap reopened)
    db = str(Path(cfg["state_dir"]) / disclosure.HISTORY_DB_NAME)
    store = disclosure.HistoryStore(db)
    with pytest.raises(ValueError, match="written reason"):
        store.compact("acme", "ad_user", "because")
    assert store.compact("acme", "ad_user", "CHG-1234 quarterly review by DPO") == 3      # 2 new + 1 repeat
    (entry,) = store.admin_log()
    assert entry["action"] == "compact" and entry["reason"].startswith("CHG-1234")
    store.close()
    app.state.pgw_disclosure_history._burst.clear()
    n0 = len(mock.received)
    _refused(post(client, "roster_question", sy.envelope(FIN)))          # 14 - 12 = 2: still refused
    assert len(mock.received) == n0
    assert post(client, "roster_question", sy.envelope(_q("Legal", op="eq"))).status_code == 200
    assert disclosure._main(["history", "list", "--db", db]) == 0


def test_capacity_refuses_only_new_information(tmp_path):
    client, mock, app, _cfg = _dc_variant(tmp_path, max_cells_per_scope=3)
    sy = _pop12()
    assert post(client, "roster_question", sy.envelope(FIN_LEGAL)).status_code == 200      # total + matched: 2
    assert post(client, "roster_question", sy.envelope(_q("Sales", op="eq"))).status_code == 200   # +1
    _refused(post(client, "roster_question", sy.envelope(_q("Legal", op="eq"))), reason="history_full")
    assert post(client, "roster_question", sy.envelope(FIN_LEGAL)).status_code == 200      # a repeat: answered
    assert app.state.pgw_disclosure_metrics.snapshot()["refused_capacity"] == 1


# =============================================================================================================
# 4. storage hygiene and startup integrity
# =============================================================================================================

def test_history_file_modes_and_no_clear_filter_values_or_timestamps(gw):
    client, mock = gw
    sy = S.ad_population([person("Finance", created=(2021, 4))] * 12 + [person("Legal", created=(2019, 3))] * 6
                         + [person("Sales", created=(2023, 11))] * 8)
    queries = [FIN_LEGAL, _q("Sales", op="eq", group_by="account_state"),
               {"filter": [{"field": "created_month", "op": "gte", "value": "2019-03"}], "group_by": "department"},
               {"filter": [{"field": "enabled", "op": "eq", "value": True}], "list": True}]
    for q in queries:
        assert post(client, "roster_question", sy.envelope(q)).status_code in (200, 422)
    store = client.app.state.pgw_disclosure_history
    store._db.execute("PRAGMA wal_checkpoint(PASSIVE)")
    db = Path(store.path)
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700
    for side in (Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        if side.exists():
            assert stat.S_IMODE(side.stat().st_mode) & 0o077 == 0, side
    blob = b"".join(p.read_bytes() for p in (db, Path(str(db) + "-wal")) if p.exists())
    for clear in ("Finance", "Legal", "Sales", "2019-03", "2021-04", "created_month", "department", "account_state",
                  "enabled", '"eq"'):
        assert clear.encode() not in blob, clear
        assert clear.encode("utf-16-le") not in blob, clear
    now = int(time.time())
    for prefix in {str(now)[:6], str(now - 3600)[:6]}:
        assert prefix.encode() not in blob                 # no epoch-seconds timestamp in decimal
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        for col in con.execute(f"PRAGMA table_info({table})"):   # noqa: S608 - table names from sqlite_master
            assert col[2].upper() != "REAL", (table, col)
    days = [d for (d,) in con.execute("SELECT day FROM query")] + [d for (d,) in
                                                                    con.execute("SELECT created_day FROM scope")]
    assert days and all(d == now // 86400 or d == now // 86400 - 1 for d in days)
    preds = [p for (p,) in con.execute("SELECT predicate FROM query")]
    assert all(isinstance(p, bytes) and len(p) == 16 for p in preds)
    con.close()


def _restart(tmp_path, cfg, master):
    return create_app(_fixed_settings(tmp_path, cfg, MockLLMUpstream("summarise"), master))


def _setup(tmp_path):
    _c, _m, cfg = make(tmp_path, {"roster_question": str(ROOT / "contracts/roster_question.yaml")})
    master = os.urandom(32).hex()
    app = _restart(tmp_path, cfg, master)
    assert post(TestClient(app), "roster_question", _pop12().envelope(FIN_LEGAL)).status_code == 200
    db = Path(cfg["state_dir"]) / disclosure.HISTORY_DB_NAME
    hwm = json.loads((Path(cfg["state_dir"]) / disclosure.HWM_NAME).read_text())
    assert hwm["seq"] >= 1 and hwm["db_id"] == app.state.pgw_disclosure_history.db_id
    return cfg, master, app, db


def _drop(db: Path) -> None:
    for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        if p.exists():
            p.unlink()


def test_deleting_the_history_stops_startup_until_a_logged_recovery(tmp_path):
    """Tester: deleting the database silently reset every history. Now the state directory's high-water mark
    records the database's id and commit sequence; a missing database stops startup; only the deliberate
    `history recover` (logged, written reason) starts again."""
    cfg, master, app, db = _setup(tmp_path)
    app.state.pgw_disclosure_history.close()
    _drop(db)
    with pytest.raises(disclosure.HistoryIntegrityError, match="missing"):
        _restart(tmp_path, cfg, master)
    _drop(db)                                    # the refused start must not have left a usable empty history
    db.write_bytes(b"")                          # replaced by an empty file: the same
    with pytest.raises(disclosure.HistoryIntegrityError, match="missing"):
        _restart(tmp_path, cfg, master)
    with pytest.raises(SystemExit):
        disclosure._main(["history", "recover", "--db", str(db), "--state-dir", cfg["state_dir"]])  # no reason
    assert disclosure._main(["history", "recover", "--db", str(db), "--state-dir", cfg["state_dir"], "--new",
                             "--reason", "INC-77: disk lost, no backup; DPO approved"]) == 0
    app2 = _restart(tmp_path, cfg, master)
    log = app2.state.pgw_disclosure_history.admin_log()
    assert log[-1]["action"] == "recover_new" and log[-1]["reason"].startswith("INC-77")


def test_replaced_or_older_history_or_missing_mark_stops_startup(tmp_path):
    cfg, master, app, db = _setup(tmp_path)
    store = app.state.pgw_disclosure_history
    old = tmp_path / "old.sqlite"
    with sqlite3.connect(old) as dst:
        store._db.backup(dst)                     # a consistent copy of the history after one answer
    assert post(TestClient(app), "roster_question", _pop12().envelope(_q("Sales", op="eq"))).status_code == 200
    store.close()
    # an older copy restored in place: its sequence is below the mark
    newer = tmp_path / "newer.sqlite"
    with sqlite3.connect(db) as src, sqlite3.connect(newer) as dst:
        src.backup(dst)
    _drop(db)
    db.write_bytes(old.read_bytes())
    with pytest.raises(disclosure.HistoryIntegrityError, match="older"):
        _restart(tmp_path, cfg, master)
    # another history file (other id)
    _drop(db)
    other = disclosure.HistoryStore(str(tmp_path / "other.sqlite"))
    other.close()
    db.write_bytes((tmp_path / "other.sqlite").read_bytes())
    with pytest.raises(disclosure.HistoryIntegrityError, match="replaced"):
        _restart(tmp_path, cfg, master)
    # the right file again, but the mark deleted
    _drop(db)
    db.write_bytes(newer.read_bytes())
    os.chmod(db, 0o600)
    mark = Path(cfg["state_dir"]) / disclosure.HWM_NAME
    saved = mark.read_bytes()
    mark.unlink()
    with pytest.raises(disclosure.HistoryIntegrityError, match="no high-water mark"):
        _restart(tmp_path, cfg, master)
    mark.write_bytes(saved)
    app3 = _restart(tmp_path, cfg, master)        # file and mark agree: starts, and still refuses the pair
    _refused(post(TestClient(app3), "roster_question", _pop12().envelope(FIN)))
    # a corrupt mark: refused as well
    mark.write_text("{not json")
    with pytest.raises(disclosure.HistoryIntegrityError, match="corrupt"):
        _restart(tmp_path, cfg, master)
