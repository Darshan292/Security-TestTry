"""Pilot validation against a REAL external provider, SYNTHETIC HRIS data only (no real employee data, ever).

Runs the five pilot tasks through the real gateway app (integrity check, local NER, contracts, limits) and the
real Anthropic adapter, and records inside the trusted environment only:
  exact outbound HTTP body, exact inbound HTTP body, restored application response, gateway decision (ledger row),
  latency (total, provider, gateway).
Then checks: raw protected values never leave; derived values obey their contract; aliases survive provider
formatting; restoration works; provider failures fail closed; gateway failures fail closed; no protected value
appears in logs, error bodies, the audit ledger or stderr.

    ANTHROPIC_API_KEY=... python -m scripts.pilot_validation                 # real provider
    python -m scripts.pilot_validation --provider mock                       # harness self-test only

Captures contain synthetic raw values and go OUTSIDE the repository (default /home/user/pilot_captures/<ts>/).
The summary (`summary.md`) is content-free: check names, pass/fail, counts and latencies only.
Without ANTHROPIC_API_KEY the run still goes to the real endpoint with a deliberately invalid key: outbound
payloads and provider-failure handling are validated for real; checks that need a model answer are BLOCKED.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pgw.gateway.app import build_upstream, create_app  # noqa: E402
from pgw.gateway.settings import Settings, check_destination  # noqa: E402
from pgw.gateway.upstream import AnthropicUpstream, MockLLMUpstream, OpenAICompatUpstream  # noqa: E402
from pgw.alias import ALIAS_RE as ALIAS_BODY  # noqa: E402
from pgw.product.attestation import sign_headers  # noqa: E402
from pgw.product.local_compute import surface_forms  # noqa: E402

MODEL = "claude-opus-5-5"
MODELS = {"anthropic": MODEL, "groq": "openai/gpt-oss-120b", "mock": "mock-1"}
GROQ_URL = "https://api.groq.com/openai/v1"
APP_KEY = "pilot-hr-helpdesk-key-change-me"      # registry/apps.yaml demo key for hr-helpdesk-pilot
BASE_URL = "https://api.anthropic.com"
# Exact alias tokens as the gateway mints them ([TYPE_<digit><5 or 7 base32>]); redaction markers such as
# [BANK_ACCOUNT] are not aliases. ALIAS_RE (the restorer's own lenient pattern) finds mangled variants.
ALIAS = re.compile(r"\[[A-Z_]+_[0-9][0-9A-Z]{5}(?:[0-9A-Z]{2})?\]")

# --- synthetic scenarios (fictional people at a fictional company; identifiers are synthetic) ----------------

TICKET = (
    "Ticket PAY-88231 | Category: Payroll | Raised by: Imran Ansari (KD-11914) | Status: Resolved\n"
    "[28 Sep 2026] Imran Ansari: My October TDS projection shows an extra deduction. I had uploaded rent receipts "
    "but HRA exemption was not applied. PAN ZXWPA3889P.\n"
    "[29 Sep 2026] Payroll Ops: Rent receipts were uploaded after the 25 September cut-off. Declaration re-opened "
    "after approval from the payroll lead Suresh Menon.\n"
    "[30 Sep 2026] Payroll Ops: The excess TDS will be refunded in the November 2026 payroll to salary account "
    "644748074748 (IFSC SBIN0095056). Contact imran.ansari@kestrel-dyn.in if needed.\n")
CASE = (
    "Case HR-5521 | Type: Leave encashment | Employee: Hetal Shah (KD-11063)\n"
    "Hetal Shah resigned with last working day 31 Oct 2026 and has 12 days of earned leave balance. Policy LV-04 "
    "allows encashment of up to 30 days of earned leave at final settlement. Her reporting manager Kavita Rao "
    "approved the encashment on 1 Oct 2026. Aadhaar 2341 2341 2346 was submitted for the F&F form.\n"
    "Open question from HR operations: why has the encashment not appeared in the F&F computation yet?")
EMAIL = (
    "Draft a short, polite email from HR Operations to Amit Trivedi (KD-10989, amit.trivedi@kestrel-dyn.in, "
    "+91 97400 11223) confirming that his salary account was updated to 50100234567891 (IFSC HDFC0001234) on "
    "2 Oct 2026, that his UPI ID amit@okhdfc will not be used for payroll, and that the change applies from the "
    "October 2026 payroll. Do not include the account number in the email.")
SALARY = {"instruction": "Who earns more among these two, and by how much? Write a two-line note for the HR manager.",
          "employees": [{"name": "Amit Trivedi", "employee_id": "KD-10989", "annual_ctc": 2450000},
                        {"name": "Hetal Shah", "employee_id": "KD-11063", "annual_ctc": 2180000}]}
# Names the existing detectors (roster, cues, en_core_web_lg) miss in this text: only the declaration protects them.
NER_MISS = ("Ticket PAY-90412 | Raised by: Imran Ansari (KD-11914)\n"
            "Imran Ansari: my arrears for August 2026 are missing from the September payslip.\n"
            "Payroll Ops: please loop in tejaswini kulkarni from payroll before closing.\n"
            "Reply from Nandini: the arrears file was re-run on 30 Sep 2026; the amount will be paid in October.\n")
BAND = {"instruction": "Explain to the HR manager where this employee sits in their pay band, in two lines.",
        "employee": [{"name": "Hetal Shah", "employee_id": "KD-11063", "annual_ctc": 2180000}],
        "band": [{"name": "P3", "min_ctc": 1800000, "max_ctc": 2600000}]}
ESI = {"instruction": "Explain to the employee whether ESI applies to them, in two lines.",
       "employee": [{"name": "Hetal Shah", "employee_id": "KD-11063", "monthly_gross": 19800}]}


@dataclass
class Scenario:
    name: str
    purpose: str
    content: str
    raw: list[str]                       # protected values that must never leave raw
    must_cross: list[str] = field(default_factory=list)     # contract says RAW: must be in the payload
    expect_restored: list[str] = field(default_factory=list)  # authorised values the app should get back
    never_returned: list[str] = field(default_factory=list)   # C3: never in the restored response
    subjects: dict | None = None         # PATH A declared subjects (pgw_subjects)
    ner_misses: list[str] = field(default_factory=list)       # declared names to check NER alone would miss
    ner_only: list[str] = field(default_factory=list)         # undeclared names whose protection requires NER
    answer_has: list[str] = field(default_factory=list)       # business-correctness (content) checks, case-insens.
    answer_lacks: list[str] = field(default_factory=list)


def _forms(*vals):
    out = []
    for v in vals:
        out += surface_forms(v)
    return out


SCENARIOS = [
    Scenario("A1 ticket summary", "ticket_summary", "Summarise this ticket in three lines for the weekly payroll "
             "review.\n---\n" + TICKET,
             raw=["Imran Ansari", "KD-11914", "ZXWPA3889P", "644748074748", "imran.ansari@kestrel-dyn.in"],
             ner_only=["Suresh Menon"],
             expect_restored=["Imran Ansari"], never_returned=["ZXWPA3889P", "644748074748"],
             # Suresh Menon is deliberately NOT declared: NER is the safety net for undeclared people.
             subjects={"requester": {"name": "Imran Ansari", "employee_id": "KD-11914",
                                     "email": "imran.ansari@kestrel-dyn.in"}}),
    Scenario("A2 HR case explanation", "hr_case_explanation", "Explain the status of this case and the next "
             "step for HR operations.\n---\n" + CASE,
             raw=["Hetal Shah", "KD-11063", "2341 2341 2346", "234123412346", "Kavita Rao"],
             must_cross=["12 days"],    # leave_balance: PSEUDONYMISED_RAW in this contract
             expect_restored=["Hetal Shah"], never_returned=["2341 2341 2346"],
             answer_has=["hypothes"], answer_lacks=["[AADHAAR]"],
             subjects={"requester": {"name": "Hetal Shah", "employee_id": "KD-11063"},
                       "mentioned": [{"name": "Kavita Rao"}]}),
    Scenario("A3 email drafting", "hr_email_draft", EMAIL,
             raw=["Amit Trivedi", "KD-10989", "amit.trivedi@kestrel-dyn.in", "97400 11223", "9740011223",
                  "50100234567891", "amit@okhdfc"],
             expect_restored=["Amit"], never_returned=["50100234567891", "amit@okhdfc"],
             answer_has=["hr-ops@kestrel-dyn.in"],
             answer_lacks=["97400 11223", "amit.trivedi@kestrel-dyn.in", "[UPI_ID]", "[BANK_ACCOUNT]", "[Company"],
             subjects={"recipient": {"name": "Amit Trivedi", "employee_id": "KD-10989",
                                     "email": "amit.trivedi@kestrel-dyn.in", "phone": "+91 97400 11223"}}),
    Scenario("A4 ticket with names NER misses", "ticket_summary", "Summarise this ticket in two lines for the "
             "payroll lead.\n---\n" + NER_MISS,
             raw=["Imran Ansari", "KD-11914", "tejaswini", "Tejaswini", "kulkarni", "Kulkarni", "Nandini"],
             expect_restored=["Imran Ansari", "Nandini"],
             subjects={"requester": {"name": "Imran Ansari", "employee_id": "KD-11914"},
                       "assignee": {"name": "Tejaswini Kulkarni"}, "mentioned": [{"name": "Nandini"}]},
             ner_misses=["tejaswini kulkarni", "Nandini"]),
    Scenario("B1 salary comparison", "salary_comparison", json.dumps(SALARY),
             raw=["Amit Trivedi", "KD-10989", "Hetal Shah", "KD-11063"] + _forms(2450000, 2180000, 270000)
             + ["12.4%"],                # TRANSFORMED: only as an alias
             expect_restored=["Amit Trivedi", "₹2,70,000"]),
    Scenario("B2 ESI eligibility letter", "esi_letter", json.dumps(ESI),
             raw=["Hetal Shah", "KD-11063"] + _forms(19800, 1200),
             must_cross=["covered by ESI", "₹21,000"],   # RAW by contract (justified) / C0
             expect_restored=["Hetal Shah"], answer_has=["Hetal Shah"]),
]
SCENARIOS.append(
    Scenario("B3 pay band position", "band_position", json.dumps(BAND),
             raw=["Hetal Shah", "KD-11063"] + _forms(2180000, 420000) + ["48%"],
             must_cross=["P3", "middle third"], expect_restored=["Hetal Shah"]))
ALL_RAW = sorted({v for s in SCENARIOS for v in s.raw})


# --- capture --------------------------------------------------------------------------------------------------

class Capture:
    def __init__(self):
        self.exchanges: list[dict] = []

    async def on_request(self, req):
        self.exchanges.append({"url": str(req.url), "t_req": time.perf_counter(),
                               "outbound": req.content.decode("utf-8", "replace")})

    async def on_response(self, resp):
        await resp.aread()
        self.exchanges[-1].update(t_resp=time.perf_counter(), status=resp.status_code,
                                  inbound=resp.content.decode("utf-8", "replace"))


def groq_upstream(cap: Capture, key: str, timeout: float = 120.0) -> OpenAICompatUpstream:
    import httpx
    check_destination(GROQ_URL, ("api.groq.com",))     # the deployment's allowlist for this provider
    client = httpx.AsyncClient(timeout=timeout, event_hooks={"request": [cap.on_request],
                                                             "response": [cap.on_response]})
    return OpenAICompatUpstream(GROQ_URL, key, timeout=timeout, client=client)


def real_upstream(provider: str, cap: Capture, key: str, timeout: float = 120.0):
    return (groq_upstream if provider == "groq" else anthropic_upstream)(cap, key, timeout)


def anthropic_upstream(cap: Capture, key: str, timeout: float = 120.0) -> AnthropicUpstream:
    import anthropic
    check_destination(BASE_URL, Settings().egress_allowlist)   # same rule build_upstream applies
    http = anthropic.DefaultAsyncHttpxClient(event_hooks={"request": [cap.on_request], "response": [cap.on_response]})
    client = anthropic.AsyncAnthropic(api_key=key, base_url=BASE_URL, timeout=timeout, max_retries=0,
                                      http_client=http)
    return AnthropicUpstream(key, client=client)


class MockCapture(MockLLMUpstream):
    """Self-test stand-in: echoes the protected prompt, so restoration and alias handling are exercised."""
    def __init__(self, cap: Capture):
        super().__init__("echo")
        self.cap = cap

    async def complete(self, body):
        out = await super().complete(body)
        self.cap.exchanges.append({"url": "mock://", "t_req": time.perf_counter(), "t_resp": time.perf_counter(),
                                   "status": 200, "outbound": self.received[-1].decode(), "inbound": json.dumps(out)})
        return out


def payload_strings(body: str) -> str:
    """All string values in a JSON body, decoded (an escaped \\u20b9 must not hide a value from the scan)."""
    try:
        obj = json.loads(body)
    except ValueError:
        return body
    acc: list[str] = []

    def walk(x):
        if isinstance(x, str):
            acc.append(x)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
    walk(obj)
    return "\n".join(acc)


def inbound_text(inbound: str) -> str:
    try:
        d = json.loads(inbound)
    except ValueError:
        return ""
    if "content" in d:   # Anthropic Messages response
        return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    return "".join((c.get("message") or {}).get("content") or "" for c in d.get("choices", []))


# --- run ------------------------------------------------------------------------------------------------------

def make_settings(out: Path, upstream, **kw) -> Settings:
    # Development mode: the repo manifest is sealed but unsigned (production requires the operator's Ed25519
    # signature, SECURITY_BOUNDARY.md). Integrity of registry, policy and contracts is still verified.
    return Settings(mode="development", registry_path=str(ROOT / "registry/apps.yaml"),
                    roster_path=str(ROOT / "tests/fixtures/slice_roster.jsonl"),
                    ledger_db_path=str(out / "restore_ledger.sqlite"), egress_ledger_path=str(out / "egress.jsonl"),
                    master_key=os.urandom(32).hex(), ner_model=None, upstream=upstream,
                    sanitizer_hmac_keys={sid: hashlib.sha256(("PILOT-ONLY:" + sid).encode()).digest() for sid in
                        ("band-review-form@1", "esi-coverage-form@1", "hris-case-export@1", "hr-email-composer@1", "comp-review-form@1", "helpdesk-ticket-export@1")},
                    **kw)


def input_source(purpose: str, body: dict) -> dict:
    """Sign synthetic harness requests; never use these test keys in production."""
    from pgw.product.contracts import load_contract
    path = ROOT / f"contracts/{purpose}.yaml"
    if not path.exists():   # an unregistered/uncontracted purpose: the gateway refuses it before this matters
        return {}
    sid = sorted(load_contract(path).external_input.sanitizers)[0]
    key = hashlib.sha256(("PILOT-ONLY:" + sid).encode()).digest()
    return sign_headers(sanitizer=sid, body=body, key=key, tenant="acme", app_id="hr-helpdesk-pilot", purpose=purpose)


def post(client, purpose, content, model=MODEL, subjects=None, messages=None, conv=None):
    body = {"model": model, "max_tokens": 4096,
            "messages": messages or [{"role": "user", "content": content}]}
    if subjects is not None:
        body["pgw_subjects"] = subjects
    h = {"Authorization": f"Bearer {APP_KEY}", "X-PGW-Purpose": purpose, **input_source(purpose, body)}
    if conv:
        h["X-PGW-Conversation-Id"] = conv
    return client.post("/v1/chat/completions", headers=h, json=body)


def available_ner_model() -> str | None:
    try:
        import spacy
        return "en_core_web_lg" if spacy.util.is_package("en_core_web_lg") else None
    except (ImportError, OSError):
        return None


def ner_alone_finds(text: str, names: list[str]) -> list[str] | None:
    """Return None rather than claiming coverage when the pinned NER model is absent."""
    model = available_ner_model()
    if model is None:
        return None
    try:
        from pgw.detect.ner import NerDetector
        from pgw.pipeline import Keys, Protector
        from pgw.policy import PolicyPack
        p = Protector(PolicyPack.load(str(ROOT / "policies/hris-default.yaml")), Keys(bytes(32)), ner=NerDetector(model))
        found = " | ".join(text[d.start:d.end] for d in p.detect_text(text)).lower()
        return [n for n in names if n.lower() in found]
    except (ImportError, OSError, RuntimeError):
        return None


def ledger_row(out: Path, rid: str) -> dict | None:
    for line in (out / "egress.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["request_id"] == rid:
            return r
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["anthropic", "groq", "mock"], default="groq")
    ap.add_argument("--out", default=f"/home/user/pilot_captures/{time.strftime('%Y%m%d-%H%M%S')}")
    args = ap.parse_args()
    try:
        import spacy
        ner_model = "en_core_web_lg" if spacy.util.is_package("en_core_web_lg") else None
    except ImportError:
        ner_model = None
    if args.provider != "mock" and ner_model is None:
        raise SystemExit("Install the pinned en_core_web_lg model before provider validation; no provider requests sent.")
    from demo.envfile import load_env
    load_env()
    out = Path(args.out).resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("captures contain synthetic raw values: write them outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    if args.provider == "groq":
        key = os.environ.get("GROQ_API_KEY", "")
        kf = Path(os.environ.get("PGW_GROQ_KEY_FILE", "/root/.groq_key"))
        if not key and kf.exists():
            key = kf.read_text().strip()
    else:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = MODELS[args.provider]
    have_answers = args.provider != "mock" and bool(key)
    mode = "mock self-test (NOT provider validation)" if args.provider == "mock" else (
        f"real provider ({args.provider}, {model})" if key else "real endpoint, invalid key (answers BLOCKED)")

    logbuf = io.StringIO()
    handler = logging.StreamHandler(logbuf)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    stderr = io.StringIO()
    checks: list[tuple[str, str, str]] = []     # (check, PASS/FAIL/BLOCKED, content-free note)
    records = []
    error_bodies: list[str] = []

    def check(name, ok, note=""):
        checks.append((name, "BLOCKED" if ok is None else ("PASS" if ok else "FAIL"), note))

    cap = Capture()
    up = MockCapture(cap) if args.provider == "mock" else real_upstream(args.provider, cap, key or "invalid-pilot-check")
    with contextlib.redirect_stderr(stderr):
        app = create_app(make_settings(out, up, ner_workers=2))   # the main app runs NER in the bounded pool
        with TestClient(app) as client:
            for sc in SCENARIOS:
                n0 = len(cap.exchanges)
                t0 = time.perf_counter()
                r = post(client, sc.purpose, sc.content, model=model,
                         subjects=sc.subjects)
                total = (time.perf_counter() - t0) * 1000
                ex = cap.exchanges[n0:]
                rid = r.headers.get("X-PGW-Request-Id", "")
                row = ledger_row(out, rid)
                body = r.json()
                restored = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                if r.status_code != 200:
                    error_bodies.append(r.text)
                outbound = "\n".join(e["outbound"] for e in ex)
                inbound = "\n".join(e.get("inbound", "") for e in ex)
                provider_ms = sum((e.get("t_resp", e["t_req"]) - e["t_req"]) * 1000 for e in ex)
                rec = {"scenario": sc.name, "purpose": sc.purpose, "status": r.status_code, "request_id": rid,
                       "gateway_decision": row, "latency_ms": {"total": round(total, 1),
                                                               "provider": round(provider_ms, 1),
                                                               "gateway": round(total - provider_ms, 1)},
                       "outbound_http": [e["outbound"] for e in ex], "inbound_http": [e.get("inbound") for e in ex],
                       "provider_status": [e.get("status") for e in ex], "restored_response": restored,
                       "app_response": body}
                records.append(rec)
                (out / f"{sc.name.split()[0]}_{sc.purpose}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))

                sent_text = outbound + "\n" + payload_strings(outbound)
                leaked = [v for v in sc.raw if v in sent_text]
                check(f"{sc.name}: request reached the provider", bool(ex), f"{len(ex)} exchange(s)")
                what = ("local-compute inputs and INTERNAL outputs" if sc.purpose in ("salary_comparison", "esi_letter")
                        else "raw protected value")
                check(f"{sc.name}: no {what} in outbound bytes", bool(ex) and not leaked,
                      f"{len(sc.raw)} values checked, {len(leaked)} found")
                if sc.ner_only:
                    ner_leaked = [v for v in sc.ner_only if v.lower() in sent_text.lower()]
                    check(f"{sc.name}: undeclared NER-only values are not sent",
                          None if ner_model is None else bool(ex) and not ner_leaked,
                          "NER model unavailable; not evaluated" if ner_model is None else
                          f"{len(sc.ner_only)} values checked, {len(ner_leaked)} found")
                if sc.must_cross:
                    missing = [v for v in sc.must_cross if v not in sent_text]
                    check(f"{sc.name}: contract RAW/C0 values cross as declared", bool(ex) and not missing,
                          f"{len(sc.must_cross) - len(missing)}/{len(sc.must_cross)} present")
                aliases_out = sorted(set(ALIAS.findall(payload_strings(outbound))))
                check(f"{sc.name}: aliases in outbound", bool(aliases_out), f"{len(aliases_out)} distinct")
                check(f"{sc.name}: ledger row with contract, route, integrity", bool(row and row.get("contract")
                      and row.get("integrity") not in (None, "unchecked")),
                      f"{(row or {}).get('decision', 'none')} {(row or {}).get('contract')} "
                      f"subjects={(row or {}).get('declared_subjects')}")
                if sc.ner_misses:
                    found = ner_alone_finds(sc.content, sc.ner_misses)
                    check(f"{sc.name}: detectors alone (incl. NER) miss declared names",
                          None if found is None else len(found) < len(sc.ner_misses),
                          "NER model unavailable; not evaluated" if found is None else
                          f"detectors alone find {len(found)}/{len(sc.ner_misses)}")
                    check(f"{sc.name}: declared subjects protected where NER misses them", bool(ex) and not leaked,
                          f"{len(leaked)} leaked after declaration")
                if r.status_code != 200:
                    for c in ("aliases survive provider formatting", "restoration", "C3 never returned"):
                        check(f"{sc.name}: {c}", False, f"gateway status {r.status_code}")
                    continue
                ans = inbound_text(inbound)
                exact = [a for a in aliases_out if a in ans]
                mangled = [m.group(0) for m in ALIAS_BODY.finditer(ALIAS.sub("", ans))]
                check(f"{sc.name}: aliases survive provider formatting", not mangled,
                      f"{len(exact)} exact alias uses in answer, {len(mangled)} mangled")
                left = [m.group(0) for m in ALIAS_BODY.finditer(restored)]   # incl. lenient/mangled forms
                got = [v for v in sc.expect_restored if v in restored]
                used = [x for x in aliases_out if x in ans] + mangled
                check(f"{sc.name}: restoration (every alias the model used came back as the real value)",
                      not left, f"{len(used)} alias uses in the answer, {len(left)} left unrestored")
                low = restored.lower()
                for k, v in enumerate(sc.answer_has, 1):
                    check(f"{sc.name}: answer contains required item {k} (business correctness)",
                          None if not have_answers else v.lower() in low,
                          "mock provider echoes the prompt; semantic answer not evaluated" if not have_answers else "")
                for v in sc.answer_lacks:
                    check(f"{sc.name}: answer does not contain a forbidden item (business correctness)",
                          None if not have_answers else v.lower() not in low,
                          "mock provider echoes the prompt; semantic answer not evaluated" if not have_answers else
                          ("ok" if v.lower() not in low else "present"))
                check(f"{sc.name}: answer names the expected people (content, model-dependent)",
                      None if not have_answers else len(got) == len(sc.expect_restored),
                      "mock provider echoes the prompt; semantic answer not evaluated" if not have_answers else
                      f"{len(got)}/{len(sc.expect_restored)}")
                check(f"{sc.name}: C3 never returned", not any(v in restored for v in sc.never_returned),
                      f"{len(sc.never_returned)} checked")

            # --- multi-turn: declared subjects across turns (same contract, same conversation) ---
            mt = next(x for x in SCENARIOS if x.ner_misses)
            conv = "conv-" + os.urandom(6).hex()
            mt_raw = mt.raw + ["Imran", "Ansari"]
            turns = [("Summarise this ticket in two lines for the payroll lead.\n---\n" + NER_MISS, mt.subjects),
                     ("Draft a two-line reply to the requester confirming when the arrears will be paid.",
                      {"requester": mt.subjects["requester"]}),          # app no longer declares the others
                     ("Summarise the whole thread for the payroll lead in one line, naming who did what.",
                      {"requester": mt.subjects["requester"]})]
            history, mt_out, mt_ok = [], [], True
            for i, (ask, subj) in enumerate(turns, 1):
                history.append({"role": "user", "content": ask})
                n0 = len(cap.exchanges)
                t0 = time.perf_counter()
                r = post(client, mt.purpose, None, model=model, subjects=subj, messages=list(history), conv=conv)
                total = (time.perf_counter() - t0) * 1000
                ex = cap.exchanges[n0:]
                body = r.json()
                restored = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                outbound = "\n".join(e["outbound"] for e in ex)
                sent_text = outbound + "\n" + payload_strings(outbound)
                leaked = [v for v in mt_raw if v.lower() in sent_text.lower()]
                rec = {"scenario": f"M turn {i}", "status": r.status_code, "latency_ms": round(total, 1),
                       "gateway_decision": ledger_row(out, r.headers.get("X-PGW-Request-Id", "")),
                       "outbound_http": [e["outbound"] for e in ex], "inbound_http": [e.get("inbound") for e in ex],
                       "restored_response": restored}
                records.append(rec)
                (out / f"multiturn_{i}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
                check(f"M turn {i}: no raw value (incl. undeclared-this-turn names) in outbound",
                      bool(ex) and not leaked, f"status {r.status_code}, {len(leaked)} found")
                if r.status_code == 200:
                    left = [m.group(0) for m in ALIAS_BODY.finditer(restored)]
                    check(f"M turn {i}: restored answer has no aliases left", not left, f"{len(left)} left")
                mt_out.append(json.loads(ex[0]["outbound"]) if ex else None)
                mt_ok &= r.status_code == 200
                history.append({"role": "assistant", "content": restored or "(no answer)"})
            if mt_ok and all(mt_out):
                def first_user(o):
                    ms = o.get("messages", [])
                    u = next(m for m in ms if m.get("role") == "user")
                    c = u["content"]
                    return c if isinstance(c, str) else "".join(x.get("text", "") for x in c)
                firsts = [first_user(o) for o in mt_out]
                seqs = [ALIAS.findall(f) for f in firsts]
                check("M: alias stability (same aliases, same positions, for turn-1 text in turns 2 and 3)",
                      seqs[0] == seqs[1] == seqs[2] and bool(seqs[0]), f"{len(seqs[0])} aliases per turn")
                check("M: turn-1 text protected byte-identically in later turns (utility)",
                      firsts[0] == firsts[1] == firsts[2],
                      "identical" if firsts[0] == firsts[1] == firsts[2] else
                      "differs: a value detected in the model's reply was propagated into earlier text")
            db = (out / "restore_ledger.sqlite").read_bytes()
            import sqlite3
            n_rows = sqlite3.connect(str(out / "restore_ledger.sqlite")).execute(
                "SELECT COUNT(*) FROM restore_ledger WHERE conversation_id=?", (conv,)).fetchone()[0]
            check("M: restore ledger holds the conversation as keyed hashes only", n_rows > 0 and not any(
                v.encode() in db or v.lower().encode() in db for v in mt_raw), f"{n_rows} rows, no plaintext")
            r = post(client, mt.purpose, None, model=model, subjects=mt.subjects, messages=list(history))
            error_bodies.append(r.text)
            check("M: history without a conversation id is refused (fail closed)", r.status_code == 400
                  and r.json()["error"]["type"] == "conversation_id_required", f"status {r.status_code}")

            # --- provider failures (real endpoint) must fail closed with no content ---
            if args.provider != "mock":
                for label, upk in (("invalid API key", dict(key="invalid-pilot-check")),
                                   ("provider timeout", dict(key=key or "invalid", timeout=0.001))):
                    fcap = Capture()
                    fapp = create_app(make_settings(out, real_upstream(args.provider, fcap, upk["key"],
                                                                       upk.get("timeout", 60))))
                    with TestClient(fapp) as fc:
                        fr = post(fc, "ticket_summary", SCENARIOS[0].content, model=model,
                                  subjects=SCENARIOS[0].subjects)
                    error_bodies.append(fr.text)
                    check(f"provider failure ({label}) fails closed", fr.status_code == 502
                          and not any(v in fr.text for v in ALL_RAW) and "choices" not in fr.json(),
                          f"status {fr.status_code}")
            try:
                build_upstream(Settings(upstream_kind="anthropic", anthropic_base_url="https://evil.example"))
                check("non-allowlisted provider destination refused at startup", False)
            except ValueError:
                check("non-allowlisted provider destination refused at startup", True)

            # --- gateway failures must fail closed: nothing sent, no content returned ---
            import pgw.pipeline as P
            orig = P.Protector.protect

            def crash(self, *a, **k):
                raise RuntimeError("detector crashed on: " + json.dumps(a[0]))

            def leaky(self, *a, **k):
                prot = orig(self, *a, **k)
                prot.messages[-1]["content"] += " Imran Ansari ZXWPA3889P"
                return prot

            def slow(self, *a, **k):
                time.sleep(1.5)
                return orig(self, *a, **k)
            for label, fn, extra, want in (("detector crash", crash, {}, 503), ("egress invariant", leaky, {}, 500),
                                           ("protection deadline", slow, {"protect_timeout_s": 0.3}, 503)):
                gcap = Capture()
                gup = MockCapture(gcap)
                P.Protector.protect = fn
                try:
                    gapp = create_app(make_settings(out, gup, **extra))
                    with TestClient(gapp) as gc:
                        gr = post(gc, "ticket_summary", SCENARIOS[0].content, model="mock-1", subjects=SCENARIOS[0].subjects)
                finally:
                    P.Protector.protect = orig
                error_bodies.append(gr.text)
                check(f"gateway failure ({label}) fails closed", gr.status_code == want and not gup.received
                      and not any(v in gr.text for v in ALL_RAW), f"status {gr.status_code}, sent {len(gup.received)}")
            # declared-subject egress check: simulate the pipeline not applying declarations
            orig_protect = P.Protector.protect   # simulate a pipeline bug: declarations ignored by the protector
            P.Protector.protect = lambda self, messages, ctx, declared=None: orig_protect(self, messages, ctx)
            try:
                gup = MockCapture(Capture())
                with TestClient(create_app(make_settings(out, gup))) as gc:
                    a4 = next(x for x in SCENARIOS if x.ner_misses)
                    gr = post(gc, a4.purpose, a4.content, model="mock-1", subjects=a4.subjects)
            finally:
                P.Protector.protect = orig_protect
            error_bodies.append(gr.text)
            check("declared-subject egress violation fails closed", gr.status_code == 500 and not gup.received
                  and not any(v in gr.text for v in ALL_RAW), f"status {gr.status_code}, sent {len(gup.received)}")
            time.sleep(1.5)   # let the timed-out worker finish before scanning logs

    root.removeHandler(handler)
    logs = logbuf.getvalue()
    ledger = (out / "egress.jsonl").read_text()
    for where, text in (("logs", logs), ("stderr/traces", stderr.getvalue()), ("audit ledger", ledger),
                        ("error bodies", "\n".join(error_bodies))):
        hits = [v for v in ALL_RAW if v in text]
        check(f"no protected value in {where}", not hits, f"{len(ALL_RAW)} values, {len(hits)} found")
    (out / "logs.txt").write_text(logs)
    (out / "stderr.txt").write_text(stderr.getvalue())

    review = ["# Answers for human review (synthetic data; trusted environment only)", ""]
    for r in records:
        review += [f"## {r['scenario']} (HTTP {r['status']})", "", r.get("restored_response") or "(none)", "",
                   "- Useful? [ ]  Correct vs. the input facts? [ ]  Notes:", ""]
    (out / "human_review.md").write_text("\n".join(review))
    lat = [r["latency_ms"] for r in records if isinstance(r["latency_ms"], dict)]
    lines = [f"# Pilot validation (Path A declared subjects + Path B local compute): {mode}", "",
             f"- Model: `{model}` · data: synthetic only · captures: outside the repo",
             f"- Policy/contract integrity digest: `{next((r['gateway_decision'] or {}).get('integrity') for r in records if r.get('gateway_decision'))}`",
             f"- Gateway latency (ms, excl. provider), per scenario: {[x['gateway'] for x in lat]}",
             f"- Provider latency (ms): {[x['provider'] for x in lat]}", "",
             "| Check | Result | Note |", "|---|---|---|"]
    lines += [f"| {c} | {s} | {n} |" for c, s, n in checks]
    n_fail = sum(s == "FAIL" for _, s, _ in checks)
    n_block = sum(s == "BLOCKED" for _, s, _ in checks)
    lines += ["", f"**{sum(s == 'PASS' for _, s, _ in checks)} pass, {n_fail} fail, {n_block} blocked.**"]
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\ncaptures: {out}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
