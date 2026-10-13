"""Five-minute demo of the ESU privacy gateway (synthetic data only, mock LLM, no network).

    python -m demo.demo_gateway

Shows, for each request: what the app sent, the exact bytes that left for the "LLM", and what the app got back.
STATUS: research prototype. It is NOT secure for real HR data (see SECURITY_EVALUATION_R3.md); scene 4 shows why.
"""
import hashlib, json, os, tempfile
from pathlib import Path
import yaml
from starlette.testclient import TestClient
from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream

KEY = "demo-key"
SCENES = [
    ("1. Email draft: names, e-mail and salary are aliased, then restored for the HR app", "email_draft", "summarise",
     "Draft an email to Kavya Raghunathan (kavya.r@kestrel-demo.in) confirming her salary revision from "
     "₹14,20,000 to ₹16,10,000 effective 1 April."),
    ("2. Known gap: the shipped gateway runs WITHOUT NER and here without a roster, so person names leave raw (IDs and phone are aliased)", "hr_case_summary", "summarise",
     "Employee Arjun Mehta (EMP-204518, phone +91 98450 12345) reported that his manager Neha Kulkarni "
     "denied leave twice in March."),
    ("3. HIGH-risk value: an Aadhaar number in a free-text request is blocked before anything leaves", "hr_case_summary", "summarise",
     "Please update the KYC for Ritu Sharma, Aadhaar 2345 6789 0124, before payroll closes."),
    ("4. Known weakness: an evasion the current detector misses (what R3 measured)", "hr_case_summary", "echo",
     "Bank a/c for reimbursement: five zero two one eight eight four four three nine one two, IFSC HDFC0001234."),
]

def main():
    tmp = Path(tempfile.mkdtemp())
    reg = tmp / "apps.yaml"
    reg.write_text(yaml.safe_dump({"apps": [{"app_id": "hr-assistant", "tenant": "demo",
        "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(),
        "purposes": ["email_draft", "hr_case_summary"], "restore_output": True, "models": ["mock-1"]}]}))
    for title, purpose, behaviour, text in SCENES:
        mock = MockLLMUpstream(behaviour)
        # The pre-contract (legacy) path, kept to show what detection alone does. Development-only: production
        # refuses legacy_uncontracted and every uncontracted purpose (SECURITY_BOUNDARY.md).
        s = Settings(mode="development", legacy_uncontracted=True, integrity_manifest=None,
                     policy_path="policies/hris-default.yaml", registry_path=str(reg),
                     ledger_db_path=str(tmp / "ledger.sqlite"), egress_ledger_path=str(tmp / "egress.jsonl"),
                     master_key=os.urandom(32).hex(), upstream=mock)
        client = TestClient(create_app(s))
        r = client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": purpose},
                        json={"model": "mock-1", "messages": [{"role": "user", "content": text}]})
        print("=" * 100, f"\n{title}\n", "-" * 100)
        print("APP SENT        :", text)
        if mock.received:
            sent = json.loads(mock.received[-1])["messages"][-1]["content"]
            print("LEFT THE GATEWAY:", sent)
        else:
            print("LEFT THE GATEWAY: (nothing - request stopped at the gateway)")
        try:
            out = r.json()
            msg = out["choices"][0]["message"]["content"] if "choices" in out else json.dumps(out)
        except (ValueError, TypeError, KeyError):
            msg = r.text
        print(f"APP RECEIVED    : HTTP {r.status_code}: {msg}")
    print("=" * 100)
    print("Egress ledger (content-free audit rows):")
    for line in (tmp / "egress.jsonl").read_text().splitlines():
        row = json.loads(line); print("  ", {k: row[k] for k in list(row)[:8]})

if __name__ == "__main__":
    main()
