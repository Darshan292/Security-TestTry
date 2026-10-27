"""HR Workspace (pgw/workspace): the staff UI and its backend-for-frontend, end to end against a REAL production-mode
gateway (tests/workspace_harness.py: sealed signed config, mandatory contracts, transparency on). Synthetic data only.
"""
import json
import logging
import re
from pathlib import Path

import httpx
import pytest
import yaml

from pgw.product import attest
from pgw.product.contracts import load_contract
from pgw.workspace import report, tasks as T
from pgw.workspace.settings import DEMO_APP_KEY, SettingsError, WorkspaceSettings
from tests.sealed import source_key
from tests.workspace_harness import (APP_KEY, BAND_FORM, ESI_FORM, FIXTURES, HEADERS, INTEGRATION_TOKEN, ORIGIN,
                                     PROXY_IP, REQUESTER, SALARY_FORM, TICKET, USER, gateway, signed_ticket, workspace)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "pgw/workspace/static"
SALARY_FORMS = ["2450000", "2180000", "24,50,000", "21,80,000", "2,450,000", "2,180,000"]


@pytest.fixture(scope="module")
def gw(tmp_path_factory):
    return gateway(tmp_path_factory.mktemp("gw"))


@pytest.fixture
def ws(gw):
    client, rec, s = workspace(gw[0])
    return client, rec, gw[1]


def run(client, body, **headers):
    return client.post("/api/run", json=body, headers={**HEADERS, **headers})


# --- authentication: identity only from the trusted SSO proxy -------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/static/app.js", "/static/app.css", "/api/session"])
def test_identity_header_is_required(ws, path):
    client, _, _ = ws
    r = client.get(path)
    assert r.status_code == 401 and r.json()["error"]["type"] == "unauthenticated"
    assert client.get(path, headers={"X-Forwarded-Email": USER}).status_code == 200


def test_identity_from_an_untrusted_peer_is_rejected(gw):
    client, rec, _ = workspace(gw[0])
    from starlette.testclient import TestClient
    outsider = TestClient(client.app, base_url=ORIGIN, client=("192.0.2.9", 4000))
    assert outsider.get("/api/session", headers={"X-Forwarded-Email": USER}).status_code == 401
    r = outsider.post("/api/run", json={"task": "ticket_summary", "text": TICKET}, headers=HEADERS)
    assert r.status_code == 401 and rec.calls == []


def test_identity_is_the_proxy_header_and_ambiguous_or_bad_values_are_rejected(ws):
    client, _, _ = ws
    assert client.get("/api/session", headers={"X-Forwarded-Email": USER}).json()["user"] == USER
    two = httpx.Headers([("X-Forwarded-Email", USER), ("X-Forwarded-Email", "other@x.example")])
    assert client.get("/api/session", headers=two).status_code == 401
    assert client.get("/api/session", headers={"X-Forwarded-Email": "a\tb"}).status_code == 401
    assert client.get("/healthz").status_code == 200    # the only unauthenticated path


def test_configurable_identity_header(gw):
    client, _, _ = workspace(gw[0], identity_header="X-Auth-Request-User")
    assert client.get("/api/session", headers={"X-Forwarded-Email": USER}).status_code == 401
    assert client.get("/api/session", headers={"X-Auth-Request-User": "asha"}).json()["user"] == "asha"


def test_development_mode_uses_the_local_dev_user_and_says_so(gw):
    client, _, _ = workspace(gw[0], mode="development", trusted_proxies=(), public_origin=None)
    s = client.get("/api/session").json()      # no proxy header needed
    assert s["development"] is True and s["user"] == "dev.user@localhost"
    assert 'id="dev-banner"' in client.get("/").text
    assert client.get("/api/session", headers={"X-Forwarded-Email": "spoof@x"}).json()["user"] == "dev.user@localhost"


@pytest.mark.parametrize("bad, match", [
    ({"trusted_proxies": ()}, "WS_TRUSTED_PROXIES"), ({"public_origin": "http://x.example"}, "WS_PUBLIC_ORIGIN"),
    ({"gateway_key": DEMO_APP_KEY}, "demo app key"),
    ({"source_key": attest.load_demo_keys()[T.FORM_SOURCE_ID]}, "DEMO source key"),
    ({"gateway_url": "ftp://gw"}, "WS_GATEWAY_URL"), ({"trusted_proxies": ("not-an-ip",)}, "WS_TRUSTED_PROXIES")])
def test_production_settings_fail_closed(bad, match):
    base = dict(mode="production", gateway_url="http://gateway:8080", gateway_key="k", trusted_proxies=("10.0.0.0/8",),
                public_origin=ORIGIN)
    with pytest.raises(SettingsError, match=match):
        WorkspaceSettings(**{**base, **bad}).validate()


def test_settings_from_env_read_secrets_from_files(tmp_path):
    (tmp_path / "key").write_text("gw-key\n")
    (tmp_path / "src").write_text(source_key()[0])
    (tmp_path / "int").write_text("# id sha256\nhelpdesk " + "a" * 64 + "\n")
    s = WorkspaceSettings.from_env({"WS_GATEWAY_URL": "http://gateway:8080/", "WS_GATEWAY_KEY_FILE": str(tmp_path / "key"),
                                    "WS_SOURCE_KEY_FILE": str(tmp_path / "src"), "WS_INTEGRATIONS_FILE": str(tmp_path / "int"),
                                    "WS_TRUSTED_PROXIES": "10.1.0.0/16, 10.2.0.5", "WS_PUBLIC_ORIGIN": ORIGIN})
    assert s.gateway_key == "gw-key" and s.gateway_url == "http://gateway:8080" and s.integrations == (("helpdesk", "a" * 64),)
    assert "gw-key" not in repr(s) and source_key()[0] not in repr(s)


# --- CSRF ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("drop, extra", [
    ("Origin", {}), ("X-PGW-Workspace", {}), (None, {"Origin": "https://evil.example"}),
    (None, {"Sec-Fetch-Site": "cross-site"}), (None, {"Origin": "null"}), (None, {"X-PGW-Workspace": "yes"})])
def test_csrf_needs_same_origin_and_custom_header(ws, drop, extra):
    client, rec, _ = ws
    h = {k: v for k, v in HEADERS.items() if k != drop}
    r = client.post("/api/run", json={"task": "ticket_summary", "text": TICKET}, headers={**h, **extra})
    assert r.status_code == 403 and rec.calls == []


def test_csrf_needs_a_json_body_and_no_cors(ws):
    client, rec, _ = ws
    r = client.post("/api/run", content=json.dumps({"task": "ticket_summary", "text": TICKET}),
                    headers={**HEADERS, "Content-Type": "text/plain"})
    assert r.status_code == 403
    pre = client.options("/api/run", headers={**HEADERS, "Origin": "https://evil.example",
                                              "Access-Control-Request-Method": "POST"})
    assert pre.status_code in (403, 405) and "access-control-allow-origin" not in pre.headers
    assert rec.calls == []


def test_body_size_limit(gw):
    client, rec, _ = workspace(gw[0], max_body_bytes=4096)
    r = run(client, {"task": "ticket_summary", "text": "x" * 5000})
    assert r.status_code == 413 and rec.calls == []


# --- headers, CSP, assets, XSS --------------------------------------------------------------------------------------

def test_strict_csp_without_unsafe_inline_on_every_response(ws):
    client, _, _ = ws
    for r in (client.get("/"), client.get("/static/app.js", headers=HEADERS), client.get("/api/session"),
              client.get("/healthz"), run(client, {"task": "nope"})):
        csp = r.headers["content-security-policy"]
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert "script-src 'self'" in csp and "style-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "require-trusted-types-for 'script'" in csp
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer" and r.headers["cache-control"] == "no-store"
        assert "strict-transport-security" in r.headers and "server" not in r.headers


def test_static_assets_have_the_right_types(ws):
    client, _, _ = ws
    h = {"X-Forwarded-Email": USER}
    for path, ctype in [("/", "text/html"), ("/static/app.js", "text/javascript"), ("/static/app.css", "text/css"),
                        ("/static/icon.svg", "image/svg+xml")]:
        r = client.get(path, headers=h)
        assert r.status_code == 200 and r.headers["content-type"].startswith(ctype), path
    for path in ("/static/../app.py", "/static/app.py", "/static/x.js", "/index.html"):
        assert client.get(path, headers=h).status_code == 404


def test_page_has_no_inline_code_or_external_resources():
    html = (STATIC / "index.html").read_text()
    assert re.findall(r"<script\b[^>]*>", html) == ['<script src="/static/app.js" defer>']
    assert "</script>" in html and not re.search(r"<script[^>]*>[^<]+</script>", html)
    assert not re.search(r"\sstyle\s*=", html) and "<style" not in html
    assert not re.search(r"\son[a-z]+\s*=", html, re.I)
    for f in ("index.html", "app.js", "app.css"):
        text = (STATIC / f).read_text()
        assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", text), f
        assert "@import" not in text and "url(http" not in text


def test_frontend_never_parses_data_as_html():
    js = (STATIC / "app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                 "createContextualFragment", "DOMParser", "srcdoc", 'setAttribute("on', "setTimeout(\"",
                 "javascript:"):
        assert sink not in js, sink
    assert "textContent" in js and "createElement" in js


# --- free text: kept inside --------------------------------------------------------------------------------------------

def test_free_text_is_sent_unattested_and_kept_inside(ws):
    client, rec, mock = ws
    before = len(mock.received)
    r = run(client, {"task": "ticket_summary", "text": TICKET, "people": REQUESTER})
    d = r.json()
    assert r.status_code == 200 and d["ok"] and d["signed_as"] is None
    sent = rec.calls[-1]
    assert not any(k.startswith("x-pgw-input") for k in sent["headers"])     # the workspace never signs free text
    assert sent["headers"]["x-pgw-purpose"] == "ticket_summary"
    assert sent["body"]["pgw_subjects"] == {"requester": [{"name": "Imran Ansari", "employee_id": "KD-11914"}]}
    assert sent["body"]["messages"][1] == {"role": "user", "content": TICKET}
    v = d["view"]
    assert v["available"] and v["outcome"] == "kept_inside" and v["route"] == "PRIVATE_LOCAL"
    assert v["outbound"]["destination"] == "in_house" and v["outbound"]["messages"] is None
    assert [s["status"] for s in v["stages"] if s["id"] in ("verify_source", "protect")] == ["failed", "skipped"]
    assert len(mock.received) == before                                       # the external model got nothing
    assert d["answer"] and d["input"] == {"kind": "text", "text": TICKET, "people": sent["body"]["pgw_subjects"]}


def test_missing_required_person_is_refused_and_explained(ws):
    client, rec, mock = ws
    before = len(mock.received)
    d = run(client, {"task": "ticket_summary", "text": TICKET}).json()
    assert d["http_status"] == 400 and not d["ok"] and d["answer"] is None
    assert d["error"]["type"] == "invalid_declared_subjects" and d["error"]["sent"] is False
    assert "People in this request" in d["error"]["plain"] and "requester" in d["error"]["detail"]
    v = d["view"]
    assert v["outcome"] == "refused" and v["stages"] == [{"id": "received", "status": "failed", "ms": v["stages"][0]["ms"],
                                                          "known": True}]
    assert v["outbound"] is None and len(mock.received) == before


@pytest.mark.parametrize("body, field", [
    ({"task": "ticket_summary", "text": "   "}, "text"),
    ({"task": "ticket_summary", "text": "x" * 20001}, "text"),
    ({"task": "ticket_summary", "text": "hi", "people": [{"role": "boss", "name": "A B"}]}, "people[0].role"),
    ({"task": "ticket_summary", "text": "hi", "people": [{"role": "requester", "email": "nope"}]}, "people[0].email"),
    ({"task": "ticket_summary", "text": "hi", "people": [{"role": "requester", "name": "Al\nB"}]}, "people[0].name"),
    ({"task": "hr_email_draft", "text": "hi", "people": [{"role": "requester", "name": "A B"}]}, "people[0].role"),
    ({"task": "unknown"}, "task")])
def test_bad_input_is_refused_by_the_workspace_without_calling_the_gateway(ws, body, field):
    client, rec, _ = ws
    r = run(client, body)
    assert r.status_code == 400 and r.json()["error"]["field"] == field and rec.calls == []


# --- forms: signed by the workspace, computed inside the gateway ----------------------------------------------------

@pytest.mark.parametrize("task, form, secret", [
    ("salary_comparison", SALARY_FORM, SALARY_FORMS), ("esi_letter", ESI_FORM, ["19500", "19,500"]),
    ("band_position", BAND_FORM, ["2150000", "21,50,000", "2,150,000"])])
def test_form_is_signed_and_runs_local_compute_verified(ws, task, form, secret):
    client, rec, mock = ws
    d = run(client, {"task": task, "form": form}).json()
    assert d["ok"] and d["signed_as"] == T.FORM_SOURCE_ID
    h = rec.calls[-1]["headers"]
    assert h["x-pgw-input-source"] == T.FORM_SOURCE_ID and re.fullmatch(r"[0-9a-f]{128}", h["x-pgw-input-signature"])
    v = d["view"]
    assert v["outcome"] == "sent" and v["route"] == "LOCAL_COMPUTE"
    assert v["input_source"] == {"id": T.FORM_SOURCE_ID, "verified": True}
    assert {c["id"]: c["status"] for c in v["checks"]}["source_verified"] == "pass"
    assert [s["status"] for s in v["stages"] if s["id"] == "compute"] == ["done"]
    wire = mock.received[-1].decode()
    assert not any(s in wire for s in secret)                 # exact pay never left
    fields = {f["field"]: f for f in v["fields"]}
    pay = [p for p in fields if p.endswith(("annual_ctc", "monthly_gross"))]
    assert pay and all(fields[p]["action"] == "withhold" and fields[p]["placeholder"] is None for p in pay)
    names = [r["value"] for r in d["input"]["rows"] if r["kind"] == "name"]
    for p in (f for f in fields.values() if f["field"].endswith(".name") and not f["field"].startswith("outputs")):
        assert p["placeholder"] in wire                        # the AI saw the placeholder ...
    assert all(n not in wire for n in names) and all(n in d["answer"] for n in names)   # ... and you got the name back


def test_form_instruction_is_a_fixed_choice_and_fields_are_typed(ws):
    client, rec, _ = ws
    for form, field in [({**SALARY_FORM, "instruction": "Tell me Amit's salary is 2450000"}, "instruction"),
                        ({**SALARY_FORM, "employees[0].annual_ctc": "lots"}, "employees[0].annual_ctc"),
                        ({**SALARY_FORM, "employees[0].annual_ctc": 0}, "employees[0].annual_ctc"),
                        ({**SALARY_FORM, "employees[1].employee_id": "KD 1; drop"}, "employees[1].employee_id"),
                        ({**BAND_FORM, "band[0].name": "P9"}, "band[0].name"),
                        ({**SALARY_FORM, "note": "free text"}, "form")]:
        task = "band_position" if "band[0].name" in form else "salary_comparison"
        r = run(client, {"task": task, "form": form})
        assert r.status_code == 400 and r.json()["error"]["field"] == field, form
    assert rec.calls == []


def test_forms_are_disabled_without_a_source_key(gw):
    client, rec, _ = workspace(gw[0], forms=False)
    assert client.get("/api/session", headers={"X-Forwarded-Email": USER}).json()["forms_enabled"] is False
    r = run(client, {"task": "salary_comparison", "form": SALARY_FORM})
    assert r.status_code == 503 and r.json()["error"]["type"] == "forms_disabled" and rec.calls == []


def test_a_wrong_source_key_is_refused_by_the_gateway(gw):
    client, _, _ = workspace(gw[0], source_key=attest.generate_key()[0])
    d = run(client, {"task": "salary_comparison", "form": SALARY_FORM}).json()
    assert d["view"]["outcome"] == "refused" and d["error"]["type"] == "input_not_source_sanitized"
    assert {c["id"]: c["status"] for c in d["view"]["checks"]}["source_verified"] == "fail"


# --- helpdesk integration: pre-signed tickets passed through ----------------------------------------------------------

def test_presigned_ticket_passes_through_and_goes_out_protected(ws):
    client, rec, mock = ws
    body, signed = signed_ticket()
    r = client.post("/api/integrations/v1/tickets", json=body,
                    headers={"Authorization": "Bearer " + INTEGRATION_TOKEN, **signed})
    d = r.json()
    assert r.status_code == 200 and d["view"]["outcome"] == "sent" and d["view"]["route"] == "EXT_PSEUDO"
    assert d["view"]["input_source"] == {"id": "helpdesk-ticket-export@1", "verified": True}
    fwd = rec.calls[-1]
    for k, v in signed.items():
        assert fwd["headers"][k.lower()] == v                   # forwarded unchanged
    assert fwd["body"]["messages"] == body["messages"] and fwd["body"]["pgw_subjects"] == body["pgw_subjects"]
    text = body["messages"][0]["content"]
    spans = d["view"]["spans"]
    assert spans and all(text[s["start"]:s["end"]] for s in spans)
    assert {text[s["start"]:s["end"]] for s in spans} >= {"Imran Ansari", "ZXWPA3889P", "644748074748"}
    wire = mock.received[-1].decode()
    assert "Imran Ansari" not in wire and "ZXWPA3889P" not in wire


def test_integration_cannot_upgrade_altered_text_and_needs_its_token(ws):
    client, rec, mock = ws
    body, signed = signed_ticket()
    body["messages"][0]["content"] += " extra"                   # changed after signing
    before = len(mock.received)
    auth = {"Authorization": "Bearer " + INTEGRATION_TOKEN}
    d = client.post("/api/integrations/v1/tickets", json=body, headers={**auth, **signed}).json()
    assert d["view"]["outcome"] == "kept_inside" and len(mock.received) == before
    n = len(rec.calls)
    assert client.post("/api/integrations/v1/tickets", json=body, headers=signed).status_code == 401
    assert client.post("/api/integrations/v1/tickets", json=body,
                       headers={"Authorization": "Bearer wrong", **signed}).status_code == 401
    r = client.post("/api/integrations/v1/tickets", json=body, headers=auth)
    assert r.status_code == 400 and r.json()["error"]["type"] == "attestation_required"
    r = client.post("/api/integrations/v1/tickets", json={**body, "purpose": "salary_comparison"}, headers={**auth, **signed})
    assert r.status_code == 400 and len(rec.calls) == n


def test_integration_is_off_unless_configured(gw):
    client, _, _ = workspace(gw[0], integrations=False)
    body, signed = signed_ticket()
    assert client.post("/api/integrations/v1/tickets", json=body,
                       headers={"Authorization": "Bearer " + INTEGRATION_TOKEN, **signed}).status_code == 404


# --- failures are visible -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("exc, status, etype", [
    (httpx.ConnectError("refused"), 502, "gateway_unavailable"), (httpx.ReadTimeout("slow"), 504, "gateway_timeout")])
def test_gateway_outage_and_timeout_fail_visibly(gw, exc, status, etype):
    client, rec, _ = workspace(gw[0])
    rec.fail = exc
    r = run(client, {"task": "ticket_summary", "text": TICKET, "people": REQUESTER})
    d = r.json()
    assert r.status_code == status and not d["ok"] and d["error"]["type"] == etype and d["view"]["outcome"] == "error"
    assert d["error"]["title"] and d["answer"] is None


def test_non_json_gateway_reply_is_an_error_not_an_answer(gw):
    from starlette.responses import PlainTextResponse
    bad = PlainTextResponse("<html>bad gateway</html>", status_code=502)   # e.g. a proxy's error page
    client, _, _ = workspace(bad)
    d = run(client, {"task": "ticket_summary", "text": TICKET, "people": REQUESTER}).json()
    assert d["error"]["type"] == "gateway_unavailable" and d["view"]["outcome"] == "error"


def test_without_a_report_only_what_the_response_proves_is_shown(tmp_path):
    g, _ = gateway(tmp_path, transparency=False)
    client, _, _ = workspace(g)
    d = run(client, {"task": "ticket_summary", "text": TICKET, "people": REQUESTER}).json()
    assert d["view"]["available"] is False and d["view"]["outcome"] == "kept_inside"
    assert d["view"]["stages"] == [] and d["view"]["checks"] == [] and d["view"]["total_ms"] is None


def test_every_gateway_error_type_has_plain_words():
    src = (ROOT / "pgw/gateway/app.py").read_text()
    types = set(re.findall(r'_(?:Reject|err)\(\s*\d+,\s*"([A-Za-z_]+)"', src))
    assert types and types <= set(report.ERRORS)


# --- secrets and content never leak -------------------------------------------------------------------------------------

def test_keys_are_never_in_responses(ws):
    client, _, _ = ws
    body, signed = signed_ticket()
    responses = [client.get(p, headers={"X-Forwarded-Email": USER})
                 for p in ("/", "/static/app.js", "/static/app.css", "/api/session")]
    responses += [run(client, {"task": "ticket_summary", "text": TICKET, "people": REQUESTER}),
                  run(client, {"task": "ticket_summary", "text": TICKET}),
                  run(client, {"task": "salary_comparison", "form": SALARY_FORM}),
                  run(client, {"task": "salary_comparison", "form": {}}),
                  client.post("/api/integrations/v1/tickets", json=body,
                              headers={"Authorization": "Bearer " + INTEGRATION_TOKEN, **signed})]
    for r in responses:
        blob = r.text + json.dumps(dict(r.headers))
        for secret in (APP_KEY, source_key()[0], INTEGRATION_TOKEN, "gateway:8080"):
            assert secret not in blob


def test_no_content_in_logs(ws, caplog):
    client, _, _ = ws
    caplog.set_level(logging.DEBUG)
    marker = "Zubin Wadia-Quill"
    run(client, {"task": "hr_case_explanation", "text": "Case for " + marker + ": " + TICKET,
                 "people": [{"role": "requester", "name": marker, "email": "zubin.q@kestrel-dyn.example"}]})
    run(client, {"task": "hr_case_explanation", "text": "no people " + marker})
    run(client, {"task": "salary_comparison", "form": SALARY_FORM})
    body, signed = signed_ticket()
    client.post("/api/integrations/v1/tickets", json=body, headers={"Authorization": "Bearer " + INTEGRATION_TOKEN, **signed})
    text = caplog.text
    assert "event='run'" in text and "event='integration'" in text
    for needle in (marker, "zubin.q", "Imran", "ZXWPA3889P", "Amit Trivedi", "2450000", "24,50,000", USER, "KD-11914",
                   "Summary:"):
        assert needle not in text, needle


def test_workspace_persists_nothing():
    for f in sorted((ROOT / "pgw/workspace").glob("*.py")):
        src = f.read_text()
        for api in ("write_text", "write_bytes", "sqlite3", "shelve", "pickle", "open(", "localStorage"):
            assert api not in src, (f.name, api)
    js = (STATIC / "app.js").read_text()
    for api in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
        assert api not in js, api     # session history is in-memory only


# --- the adapter, on recorded real gateway responses ------------------------------------------------------------------

FIXTURE_FILES = sorted(FIXTURES.glob("pgw_report_*.json"))


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_adapter_on_recorded_gateway_responses(path):
    fx = json.loads(path.read_text())
    body, status = fx["response"]["body"], fx["response"]["status"]
    rep = body.get("pgw_report") if status == 200 else body["error"]["pgw_report"]
    msgs = fx["request"]["body"]["messages"]
    texts = {i: m["content"] for i, m in enumerate(msgs) if isinstance(m.get("content"), str)}
    v = report.build_view(rep, http_status=status, route_header=fx["response"]["headers"].get("x-pgw-route"),
                          texts=texts)
    assert v["available"] and v["outcome"] == rep["outcome"] and v["dropped"] == 0
    assert [s["id"] for s in v["stages"]] == [s["id"] for s in rep["stages"]]
    assert v["total_ms"] == pytest.approx(sum(s["ms"] for s in rep["stages"]), abs=1e-6)
    assert [c["label"] for c in v["checks"]] == [c["label"] for c in rep["checks"]]   # the server's wording
    for s in v["spans"]:
        assert texts[s["message"]][s["start"]:s["end"]]
    if v["outbound"] and v["outbound"]["messages"]:
        out = v["outbound"]["messages"][-1]["content"]
        for s in v["spans"]:
            if s["placeholder"]:
                assert s["placeholder"] in out
            assert texts[s["message"]][s["start"]:s["end"]] not in out or s["action"] == "allow"
    if rep["route"] == "LOCAL_COMPUTE":
        assert v["spans"] == [] and any(f["field"].startswith("outputs.") for f in v["fields"])
    if rep["outcome"] == "kept_inside":
        assert v["outbound"]["destination"] == "in_house" and v["outbound"]["messages"] is None


def test_fixtures_cover_every_outcome_and_route():
    seen = set()
    for p in FIXTURE_FILES:
        b = json.loads(p.read_text())["response"]["body"]
        r = b.get("pgw_report") or b["error"]["pgw_report"]
        seen |= {r["outcome"], r["route"]}
    assert {"sent", "kept_inside", "refused", "EXT_PSEUDO", "LOCAL_COMPUTE", "PRIVATE_LOCAL"} <= seen


def test_adapter_tolerates_small_deviations_and_drops_malformed_entries():
    text = "Imran Ansari wrote"
    rep = {"version": 1, "outcome": "sent", "route": "EXT_PSEUDO",
           "stages": {"received": {"status": "ok", "ms": 1}, "egress": {"status": "passed", "duration_ms": 2.5},
                      "protect": {"status": "weird"}},
           "detected": [{"message": 0, "offsets": [0, 12], "type": "PERSON", "action": "alias", "placeholder": "[PERSON_AB12CD]"},
                        {"message": 0, "start": 0, "end": 5, "type": "PERSON"},            # overlaps: dropped
                        {"message": 0, "start": 5, "end": 99, "type": "PERSON"},           # out of range
                        {"message": 3, "start": 0, "end": 1, "type": "PERSON"}, "junk",
                        {"message": 0, "start": 0, "end": 2, "type": "IFSC", "action": "allow", "field": "name"}],
           "checks": [{"id": "egress_clean", "status": "na", "label": "x"}], "outbound": {"messages": None, "bytes": 3}}
    v = report.build_view(rep, http_status=200, texts={0: text})
    assert [(s["id"], s["status"], s["ms"]) for s in v["stages"]] == [("received", "done", 1.0), ("egress_check", "done", 2.5)]
    assert [(s["start"], s["end"], s["category"]) for s in v["spans"]] == [(0, 12, "person")]
    assert v["fields"][0]["action"] == "allow" and v["dropped"] == 5
    assert v["checks"][0]["status"] == "n/a" and v["outbound"]["messages"] is None and v["outbound"]["bytes"] == 3


def test_fallback_outcome_from_status_and_route_header():
    assert report.build_view(None, http_status=200, route_header="PRIVATE_LOCAL")["outcome"] == "kept_inside"
    assert report.build_view(None, http_status=200)["outcome"] == "sent"
    assert report.build_view(None, http_status=422, error_type="request_blocked")["outcome"] == "refused"
    assert report.build_view(None, http_status=502, error_type="upstream_error")["outcome"] == "error"


# --- the task catalogue matches the contracts -------------------------------------------------------------------------

def _leaves(schema, prefix=""):
    for k, spec in schema.items():
        if spec["type"] == "list":
            for i in range(spec["min"]):
                yield from _leaves(spec["items"], f"{prefix}{k}[{i}].")
        elif k != "instruction":
            yield prefix + k, spec


def test_task_catalogue_matches_the_contracts():
    for t in T.TASKS.values():
        c = load_contract(ROOT / f"contracts/{t.purpose}.yaml")
        if t.kind == "form":
            assert c.route == "LOCAL_COMPUTE"
            leaves = dict(_leaves(c.input_schema))
            assert [f.path for f in t.fields] == list(leaves)
            for f in t.fields:
                if f.kind == "amount":
                    assert (f.min, f.max) == (leaves[f.path]["min"], leaves[f.path]["max"])
            if t.bands:
                assert {b: [lo, hi] for b, lo, hi in t.bands} == {k: list(v) for k, v in c.reference["pay_bands"].items()}
            for ch in t.choices:
                assert len(ch.text) <= 500 and not re.search(r"\d", ch.text)
        else:
            assert c.route == "EXT_PSEUDO" and c.external_input.unattested == "PRIVATE"
            assert {r.id: r.max for r in t.roles} == dict(c.subjects.roles)
            assert {r.id for r in t.roles if r.required} == set(c.subjects.required)
            assert t.max_chars <= c.max_chars


def test_workspace_source_key_is_pinned_only_on_the_form_contracts():
    raw = json.loads((ROOT / "tests/fixtures/DEMO_ONLY_source_signing_keys.json").read_text())["keys"][T.FORM_SOURCE_ID]
    assert raw["public_key"] in attest.DEMO_PUBKEYS
    for p in sorted((ROOT / "contracts").glob("*.yaml")):
        keys = yaml.safe_load(p.read_text())["external_input"]["sanitizers"]
        local = yaml.safe_load(p.read_text())["route"] == "LOCAL_COMPUTE"
        assert (T.FORM_SOURCE_ID in keys) == local, p.name     # it can never sign free text
        if local:
            assert keys[T.FORM_SOURCE_ID] == raw["public_key"] and len(keys) == 2


def test_workspace_is_registered_with_transparency():
    apps = {a["app_id"]: a for a in yaml.safe_load((ROOT / "registry/apps.yaml").read_text())["apps"]}
    ws = apps["hr-workspace"]
    assert ws["transparency"] is True and set(ws["purposes"]) == set(T.TASKS)
    assert PROXY_IP.startswith("10.20.")    # harness sanity: the proxy address is inside the trusted CIDR
