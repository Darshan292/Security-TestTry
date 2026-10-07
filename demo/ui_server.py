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
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
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
          "send": "Sent to LLM (exact bytes)", "provider": "LLM responded",
          "restore": "Restoration & output gate", "audit": "Audit ledger row written", "final": "Answer returned to app"}
NEXT = {"auth": "contract", "contract": "subjects", "subjects": "route", "local_compute": "protect",
        "protect": "egress", "egress": "send", "send": "provider", "provider": "restore", "restore": "audit"}
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
    private = OpenAICompatUpstream("http://127.0.0.1:8090/v1", "",
                                   client=httpx.AsyncClient(transport=httpx.ASGITransport(app=private_app)))
    try:
        import spacy
        ner = "en_core_web_lg" if spacy.util.is_package("en_core_web_lg") else None
    except ImportError:
        ner = None
    s = Settings(registry_path=str(reg), roster_path=str(ROOT / "tests/fixtures/slice_roster.jsonl"),
                 ledger_db_path=str(tmp / "restore.sqlite"), egress_ledger_path=str(tmp / "audit.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=upstream, private_upstream=private, ner_model=ner,
                 trace=runs.observe)
    return create_app(s), model, provider, ner, s


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

def make_ui(groq_key: str | None) -> Starlette:
    runs = Runs()
    tmp = Path(tempfile.mkdtemp(prefix="pgw-ui-"))
    gw, model, provider, ner, settings = build(groq_key, tmp, runs)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gw), base_url="http://gateway", timeout=180)
    scen = scenarios()

    async def index(_):
        return FileResponse(ROOT / "demo/ui/index.html")

    async def status(_):
        integrity = "unknown"
        try:
            from pgw.product import integrity as I
            integrity = I.verify([settings.policy_path] + [str(ROOT / f"contracts/{p}.yaml") for p in CONTRACTS],
                                 settings.integrity_manifest)
        except Exception as e:  # noqa: BLE001 - shown to the local user only
            integrity = f"FAILED: {type(e).__name__}"
        warnings = ["Synthetic data only. The inspector shows original values next to aliases on this machine."]
        if provider == "groq":
            warnings.append("Provider data terms (retention/region) are not attested for this demo key.")
        return JSONResponse({
            "provider": provider, "model": model,
            "provider_note": "real LLM (Groq)" if provider == "groq" else
            "no GROQ_API_KEY: deterministic mock LLM (answers are templated, not real)",
            "ner": f"{ner} (in-process)" if ner else "off (en_core_web_lg not installed)",
            "contracts": [{"purpose": p, "ref": (c := load_contract(ROOT / f"contracts/{p}.yaml")).ref,
                           "route": c.route, "task_type": c.task_type} for p in CONTRACTS],
            "integrity": integrity,
            "private_route": f"in-boundary endpoint, backend: {PRIVATE_BACKEND.name}"
            + (" (extractive stand-in, NOT an LLM)" if PRIVATE_BACKEND.name == "local-extractive" else ""),
            "warnings": warnings})

    async def scen_list(_):
        return JSONResponse(scen)

    async def run(request: Request):
        try:
            req = await request.json()
            purpose, messages = str(req["purpose"]), req["messages"]
            subj, conv = req.get("subjects"), req.get("conversation_id") or None
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad request"}, status_code=400)

        async def gen():
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
                return ev({"type": "step", "id": sid, "title": TITLES.get(sid, sid), "status": status_,
                           "ms": round((time.perf_counter() - t0) * 1000, 1), "detail": detail or {}})

            n_chars = sum(len(m.get("content") or "") for m in messages if isinstance(m, dict))
            yield step("request", "done", {"purpose": purpose, "messages": len(messages), "characters": n_chars,
                                           "declared_subject_roles": sorted(subj) if isinstance(subj, dict) else [],
                                           "conversation_id": conv or "(none)", "model": model})
            yield step("auth", "running")
            body = {"model": model, "max_tokens": 2048, "messages": messages}
            if subj:
                body["pgw_subjects"] = subj
            headers = {"Authorization": f"Bearer {APP_KEY}", "X-PGW-Purpose": purpose, "X-PGW-Trace": rid}
            if conv:
                headers["X-PGW-Conversation-Id"] = conv
            task = asyncio.create_task(client.post("/v1/chat/completions", json=body, headers=headers))
            try:
                while not (task.done() and q.empty()):
                    try:
                        stage, data = await asyncio.wait_for(q.get(), 0.1)
                    except asyncio.TimeoutError:
                        continue
                    if stage == "error":
                        state["error"] = data
                        running = next((s for s, v in seen.items() if v["status"] == "running"), "auth")
                        yield step(running, "failed", data)
                        yield ev({"type": "error", "status": data.get("status"), "error_type": data.get("error_type"),
                                  "message": data.get("message")})
                        continue
                    if stage == "protect":
                        state["entities"] = data.get("entities") or []
                        data = {**data, "entities": [{k: e[k] for k in ("type", "action", "replacement")}
                                                     for e in state["entities"]]}
                    if stage == "send":
                        state["outbound"] = data.get("outbound") or ""
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
            checks = [
                {"name": "Private route: content stayed inside the boundary (no external call)",
                 "ok": True if private and sent else None, "note": "C4 content goes to the in-boundary model only"},
                {"name": "Protected values absent from what left the gateway",
                 "ok": (not any(v.lower() in outbound for v in sensitive)) if sent and not private else None,
                 "note": f"{len(sensitive)} values checked" if sent else "nothing was sent"},
                {"name": "Declared subjects absent from outbound",
                 "ok": (not any(v.lower() in outbound for v in declared)) if sent and declared and not private
                 else None, "note": f"{len(declared)} declared values" if declared else "none declared"},
                {"name": "Egress invariant passed before sending", "ok": None if private else True if seen.get("egress", {}).get("status") == "done" else
                 (None if not sent else False), "note": "checked on the exact bytes"},
                {"name": "Every alias restored in the answer", "ok": (not ALIAS.search(answer)) if answer else None,
                 "note": f"{len(ALIAS.findall(state['raw']))} alias uses by the LLM"},
                {"name": "Redacted (C3) values never returned", "ok": (not any(v in answer for v in c3))
                 if answer else None, "note": f"{len(c3)} redacted values"},
                {"name": "Fail-closed: refused requests sent nothing", "ok": (not sent) if state["error"] else None,
                 "note": (state["error"] or {}).get("error_type", "request was not refused")},
            ]
            app_input = json.dumps({"messages": messages, "pgw_subjects": subj}, indent=1, ensure_ascii=False)
            try:
                outbound_pretty = json.dumps(json.loads(state["outbound"]), indent=1, ensure_ascii=False)
            except ValueError:
                outbound_pretty = state["outbound"]
            yield ev({"type": "summary", "http_status": resp.status_code, "total_ms": round(total, 1),
                      "provider_ms": state["provider_ms"], "gateway_ms": round(total - state["provider_ms"], 1),
                      "checks": checks,
                      "panes": {"app_input": app_input, "outbound": outbound_pretty, "provider_raw": state["raw"],
                                "answer": answer},
                      "alias_map": [{"alias": e["replacement"], "type": e["type"], "value": e["value"],
                                     "action": e["action"]} for e in state["entities"] if e.get("replacement")]})

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    async def favicon(_):
        from starlette.responses import Response
        return Response(status_code=204)

    return Starlette(routes=[Route("/", index), Route("/favicon.ico", favicon),
                             Route("/api/status", status), Route("/api/scenarios", scen_list),
                             Route("/api/run", run, methods=["POST"])])


def main() -> None:
    import uvicorn
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--groq-key-file", default=None, help="file containing a Groq API key (else $GROQ_API_KEY)")
    a = ap.parse_args()
    key = os.environ.get("GROQ_API_KEY") or (Path(a.groq_key_file).read_text().strip() if a.groq_key_file else None)
    print(f"Privacy Gateway Live Inspector on http://127.0.0.1:{a.port}  (LLM: {'Groq' if key else 'mock'})")
    uvicorn.run(make_ui(key), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
