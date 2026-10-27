"""HR Workspace test harness: a REAL production-mode gateway (tests/sealed.py production(): sealed, signed config,
mandatory contracts, transparency enabled for the app) served in-process, and the workspace BFF pointed at it through
httpx.ASGITransport. A recording transport keeps the raw gateway responses.

    PYTHONPATH=. python -m tests.workspace_harness record    # regenerate tests/fixtures/pgw_report_*.json

The fixtures are recorded gateway responses (synthetic data, mock model); their request ids, placeholders, hashes
and timings change on every recording.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from pgw.gateway.app import create_app as gateway_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.workspace.app import create_app as workspace_app
from pgw.workspace.settings import WorkspaceSettings
from tests.sealed import attest, production, sha, source_key

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
PURPOSES = ["ticket_summary", "hr_case_explanation", "hr_email_draft", "salary_comparison", "esi_letter",
            "band_position"]
APP_ID, APP_KEY = "hr-workspace", "ws-test-app-key-not-a-real-one"
PROXY_IP, ORIGIN = "10.20.0.7", "https://hr-workspace.example.test"
USER = "asha.rao@kestrel-dyn.example"
INTEGRATION_TOKEN = "helpdesk-integration-token-for-tests"   # noqa: S105 test-only

TICKET = ("Ticket PAY-88231 | Payroll | Raised by: Imran Ansari (KD-11914)\n"
          "[28 Sep] Imran Ansari: My October TDS projection shows an extra deduction of Rs 4,500. I uploaded rent "
          "receipts but the HRA exemption was not applied. PAN ZXWPA3889P.\n"
          "[30 Sep] Payroll Ops: The excess TDS will be refunded in the November payroll to salary account "
          "644748074748 (IFSC SBIN0095056). Contact imran.ansari@kestrel-dyn.in if needed.")
REQUESTER = [{"role": "requester", "name": "Imran Ansari", "employee_id": "KD-11914"}]
SALARY_FORM = {"instruction": "who_more", "employees[0].name": "Amit Trivedi", "employees[0].employee_id": "KD-10989",
               "employees[0].annual_ctc": 2450000, "employees[1].name": "Hetal Shah",
               "employees[1].employee_id": "KD-11063", "employees[1].annual_ctc": 2180000}
ESI_FORM = {"instruction": "letter", "employee[0].name": "Meera Iyer", "employee[0].employee_id": "KD-12077",
            "employee[0].monthly_gross": 19500}
BAND_FORM = {"instruction": "manager", "employee[0].name": "Rohan Mehta", "employee[0].employee_id": "KD-10422",
             "employee[0].annual_ctc": 2150000, "band[0].name": "P3"}
HEADERS = {"Origin": ORIGIN, "X-PGW-Workspace": "1", "X-Forwarded-Email": USER}


class Recorder(httpx.AsyncBaseTransport):
    """ASGI transport to the gateway that keeps every (request, response) pair, raw."""

    def __init__(self, app):
        self.inner = httpx.ASGITransport(app=app)
        self.calls: list[dict] = []
        self.fail: Exception | None = None     # set to simulate an outage / timeout

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.fail is not None:
            raise self.fail
        resp = await self.inner.handle_async_request(request)
        body = await resp.aread()
        self.calls.append({"request": request, "headers": dict(request.headers), "body": json.loads(request.content),
                           "status": resp.status_code, "response_headers": dict(resp.headers), "response": body})
        return httpx.Response(resp.status_code, headers=resp.headers, content=body)


def gateway(tmp: Path, transparency: bool = True, **settings):
    app = {"app_id": APP_ID, "tenant": "acme", "key_sha256": sha(APP_KEY), "purposes": PURPOSES,
           "restore_output": True, "models": ["mock-1"], "transparency": transparency,
           "tasks": {p: str(ROOT / f"contracts/{p}.yaml") for p in PURPOSES}}
    mock = settings.pop("upstream", None) or MockLLMUpstream("summarise")
    s = Settings(**production(tmp, [app]), ledger_db_path=str(tmp / "restore.sqlite"),
                 egress_ledger_path=str(tmp / "egress.jsonl"), master_key=os.urandom(32).hex(), upstream=mock,
                 **settings)
    return gateway_app(s), mock


def workspace(gw, *, forms: bool = True, integrations: bool = True, **overrides):
    """(TestClient as the SSO proxy, recorder, settings) for a production-mode workspace in front of `gw`."""
    rec = Recorder(gw)
    kw = dict(mode="production", gateway_url="http://gateway:8080", gateway_key=APP_KEY,
              source_key=source_key()[0] if forms else None, app_id=APP_ID, trusted_proxies=("10.20.0.0/24",),
              public_origin=ORIGIN, integrations=((("helpdesk", sha(INTEGRATION_TOKEN)),) if integrations else ()))
    kw.update(overrides)
    s = WorkspaceSettings(**kw)
    client = TestClient(workspace_app(s, transport=rec), base_url=ORIGIN, client=(PROXY_IP, 51000))
    return client, rec, s


def signed_ticket(purpose: str = "ticket_summary") -> tuple[dict, dict]:
    """A helpdesk ticket as its source system would submit it: body plus the four signed headers (test source key)."""
    body = {"purpose": purpose, "messages": [{"role": "user", "content": "Summarise this ticket.\n---\n" + TICKET}],
            "pgw_subjects": {"requester": [{"name": "Imran Ansari", "employee_id": "KD-11914"}]}}
    return body, attest(purpose, body, app_id=APP_ID)


def record() -> list[str]:
    tmp = Path(tempfile.mkdtemp(prefix="ws-record-"))
    gw, _ = gateway(tmp)
    client, rec, _ = workspace(gw)
    def sent():
        body, hdrs = signed_ticket()
        return client.post("/api/integrations/v1/tickets", json=body,
                           headers={"Authorization": "Bearer " + INTEGRATION_TOKEN, **hdrs})

    runs = {
        "sent": sent,
        "kept_inside": lambda: client.post("/api/run", headers=HEADERS,
                                           json={"task": "ticket_summary", "text": TICKET, "people": REQUESTER}),
        "refused": lambda: client.post("/api/run", headers=HEADERS, json={"task": "ticket_summary", "text": TICKET}),
        "local_compute": lambda: client.post("/api/run", headers=HEADERS,
                                             json={"task": "salary_comparison", "form": SALARY_FORM}),
        "esi": lambda: client.post("/api/run", headers=HEADERS, json={"task": "esi_letter", "form": ESI_FORM}),
        "band": lambda: client.post("/api/run", headers=HEADERS, json={"task": "band_position", "form": BAND_FORM}),
    }
    written = []
    for name, go in runs.items():
        n = len(rec.calls)
        go()
        call = rec.calls[n]
        fx = {"_comment": "Recorded from a real production-mode gateway (tests/workspace_harness.py record): "
                          "synthetic data, mock model. Raw gateway response; its pgw_report is "
                          "docs/TRANSPARENCY_REPORT.md v1.",
              "request": {"purpose": call["headers"].get("x-pgw-purpose"),
                          "attested": "x-pgw-input-signature" in call["headers"], "body": call["body"]},
              "response": {"status": call["status"],
                           "headers": {k: v for k, v in call["response_headers"].items() if k.startswith("x-pgw")},
                           "body": json.loads(call["response"])}}
        path = FIXTURES / f"pgw_report_{name}.json"
        path.write_text(json.dumps(fx, indent=2, ensure_ascii=False) + "\n")
        written.append(path.name)
    return written


if __name__ == "__main__":
    if sys.argv[1:] != ["record"]:
        sys.exit("usage: python -m tests.workspace_harness record")
    print("\n".join(record()))
