"""Starlette app for the privacy gateway: POST /v1/chat/completions.

Fail-closed everywhere: any detector/ledger exception -> 503, egress hit -> 500, and no error body
or log line ever contains request text or values (ADR-0007).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ..detect.edm import RestoreLedger, RosterIndex
from ..detect.recognizers import detect_d2
from ..egress import check as egress_check
from ..pipeline import ConversationIdRequired, Keys, Protector, UnsupportedContent
from ..policy import PolicyPack, UnknownPurpose
from ..stream import Restorer
from ..types import BlockedRequest, Context, EgressViolation, EntityType
from .settings import Settings
from .upstream import AnthropicUpstream, MockLLMUpstream, OpenAICompatUpstream, Upstream, UpstreamError

log = logging.getLogger("pgw.gateway")

ALLOWED_TOP = {"model", "messages", "stream", "temperature", "max_tokens", "max_completion_tokens", "top_p",
               "stop", "tools", "tool_choice", "n", "stream_options"}
LOG_FIELDS = ("event", "request_id", "tenant", "app", "purpose", "model", "status", "decision", "error_type",
              "latency_ms", "gate_events", "stream")


def _log(level: int, **fields) -> None:
    """Allow-listed structured logging. Values are coerced to short scalars; nothing free-form."""
    parts = []
    for k in LOG_FIELDS:
        if k in fields and fields[k] is not None:
            v = fields[k]
            parts.append(f"{k}={v if isinstance(v, (int, float, bool)) else str(v)[:64]!r}")
    log.log(level, " ".join(parts))


def _err(status: int, etype: str, message: str, rid: str | None = None, **extra) -> JSONResponse:
    body: dict = {"error": {"type": etype, "message": message, **extra}}
    headers = {"X-PGW-Request-Id": rid} if rid else {}
    return JSONResponse(body, status_code=status, headers=headers)


class _Reject(Exception):
    def __init__(self, status: int, etype: str, message: str, **extra):
        self.status, self.etype, self.message, self.extra = status, etype, message, extra


@dataclass(frozen=True)
class AppEntry:
    app_id: str
    tenant: str
    key_sha256: str
    purposes: frozenset
    restore_output: bool
    models: frozenset


def load_registry(path: str) -> list[AppEntry]:
    raw = yaml.safe_load(Path(path).read_text())
    return [AppEntry(a["app_id"], a["tenant"], a["key_sha256"].lower(), frozenset(a["purposes"]),
                     bool(a.get("restore_output", True)), frozenset(a["models"])) for a in raw["apps"]]


def build_upstream(s: Settings) -> Upstream:
    if s.upstream is not None:
        return s.upstream  # type: ignore[return-value]
    if s.upstream_kind == "mock":
        return MockLLMUpstream("summarise")
    if s.upstream_kind == "openai":
        return OpenAICompatUpstream(s.upstream_url, s.openai_api_key)
    if s.upstream_kind == "anthropic":
        return AnthropicUpstream(s.anthropic_api_key)
    raise ValueError("PGW_UPSTREAM must be mock|openai|anthropic")


def load_roster(path: str, key: bytes) -> RosterIndex:
    idx = RosterIndex(key)
    for line in Path(path).read_text().splitlines():
        if line.strip():
            idx.add_employee(json.loads(line))
    return idx


class EgressLedger:
    """Append-only JSONL, one line per upstream call. Metadata only, never content."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def write(self, **rec) -> None:
        line = json.dumps(rec, sort_keys=True)
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


@dataclass
class _ChoiceState:
    restorer: Restorer
    tools: dict[int, dict] = field(default_factory=dict)
    finish: str | None = None


def create_app(settings: Settings) -> Starlette:
    policy = PolicyPack.load(settings.policy_path)
    keys = Keys(bytes.fromhex(settings.master_key)) if settings.master_key else Keys.from_env()
    registry = load_registry(settings.registry_path)
    roster = load_roster(settings.roster_path, keys.derive("*", "edm")) if settings.roster_path else None
    ledger = RestoreLedger(keys.derive("*", "ledger"), settings.ledger_db_path)
    protector = Protector(policy, keys, roster, ledger)
    upstream = build_upstream(settings)
    egress_ledger = EgressLedger(settings.egress_ledger_path)

    def authenticate(request: Request) -> AppEntry:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer ") or not auth[7:].strip():
            raise _Reject(401, "unauthorized", "missing or invalid credentials")
        digest = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
        found = None
        for a in registry:   # constant-time compare against every entry
            if hmac.compare_digest(a.key_sha256, digest):
                found = a
        if found is None:
            raise _Reject(401, "unauthorized", "missing or invalid credentials")
        return found

    def ledger_line(rid, app, purpose, model, body_hash, counts, decision, ms, reason=None):
        try:
            egress_ledger.write(request_id=rid, tenant=app.tenant, app=app.app_id, purpose=purpose, model=model,
                                provider=upstream.name, body_sha256=body_hash, entity_counts=counts,
                                policy=policy.ref, decision=decision, latency_ms=round(ms, 2), reason=reason,
                                ts=round(time.time(), 3))
        except Exception as e:  # audit write failure must not leak content; fail closed
            _log(logging.ERROR, event="egress_ledger_write_failed", request_id=rid, error_type=type(e).__name__)
            raise _Reject(503, "ledger_unavailable", "audit ledger unavailable") from None

    async def chat(request: Request) -> Response:
        rid = "req_" + uuid.uuid4().hex[:24]
        try:
            resp = await _chat(request, rid)
        except _Reject as r:
            _log(logging.INFO, event="reject", request_id=rid, status=r.status, error_type=r.etype)
            return _err(r.status, r.etype, r.message, rid, **r.extra)
        except Exception as e:    # never include exception text: it may carry request data
            _log(logging.ERROR, event="internal_error", request_id=rid, error_type=type(e).__name__)
            return _err(500, "internal_error", "internal error", rid)
        return resp

    async def _chat(request: Request, rid: str) -> Response:
        app = authenticate(request)
        purpose = request.headers.get("x-pgw-purpose", "").strip()
        if not purpose:
            raise _Reject(400, "purpose_required", "X-PGW-Purpose header is required")
        if purpose not in app.purposes:
            raise _Reject(403, "purpose_forbidden", "purpose not permitted for this app")
        conv = request.headers.get("x-pgw-conversation-id", "").strip() or None
        if conv and len(conv) > 128:
            raise _Reject(400, "invalid_conversation_id", "conversation id too long")

        try:
            body = json.loads(await request.body())
        except ValueError:
            raise _Reject(400, "invalid_request", "body is not valid JSON") from None
        if not isinstance(body, dict):
            raise _Reject(400, "invalid_request", "body must be a JSON object")
        extra = set(body) - ALLOWED_TOP
        if extra:   # key NAMES are client-chosen but never values; still, do not echo them
            raise _Reject(400, "unsupported_parameter", f"{len(extra)} unsupported top-level field(s)")
        model = body.get("model")
        if not isinstance(model, str) or not isinstance(body.get("messages"), list) or not body["messages"]:
            raise _Reject(400, "invalid_request", "model (string) and non-empty messages (list) are required")
        if body.get("n", 1) != 1:
            raise _Reject(400, "unsupported_parameter", "n must be 1")
        if model not in app.models:
            raise _Reject(403, "model_forbidden", "model not permitted for this app")
        stream = bool(body.get("stream", False))

        # tools/stop are not message content, but they still leave the building: they must be clean.
        aux = [json.dumps(body[k], ensure_ascii=False) for k in ("tools", "stop") if k in body]
        try:
            dirty = any(detect_d2(a) or (roster is not None and roster.detect(a)) for a in aux)
        except Exception:
            raise _Reject(503, "detector_unavailable", "protection unavailable; request not sent") from None
        if dirty:
            raise _Reject(400, "sensitive_data_in_parameters", "tools/stop contain data that must not be sent")

        ctx = Context(app.tenant, app.app_id, purpose, conv, app.restore_output)
        t0 = time.perf_counter()

        # --- protect + egress (fail closed) ---
        try:
            prot = await run_in_threadpool(protector.protect, body["messages"], ctx)
        except BlockedRequest as e:
            ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000, "policy")
            raise _Reject(422, "request_blocked", "request contains data that policy forbids sending",
                          types=list(e.types)) from None
        except (UnsupportedContent, ConversationIdRequired) as e:
            msg = ("conversation id required for multi-turn restore" if isinstance(e, ConversationIdRequired)
                   else "unsupported message content")
            raise _Reject(400, "invalid_request" if isinstance(e, UnsupportedContent) else "conversation_id_required",
                          msg) from None
        except UnknownPurpose:
            raise _Reject(403, "purpose_forbidden", "purpose has no policy") from None
        except Exception as e:
            _log(logging.ERROR, event="protect_failed", request_id=rid, error_type=type(e).__name__)
            ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000, "detector_error")
            raise _Reject(503, "detector_unavailable", "protection unavailable; request not sent") from None

        up_body = {k: v for k, v in body.items() if k != "messages"}
        up_body["messages"] = prot.messages
        wire = upstream.wire_bytes(up_body)
        body_hash = hashlib.sha256(wire).hexdigest()
        try:
            egress_check(wire, prot.sensitive_values)
        except EgressViolation:
            ms = (time.perf_counter() - t0) * 1000
            ledger_line(rid, app, purpose, model, body_hash, prot.type_counts(), "VIOLATION", ms, "egress")
            _log(logging.CRITICAL, event="egress_violation", request_id=rid, tenant=app.tenant, app=app.app_id)
            raise _Reject(500, "PGW_EGRESS_VIOLATION", "egress invariant violated; request not sent") from None
        except Exception as e:
            _log(logging.ERROR, event="egress_failed", request_id=rid, error_type=type(e).__name__)
            raise _Reject(503, "detector_unavailable", "protection unavailable; request not sent") from None
        ms = (time.perf_counter() - t0) * 1000
        ledger_line(rid, app, purpose, model, body_hash, prot.type_counts(), "SENT", ms)
        _log(logging.INFO, event="sent", request_id=rid, tenant=app.tenant, app=app.app_id, purpose=purpose,
             model=model, decision="SENT", latency_ms=round(ms, 2), stream=stream)

        def new_restorer() -> Restorer:
            return Restorer(prot.alias_map, restore=app.restore_output, never_restore=policy.never_restore,
                            roster=roster, mode=policy.stream_mode(purpose))

        def record(items) -> None:
            if ctx.conversation_id:
                ledger.record(ctx, items)

        if not stream:
            return await _non_stream(rid, up_body, new_restorer, record)
        return await _stream(rid, up_body, new_restorer, record)

    async def _non_stream(rid, up_body, new_restorer, record) -> Response:
        try:
            data = await upstream.complete(up_body)
        except UpstreamError as e:
            raise _Reject(502, "upstream_error", "upstream provider error") from None
        except Exception as e:
            raise _Reject(502, "upstream_error", type(e).__name__) from None
        try:
            restored: list = []
            events = 0
            out_choices = []
            for ch in data.get("choices") or []:
                m = ch.get("message") or {}
                msg: dict = {"role": "assistant", "content": None}   # unknown fields are dropped, not forwarded
                if isinstance(m.get("content"), str):
                    r = new_restorer()
                    msg["content"] = r.feed(m["content"]) + r.finish()
                    restored += r.restored
                    events += len(r.events)
                calls = []
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    r = new_restorer()
                    args = r.feed(fn.get("arguments") or "") + r.finish()
                    restored += r.restored
                    events += len(r.events)
                    calls.append({"id": tc.get("id"), "type": "function",
                                  "function": {"name": fn.get("name"), "arguments": args}})
                if calls:
                    msg["tool_calls"] = calls
                out_choices.append({"index": ch.get("index", 0), "message": msg,
                                    "finish_reason": ch.get("finish_reason")})
            record(restored)
        except Exception as e:
            _log(logging.ERROR, event="restore_failed", request_id=rid, error_type=type(e).__name__)
            raise _Reject(503, "restore_unavailable", "response withheld") from None
        out = {"id": data.get("id"), "object": "chat.completion", "created": data.get("created"),
               "model": data.get("model"), "choices": out_choices}
        if isinstance(data.get("usage"), dict):
            out["usage"] = data["usage"]
        return JSONResponse(out, headers={"X-PGW-Request-Id": rid, "X-PGW-Gate-Events": str(events)})

    async def _stream(rid, up_body, new_restorer, record) -> Response:
        it = upstream.stream(up_body).__aiter__()
        try:   # prime: a failure before the first event can still be a proper HTTP error
            first = await it.__anext__()
        except StopAsyncIteration:
            first = None
        except UpstreamError as e:
            raise _Reject(502, "upstream_error", "upstream provider error") from None
        except Exception as e:
            raise _Reject(502, "upstream_error", type(e).__name__) from None

        def sse(obj) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        async def gen():
            states: dict[int, _ChoiceState] = {}
            meta = {"id": None, "created": None, "model": None}
            usage = None
            done = False
            restored: list = []
            events = 0

            def chunk(idx, delta, finish=None):
                return sse({"id": meta["id"], "object": "chat.completion.chunk", "created": meta["created"],
                            "model": meta["model"], "choices": [{"index": idx, "delta": delta,
                                                                 "finish_reason": finish}]})

            async def events_iter():
                if first is not None:
                    yield first
                async for x in it:
                    yield x
            try:
                async for raw in events_iter():
                    if raw.strip() == "[DONE]":
                        done = True
                        break
                    obj = json.loads(raw)
                    if not isinstance(obj, dict) or "error" in obj:
                        raise UpstreamError("upstream stream error")
                    for k in meta:
                        meta[k] = meta[k] if meta[k] is not None else obj.get(k)
                    if isinstance(obj.get("usage"), dict):
                        usage = obj["usage"]
                    for ch in obj.get("choices") or []:
                        idx = ch.get("index", 0)
                        st = states.get(idx)
                        if st is None:
                            st = states[idx] = _ChoiceState(new_restorer())
                            yield chunk(idx, {"role": "assistant", "content": ""})
                        delta = ch.get("delta") or {}
                        if isinstance(delta.get("content"), str) and delta["content"]:
                            out = st.restorer.feed(delta["content"])
                            if out:
                                yield chunk(idx, {"content": out})
                        for tc in delta.get("tool_calls") or []:
                            t = st.tools.setdefault(tc.get("index", 0), {"id": None, "name": None, "args": []})
                            t["id"] = tc.get("id") or t["id"]
                            fn = tc.get("function") or {}
                            t["name"] = fn.get("name") or t["name"]
                            if fn.get("arguments"):
                                t["args"].append(fn["arguments"])
                        if ch.get("finish_reason"):
                            st.finish = ch["finish_reason"]
                            done = True
                if not done:
                    raise UpstreamError("upstream stream ended early")
                # upstream finished cleanly: flush holdback, then whole tool calls, then finish
                for idx, st in sorted(states.items()):
                    tail = st.restorer.finish()
                    if tail:
                        yield chunk(idx, {"content": tail})
                    events += len(st.restorer.events)
                    restored += st.restorer.restored
                    for ti, t in sorted(st.tools.items()):
                        r = new_restorer()
                        args = r.feed("".join(t["args"])) + r.finish()
                        restored += r.restored
                        events += len(r.events)
                        yield chunk(idx, {"tool_calls": [{"index": ti, "id": t["id"], "type": "function",
                                                          "function": {"name": t["name"], "arguments": args}}]})
                    yield chunk(idx, {}, st.finish or "stop")
                record(restored)
                if usage:
                    yield sse({"id": meta["id"], "object": "chat.completion.chunk", "created": meta["created"],
                               "model": meta["model"], "choices": [], "usage": usage})
                yield f": pgw-gate-events={events}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                # Held-back/unflushed text is dropped: unrestored aliases are never emitted as final.
                _log(logging.ERROR, event="stream_failed", request_id=rid, error_type=type(e).__name__, stream=True)
                try:
                    for st in states.values():
                        restored += st.restorer.restored
                    record(restored)   # what already reached the client
                except Exception:
                    pass
                yield sse({"error": {"type": "stream_interrupted", "message": "stream interrupted; response incomplete"}})

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"X-PGW-Request-Id": rid, "Cache-Control": "no-store"})

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"]),
                             Route("/healthz", health, methods=["GET"])])
