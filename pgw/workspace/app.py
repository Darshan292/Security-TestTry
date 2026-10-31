"""HR Workspace: the staff-facing UI and its backend-for-frontend (docs/WORKSPACE.md).

The browser talks only to this service; this service talks only to the privacy gateway, over HTTP, with the app key it
holds server-side. The key, the source signing key and the gateway URL never reach the browser.

Security properties (each has a test in tests/test_workspace.py):
* Identity: production trusts a user identity header (default X-Forwarded-Email, set by the SSO reverse proxy) only
  when the direct peer address is in WS_TRUSTED_PROXIES; otherwise 401. Development uses a local dev user and the
  page shows a banner.
* CSRF: state-changing calls need the exact same Origin, a custom header (X-PGW-Workspace: 1) and a JSON body; the
  service never answers CORS preflights, so other origins cannot make them.
* Strict CSP without 'unsafe-inline' (script and style are separate same-origin files), Trusted Types, no framing,
  no-sniff, no-referrer, no-store; request bodies are size-limited.
* No content is persisted or logged: log lines carry the task, status, outcome, timing, the gateway request id and a
  short hash of the user, never text, form values or answers. History lives only in the browser tab.
* Free text is sent unattested (the contract keeps it inside); only form payloads built from validated typed fields
  are signed, as FORM_SOURCE_ID. Pre-signed tickets from a trusted helpdesk integration are passed through unchanged.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .. import enduser
from ..product import attest
from . import report, tasks as T
from .settings import WorkspaceSettings

log = logging.getLogger("pgw.workspace")
STATIC = Path(__file__).resolve().parent / "static"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/static/app.css": ("app.css", "text/css; charset=utf-8"),
          "/static/icon.svg": ("icon.svg", "image/svg+xml")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
       "require-trusted-types-for 'script'; trusted-types 'none'")
SECURITY_HEADERS = {"content-security-policy": CSP, "x-frame-options": "DENY", "x-content-type-options": "nosniff",
                    "referrer-policy": "no-referrer", "cache-control": "no-store",
                    "cross-origin-opener-policy": "same-origin", "cross-origin-resource-policy": "same-origin",
                    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()"}
CSRF_HEADER = "x-pgw-workspace"
INTEGRATION_PATH = "/api/integrations/v1/tickets"
PUBLIC_PATHS = {"/healthz"}
MAX_GATEWAY_RESPONSE = 8 * 1024 * 1024
_IDENTITY = re.compile(r"[\x21-\x2b\x2d-\x7e][\x20-\x2b\x2d-\x7e]{0,253}")   # printable, no comma (joined duplicates)
_INPUT_HEADERS = (attest.H_SOURCE, attest.H_TIMESTAMP, attest.H_NONCE, attest.H_SIGNATURE)
_INPUT_HEADER_FORMAT = re.compile(r"[A-Za-z0-9@._:+/=-]{1,200}")


def _log(**fields) -> None:
    """Allow-listed, content-free log line."""
    allowed = ("event", "task", "status", "outcome", "error_type", "round_trip_ms", "gateway_request_id", "user",
               "integration", "signed")
    log.info(" ".join(f"{k}={str(fields[k])[:80]!r}" for k in allowed if fields.get(k) is not None))


def _user_ref(user: str) -> str:
    return hashlib.sha256(user.encode("utf-8", "replace")).hexdigest()[:12]


def _json_error(status: int, etype: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": {"type": etype, "message": message, **extra}}, status_code=status)


class _Reject(Exception):
    def __init__(self, status: int, etype: str, message: str, **extra):
        self.status, self.etype, self.message, self.extra = status, etype, message, extra


class Guard:
    """ASGI layer: security headers on every response; the SSO identity on every path but the health check and the
    integration endpoint (which authenticates machines with its own tokens)."""

    def __init__(self, app, settings: WorkspaceSettings):
        self.app, self.s, self.nets = app, settings, settings.networks()

    def identity(self, scope) -> str | None:
        if not self.s.production:
            return self.s.dev_user
        peer = (scope.get("client") or (None, None))[0]
        try:
            ip = ipaddress.ip_address(peer)
        except (TypeError, ValueError):
            return None
        if not any(ip in n for n in self.nets):
            return None   # only the trusted reverse proxy may assert who the user is
        name = self.s.identity_header.lower().encode("latin-1")
        values = [v for k, v in scope.get("headers") or [] if k == name]
        if len(values) != 1:
            return None
        v = values[0].decode("latin-1").strip()
        return v if _IDENTITY.fullmatch(v) else None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_secure(msg):
            if msg["type"] == "http.response.start":
                headers = [(k, v) for k, v in msg.get("headers", []) if k.decode("latin-1").lower()
                           not in SECURITY_HEADERS and k.lower() != b"server"]
                headers += [(k.encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
                if self.s.production:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                msg = {**msg, "headers": headers}
            await send(msg)

        path = scope.get("path", "")
        if path not in PUBLIC_PATHS and path != INTEGRATION_PATH:
            user = self.identity(scope)
            if user is None:
                resp = _json_error(401, "unauthenticated", "sign-in required")
                return await resp(scope, receive, send_secure)
            scope.setdefault("state", {})["user"] = user
        return await self.app(scope, receive, send_secure)


@dataclass
class GatewayResult:
    status: int                    # HTTP status from the gateway; 0 when it could not be reached
    data: dict | None
    route: str | None
    request_id: str | None
    round_trip_ms: float
    failure: str | None = None     # gateway_timeout | gateway_unavailable | bad_gateway_response
    private_model: str | None = None   # X-PGW-Private-Model on PRIVATE_LOCAL: model | standin | unverified


def create_app(settings: WorkspaceSettings, *, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    s = settings.validate()
    assets = {p: ((STATIC / f).read_bytes(), ct) for p, (f, ct) in ASSETS.items()}
    client = httpx.AsyncClient(base_url=s.gateway_url, transport=transport, trust_env=False,
                               timeout=httpx.Timeout(s.gateway_timeout_s, connect=min(5.0, s.gateway_timeout_s)))
    tokens = tuple((iid, h) for iid, h in s.integrations)

    # --- helpers ---------------------------------------------------------------------------------------------

    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin")
        if not origin:
            return False
        allowed = {s.public_origin} if s.public_origin else set()
        if not s.production:
            host = request.headers.get("host", "")
            allowed |= {f"http://{host}", f"https://{host}"}
        site = request.headers.get("sec-fetch-site")
        return origin in allowed and site in (None, "same-origin")

    def is_json(request: Request) -> bool:
        return request.headers.get("content-type", "").split(";")[0].strip().lower() == "application/json"

    async def read_json(request: Request) -> dict:
        cl = request.headers.get("content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > s.max_body_bytes):
            raise _Reject(413, "too_large", "request body exceeds the size limit")
        buf, n = [], 0
        async for chunk in request.stream():
            n += len(chunk)
            if n > s.max_body_bytes:
                raise _Reject(413, "too_large", "request body exceeds the size limit")
            buf.append(chunk)
        try:
            body = json.loads(b"".join(buf))
        except ValueError:
            raise _Reject(400, "invalid_json", "body is not valid JSON") from None
        if not isinstance(body, dict):
            raise _Reject(400, "invalid_json", "body must be a JSON object")
        return body

    async def call_gateway(purpose: str, body: dict, extra_headers: dict, user: str | None = None) -> GatewayResult:
        payload = json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {s.gateway_key}", "X-PGW-Purpose": purpose,
                   "Content-Type": "application/json", **extra_headers}
        if user:   # the SSO identity, as an opaque id the gateway rate-limits by (asserted by this app: pgw.enduser)
            headers.update(enduser.sign(s.gateway_key, purpose, enduser.opaque_id(user), payload))
        t0 = time.perf_counter()

        def done(status, data=None, route=None, rid=None, failure=None, private=None) -> GatewayResult:
            return GatewayResult(status, data, route, rid, round((time.perf_counter() - t0) * 1000, 1), failure,
                                 private if private in ("model", "standin", "unverified") else None)
        try:
            r = await client.post("/v1/chat/completions", content=payload, headers=headers)
        except httpx.TimeoutException:
            return done(0, failure="gateway_timeout")
        except httpx.HTTPError:
            return done(0, failure="gateway_unavailable")
        rid = r.headers.get("x-pgw-request-id")
        if len(r.content) > MAX_GATEWAY_RESPONSE:
            return done(r.status_code, rid=rid, failure="bad_gateway_response")
        try:
            data = r.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            failure = "gateway_unavailable" if r.status_code in (502, 503, 504) else "bad_gateway_response"
            return done(r.status_code, rid=rid, failure=failure)
        return done(r.status_code, data, r.headers.get("x-pgw-route"), rid,
                    private=r.headers.get("x-pgw-private-model"))

    def result_view(res: GatewayResult, texts: dict[int, str]) -> dict:
        """Answer, plain-language error and the report view of one gateway call."""
        answer, error, rep, etype = None, None, None, res.failure
        if res.failure is None and res.data is not None:
            if res.status == 200:
                try:
                    answer = res.data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    answer = None
                if not isinstance(answer, str):
                    answer, etype = None, "bad_gateway_response"
                rep = res.data.get("pgw_report")
            else:
                err = res.data.get("error") if isinstance(res.data.get("error"), dict) else {}
                etype = err.get("type") if isinstance(err.get("type"), str) else "unknown"
                rep = err.get("pgw_report")
                error = report.plain_error(etype, err.get("message"))
        if etype and error is None:
            error = report.plain_error(etype)
        view = report.build_view(rep, http_status=res.status if res.failure is None else 0,
                                 route_header=res.route, error_type=etype, texts=texts)
        if etype == "bad_gateway_response":
            view["outcome"] = "error"
        return {"ok": res.status == 200 and error is None, "http_status": res.status,
                "round_trip_ms": res.round_trip_ms, "gateway_request_id": (res.request_id or "")[:64] or None,
                "answer": answer, "error": error, "view": view, "private_model": res.private_model}

    # --- routes ----------------------------------------------------------------------------------------------

    async def asset(request: Request) -> Response:
        data, ctype = assets[request.url.path]
        return Response(data, media_type=ctype)

    async def session(request: Request) -> Response:
        return JSONResponse({
            "user": request.state.user, "mode": s.mode, "development": not s.production,
            "forms_enabled": s.source_key is not None,
            "tasks": [t.public() for t in T.TASKS.values()]})

    async def run(request: Request) -> Response:
        user = request.state.user
        try:
            if not (same_origin(request) and request.headers.get(CSRF_HEADER) == "1" and is_json(request)):
                raise _Reject(403, "forbidden", "request refused (same-origin JSON requests from the workspace only)")
            body = await read_json(request)
            task = T.TASKS.get(body.get("task"))
            if task is None:
                raise _Reject(400, "invalid_input", "unknown task", field="task")
            if task.kind == "text":
                text = T.free_text(task, body.get("text"))
                subj = T.subjects(task, body.get("people"))
                # The workspace is not a sanitizer: pasted text is sent WITHOUT attestation, so the contract
                # (unattested: PRIVATE) keeps it with the in-house model.
                gw_body = {"model": s.model, "messages": [{"role": "system", "content": task.instruction},
                                                          {"role": "user", "content": text}]}
                if subj:
                    gw_body["pgw_subjects"] = subj
                headers, signed, texts = {}, None, {1: text}
                echo = {"kind": "text", "text": text, "people": subj or {}}
            else:
                if s.source_key is None:
                    raise _Reject(503, "forms_disabled", "structured tasks are not enabled on this workspace")
                payload, rows = T.form_payload(task, body.get("form"))
                content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                gw_body = {"model": s.model, "messages": [{"role": "user", "content": content}]}
                # Built only from validated typed fields and a fixed instruction: the workspace is the source.
                headers = attest.sign(s.source_key, task.purpose, T.FORM_SOURCE_ID, gw_body["messages"], None,
                                      app_id=s.app_id)
                signed, texts = T.FORM_SOURCE_ID, {}
                echo = {"kind": "form", "rows": rows}
        except T.InputError as e:
            return _json_error(400, "invalid_input", e.message, field=e.path)
        except _Reject as r:
            return _json_error(r.status, r.etype, r.message, **r.extra)
        res = await call_gateway(task.purpose, gw_body, headers, user)
        out = result_view(res, texts)
        _log(event="run", task=task.id, status=res.status, outcome=out["view"]["outcome"],
             error_type=(out["error"] or {}).get("type"), round_trip_ms=res.round_trip_ms,
             gateway_request_id=res.request_id, user=_user_ref(user), signed=bool(signed))
        out.update(task=task.id, purpose=task.purpose, signed_as=signed, input=echo,
                   user_message_index=1 if task.kind == "text" else None)
        status = 504 if res.failure == "gateway_timeout" else 502 if res.failure else 200
        return JSONResponse(out, status_code=status)

    async def integration(request: Request) -> Response:
        """A trusted helpdesk integration submits a ticket its source system already signed (server to server).
        The four X-PGW-Input-* headers and the signed messages/pgw_subjects are passed through unchanged; the
        workspace never signs free text. Disabled (404) unless WS_INTEGRATIONS_FILE lists integrations."""
        if not tokens:
            return _json_error(404, "not_found", "not found")
        auth = request.headers.get("authorization", "")
        digest = hashlib.sha256(auth[7:].strip().encode()).hexdigest() if auth.lower().startswith("bearer ") else ""
        who = None
        for iid, h in tokens:
            if hmac.compare_digest(h, digest):
                who = iid
        if who is None:
            return _json_error(401, "unauthenticated", "integration token required")
        try:
            if not is_json(request):
                raise _Reject(415, "unsupported_media_type", "application/json required")
            body = await read_json(request)
            task = T.TASKS.get(body.get("purpose"))
            if task is None or task.kind != "text":
                raise _Reject(400, "invalid_input", "purpose must be a free-text task", field="purpose")
            if set(body) - {"purpose", "messages", "pgw_subjects"}:
                raise _Reject(400, "invalid_input", "unexpected fields")
            msgs = body.get("messages")
            if not isinstance(msgs, list) or not 1 <= len(msgs) <= 20 or not all(isinstance(m, dict) for m in msgs):
                raise _Reject(400, "invalid_input", "messages must be a list of 1-20 messages", field="messages")
            hdrs = {}
            for h in _INPUT_HEADERS:
                v = request.headers.get(h, "").strip()
                if not _INPUT_HEADER_FORMAT.fullmatch(v):
                    raise _Reject(400, "attestation_required", "all four X-PGW-Input-* headers are required",
                                  field=h)
                hdrs[h] = v
        except _Reject as r:
            return _json_error(r.status, r.etype, r.message, **r.extra)
        gw_body = {"model": s.model, "messages": msgs}
        if "pgw_subjects" in body:
            gw_body["pgw_subjects"] = body["pgw_subjects"]
        res = await call_gateway(task.purpose, gw_body, hdrs)
        texts = {i: m["content"] for i, m in enumerate(msgs) if isinstance(m.get("content"), str)}
        out = result_view(res, texts)
        _log(event="integration", task=task.id, status=res.status, outcome=out["view"]["outcome"],
             error_type=(out["error"] or {}).get("type"), round_trip_ms=res.round_trip_ms,
             gateway_request_id=res.request_id, integration=who, signed=True)
        status = 504 if res.failure == "gateway_timeout" else 502 if res.failure else res.status
        return JSONResponse({**out, "purpose": task.purpose}, status_code=status)

    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await client.aclose()

    routes = [Route(p, asset, methods=["GET"]) for p in assets]
    routes += [Route("/api/session", session, methods=["GET"]), Route("/api/run", run, methods=["POST"]),
               Route(INTEGRATION_PATH, integration, methods=["POST"]), Route("/healthz", health, methods=["GET"])]
    if not s.production:
        log.warning("event='workspace_development_mode' user=%r", s.dev_user)
    log.info("event='workspace_started' mode=%r forms=%r integrations=%d", s.mode, s.source_key is not None,
             len(tokens))
    return Guard(Starlette(routes=routes, lifespan=lifespan), s)
