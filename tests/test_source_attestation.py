"""Cryptographic source attestation (SECURITY_BOUNDARY.md §4, audit finding S3).

Free text reaches an external model only with a valid Ed25519 signature, by a source key the signed contract pins,
over the purpose, the source id, a fresh timestamp and the exact messages + pgw_subjects. Every failure is decided
before any send: the external mock receives nothing and the response carries no request content.
Synthetic data only.
"""
import json
import os
import time
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product import attest as A
from pgw.product.contracts import ContractError, load_contract
from tests.sealed import ROOT, attest, production, sha, source_key

KEY = "attest-app-key"
APP = "pilot"
CANARY = "Zorawar Pemberton-Okafor"   # synthetic
TEXT = f"Ticket for {CANARY}: payroll arrears missing; employee mentioned chemotherapy."
TICKET = ROOT / "contracts/ticket_summary.yaml"
SID = "helpdesk-ticket-export@1"


def refusing(tmp: Path, src: Path = TICKET) -> str:
    p = tmp / "refuse" / src.name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src.read_text().replace("unattested: PRIVATE", "unattested: REFUSE"))
    return str(p)


def make(tmp, tasks, private=None, **kw):
    ext = MockLLMUpstream("summarise")
    cfg = production(tmp, [{"app_id": APP, "tenant": "acme", "key_sha256": sha(KEY), "restore_output": True,
                             "models": ["mock-1"], "purposes": sorted(tasks), "tasks": tasks}],
                     private_upstream=private, **kw)
    s = Settings(**cfg, ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=ext)
    return TestClient(create_app(s)), ext


def body(text=TEXT, subjects=True, messages=None):
    b = {"model": "mock-1", "messages": messages or [{"role": "user", "content": text}]}
    if subjects:
        b["pgw_subjects"] = {"requester": {"name": CANARY}}
    return b


def post(client, b, headers, purpose="ticket_summary"):
    return client.post("/v1/chat/completions", json=b,
                       headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose, **headers})


def ledger(tmp) -> list[dict]:
    p = tmp / "e.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def refuse(tmp_path):
    client, ext = make(tmp_path, {"ticket_summary": refusing(tmp_path)})
    return client, ext, tmp_path


def assert_refused_before_send(r, ext, tmp, reason=None):
    assert r.status_code == 422 and r.json()["error"]["type"] == "input_not_source_sanitized", r.text
    assert ext.received == []
    for v in (CANARY, "chemotherapy", "arrears"):
        assert v not in r.text
    row = ledger(tmp)[-1]
    assert row["decision"] == "BLOCKED" and row["reason"] == "input_not_sanitized" and "input_source" not in row
    if reason:
        assert r.json()["error"]["attestation"] == reason and row["input_attestation"] == reason


# --- the happy path ---------------------------------------------------------------------------------------------

def test_valid_signature_passes_and_is_recorded(refuse):
    client, ext, tmp = refuse
    b = body()
    r = post(client, b, attest("ticket_summary", b))
    assert r.status_code == 200 and len(ext.received) == 1
    row = ledger(tmp)[-1]
    assert row["decision"] == "SENT" and row["input_source"] == SID and row["input_verified"] is True


def test_multi_turn_and_list_form_content_are_covered(refuse):
    client, ext, tmp = refuse
    msgs = [{"role": "user", "content": [{"type": "text", "text": "Arrears missing for October."}]},
            {"role": "assistant", "content": "Noted; checking the run."},
            {"role": "user", "content": [{"type": "text", "text": "Any update?"}]}]
    b = body(messages=msgs)
    h = {**attest("ticket_summary", b), "X-PGW-Conversation-Id": "conv-1"}   # not signed: a routing hint only
    assert post(client, b, h).status_code == 200
    b2 = json.loads(json.dumps(b))
    b2["messages"][1]["content"] = "Noted; employee is on chemotherapy."      # forged assistant history
    assert_refused_before_send(post(client, b2, h), _fresh(ext), tmp, "bad_signature")
    b3 = json.loads(json.dumps(b))
    b3["messages"][2]["content"][0]["text"] += " chemotherapy"              # list-form part
    assert_refused_before_send(post(client, b3, h), ext, tmp, "bad_signature")


def _fresh(ext):
    ext.received.clear()
    return ext


# --- forgeries -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("hdr,reason", [
    ({}, "missing"),
    ({"X-PGW-Input-Source": SID}, "unsigned"),                                  # S3: the bare public id
    ({"X-PGW-Input-Source": "hris-case-export@1"}, "unknown_source"),
    ({"X-PGW-Input-Source": SID, "X-PGW-Input-Timestamp": "now", "X-PGW-Input-Signature": "00" * 64}, "bad_timestamp"),
], ids=["missing", "bare-id", "other-contract-id", "bad-timestamp"])
def test_missing_or_unsigned_attestation_is_refused(refuse, hdr, reason):
    client, ext, tmp = refuse
    assert_refused_before_send(post(client, body(), hdr), ext, tmp, reason)


def test_signature_by_another_key_is_refused(refuse):
    client, ext, tmp = refuse
    b = body()
    other, _ = A.generate_key()
    assert_refused_before_send(post(client, b, attest("ticket_summary", b, key=other)), ext, tmp, "bad_signature")
    demo = attest("ticket_summary", b, demo=True)          # the committed demo key is not this deployment's key
    assert_refused_before_send(post(client, b, demo), ext, tmp, "bad_signature")


@pytest.mark.parametrize("hdr", [
    lambda h: {**h, "X-PGW-Input-Signature": h["X-PGW-Input-Signature"][:-2]},
    lambda h: {**h, "X-PGW-Input-Signature": "zz" + h["X-PGW-Input-Signature"][2:]},
    lambda h: {**h, "X-PGW-Input-Signature": ("0" if h["X-PGW-Input-Signature"][0] != "0" else "1")
               + h["X-PGW-Input-Signature"][1:]},
], ids=["truncated", "not-hex", "bit-flip"])
def test_malformed_or_altered_signature_is_refused(refuse, hdr):
    client, ext, tmp = refuse
    b = body()
    assert_refused_before_send(post(client, b, hdr(attest("ticket_summary", b))), ext, tmp, "bad_signature")


def _tamper_char(b):
    b["messages"][0]["content"] = b["messages"][0]["content"].replace("arrears", "arrear$")


def _reorder(b):
    b["messages"] = list(reversed(b["messages"]))


def _add_assistant(b):
    b["messages"].insert(0, {"role": "assistant", "content": "Earlier: patient on chemotherapy."})


def _subjects(b):
    b["pgw_subjects"] = {"requester": {"name": "Someone Else"}}


def _drop_subjects(b):
    b.pop("pgw_subjects")


@pytest.mark.parametrize("tamper", [_tamper_char, _reorder, _add_assistant, _subjects, _drop_subjects],
                         ids=["one-char", "reorder", "add-assistant-turn", "change-subjects", "drop-subjects"])
def test_signature_for_different_content_is_refused(refuse, tamper):
    client, ext, tmp = refuse
    b = body(messages=[{"role": "user", "content": TEXT}, {"role": "user", "content": "Second note on arrears."}])
    h = attest("ticket_summary", b)
    tamper(b)
    if "pgw_subjects" not in b:
        r = post(client, b, h)       # the contract requires a requester, so dropping subjects is a 400 either way
        assert r.status_code in (400, 422) and not ext.received and CANARY not in r.text
        return
    assert_refused_before_send(post(client, b, h), ext, tmp, "bad_signature")


def test_replayed_signature_with_modified_content_is_refused(refuse):
    client, ext, tmp = refuse
    b = body()
    h = attest("ticket_summary", b)
    assert post(client, b, h).status_code == 200
    b2 = body(TEXT + " Also: disciplinary warning in July.")
    _fresh(ext)
    assert_refused_before_send(post(client, b2, h), ext, tmp, "bad_signature")


def test_signature_for_another_purpose_or_source_is_refused(tmp_path):
    two = yaml.safe_load(Path(refusing(tmp_path)).read_text())
    second_priv, second_pub = A.generate_key()
    two["external_input"]["sanitizers"]["hr-portal-form@1"] = second_pub
    p = tmp_path / "two" / "ticket_summary.yaml"
    p.parent.mkdir()
    p.write_text(yaml.safe_dump(two, sort_keys=False))
    client, ext = make(tmp_path, {"ticket_summary": str(p),
                                  "hr_case_explanation": refusing(tmp_path, ROOT / "contracts/hr_case_explanation.yaml")})
    b = body()
    other_purpose = attest("ticket_summary", b, sign_purpose="hr_case_explanation")
    assert_refused_before_send(post(client, b, other_purpose), ext, tmp_path, "bad_signature")
    # key of source A presented as source B (both allowlisted): the signature binds the id
    swapped = A.sign(source_key()[0], "ticket_summary", "hr-portal-form@1", b["messages"], b["pgw_subjects"],
                     app_id=APP)
    assert_refused_before_send(post(client, b, swapped), ext, tmp_path, "bad_signature")
    relabelled = {**attest("ticket_summary", b), "X-PGW-Input-Source": "hr-portal-form@1"}
    assert_refused_before_send(post(client, b, relabelled), ext, tmp_path, "bad_signature")
    # each allowlisted source signs with its own key
    ok = A.sign(second_priv, "ticket_summary", "hr-portal-form@1", b["messages"], b["pgw_subjects"], app_id=APP)
    assert post(client, b, ok).status_code == 200 and ledger(tmp_path)[-1]["input_source"] == "hr-portal-form@1"


@pytest.mark.parametrize("skew", [-(A.MAX_SKEW_S + 5), A.MAX_SKEW_S + 5, -10 ** 9], ids=["expired", "future", "epoch"])
def test_stale_or_future_timestamp_is_refused(refuse, skew):
    client, ext, tmp = refuse
    b = body()
    h = attest("ticket_summary", b, ts=max(0, int(time.time()) + skew))
    assert_refused_before_send(post(client, b, h), ext, tmp, "stale_timestamp")


def test_timestamp_inside_the_window_passes(refuse):
    client, ext, _ = refuse
    b = body()
    assert post(client, b, attest("ticket_summary", b, ts=int(time.time()) - A.MAX_SKEW_S + 30)).status_code == 200


def test_rewriting_the_timestamp_header_breaks_the_signature(refuse):
    client, ext, tmp = refuse
    b = body()
    h = attest("ticket_summary", b, ts=int(time.time()) - 100)
    h["X-PGW-Input-Timestamp"] = str(int(time.time()))
    assert_refused_before_send(post(client, b, h), ext, tmp, "bad_signature")


def test_local_compute_free_text_needs_a_valid_signature(tmp_path):
    client, ext = make(tmp_path, {"salary_comparison": str(ROOT / "contracts/salary_comparison.yaml")})
    payload = {"instruction": "Compare; note one of them is on medical leave.",
               "employees": [{"name": "Amit Trivedi", "employee_id": "KD-10989", "annual_ctc": 2450000},
                             {"name": "Hetal Shah", "employee_id": "KD-11063", "annual_ctc": 2180000}]}
    b = body(json.dumps(payload), subjects=False)
    h = attest("salary_comparison", b)
    payload["instruction"] = "Compare; note Amit Trivedi is on chemotherapy."
    r = post(client, body(json.dumps(payload), subjects=False), h, "salary_comparison")
    assert r.status_code == 422 and not ext.received and "Trivedi" not in r.text
    assert post(client, b, h, "salary_comparison").status_code == 200


# --- PRIVATE: unverified input stays in-boundary -----------------------------------------------------------------

def test_private_contract_routes_unverified_input_in_boundary(tmp_path):
    local = MockLLMUpstream("summarise")
    client, ext = make(tmp_path, {"ticket_summary": str(TICKET)}, private=local)
    assert load_contract(TICKET).external_input.unattested == "PRIVATE"
    b = body()
    for h in ({}, {"X-PGW-Input-Source": SID}, attest("ticket_summary", b, key=A.generate_key()[0])):
        r = post(client, b, h)
        assert r.status_code == 200 and r.headers["X-PGW-Route"] == "PRIVATE_LOCAL"
        row = ledger(tmp_path)[-1]
        assert row["decision"] == "SENT_PRIVATE" and row["reason"] == "input_not_sanitized"
        assert "input_source" not in row
    assert ext.received == [] and len(local.received) == 3
    r = post(client, b, attest("ticket_summary", b))                 # verified: protected external path
    assert r.status_code == 200 and "X-PGW-Route" not in r.headers and len(ext.received) == 1


def test_shipped_ext_pseudo_contracts_keep_unverified_input_in_boundary():
    for p in sorted((ROOT / "contracts").glob("*.yaml")):
        c = load_contract(p)
        assert c.external_input.unattested == ("PRIVATE" if c.route == "EXT_PSEUDO" else "REFUSE"), p.name


# --- demo keys ---------------------------------------------------------------------------------------------------

def test_shipped_contracts_carry_only_demo_keys_matching_the_fixture():
    demo = A.load_demo_keys()
    for p in sorted((ROOT / "contracts").glob("*.yaml")):
        for sid, pub in load_contract(p).external_input.keys.items():
            assert pub in A.DEMO_PUBKEYS, p.name
            signed = A.sign(demo[sid], "x", sid, [{"role": "user", "content": "t"}], app_id=APP)
            A.verify({sid: pub}, "x", sid, signed[A.H_TIMESTAMP], signed[A.H_SIGNATURE],
                     [{"role": "user", "content": "t"}], app_id=APP, nonce=signed[A.H_NONCE])


def test_production_refuses_demo_keys_development_accepts_them(tmp_path):
    tasks = {"ticket_summary": refusing(tmp_path)}
    with pytest.raises(ContractError, match="DEMO keys"):
        make(tmp_path / "p", tasks, demo_keys=True)
    ext = MockLLMUpstream("summarise")
    cfg = production(tmp_path / "d", [{"app_id": APP, "tenant": "acme", "key_sha256": sha(KEY), "models": ["mock-1"],
                                       "purposes": ["ticket_summary"], "tasks": tasks}], demo_keys=True)
    s = Settings(**{**cfg, "mode": "development"}, ledger_db_path=str(tmp_path / "l.sqlite"),
                 egress_ledger_path=str(tmp_path / "e.jsonl"), master_key=os.urandom(32).hex(), upstream=ext)
    client = TestClient(create_app(s))
    b = body()
    assert post(client, b, attest("ticket_summary", b, demo=True)).status_code == 200


# --- contract key validation -----------------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["ab" * 31, "ab" * 33, "AB" * 32, "zz" * 32, "00" * 32, 12345, None, ["ab" * 32]],
                         ids=["short", "long", "uppercase", "not-hex", "all-zero", "int", "null", "list"])
def test_malformed_pubkey_stops_loading(tmp_path, key):
    c = yaml.safe_load(TICKET.read_text())
    c["external_input"]["sanitizers"] = {SID: key}
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(c))
    with pytest.raises(ContractError, match="public key"):
        load_contract(p)


def test_list_form_without_keys_stops_loading(tmp_path):
    c = yaml.safe_load(TICKET.read_text())
    c["external_input"]["sanitizers"] = [SID]
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(c))
    with pytest.raises(ContractError, match="public key"):
        load_contract(p)


# --- the helper module ------------------------------------------------------------------------------------------

def test_canonical_message_is_domain_separated_and_unambiguous():
    d = A.content_digest([{"role": "user", "content": "x"}])
    m = A.canonical_message(APP, "p", "s@1", 1, "ab" * 16, d)
    assert m.startswith(A.DOMAIN + b"\n") and A.DOMAIN.endswith(b"/v2")
    assert A.canonical_message(APP, "p\",\"s", "1", 1, "ab" * 16, d) != \
        A.canonical_message(APP, "p", "s\",\"1", 1, "ab" * 16, d)
    assert A.canonical_message("a", "p", "s", 1, "ab" * 16, d) != A.canonical_message("b", "p", "s", 1, "ab" * 16, d)
    assert A.content_digest([{"a": 1, "b": 2}]) == A.content_digest([{"b": 2, "a": 1}])   # key order irrelevant
    assert A.content_digest([{"role": "user", "content": "x"}], None) != \
        A.content_digest([{"role": "user", "content": "x"}], {})
    with pytest.raises(ValueError):
        A.content_digest([{"x": float("nan")}])


def test_verify_never_accepts_a_non_finite_number(refuse):
    client, ext, tmp = refuse
    raw = ('{"model":"mock-1","pgw_subjects":{"requester":{"name":"A B"}},'
           '"messages":[{"role":"user","content":"hi","x":NaN}]}')
    r = client.post("/v1/chat/completions", content=raw.encode(),
                    headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary",
                             "content-type": "application/json", "X-PGW-Input-Source": SID,
                             "X-PGW-Input-Timestamp": str(int(time.time())), "X-PGW-Input-Signature": "00" * 64})
    assert r.status_code in (400, 422) and not ext.received


def test_keygen_cli_writes_a_private_key_file(tmp_path):
    out = A._main(["keygen", str(tmp_path / "src.key")])
    priv = (tmp_path / "src.key").read_text().strip()
    pub = out.split('"')[1]
    assert (tmp_path / "src.key").stat().st_mode & 0o777 == 0o600
    assert A.parse_public_key(pub) and pub not in A.DEMO_PUBKEYS
    h = A.sign(priv, "p", "s@1", [{"role": "user", "content": "t"}], app_id=APP)
    assert A.verify({"s@1": pub}, "p", "s@1", h[A.H_TIMESTAMP], h[A.H_SIGNATURE],
                    [{"role": "user", "content": "t"}], app_id=APP, nonce=h[A.H_NONCE]) == "s@1"
    with pytest.raises(SystemExit):
        A._main(["keygen", str(tmp_path / "src.key")])                # never overwrites


# --- single use: replay cache and app binding ---------------------------------------------------------------------

def test_identical_resend_is_rejected(refuse):
    client, ext, tmp = refuse
    b = body()
    h = attest("ticket_summary", b)
    assert post(client, b, h).status_code == 200 and len(ext.received) == 1
    _fresh(ext)
    assert_refused_before_send(post(client, b, h), ext, tmp, "replayed")
    assert post(client, b, attest("ticket_summary", b)).status_code == 200   # a fresh signature (new nonce) passes


def test_attestation_for_another_app_is_rejected(tmp_path):
    other_key = "other-app-key"
    ext = MockLLMUpstream("summarise")
    tasks = {"ticket_summary": refusing(tmp_path)}
    cfg = production(tmp_path, [
        {"app_id": APP, "tenant": "acme", "key_sha256": sha(KEY), "models": ["mock-1"],
         "purposes": ["ticket_summary"], "tasks": tasks},
        {"app_id": "other", "tenant": "acme", "key_sha256": sha(other_key), "models": ["mock-1"],
         "purposes": ["ticket_summary"], "tasks": tasks}], private_upstream=None)
    client = TestClient(create_app(Settings(**cfg, ledger_db_path=str(tmp_path / "l.sqlite"),
                                            egress_ledger_path=str(tmp_path / "e.jsonl"),
                                            master_key=os.urandom(32).hex(), upstream=ext)))
    b = body()
    h = attest("ticket_summary", b)                    # signed for APP
    r = client.post("/v1/chat/completions", json=b, headers={"Authorization": f"Bearer {other_key}",
                                                             "X-PGW-Purpose": "ticket_summary", **h})
    assert_refused_before_send(r, ext, tmp_path, "bad_signature")
    assert post(client, b, h).status_code == 200       # the app it was signed for (not consumed by the failure)
    ok = attest("ticket_summary", b, app_id="other")
    r = client.post("/v1/chat/completions", json=b, headers={"Authorization": f"Bearer {other_key}",
                                                             "X-PGW-Purpose": "ticket_summary", **ok})
    assert r.status_code == 200


@pytest.mark.parametrize("nonce", [None, "", "xyz", "ab" * 8, "AB" * 16, "ab" * 33],
                         ids=["absent", "empty", "not-hex", "short", "uppercase", "long"])
def test_missing_or_malformed_nonce_is_refused(refuse, nonce):
    client, ext, tmp = refuse
    b = body()
    h = attest("ticket_summary", b)
    if nonce is None:
        h.pop(A.H_NONCE)
    else:
        h[A.H_NONCE] = nonce
    assert_refused_before_send(post(client, b, h), ext, tmp, "bad_nonce")


def test_replay_cache_evicts_expired_entries():
    c = A.ReplayCache(max_entries=2, max_skew=300)
    c.add(b"a", 1000, now=1000)
    c.add(b"b", 1100, now=1100)
    with pytest.raises(A.AttestationError, match="replayed"):
        c.add(b"a", 1000, now=1299)
    with pytest.raises(A.ReplayCacheFull):                 # both still inside the window: no eviction
        c.add(b"c", 1299, now=1299)
    c.add(b"c", 1301, now=1301)                            # a's window (until 1300) has passed: evicted
    assert len(c) == 2
    with pytest.raises(A.ReplayCacheFull):
        c.add(b"d", 1301, now=1301)


def test_full_replay_cache_fails_closed(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(A, "REPLAY_CACHE_MAX", 2)
    client, ext = make(tmp_path, {"ticket_summary": refusing(tmp_path)})
    for _ in range(2):
        b = body()
        assert post(client, b, attest("ticket_summary", b)).status_code == 200
    _fresh(ext)
    b = body()
    r = post(client, b, attest("ticket_summary", b))
    assert r.status_code == 503 and r.json()["error"]["type"] == "gateway_busy"
    assert ext.received == [] and CANARY not in r.text and "chemotherapy" not in r.text
    r = post(client, b, {"X-PGW-Input-Source": SID})       # forgeries are refused without touching the cache
    assert r.status_code == 422


# --- small-order public keys ---------------------------------------------------------------------------------------

_P = 2 ** 255 - 19
_ORDER8 = ["26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
           "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a"]


def _enc(y: int, sign: int = 0) -> str:
    return (y | (sign << 255)).to_bytes(32, "little").hex()


SMALL_ORDER = {
    "identity": _enc(1), "order2": _enc(_P - 1), "order4+": _enc(0), "order4-": _enc(0, 1),
    "order8a": _ORDER8[0], "order8b": _ORDER8[1],
    "order8a-neg": _enc(int.from_bytes(bytes.fromhex(_ORDER8[0]), "little"), 1),
    "order8b-neg": _enc(int.from_bytes(bytes.fromhex(_ORDER8[1]), "little"), 1),
    # non-canonical encodings of the same points (y >= p, or a sign bit on x = 0)
    "identity-signbit": _enc(1, 1), "order2-signbit": _enc(_P - 1, 1), "zero-as-p": _enc(_P),
    "zero-as-p-signbit": _enc(_P, 1), "identity-as-p+1": _enc(_P + 1), "identity-as-p+1-signbit": _enc(_P + 1, 1),
}


@pytest.mark.parametrize("hexkey", list(SMALL_ORDER.values()), ids=list(SMALL_ORDER))
def test_small_order_public_keys_are_refused(tmp_path, hexkey):
    with pytest.raises(ValueError):
        A.parse_public_key(hexkey)
    c = yaml.safe_load(TICKET.read_text())
    c["external_input"]["sanitizers"] = {SID: hexkey}
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(c))
    with pytest.raises(ContractError, match="public key"):
        load_contract(p)


def test_ordinary_keys_still_load():
    for _ in range(20):
        assert len(A.parse_public_key(A.generate_key()[1])) == 32
    for pub in A.DEMO_PUBKEYS:
        A.parse_public_key(pub)
