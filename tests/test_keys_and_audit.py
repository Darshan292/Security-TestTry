"""Step 5: per-tenant keys, key rotation, tamper-evident audit log. Synthetic data only."""
import hashlib
import json
import os
import secrets
import stat
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from pgw import audit
from tests.sealed import legacy_dev
from pgw import keys as K
from pgw.detect.edm import RestoreLedger
from pgw.gateway import Settings, create_app
from pgw.gateway.upstream import MockLLMUpstream
from pgw.pipeline import KeyUnavailable, Keys, Protector
from pgw.policy import PolicyPack
from pgw.stream import Restorer
from pgw.types import Context, EntityType as T

ROOT = Path(__file__).resolve().parents[1]
POLICY = PolicyPack.load(str(ROOT / "policies/hris-default.yaml"))
NAME = "Dear Anika Deshpande, please summarise the grievance."
APP_KEY = "keys-test-app-" + secrets.token_hex(6)
NOKEY_APP_KEY = "keys-test-nokey-" + secrets.token_hex(6)


def ring(**tenants):
    """tenants: name -> list of hex keys (last one active)."""
    def r(hexes):
        return {"active": f"k{len(hexes)}", "keys": {f"k{i}": h for i, h in enumerate(hexes, 1)}}
    return {"platform": r([secrets.token_hex(32)]), "tenants": {t: r(h) for t, h in tenants.items()}}


def ctx(tenant="acme", conv="conv-1"):
    return Context(tenant, "hr-assistant", "email_draft", conversation_id=conv)


def alias_of(prot, value="Anika Deshpande"):
    return next(a for a, e in prot.alias_map.items() if e.value == value)


def user(text):
    return [{"role": "user", "content": text}]


# --- tenant isolation -----------------------------------------------------------------------

def test_same_value_gets_different_aliases_per_tenant():
    kr = K.TenantKeyring(ring(acme=[secrets.token_hex(32)], globex=[secrets.token_hex(32)]))
    pr = Protector(POLICY, kr)
    a = alias_of(pr.protect(user(NAME), ctx("acme")))
    b = alias_of(pr.protect(user(NAME), ctx("globex")))
    assert a != b
    assert kr.derive("acme", "alias") != kr.derive("globex", "alias")


def test_tenant_without_key_fails_closed_in_protector():
    kr = K.TenantKeyring(ring(acme=[secrets.token_hex(32)]))
    with pytest.raises(KeyUnavailable):
        Protector(POLICY, kr).protect(user(NAME), ctx("initech"))
    with pytest.raises(KeyUnavailable):   # the platform key is never a fallback for tenant keys
        kr.derive("initech", "alias")


def test_single_master_key_still_works_and_matches_old_derivation():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    master = bytes(range(32))
    old = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"pgw/alias/v1/acme").derive(master)
    k = Keys(master)
    assert k.derive("acme", "alias") == old == k.derive("acme", "alias", "v1")
    with pytest.raises(KeyUnavailable):
        k.derive("acme", "alias", "k9")


# --- rotation -------------------------------------------------------------------------------

def test_rotation_keeps_ledger_entries_and_alias_stability(tmp_path):
    k1, k2 = secrets.token_hex(32), secrets.token_hex(32)
    before = K.TenantKeyring(ring(acme=[k1]))
    after_doc = ring(acme=[k1, k2])
    after_doc["platform"] = {"active": "k1", "keys": {"k1": before._rings["*"]["keys"]["k1"]}}
    after = K.TenantKeyring(after_doc)
    db = str(tmp_path / "ledger.sqlite")

    # turn 1 under k1: alias pinned to k1, restored value recorded under k1
    led1 = RestoreLedger(before.derive("*", "ledger"), db, keys=before)
    p1 = Protector(POLICY, before, None, led1).protect(user(NAME), ctx())
    alias1 = alias_of(p1)
    r = Restorer(p1.alias_map)
    reply = r.feed(f"[{alias1}] raised a grievance.") + r.finish()
    led1.record(ctx(), r.restored)

    # key rotated (k2 active), gateway restarted on the same ledger
    led2 = RestoreLedger(after.derive("*", "ledger"), db, keys=after)
    pr2 = Protector(POLICY, after, None, led2)
    history = [{"role": "assistant", "content": reply}, {"role": "user", "content": "Shorten it."}]
    p2 = pr2.protect(history, ctx())     # no cue left: only the k1 ledger row can catch the name
    assert "Anika" not in json.dumps(p2.messages)
    assert "Anika" in json.dumps(Protector(POLICY, after).protect(history, ctx()).messages)   # control
    assert alias_of(pr2.protect(user(NAME), ctx())) == alias1   # same conversation keeps its pinned key id
    assert led2.pinned_key(ctx()) == "k1"
    # a new conversation uses the new key id, for aliases and ledger rows
    p3 = pr2.protect(user(NAME), ctx(conv="conv-2"))
    assert led2.pinned_key(ctx(conv="conv-2")) == "k2" and alias_of(p3) != alias1
    led2.record(ctx(conv="conv-2"), [(T.PERSON, "Anika Deshpande")])
    kids = {r[0] for r in led2.db.execute("SELECT key_id FROM restore_ledger")}
    assert kids == {"k1", "k2"}

    # retiring k1 while its rows are live fails closed instead of silently missing them
    del after_doc["tenants"]["acme"]["keys"]["k1"]
    led3 = RestoreLedger(b"x" * 32, db, keys=K.TenantKeyring(after_doc))
    with pytest.raises(KeyUnavailable):
        led3.detect(ctx(), ["Anika Deshpande again"])


def test_rotate_cli_writes_atomically_0600_and_never_prints_keys(tmp_path, capsys):
    f = tmp_path / "keys.json"
    assert K.main(["init", "--file", str(f)]) == 0
    assert K.main(["rotate", "--tenant", "acme", "--file", str(f)]) == 0
    assert K.main(["rotate", "--tenant", "acme", "--file", str(f)]) == 0
    doc = json.loads(f.read_text())
    assert doc["tenants"]["acme"]["active"] == "k2" and set(doc["tenants"]["acme"]["keys"]) == {"k1", "k2"}
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    out = capsys.readouterr()
    for hx in [*doc["tenants"]["acme"]["keys"].values(), doc["platform"]["keys"]["k1"]]:
        assert hx not in out.out + out.err
    assert K.main(["retire", "--tenant", "acme", "--key-id", "k2", "--file", str(f)]) == 1   # active
    assert K.main(["retire", "--tenant", "acme", "--key-id", "k1", "--file", str(f)]) == 0
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".pgw-keys-")]
    os.chmod(f, 0o644)
    with pytest.raises(K.KeyFileError):
        K.TenantKeyring.load(f)


# --- gateway with a tenant key file ---------------------------------------------------------

def _gateway(tmp_path, doc, mock):
    kf = tmp_path / "keys.json"
    K.write_file(kf, doc)
    reg = tmp_path / "apps.yaml"
    sha = lambda k: hashlib.sha256(k.encode()).hexdigest()  # noqa: E731
    reg.write_text(yaml.safe_dump({"apps": [
        {"app_id": "hr-assistant", "tenant": "acme", "key_sha256": sha(APP_KEY), "purposes": ["email_draft"],
         "restore_output": True, "models": ["mock-1"]},
        {"app_id": "nokey", "tenant": "initech", "key_sha256": sha(NOKEY_APP_KEY), "purposes": ["email_draft"],
         "restore_output": True, "models": ["mock-1"]}]}))
    s = Settings(policy_path=str(ROOT / "policies/hris-default.yaml"), **legacy_dev(reg),   # legacy path, dev only
                 ledger_db_path=str(tmp_path / "ledger.sqlite"), egress_ledger_path=str(tmp_path / "egress.jsonl"),
                 tenant_keys_file=str(kf), upstream=mock)
    return TestClient(create_app(s)), kf


def _post(client, key, text=NAME):
    return client.post("/v1/chat/completions", json={"model": "mock-1", "messages": user(text)},
                       headers={"Authorization": f"Bearer {key}", "X-PGW-Purpose": "email_draft"})


def test_gateway_tenant_without_key_503_nothing_sent(tmp_path):
    mock = MockLLMUpstream("summarise")
    client, kf = _gateway(tmp_path, ring(acme=[secrets.token_hex(32)]), mock)
    r = _post(client, NOKEY_APP_KEY)
    assert r.status_code == 503 and mock.received == []
    assert "Anika" not in r.text
    assert _post(client, APP_KEY).status_code == 200 and len(mock.received) == 1
    rows = [json.loads(x) for x in (tmp_path / "egress.jsonl").read_text().splitlines()]
    assert [x["decision"] for x in rows] == ["BLOCKED", "SENT"] and all("mac" in x for x in rows)
    assert audit.main(["verify", str(tmp_path / "egress.jsonl"), "--keys-file", str(kf)]) == 0


def test_gateway_refuses_to_start_on_broken_chain(tmp_path):
    mock = MockLLMUpstream("summarise")
    client, kf = _gateway(tmp_path, ring(acme=[secrets.token_hex(32)]), mock)
    for _ in range(2):
        assert _post(client, APP_KEY).status_code == 200
    log = tmp_path / "egress.jsonl"
    lines = log.read_text().splitlines()
    log.write_text(lines[1] + "\n")    # first row deleted
    s = Settings(policy_path=str(ROOT / "policies/hris-default.yaml"), **legacy_dev(tmp_path / "apps.yaml"),
                 ledger_db_path=str(tmp_path / "ledger.sqlite"), egress_ledger_path=str(log),
                 tenant_keys_file=str(kf), upstream=mock)
    with pytest.raises(audit.AuditChainError):
        create_app(s)


def test_writer_stops_when_log_changes_underneath(tmp_path):
    mock = MockLLMUpstream("summarise")
    client, _ = _gateway(tmp_path, ring(acme=[secrets.token_hex(32)]), mock)
    assert _post(client, APP_KEY).status_code == 200
    with open(tmp_path / "egress.jsonl", "a") as f:
        f.write('{"forged": true}\n')
    r = _post(client, APP_KEY)
    assert r.status_code == 503 and r.json()["error"]["type"] == "ledger_unavailable"
    assert len(mock.received) == 1


# --- audit chain ----------------------------------------------------------------------------

def _log(tmp_path, keys=None, n=4):
    p = str(tmp_path / "audit.jsonl")
    led = audit.ChainedLedger(p, keys)
    for i in range(n):
        led.write(request_id=f"req_{i}", decision="SENT", entity_counts={"PERSON:TOKENIZE": i})
    return Path(p)


@pytest.mark.parametrize("tamper", [
    lambda ls: [ls[0], ls[1].replace('"SENT"', '"BLOCKED"'), *ls[2:]],       # edit
    lambda ls: [ls[0], *ls[2:]],                                              # delete middle
    lambda ls: [ls[1], ls[0], *ls[2:]],                                       # reorder
    lambda ls: [*ls[:2], ls[1], *ls[2:]],                                     # duplicate / insert
    lambda ls: ls[:-1] + [ls[-1][:-5]],                                       # torn last row
])
def test_chain_detects_tampering(tmp_path, tamper):
    p = _log(tmp_path)
    assert audit.verify(str(p))[0] == 4
    p.write_text("\n".join(tamper(p.read_text().splitlines())) + "\n")
    with pytest.raises(audit.AuditChainError):
        audit.verify(str(p))
    assert audit.main(["verify", str(p), "--no-mac"]) == 1
    with pytest.raises(audit.AuditChainError):     # never appended to
        audit.ChainedLedger(str(p))


def test_mac_stops_a_recomputed_chain(tmp_path):
    kr = K.TenantKeyring(ring())
    p = _log(tmp_path, kr)
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    rows[1]["decision"] = "BLOCKED"
    prev, out = audit.GENESIS, []
    for r in rows:   # attacker without the key rebuilds seq/prev but cannot redo the MACs
        r["prev"] = prev
        line = json.dumps(r, sort_keys=True)
        out.append(line)
        prev = hashlib.sha256(line.encode()).hexdigest()
    p.write_text("\n".join(out) + "\n")
    assert audit.verify(str(p), require_mac=False)[0] == 4      # chain alone is fooled
    with pytest.raises(audit.AuditChainError, match="MAC"):
        audit.verify(str(p), kr)
    stripped = [{k: v for k, v in json.loads(x).items() if k not in ("mac", "kid")} for x in out]
    p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in stripped))
    with pytest.raises(audit.AuditChainError):                  # MACs cannot be stripped either
        audit.verify(str(p), kr)


def test_rows_stay_content_free_and_reserved_fields_rejected(tmp_path):
    led = audit.ChainedLedger(str(tmp_path / "a.jsonl"))
    with pytest.raises(ValueError):
        led.write(seq=99)
