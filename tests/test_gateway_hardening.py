"""Production-engineering hardening of the product slice: integrity, size/time/concurrency limits, local NER on
contracted purposes, buffered output, egress destination, and no silent failure in the stream path.

Synthetic data only. Every "nothing was sent" assertion reads what the upstream actually received.
"""
import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from pgw.gateway.app import build_upstream, create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product import integrity
from pgw.product.contracts import ContractError

ROOT = Path(__file__).resolve().parents[1]
KEY = "pilot-key"
TICKET = ("Ticket PAY-88231 | Raised by: Imran Ansari (KD-11914)\n"
          "Imran Ansari: my TDS is wrong. PAN ZXWPA3889P. Contact imran.ansari@kestrel-dyn.in.\n"
          "Payroll Ops: refund approved by the payroll lead Suresh Menon for the November cycle.\n")
RAW = ["Imran Ansari", "KD-11914", "ZXWPA3889P", "imran.ansari@kestrel-dyn.in"]


def sha(k):
    return hashlib.sha256(k.encode()).hexdigest()


def registry(tmp, contracts_dir=ROOT / "contracts"):
    reg = tmp / f"apps-{abs(hash(str(contracts_dir)))}.yaml"
    reg.write_text(yaml.safe_dump({"apps": [
        {"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY),
         "purposes": ["ticket_summary", "salary_comparison", "esi_letter"], "restore_output": True,
         "models": ["mock-1"],
         "tasks": {"ticket_summary": str(contracts_dir / "ticket_summary.yaml"),
                   "salary_comparison": str(contracts_dir / "salary_comparison.yaml"),
                   "esi_letter": str(contracts_dir / "esi_letter.yaml")}}]}))
    return reg


def settings(tmp, upstream=None, **kw):
    base = dict(policy_path=str(ROOT / "policies/hris-default.yaml"), registry_path=str(registry(tmp)),
                roster_path=str(ROOT / "tests/fixtures/slice_roster.jsonl"),
                ledger_db_path=str(tmp / "ledger.sqlite"), egress_ledger_path=str(tmp / "egress.jsonl"),
                master_key=os.urandom(32).hex(), upstream=upstream or MockLLMUpstream("summarise"))
    base.update(kw)
    return Settings(**base)


def call(client, content, purpose="ticket_summary", stream=False, headers=None, raw=None):
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose, **(headers or {})}
    if raw is not None:
        return client.post("/v1/chat/completions", headers={**h, "content-type": "application/json"}, content=raw)
    return client.post("/v1/chat/completions", headers=h,
                       json={"model": "mock-1", "stream": stream, "messages": [{"role": "user", "content": content}]})


# --- integrity: the repo's sealed manifest is used by default; any drift stops startup -------------------------

def sealed_tree(tmp):
    """A copy of policies/ and contracts/ with its own manifest (base = manifest's grandparent)."""
    for d in ("policies", "contracts"):
        shutil.copytree(ROOT / d, tmp / d, ignore=shutil.ignore_patterns("MANIFEST.json"))
    return tmp / "policies" / "MANIFEST.json"


def tree_settings(tmp, manifest, **kw):
    return settings(tmp, policy_path=str(tmp / "policies/hris-default.yaml"),
                    registry_path=str(registry(tmp, tmp / "contracts")), integrity_manifest=str(manifest), **kw)


def test_repo_manifest_matches_the_shipped_policy_and_contracts(tmp_path):
    create_app(settings(tmp_path))      # default integrity_manifest = policies/MANIFEST.json


def test_integrity_digest_is_recorded_in_every_ledger_row(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    call(client, TICKET)
    row = json.loads((tmp_path / "egress.jsonl").read_text().splitlines()[0])
    assert len(row["integrity"]) == 16 and row["integrity"] != "unchecked"


@pytest.mark.parametrize("tamper", [
    lambda t: (t / "contracts/ticket_summary.yaml").write_text(
        (t / "contracts/ticket_summary.yaml").read_text().replace("C2: GENERALIZE", "C2: REDACT")),
    lambda t: (t / "policies/hris-default.yaml").write_text((t / "policies/hris-default.yaml").read_text() + "\n# x\n"),
    lambda t: (t / "contracts/esi_letter.yaml").unlink() or (t / "contracts/esi_letter.yaml").write_text(
        (ROOT / "contracts/esi_letter.yaml").read_text().replace("version: 1", "version: 2", 1)),
], ids=["contract edited", "policy edited", "version changed"])
def test_unreviewed_change_stops_startup(tmp_path, tamper):
    m = sealed_tree(tmp_path)
    integrity.seal(m)
    create_app(tree_settings(tmp_path, m))              # sealed tree starts
    tamper(tmp_path)
    with pytest.raises(integrity.IntegrityError):
        create_app(tree_settings(tmp_path, m))


def test_unsealed_contract_stops_startup(tmp_path):
    m = sealed_tree(tmp_path)
    integrity.seal(m)
    doc = json.loads(m.read_text())
    del doc["files"]["contracts/esi_letter.yaml"]
    m.write_text(json.dumps(doc))
    with pytest.raises(integrity.IntegrityError):
        create_app(tree_settings(tmp_path, m))


def test_signed_manifest(tmp_path):
    m = sealed_tree(tmp_path)
    k = os.urandom(32)
    integrity.seal(m, k)
    create_app(tree_settings(tmp_path, m, policy_signing_key=k.hex(), require_signed_policy=True))
    with pytest.raises(integrity.IntegrityError):          # wrong key
        create_app(tree_settings(tmp_path, m, policy_signing_key=os.urandom(32).hex()))
    doc = json.loads(m.read_text())
    doc["files"]["contracts/esi_letter.yaml"]["version"] = 9   # manifest edited after signing
    m.write_text(json.dumps(doc))
    with pytest.raises(integrity.IntegrityError):
        create_app(tree_settings(tmp_path, m, policy_signing_key=k.hex()))
    integrity.seal(m)                                     # unsigned manifest
    with pytest.raises(integrity.IntegrityError):
        create_app(tree_settings(tmp_path, m, require_signed_policy=True))


def test_env_settings_have_no_integrity_off_switch():
    assert Settings.from_env({}).integrity_manifest.endswith("policies/MANIFEST.json")
    assert Settings.from_env({}).ner_model == "en_core_web_lg"


# --- input size, time and concurrency limits (fail closed, nothing sent) -----------------------------------------

def test_oversized_body_is_refused_before_parsing(tmp_path):
    mock = MockLLMUpstream("summarise")
    client = TestClient(create_app(settings(tmp_path, mock, max_body_bytes=4096)))
    r = call(client, None, raw=json.dumps({"model": "mock-1", "messages": [{"role": "user", "content": "x" * 5000}]}))
    assert r.status_code == 413 and r.json()["error"]["type"] == "request_too_large" and not mock.received


def test_content_and_message_count_limits(tmp_path):
    mock = MockLLMUpstream("summarise")
    client = TestClient(create_app(settings(tmp_path, mock, max_content_chars=1000, max_messages=3)))
    assert call(client, "a" * 1001).status_code == 413
    many = {"model": "mock-1", "messages": [{"role": "user", "content": "hi"}] * 4}
    r = call(client, None, raw=json.dumps(many))
    assert r.status_code == 413 and not mock.received


def _slow_protect(monkeypatch, seconds):
    import pgw.pipeline as P
    orig = P.Protector.protect

    def slow(self, *a, **k):
        time.sleep(seconds)
        return orig(self, *a, **k)
    monkeypatch.setattr(P.Protector, "protect", slow)


def test_protection_deadline_fails_closed(tmp_path, monkeypatch):
    _slow_protect(monkeypatch, 1.0)
    mock = MockLLMUpstream("summarise")
    client = TestClient(create_app(settings(tmp_path, mock, protect_timeout_s=0.2)))
    t = time.perf_counter()
    r = call(client, TICKET)
    assert time.perf_counter() - t < 0.9
    assert r.status_code == 503 and r.json()["error"]["type"] == "protection_timeout" and not mock.received
    row = json.loads((tmp_path / "egress.jsonl").read_text().splitlines()[0])
    assert row["decision"] == "BLOCKED" and row["reason"] == "protection_timeout"


def test_timed_out_work_keeps_its_slot(tmp_path, monkeypatch):
    """A deadline does not free CPU (threads cannot be killed); the slot stays taken, so callers cannot pile up."""
    _slow_protect(monkeypatch, 1.0)
    mock = MockLLMUpstream("summarise")
    client = TestClient(create_app(settings(tmp_path, mock, protect_timeout_s=0.2, max_concurrent_protect=1)))
    assert call(client, TICKET).json()["error"]["type"] == "protection_timeout"
    r = call(client, TICKET)
    assert r.status_code == 503 and r.json()["error"]["type"] == "gateway_busy" and not mock.received
    time.sleep(1.0)
    monkeypatch.undo()
    assert call(client, TICKET).status_code == 200          # slot released once the worker finished


# --- output stays buffered for contracted purposes -------------------------------------------------------------

def test_contracted_purposes_must_be_buffered(tmp_path):
    pol = yaml.safe_load((ROOT / "policies/hris-default.yaml").read_text())
    pol["stream_mode"]["RESTRICTED"] = "incremental"
    p = tmp_path / "pol.yaml"
    p.write_text(yaml.safe_dump(pol))
    with pytest.raises(ContractError):
        create_app(settings(tmp_path, policy_path=str(p), integrity_manifest=None))


def test_streamed_contract_response_releases_nothing_before_the_upstream_finishes(tmp_path):
    class Slow(MockLLMUpstream):
        async def stream(self, body):
            self.received.append(self.wire_bytes(body))
            yield json.dumps({"id": "x", "created": 0, "model": "m",
                              "choices": [{"index": 0, "delta": {"content": "Ticket handled for "}}]})
            raise RuntimeError("connection reset")
    client = TestClient(create_app(settings(tmp_path, Slow("summarise"))))
    r = call(client, TICKET, stream=True)
    assert "Ticket handled" not in r.text and "stream_interrupted" in r.text   # buffered: partial text never sent


# --- the stream path no longer swallows a restore-ledger failure ------------------------------------------------

def test_stream_ledger_failure_is_logged_not_swallowed(tmp_path, monkeypatch, caplog):
    class Broken(MockLLMUpstream):
        async def stream(self, body):
            self.received.append(self.wire_bytes(body))
            yield json.dumps({"id": "x", "created": 0, "model": "m",
                              "choices": [{"index": 0, "delta": {"content": "partial"}}]})
            raise RuntimeError("connection reset")
    import pgw.detect.edm as E
    monkeypatch.setattr(E.RestoreLedger, "record", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    client = TestClient(create_app(settings(tmp_path, Broken("summarise"))))
    with caplog.at_level(logging.INFO, logger="pgw.gateway"):
        r = call(client, TICKET, stream=True, headers={"X-PGW-Conversation-Id": "conv-1"})
    assert "stream_interrupted" in r.text
    crit = [rec for rec in caplog.records if rec.levelno == logging.CRITICAL]
    assert crit and "restore_ledger_record_failed" in crit[0].getMessage()


# --- egress destination ------------------------------------------------------------------------------------------

def test_real_providers_only_reach_allowlisted_destinations():
    ok = Settings(upstream_kind="anthropic", anthropic_api_key="k")
    assert build_upstream(ok).name == "anthropic"
    for bad in (Settings(upstream_kind="anthropic", anthropic_base_url="https://evil.example"),
                Settings(upstream_kind="anthropic", anthropic_base_url="http://api.anthropic.com"),
                Settings(upstream_kind="openai"),       # default allowlist has no OpenAI host
                Settings(upstream_kind="openai", upstream_url="https://api.anthropic.com.evil.example/v1")):
        with pytest.raises(ValueError):
            build_upstream(bad)


def test_sdk_base_url_env_cannot_redirect_the_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    up = build_upstream(Settings(upstream_kind="anthropic", anthropic_api_key="k"))
    assert str(up._client.base_url).startswith("https://api.anthropic.com")


# --- local NER on contracted purposes ----------------------------------------------------------------------------

spacy = pytest.importorskip("spacy")
needs_lg = pytest.mark.skipif(not spacy.util.is_package("en_core_web_lg"), reason="en_core_web_lg not installed")


@needs_lg
def test_ner_protects_names_outside_the_roster_on_path_a(tmp_path):
    for ner, leaked in ((None, True), ("en_core_web_lg", False)):
        mock = MockLLMUpstream("summarise")
        client = TestClient(create_app(settings(tmp_path, mock, ner_model=ner)))
        assert call(client, TICKET).status_code == 200
        wire = mock.received[0].decode()
        assert ("Suresh Menon" in wire) is leaked
        assert not any(v in wire for v in RAW)


def test_ner_model_that_does_not_load_stops_startup(tmp_path):
    from pgw.detect.ner import NerUnavailable
    with pytest.raises(NerUnavailable):
        create_app(settings(tmp_path, ner_model="no_such_model_xyz"))


@needs_lg
def test_no_raw_values_in_logs_errors_or_ledger(tmp_path, caplog):
    class Down(MockLLMUpstream):
        async def complete(self, body):
            raise RuntimeError("provider said: " + json.dumps(body))
    with caplog.at_level(logging.DEBUG):
        for up in (MockLLMUpstream("summarise"), Down("summarise")):
            client = TestClient(create_app(settings(tmp_path, up, ner_model="en_core_web_lg")))
            r = call(client, TICKET)
            assert "ZXWPA3889P" not in r.text                   # C3 is never returned
            if r.status_code != 200:                            # errors carry no values at all
                assert r.status_code == 502 and not any(v in r.text for v in RAW + ["Suresh Menon"])
    logs = caplog.text + (tmp_path / "egress.jsonl").read_text()
    assert not any(v in logs for v in RAW + ["Suresh Menon", "PAY-88231"])
