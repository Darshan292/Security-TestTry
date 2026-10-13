"""Additional guardrails for production startup and concurrent source-attestation replay."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product.attestation import SQLiteReplayGuard
from tests.sealed import ROOT, production, sha


def test_production_environment_rejects_disabled_ner():
    with pytest.raises(ValueError, match="PGW_NER_MODEL cannot be disabled"):
        Settings.from_env({"PGW_MODE": "production", "PGW_NER_MODEL": "off"})


def test_direct_production_settings_cannot_bypass_ner_startup_guard(tmp_path):
    key = "ner-startup-guard-test"
    app = {
        "app_id": "pilot",
        "tenant": "acme",
        "key_sha256": sha(key),
        "purposes": ["ticket_summary"],
        "restore_output": True,
        "models": ["mock-1"],
        "tasks": {"ticket_summary": str(ROOT / "contracts" / "ticket_summary.yaml")},
    }
    cfg = production(tmp_path, [app])
    cfg.pop("ner_detector_override", None)
    settings = Settings(
        **cfg,
        ner_model=None,
        master_key=os.urandom(32).hex(),
        ledger_db_path=str(tmp_path / "restore.sqlite"),
        egress_ledger_path=str(tmp_path / "egress.jsonl"),
        upstream=MockLLMUpstream("summarise"),
    )
    with pytest.raises(ValueError, match="requires a local NER model"):
        create_app(settings)


def test_attestation_nonce_claim_is_atomic_under_concurrency(tmp_path):
    guard = SQLiteReplayGuard(tmp_path / "attestations.sqlite3")
    def claim(_):
        return guard.claim(
            sanitizer="helpdesk-ticket-export@1",
            nonce="same-nonce-0123456789",
            tenant="acme",
            app_id="pilot",
            expires_at=2_000_000_000,
        )
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(claim, range(24)))
    assert sum(results) == 1, "the same attestation nonce was accepted more than once"


def test_attestation_nonce_scopes_are_distinct(tmp_path):
    guard = SQLiteReplayGuard(tmp_path / "attestations.sqlite3")
    assert guard.claim(sanitizer="helpdesk-ticket-export@1", nonce="nonce-000000000001",
                       tenant="acme", app_id="pilot", expires_at=2_000_000_000)
    assert guard.claim(sanitizer="helpdesk-ticket-export@1", nonce="nonce-000000000001",
                       tenant="acme", app_id="other-app", expires_at=2_000_000_000)
