# Product slice: HR helpdesk pilot (two paths, end to end)

- **Date:** 2026-10-02 · **Scope:** the thin vertical slice of PRODUCT_ARCHITECTURE.md. Synthetic data only; mock external LLM (no provider key in this environment).
- **Not a readiness claim:** the slice proves the **product flow**. The detection weaknesses in §C still make Path A unsafe for real HR data.
- **Run it:** `python -m demo.demo_slice` · tests: `pytest tests/test_product_slice.py` (23 tests; full suite 1,422 passed, 1 xfailed).

## What was built (product code)

| Piece | Where |
|---|---|
| App registration: purposes bound to data contracts (task type, route); uncontracted purposes refused | `registry/apps.yaml` (`hr-helpdesk-pilot`), `pgw/gateway/app.py: load_registry, load_contracts` |
| Data contracts, validated at startup (unsafe contract = gateway does not start) | `contracts/ticket_summary.yaml`, `contracts/salary_comparison.yaml`, `pgw/product/contracts.py` |
| C0–C4 data classes and routing (EXT_PSEUDO / LOCAL_COMPUTE; C4 content refused) | `pgw/product/contracts.py`, `pgw/product/content.py` |
| Redact unneeded C3 identifiers instead of blocking | Contract `C3: REDACT` mapped onto the existing pipeline |
| Scoped aliases, response restoration (only contract-listed classes), egress invariant | Existing `pgw/alias.py`, `pgw/stream.py`, `pgw/egress.py`; gateway wiring |
| Contract-declared values (protected without detection) | `pgw/pipeline.py: Protector.protect(..., declared=)` |
| LOCAL_COMPUTE with one real function (`salary_difference`, exact decimal arithmetic) | `pgw/product/local_compute.py` |
| External LLM adapter | Existing OpenAI/Anthropic/mock adapters (`pgw/gateway/upstream.py`); the slice runs on the mock |
| Content-free audit row with route and contract version | Egress ledger |

## Proven by tests (`tests/test_product_slice.py`), each reading the exact bytes the upstream received

| Property | Test |
|---|---|
| Raw C1/C3 values (name, employee ID, email, PAN, bank account) never in the outbound request; C0 (ticket ID, dates) unchanged | `test_path_a_raw_values_never_leave_and_high_ids_do_not_block` |
| Unneeded HIGH identifiers do not block: the same ticket is 422 under the legacy rule and 200 under the contract | `…high_ids_do_not_block`, `…blocked_by_the_legacy_presence_rule` |
| Aliases restored for the authorised app; C3 never returned; the gateway's own note never reads as an alias | `test_path_a_identities_restored_c3_never_returned` |
| Exact salaries (6 formats) never leave; the LLM gets aliased identities, an aliased derived difference and a derived percentage only | `test_path_b_exact_salaries_stay_inside_and_only_derived_result_is_sent` |
| Restoration puts names and the exact difference (₹2,70,000) into the answer; inputs never echoed | `test_path_b_answer_restores_names_and_exact_difference` |
| **Fail closed:** malformed payload 400; compute error 503; detector error 503; buggy compute that leaks a salary is caught by the egress invariant (500); upstream down 502; uncontracted purpose 403; non-Latin script 422. **Nothing is sent and no values are echoed** | the remaining tests |
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
- **No real external provider run.** There is no API key here; adapters exist but only the mock was exercised.
- **No private/local LLM route.** C4 content is refused, not served.
- **Untested on the contract paths:** streaming and multi-turn (restore ledger).
- **Only one local-compute function.** There is no catalogue derived from real ESU tasks.
- **NER is still not wired into the gateway.**
- **Contracts and policy are unsigned;** there is no contract-change governance.
- **Deployment basics are absent:** no dependency manifest or pinning, no egress NetworkPolicy, SQLite ledger, single shared roster key.
- **No evaluation on real ESU tasks inside the boundary,** and no owner review of the E-UTIL cases yet.

### C. Detection weaknesses that actually prevent this slice from being safe
- **Path A depends on detection; Path B mostly does not.**
  - Path B's sensitive values arrive as declared, structured fields and are never put into the prompt.
  - Only its free-text `instruction` field (≤ 500 characters) depends on detection.
- **Names of people outside the roster leave raw** (vendors, candidates, dependants, managers not in the roster). There is no NER in the gateway. This is the biggest gap for ticket text.
- **Identifiers in unusual forms leave raw:** spoken or spelled digits, encodings, unseen layouts. R3 measured complete high-risk exposure in 31 % of documents for the current detectors, on unseen formats.
- **Salaries or DOBs written in unseen formats are not detected,** so the contract's `C2: GENERALIZE` never applies to them.
- **Over-masking, a utility cost rather than a leak:** non-personal amounts are typed as compensation. In the demo the TDS refund "Rs 4,500" was generalised, losing a fact the summary needed.
- **Availability risk:** the quadratic-time paths in D0/D2 let any caller stall the gateway.

### D. What should be solved next (in order)
1. **Declared subjects in the Path A contract.** The helpdesk app knows who a ticket is about and who is involved. Send them as declared fields so their names and IDs are protected without detection, and wire the existing local NER (prose only, size-capped) for the rest.
2. **Remove the two quadratic paths, add input size caps, and fix the silent `except: pass` in the stream path.**
3. **Run both paths against the real provider** (API key), have a human review the outputs, and exercise streaming and multi-turn on contract paths.
4. **A private/local route endpoint for C4,** and a short LOCAL-COMPUTE function catalogue taken from real ESU tasks (eligibility, arrears, band position).
5. **Deployment basics:** pinned dependency manifest, egress NetworkPolicy, signed contracts and policy, per-tenant keys.

### E. What should explicitly NOT be solved yet
- New detector families, corpora, benchmark cycles or PD-13 adoption.
- GLiNER or other ML upgrades, beyond wiring the NER that already exists.
- RAG, agents and the tool gateway; durable vault and KMS; governance console; multimodal.
- "Compute on placeholders"; incremental streaming for RESTRICTED; quasi-identifier risk scoring.
