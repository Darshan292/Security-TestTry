# Pilot Readiness Review: HR helpdesk privacy gateway

- **Date:** 2026-10-02.
- **Data:** synthetic HRIS data only. No real employee data was used anywhere.
- **Status:** not production-ready, and not yet authorised for real HR data.
- **Real provider:** Groq `openai/gpt-oss-120b`, through the OpenAI-compatible adapter (allowlisted host).
- **Evidence:** `eval/results/pilot_validation_groq.md` (content-free). Full captures stay outside the repo: exact outbound and inbound bytes, restored answers, ledger rows, latency, and contract and policy integrity digest.
- **Runs:** four real-provider runs of the full suite on the final code paths.
- **Tests:** 1,599 passing.

The suite has 8 workflows:
- A1 HR ticket summary;
- A2 HR case explanation;
- A3 HR email draft;
- A4 a ticket whose names the detectors miss;
- B1 salary comparison;
- B2 ESI eligibility;
- B3 pay-band position (the additional local-compute task);
- a 3-turn HR case conversation.

The private-route check was run separately, also against live processes.

## A. What is technically proven (real external LLM, real network path, synthetic data)

| # | Property | Result |
|---|---|---|
| 1 | Protected raw values never leave | 0 of 47 distinct protected values in any outbound byte, raw or JSON-decoded, in every run |
| 2 | Declared subjects stay protected where NER misses them | The detectors alone find 0 of 2 names in A4. After declaration, 0 leak. This held in all runs, including later turns where the app did not declare them again |
| 3 | Exact LOCAL_COMPUTE inputs never leave | B1 salaries, B2 wage, B3 CTC, percentage and headroom: 0 forms found. Only contract-declared results cross |
| 4 | Aliases survive the real model | 0 mangled aliases across all answers in all runs. The model reformatted freely (tables, bullets, Markdown) |
| 5 | Restoration works on realistic formatting | Every alias the model used came back as the real value, with 0 left unrestored, in every run |
| 6 | C3 never returns to the application | PAN, bank account, Aadhaar and UPI: never in a restored answer. The private route additionally masks long digit runs |
| 7 | Failures are fail-closed | Real provider: invalid key 502, timeout 502. Detector crash 503, deadline 503, pipeline leak 500, declared-subject leak 500, conversation without an ID 400. A non-allowlisted destination, unattested provider data terms, a tampered contract, a missing tenant key and a broken audit chain all refuse startup or the request. Nothing is sent in any failure case |
| 8 | No sensitive values in logs, stderr, ledger or error bodies | 47 values checked: 0 found |

Multi-turn (Step 2), 3 turns:
- Aliases are identical, in the same positions, across turns.
- No raw re-identification, including names the app stopped declaring after turn 1; they persist as keyed hashes in the conversation ledger.
- The restore ledger holds no plaintext.
- A ledger failure before sending fails closed.

Other proven items:
- **Private route (Step 3):** proven live with real processes. A Hindi ticket went only to the in-boundary endpoint (`PRIVATE_LOCAL`, 0 external calls). A Latin ticket on the same purpose went to Groq with values protected.
- **Network restriction (Step 4):** proven with real Docker containers.
  - The app container cannot reach `api.groq.com`, `api.anthropic.com` or `example.com`.
  - It cannot use a direct IP, 1.1.1.1, DNS via 8.8.8.8, or the host proxy.
  - The gateway container can reach the external network.
  - Result: 13/13 checks, run twice (`deploy/verify_network.sh`).
- **Operations (Step 5), implemented and unit-tested:**
  - per-tenant keys with no cross-tenant fallback;
  - key rotation with aliases pinned per conversation;
  - a hash-chained, fsync'd audit log (HMAC when a key file is configured) that verifies at startup;
  - CI workflow and CODEOWNERS;
  - provider data terms attested in config and recorded in each audit row;
  - contract ownership and change process in `OPERATIONS.md`.

**Defects the real model exposed, all fixed with regression tests:**
- C0 pay-band limits were being re-generalised in local-compute prompts, so the model received wrong figures.
- A keyless in-boundary endpoint got an illegal `Authorization` header.
- The C3 output gate missed an account number written in Hindi.

## B. What is only tested synthetically, or in-process

- **All data is synthetic:** a 3-person roster, a handful of tickets, a single tenant in the provider runs.
- **Load:** in-process, single host, mock upstream (`eval/results/load_test.md`).
  - NER off: about 45 req/s at concurrency 8.
  - NER on: about 12 req/s at concurrency 1. At concurrency 8, protection timed out and the worker slots saturated (0–44 of 200 requests succeeded across runs).
  - **NER-enabled concurrency is a real capacity problem today.**
- **Keys, rotation and audit chain:** tested by unit tests only. Not exercised in a real deployment, across restarts at scale, or through backup/restore.
- **CI workflow:** written, but it has never run on GitHub.
- **Private route model:** the endpoint ran its deterministic extractive stand-in, not an LLM. No model weights could be downloaded here (Hugging Face is blocked). A GGUF model plugs in via `PGW_PRIVATE_GGUF`, untested.

## C. What remains unproven

- **Human review of the answers has not been done.** `human_review.md` in each capture folder is the review sheet. My first-pass reading is not a substitute. It found:
  - A1, B1, B2, B3 (after the fix) and the multi-turn answers are correct.
  - A2 is correct but adds speculative causes.
  - A3 used the employee's own email and phone as HR's signature and left `[BANK_ACCOUNT]` markers in the draft.
  - A4 once lost the requester because the existing NER tags "Raised" as a person.
- **Model content varies between runs.** Whether a person is named at all differs, e.g. "you" instead of the name. In 2 of 4 runs a value the detectors found in the model's reply was propagated back into earlier turn text: a ticket ID became `[COMPENSATION …]`. That is over-masking, not a leak.
- **Kubernetes NetworkPolicy:** no cluster. Docker caveat: the host-side bridge IP is reachable from apps, so host services bound to 0.0.0.0 would be reachable too.
- **Undeclared people and identifiers still depend on detection.** Indirect identifiers cross by design (pronouns, roles, dates, leave balances); that needs a DPIA decision.
- **Groq's data terms for this account were not verified.** The run used a standard API key. The attestation mechanism exists; the contract does not.
- **Not done:** KMS/HSM, WORM audit shipping, multi-tenant runs, real ESU tasks.

## D. What can safely be demonstrated to an ESU architect (synthetic data only)

- The suite live against a real LLM, showing:
  - outbound bytes next to the app's input;
  - restored answers;
  - the audit row with contract version and integrity digest.
- A4 and the conversation: NER finds 0 of 2 names, the declaration protects both, and they stay protected in later turns.
- Path B: salaries and wages never leave; correct answers come back.
- The private route: a Hindi ticket stays in-boundary.
- The Docker bypass matrix.
- The fail-closed matrix, and the startup refusals.

**Do not claim:** answer quality without the human review, anonymisation, Kubernetes isolation, real local-LLM quality, or production readiness.

## E. Minimum required before an authorised, controlled HR-data pilot

1. **Human review** of the answers by HR/payroll staff, with sign-off on usefulness and correctness. Fix the A3 prompt/contract issues.
2. **DPIA and provider terms:** a DPIA covering the indirect identifiers that cross by design. Contracted zero-retention/no-training terms and region for the chosen provider, verified, not just attested.
3. **Capacity:** fix NER concurrency, e.g. a detection process pool, or fewer slots with queueing. Then re-run the load test on the target hardware.
4. **Deployment:** the real gateway image deployed on the target platform. On Kubernetes, the NetworkPolicy exercised. Host services bound only to loopback. Keys in KMS or at least a sealed secret store; audit shipped off-host. CI green on GitHub.
5. **The app must send declared subjects for every person it knows**, verified in integration tests against the real helpdesk app.
6. **Pilot scope:** a small named user group, a few low-risk purposes (ticket summary, case explanation), Latin-script tickets only unless a real local model is installed, daily audit-chain verification, and a kill switch.
