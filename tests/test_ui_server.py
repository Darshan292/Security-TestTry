"""Smoke test for the local Live Inspector (demo/ui_server.py) with the mock LLM. Synthetic data only."""
import json

from starlette.testclient import TestClient

from demo.ui_server import STEPS, make_ui


def events(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def run(client, sid):
    sc = next(s for s in client.get("/api/scenarios").json() if s["id"] == sid)
    return events(client.post("/api/run", json={"purpose": sc["purpose"], "messages": sc["messages"],
                                                "subjects": sc["subjects"]}))


def test_status_scenarios_and_full_trace():
    with TestClient(make_ui(None)) as c:
        st = c.get("/api/status").json()
        assert st["provider"] == "mock" and len(st["contracts"]) == 7 and not st["integrity"].startswith("FAILED")
        ev = run(c, "A4")
        final = {e["id"]: e["status"] for e in ev if e["type"] == "step"}
        assert set(STEPS) <= set(final)
        assert final["send"] == "done" and final["local_compute"] == "skipped" and final["restore"] == "done"
        summ = ev[-1]
        assert summ["type"] == "summary" and summ["http_status"] == 200
        checks = {ch["name"]: ch["ok"] for ch in summ["checks"]}
        assert checks["Protected values absent from what left the gateway"] is True
        assert checks["Declared subjects absent from outbound"] is True
        assert "tejaswini" not in summ["panes"]["outbound"].lower()


def test_private_route_and_fail_closed_traces():
    with TestClient(make_ui(None)) as c:
        ev = run(c, "C4")
        steps = {e["id"]: e["status"] for e in ev if e["type"] == "step"}
        assert steps["route"] == "done" and steps["protect"] == "skipped" and steps["restore"] == "done"
        assert ev[-1]["http_status"] == 200
        ev = run(c, "FAIL")
        assert any(e["type"] == "error" and e["status"] == 400 for e in ev)
        summ = ev[-1]
        checks = {ch["name"]: ch["ok"] for ch in summ["checks"]}
        assert summ["panes"]["outbound"] == "" and checks["Fail-closed: refused requests sent nothing"] is True
        assert checks["Egress invariant passed before sending"] is not True
