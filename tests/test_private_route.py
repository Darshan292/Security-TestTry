"""PRIVATE (C4) route: content the gateway cannot protect reliably goes only to the in-boundary model.

Synthetic data only. The external upstream's `received` list proves nothing left the boundary.
"""
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from pgw.gateway.app import create_app
from tests.sealed import production, source
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream, OpenAICompatUpstream
from pgw.private_llm import app as private_app
from pgw.product.contracts import ContractError

ROOT = Path(__file__).resolve().parents[1]
KEY = "pilot-key"
HINDI = "राहुल शर्मा (KD-11914) का वेतन खाता 644748074748 बदलना है। कृपया पुष्टि करें।"
SUBJ = {"requester": {"name": "Rahul Sharma", "employee_id": "KD-11914"}}


class LocalModel(OpenAICompatUpstream):
    """The real pgw.private_llm app, reached over HTTP (ASGI transport), recording what it received."""
    name = "private"

    def __init__(self, fail=False):
        transport = httpx.ASGITransport(app=private_app)
        super().__init__("http://127.0.0.1:8090/v1", "", client=httpx.AsyncClient(transport=transport))
        self.received, self.fail = [], fail

    async def complete(self, body):
        self.received.append(self.wire_bytes(body))
        if self.fail:
            raise RuntimeError("local model down")
        return await super().complete(body)


def make(tmp, private=None, url=None, contract="ticket_summary_any_script"):
    cfg = production(tmp, [{
        "app_id": "pilot", "tenant": "acme", "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(),
        "restore_output": True, "models": ["mock-1"], "purposes": [contract],
        "tasks": {contract: str(ROOT / f"contracts/{contract}.yaml")}}])
    ext = MockLLMUpstream("summarise")
    s = Settings(**cfg, ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=ext, private_upstream=private, private_upstream_url=url)
    return TestClient(create_app(s)), ext


def call(client, text, purpose="ticket_summary_any_script", **extra):
    body = {"model": "mock-1", "pgw_subjects": SUBJ, **extra, "messages": [{"role": "user", "content": text}]}
    return client.post("/v1/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose, **source(purpose, body)})


def test_c4_content_goes_only_to_the_private_model(tmp_path):
    local = LocalModel()
    client, ext = make(tmp_path, private=local)
    r = call(client, HINDI)
    assert r.status_code == 200 and r.headers["X-PGW-Route"] == "PRIVATE_LOCAL"
    assert not ext.received                                   # nothing reached the external provider
    assert "राहुल" in local.received[0].decode()              # in-boundary model got the real content
    assert "pgw_subjects" not in json.loads(local.received[0])
    assert "644748074748" not in r.text                       # C3 never returned, even from the private model
    row = json.loads((tmp_path / "e.jsonl").read_text().splitlines()[-1])
    assert row["route"] == "PRIVATE_LOCAL" and row["decision"] == "SENT_PRIVATE"
    assert "राहुल" not in (tmp_path / "e.jsonl").read_text()


def test_latin_content_on_the_same_purpose_stays_on_the_protected_external_path(tmp_path):
    local = LocalModel()
    client, ext = make(tmp_path, private=local)
    assert call(client, "Rahul Sharma (KD-11914) wants to change his salary account.").status_code == 200
    assert ext.received and not local.received
    assert "Rahul" not in ext.received[0].decode()


def test_private_model_failure_fails_closed_and_never_falls_back_external(tmp_path):
    client, ext = make(tmp_path, private=LocalModel(fail=True))
    r = call(client, HINDI)
    assert r.status_code == 502 and not ext.received and "राहुल" not in r.text


def test_refuse_contracts_still_refuse(tmp_path):
    client, ext = make(tmp_path, contract="ticket_summary")
    assert call(client, HINDI, purpose="ticket_summary").status_code == 422 and not ext.received


def test_private_contract_without_endpoint_stops_startup(tmp_path):
    with pytest.raises(ContractError):
        make(tmp_path)


@pytest.mark.parametrize("url", ["https://api.groq.com/openai/v1", "https://api.anthropic.com/v1",
                                 "http://evil.example:8090/v1", "http://user:pw@127.0.0.1:8090/v1"])
def test_private_endpoint_must_be_in_boundary(tmp_path, url):
    with pytest.raises(ValueError):
        make(tmp_path, url=url)


def test_private_route_rejects_streaming(tmp_path):
    client, ext = make(tmp_path, private=LocalModel())
    assert call(client, HINDI, stream=True).status_code == 400 and not ext.received


def test_keyless_in_boundary_endpoint_sends_no_authorization_header():
    assert "Authorization" not in OpenAICompatUpstream("http://127.0.0.1:8090/v1", "")._headers
    assert OpenAICompatUpstream("https://api.groq.com/openai/v1", "k")._headers["Authorization"] == "Bearer k"


def test_private_output_gate_blocks_known_credential_echo(tmp_path):
    token = "ghp_TEST_ONLY_PRIVATE_ECHO_000000000000000000"
    class EchoPrivate(LocalModel):
        async def complete(self, body):
            self.received.append(self.wire_bytes(body))
            text = " ".join(m.get("content", "") for m in body.get("messages", []) if isinstance(m.get("content"), str))
            return {"id": "private-echo", "object": "chat.completion", "created": 0,
                    "model": body.get("model", "local"), "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}
    local = EchoPrivate()
    client, ext = make(tmp_path, private=local)
    r = call(client, f"राहुल शर्मा: Authorization: Bearer {token}")
    assert r.status_code == 200, r.text
    assert not ext.received
    assert token in local.received[0].decode(), "private-route test must confirm the input canary was seen locally"
    assert token not in r.text, "private model echoed an input secret to the caller"
