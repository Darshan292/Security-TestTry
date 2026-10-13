"""Product slice demo (synthetic data, mock external LLM, no network).

    python -m demo.demo_slice

PATH A: HR ticket summary -> identities aliased, bank/tax IDs removed (not blocked) -> LLM -> identities restored.
PATH B: salary comparison -> exact CTCs stay in the gateway -> local computation -> only derived result to LLM.
Then three fail-closed cases. Not secure for real HR data: see the report in PRODUCT_SLICE.md.
"""
import hashlib, json, os, tempfile
from pathlib import Path
import yaml
from starlette.testclient import TestClient
from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product.attestation import sign_headers

ROOT = Path(__file__).resolve().parents[1]
KEY = "pilot-key"
_SANITIZER_IDS = ("band-review-form@1", "esi-coverage-form@1", "hris-case-export@1", "hr-email-composer@1", "comp-review-form@1", "helpdesk-ticket-export@1")
SANITIZER_KEYS = {sid: hashlib.sha256(("DEMO-ONLY-SYNTHETIC:" + sid).encode()).digest() for sid in _SANITIZER_IDS}
TICKET = ("Summarise ticket PAY-88231 in three lines for the weekly payroll review.\n---\n"
          "Ticket PAY-88231 | Raised by: Imran Ansari (KD-11914) | Status: Resolved\n"
          "[28 Sep] Imran Ansari: Extra TDS deduction of Rs 4,500; HRA exemption not applied. PAN ZXWPA3889P.\n"
          "[30 Sep] Payroll Ops: Refund in the November payroll to salary account 644748074748 (IFSC SBIN0095056).")
PAYLOAD = {"instruction": "Who earns more among these two, and by how much? Write a two-line note.",
           "employees": [{"name": "Amit Trivedi", "employee_id": "KD-10989", "annual_ctc": 2450000},
                         {"name": "Hetal Shah", "employee_id": "KD-11063", "annual_ctc": 2180000}]}


def client(tmp):
    reg = tmp / "apps.yaml"
    reg.write_text(yaml.safe_dump({"apps": [{"app_id": "hr-helpdesk-pilot", "tenant": "demo",
        "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(), "purposes": ["ticket_summary", "salary_comparison"],
        "tasks": {"ticket_summary": str(ROOT / "contracts/ticket_summary.yaml"),
                  "salary_comparison": str(ROOT / "contracts/salary_comparison.yaml")},
        "restore_output": True, "models": ["mock-1"]}]}))
    mock = MockLLMUpstream("summarise")
    # Development mode: this throwaway registry is not sealed (production needs a signed manifest).
    s = Settings(mode="development", integrity_manifest=None,
                 policy_path=str(ROOT / "policies/hris-default.yaml"), registry_path=str(reg),
                 roster_path=str(ROOT / "tests/fixtures/slice_roster.jsonl"), ledger_db_path=str(tmp / "l.sqlite"),
                 egress_ledger_path=str(tmp / "egress.jsonl"), master_key=os.urandom(32).hex(), upstream=mock, sanitizer_hmac_keys=SANITIZER_KEYS)
    return TestClient(create_app(s)), mock


def input_source(purpose: str, body: dict) -> dict:
    """Sign fixed synthetic demo input; these keys must never be reused in production."""
    from pgw.product.contracts import load_contract
    path = ROOT / f"contracts/{purpose}.yaml"
    if not path.exists(): return {}
    sid = sorted(load_contract(path).external_input.sanitizers)[0]
    return sign_headers(sanitizer=sid, body=body, key=SANITIZER_KEYS[sid], tenant="demo",
                        app_id="hr-helpdesk-pilot", purpose=purpose)


def scene(title, purpose, content, attest=True):
    tmp = Path(tempfile.mkdtemp())
    c, mock = client(tmp)
    body = {"model": "mock-1", "messages": [{"role": "user", "content": content}],
            **({"pgw_subjects": {"requester": {"name": "Imran Ansari"}}} if purpose == "ticket_summary" else {})}
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose}
    if attest: h.update(input_source(purpose, body))
    r = c.post("/v1/chat/completions", headers=h, json=body)
    print("=" * 100, f"\n{title}\n" + "-" * 100)
    print("APP SENT:\n ", content.replace("\n", "\n  "))
    if mock.received:
        msgs = json.loads(mock.received[-1])["messages"]
        print("LEFT THE GATEWAY (to the external LLM):")
        for m in msgs:
            print(f"  [{m['role']}] " + m["content"].replace("\n", "\n    "))
    else:
        print("LEFT THE GATEWAY: nothing")
    body = r.json()
    msg = body["choices"][0]["message"]["content"] if "choices" in body else json.dumps(body["error"])
    print(f"APP RECEIVED (HTTP {r.status_code}):\n  {msg}")
    if (tmp / "egress.jsonl").exists():
        row = json.loads((tmp / "egress.jsonl").read_text().splitlines()[-1])
        print("AUDIT ROW:", {k: row.get(k) for k in ("route", "contract", "decision", "entity_counts", "reason")})


if __name__ == "__main__":
    scene("PATH A - ticket summary: aliases out, IDs removed (not blocked), names restored", "ticket_summary", TICKET)
    scene("PATH B - salary comparison: exact CTCs stay inside; local compute; derived result only",
          "salary_comparison", json.dumps(PAYLOAD))
    scene("FAIL CLOSED 1 - payload does not match the contract", "salary_comparison",
          json.dumps({**PAYLOAD, "employees": PAYLOAD["employees"][:1]}))
    scene("FAIL CLOSED 2 - script the gateway cannot protect (C4)", "ticket_summary", "सारांश: रामलाल तिवारी का क्लेम")
    scene("FAIL CLOSED 3 - purpose the app is not registered for", "email_draft", "Draft an email")
    scene("FAIL CLOSED 4 - free text without a source-side sanitizer attestation (NOT_EXPECTED boundary)",
          "ticket_summary", TICKET, attest=False)
