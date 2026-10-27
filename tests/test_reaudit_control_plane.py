"""Re-audit control-plane regressions (N5 rollback, N2 in-boundary stand-in, N8 output identifier backstop,
N9 mangled aliases). Synthetic data only."""
import hashlib
import json
import logging
import os
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from pgw import private_llm
from pgw.gateway import rollback
from pgw.gateway.app import create_app
from pgw.gateway.rollback import RollbackError
from pgw.gateway.settings import ProfileError, Settings, parse_profile
from pgw.gateway.upstream import MockLLMUpstream, OpenAICompatUpstream
from pgw.stream import Restorer
from pgw.types import Action, Entity, EntityType
from tests.sealed import attest, production, write_profile
from tests.test_private_route import HINDI, SUBJ, InBoundaryTestBackend, LocalModel

ROOT = Path(__file__).resolve().parents[1]
KEY = "reaudit-key"


def apps(purpose="ticket_summary"):
    return [{"app_id": "pilot", "tenant": "acme", "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(),
             "restore_output": True, "models": ["mock-1"], "purposes": [purpose],
             "tasks": {purpose: str(ROOT / f"contracts/{purpose}.yaml")}}]


def runtime(tmp, upstream=None):
    return dict(ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                master_key=os.urandom(32).hex(), upstream=upstream or MockLLMUpstream("summarise"))


def tree(tmp: Path, name: str, serial, **deployment):
    """A signed production config tree (all signed with the same trusted key) with the given config_serial."""
    d = tmp / name
    d.mkdir()
    return production(d, apps(), deployment={"config_serial": serial, **deployment})


def start(cfg, tmp, **over):
    return create_app(Settings(**{**cfg, **over}, **runtime(tmp)))


# ---------------------------------------------------------------------------------------------- N5: rollback

def test_audit_scenario_older_weaker_signed_tree_is_refused_by_both_mechanisms(tmp_path):
    current = tree(tmp_path, "current", 2)
    old = tree(tmp_path, "old", 1, max_content_chars=10_000_000)   # validly signed, NER off with justification
    state = current["state_dir"]
    start(current, tmp_path, min_config_serial=2)                  # records high-water mark 2
    # (1) the pinned minimum alone (fresh, empty state directory)
    fresh = tmp_path / "fresh-state"
    fresh.mkdir()
    with pytest.raises(RollbackError, match="pinned minimum"):
        start(old, tmp_path, min_config_serial=2, state_dir=str(fresh))
    # (2) the high-water mark alone (minimum pinned too low to help)
    with pytest.raises(RollbackError, match="high-water mark 2"):
        start(old, tmp_path, min_config_serial=1, state_dir=state)


def test_equal_and_higher_serials_start_and_raise_the_mark(tmp_path):
    a, b = tree(tmp_path, "a", 3), tree(tmp_path, "b", 7)
    state = a["state_dir"]
    start(a, tmp_path)
    assert rollback.read_high_water(state) == 3
    start(a, tmp_path)                                             # equal serial: restart is fine
    start(b, tmp_path, state_dir=state)                            # higher serial
    assert rollback.read_high_water(state) == 7
    start(b, tmp_path, state_dir=state, min_config_serial=7)       # equal to the pinned minimum
    with pytest.raises(RollbackError):
        start(a, tmp_path)                                         # and now 3 is a rollback


def test_deliberate_rollback_is_old_content_resigned_with_a_higher_serial(tmp_path):
    new = tree(tmp_path, "new", 5, max_content_chars=40_000)
    start(new, tmp_path)
    resigned_old = tree(tmp_path, "old", 6)                       # the old settings, a new signed change
    start(resigned_old, tmp_path, state_dir=new["state_dir"])
    assert rollback.read_high_water(new["state_dir"]) == 6


@pytest.mark.parametrize("content", [b"", b"garbage", b'{"kind": "pgw-config-high-water"}',
                                     b'{"kind": "other", "config_serial": 9}',
                                     b'{"kind": "pgw-config-high-water", "config_serial": "9"}',
                                     b'{"kind": "pgw-config-high-water", "config_serial": 0}',
                                     b'{"kind": "pgw-config-high-water", "config_serial": true}',
                                     b"\xff\xfe"], ids=range(8))
def test_corrupt_high_water_mark_fails_closed_and_is_not_reset(tmp_path, content):
    cfg = tree(tmp_path, "t", 4)
    hwm = Path(cfg["state_dir"]) / rollback.HWM_NAME
    hwm.write_bytes(content)
    with pytest.raises(RollbackError, match="corrupt"):
        start(cfg, tmp_path)
    assert hwm.read_bytes() == content                             # never overwritten


def test_unreadable_high_water_mark_fails_closed(tmp_path):
    cfg = tree(tmp_path, "t", 4)
    (Path(cfg["state_dir"]) / rollback.HWM_NAME).mkdir()            # exists, cannot be read as a file
    with pytest.raises(RollbackError, match="unreadable"):
        start(cfg, tmp_path)


def test_production_requires_serial_pinned_minimum_and_existing_state_dir(tmp_path):
    cfg = tree(tmp_path, "t", None)                                 # profile without config_serial
    with pytest.raises(RollbackError, match="config_serial"):
        start(cfg, tmp_path)
    cfg = tree(tmp_path, "u", 1)
    with pytest.raises(RollbackError, match="PGW_MIN_CONFIG_SERIAL"):
        start(cfg, tmp_path, min_config_serial=None)
    with pytest.raises(RollbackError, match="PGW_STATE_DIR"):
        start(cfg, tmp_path, state_dir=None)
    missing = tmp_path / "no-such-dir"
    with pytest.raises(RollbackError, match="does not exist"):
        start(cfg, tmp_path, state_dir=str(missing))
    assert not missing.exists()                                     # never created (a reset in disguise)


def test_high_water_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    cfg = tree(tmp_path, "t", 12)
    start(cfg, tmp_path)
    files = sorted(p.name for p in Path(cfg["state_dir"]).iterdir())
    assert files == [rollback.HWM_NAME, rollback.LOCK_NAME]
    assert json.loads((Path(cfg["state_dir"]) / rollback.HWM_NAME).read_text()) == {
        "kind": rollback.HWM_KIND, "config_serial": 12}
    assert rollback.record(12, cfg["state_dir"]) is False           # equal: unchanged
    with pytest.raises(RollbackError):
        rollback.record(11, cfg["state_dir"])                       # re-checked under the lock at record time


def test_environment_supplies_the_floor_and_state_dir():
    s = Settings.from_env({"PGW_MIN_CONFIG_SERIAL": "42", "PGW_STATE_DIR": "/var/lib/pgw"})
    assert s.min_config_serial == 42 and s.state_dir == "/var/lib/pgw"
    with pytest.raises(ValueError):
        Settings.from_env({"PGW_MIN_CONFIG_SERIAL": "-1"})


@pytest.mark.parametrize("bad", [0, -3, "7", True, 1.5])
def test_profile_serial_must_be_a_positive_integer(tmp_path, bad):
    p = write_profile(tmp_path, {"config_serial": bad})
    with pytest.raises(ProfileError, match="config_serial"):
        parse_profile(p.read_bytes())


def test_development_warns_and_never_writes_the_mark(tmp_path, caplog):
    cfg = tree(tmp_path, "t", 2)
    state = Path(cfg["state_dir"])
    with caplog.at_level(logging.WARNING, logger="pgw.gateway"):
        start(cfg, tmp_path, mode="development", min_config_serial=9)
    assert "config_serial_below_pinned_minimum_development_only" in caplog.text
    assert not (state / rollback.HWM_NAME).exists()


def test_shipped_profile_carries_a_serial():
    doc_serial = __import__("yaml").safe_load((ROOT / "registry/deployment.yaml").read_text())["config_serial"]
    assert isinstance(doc_serial, int) and doc_serial >= 1


# ---------------------------------------------------------------------------------------------- N2: stand-in

def private_cfg(tmp, private):
    return production(tmp, apps("ticket_summary_any_script"), private_upstream=private)


def call_private(client):
    body = {"model": "mock-1", "pgw_subjects": SUBJ, "messages": [{"role": "user", "content": HINDI}]}
    return client.post("/v1/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary_any_script",
                                **attest("ticket_summary_any_script", body)})


def test_stand_in_reports_itself_on_the_identity_probe():
    r = TestClient(private_llm.build_app(private_llm._Extractive())).get("/v1/models")
    entry = r.json()["data"][0]
    assert entry["id"] == "extractive-standin" and entry["pgw_backend"] == "standin"


def test_production_refuses_the_extractive_stand_in(tmp_path):
    with pytest.raises(ValueError, match="stand-in"):
        start(private_cfg(tmp_path, LocalModel(backend=private_llm._Extractive())), tmp_path)


def test_production_refuses_the_stand_in_even_when_the_model_name_claims_otherwise(tmp_path):
    cfg = private_cfg(tmp_path, LocalModel(backend=private_llm._Extractive()))
    with pytest.raises(ValueError, match="stand-in"):
        start(cfg, tmp_path, private_model="extractive-standin")


@pytest.mark.parametrize("private", [OpenAICompatUpstream("http://127.0.0.1:9/v1", ""), object()],
                         ids=["unreachable", "no-probe"])
def test_production_refuses_an_endpoint_whose_identity_cannot_be_verified(tmp_path, private):
    with pytest.raises(ValueError, match="identity cannot be verified"):
        start(private_cfg(tmp_path, private), tmp_path)


def test_production_with_a_real_model_endpoint_starts_and_marks_replies(tmp_path):
    client = TestClient(start(private_cfg(tmp_path, LocalModel()), tmp_path))
    r = call_private(client)
    assert r.status_code == 200 and r.headers["X-PGW-Private-Model"] == "model"


def test_production_withholds_a_stand_in_reply_after_the_endpoint_changed(tmp_path):
    swapped = LocalModel()   # probe answered by a real model ...
    standin_app = private_llm.build_app(private_llm._Extractive())   # ... replies now come from the stand-in
    swapped._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=standin_app))
    client = TestClient(start(private_cfg(tmp_path, swapped), tmp_path))
    r = call_private(client)
    assert r.status_code == 503 and r.json()["error"]["type"] == "private_model_unavailable"
    assert "राहुल" not in r.text


def test_development_allows_the_stand_in_loudly_and_marks_every_reply(tmp_path, caplog):
    cfg = private_cfg(tmp_path, LocalModel(backend=private_llm._Extractive()))
    with caplog.at_level(logging.WARNING, logger="pgw.gateway"):
        client = TestClient(start(cfg, tmp_path, mode="development"))
    assert "PRIVATE_MODEL_IS_EXTRACTIVE_STANDIN_NOT_AN_LLM" in caplog.text
    r = call_private(client)
    assert r.status_code == 200 and r.headers["X-PGW-Private-Model"] == "standin"


def test_the_injected_mock_answers_the_probe_as_a_model(tmp_path):
    client = TestClient(start(private_cfg(tmp_path, MockLLMUpstream("summarise")), tmp_path, mode="development"))
    r = call_private(client)
    assert r.status_code == 200 and r.headers["X-PGW-Private-Model"] == "model"   # the mock answers the probe


# ---------------------------------------------------------------------------------------------- N8: output gate

BARE_AADHAAR = "2345.6789.0124"        # Verhoeff-valid, dotted: no scheme recognizer types it
UAN = "100987654321"
ANSWER = f"Noted. Aadhaar {BARE_AADHAAR} and UAN {UAN} are on file; ticket PAY-88231 is closed."


class Fixed(MockLLMUpstream):
    """A model that writes `text` whatever it is asked."""

    def __init__(self, text):
        super().__init__("summarise")
        self.text = text

    def _text(self, body):
        return self.text


def gw(tmp, text, purpose="ticket_summary"):
    up = Fixed(text)
    cfg = production(tmp, apps(purpose))
    return TestClient(create_app(Settings(**cfg, **runtime(tmp, up)))), up


def ask(client, content, stream=False):
    body = {"model": "mock-1", "pgw_subjects": SUBJ, "messages": [{"role": "user", "content": content}]}
    if stream:
        body["stream"] = True
    return client.post("/v1/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary",
                                **attest("ticket_summary", body)})


def streamed_text(r) -> str:
    out = []
    for line in r.text.splitlines():
        if line.startswith("data: {"):
            for ch in json.loads(line[6:]).get("choices") or []:
                out.append((ch.get("delta") or {}).get("content") or "")
    return "".join(out)


@pytest.mark.parametrize("stream", [False, True], ids=["non-stream", "stream"])
def test_identifier_shaped_tokens_the_model_wrote_are_masked(tmp_path, stream):
    client, _ = gw(tmp_path, ANSWER)
    r = ask(client, "Please summarise ticket PAY-88231 for the requester.", stream)
    assert r.status_code == 200
    text = streamed_text(r) if stream else r.json()["choices"][0]["message"]["content"]
    assert BARE_AADHAAR not in text and UAN not in text and "6789" not in text
    assert text.count("[IDENTIFIER]") == 2
    assert "PAY-88231" in text                     # contract business reference format: not personal


@pytest.mark.parametrize("stream", [False, True], ids=["non-stream", "stream"])
def test_identifiers_sent_in_clear_or_restored_are_not_masked(tmp_path, stream):
    # contract business reference formats, here also sent in clear by the request, may be written back
    client, up = gw(tmp_path, "Case DC-2026-001 and request REQ-55120 are linked.")
    r = ask(client, "Case DC-2026-001 is open.", stream)
    text = streamed_text(r) if stream else r.json()["choices"][0]["message"]["content"]
    assert "DC-2026-001" in text and "REQ-55120" in text and "[IDENTIFIER]" not in text


def test_incremental_stream_masks_identifiers_across_chunk_boundaries():
    text = f"Line one.\nAadhaar {BARE_AADHAAR} noted and UAN {UAN} too.\nDone.\n"
    for step in (1, 3, 7):
        r = Restorer({}, mode="incremental")
        out = "".join(r.feed(text[i:i + step]) for i in range(0, len(text), step)) + r.finish()
        assert BARE_AADHAAR not in out and UAN not in out and out.count("[IDENTIFIER]") == 2


def test_known_ids_and_business_formats_pass_the_gate():
    import re
    r = Restorer({}, mode="buffered", known_ids=frozenset({"Z4872631"}),
                 business=(re.compile(r"^PAY-\d{4,6}$"),))
    out = r.feed("passport Z4872631, ref PAY-88231, other K9182736") + r.finish()
    assert "Z4872631" in out and "PAY-88231" in out and "K9182736" not in out and "[IDENTIFIER]" in out


def test_private_route_masks_identifier_shaped_tokens(tmp_path):
    class Echo(InBoundaryTestBackend):
        def __call__(self, messages, max_tokens):
            return "पासपोर्ट Z4872631 दर्ज है।"
    client = TestClient(start(private_cfg(tmp_path, LocalModel(backend=Echo())), tmp_path))
    r = call_private(client)
    assert r.status_code == 200 and "Z4872631" not in r.text and "[IDENTIFIER]" in r.text


def test_private_route_keeps_the_declared_requesters_employee_id(tmp_path):
    class Echo(InBoundaryTestBackend):
        def __call__(self, messages, max_tokens):
            return "KD-11914 का अनुरोध; पासपोर्ट Z4872631 दर्ज है।"
    client = TestClient(start(private_cfg(tmp_path, LocalModel(backend=Echo())), tmp_path))
    text = call_private(client).json()["choices"][0]["message"]["content"]
    assert "KD-11914" in text and "Z4872631" not in text


# ---------------------------------------------------------------------------------------------- N9: mangled aliases

PERSON = Entity(EntityType.PERSON, "Priya Raghunathan", Action.TOKENIZE, alias="[PERSON_2E68HT]")
EMAIL = Entity(EntityType.EMAIL, "priya.r@example.com", Action.TOKENIZE, alias="[EMAIL_8PESJS]")
AMAP = {"PERSON_2E68HT": PERSON, "EMAIL_8PESJS": EMAIL}


def restore(text, mode="buffered", step=None):
    r = Restorer(AMAP, mode=mode)
    if step is None:
        return r.feed(text) + r.finish()
    return "".join(r.feed(text[i:i + step]) for i in range(0, len(text), step)) + r.finish()


@pytest.mark.parametrize("mangled", [
    "[PERSON 2E68HT]", "[PERSON-2E68HT]", "[PERSON_2E68HT]", "[person 2e68ht]", "[Person-2E68ht]",
    "[ PERSON_2E68HT ]", "[PERSON\n2E68HT]", "[PERSON_2E68\nHT]", "[\nPERSON 2E68HT\n]",
    "[PERSON 2E68HT", "PERSON-2E68HT]", "［PERSON_2E68HT］", "［PERSON 2E68HT］",
])
@pytest.mark.parametrize("mode,step", [("buffered", None), ("incremental", 1), ("incremental", 4)])
def test_mangled_alias_of_this_request_is_restored(mangled, mode, step):
    out = restore(f"Please call {mangled} today.\nThanks.\n", mode, step)
    assert "Priya Raghunathan" in out and "2E68" not in out.upper()


def test_hyphenated_email_alias_is_restored():
    assert restore("Write to [EMAIL-8PESJS].") == "Write to priya.r@example.com."


@pytest.mark.parametrize("lookalike", [
    "[PERSON 2E68HX]",            # same shape, not an alias of this request
    "[PERSON 9ZZZZZ]",
    "[EMAIL 2023 REPORT]",
    "Person 2E68HT",              # no bracket at either end, space separator
    "person-2e68ht",
    "[PERSON 2E68HTQ]",           # longer code
    "[PERSON 2E68HTQ",
    "[NOTATYPE 2E68HT]",
    "[PERSON_-2E68HT]",           # two separators
    "[PHONE 2E68HT]",             # known code, other type
])
def test_lookalike_text_is_left_untouched(lookalike):
    text = f"Note: {lookalike} stays."
    assert restore(text) == text
    assert restore(text, "incremental", 3) == text


def test_streaming_holdback_for_an_unclosed_bracket_is_bounded():
    r = Restorer(AMAP, mode="incremental")
    released = r.feed("Line [PERSON\n")
    assert "[PERSON" not in released                                # held: it may still become an alias
    released += r.feed("x" * 60 + " and more words here to release\n")
    assert "[PERSON" in released                                     # cannot be an alias any more: released
    released += r.finish()
    assert released.startswith("Line [PERSON\n")
