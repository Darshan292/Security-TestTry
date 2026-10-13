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
from tests.sealed import TEST_NER, TEST_SANITIZER_KEYS, production, signing_key, source
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


def apps(contracts_dir=ROOT / "contracts"):
    return [{"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY),
             "purposes": ["ticket_summary", "salary_comparison", "esi_letter"], "restore_output": True,
             "models": ["mock-1"],
             "tasks": {"ticket_summary": str(contracts_dir / "ticket_summary.yaml"),
                       "salary_comparison": str(contracts_dir / "salary_comparison.yaml"),
                       "esi_letter": str(contracts_dir / "esi_letter.yaml")}}]


def settings(tmp, upstream=None, policy=None, **kw):
    """Production mode over a sealed, signed copy of the config (tests/sealed.py)."""
    base = production(tmp, apps(), policy=policy)
    base.update(roster_path=str(ROOT / "tests/fixtures/slice_roster.jsonl"),
                ledger_db_path=str(tmp / "ledger.sqlite"), egress_ledger_path=str(tmp / "egress.jsonl"),
                master_key=os.urandom(32).hex(), upstream=upstream or MockLLMUpstream("summarise"),
                sanitizer_hmac_keys=TEST_SANITIZER_KEYS, ner_detector_override=TEST_NER)
    base.update(kw)
    return Settings(**base)


def call(client, content, purpose="ticket_summary", stream=False, headers=None, raw=None):
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose}
    if raw is not None:
        h.update(source(purpose)); h.update(headers or {})
        return client.post("/v1/chat/completions", headers={**h, "content-type": "application/json"}, content=raw)
    body = {"model": "mock-1", "stream": stream, "messages": [{"role": "user", "content": content}]}
    if purpose == "ticket_summary":   # only the requester is declared; everything else relies on detection
        body["pgw_subjects"] = {"requester": {"name": "Imran Ansari"}}
    h.update(source(purpose, body)); h.update(headers or {})
    return client.post("/v1/chat/completions", headers=h, json=body)


# --- integrity: registry, policy and contracts are sealed; production needs a trusted signature ----------------

def sealed_tree(tmp):
    """A copy of registry/, policies/ and contracts/ with its own manifest (base = manifest's grandparent)."""
    for d in ("registry", "policies", "contracts"):
        shutil.copytree(ROOT / d, tmp / d, ignore=shutil.ignore_patterns("MANIFEST.json"))
    return tmp / "policies" / "MANIFEST.json"


def tree_settings(tmp, manifest, **kw):
    """The repo's own registry (hr-helpdesk-pilot, all six contracts) served from a copied, sealed tree."""
    base = dict(mode="production", trusted_config_pubkey=signing_key()[1], policy_path=str(tmp / "policies/hris-default.yaml"),
                registry_path=str(tmp / "registry/apps.yaml"), integrity_manifest=str(manifest),
                sanitizer_hmac_keys=TEST_SANITIZER_KEYS, ner_detector_override=TEST_NER,
                ledger_db_path=str(tmp / "ledger.sqlite"), egress_ledger_path=str(tmp / "egress.jsonl"),
                master_key=os.urandom(32).hex(), upstream=MockLLMUpstream("summarise"))
    base.update(kw)
    return Settings(**base)


def signed(tmp):
    m = sealed_tree(tmp)
    integrity.seal(m, sign_key=signing_key()[0])
    return m


def test_repo_manifest_seals_the_shipped_registry_policy_and_contracts(tmp_path):
    # The repo manifest is unsigned: it is valid for development only. Production needs the operator's signature.
    dev = dict(registry_path=str(ROOT / "registry/apps.yaml"), policy_path=str(ROOT / "policies/hris-default.yaml"),
               ledger_db_path=str(tmp_path / "l.sqlite"), egress_ledger_path=str(tmp_path / "e.jsonl"),
               master_key=os.urandom(32).hex(), upstream=MockLLMUpstream("summarise"))
    create_app(Settings(mode="development", **dev))
    files = json.loads((ROOT / "policies/MANIFEST.json").read_text())["files"]
    assert "registry/apps.yaml" in files
    with pytest.raises(integrity.IntegrityError, match="trusted config public key"):
        create_app(Settings(**dev))                                   # production, no trust anchor
    with pytest.raises(integrity.IntegrityError, match="not signed"):
        create_app(Settings(trusted_config_pubkey=signing_key()[1], **dev))


def test_integrity_digest_is_recorded_in_every_ledger_row(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    call(client, TICKET)
    row = json.loads((tmp_path / "egress.jsonl").read_text().splitlines()[0])
    assert len(row["integrity"]) == 16 and row["integrity"] != "unchecked" and row["mode"] == "production"


def _edit(path, old, new):
    t = path.read_text()
    assert old in t
    path.write_text(t.replace(old, new, 1))


@pytest.mark.parametrize("tamper", [
    lambda t: _edit(t / "contracts/ticket_summary.yaml", "C2: GENERALIZE", "C2: REDACT"),
    lambda t: (t / "policies/hris-default.yaml").write_text((t / "policies/hris-default.yaml").read_text() + "\n# x\n"),
    lambda t: (t / "contracts/esi_letter.yaml").unlink() or (t / "contracts/esi_letter.yaml").write_text(
        __import__("re").sub(r"(?m)^version: \d+", "version: 99", (ROOT / "contracts/esi_letter.yaml").read_text(), 1)),
    lambda t: _edit(t / "registry/apps.yaml", "models: [mock-1,", "models: [mock-1, gpt-4o,"),
    lambda t: _edit(t / "registry/apps.yaml", "band_position]", "band_position, hr_case_summary]"),
    lambda t: _edit(t / "registry/apps.yaml", "ticket_summary.yaml", "ticket_summary_any_script.yaml"),
    lambda t: _edit(t / "registry/apps.yaml", "key_sha256: 3254", "key_sha256: 0254"),
], ids=["contract edited", "policy edited", "version changed", "registry model added", "registry purpose added",
        "registry task rebound", "registry key replaced"])
def test_unreviewed_change_stops_startup(tmp_path, tamper):
    m = signed(tmp_path)
    create_app(tree_settings(tmp_path, m))              # sealed, signed tree starts in production
    tamper(tmp_path)
    with pytest.raises(integrity.IntegrityError):
        create_app(tree_settings(tmp_path, m))


def test_resealing_without_the_trusted_key_does_not_help(tmp_path):
    m = signed(tmp_path)
    _edit(tmp_path / "registry/apps.yaml", "models: [mock-1,", "models: [mock-1, gpt-4o,")
    integrity.seal(m)                                    # attacker re-seals: unsigned
    with pytest.raises(integrity.IntegrityError, match="not signed"):
        create_app(tree_settings(tmp_path, m))
    other, _ = integrity.generate_signing_key()
    integrity.seal(m, sign_key=other)                    # attacker re-seals with their own key
    with pytest.raises(integrity.IntegrityError, match="does not verify"):
        create_app(tree_settings(tmp_path, m))
    doc = json.loads(m.read_text())
    integrity.seal(m, sign_key=signing_key()[0])         # the approver signs the reviewed change: starts
    create_app(tree_settings(tmp_path, m))
    doc2 = json.loads(m.read_text())
    doc2["files"]["registry/apps.yaml"]["sha256"] = doc["files"]["registry/apps.yaml"]["sha256"][::-1]
    m.write_text(json.dumps(doc2))                       # manifest edited after signing
    with pytest.raises(integrity.IntegrityError):
        create_app(tree_settings(tmp_path, m))


def test_unsigned_config_is_development_only(tmp_path):
    m = sealed_tree(tmp_path)
    integrity.seal(m)
    create_app(tree_settings(tmp_path, m, mode="development", trusted_config_pubkey=None))
    with pytest.raises(integrity.IntegrityError):
        create_app(tree_settings(tmp_path, m, trusted_config_pubkey=None))
    with pytest.raises(ValueError, match="integrity manifest"):
        create_app(tree_settings(tmp_path, m, integrity_manifest=None))
    with pytest.raises(ValueError, match="PGW_MODE"):
        create_app(tree_settings(tmp_path, m, mode="staging"))


def test_unsealed_contract_stops_startup(tmp_path):
    m = signed(tmp_path)
    doc = json.loads(m.read_text())
    del doc["files"]["contracts/esi_letter.yaml"]
    m.write_text(json.dumps(doc))
    with pytest.raises(integrity.IntegrityError):
        create_app(tree_settings(tmp_path, m))
    integrity.seal(m, sign_key=signing_key()[0])
    (tmp_path / "registry/apps.yaml").rename(tmp_path / "registry/other.yaml")
    with pytest.raises((integrity.IntegrityError, OSError)):
        create_app(tree_settings(tmp_path, m))


def test_legacy_hmac_signature_still_verifies(tmp_path):
    m = sealed_tree(tmp_path)
    k = os.urandom(32)
    integrity.seal(m, k, sign_key=signing_key()[0])
    create_app(tree_settings(tmp_path, m, policy_signing_key=k.hex(), require_signed_policy=True))
    with pytest.raises(integrity.IntegrityError):          # wrong HMAC key
        create_app(tree_settings(tmp_path, m, policy_signing_key=os.urandom(32).hex()))


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
        create_app(settings(tmp_path, policy=p))


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
    calls = {"n": 0}
    orig_record = E.RestoreLedger.record

    def flaky(self, *a, **k):   # the pre-send write (declared subjects) succeeds; the post-stream write fails
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("disk")
        return orig_record(self, *a, **k)
    monkeypatch.setattr(E.RestoreLedger, "record", flaky)
    client = TestClient(create_app(settings(tmp_path, Broken("summarise"))))
    with caplog.at_level(logging.INFO, logger="pgw.gateway"):
        r = call(client, TICKET, stream=True, headers={"X-PGW-Conversation-Id": "conv-1"})
    assert "stream_interrupted" in r.text
    crit = [rec for rec in caplog.records if rec.levelno == logging.CRITICAL]
    assert crit and "restore_ledger_record_failed" in crit[0].getMessage()


# --- egress destination ------------------------------------------------------------------------------------------

TERMS = {"provider_region": "in-south", "provider_zero_retention": True}


def test_real_provider_requires_attested_data_terms():
    for s in (Settings(upstream_kind="anthropic", anthropic_api_key="k"),
              Settings(upstream_kind="anthropic", anthropic_api_key="k", provider_region="in-south"),
              Settings(upstream_kind="anthropic", anthropic_api_key="k", provider_zero_retention=True)):
        with pytest.raises(ValueError):
            build_upstream(s)
    assert Settings.from_env({"PGW_PROVIDER_REGION": "in-south",
                              "PGW_PROVIDER_ZERO_RETENTION_ATTESTED": "1"}).provider_zero_retention


def test_real_providers_only_reach_allowlisted_destinations():
    ok = Settings(upstream_kind="anthropic", anthropic_api_key="k", **TERMS)
    assert build_upstream(ok).name == "anthropic"
    for bad in (Settings(upstream_kind="anthropic", anthropic_base_url="https://evil.example", **TERMS),
                Settings(upstream_kind="anthropic", anthropic_base_url="http://api.anthropic.com", **TERMS),
                Settings(upstream_kind="openai", **TERMS),       # default allowlist has no OpenAI host
                Settings(upstream_kind="openai", upstream_url="https://api.anthropic.com.evil.example/v1", **TERMS)):
        with pytest.raises(ValueError):
            build_upstream(bad)


def test_sdk_base_url_env_cannot_redirect_the_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    up = build_upstream(Settings(upstream_kind="anthropic", anthropic_api_key="k", **TERMS))
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


def test_verified_bytes_are_the_parsed_bytes(tmp_path, monkeypatch):
    """Race: the registry is tampered while it is parsed and restored before verification. The gateway must verify
    the bytes it parsed, so this stops startup instead of serving an unverified registry."""
    import pgw.gateway.app as gw
    m = signed(tmp_path)
    reg = tmp_path / "registry/apps.yaml"
    good = reg.read_text()
    reg.write_text(good.replace("models: [mock-1,", "models: [mock-1, gpt-4o,"))
    real = gw.load_registry

    def racing(path, data=None):
        out = real(path, data)
        reg.write_text(good)          # the valid file is back on disk before integrity.verify runs
        return out
    monkeypatch.setattr(gw, "load_registry", racing)
    with pytest.raises(integrity.IntegrityError):
        create_app(tree_settings(tmp_path, m))
