"""Round 9, store and identity: record-key identity is a strict character set; rate limits are per (app, end user),
per app and per scope, shared by every process, and meter refused queries; the history store is bound by an HMAC
high-water mark plus a rollback copy, recovery needs an operator and a stopped gateway, and a locked database fails
closed instead of blocking. Production mode, signed config, attested payloads (tests/sealed.py); SYNTHETIC data only.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import pytest

from pgw import enduser
from pgw.gateway import rollback
from pgw.gateway.app import create_app
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product import disclosure
from tests import hrms_synth as S
from tests.sealed import attest
from tests.test_disclosure_control import (FIN, FIN_LEGAL, ROOT, _dc_variant, _fixed_settings, _pop12, _refused, person)
from tests.test_hrms_records import KEY, MASTER, TestClient, body_of, make, post

D = disclosure.DEFAULT_NORM
CONTRACT = {"roster_question": str(ROOT / "contracts/roster_question.yaml")}


def _post_as(client, user, envelope, purpose="roster_question", key=KEY, tamper=None, **extra):
    """The request as the workspace sends it: the body bytes are signed with the app key (pgw.enduser)."""
    body = body_of(envelope)
    raw = json.dumps(body).encode()
    h = {"Authorization": f"Bearer {key}", "X-PGW-Purpose": purpose, "Content-Type": "application/json",
         **attest(purpose, body, contract="roster_question")}
    if user is not None:
        h.update(enduser.sign(KEY, purpose, user, raw))
    h.update(extra)
    if tamper:
        h = tamper(h)
    return client.post("/v1/chat/completions", content=raw, headers=h)


def _q(i):
    return {"filter": [{"field": "created_month", "op": "lte", "value": f"20{10 + i:02d}-06"}], "list": False}


def _months():
    """60 people, six per creation year: _q(i) cuts differ by six people (>= k), so each is new and not differencing."""
    return S.ad_population([person("Finance", created=(2010 + i // 6, 3)) for i in range(60)])


# =============================================================================================================
# 3. identity: a strict allowed character set, one explicit blank list, no confusables table
# =============================================================================================================

BASE = "pooja.nair01"


def spellings(u):
    return {"upper": u.upper(), "hangul filler U+3164": u + "\u3164", "halfwidth hangul filler U+FFA0": u + "\uffa0",
            "hangul choseong filler U+115F": u + "\u115f", "braille blank U+2800": u + "\u2800",
            "combining grapheme joiner U+034F": u[:3] + "\u034f" + u[3:], "variation selector U+FE0F": u + "\ufe0f",
            "variation selector supplement": u + "\U000e0100", "trailing dot": u + ".", "trailing dots": u + "...",
            "slash form": "CORP/" + u, "backslash form": "CORP\\" + u, "nested domain": "CORP\\SUB\\" + u,
            "mixed slashes": "corp.local\\sub/" + u, "upn": u + "@corp.example", "upn and domain": "C\\" + u + "@x.y",
            "ZWSP": u[:3] + "\u200b" + u[3:], "soft hyphen": u[:3] + "\u00ad" + u[3:], "BOM": "\ufeff" + u,
            "RTL override": "\u202e" + u, "tag char": u + "\U000e0041",
            "fullwidth": "".join(chr(ord(c) + 0xFEE0) if "a" <= c <= "z" or "0" <= c <= "9" else c for c in u),
            "whitespace": " \t" + u + " \n"}


SAME = spellings(BASE)
REFUSED = {"cyrillic o": BASE.replace("o", "о"), "cyrillic a": BASE.replace("a", "а"),
           "greek omicron": BASE.replace("o", "ο"), "dotless i": BASE.replace("i", "ı"),
           "combining acute": BASE + "́", "percent-encoded @": BASE + "%40corp", "quote": BASE + "'",
           "apostrophe form": "o'brien", "emoji": BASE + "\U0001f600", "empty": "", "only blanks": "ㅤ⠀ ",
           "only dots": "...", "domain only": "CORP\\", "upn only": "@corp.example", "comma": "a,b",
           "ZWJ-joined emoji": "‍", "bool": True, "float": 1.5, "empty list": []}


@pytest.mark.parametrize("name", sorted(SAME))
def test_identity_spellings_of_one_person_are_one_key(name):
    assert disclosure.normalise_identity(SAME[name], D) == BASE


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_identity_outside_the_allowed_set_is_refused_never_guessed(name):
    with pytest.raises(disclosure.IdentityInvalid):
        disclosure.normalise_identity(REFUSED[name], D)


def test_identity_charsets_per_key_spec():
    num = {**D, "charset": "numeric", "strip_leading_zeros": True}
    assert disclosure.normalise_identity("００１２３", num) == "123"          # fullwidth digits fold under NFKC
    for bad in ("12a", "CORP\\123", "1.5", "٣٤"):                  # Arabic-Indic digits are not [0-9]
        with pytest.raises(disclosure.IdentityInvalid):
            disclosure.normalise_identity(bad, num)
    upn = {**D, "charset": "email", "strip_upn_suffix": False}
    assert disclosure.normalise_identity("CORP\\Asha.R@Corp.Example", upn) == "asha.r@corp.example"
    with pytest.raises(disclosure.IdentityInvalid):
        disclosure.normalise_identity("asha", upn)
    exact = {**dict.fromkeys(disclosure.NORM_OPTIONS, False), "charset": "username"}
    assert disclosure.normalise_identity("Asha", exact) == "Asha"          # case kept when casefold is off
    with pytest.raises(disclosure.IdentityInvalid):
        disclosure.normalise_identity("Аsha", exact)                  # Cyrillic capital A


def test_record_key_charset_is_sealed_and_checked():
    from pgw.product.fieldschema import SchemaError, load_schema
    schema = load_schema(ROOT / "registry/schemas/ad_user.yaml")
    cfg = disclosure.load_config(ROOT / "registry/rules/disclosure_control.yaml")
    ok = disclosure.Config(**{**cfg.__dict__, "schemas": {"ad_user": {"record_key": {
        "fields": ["username"], "normalise": {"username": {"charset": "numeric"}}}}}})
    assert disclosure.schema_control(ok, schema).record_key.norm["username"]["charset"] == "numeric"
    bad = disclosure.Config(**{**cfg.__dict__, "schemas": {"ad_user": {"record_key": {
        "fields": ["username"], "normalise": {"username": {"charset": "anything"}}}}}})
    with pytest.raises(SchemaError, match="charset"):
        disclosure.schema_control(bad, schema)


def _mk(f):
    """The 22-person population with readable usernames pooja.nairNN, spelled by f(username)."""
    sy = _pop12()
    for i, d in enumerate(sy.data):
        d["sAMAccountName"] = f(f"pooja.nair{i:02d}")
    return sy


@pytest.fixture
def gw(tmp_path):
    client, mock, _cfg = make(tmp_path, CONTRACT)
    return client, mock


@pytest.mark.parametrize("name", sorted(SAME))
def test_spelling_variants_cannot_evade_the_differencing_pair(gw, name):
    """Tester t_a2: the second export spells every username differently. Identical people: the pair is refused."""
    client, mock = gw
    base = _mk(lambda u: u)
    assert post(client, "roster_question", base.envelope(FIN_LEGAL)).status_code == 200
    var = _mk(lambda u: spellings(u)[name])
    n0 = len(mock.received)
    _refused(post(client, "roster_question", var.envelope(FIN)))
    assert len(mock.received) == n0


LOOKALIKE = {"cyrillic o": lambda u: u.replace("o", "\u043e"), "cyrillic a": lambda u: u.replace("a", "\u0430"),
             "greek omicron": lambda u: u.replace("o", "\u03bf"), "dotless i": lambda u: u.replace("i", "\u0131"),
             "combining acute": lambda u: u + "\u0301"}


@pytest.mark.parametrize("name", sorted(LOOKALIKE))
def test_look_alike_usernames_refuse_the_dataset(gw, name):
    client, mock = gw
    r = post(client, "roster_question", _mk(LOOKALIKE[name]).envelope(FIN))
    _refused(r, "invalid_record_key")
    assert mock.received == []
    assert "pooja" not in r.text and "nair" not in r.text


@pytest.mark.parametrize("what", ["missing key", "null", "empty string", "blanks only", "one bad of many"])
def test_any_missing_empty_or_invalid_key_refuses_the_whole_dataset(gw, what):
    client, mock = gw
    sy = _mk(lambda u: u)
    if what == "missing key":
        del sy.data[5]["sAMAccountName"]
    elif what == "null":
        sy.data[5]["sAMAccountName"] = None
    elif what == "empty string":
        sy.data[5]["sAMAccountName"] = ""
    elif what == "blanks only":
        sy.data[5]["sAMAccountName"] = "ㅤ⠀"
    else:
        sy.data[19]["sAMAccountName"] = sy.data[19]["sAMAccountName"].replace("a", "а", 1)
    r = post(client, "roster_question", sy.envelope(FIN))
    err = _refused(r, "invalid_record_key")
    assert mock.received == [] and "records have a missing, empty or invalid record key" in err["message"]
    assert "pooja" not in r.text
    m = client.app.state.pgw_disclosure_metrics.snapshot()
    assert m["refused_identity"] == 1 and m["answered"] == 0


# =============================================================================================================
# 4. rate limits: per (app, end user), per app, per scope; refused queries metered; shared across processes
# =============================================================================================================

def test_end_user_assertion_is_hmac_bound_and_checked(tmp_path):
    client, mock, _app, _cfg = _dc_variant(tmp_path)
    sy, env = _pop12(), _pop12().envelope(FIN_LEGAL)
    assert _post_as(client, "sso-aaaa", env).status_code == 200
    bad = lambda h: {**h, enduser.HEADER_SIG: "0" * 64}                      # noqa: E731
    stale = lambda h: {**h, **enduser.sign(KEY, "roster_question", "sso-aaaa", b"x", ts=int(time.time()) - 4000)}  # noqa: E731
    other = lambda h: {**h, enduser.HEADER_USER: "sso-bbbb"}                  # noqa: E731  (identity swapped)
    partial = lambda h: {k: v for k, v in h.items() if k != enduser.HEADER_SIG}   # noqa: E731
    wrongkey = lambda h: {**h, **enduser.sign("not-the-app-key", "roster_question", "sso-aaaa", b"x")}  # noqa: E731
    n0 = len(mock.received)
    for t in (bad, stale, other, partial, wrongkey):
        r = _post_as(client, "sso-aaaa", env, tamper=t)
        assert r.status_code == 401 and r.json()["error"]["type"] == "end_user_invalid"
    # signed for another purpose / another body
    h = enduser.sign(KEY, "roster_pin", "sso-aaaa", json.dumps(body_of(env)).encode())
    r = _post_as(client, "sso-aaaa", env, tamper=lambda hh: {**hh, **h})
    assert r.status_code == 401
    r = _post_as(client, "sso-aaaa", env, tamper=lambda hh: {**hh, enduser.HEADER_USER: "bad id with spaces"})
    assert r.status_code == 401
    assert len(mock.received) == n0                                          # nothing reached the provider
    _ = sy


def test_user_cap_is_a_fair_share_one_user_cannot_exhaust_the_others(tmp_path):
    client, mock, app, _cfg = _dc_variant(tmp_path, per_caller={
        "new_queries_per_user_per_minute": 2, "new_queries_per_minute": 20, "new_queries_per_scope_per_minute": 40})
    sy = _months()
    for i in range(2):
        assert _post_as(client, "sso-alice", sy.envelope(_q(i))).status_code == 200
    n0 = len(mock.received)
    r = _post_as(client, "sso-alice", sy.envelope(_q(2)))
    assert r.status_code == 429 and r.json()["error"]["reason"] == "rate_limited"
    assert 1 <= int(r.headers["Retry-After"]) <= 60
    assert len(mock.received) == n0
    for i in range(3, 5):                                                   # bob is unaffected by alice
        assert _post_as(client, "sso-bob", sy.envelope(_q(i))).status_code == 200
    for i in range(2):                                                      # alice's repeats are still answered
        assert _post_as(client, "sso-alice", sy.envelope(_q(i))).status_code == 200
    # an app that asserts no user is one bucket under the per-app limit only (unchanged behaviour)
    for i in range(5, 8):
        assert _post_as(client, None, sy.envelope(_q(i))).status_code == 200
    assert app.state.pgw_disclosure_metrics.snapshot()["refused_rate"] == 1


def test_app_and_scope_limits_cap_the_whole(tmp_path):
    client, mock, _app, _cfg = _dc_variant(tmp_path, per_caller={
        "new_queries_per_user_per_minute": 5, "new_queries_per_minute": 3, "new_queries_per_scope_per_minute": 40})
    sy = _months()
    for i, u in enumerate(("sso-a", "sso-b", "sso-c")):
        assert _post_as(client, u, sy.envelope(_q(i))).status_code == 200
    r = _post_as(client, "sso-d", sy.envelope(_q(3)))                        # the APP is out, whoever asks
    assert r.status_code == 429 and r.headers["Retry-After"]
    client2, mock2, _a, _c = _dc_variant(tmp_path / "second", per_caller={
        "new_queries_per_user_per_minute": 5, "new_queries_per_minute": 20, "new_queries_per_scope_per_minute": 2})
    for i, u in enumerate(("sso-a", "sso-b")):
        assert _post_as(client2, u, sy.envelope(_q(i))).status_code == 200
    r = _post_as(client2, "sso-c", sy.envelope(_q(2)))                       # the SCOPE is out
    assert r.status_code == 429 and r.json()["error"]["reason"] == "rate_limited"


def test_refused_queries_are_metered_so_probing_is_not_free(tmp_path):
    client, mock, app, _cfg = _dc_variant(tmp_path, per_caller={"refused_per_user_per_minute": 2,
                                                                "refused_per_app_per_minute": 40})
    sy = _pop12()
    assert _post_as(client, "sso-eve", sy.envelope(FIN_LEGAL)).status_code == 200
    for _ in range(2):                                                      # two differencing probes, both refused
        _refused(_post_as(client, "sso-eve", sy.envelope(FIN)))
    n0 = len(mock.received)
    r = _post_as(client, "sso-eve", sy.envelope(FIN))                       # the third is not even evaluated
    assert r.status_code == 429 and r.json()["error"]["reason"] == "rate_limited" and "Retry-After" in r.headers
    assert len(mock.received) == n0
    assert _post_as(client, "sso-eve", sy.envelope(FIN_LEGAL)).status_code == 200      # a repeat: still answered
    assert _post_as(client, "sso-honest", sy.envelope({"filter": [], "list": False})).status_code == 200  # others fine
    # invalid-key probes are metered as well
    bad = _pop12()
    bad.data[0]["sAMAccountName"] = ""
    for _ in range(2):
        _refused(_post_as(client, "sso-mallory", bad.envelope(FIN)), "invalid_record_key")
    assert _post_as(client, "sso-mallory", bad.envelope(FIN)).status_code == 429


def test_counters_are_shared_across_processes_through_the_store(tmp_path):
    """Two gateway instances (two stores, two in-memory states) on one state directory: one budget."""
    client, mock, _app, cfg = _dc_variant(tmp_path, per_caller={"new_queries_per_user_per_minute": 3})
    from pgw.gateway.settings import Settings
    other = TestClient(create_app(Settings(**cfg, ledger_db_path=str(tmp_path / "l3.sqlite"),
                                           egress_ledger_path=str(tmp_path / "e3.jsonl"), master_key=MASTER,
                                           upstream=MockLLMUpstream("summarise"))))
    sy = _months()
    assert _post_as(client, "sso-alice", sy.envelope(_q(0))).status_code == 200
    assert _post_as(other, "sso-alice", sy.envelope(_q(1))).status_code == 200
    assert _post_as(client, "sso-alice", sy.envelope(_q(2))).status_code == 200
    for c in (client, other):
        assert _post_as(c, "sso-alice", sy.envelope(_q(3))).status_code == 429
    assert _post_as(other, "sso-bob", sy.envelope(_q(3))).status_code == 200


def test_counters_are_atomic_under_concurrent_processes(tmp_path):
    db = str(tmp_path / "h.sqlite")
    stores = [disclosure.HistoryStore(db) for _ in range(2)]
    cfg = disclosure.load_config(ROOT / "registry/rules/disclosure_control.yaml")
    sid = "s" * 8
    errs = []

    def work(store):
        for _ in range(25):
            try:
                with store._guard():
                    store._db.execute("BEGIN IMMEDIATE")
                    store._bump(sid, [(b"u" * 16, "new")], time.time())
                    store._db.execute("COMMIT")
            except Exception as e:      # noqa: BLE001
                errs.append(e)
    ts = [threading.Thread(target=work, args=(s,)) for s in stores for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    (n,) = stores[0]._db.execute("SELECT n FROM rate WHERE per = 'd'").fetchone()
    assert n == 100 and cfg.user_per_minute < n


def test_require_end_user_is_a_sealed_registry_flag(tmp_path):
    from pgw.gateway.app import load_registry
    p = tmp_path / "apps.yaml"
    base = {"app_id": "a", "tenant": "t", "key_sha256": "0" * 64, "purposes": ["p"], "models": ["m"]}
    import yaml
    p.write_text(yaml.safe_dump({"version": 1, "apps": [{**base, "require_end_user": True}]}))
    assert load_registry(str(p))[0].require_end_user is True
    p.write_text(yaml.safe_dump({"version": 1, "apps": [{**base, "require_end_user": "yes"}]}))
    with pytest.raises(ValueError, match="require_end_user"):
        load_registry(str(p))


def test_workspace_asserts_the_signed_in_user(tmp_path):
    from tests.workspace_harness import APP_KEY, HEADERS, TICKET, USER, gateway, workspace
    gw, _ = gateway(tmp_path)
    client, rec, _s = workspace(gw)
    r = client.post("/api/run", json={"task": "ticket_summary", "text": TICKET}, headers=HEADERS)
    assert r.status_code == 200
    call = rec.calls[-1]
    h = call["headers"]
    assert h["x-pgw-end-user"] == enduser.opaque_id(USER) and USER not in json.dumps(h)
    raw = call["request"].content
    assert enduser.verify(call["request"].headers, APP_KEY, h["x-pgw-purpose"], raw) == enduser.opaque_id(USER)
    assert enduser.opaque_id(USER.upper()) == enduser.opaque_id(USER)       # case-insensitive, stable


# =============================================================================================================
# 5. store: HMAC-bound mark + rollback copy; operator recovery; bounded waits
# =============================================================================================================

def _boot(tmp_path):
    _c, _m, cfg = make(tmp_path, CONTRACT)
    _c.app.state.pgw_disclosure_history.close()
    app = create_app(_fixed_settings(tmp_path, cfg, MockLLMUpstream("summarise"), MASTER))
    return cfg, app, Path(cfg["state_dir"])


def _ask(app, i):
    assert post(TestClient(app), "roster_question", _months().envelope(_q(i))).status_code == 200


def _start_fails(tmp_path, cfg, match):
    with pytest.raises(disclosure.HistoryIntegrityError, match=match):
        create_app(_fixed_settings(tmp_path, cfg, MockLLMUpstream("summarise"), MASTER))


def test_joint_rollback_of_database_and_mark_is_refused(tmp_path):
    cfg, app, state = _boot(tmp_path)
    store = app.state.pgw_disclosure_history
    _ask(app, 0)
    old_db, old_mark = tmp_path / "old.sqlite", state / "old.hwm"
    with sqlite3.connect(old_db) as dst:
        store._db.backup(dst)
    shutil.copy(state / disclosure.HWM_NAME, old_mark)
    _ask(app, 1)
    _ask(app, 2)
    store.close()
    db = state / disclosure.HISTORY_DB_NAME
    for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        p.unlink(missing_ok=True)
    shutil.copy(old_db, db)                                                  # an older, internally consistent pair
    shutil.copy(old_mark, state / disclosure.HWM_NAME)
    _start_fails(tmp_path, cfg, "joint rollback")
    # the copy is monotonic: it was never lowered and still names the newest sequence
    assert rollback.read_disclosure_copy(state)["seq"] == 3


def test_forged_or_unauthenticated_marks_are_refused(tmp_path):
    cfg, app, state = _boot(tmp_path)
    _ask(app, 0)
    store = app.state.pgw_disclosure_history
    store.close()
    mark = state / disclosure.HWM_NAME
    good = json.loads(mark.read_text())
    # an attacker without the key store writes a mark for this database with a made-up MAC
    mark.write_text(json.dumps({**good, "mac": "1" * 64}))
    _start_fails(tmp_path, cfg, "failed authentication")
    # the pre-round-9 format (no MAC) is not accepted in production
    mark.write_text(json.dumps({k: good[k] for k in ("kind", "db_id", "seq")}))
    _start_fails(tmp_path, cfg, "not authenticated")
    mark.write_text(json.dumps(good))
    # the database's own label cannot be raised without the key either (an old database with a copied-up sequence)
    con = sqlite3.connect(state / disclosure.HISTORY_DB_NAME)
    con.execute("UPDATE meta SET v = '99' WHERE k = 'seq'")
    con.commit()
    con.close()
    _start_fails(tmp_path, cfg, "database's sequence failed authentication")
    # the rollback copy deleted: refused like a deleted mark
    con = sqlite3.connect(state / disclosure.HISTORY_DB_NAME)
    con.execute("UPDATE meta SET v = '1' WHERE k = 'seq'")
    con.commit()
    con.close()
    (state / rollback.DISC_NAME).unlink()
    _start_fails(tmp_path, cfg, "rollback copy.*missing")
    # a different key store cannot start it either
    with pytest.raises(disclosure.HistoryIntegrityError):
        create_app(_fixed_settings(tmp_path, cfg, MockLLMUpstream("summarise"), "ab" * 32))


def test_rollback_copy_is_monotonic_and_validated(tmp_path):
    d = {"kind": rollback.DISC_KIND, "db_id": "a" * 32, "seq": 5, "key_id": "v1", "mac": "b" * 64}
    rollback.record_disclosure(tmp_path, d)
    rollback.record_disclosure(tmp_path, {**d, "seq": 3})                    # never lowered
    assert rollback.read_disclosure_copy(tmp_path)["seq"] == 5
    rollback.record_disclosure(tmp_path, {**d, "seq": 3}, force=True)        # only the logged recovery lowers it
    assert rollback.read_disclosure_copy(tmp_path)["seq"] == 3
    (tmp_path / rollback.DISC_NAME).write_text("{bad")
    with pytest.raises(rollback.RollbackError):
        rollback.read_disclosure_copy(tmp_path)
    with pytest.raises(rollback.RollbackError):
        rollback.record_disclosure(tmp_path, {**d, "seq": "x"})


def test_recover_needs_operator_reason_and_a_stopped_gateway(tmp_path, monkeypatch, capsys):
    cfg, app, state = _boot(tmp_path)
    _ask(app, 0)
    db = str(state / disclosure.HISTORY_DB_NAME)
    monkeypatch.setenv("PGW_MASTER_KEY", MASTER)
    args = ["history", "recover", "--db", db, "--state-dir", str(state), "--new"]
    reason = ["--reason", "INC-90: restored from backup after disk failure"]
    with pytest.raises(SystemExit):                                          # no operator identity
        disclosure._main(args + reason)
    with pytest.raises(SystemExit):                                          # an operator that is not an identity
        disclosure._main(args + reason + ["--operator", "x; rm -rf"])
    # the gateway is running (it holds the store): refused, nothing changed
    assert disclosure._main(args + reason + ["--operator", "asha.r"]) == 2
    assert "gateway process holds the history" in capsys.readouterr().err
    assert app.state.pgw_disclosure_history.admin_log() == []
    app.state.pgw_disclosure_history.close()                                 # gateway stopped
    with pytest.raises(ValueError, match="written reason"):
        disclosure.HistoryStore(db, mac_keyfn=None).recover("short", operator="asha.r")
    assert disclosure._main(args + reason + ["--operator", "asha.r"]) == 0
    app2 = create_app(_fixed_settings(tmp_path, cfg, MockLLMUpstream("summarise"), MASTER))     # starts again
    (entry,) = app2.state.pgw_disclosure_history.admin_log()
    assert entry["action"] == "recover_new" and entry["operator"] == "asha.r" and entry["reason"].startswith("INC-90")
    assert rollback.read_disclosure_copy(state)["db_id"] == app2.state.pgw_disclosure_history.db_id
    # non-new recovery after a restore also re-binds the copy (and works only with the key store)
    app2.state.pgw_disclosure_history.close()
    monkeypatch.delenv("PGW_MASTER_KEY")
    with pytest.raises(SystemExit):
        disclosure._main(["history", "recover", "--db", db, "--state-dir", str(state), "--operator", "asha.r"] + reason)


def _async_get_and_chat(app, chat_headers, chat_body):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as c:
            t0 = time.perf_counter()
            chat = asyncio.ensure_future(c.post("/v1/chat/completions", content=chat_body, headers=chat_headers))
            await asyncio.sleep(0.2)                                           # the chat is now waiting on the lock
            h0 = time.perf_counter()
            hz = await c.get("/healthz")
            health_s = time.perf_counter() - h0
            r = await chat
            return hz.status_code, health_s, r, time.perf_counter() - t0
    return asyncio.run(go())


def test_locked_database_fails_closed_without_blocking_the_event_loop(tmp_path):
    cfg, app, state = _boot(tmp_path)
    store = app.state.pgw_disclosure_history
    store._db.execute("PRAGMA busy_timeout=700")
    blocker = sqlite3.connect(state / disclosure.HISTORY_DB_NAME, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")                                         # another process holds the write lock
    body = body_of(_months().envelope(_q(0)))
    raw = json.dumps(body).encode()
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "roster_question", "Content-Type": "application/json",
         **attest("roster_question", body, contract="roster_question")}
    status, health_s, r, total = _async_get_and_chat(app, h, raw)
    blocker.execute("ROLLBACK")
    blocker.close()
    assert status == 200 and health_s < 0.5                                    # the event loop was free meanwhile
    assert r.status_code == 503 and r.json()["error"]["type"] == "disclosure_store_unavailable"
    assert total < 5
    assert app.state.pgw_disclosure_metrics.c["store_unavailable"] >= 1
    _ask(app, 1)                                                               # and it recovers once the lock is gone


def test_a_stuck_mark_lock_fails_closed_after_a_bounded_wait(tmp_path, monkeypatch):
    cfg, app, state = _boot(tmp_path)
    monkeypatch.setattr(disclosure, "STORE_TIMEOUT_S", 0.3)
    fd = open(state / (disclosure.HWM_NAME + ".lock"), "a")
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        t0 = time.perf_counter()
        r = post(TestClient(app), "roster_question", _months().envelope(_q(0)))
        assert r.status_code == 503 and r.json()["error"]["type"] == "disclosure_store_unavailable"
        assert time.perf_counter() - t0 < 3
    finally:
        fd.close()
    _ask(app, 1)


def test_store_lock_wait_is_bounded(tmp_path):
    store = disclosure.HistoryStore(str(tmp_path / "h.sqlite"), timeout=0.2)
    store._lock.acquire()
    try:
        t0 = time.perf_counter()
        with pytest.raises(disclosure.HistoryUnavailable, match="timeout"):
            store.sizes()
        assert time.perf_counter() - t0 < 2
    finally:
        store._lock.release()
