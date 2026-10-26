import hashlib
import json
import logging
import os

import pytest
import yaml
from starlette.testclient import TestClient

from pgw.gateway import Settings, create_app
from tests.sealed import legacy_dev
from pgw.gateway.upstream import (MockLLMUpstream, from_anthropic_message, to_anthropic_params)

KEY = "test-app-key-" + os.urandom(6).hex()
OTHER_KEY = "other-app-key-" + os.urandom(6).hex()
PROMPT = ("Draft an email to Rahul Sharma at rahul.sharma@company.com saying his salary revision "
          "from ₹18,40,000 to ₹20,10,000 has been approved.")
RAW = ["Rahul Sharma", "rahul.sharma@company.com", "18,40,000", "20,10,000"]
H = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "email_draft"}


def sha(k):
    return hashlib.sha256(k.encode()).hexdigest()


@pytest.fixture
def env(tmp_path):
    reg = tmp_path / "apps.yaml"
    reg.write_text(yaml.safe_dump({"apps": [
        {"app_id": "hr-assistant", "tenant": "acme", "key_sha256": sha(KEY),
         "purposes": ["email_draft", "hr_case_summary", "comp_band_analysis"], "restore_output": True,
         "models": ["mock-1"]},
        {"app_id": "norestore", "tenant": "acme", "key_sha256": sha(OTHER_KEY),
         "purposes": ["email_draft"], "restore_output": False, "models": ["mock-1"]},
    ]}))
    return tmp_path, reg


def make(env, behaviour="summarise", upstream=None):
    tmp, reg = env
    mock = upstream or MockLLMUpstream(behaviour)
    # The pre-contract (legacy) path: only reachable through the explicit development-only switch (tests/sealed.py).
    s = Settings(policy_path="policies/hris-default.yaml", **legacy_dev(reg),
                 ledger_db_path=str(tmp / "ledger.sqlite"), egress_ledger_path=str(tmp / "egress.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s)), mock, tmp / "egress.jsonl"


def body(text=PROMPT, **kw):
    return {"model": "mock-1", "messages": [{"role": "user", "content": text}], **kw}


def sse_text(resp):
    text, errors = "", []
    for line in resp.text.splitlines():
        if line.startswith("data:") and line[5:].strip() != "[DONE]":
            o = json.loads(line[5:])
            if "error" in o:
                errors.append(o)
                continue
            for ch in o["choices"]:
                text += ch["delta"].get("content") or ""
    return text, errors


# --- auth / validation -------------------------------------------------------------

def test_auth_and_purpose_and_model(env):
    c, mock, _ = make(env)
    assert c.post("/v1/chat/completions", json=body()).status_code == 401
    assert c.post("/v1/chat/completions", json=body(), headers={"Authorization": "Bearer nope",
                                                                  "X-PGW-Purpose": "email_draft"}).status_code == 401
    assert c.post("/v1/chat/completions", json=body(), headers={"Authorization": f"Bearer {KEY}"}).status_code == 400
    r = c.post("/v1/chat/completions", json=body(), headers={**H, "X-PGW-Purpose": "eval_tokenize_all"})
    assert r.status_code == 403
    r = c.post("/v1/chat/completions", json={**body(), "model": "gpt-9"}, headers=H)
    assert r.status_code == 403
    assert mock.received == []


@pytest.mark.parametrize("extra", [{"logit_bias": {}}, {"user": "x"}, {"n": 2}, {"functions": []}])
def test_strict_body(env, extra):
    c, mock, _ = make(env)
    r = c.post("/v1/chat/completions", json={**body(), **extra}, headers=H)
    assert r.status_code == 400 and r.json()["error"]["type"]
    assert mock.received == []


def test_unsupported_content_400(env):
    c, mock, _ = make(env)
    b = {"model": "mock-1", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]}
    assert c.post("/v1/chat/completions", json=b, headers=H).status_code == 400
    assert mock.received == []


def test_block_422_no_value_echo(env):
    c, mock, egress = make(env)
    r = c.post("/v1/chat/completions", json=body("Employee PAN is ABCPE1234F please file."), headers=H)
    assert r.status_code == 422
    assert "ABCPE1234F" not in r.text and r.json()["error"]["types"] == ["PAN"]
    assert mock.received == []
    line = json.loads(egress.read_text().splitlines()[-1])
    assert line["decision"] == "BLOCKED" and "ABCPE1234F" not in egress.read_text()


def test_tools_and_stop_with_pii_rejected(env):
    c, mock, _ = make(env)
    tool = {"type": "function", "function": {"name": "notify", "description": "Notify rahul.sharma@acme.in",
                                              "parameters": {"type": "object", "properties": {}}}}
    r = c.post("/v1/chat/completions", json=body("hello", tools=[tool]), headers=H)
    assert r.status_code == 400 and "rahul" not in r.text and mock.received == []
    r = c.post("/v1/chat/completions", json=body("hello", stop=["sk-proj-abcdefghijklmnopqrstuvwxyz0123"]), headers=H)
    assert r.status_code == 400 and mock.received == []
    clean = {"type": "function", "function": {"name": "notify", "description": "Notify the employee by email",
                                               "parameters": {"type": "object", "properties": {}}}}
    assert c.post("/v1/chat/completions", json=body("hello", tools=[clean]), headers=H).status_code == 200


# --- round trips --------------------------------------------------------------------

def test_non_stream_round_trip(env):
    c, mock, egress = make(env)
    r = c.post("/v1/chat/completions", json=body(), headers=H)
    assert r.status_code == 200
    content = r.json()["choices"][0]["message"]["content"]
    assert "Rahul Sharma" in content and "rahul.sharma@company.com" in content
    sent = b"".join(mock.received).decode()
    for v in RAW:
        assert v not in sent
    assert "[PERSON_" in sent and "[EMAIL_" in sent
    assert r.headers["X-PGW-Request-Id"].startswith("req_") and r.headers["X-PGW-Gate-Events"] == "0"
    # egress ledger: metadata only
    lines = [json.loads(x) for x in egress.read_text().splitlines()]
    assert len(lines) == 1
    L = lines[0]
    assert L["decision"] == "SENT" and L["tenant"] == "acme" and L["app"] == "hr-assistant"
    assert L["purpose"] == "email_draft" and L["body_sha256"] == hashlib.sha256(mock.received[0]).hexdigest()
    assert L["entity_counts"]["PERSON:TOKENIZE"] >= 1 and L["policy"] == "hris-default@1"
    raw_ledger = egress.read_text()
    for v in RAW + ["Draft an email"]:
        assert v not in raw_ledger


def test_stream_equals_non_stream(env):
    c, mock, _ = make(env)
    ns = c.post("/v1/chat/completions", json=body(), headers=H).json()["choices"][0]["message"]["content"]
    r = c.post("/v1/chat/completions", json=body(stream=True), headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    text, errors = sse_text(r)
    assert not errors and text == ns
    assert r.text.rstrip().endswith("data: [DONE]")
    for v in RAW:
        assert v not in mock.received[-1].decode()


@pytest.mark.parametrize("seed", range(6))
def test_stream_any_chunking(env, seed):
    c, mock, _ = make(env, upstream=MockLLMUpstream("summarise", seed=seed))
    ns = c.post("/v1/chat/completions", json=body(), headers=H).json()["choices"][0]["message"]["content"]
    text, errors = sse_text(c.post("/v1/chat/completions", json=body(stream=True), headers=H))
    assert not errors and "[PERSON_" not in text and "[EMAIL_" not in text and "Rahul Sharma" in text
    assert "rahul.sharma@company.com" in text


def test_mangle_still_restores(env):
    c, _, _ = make(env, "mangle")
    for stream in (False, True):
        r = c.post("/v1/chat/completions", json=body(stream=stream), headers=H)
        text = sse_text(r)[0] if stream else r.json()["choices"][0]["message"]["content"]
        assert "Rahul Sharma" in text and "rahul.sharma@company.com" in text
        assert "person_" not in text.lower()


def test_fabricate_becomes_unknown(env):
    c, _, _ = make(env, "fabricate")
    r = c.post("/v1/chat/completions", json=body(), headers=H)
    content = r.json()["choices"][0]["message"]["content"]
    assert "[unknown PERSON]" in content and "ZZZZZ" not in content
    assert r.headers["X-PGW-Gate-Events"] == "1"
    text, _ = sse_text(c.post("/v1/chat/completions", json=body(stream=True), headers=H))
    assert "[unknown PERSON]" in text and "ZZZZZ" not in text


def test_leak_aadhaar_masked(env):
    c, _, _ = make(env, "leak_aadhaar")
    r = c.post("/v1/chat/completions", json=body(), headers=H)
    content = r.json()["choices"][0]["message"]["content"]
    assert "2341" not in content and "[AADHAAR]" in content
    text, _ = sse_text(c.post("/v1/chat/completions", json=body(stream=True), headers=H))
    assert "2341" not in text and "[AADHAAR]" in text


def test_restore_output_false_leaves_aliases(env):
    c, _, _ = make(env)
    r = c.post("/v1/chat/completions", json=body(), headers={**H, "Authorization": f"Bearer {OTHER_KEY}"})
    content = r.json()["choices"][0]["message"]["content"]
    assert "[PERSON_" in content and "Rahul" not in content


def test_tool_call_arguments_restored(env):
    c, mock, _ = make(env, "tool_call")
    r = c.post("/v1/chat/completions", json=body(), headers={**H, "X-PGW-Conversation-Id": "tc1"})
    tc = r.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert "Rahul Sharma" in json.loads(tc)["mentions"] or "rahul.sharma@company.com" in json.loads(tc)["mentions"]
    r = c.post("/v1/chat/completions", json=body(stream=True), headers=H)
    calls = [json.loads(line[5:])["choices"][0]["delta"]["tool_calls"][0]
             for line in r.text.splitlines()
             if line.startswith("data:") and "[DONE]" not in line and json.loads(line[5:])["choices"]
             and json.loads(line[5:])["choices"][0]["delta"].get("tool_calls")]
    assert len(calls) == 1                          # whole call emitted once (ADR-0008)
    assert "Rahul Sharma" in json.loads(calls[0]["function"]["arguments"])["mentions"]
    for v in RAW:
        assert v not in mock.received[-1].decode()


# --- multi-turn / ledger ---------------------------------------------------------------

def test_multi_turn_and_truncated_history(env):
    c, mock, _ = make(env)
    hc = {**H, "X-PGW-Conversation-Id": "conv-42"}
    r1 = c.post("/v1/chat/completions", json=body(), headers=hc)
    answer = r1.json()["choices"][0]["message"]["content"]
    assert "Rahul Sharma" in answer
    # full history
    msgs = [{"role": "user", "content": PROMPT}, {"role": "assistant", "content": answer},
            {"role": "user", "content": "Make it shorter."}]
    r2 = c.post("/v1/chat/completions", json={"model": "mock-1", "messages": msgs}, headers=hc)
    assert r2.status_code == 200
    # client truncated the history: only the restored assistant answer remains
    msgs = [{"role": "assistant", "content": answer}, {"role": "user", "content": "Now send it to Rahul."}]
    r3 = c.post("/v1/chat/completions", json={"model": "mock-1", "messages": msgs}, headers=hc)
    assert r3.status_code == 200
    for sent in mock.received[1:]:
        s = sent.decode()
        for v in ("Rahul Sharma", "rahul.sharma@company.com", "Rahul"):
            assert v not in s
    assert "Rahul Sharma" in r3.json()["choices"][0]["message"]["content"] or \
        "[PERSON_" in mock.received[-1].decode()


def test_assistant_turn_without_conversation_id_400(env):
    c, mock, _ = make(env)
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "again"}]
    r = c.post("/v1/chat/completions", json={"model": "mock-1", "messages": msgs}, headers=H)
    assert r.status_code == 400 and mock.received == []


# --- failure modes ---------------------------------------------------------------------

class Broken(MockLLMUpstream):   # a fake (development mode only reaches mock providers)
    name = "broken"

    def __init__(self):
        super().__init__("summarise")

    def wire_bytes(self, body):
        return json.dumps(body).encode()

    async def complete(self, body):
        raise RuntimeError("boom " + json.dumps(body))

    async def stream(self, body):
        yield json.dumps({"id": "x", "created": 0, "model": "m", "choices": [
            {"index": 0, "delta": {"content": "Summary: [PERSON_ABC"}}]})
        raise RuntimeError("connection reset")


def test_stream_break_emits_error_no_alias(env):
    c, _, _ = make(env, upstream=Broken())
    r = c.post("/v1/chat/completions", json=body(stream=True), headers=H)
    text, errors = sse_text(r)
    assert errors and errors[0]["error"]["type"] == "stream_interrupted"
    assert "[DONE]" not in r.text and "PERSON_" not in r.text and "Rahul" not in r.text


def test_upstream_failure_message_has_no_content(env):
    c, _, _ = make(env, upstream=Broken())
    r = c.post("/v1/chat/completions", json=body(), headers=H)
    assert r.status_code == 502 and "PERSON_" not in r.text and "Rahul" not in r.text


def test_egress_violation_500(env, monkeypatch):
    c, mock, egress = make(env)
    import pgw.pipeline as P
    orig = P.Protector.protect

    def leaky(self, messages, ctx):     # simulate a pipeline bug: original text survives in the body
        prot = orig(self, messages, ctx)
        prot.messages[0]["content"] += " (Rahul Sharma)"
        return prot
    monkeypatch.setattr(P.Protector, "protect", leaky)
    r = c.post("/v1/chat/completions", json=body(), headers=H)
    assert r.status_code == 500 and r.json()["error"]["type"] == "PGW_EGRESS_VIOLATION"
    assert mock.received == [] and "Rahul" not in r.text
    assert json.loads(egress.read_text().splitlines()[-1])["decision"] == "VIOLATION"


def test_detector_exception_503(env, monkeypatch):
    c, mock, _ = make(env)
    import pgw.pipeline as P

    def boom(self, messages, ctx):
        raise ValueError("secret Rahul Sharma")
    monkeypatch.setattr(P.Protector, "protect", boom)
    r = c.post("/v1/chat/completions", json=body(), headers=H)
    assert r.status_code == 503 and "Rahul" not in r.text and mock.received == []


def test_ledger_exception_503(env, monkeypatch):
    c, mock, _ = make(env)
    from pgw.detect.edm import RestoreLedger

    def boom(self, ctx, items):
        raise RuntimeError("db down")
    monkeypatch.setattr(RestoreLedger, "record", boom)
    r = c.post("/v1/chat/completions", json=body(), headers={**H, "X-PGW-Conversation-Id": "c9"})
    assert r.status_code == 503 and "Rahul" not in r.text


def test_logs_never_contain_values(env, caplog):
    caplog.set_level(logging.DEBUG)
    c, _, _ = make(env)
    c.post("/v1/chat/completions", json=body(), headers=H)
    c.post("/v1/chat/completions", json=body("PAN ABCPE1234F"), headers=H)
    c.post("/v1/chat/completions", json={**body(), "bogus": 1}, headers=H)
    logs = caplog.text
    for v in RAW + ["ABCPE1234F", KEY]:
        assert v not in logs


# --- adapters (offline) ----------------------------------------------------------------

def test_anthropic_translation():
    p = to_anthropic_params({"model": "claude-x", "max_tokens": 50, "messages": [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi [PERSON_ABC123]"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{\"a\": 1}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"}]})
    assert p["system"] == "be brief" and p["max_tokens"] == 50
    assert [m["role"] for m in p["messages"]] == ["user", "assistant", "user"]
    assert p["messages"][1]["content"][0]["type"] == "tool_use"
    assert p["messages"][2]["content"][0]["type"] == "tool_result"
    out = from_anthropic_message({"id": "m", "model": "claude-x", "stop_reason": "end_turn",
                                  "content": [{"type": "text", "text": "yo"}], "usage": {}})
    assert out["choices"][0]["message"]["content"] == "yo" and out["choices"][0]["finish_reason"] == "stop"
