"""Live Inspector (demo/ui_server.py): UI semantics and local-request protections. Mock LLM, synthetic data only."""
import hashlib
import json

import pytest
from starlette.testclient import TestClient

from demo.ui_server import MAX_RUN_BODY, STEPS, make_ui

BASE = "http://127.0.0.1:8765"
UI = {"Origin": BASE, "X-PGW-UI": "1"}


@pytest.fixture(scope="module")
def client():
    with TestClient(make_ui(None, 8765), base_url=BASE) as c:
        yield c


def events(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def run(client, sid, **override):
    sc = next(s for s in client.get("/api/scenarios").json() if s["id"] == sid)
    body = {"purpose": sc["purpose"], "messages": sc["messages"], "subjects": sc["subjects"], **override}
    r = client.post("/api/run", json=body, headers=UI)
    assert r.status_code == 200, r.text
    ev = events(r)
    return ev, {e["id"]: e for e in ev if e["type"] == "step"}, {c["id"]: c for c in ev[-1]["checks"]}


# --- check semantics ---------------------------------------------------------------------------------------------

def test_external_route_checks_and_honest_wording(client):
    ev, steps, checks = run(client, "A4")
    assert set(STEPS) <= set(steps) and steps["local_compute"]["status"] == "skipped"
    assert steps["local_compute"]["ms"] is None                            # shown as N/A, not a timing
    for cid in ("known_values_absent", "declared_absent", "egress", "aliases_restored"):
        assert checks[cid]["status"] == "pass", cid
    for cid in ("private_boundary", "private_withheld", "fail_closed"):
        assert checks[cid]["status"] == "n/a" and checks[cid]["ok"] is None, cid
    name = checks["known_values_absent"]["name"]
    assert "THIS request" in name and "cannot show that undetected" in name


def test_private_route_marks_external_checks_na_and_checks_withholding(client):
    ev, steps, checks = run(client, "C4")
    summ = ev[-1]
    assert summ["route"] == "PRIVATE_LOCAL"
    for cid in ("known_values_absent", "declared_absent", "egress", "aliases_restored", "c3_not_returned"):
        assert checks[cid]["status"] == "n/a" and checks[cid]["ok"] is None, cid    # never pass or fail
    assert checks["private_boundary"]["status"] == "pass"
    assert checks["private_withheld"]["status"] == "pass" and "644748074748" not in summ["panes"]["answer"]
    for sid in ("local_compute", "protect", "egress"):
        assert steps[sid]["status"] == "skipped" and steps[sid]["ms"] is None
    assert "extractive stand-in" in steps["send"]["title"] and "not an LLM" in steps["send"]["title"]
    assert "not an LLM" in steps["provider"]["title"]
    assert "stand-in" in summ["outbound_caption"] and "not an LLM" in summ["outbound_caption"]


def test_private_withholding_check_fails_when_the_gate_is_broken(client, monkeypatch):
    import re

    import pgw.gateway.app as A
    monkeypatch.setattr(A, "_LONG_DIGITS", re.compile(r"(?!x)x"))       # simulate a broken output gate
    ev, steps, checks = run(client, "C4")
    assert "644748074748" in ev[-1]["panes"]["answer"]
    assert checks["private_withheld"]["status"] == "fail"


def test_outbound_pane_is_labelled_pretty_printed_with_hash_of_serialized_body(client):
    ev, steps, checks = run(client, "A1")
    summ = ev[-1]
    assert "PRETTY-PRINTED" in summ["outbound_caption"] and "not a byte-for-byte" in summ["outbound_caption"]
    canonical = json.dumps(json.loads(summ["panes"]["outbound"]), ensure_ascii=False,
                           separators=(",", ":")).encode()           # the gateway's serialisation of the body
    assert summ["outbound_bytes"] == len(canonical)
    assert summ["outbound_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert summ["outbound_sha256"] in summ["outbound_caption"]


def test_fail_closed_run(client):
    ev, steps, checks = run(client, "FAIL")
    assert any(e["type"] == "error" and e["status"] == 400 for e in ev)
    assert checks["fail_closed"]["status"] == "pass"
    assert checks["egress"]["status"] == "n/a" and checks["known_values_absent"]["status"] == "n/a"
    assert ev[-1]["panes"]["outbound"] == "" and ev[-1]["outbound_caption"] == ""


def test_status_reports_commit_and_mock(client):
    st = client.get("/api/status").json()
    assert st["provider"] == "mock" and st["commit"] and len(st["contracts"]) == 7


# --- request-origin protections and limits ---------------------------------------------------------------------

BODY = {"purpose": "ticket_summary", "messages": [{"role": "user", "content": "hi"}], "subjects": None}


@pytest.mark.parametrize("headers,status", [
    ({"X-PGW-UI": "1"}, 403),                                            # no Origin (non-browser or stripped)
    ({"Origin": "http://evil.example", "X-PGW-UI": "1"}, 403),           # cross-origin page
    ({"Origin": "http://127.0.0.1:9999", "X-PGW-UI": "1"}, 403),         # other local port
    ({"Origin": BASE}, 403),                                             # missing custom header
])
def test_cross_origin_or_unmarked_posts_are_refused(client, headers, status):
    assert client.post("/api/run", json=BODY, headers=headers).status_code == status


def test_non_json_post_and_preflight_are_refused(client):
    r = client.post("/api/run", content=json.dumps(BODY), headers={**UI, "Content-Type": "text/plain"})
    assert r.status_code == 415
    pre = client.options("/api/run", headers={"Origin": "http://evil.example",
                                                "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 403 and "access-control-allow-origin" not in pre.headers


def test_dns_rebinding_host_is_refused():
    with TestClient(make_ui(None, 8765), base_url="http://attacker.example:8765") as c:
        assert c.get("/api/status").status_code == 421
        assert c.post("/api/run", json=BODY, headers={**UI, "Origin": "http://attacker.example:8765"}).status_code == 421


def test_size_and_shape_limits(client):
    big = {**BODY, "messages": [{"role": "user", "content": "x" * (MAX_RUN_BODY + 10)}]}
    assert client.post("/api/run", json=big, headers=UI).status_code == 413
    many = {**BODY, "messages": [{"role": "user", "content": "x"}] * 21}
    assert client.post("/api/run", json=many, headers=UI).status_code == 400
    assert client.post("/api/run", json={**BODY, "subjects": "x"}, headers=UI).status_code == 400


def test_security_headers_on_every_response(client):
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "no-referrer"
    assert client.get("/api/status").headers["x-content-type-options"] == "nosniff"


# --- provider failure diagnostics and forwarded (Codespaces) origins -------------------------------------------

def test_provider_failure_shows_cause_and_hint_without_blaming_other_steps(client, monkeypatch):
    from pgw.gateway.upstream import MockLLMUpstream, UpstreamError

    async def boom(self, body):
        raise UpstreamError("upstream status 401")
    monkeypatch.setattr(MockLLMUpstream, "complete", boom)
    ev, steps, checks = run(client, "A1")
    assert steps["provider"]["status"] == "failed"
    assert steps["provider"]["detail"]["cause"] == "upstream status 401"
    assert "API key" in steps["provider"]["detail"]["hint"]
    assert steps["auth"]["status"] == "done" and steps["send"]["status"] == "done"
    err = next(e for e in ev if e["type"] == "error")
    assert err["status"] == 502 and "upstream status 401" in err["message"] and "API key" in err["message"]
    assert ev[-1]["panes"]["answer"] == ""


def test_codespaces_forwarded_origin_is_allowed_exactly():
    from demo.ui_server import codespaces_origin
    o = codespaces_origin(8765, {"CODESPACE_NAME": "my-space-abc123",
                                 "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev"})
    assert o == "https://my-space-abc123-8765.app.github.dev"
    assert codespaces_origin(8765, {}) is None
    assert codespaces_origin(8765, {"CODESPACE_NAME": "x/../evil", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "a"}) is None
    with TestClient(make_ui(None, 8765, (o,)), base_url=o) as c:
        assert c.get("/api/status").status_code == 200
        ok = c.post("/api/run", json=BODY, headers={"Origin": o, "X-PGW-UI": "1"})
        assert ok.status_code == 200
        bad = c.post("/api/run", json=BODY, headers={"Origin": "https://other-8765.app.github.dev", "X-PGW-UI": "1"})
        assert bad.status_code == 403
