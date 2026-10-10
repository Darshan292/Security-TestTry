# Product slice: HR helpdesk pilot (two paths, end to end)

Status updated 2026-10-02 after the hardening pass; see PILOT_READINESS.md for the current verdict.

- **Date:** 2026-10-02 · **Scope:** the thin vertical slice of PRODUCT_ARCHITECTURE.md. Synthetic data only; mock external LLM (no provider key in this environment).
- **Not a readiness claim:** the slice proves the **product flow**. The detection weaknesses in §C still make Path A unsafe for real HR data.
- **Run it:** `python -m demo.demo_slice` · tests: `pytest tests/test_product_slice.py` (full suite at the 2026-10-02 hardening pass: 1509 passed, 1 xfailed, 1 warning; the original slice had 23 tests in that file and a 1,422-test suite).

## What was built (product code)

| Piece | Where |
|---|---|
| App registration: every purpose bound to a data contract (task type, route). An uncontracted purpose stops startup, and a request for one is a 403 before the body is read | `registry/apps.yaml` (`hr-helpdesk-pilot`), `pgw/gateway/app.py: load_registry, load_contracts` |
| Data contracts, validated at startup (unsafe contract = gateway does not start) | `contracts/ticket_summary.yaml`, `contracts/salary_comparison.yaml`, `pgw/product/contracts.py` |
| C0–C4 data classes and routing (EXT_PSEUDO / LOCAL_COMPUTE; C4 content refused) | `pgw/product/contracts.py`, `pgw/product/content.py` |
| Redact unneeded C3 identifiers instead of blocking | Contract `C3: REDACT` mapped onto the existing pipeline |
| Scoped aliases, response restoration (only contract-listed classes), egress invariant | Existing `pgw/alias.py`, `pgw/stream.py`, `pgw/egress.py`; gateway wiring |
| Contract-declared values (protected without detection) | `pgw/pipeline.py: Protector.protect(..., declared=)` |
| LOCAL_COMPUTE with one real function (`salary_difference`, exact decimal arithmetic) | `pgw/product/local_compute.py` |
| External LLM adapter | Existing OpenAI/Anthropic/mock adapters (`pgw/gateway/upstream.py`); the slice runs on the mock |
| Content-free audit row with route and contract version | Egress ledger |
| **Added in the hardening pass:** two more Path A contracts (`hr_case_explanation`, `hr_email_draft`) and a second LOCAL_COMPUTE contract (`esi_letter`); a second local function, `esi_eligibility` | `contracts/*.yaml`, `pgw/product/local_compute.py` |
| **Added:** linear-time fixes for 3 regex DoS paths (`_DIGIT_CHAIN`, `_PGPASS`, person-cue `_PT`) | `pgw/normalize.py`, `pgw/detect/recognizers.py`, `tests/test_dos_bounds.py` |
| **Added:** input limits (body bytes, content chars, message count, per-request deadline, worker slots); silent `except: pass` in the stream path replaced by a CRITICAL `restore_ledger_record_failed` log | `pgw/gateway/settings.py`, `pgw/gateway/app.py`, `tests/test_gateway_hardening.py` |
| **Added:** existing local NER (`en_core_web_lg`) wired for contracted purposes; a model that fails to load stops startup (it is a secondary net, not full coverage; it can be switched off by env, which logs a warning); contracted purposes forced to buffered output | `pgw/gateway/app.py`, `pgw/detect/ner.py`, `tests/test_gateway_hardening.py` |
| **Added:** hash-locked requirements | `requirements/`, `INSTALL.md`, `tests/test_requirements_lock.py` |
| **Added:** egress destination allowlist in code (https, exact host, checked at startup) and in deploy configs (compose internal network, k8s default-deny). The network configs are written but **not exercised** | `pgw/gateway/settings.py`, `deploy/`, `tests/test_egress_allowlist.py` |
| **Added:** sealed, versioned registry, policy and contracts. Production requires an Ed25519 signature under the trusted key. The manifest digest and the mode are in each ledger row (SECURITY_BOUNDARY.md) | `policies/MANIFEST.json`, `pgw/product/integrity.py` |
| **Added:** real-provider validation harness. Run so far against the real endpoint without a valid key: outbound bytes and fail-closed behaviour checked; no real model answer seen yet | `scripts/pilot_validation.py`, `eval/results/pilot_validation_*.md` |

## Proven by tests (`tests/test_product_slice.py`), each reading the exact bytes the upstream received

| Property | Test |
|---|---|
| Raw C1/C3 values (name, employee ID, email, PAN, bank account) never in the outbound request; C0 (ticket ID, dates) unchanged | `test_path_a_raw_values_never_leave_and_high_ids_do_not_block` |
| Unneeded HIGH identifiers do not block: the same ticket is 422 under the legacy rule (development-only comparison) and 200 under the contract | `…high_ids_do_not_block`, `…blocked_by_the_legacy_presence_rule` |
| Aliases restored for the authorised app; C3 never returned; the gateway's own note never reads as an alias | `test_path_a_identities_restored_c3_never_returned` |
| Exact salaries (6 formats) never leave; the LLM gets aliased identities, an aliased derived difference and a derived percentage only | `test_path_b_exact_salaries_stay_inside_and_only_derived_result_is_sent` |
| Restoration puts names and the exact difference (₹2,70,000) into the answer; inputs never echoed | `test_path_b_answer_restores_names_and_exact_difference` |
| **Fail closed:** malformed payload 400; compute error 503; detector error 503; buggy compute that leaks a salary is caught by the egress invariant (500); upstream down 502; uncontracted purpose 403; non-Latin script 422; input without a source-sanitizer attestation 422. **Nothing is sent and no values are echoed** | the remaining tests |
| Unsafe contracts (C1 ALLOW, C3 ALIAS, C3 restore, unknown route, missing class, overriding an existing policy purpose) stop startup | `test_unsafe_contracts_…`, `test_contract_cannot_override_…` |

## Report

### A. What product capability now exists end to end
1. **A registered app can only use purposes it has a data contract for.** The contract fixes the route and the treatment of each data class, and unsafe contracts are rejected at startup.
2. **Path A (ticket summary):**
   - identities leave as scoped aliases;
   - bank and tax identifiers are **removed instead of blocking the whole request** (the E-UTIL failure mode that refused 30 % of tasks);
   - business content is untouched;
   - non-Latin-script content is refused;
   - the answer comes back with the authorised identities restored.
3. **Path B (salary comparison):** exact CTCs are used only inside the gateway, where `salary_difference` computes the result. The external model receives aliased identities, an aliased difference and a percentage. The app receives the restored names and the exact difference. The egress invariant independently proves the exact inputs did not leave.
4. **Every failure tested fails closed with no content in the response.** Each request leaves a content-free audit row with its route and contract version.

### B. What is still missing
- **No real model answer seen.** The harness exists and ran against the real endpoint only without a valid key (outbound bytes and fail-closed behaviour validated). Alias survival and restoration of a real answer are not validated.
- **No private/local LLM route.** C4 content is refused, not served.
- **Untested on the contract paths:** multi-turn (restore ledger). Contracted purposes are forced to buffered output, so incremental streaming is not used there.
- **Local-compute catalogue is two functions** (`salary_difference`, `esi_eligibility`), not derived from real ESU tasks.
- **NER is a secondary net, not complete coverage** (done: wired, see D). Names it misses still leave raw.
- **Network egress configs are not exercised** (the code allowlist is tested).
- **Still absent:** SQLite ledger (no PostgreSQL), single shared roster key (no per-tenant keys), no contract-change governance process beyond the signed manifest (who signs, and how changes are reviewed, is organisational). No tenant-specific contracts (one purpose means one contract).
- **No evaluation on real ESU tasks inside the boundary,** and no owner review of the E-UTIL cases yet.
- **Declared subjects for Path A: implemented** (`pgw/product/subjects.py`, `tests/test_declared_subjects.py`). Undeclared people still depend on detection.

Done since the original slice (pointer in brackets):
- ~~Contracts and policy unsigned/unversioned~~ sealed and versioned [`policies/MANIFEST.json`, `pgw/product/integrity.py`].
- ~~No dependency manifest or pinning~~ hash-locked [`requirements/`, `tests/test_requirements_lock.py`].
- ~~No egress NetworkPolicy~~ written, not exercised [`deploy/`].
- ~~NER not wired~~ wired for contracted purposes [`pgw/gateway/app.py`].

### C. Detection weaknesses that actually prevent this slice from being safe
- **Path A depends on detection; Path B mostly does not.**
  - Path B's sensitive values arrive as declared, structured fields and are never put into the prompt.
  - Only its free-text `instruction` field (≤ 500 characters) depends on detection.
- **Names of people outside the roster leave raw** (vendors, candidates, dependants, managers not in the roster). The local NER is now wired for contracted purposes but is only a secondary net: it misses names (and non-Latin or unusual forms). This is still the biggest gap for ticket text, and Path A still depends on detection for undeclared values.
- **Identifiers in unusual forms leave raw:** spoken or spelled digits, encodings, unseen layouts. R3 measured complete high-risk exposure in 31 % of documents for the current detectors, on unseen formats.
- **Salaries or DOBs written in unseen formats are not detected,** so the contract's `C2: GENERALIZE` never applies to them.
- **Over-masking, a utility cost rather than a leak:** non-personal amounts are typed as compensation. In the demo the TDS refund "Rs 4,500" was generalised, losing a fact the summary needed.
- **Availability risk (reduced):** the three known quadratic paths are fixed and input size/time/slot limits exist (`tests/test_dos_bounds.py`, `tests/test_gateway_hardening.py`). Other unknown slow paths cannot be ruled out.

### D. What should be solved next (in order)
1. **Declared subjects in the Path A contract: done.** The app sends requester, assignee, mentioned and other subjects as `pgw_subjects`; every written variant is aliased without detection, and a separate egress sweep with the same variant patterns fails closed (`pgw/product/subjects.py`, `tests/test_declared_subjects.py`). *Done:* the existing local NER is wired for the rest (`pgw/gateway/app.py`, `tests/test_gateway_hardening.py`); it does not give complete coverage.
2. ~~Remove the quadratic paths, add input size caps, fix the silent `except: pass`.~~ **Done** for the 3 known paths, the limits and the stream-path log (`tests/test_dos_bounds.py`, `tests/test_gateway_hardening.py`).
3. **Run both paths against the real provider with a valid key,** have a human review the outputs, and exercise multi-turn on contract paths. *Partly done:* harness exists; real endpoint run without a valid key only (`scripts/pilot_validation.py`).
4. **A private/local route endpoint for C4,** and a LOCAL-COMPUTE function catalogue taken from real ESU tasks (eligibility, arrears, band position). *Partly done:* `salary_difference` and `esi_eligibility` exist.
5. **Deployment basics.** *Done:* hash-locked requirements (`requirements/`); egress allowlist in code (`tests/test_egress_allowlist.py`); sealed, versioned contracts and policy (`policies/MANIFEST.json`). *Written, not exercised:* compose/k8s egress network configs (`deploy/`). *Remaining:* per-tenant keys, durable ledger, exercising the network configs.

### E. What should explicitly NOT be solved yet
- New detector families, corpora, benchmark cycles or PD-13 adoption.
- GLiNER or other ML upgrades, beyond wiring the NER that already exists.
- RAG, agents and the tool gateway; durable vault and KMS; governance console; multimodal.
- "Compute on placeholders"; incremental streaming for RESTRICTED; quasi-identifier risk scoring.
