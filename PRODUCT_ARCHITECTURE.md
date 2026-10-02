# ESU Privacy-Preserving AI Gateway — Product Architecture

- **Status:** Proposed (2026-10-02). Supersedes DESIGN.md §1, §4 and §5 as the product description. ADR-0001…0010 remain the decision record; departures are marked.
- **Evidence base:** R1–R3 security evaluations, R4 architecture analysis, R4 E-UTIL (`eval/eutil/E_UTIL_REPORT.md`).
- **Nothing in this document is implemented yet unless the "Exists" column says so.** Since 2026-10-02, a thin vertical slice exists: app contracts, C0–C4 routing, redact-not-block, LOCAL_COMPUTE with one function, and fail-closed tests (PRODUCT_SLICE.md).
- **This is not a readiness claim.** The system has failed every held-out leakage gate so far (R1–R3) and must not see real HR data.

## 1. What the product is

A **privacy-preserving AI gateway**: the only path from ESU's HRIS/ERP applications (and later agents) to AI models. For each request it decides **where the work may be done** and **in what form data may travel**, then enforces that decision at the network boundary:

```
app request ──► authenticate + purpose ──► understand the content ──► ROUTE ──┬─► external LLM (pseudonymised) ──► response gate ──► restore ──► app
                                                                             ├─► local computation (exact values never leave)
                                                                             ├─► private / in-boundary LLM
                                                                             └─► refuse (with reason)
```

It is **not** a PII detector. Detection is one input to a routing and transformation decision. The goal is that **raw sensitive values do not cross the boundary even when their representation, format, field name, language or encoding is unknown**. Where that cannot be guaranteed, the work does not go outside.

## 2. The product contract

*What may cross the boundary, for what purpose, and what stays inside.* Every rule cites its evidence. Each purpose has a versioned data contract (`contracts/*.yaml`, loaded by `pgw/product/contracts.py`) that fixes the route, the action per class and, for LOCAL-COMPUTE, the egress of every computed output. An unsafe or incomplete contract stops the gateway from starting.

| Data class | Examples | May cross to an external LLM? | Evidence |
|---|---|---|---|
| **C0 Non-personal business content** | Instructions, policy text, ticket text minus personal values, counts, policy thresholds, non-personal dates, grades, project names | **Yes, raw.** Masking it destroys reasoning | E-UTIL: MAX masked 133 task-critical values and lost case explanation and Q&A; the gateway mis-masked a ₹21,000 policy threshold |
| **C1 Identity values** | Person names, employee/vendor IDs, work emails, phones | **Only as scoped aliases**, restored on return for authorised apps | E-UTIL: 100 % restored, 0 fabricated, no loss on classification, summary or drafting |
| **C2 Personal quantities and attributes** | Salary/CTC, DOB/age | **Never as exact values.** Generalised (band, decade, month/year) only where the purpose allows. **Computations on them run locally**. Contextual attributes are listed separately below | E-UTIL: comparison, eligibility and report tasks fail on placeholders and bands |
| **C3 Government, financial and credential identifiers** | Aadhaar, PAN, bank account, card, UPI, passport/UAN, secrets | **Never.** Removed when the task does not need them (the request proceeds); if the task needs them, local or private route, or refused | E-UTIL: blocking whenever present refused 12/40 useful tasks; DESIGN §5.2 rationale kept |
| **C4 Content the gateway cannot reliably understand** | Non-Latin-script personal text, speech transcripts, scans/OCR, undecodable encodings, under RESTRICTED | **No.** Private/in-boundary LLM, or refused (this slice: refused) | R3: 61 % of residual exposure is names in such content; E-UTIL: Devanagari names left raw |
| **D Derived** | Values computed inside the gateway by LOCAL-COMPUTE: a pay difference, a percentage, an eligibility result, a margin | **Per output**, by the egress rule below. **A derived value is not assumed non-sensitive** | E-UTIL: comparison and eligibility tasks need the result, not the inputs |
| **Bulk export of C1–C3** | "All PANs and bank accounts as CSV" | **Refused.** It is a database query, not language work | E-UTIL extraction-02 |

### Egress rule for every LOCAL-COMPUTE output (class D)

Each contract declares, per output, one rule. An output the contract does not list is INTERNAL.

| Egress | Meaning | Condition |
|---|---|---|
| **RAW** | Leaves as is | Output derived from C0; from any other class only with a written justification of at least 20 characters |
| **TRANSFORMED** | Leaves only as a scoped alias; restored to the app only if the contract restores class D | None |
| **INTERNAL** | Never leaves. Default. The egress invariant withholds every surface form (plain, grouped, Indian-grouped, with or without ₹) | None |

Outputs derived from C3 or C4 can never leave (startup error otherwise). A prompt template may not reference INTERNAL or undeclared outputs (startup error).

### C2 purpose-dependent attributes

C2 values (compensation, DOB) are never sent exact. Some attributes cannot be detected reliably and are personal or not depending on the purpose: **leave_balance, attendance, performance_rating, health_or_leave_reason, disciplinary_outcome, eligibility_result, derived_compensation**. Because detection cannot decide, **every contract MUST state a stance for each**; a missing or invalid stance stops startup.

| Stance | Meaning |
|---|---|
| **PSEUDONYMISED_RAW** | May cross raw, only next to aliased identities. Still personal data |
| **NOT_EXPECTED** | The app must not send it for this purpose. An app obligation; not enforced by detection |
| **LOCAL_ONLY** | Only via LOCAL-COMPUTE inputs and outputs, under the egress rules |

Stances chosen by the shipped contracts:

| Attribute | ticket_summary v2 | salary_comparison v2 | esi_letter v1 |
|---|---|---|---|
| leave_balance | PSEUDONYMISED_RAW | NOT_EXPECTED | NOT_EXPECTED |
| attendance | PSEUDONYMISED_RAW | NOT_EXPECTED | NOT_EXPECTED |
| performance_rating | NOT_EXPECTED | NOT_EXPECTED | NOT_EXPECTED |
| health_or_leave_reason | NOT_EXPECTED | NOT_EXPECTED | NOT_EXPECTED |
| disciplinary_outcome | NOT_EXPECTED | NOT_EXPECTED | NOT_EXPECTED |
| eligibility_result | PSEUDONYMISED_RAW | NOT_EXPECTED | LOCAL_ONLY |
| derived_compensation | NOT_EXPECTED | LOCAL_ONLY | LOCAL_ONLY |

**Corrected (2026-10-02):** an earlier version of this section listed leave balances as C0 (non-personal). An employee-specific leave balance is personal data. In ticket_summary it is PSEUDONYMISED_RAW: it crosses only next to aliased identities. Generic policy figures (for example an annual entitlement for all staff) remain C0.

### Worked examples

| Contract | Output | Egress | Why |
|---|---|---|---|
| salary_comparison | difference, pct_of_lower | TRANSFORMED | Aliased; the model never sees the amount; restored for the HR manager (contract restores C1, D) |
| salary_comparison | difference_value, higher_ctc, lower_ctc | INTERNAL | Exact CTCs and the numeric difference never leave |
| esi_letter | eligibility ("covered by ESI" / "not covered") | RAW, with justification | The letter must state it; only a yes/no next to an aliased identity crosses |
| esi_letter | threshold (₹21,000) | RAW | Derived from C0: the public statutory ceiling |
| esi_letter | monthly_gross, margin | INTERNAL | The wage and its distance from the ceiling stay inside |

### Versioning and integrity

Contracts and the policy pack are versioned and sealed in `policies/MANIFEST.json` (sha256 and version per file, optional HMAC signature). The gateway refuses to start on any unreviewed change. After a reviewed change, reseal with `python -m pgw.product.integrity seal`.

### Task routing (declared by the app per purpose, enforced by the gateway; extends ADR-0004)

| Route | When | E-UTIL share | Typical ESU tasks |
|---|---|---|---|
| **EXT-PSEUDO** | The answer needs language over C0 plus identities as aliases | 26 / 40 | Classification, summarisation, drafting, case explanation, policy Q&A |
| **LOCAL-COMPUTE** | The answer needs arithmetic, thresholds or format conversion on C2/C3 values | 9 / 40 | Comparisons, eligibility, arrears, band analytics, payslip maths |
| **PRIVATE-LLM** | The substance is C2–C4 in content the gateway cannot transform safely | 4 / 40 | Extraction of personal fields from free text or forms, non-Latin documents, KYC |
| **REFUSE** | Not language work, or forbidden by purpose | 1 / 40 + a rule | Bulk identifier export; anything that would send a credential |

**Routing is decided by purpose and contract first, and by content second.** The app declares the task type and the fields it sends (§3-E). The content analysis can only make the route **stricter**: from EXT-PSEUDO to LOCAL-COMPUTE or PRIVATE-LLM, or to REFUSE. It can never make it more permissive.

## 3. Architecture

| # | Layer | Does | Det./ML · location | Main failure and attack modes | Latency | Evidence | Exists today |
|---|---|---|---|---|---|---|---|
| A | **Canonicalisation** | One linear-time pass: Unicode (NFKC, confusables, bidi/zero-width removal, native digits); bounded decoders over the whole document (HTML, URL, QP, MIME words, JSON strings, base64 depth ≤ 2, mojibake repair); reversal and unwrap views. Offset maps throughout; size and time budgets **fail closed** | Deterministic · local | Decode bombs, nesting (caps); offset-map bugs (property tests); quadratic paths (forbidden by design) | ms/KB, linear | R4 §5: canonicalisation ceilings of 96–100 % on 9 evasion kinds; R4 §4 quadratic DoS in today's D0 | Partly (`normalize.py`, with quadratic `_join_digits`) |
| B | **Structure reconstruction** | Standard parsers into a record graph (JSON, XML, delimited, kv, YAML-like, fixed-width, markup to text). Keep every parse on ambiguity. **No corpus-tuned heuristics** | Deterministic · local | Ambiguous layouts; mis-segmentation (C must not depend on boundaries) | ~1.5 ms/KB | R3 structured-family gains; PD-13 heuristic overfit (gap 4.2×) | Experimental only (`pgw/pd13/records.py`) |
| C | **Candidate detection** | Value-like candidates independent of field names: checksums/validators; value shapes (dates in all standard encodings, amounts, long digits, entropy); spoken, spelled and worded numerals; roster exact match; **local NER once, on prose only** | Deterministic + local ML (NER) | Unknown encodings (go to C4 routing); NER misses in non-Latin script or speech (C4 routing, not more NER) | Validators ~2 ms/KB; NER ~17 ms/KB, prose only | Validators generalise (M-33); unseen-key exposure 40.6 % → 6.7 % with shapes (M-38); 290 unshaped values (M-43) | D2 validators, roster EDM, NER module (**NER not wired into the gateway**) |
| D | **Entity/value resolution** | Merge candidates across views and occurrences; type and confidence; link values to persons and records (graded anchoring); classify each value into C0–C4; propagation; restore-ledger matching | Deterministic · local | Wrong anchoring (over- or under-masking); type confusion (C2 vs C0 numbers) | ms | R3 precision collapse (M-37); E-UTIL policy threshold mis-typed | Propagation + restore ledger exist |
| E | **Policy and routing** | Signed decision tables over (tenant, app, purpose, task type, data class, confidence, content class). **Data contracts**: apps declare fields and task type; undeclared value-like content defaults to transform. Chooses the route (§2) and the per-value action (ALLOW / ALIAS / GENERALISE / REDACT / LOCAL / REFUSE) | Deterministic · local | Misconfiguration (simulator + policy tests, ADR-0006); contract drift (validate payloads) | < 1 ms | E-UTIL routing classes; blocking-by-presence cost | Policy tables exist (unsigned); no routing, no contracts |
| F | **Transformation** | Scoped HMAC aliases (ADR-0002), generalisation, redaction; per-request restore map; `never_restore` | Deterministic · local | Alias collisions (amended grammar); the model mangling aliases | ms | E-UTIL: 0 unrestored, 0 fabricated | **Exists** |
| G | **Egress enforcement** | Canonical serialisation; invariant sweep **on layer-A-canonicalised bytes**; content-free ledger; network egress only via the gateway | Deterministic · local | A bug-catcher only, not recall (M-09); availability (false alarms) | Linear, ms | M-09, M-26, M-27 | Exists (sweep not canonicalised; NetworkPolicy not built) |
| H | **Response gate and restoration** | Output gate on aliased text (fabricated aliases, raw values, roster hits); buffered for RESTRICTED; restore for authorised apps; record restored values in the restore ledger (ADR-0010) | Deterministic · local | Incremental streaming leaks (M-25, so RESTRICTED stays buffered); the model inferring sensitive facts (DESIGN §9.4) | ms | E-UTIL restore 100 %; R2 streaming FAIL | **Exists** (buffered); ledger is SQLite |
| I | **Local computation and private routing** | (i) Local compute service: deterministic functions over exact values (differences, eligibility, aggregates, format conversion) whose results feed the external prompt or the final answer. (ii) Private LLM endpoint inside the boundary (DENY_ROUTE_LOCAL target) for PRIVATE-LLM tasks | Deterministic (i) · local ML (ii) · local | (i) Wrong function or contract; (ii) capacity and quality of the local model | (i) ms; (ii) model-dependent | E-UTIL: 9 LOCAL-COMPUTE, 4 PRIVATE-LLM tasks | **Does not exist** |

## 4. Component decisions

### Delete (do not improve)

| Component | Where | Why |
|---|---|---|
| D1 field-context detection (key, keyword and concept lists, structural escalation) | `pgw/detect/recognizers.py` (`_field_scan` and related, most of its 2,180 lines) | Vocabulary overfitting: value halves dev→test (M-33); A's exposure doubles on unseen keys (M-38) |
| Person-cue regexes | `pgw/pipeline.py: detect_person_cues` | Marginal recall, high FP (M-33) |
| D0 view × detector cross-product (`despaced`, `digits_joined`, `casefold_titled` each running every detector and NER) | `pgw/normalize.py: views`, `pipeline.detect_text` | 3× NER cost; quadratic `_join_digits` DoS (M-42); replaced by layer A |
| PD-13 experiment package (L3 dictionary typing, date-column guessing, tuned heuristics, binary scope) | `pgw/pd13/` | Not adopted; L3 typing 19 % precision on unseen families (M-39). Ideas move to B/C/D as clean re-implementations |
| Measurement switches tied to deleted layers | `pgw/ablation.py` | No purpose once D1 is gone |
| "Block whenever a HIGH identifier appears anywhere" | `policies/hris-default.yaml` defaults | Refused 12/40 useful tasks; replaced by redact-if-unneeded plus routing |

### Keep (core of the product)

| Component | Where | Change needed |
|---|---|---|
| App registry, purpose declaration, authentication | `pgw/gateway/app.py`, `registry/apps.yaml` | Extend with task type, data contract and route |
| Validators (Verhoeff, Luhn, PAN, IFSC, UPI, email, phone, secrets catalogue) | `pgw/detect/recognizers.py` (D2 part) | Extract from the D1 code; ensure linear time |
| Roster exact match (keyed hash) and restore ledger | `pgw/detect/edm.py` | Per-tenant keys (T-19); PostgreSQL ledger |
| Scoped aliases | `pgw/alias.py` | None (E-FMT still unmeasured with a real provider) |
| Policy tables | `pgw/policy.py`, `policies/` | Add classes C0–C4, routes, signing (ADR-0006) |
| Propagation, protect/restore pipeline | `pgw/pipeline.py` | Restructure around layers A–I |
| Egress invariant, ledger, fail-closed matrix | `pgw/egress.py` | Sweep canonicalised bytes |
| Response gate and buffered restorer | `pgw/stream.py` | Incremental mode stays off for RESTRICTED |
| Local NER | `pgw/detect/ner.py` | **Wire it into the gateway** (it is not today), prose only, size-capped |
| Upstream adapters, mock LLM, demo | `pgw/gateway/upstream.py`, `demo/` | None |
| Evaluation method (pre-registration, sealing, one-shot locks) | `eval/`, `EVALUATION_PLAN.md` | Reuse for the product-level tests below |

### Future capabilities (not now)

| Capability | Phase | Precondition |
|---|---|---|
| Layer A decoders and reconstruction (spoken and worded numerals, document-level decoding) | Next build | Owner approval of this architecture |
| Data contracts plus task router (layer E) | Next build | Contract format agreed with app owners |
| Local compute service (layer I-i) | Next build | Function catalogue from real ESU tasks |
| Private LLM endpoint (layer I-ii) | After infra decision | In-boundary GPU or CPU model; quality measured with E-UTIL |
| "Compute on placeholders": the external model writes the computation over aliases and the gateway evaluates it locally | Research spike | Must be shown safe (no value disclosure through results) before use |
| Durable mapping, KMS, re-identification API | Phase 3 (ROADMAP) | Unchanged |
| RAG enforcement, tool/agent gateway, governance console | Phases 4–5 | Unchanged; ADR-0009 boundaries apply |

## 5. How each layer is validated

The aim is product validation, not more leakage benchmarks.

| Claim | Test | Status |
|---|---|---|
| Useful tasks still work | E-UTIL by route (this study, then on real ESU task samples inside the boundary) | First run done (synthetic, stand-in model) |
| Nothing exact leaves for C2/C3 | Canary values plus the egress sweep on every route; E-EGRESS-FUZZ | Not run |
| Routing is correct | Policy simulator tests per app contract | Not built |
| Restoration is faithful and authorised | E-FMT with the real provider; restore-ledger multi-turn (E-STREAM-T21) | Not run |
| Bounded latency and no DoS | E-PERF end to end through the gateway; pathological-input suite (long digit runs, long tokens, nested encodings) | Not run (quadratic paths known) |

## 6. Known gaps in what exists today (not to be hidden)

1. **The shipped gateway has no NER.** Names outside the roster leave raw (`demo/demo_gateway.py`, scene 2).
2. **HIGH-identifier presence blocks whole requests** (30 % of E-UTIL tasks).
3. **Non-personal numbers can be typed as COMPENSATION and masked or generalised** (policy threshold case).
4. **Quadratic-time paths in D0/D2** let any caller stall the gateway (M-42).
5. **Incremental streaming leaks;** RESTRICTED is buffered (M-25).
6. **No local compute, no private route, no data contracts, no policy signing, no network egress policy, single shared roster key.**
7. **Detection leakage on unseen families:** CSVE 0.53 (current) and 0.36 (PD-13). Readiness gates are unmet.
