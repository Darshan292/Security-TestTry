"""Privacy Gateway — Live Inspector (local demo UI). SYNTHETIC DATA ONLY.

Runs the real gateway in-process (all contracts, integrity check, declared subjects, local compute, private route)
and streams every step of a request to the browser: what the app sent, what the gateway detected and replaced,
the exact bytes that left for the LLM, the raw LLM response, the restored answer and the audit decision.

    GROQ_API_KEY=... python -m demo.ui_server          # real LLM (Groq), http://127.0.0.1:8765
    python -m demo.ui_server                           # no key: deterministic mock LLM (clearly labelled)

The trace shows original values next to their aliases. That is acceptable only on a trusted local machine with
synthetic data: the server binds to 127.0.0.1 and must not be exposed.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pgw.gateway.app import create_app  # noqa: E402
from pgw.gateway.settings import Settings, check_destination  # noqa: E402
from pgw.gateway.upstream import MockLLMUpstream, OpenAICompatUpstream  # noqa: E402
from pgw.private_llm import BACKEND as PRIVATE_BACKEND, app as private_app  # noqa: E402
from pgw.product.contracts import load_contract  # noqa: E402

APP_KEY = "ui-demo-key"
GROQ_URL, GROQ_MODEL = "https://api.groq.com/openai/v1", "openai/gpt-oss-120b"
ALIAS = re.compile(r"\[[A-Z_]+_[0-9][0-9A-Z]{5}(?:[0-9A-Z]{2})?\]")
STEPS = ["request", "auth", "contract", "subjects", "route", "local_compute", "protect", "egress", "send",
         "provider", "restore", "audit", "final"]
TITLES = {"request": "App request received", "auth": "App authenticated, purpose allowed",
          "contract": "Data contract selected", "subjects": "Declared subjects validated",
          "route": "Route decided", "local_compute": "Local computation (exact values stay inside)",
          "protect": "Detection & protection (aliases, redaction)", "egress": "Egress invariant checked",
          "send": "Sent to LLM (serialized request body; size and SHA-256 below)", "provider": "LLM responded",
          "restore": "Restoration & output gate", "audit": "Audit ledger row written", "final": "Answer returned to app"}
NEXT = {"auth": "contract", "contract": "subjects", "subjects": "route", "local_compute": "protect",
        "protect": "egress", "egress": "send", "send": "provider", "provider": "restore", "restore": "audit"}
def provider_hint(cause: str) -> str:
    """Plain-English reason for a provider failure (the cause itself is content-free)."""
    c = cause.lower()
    if "status 401" in c or "status 403" in c:
        return ("The provider rejected the API key. Check GROQ_API_KEY in the terminal that started the server: "
                "set, current (not rotated), no quotes or spaces.")
    if "status 429" in c:
        return "Rate limited by the provider. Wait a minute and retry."
    if "status 400" in c or "status 404" in c or "status 422" in c:
        return "The provider did not accept the model or parameters for this key."
    if "status 5" in c:
        return "The provider had a server error. Retry later."
    if "transport" in c or "connect" in c or "timeout" in c:
        return "The server could not reach the provider (network, proxy or firewall)."
    return "See the cause; nothing was returned to the app (fail-closed)."


MAX_RUN_BODY = 64 * 1024     # bytes accepted by /api/run (the gateway applies its own limits after this)
MAX_RUN_MESSAGES = 20
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' "
                               "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'none'",
    "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store"}
PRIVATE_TITLES = {"send": "Sent to in-boundary extractive stand-in (not an LLM; never leaves the boundary)",
                  "provider": "In-boundary stand-in responded (extractive, not an LLM)",
                  "restore": "Output gate on the stand-in's answer (C3 and long digit runs withheld)"}
_LONG_DIGITS = re.compile(r"(?<!\d)\d(?:[\s\-./]?\d){8,}(?!\d)")


def git_commit() -> str:
    try:
        import subprocess
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,  # noqa: S603,S607 fixed args
                             text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],  # noqa: S603,S607
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return (sha or "unknown") + ("+uncommitted changes" if dirty else "")
    except Exception:  # noqa: BLE001
        return "unknown"


class LocalOnly:
    """ASGI guard for a localhost tool that can spend the configured provider key.

    * Host must be the exact local address and port (blocks DNS-rebinding pages that resolve to 127.0.0.1).
    * POST /api/run must come from this page: exact same-origin Origin header, `X-PGW-UI: 1` and
      `Content-Type: application/json`. The custom header and JSON type force a CORS preflight for any other
      origin, and the server never answers preflights with CORS headers, so cross-origin pages cannot call it;
      plain HTML forms cannot set the header or the JSON type.
    * Every response carries a strict CSP, no framing, no-sniff and no-referrer.
    """

    def __init__(self, app, port: int, extra_origins: tuple[str, ...] = ()):
        self.app = app
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}
        for o in extra_origins:   # exact forwarded origins only (e.g. this Codespace's port URL), never wildcards
            self.origins.add(o.rstrip("/"))
            self.hosts.add(o.split("://", 1)[1].rstrip("/"))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        h = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        deny = None
        if h.get("host") not in self.hosts:
            deny = (421, "unexpected Host header: this tool only answers on its local address")
        elif scope["method"] == "OPTIONS":
            deny = (403, "cross-origin requests are not supported")
        elif scope["method"] == "POST":
            if h.get("origin") not in self.origins:
                deny = (403, "cross-origin or Origin-less POST refused")
            elif h.get("x-pgw-ui") != "1":
                deny = (403, "missing X-PGW-UI header")
            elif not h.get("content-type", "").startswith("application/json"):
                deny = (415, "application/json required")
        if deny:
            resp = JSONResponse({"error": deny[1]}, status_code=deny[0], headers=SECURITY_HEADERS)
            return await resp(scope, receive, send)

        async def send_with_headers(msg):
            if msg["type"] == "http.response.start":
                hdrs = [(k, v) for k, v in msg.get("headers", []) if k.decode().lower() not in
                        {x.lower() for x in SECURITY_HEADERS}]
                hdrs += [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
                msg = {**msg, "headers": hdrs}
            await send(msg)
        return await self.app(scope, receive, send_with_headers)


CONTRACTS = ["ticket_summary", "hr_case_explanation", "hr_email_draft", "salary_comparison", "esi_letter",
             "band_position", "ticket_summary_any_script"]


# --- gateway wiring --------------------------------------------------------------------------------------------

class Runs:
    """run id -> queue of trace events (the gateway calls `observe` from its event loop)."""

    def __init__(self):
        self.queues: dict[str, asyncio.Queue] = {}

    def observe(self, run: str, stage: str, data: dict) -> None:
        q = self.queues.get(run)
        if q is not None:
            q.put_nowait((stage, data))


def build(groq_key: str | None, tmp: Path, runs: Runs):
    reg = tmp / "apps.yaml"
    reg.write_text(yaml.safe_dump({"apps": [{
        "app_id": "ui-demo", "tenant": "demo", "key_sha256": hashlib.sha256(APP_KEY.encode()).hexdigest(),
        "restore_output": True, "models": ["mock-1", GROQ_MODEL], "purposes": CONTRACTS,
        "tasks": {p: str(ROOT / f"contracts/{p}.yaml") for p in CONTRACTS}}]}))
    if groq_key:
        check_destination(GROQ_URL, ("api.groq.com",))
        upstream, model, provider = OpenAICompatUpstream(GROQ_URL, groq_key, timeout=120), GROQ_MODEL, "groq"
    else:
        upstream, model, provider = MockLLMUpstream("summarise"), "mock-1", "mock"
    from starlette.testclient import TestClient   # synchronous ASGI client for the startup identity probe
    private = OpenAICompatUpstream("http://127.0.0.1:8090/v1", "",
                                   client=httpx.AsyncClient(transport=httpx.ASGITransport(app=private_app)),
                                   sync_client=TestClient(private_app, base_url="http://127.0.0.1:8090"))
    try:
        import spacy
        ner = "en_core_web_lg" if spacy.util.is_package("en_core_web_lg") else None
    except ImportError:
        ner = None
    # Development mode (the trace hook is development-only and refused in production). The throwaway registry is
    # not sealed, so the gateway's integrity check is off here; /api/status still verifies the shipped policy and
    # contracts against policies/MANIFEST.json and shows the result.
    s = Settings(mode="development", integrity_manifest=None, registry_path=str(reg), roster_path=str(ROOT / "tests/fixtures/slice_roster.jsonl"),
                 ledger_db_path=str(tmp / "restore.sqlite"), egress_ledger_path=str(tmp / "audit.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=upstream, private_upstream=private, ner_model=ner,
                 dev_allow_real_provider=bool(groq_key),   # synthetic demo; set in code, never from env
                 trace=runs.observe)
    return create_app(s), model, provider, ner, s


def input_source(purpose: str, body: dict) -> dict:
    """The demo plays the source system: it signs its fixed synthetic scenarios with the contract's DEMO source key
    (committed, development only; production refuses demo keys). A real source keeps its own private key."""
    from pgw.product import attest
    from pgw.product.contracts import load_contract
    sid = sorted(load_contract(ROOT / f"contracts/{purpose}.yaml").external_input.sanitizers)[0]
    return attest.sign_body(attest.load_demo_keys()[sid], purpose, sid, body, app_id="ui-demo")


def scenarios() -> list[dict]:
    sys.path.insert(0, str(ROOT / "scripts"))
    import pilot_validation as PV   # the fixed synthetic workflows used for real-provider validation
    out = []
    for sc in PV.SCENARIOS:
        kind = "B" if sc.purpose in ("salary_comparison", "esi_letter", "band_position") else "A"
        out.append({"id": sc.name.split()[0], "title": sc.name, "purpose": sc.purpose, "kind": kind,
                    "description": "Local computation: exact values stay inside; only permitted results go out"
                    if kind == "B" else "External LLM on protected text; identities aliased and restored",
                    "messages": [{"role": "user", "content": sc.content}], "subjects": sc.subjects})
    out.append({"id": "C4", "title": "C4 Hindi ticket (private route)", "purpose": "ticket_summary_any_script",
                "kind": "C4", "description": "Non-Latin content the gateway cannot read reliably: in-boundary model only",
                "messages": [{"role": "user", "content": "राहुल शर्मा (KD-11914) का वेतन खाता 644748074748 बदलना है। "
                              "नया खाता अगले महीने से लागू होगा। कृपया पुष्टि करें।"}],
                "subjects": {"requester": {"name": "Rahul Sharma", "employee_id": "KD-11914"}}})
    out.append({"id": "MT", "title": "Multi-turn HR case", "purpose": "ticket_summary", "kind": "MT",
                "description": "Run, then 'Continue conversation': aliases stay stable, names stay protected",
                "messages": [{"role": "user", "content": "Summarise this ticket in two lines.\n---\n" + PV.NER_MISS}],
                "subjects": {"requester": {"name": "Imran Ansari", "employee_id": "KD-11914"},
                             "assignee": {"name": "Tejaswini Kulkarni"}, "mentioned": [{"name": "Nandini"}]}})
    out.append({"id": "FAIL", "title": "Fail-closed demo: no declared requester", "purpose": "ticket_summary",
                "kind": "A", "description": "The contract requires a declared requester: refused before anything is sent",
                "messages": [{"role": "user", "content": "Summarise: Imran Ansari reports a payroll issue."}],
                "subjects": None})
    return out


# --- web app ---------------------------------------------------------------------------------------------------

def codespaces_origin(port: int, env=None) -> str | None:
    """The forwarded URL GitHub Codespaces gives this port, if running in a Codespace."""
    e = os.environ if env is None else env
    name, dom = e.get("CODESPACE_NAME"), e.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    if name and dom and re.fullmatch(r"[A-Za-z0-9-]+", name) and re.fullmatch(r"[A-Za-z0-9.-]+", dom):
        return f"https://{name}-{port}.{dom}"
    return None


def check_key(key: str) -> str:
    """One cheap authenticated call at startup so a bad key shows up in the status bar, not as a run failure."""
    try:
        r = httpx.get(GROQ_URL + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
        return "verified" if r.status_code == 200 else f"REJECTED (HTTP {r.status_code}): {provider_hint(f'status {r.status_code}')}"
    except httpx.HTTPError as e:
        return f"UNREACHABLE ({type(e).__name__}): {provider_hint('transport')}"


def make_ui(groq_key: str | None, port: int = 8765, extra_origins: tuple[str, ...] = (), key_status: str = ""):
    runs = Runs()
    tmp = Path(tempfile.mkdtemp(prefix="pgw-ui-"))
    gw, model, provider, ner, settings = build(groq_key, tmp, runs)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gw), base_url="http://gateway", timeout=180)
    scen = scenarios()
    commit = git_commit()
    one_run = asyncio.Semaphore(1)   # at most one provider-spending run at a time

    async def index(_):
        return FileResponse(ROOT / "demo/ui/index.html")

    async def status(_):
        integrity = "unknown"
        try:
            from pgw.product import integrity as I
            integrity = I.verify([settings.policy_path] + [str(ROOT / f"contracts/{p}.yaml") for p in CONTRACTS],
                                 I.DEFAULT_MANIFEST) + " (unsigned: development only)"
        except Exception as e:  # noqa: BLE001 - shown to the local user only
            integrity = f"FAILED: {type(e).__name__}"
        warnings = ["Synthetic data only. The inspector shows original values next to aliases on this machine."]
        if provider == "groq":
            warnings.append("Provider data terms (retention/region) are not attested for this demo key.")
        return JSONResponse({
            "provider": provider, "model": model, "commit": commit, "key_status": key_status,
            "provider_note": (f"real LLM (Groq); key {key_status}" if key_status else "real LLM (Groq)")
            if provider == "groq" else
            "no GROQ_API_KEY: deterministic mock LLM (answers are templated, not real)",
            "ner": f"{ner} (in-process)" if ner else "off (en_core_web_lg not installed)",
            "contracts": [{"purpose": p, "ref": (c := load_contract(ROOT / f"contracts/{p}.yaml")).ref,
                           "route": c.route, "task_type": c.task_type} for p in CONTRACTS],
            "integrity": integrity,
            "private_route": f"in-boundary endpoint, backend: {PRIVATE_BACKEND.name}"
            + (" (extractive stand-in, NOT an LLM)" if PRIVATE_BACKEND.backend == "standin" else ""),
            "warnings": warnings})

    async def scen_list(_):
        return JSONResponse(scen)

    async def run(request: Request):
        cl = request.headers.get("content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > MAX_RUN_BODY):
            return JSONResponse({"error": "request too large"}, status_code=413)
        raw, n = [], 0
        async for chunk in request.stream():
            n += len(chunk)
            if n > MAX_RUN_BODY:
                return JSONResponse({"error": "request too large"}, status_code=413)
            raw.append(chunk)
        try:
            req = json.loads(b"".join(raw))
            purpose, messages = str(req["purpose"]), req["messages"]
            subj, conv = req.get("subjects"), req.get("conversation_id") or None
            if (not isinstance(messages, list) or not 1 <= len(messages) <= MAX_RUN_MESSAGES
                    or not all(isinstance(m, dict) for m in messages)
                    or (subj is not None and not isinstance(subj, dict))
                    or (conv is not None and (not isinstance(conv, str) or len(conv) > 128))):
                raise ValueError
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad request"}, status_code=400)
        if one_run.locked():
            return JSONResponse({"error": "another run is in progress"}, status_code=429)
        await one_run.acquire()

        async def gen():
            try:
                async for chunk in _gen():
                    yield chunk
            finally:
                one_run.release()

        async def _gen():
            rid = uuid.uuid4().hex
            q: asyncio.Queue = asyncio.Queue()
            runs.queues[rid] = q
            t0 = time.perf_counter()
            seen: dict[str, dict] = {}
            state = {"provider_ms": 0.0, "outbound": "", "raw": "", "answer": "", "entities": [], "error": None}

            def ev(obj) -> str:
                return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

            def step(sid, status_, detail=None):
                seen[sid] = {"status": status_}
                title = (PRIVATE_TITLES.get(sid) if state.get("route") == "PRIVATE_LOCAL" else None) or TITLES.get(sid, sid)
                return ev({"type": "step", "id": sid, "title": title, "status": status_,
                           "ms": None if status_ == "skipped" else round((time.perf_counter() - t0) * 1000, 1),
                           "detail": detail or ({"note": "N/A on this route"} if status_ == "skipped" else {})})

            n_chars = sum(len(m.get("content") or "") for m in messages if isinstance(m, dict))
            yield step("request", "done", {"purpose": purpose, "messages": len(messages), "characters": n_chars,
                                           "declared_subject_roles": sorted(subj) if isinstance(subj, dict) else [],
                                           "conversation_id": conv or "(none)", "model": model})
            yield step("auth", "running")
            body = {"model": model, "max_tokens": 2048, "messages": messages}
            if subj:
                body["pgw_subjects"] = subj
            headers = {"Authorization": f"Bearer {APP_KEY}", "X-PGW-Purpose": purpose, "X-PGW-Trace": rid,
                       **input_source(purpose, body)}
            if conv:
                headers["X-PGW-Conversation-Id"] = conv
            task = asyncio.create_task(client.post("/v1/chat/completions", json=body, headers=headers))
            try:
                while not (task.done() and q.empty()):
                    try:
                        stage, data = await asyncio.wait_for(q.get(), 0.1)
                    except asyncio.TimeoutError:
                        continue
                    if stage == "provider_error":
                        cause = str(data.get("cause") or "")
                        state["provider_cause"] = cause
                        yield step("provider", "failed", {"cause": cause, "hint": provider_hint(cause),
                                                          "note": "fail-closed: nothing was returned to the app"})
                        continue
                    if stage == "error":
                        state["error"] = data
                        running = next((s for s, v in seen.items() if v["status"] == "running"), None)
                        if running:
                            yield step(running, "failed", data)
                        msg = data.get("message")
                        if state.get("provider_cause"):
                            msg = f"{msg} — {state['provider_cause']}. {provider_hint(state['provider_cause'])}"
                        yield ev({"type": "error", "status": data.get("status"), "error_type": data.get("error_type"),
                                  "message": msg})
                        continue
                    if stage == "protect":
                        state["entities"] = data.get("entities") or []
                        data = {**data, "entities": [{k: e[k] for k in ("type", "action", "replacement")}
                                                     for e in state["entities"]]}
                    if stage == "send":
                        state["outbound"] = data.get("outbound") or ""
                        state["outbound_sha256"] = data.get("sha256")
                        state["outbound_bytes"] = data.get("bytes")
                        try:
                            data = {**data, "outbound": json.dumps(json.loads(state["outbound"]), indent=1,
                                                                   ensure_ascii=False)}
                        except ValueError:
                            pass
                    if stage == "provider":
                        state["provider_ms"] = data.get("ms") or 0.0
                        state["raw"] = data.get("raw_text") or ""
                    if stage == "route":
                        state["route"] = data.get("route")
                    if stage == "restore":
                        state["answer"] = data.get("answer") or ""
                    yield step(stage, "done", data)
                    nxt = NEXT.get(stage)
                    if stage == "route":
                        nxt = "local_compute" if data.get("route") == "LOCAL_COMPUTE" else (
                            "send" if data.get("route") == "PRIVATE_LOCAL" else "protect")
                    if stage == "route":   # steps this route never takes are marked now, in order
                        skip = {"LOCAL_COMPUTE": [], "PRIVATE_LOCAL": ["local_compute", "protect", "egress"]}.get(
                            data.get("route"), ["local_compute"])
                        for sk in skip:
                            yield step(sk, "skipped", {"note": f"not used on route {data.get('route')}"})
                    if nxt and nxt not in seen:
                        yield step(nxt, "running")
                resp = await task
            finally:
                runs.queues.pop(rid, None)
            total = (time.perf_counter() - t0) * 1000
            out = resp.json()
            answer = state["answer"] or (((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            gw_rid = resp.headers.get("X-PGW-Request-Id")
            row = None
            for line in (tmp / "audit.jsonl").read_text().splitlines() if (tmp / "audit.jsonl").exists() else []:
                r = json.loads(line)
                if r.get("request_id") == gw_rid:
                    row = r
            if row:
                yield step("audit", "done", {k: row.get(k) for k in ("decision", "contract", "route", "policy",
                       "integrity", "provider", "entity_counts", "declared_subjects", "reason", "latency_ms",
                       "body_sha256") if k in row})
            elif "audit" not in seen:
                yield step("audit", "skipped", {"note": "no ledger row (refused before the audit point)"})
            yield step("final", "done" if resp.status_code == 200 else "failed",
                       {"http_status": resp.status_code, "answer": answer} if resp.status_code == 200
                       else {"http_status": resp.status_code, "response": out.get("error")})
            for s in STEPS:
                if s not in seen:
                    yield step(s, "skipped", {})

            # safety checks on this run (computed locally; values never leave this machine)
            outbound = state["outbound"].lower()
            sensitive = {e["value"] for e in state["entities"] if e.get("action") != "ALLOW" and e.get("value")}
            declared = set()
            if isinstance(subj, dict):
                for items in subj.values():
                    for it in (items if isinstance(items, list) else [items]):
                        declared |= {str(v) for v in (it or {}).values()}
            c3 = {e["value"] for e in state["entities"] if e.get("action") == "REDACT"}
            sent = bool(state["outbound"])
            private = state.get("route") == "PRIVATE_LOCAL"   # in-boundary: unprotected by design
            def chk(cid, name, ok, note, na_reason=None):
                status_ = "n/a" if ok is None else ("pass" if ok else "fail")
                return {"id": cid, "name": name, "ok": ok, "status": status_,
                        "note": na_reason if (ok is None and na_reason) else note}

            na_private = "N/A on the private route: in-boundary only, unprotected by design, nothing sent externally"
            # Private route: sensitive values the input contains that must be withheld from the answer (C3 found
            # by the deterministic recognisers, and long digit runs, which the private output gate masks).
            from pgw.detect.recognizers import detect_d2
            from pgw.product.contracts import CLASS_OF, DataClass
            in_text = "\n".join(m.get("content") or "" for m in messages if isinstance(m, dict)
                                and isinstance(m.get("content"), str))
            must_withhold = {in_text[d.start:d.end] for d in detect_d2(in_text) if CLASS_OF.get(d.type) is DataClass.C3}
            must_withhold |= {m.group(0) for m in _LONG_DIGITS.finditer(in_text)}
            checks = [
                chk("private_boundary", "Private route: no call to the external provider",
                    (sent and not state["error"]) if private else None, "content went only to the in-boundary endpoint",
                    "N/A: not the private route"),
                chk("private_withheld",
                    "Private route: answer withholds sensitive values found in the input (C3, long digit runs)",
                    (not any(v in answer for v in must_withhold)) if private and answer else None,
                    f"{len(must_withhold)} values that must be withheld", "N/A: not the private route"),
                chk("known_values_absent",
                    "Values detected or declared in THIS request are absent from the provider request "
                    "(cannot show that undetected personal data did not leak)",
                    None if (private or not sent) else not any(v.lower() in outbound for v in sensitive),
                    f"{len(sensitive)} detected/declared values checked",
                    na_private if private else "N/A: nothing was sent"),
                chk("declared_absent", "Declared subjects absent from the provider request",
                    None if (private or not sent or not declared) else not any(v.lower() in outbound for v in declared),
                    f"{len(declared)} declared values",
                    na_private if private else ("N/A: nothing was sent" if not sent else "N/A: none declared")),
                chk("egress", "Egress invariant ran on the serialized request before sending",
                    None if (private or not sent) else seen.get("egress", {}).get("status") == "done",
                    "checked on the serialized request body", na_private if private else "N/A: nothing was sent"),
                chk("aliases_restored", "Every alias the LLM used was restored",
                    None if (private or not answer) else not ALIAS.search(answer),
                    f"{len(ALIAS.findall(state['raw']))} alias uses by the LLM",
                    na_private if private else "N/A: no answer"),
                chk("c3_not_returned", "Redacted (C3) values never returned",
                    None if (private or not answer) else not any(v in answer for v in c3),
                    f"{len(c3)} redacted values", "N/A: see the private-route check" if private else "N/A: no answer"),
                chk("fail_closed", "Fail-closed: a refused request sent nothing",
                    (not sent) if state["error"] else None, (state["error"] or {}).get("error_type", ""),
                    "N/A: request was not refused"),
            ]
            app_input = json.dumps({"messages": messages, "pgw_subjects": subj}, indent=1, ensure_ascii=False)
            try:
                outbound_pretty = json.dumps(json.loads(state["outbound"]), indent=1, ensure_ascii=False)
            except ValueError:
                outbound_pretty = state["outbound"]
            route_desc = ("the in-boundary extractive stand-in (not an LLM)" if private else f"the provider ({provider})")
            yield ev({"type": "summary", "http_status": resp.status_code, "total_ms": round(total, 1),
                      "route": state.get("route"),
                      "outbound_sha256": state.get("outbound_sha256"), "outbound_bytes": state.get("outbound_bytes"),
                      "outbound_caption": (f"Request body sent to {route_desc}, shown as PRETTY-PRINTED JSON for "
                                           "reading. It is not a byte-for-byte rendering of the HTTP exchange "
                                           "(headers, framing and whitespace differ). The serialized body that was "
                                           f"sent: {state.get('outbound_bytes')} bytes, SHA-256 "
                                           f"{state.get('outbound_sha256')}.") if sent else "",
                      "provider_ms": state["provider_ms"], "gateway_ms": round(total - state["provider_ms"], 1),
                      "checks": checks,
                      "panes": {"app_input": app_input, "outbound": outbound_pretty, "provider_raw": state["raw"],
                                "answer": answer},
                      "alias_map": [{"alias": e["replacement"], "type": e["type"], "value": e["value"],
                                     "action": e["action"]} for e in state["entities"] if e.get("replacement")]})

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    async def favicon(_):
        return Response(status_code=204)

    app = Starlette(routes=[Route("/", index), Route("/favicon.ico", favicon),
                            Route("/api/status", status), Route("/api/scenarios", scen_list),
                            Route("/api/run", run, methods=["POST"])])
    return LocalOnly(app, port, extra_origins)


def main() -> None:
    import uvicorn
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--groq-key-file", default=None, help="file containing a Groq API key (else $GROQ_API_KEY)")
    ap.add_argument("--allow-origin", action="append", default=[],
                    help="exact extra origin allowed to use the UI, e.g. a port-forwarding URL (repeatable)")
    a = ap.parse_args()
    from demo.envfile import load_env, permission_warning
    loaded = load_env()
    if loaded:
        print(f"Loaded from .env: {', '.join(loaded)}")
    if (w := permission_warning()):
        print(f"WARNING: {w}")
    key = (os.environ.get("GROQ_API_KEY") or (Path(a.groq_key_file).read_text() if a.groq_key_file else "")).strip()
    key = key.strip("'\"") or None              # tolerate KEY="..." pasted with quotes
    origins = list(a.allow_origin)
    cs = codespaces_origin(a.port)
    if cs:
        origins.append(cs)
    key_status = check_key(key) if key else ""
    print(f"Privacy Gateway Live Inspector on http://127.0.0.1:{a.port}  (LLM: {'Groq' if key else 'mock'})")
    if key:
        print(f"Groq key check: {key_status}")
    for o in origins:
        print(f"Also allowed (exact origin): {o}")
    uvicorn.run(make_ui(key, a.port, tuple(origins), key_status), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
