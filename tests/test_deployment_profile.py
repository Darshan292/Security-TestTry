"""Sealed deployment profile (audit S4): in production every security-relevant setting comes from the signed
registry/deployment.yaml, and the environment cannot weaken it. Synthetic data only."""
import dataclasses
import hashlib
import json
import logging
import os
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from pgw.gateway import settings as S
from pgw.gateway.app import create_app
from pgw.gateway.settings import ProfileError, Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product import integrity
from tests.sealed import TEST_NER_OFF, attest, production, signing_key

ROOT = Path(__file__).resolve().parents[1]
KEY = "profile-key"
APPS = [{"app_id": "pilot", "tenant": "acme", "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(),
         "restore_output": True, "models": ["mock-1"], "purposes": ["ticket_summary"],
         "tasks": {"ticket_summary": str(ROOT / "contracts/ticket_summary.yaml")}}]
DEFAULTS = {f.name: f.default for f in dataclasses.fields(Settings)}
ENV_FIELDS = [f for f in S.PROFILE_FIELDS if S.PROFILE_ENV[f]]


def runtime(tmp):
    return dict(ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                master_key=os.urandom(32).hex(), upstream=MockLLMUpstream("summarise"))


def env_for(cfg, tmp, **extra):
    """A production environment for from_env, with no profile-pinned variable unless given in `extra`."""
    return {"PGW_MODE": "production", "PGW_INTEGRITY_MANIFEST": cfg["integrity_manifest"],
            "PGW_TRUSTED_CONFIG_PUBKEY": cfg["trusted_config_pubkey"], "PGW_MASTER_KEY": os.urandom(32).hex(),
            "PGW_LEDGER_DB": str(tmp / "l.sqlite"), "PGW_EGRESS_LEDGER": str(tmp / "e.jsonl"),
            "PGW_REPLAY_DB": str(tmp / "replay.sqlite"), "PGW_MIN_CONFIG_SERIAL": str(cfg["min_config_serial"]),
            "PGW_STATE_DIR": cfg["state_dir"], **extra}


def different(field: str, tmp: Path):
    """(Settings value, env string) that differs from the default test profile for `field`."""
    kind = S.PROFILE_KIND[field].rstrip("?")
    if kind == "path":
        p = str(tmp / "elsewhere" / "file.yaml")
        return p, p
    if field == "upstream_kind":
        return "anthropic", "anthropic"
    if field == "ner_model":
        return "en_core_web_sm", "en_core_web_sm"
    if kind == "str":
        v = "https://elsewhere.example/v1" if "url" in field else "elsewhere-region"
        return v, v
    if kind == "bool":
        return (not DEFAULTS[field]), ("1" if not DEFAULTS[field] else "0")
    if kind in ("int", "int0"):
        return DEFAULTS[field] + 1, str(DEFAULTS[field] + 1)
    if kind == "float":
        return DEFAULTS[field] + 1.5, str(DEFAULTS[field] + 1.5)
    if kind == "hosts":
        return ("evil.example",), "evil.example"
    raise AssertionError(f"no test value for kind {kind}")


# --- classification ---------------------------------------------------------------------------------------------

def test_every_settings_field_is_classified():
    names = {f.name for f in dataclasses.fields(Settings)}
    assert set(S.FIELD_CLASS) == names, "classify new Settings fields in pgw/gateway/settings.py FIELD_CLASS"
    assert set(S.FIELD_CLASS.values()) <= {S.PROFILE, S.CODE, S.OPERATIONAL}
    assert set(S.PROFILE_KIND) == set(S.PROFILE_FIELDS) == set(S.PROFILE_ENV)
    for n in names:                                 # and the docstring table names every field
        assert n in S.__doc__, n


def test_the_environment_never_sets_a_code_only_field():
    env = {v: "1" for v in S.PROFILE_ENV.values() if v} | {f"PGW_{f.upper()}": "1" for f in S.FIELD_CLASS}
    env.update(PGW_NER_WORKERS="1", PGW_NER_QUEUE_MAX="1", PGW_MAX_BODY_BYTES="1", PGW_MAX_CONTENT_CHARS="1",
               PGW_MAX_MESSAGES="1", PGW_MAX_CONCURRENT_PROTECT="1")
    s = Settings.from_env(env)
    for f, c in S.FIELD_CLASS.items():
        if c == S.CODE:
            assert getattr(s, f) == DEFAULTS[f], f


# Development switches are named only via the classification (tests/test_mandatory_contracts.py scans for them).
DEV_SWITCHES = [f for f, c in S.FIELD_CLASS.items() if c == S.CODE and isinstance(DEFAULTS[f], bool)]


@pytest.mark.parametrize("switch", [*DEV_SWITCHES, "trace"])
def test_code_only_development_switches_are_refused_in_production(tmp_path, switch):
    assert len(DEV_SWITCHES) == 2
    cfg = production(tmp_path, APPS)
    with pytest.raises(ValueError):
        create_app(Settings(**cfg, **runtime(tmp_path), **{switch: True if switch != "trace" else print}))


def test_shipped_profile_holds_the_current_defaults_and_is_sealed():
    vals, just = S.parse_profile((ROOT / S.PROFILE_REL).read_bytes())
    assert vals["ner_model"] == "en_core_web_lg" and just is None
    assert vals["egress_allowlist"] == ("api.anthropic.com",) and vals["provider_zero_retention"] is False
    diffs = S.profile_differences(Settings.from_env({}), vals, ROOT, only_set=False)
    assert set(diffs) <= {"ner_workers"}             # from_env's worker default depends on the CPU count
    assert S.PROFILE_REL in json.loads((ROOT / "policies/MANIFEST.json").read_text())["files"]


# --- production: the profile is authoritative -------------------------------------------------------------------

def test_signed_profile_starts_and_its_values_are_used(tmp_path):
    mock = MockLLMUpstream("summarise")
    cfg = production(tmp_path, APPS, deployment={"max_body_bytes": 4096})
    client = TestClient(create_app(Settings(**cfg, **{**runtime(tmp_path), "upstream": mock})))
    body = {"model": "mock-1", "messages": [{"role": "user", "content": "x" * 5000}]}
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary", **attest("ticket_summary", body)}
    r = client.post("/v1/chat/completions", headers=h, json=body)
    assert r.status_code == 413 and not mock.received


@pytest.mark.parametrize("field", S.PROFILE_FIELDS)
def test_a_settings_value_differing_from_the_profile_stops_production(tmp_path, field):
    cfg = production(tmp_path, APPS)
    value, _ = different(field, tmp_path)
    with pytest.raises(ProfileError, match=field) as e:
        create_app(Settings(**{**cfg, field: value}, **runtime(tmp_path)))
    assert str(value) not in str(e.value)               # the setting is named, its value never


@pytest.mark.parametrize("field", ENV_FIELDS)
def test_an_environment_value_differing_from_the_profile_stops_production(tmp_path, field):
    cfg = production(tmp_path, APPS)
    _, raw = different(field, tmp_path)
    s = Settings.from_env(env_for(cfg, tmp_path, **{S.PROFILE_ENV[field]: raw}))
    s.upstream = MockLLMUpstream("summarise")
    with pytest.raises(ProfileError, match=S.PROFILE_ENV[field]) as e:
        create_app(s)
    assert raw not in str(e.value)


def test_unset_or_equal_environment_values_are_fine(tmp_path, monkeypatch):
    # ticket_summary keeps unattested input in-boundary, so the signed profile names the private endpoint. Production
    # probes its identity at startup (GET /v1/models, re-audit N2); no server runs here, so the probe answers as a
    # real in-boundary model would.
    from pgw.gateway.upstream import OpenAICompatUpstream
    monkeypatch.setattr(OpenAICompatUpstream, "identify", lambda self: {"data": [{"id": "local", "object": "model"}]})
    cfg = production(tmp_path, APPS, deployment={"private_upstream_url": "http://127.0.0.1:8090/v1"})
    create_app(Settings.from_env(env_for(cfg, tmp_path)))     # nothing pinned in env: the profile decides
    same = {"PGW_MAX_BODY_BYTES": str(DEFAULTS["max_body_bytes"]), "PGW_NER_MODEL": "none",
            "PGW_EGRESS_ALLOWLIST": "API.anthropic.com", "PGW_UPSTREAM": "mock",
            "PGW_POLICY": str(Path(cfg["policy_path"])), "PGW_PROTECT_TIMEOUT_S": "10"}
    create_app(Settings.from_env(env_for(cfg, tmp_path, **same)))


@pytest.mark.parametrize("env", [
    {"PGW_NER_MODEL": "none"}, {"PGW_NER_WORKERS": "0"}, {"PGW_MAX_BODY_BYTES": "100000000"},
    {"PGW_MAX_CONTENT_CHARS": "100000000"}, {"PGW_PROTECT_TIMEOUT_S": "600"},
    {"PGW_EGRESS_ALLOWLIST": "api.anthropic.com,evil.example"}, {"PGW_ROSTER": ""},
    {"PGW_UPSTREAM": "anthropic", "PGW_PROVIDER_ZERO_RETENTION_ATTESTED": "1", "PGW_PROVIDER_REGION": "anywhere"},
], ids=lambda e: ",".join(e))
def test_from_env_in_production_cannot_weaken_the_signed_profile(tmp_path, env):
    roster = ROOT / "tests/fixtures/slice_roster.jsonl"
    cfg = production(tmp_path, APPS, roster=roster,
                     deployment={"ner_model": "en_core_web_lg", "ner_workers": 2})   # NER on (never loaded here)
    with pytest.raises(ProfileError, match="differs from the signed deployment profile"):
        create_app(Settings.from_env(env_for(cfg, tmp_path, **env)))


def test_an_edited_profile_without_re_signing_stops_startup(tmp_path):
    cfg = production(tmp_path, APPS)
    s = Settings(**cfg, **runtime(tmp_path))
    create_app(s)
    prof = Path(cfg["integrity_manifest"]).parents[1] / S.PROFILE_REL
    prof.write_text(prof.read_text().replace("max_body_bytes: 262144", "max_body_bytes: 99999999"))
    with pytest.raises(integrity.IntegrityError, match="deployment.yaml"):
        create_app(s)
    integrity.seal(Path(cfg["integrity_manifest"]))                     # attacker re-seals without the key
    with pytest.raises(integrity.IntegrityError, match="not signed"):
        create_app(s)
    other, _ = integrity.generate_signing_key()
    integrity.seal(Path(cfg["integrity_manifest"]), sign_key=other)     # or with their own key
    with pytest.raises(integrity.IntegrityError, match="does not verify"):
        create_app(s)
    integrity.seal(Path(cfg["integrity_manifest"]), sign_key=signing_key()[0])   # the approver signs: starts
    create_app(s)


def test_a_missing_or_unsealed_profile_stops_production(tmp_path):
    cfg = production(tmp_path, APPS)
    prof = Path(cfg["integrity_manifest"]).parents[1] / S.PROFILE_REL
    doc = json.loads(Path(cfg["integrity_manifest"]).read_text())
    prof.unlink()
    with pytest.raises(ProfileError, match="requires the signed deployment profile"):
        create_app(Settings(**cfg, **runtime(tmp_path)))
    # A manifest from before the profile existed (signed, but without it) cannot be used to drop it either.
    cfg2 = production(tmp_path / "b", APPS)
    m2 = Path(cfg2["integrity_manifest"])
    d2 = json.loads(m2.read_text())
    del d2["files"][S.PROFILE_REL]
    integrity_files = d2["files"]
    assert doc["files"].keys() - integrity_files.keys() == {S.PROFILE_REL}
    m2.write_text(json.dumps({"files": integrity_files, "signature": None,
                              "ed25519": integrity._ed_sign(signing_key()[0], integrity_files)}))
    with pytest.raises(integrity.IntegrityError, match="not in the integrity manifest"):
        create_app(Settings(**cfg2, **runtime(tmp_path)))


@pytest.mark.parametrize("dep", [{"ner_off_justification": None}, {"ner_off_justification": "tests"},
                                 {"ner_off_justification": "     short text      "}])
def test_ner_off_without_a_signed_justification_stops_production(tmp_path, dep):
    cfg = production(tmp_path, APPS, deployment=dep)
    with pytest.raises(ProfileError, match="NER off"):
        create_app(Settings(**cfg, **runtime(tmp_path)))


def test_ner_off_with_a_signed_justification_starts(tmp_path, caplog):
    cfg = production(tmp_path, APPS, deployment={"ner_off_justification": TEST_NER_OFF})
    with caplog.at_level(logging.WARNING, logger="pgw.gateway"):
        create_app(Settings(**cfg, **runtime(tmp_path)))
    assert any("ner_disabled_for_contracted_purposes" in r.getMessage() for r in caplog.records)


def test_a_justification_with_ner_on_is_rejected(tmp_path):
    cfg = production(tmp_path, APPS, deployment={"ner_model": "en_core_web_lg"})
    prof = Path(cfg["integrity_manifest"]).parents[1] / S.PROFILE_REL
    doc = yaml.safe_load(prof.read_text())
    doc["ner_off_justification"] = TEST_NER_OFF
    prof.write_text(yaml.safe_dump(doc))
    integrity.seal(Path(cfg["integrity_manifest"]), sign_key=signing_key()[0])
    with pytest.raises(ProfileError, match="only valid with ner_model: null"):
        create_app(Settings(**cfg, **runtime(tmp_path)))


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d["settings"].pop("max_messages"), "missing"),
    (lambda d: d["settings"].update(master_key="ab"), "unknown setting"),
    (lambda d: d["settings"].update(max_messages="200"), "max_messages"),
    (lambda d: d["settings"].update(ner_workers=-1), "ner_workers"),
    (lambda d: d["settings"].update(policy_path="../outside.yaml"), "policy_path"),
    (lambda d: d["settings"].update(egress_allowlist=[]), "egress_allowlist"),
    (lambda d: d["settings"].update(upstream_kind="somewhere"), "upstream_kind"),
    (lambda d: d.update(kind="something-else"), "kind"),
])
def test_malformed_profiles_are_refused(tmp_path, mutate, match):
    cfg = production(tmp_path, APPS)
    prof = Path(cfg["integrity_manifest"]).parents[1] / S.PROFILE_REL
    doc = yaml.safe_load(prof.read_text())
    mutate(doc)
    prof.write_text(yaml.safe_dump(doc))
    integrity.seal(Path(cfg["integrity_manifest"]), sign_key=signing_key()[0])
    with pytest.raises(ProfileError, match=match):
        create_app(Settings(**cfg, **runtime(tmp_path)))


# --- development: the profile is advisory -----------------------------------------------------------------------

def test_development_tolerates_differences_with_warnings(tmp_path, caplog):
    cfg = production(tmp_path, APPS, sign=False)
    mock = MockLLMUpstream("summarise")
    dev = {**cfg, "mode": "development", "trusted_config_pubkey": None, "max_body_bytes": 4096,
           "egress_allowlist": ("api.anthropic.com", "other.example")}
    with caplog.at_level(logging.WARNING, logger="pgw.gateway"):
        client = TestClient(create_app(Settings(**dev, **{**runtime(tmp_path), "upstream": mock})))
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "deployment_profile_differs_development_only" in msgs
    assert "'max_body_bytes'" in msgs and "'egress_allowlist'" in msgs and "4096" not in msgs
    body = {"model": "mock-1", "messages": [{"role": "user", "content": "x" * 5000}]}
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary", **attest("ticket_summary", body)}
    r = client.post("/v1/chat/completions", headers=h, json=body)
    assert r.status_code == 413                      # development: the Settings value wins


def test_development_without_a_profile_starts(tmp_path):
    cfg = production(tmp_path, APPS, sign=False)
    (Path(cfg["integrity_manifest"]).parents[1] / S.PROFILE_REL).unlink()
    integrity.seal(Path(cfg["integrity_manifest"]))
    create_app(Settings(**{**cfg, "mode": "development", "trusted_config_pubkey": None}, **runtime(tmp_path)))
