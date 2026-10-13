"""Upstream LLM adapters.

Contract (ADR-0007): the gateway runs the egress sweep on `upstream.wire_bytes(body)`, and every
implementation must send exactly those bytes. `serialize_body` is the one canonical serialiser.

Known gaps (documented, not hidden):
  * AnthropicUpstream: text, tool_use and tool_result only. No images/documents, no `n>1`,
    no `stop`-less `stop_reason` nuance beyond end_turn/max_tokens/tool_use/stop_sequence,
    `stream_options`, `top_p`+`temperature` combination rules and thinking blocks are not mapped.
    Tool result content is flattened to text. Tool-call ids are passed through unchanged.
  * OpenAICompatUpstream: no retries (a retry path must re-use the same bytes; none is implemented).
  * `tools` / `stop` are forwarded as given by the client; the pipeline inspects `messages` only.
"""
from __future__ import annotations

import json
import random
import re
import time
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

import httpx


def serialize_body(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class UpstreamError(Exception):
    """Provider failure. The message never contains request or response text."""


@runtime_checkable
class Upstream(Protocol):
    name: str

    def wire_bytes(self, body: dict) -> bytes: ...
    async def complete(self, body: dict) -> dict: ...
    def stream(self, body: dict) -> AsyncIterator[str]: ...   # yields raw SSE `data:` payloads


# --- OpenAI-compatible --------------------------------------------------------------

class OpenAICompatUpstream:
    name = "openai"

    def __init__(self, base_url: str, api_key: str, timeout: float = 120.0,
                 client: httpx.AsyncClient | None = None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Content-Type": "application/json"}
        if api_key:   # an in-boundary endpoint may need no key; "Bearer " alone is an illegal header
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.AsyncClient(timeout=timeout)

    def wire_bytes(self, body: dict) -> bytes:
        return serialize_body(body)

    async def complete(self, body: dict) -> dict:
        try:
            r = await self._client.post(self.url, content=self.wire_bytes(body), headers=self._headers)
        except httpx.HTTPError as e:
            raise UpstreamError(f"upstream transport error: {type(e).__name__}") from None
        if r.status_code != 200:
            raise UpstreamError(f"upstream status {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise UpstreamError("upstream returned invalid JSON") from None

    async def stream(self, body: dict) -> AsyncIterator[str]:
        try:
            async with self._client.stream("POST", self.url, content=self.wire_bytes(body),
                                           headers=self._headers) as r:
                if r.status_code != 200:
                    raise UpstreamError(f"upstream status {r.status_code}")
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        yield line[5:].strip()
        except httpx.HTTPError as e:
            raise UpstreamError(f"upstream transport error: {type(e).__name__}") from None


# --- Anthropic ----------------------------------------------------------------------

def _text_of(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content if isinstance(p, dict))


_STOP = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool_calls",
         "refusal": "content_filter"}


def to_anthropic_params(body: dict) -> dict:
    system: list[str] = []
    msgs: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        if msgs and msgs[-1]["role"] == role:     # Anthropic wants alternating roles
            msgs[-1]["content"].extend(blocks)
        else:
            msgs.append({"role": role, "content": blocks})

    for m in body["messages"]:
        role = m["role"]
        if role in ("system", "developer"):
            system.append(_text_of(m.get("content")))
        elif role == "user":
            push("user", [{"type": "text", "text": _text_of(m.get("content"))}])
        elif role == "assistant":
            blocks = []
            text = _text_of(m.get("content"))
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except ValueError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc.get("id", ""), "name": tc["function"]["name"],
                               "input": args})
            push("assistant", blocks or [{"type": "text", "text": ""}])
        elif role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                           "content": _text_of(m.get("content"))}])
    out: dict = {"model": body["model"], "messages": msgs,
                 "max_tokens": body.get("max_completion_tokens") or body.get("max_tokens") or 1024}
    if system:
        out["system"] = "\n\n".join(system)
    for k in ("temperature", "top_p"):
        if k in body:
            out[k] = body[k]
    if "stop" in body:
        out["stop_sequences"] = [body["stop"]] if isinstance(body["stop"], str) else list(body["stop"])
    if body.get("tools"):
        out["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                         "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
                        for t in body["tools"]]
    tc = body.get("tool_choice")
    if tc == "auto":
        out["tool_choice"] = {"type": "auto"}
    elif tc == "required":
        out["tool_choice"] = {"type": "any"}
    elif isinstance(tc, dict) and tc.get("function"):
        out["tool_choice"] = {"type": "tool", "name": tc["function"]["name"]}
    elif tc == "none":
        out.pop("tools", None)
    return out


def from_anthropic_message(msg) -> dict:
    d = msg if isinstance(msg, dict) else msg.model_dump()
    text, calls = "", []
    for b in d.get("content", []):
        if b["type"] == "text":
            text += b["text"]
        elif b["type"] == "tool_use":
            calls.append({"id": b["id"], "type": "function",
                          "function": {"name": b["name"], "arguments": json.dumps(b["input"])}})
    message: dict = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = calls
    u = d.get("usage") or {}
    return {"id": d.get("id", ""), "object": "chat.completion", "created": int(time.time()), "model": d.get("model", ""),
            "choices": [{"index": 0, "message": message,
                         "finish_reason": _STOP.get(d.get("stop_reason") or "", "stop")}],
            "usage": {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0),
                      "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0)}}



class AnthropicUpstream:
    name = "anthropic"

    def __init__(self, api_key: str, client=None, base_url: str = "https://api.anthropic.com",
                 timeout: float = 60.0):
        if client is None:
            import anthropic
            # base_url is passed explicitly so ANTHROPIC_BASE_URL in the environment cannot redirect egress
            # around the gateway's destination allowlist. Retries re-send the same checked bytes.
            client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=2)
        self._client = client

    def wire_bytes(self, body: dict) -> bytes:
        return serialize_body(to_anthropic_params(body))

    async def complete(self, body: dict) -> dict:
        try:
            msg = await self._client.messages.create(**json.loads(self.wire_bytes(body)))
        except Exception as e:
            raise UpstreamError(f"upstream error: {type(e).__name__}") from None
        return from_anthropic_message(msg)

    async def stream(self, body: dict) -> AsyncIterator[str]:
        cid, model, tool_idx = "", body.get("model", ""), -1

        def chunk(delta: dict, finish: str | None = None) -> str:
            return json.dumps({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                               "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
        try:
            s = await self._client.messages.create(**json.loads(self.wire_bytes(body)), stream=True)
            async for ev in s:
                t = ev.type
                if t == "message_start":
                    cid, model = ev.message.id, ev.message.model
                    yield chunk({"role": "assistant", "content": ""})
                elif t == "content_block_start" and ev.content_block.type == "tool_use":
                    tool_idx += 1
                    yield chunk({"tool_calls": [{"index": tool_idx, "id": ev.content_block.id, "type": "function",
                                                 "function": {"name": ev.content_block.name, "arguments": ""}}]})
                elif t == "content_block_delta":
                    if ev.delta.type == "text_delta":
                        yield chunk({"content": ev.delta.text})
                    elif ev.delta.type == "input_json_delta":
                        yield chunk({"tool_calls": [{"index": tool_idx,
                                                     "function": {"arguments": ev.delta.partial_json}}]})
                elif t == "message_delta":
                    yield chunk({}, _STOP.get(ev.delta.stop_reason or "", "stop"))
                elif t == "message_stop":
                    yield "[DONE]"
        except Exception as e:
            raise UpstreamError(f"upstream error: {type(e).__name__}") from None


# --- Mock ---------------------------------------------------------------------------

_ALIAS = re.compile(r"\[[A-Z_]+_[0-9A-Z]{6,8}\]")
BEHAVIOURS = ("echo", "summarise", "mangle", "fabricate", "leak_aadhaar", "tool_call")


class MockLLMUpstream:
    """Deterministic fake LLM. `received` holds the exact request bytes (what "left the building")."""
    name = "mock"

    def __init__(self, behaviour: str = "summarise", seed: int = 1234):
        if behaviour not in BEHAVIOURS:
            raise ValueError(f"unknown behaviour {behaviour!r}")
        self.behaviour = behaviour
        self.seed = seed
        self.received: list[bytes] = []
        self._calls = 0

    def wire_bytes(self, body: dict) -> bytes:
        return serialize_body(body)

    # -- generation --
    @staticmethod
    def _aliases(body: dict) -> list[str]:
        seen: list[str] = []
        for m in body.get("messages", []):
            for a in _ALIAS.findall(_text_of(m.get("content"))):
                if a not in seen:
                    seen.append(a)
        return seen

    def _summary(self, aliases: list[str]) -> str:
        persons = [a for a in aliases if a.startswith("[PERSON_")]
        emails = [a for a in aliases if a.startswith("[EMAIL_")]
        others = [a for a in aliases if a not in persons and a not in emails]
        parts = [f"{p} raised an issue" for p in persons] + [f"contact {e}" for e in emails] \
            + [f"see {o}" for o in others]
        return "Summary: " + ("; ".join(parts) if parts else "nothing to report") + "."

    def _text(self, body: dict) -> str:
        aliases = self._aliases(body)
        b = self.behaviour
        if b == "echo":
            users = [m for m in body.get("messages", []) if m.get("role") == "user"]
            return _text_of(users[-1].get("content")) if users else ""
        s = self._summary(aliases)
        if b == "mangle":
            return _ALIAS.sub(lambda m: m.group(0)[1:-1].lower(), s)
        if b == "fabricate":
            fake = "[PERSON_9ZZZZZ]"
            while fake in aliases:
                fake = fake[:-1] + "Y"
            return s + f" Escalate to {fake}."
        if b == "leak_aadhaar":
            return s + " Reference ID: 2341 2341 2346 on file."
        return s

    def _tool_args(self, body: dict) -> str:
        return json.dumps({"mentions": self._aliases(body)})

    def _prepare(self, body: dict) -> random.Random:
        self.received.append(self.wire_bytes(body))
        self._calls += 1
        return random.Random(self.seed + self._calls)  # noqa: S311 - deterministic mock only; never used for secrets

    async def complete(self, body: dict) -> dict:
        self._prepare(body)
        msg: dict = {"role": "assistant", "content": self._text(body)}
        finish = "stop"
        if self.behaviour == "tool_call":
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": self._tool_args(body)}}]}
            finish = "tool_calls"
        return {"id": "mock-1", "object": "chat.completion", "created": 0, "model": body.get("model", "mock"),
                "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    async def stream(self, body: dict) -> AsyncIterator[str]:
        rng = self._prepare(body)

        def pieces(s: str):
            i = 0
            while i < len(s):
                n = rng.randint(1, 7)
                yield s[i:i + n]
                i += n

        def ch(delta: dict, finish=None) -> str:
            return json.dumps({"id": "mock-1", "object": "chat.completion.chunk", "created": 0,
                               "model": body.get("model", "mock"),
                               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
        yield ch({"role": "assistant", "content": ""})
        if self.behaviour == "tool_call":
            yield ch({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                      "function": {"name": "lookup", "arguments": ""}}]})
            for p in pieces(self._tool_args(body)):
                yield ch({"tool_calls": [{"index": 0, "function": {"arguments": p}}]})
            yield ch({}, "tool_calls")
        else:
            for p in pieces(self._text(body)):
                yield ch({"content": p})
            yield ch({}, "stop")
        yield "[DONE]"
