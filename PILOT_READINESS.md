# Pilot Readiness Review: HR helpdesk slice

- **Date:** 2026-10-02.
- **Scope:** the product slice after the production-engineering pass.
- **Data:** synthetic HRIS data only. No real employee data was used anywhere.
- **Verdict:** **not ready for real HR data.** The slice is ready to show to an ESU architect on synthetic data. The real-model run is **blocked**: this environment has no `ANTHROPIC_API_KEY`.

## What was done in this pass

| Item | Status | Evidence |
|---|---|---|
| Contract semantics: derived class **D**; per-output egress RAW / TRANSFORMED / INTERNAL (default INTERNAL); seven purpose-dependent attributes with a mandatory stance per contract | Done | `pgw/product/contracts.py`, `PRODUCT_ARCHITECTURE.md` §2, `tests/test_product_slice.py` |
| Quadratic/DoS paths | 3 fixed: `_DIGIT_CHAIN`, `_PGPASS`, and a third found in person cues `_PT`. Worst case at 20 KB went from 25.8 s to 0.2 s | `tests/test_dos_bounds.py` (15 shapes at 100 KB plus scaling) |
| Size, time and resource limits | Body bytes, content characters, message count, a per-request deadline, and worker slots. A timed-out worker keeps its slot until it really finishes | `tests/test_gateway_hardening.py` |
| Silent `except: pass` in the stream path | Replaced by a CRITICAL `restore_ledger_record_failed` log | same |
| Local NER (existing `en_core_web_lg`) on contracted purposes | Wired. A model that fails to load stops startup. An out-of-roster name leaks with NER off and is aliased with it on | same |
| RESTRICTED responses stay buffered | Enforced at startup for every contracted purpose; partial text is never released | same |
| Pinned, hash-locked dependencies | `requirements/*.txt` with `--require-hashes`; the spaCy model is pinned by hash by hand | `INSTALL.md`, `tests/test_requirements_lock.py` |
| Network egress configuration | **In code:** an exact https host allowlist, checked at startup, with the SDK base URL pinned so the env cannot redirect it. **In deploy config:** compose internal network and a k8s default-deny policy, written but **not exercised** | `deploy/`, `tests/test_egress_allowlist.py` |
| Policy/contract versioning and integrity | `policies/MANIFEST.json` holds sha256 and version per file, with an optional HMAC signature. Any drift stops startup. The manifest digest goes into every ledger row | `pgw/product/integrity.py` |
| Real-provider validation harness | `scripts/pilot_validation.py`. Captures stay outside the repo, and the summary is content-free | `eval/results/pilot_validation_*.md` |

- **Suite:** 1,509 passed, 1 xfailed.
- **Ruff:** nothing new; 16 pre-existing findings, down from 19.
- **Bandit:** clean on `pgw/gateway`, `pgw/product` and `scripts`.

### Validation results (5 tasks: ticket summary, case explanation, email draft, salary comparison, ESI letter)

| Property | Result | How |
|---|---|---|
| Raw protected values never leave | **PASS, against the real endpoint.** 45 values across 5 tasks; none found in the exact HTTP bytes sent to `api.anthropic.com`, raw or JSON-decoded | httpx request hook on the SDK client |
| Derived values obey their contract | **PASS.** Exact CTCs, the difference amount, the wage and the margin are absent. The difference and percentage cross only as aliases. "covered by ESI" and ₹21,000 cross as declared | same |
| Provider failures fail closed | **PASS, real endpoint.** Invalid key gives 502; a 1 ms timeout gives 502. No content is returned | real 401 and real timeout |
| Gateway failures fail closed | **PASS.** Detector crash 503, egress-invariant breach 500, deadline 503. Nothing sent in any case | fault injection |
| No sensitive data in logs, stderr/traces, ledger, error bodies | **PASS** (37 values) | root logger at DEBUG plus stderr capture |
| Aliases survive provider formatting | **BLOCKED** (no API key). The mock self-test passes, but that only proves the harness | — |
| Restoration of a real model answer | **BLOCKED** (no API key) | — |

**To unblock:** add `ANTHROPIC_API_KEY` in the environment settings, then run `python -m scripts.pilot_validation` (one command, about 5 requests).

## A. Security blockers (before any real HR data)

1. **Path A still leaks whatever detection misses.**
   - NER and the roster reduce this but do not close it. Unseen identifier formats, spelled or spoken digits, and names NER misses all leave raw.
   - R3 measured complete high-risk exposure in 31 % of unseen-format documents for the rule detectors.
   - Path B does not have this problem: its values are declared fields.
2. **Quasi-identifiers cross by design** and no detector covers them. Seen in the captured payloads:
   - pronouns ("his");
   - roles ("payroll lead");
   - exact dates;
   - ticket IDs;
   - leave balances (PSEUDONYMISED_RAW);
   - the ESI yes/no, which reveals a wage bound (at most ₹21,000).

   In a small team these re-identify a person. The aliases are pseudonymisation, not anonymisation. This needs a DPIA decision, not more code.
3. **`NOT_EXPECTED` is unenforced.** If an app puts medical or disciplinary detail into a ticket, only the names are aliased; the detail leaves.
4. **Network isolation is unproven.**
   - Only the in-process allowlist is tested.
   - The compose and k8s policies have never run.
   - The k8s CIDR is a placeholder.
   - An app with its own route to the internet bypasses the gateway entirely.
5. **Trust anchors are weak.**
   - The manifest is unsigned by default.
   - `registry/apps.yaml` (app keys, purpose-to-contract binding, `restore_output`, models) is **not sealed**.
   - There is one master key for all tenants.
   - The app keys in the registry are demo "change-me" values.

## B. Product blockers

1. **No real model answer has been seen yet.** Answer quality, alias survival and restoration on real output are all unverified.
2. **C4 content is refused, not served.** There is no private route, so Hindi, Kannada and other non-Latin tickets get a 422.
3. **Local compute is narrow.** There are 2 functions. The catalogue (arrears, band position, pro-rata) has not been taken from real ESU tasks.
4. **Over-masking.** Non-salary amounts are typed as compensation and generalised; the E-UTIL TDS example lost the ₹4,500 fact.
5. **Buffered output means waiting for the whole answer.** With adaptive thinking on Opus 5.5 that can take tens of seconds. This has not been measured.

## C. Operational blockers

1. **No CI.** `scripts/check.sh` runs only by hand. The installed venv was not built from the lock: `pip-audit` flags pip 24.0 and setuptools 79 in the venv, while the lock pins setuptools 84.
2. **Audit and storage are single-node files.** The audit trail is JSONL and the restore ledger is SQLite. There is no log shipping and no alerting on CRITICAL events (`egress_violation`, `restore_ledger_record_failed`).
3. **No capacity numbers.** NER (about 600 MB of RAM, CPU per request) and the 8-slot default have not been load-tested.
4. **No key lifecycle** (rotation, custody), and no defined owner or change process for resealing policy and contracts.
5. **Provider-side terms are not settled:** retention, zero-data-retention and region.

## D. What can safely be demonstrated to an ESU architect (synthetic data only)

- The five tasks end to end, showing the **exact bytes that reached the real provider endpoint** (`/home/user/pilot_captures/real-endpoint-nokey/`) next to what the app sent.
- Path B's guarantee: exact salaries and wages never exist in any outbound byte, and only aliased or declared-RAW derived facts cross.
- The fail-closed matrix:
  - provider down or bad key: 502;
  - detector crash: 503;
  - deadline: 503;
  - egress breach: 500;
  - uncontracted purpose: 403;
  - C4 content: 422;
  - oversize: 413.
- Startup refusals: a tampered contract or policy, an unsafe contract, an unsafe derived-output rule, a non-allowlisted provider, and an NER model that will not load.
- `python -m demo.demo_slice`, and the content-free audit row (contract version, route, integrity digest).

**Do not claim:** suitability for real HR data, answer quality, network isolation, or anonymisation.

## E. The single next capability for a controlled internal pilot

**Declared subjects for Path A.**
- The helpdesk app already knows who a ticket is about: requester, assignee, mentioned employees, with their HRIS IDs. It should send them as contract fields, as Path B does.
- The gateway then aliases those people and their identifiers wherever they appear, independent of detection.
- The egress invariant proves they did not leave.

This turns the largest Path A risk (blocker A1) from "detection must find it" into "the app declared it". It reuses code that already exists (`Protector.protect(..., declared=)`).

**Precondition (a step, not a capability):** run the real-provider validation with a key. Have a human review the five restored answers.
