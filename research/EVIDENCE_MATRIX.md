# Evidence Matrix

- **Date:** 2026-09-30 · **Scheme:** `research/PROVENANCE.md` · **Sources:** `research/SOURCE_REGISTER.md`
- Part A: our own measurements (`M-NN`, all FACT-M, reproducible from this repo).
- Part B: external claims (`C-NN`), each graded and linked to the decisions it supports or undermines.
- Part C: which claims our measurements confirm, contradict, or leave open.

## Part A — Measurements (FACT-M)

Code `a51c22d`; corpus seed 1337 (MANIFEST hashes). "Repro" = the command that regenerates the number.

| ID | Measurement | Value | Repro | Supports / refutes |
|---|---|---|---|---|
| M-01 | Character leak rate on sensitive spans | DEV 2.11 % · **TEST 2.56 %** (gate ≤ 1 %) | `eval.run_det` → FROZEN_*.json | Refutes N-4/leak gate being met |
| M-02 | TEST leak share by type | PERSON 1.96 % · SECRET 0.31 % · AADHAAR 0.20 % · PHONE 0.10 % · others 0 | analysis over FROZEN_TEST | Locates W1 and W3 |
| M-03 | Structured-type recall on TEST | BANK_ACCOUNT, CARD, COMPENSATION, DOB, EMAIL, EMPLOYEE_ID, IFSC, PAN, PHONE, UPI_ID = 1.000 (CI lower bounds 0.982–0.997) | FROZEN_TEST | Supports ADR-0005 D1/D2 for known shapes |
| M-04 | PERSON on TEST | recall 0.922 (0.912–0.931), precision 0.887 | FROZEN_TEST | Passes the P1 gate (0.90) but is the main leak |
| M-05 | AADHAAR on TEST | recall 0.918 (0.876–0.947): 19 misses in `nationalId` JSON and TSV columns | FROZEN_TEST + failure analysis | Refutes "context gating generalises" (W3) |
| M-06 | SECRET on TEST | recall 0.916 (0.880–0.942): 27 misses, all credentials inside `scheme://user:pass@host` | same | W3 |
| M-07 | External (non-roster) names | DEV: 1,160/1,160 PERSON misses are external; external recall 0.768 | analysis over DEV | Motivates A-17 NER (W1) |
| M-08 | Roster-name recall (EDM) | **1.000**, n = 5,766 (DEV 4,446 + TEST 1,320) | FROZEN_* `person_by_source` | Supports A-05 as the primary PERSON control |
| M-09 | Egress invariant on replayed traffic | TEST: **0 true catches, 8 false alarms**; DEV: 0 / 0 | `eval.replay_egress` | Confirms T-20; reveals W9 |
| M-10 | Documents sending ≥ 1 raw gold value | DEV 9.5 % (377/3,977) · **TEST 14.6 % (181/1,240)** | `eval.replay_egress` | Headline exposure |
| M-11 | False positives on look-alike negatives | DEV 45/750 items · **TEST 164/250 items** (PERSON 258, COMPENSATION 79) | FROZEN_* | Overfitting / precision (W8) |
| M-12 | Alias grammar collision before the fix | 166/3,977 DEV items had ordinary identifiers rewritten as `[unknown TYPE]` | analysis at `75d171a`; fixed in `a51c22d` | ADR-0002 amendment |
| M-13 | Evasion leak, TEST | spaced 38.0 % · obfuscated email 14.4 % · case 5.6 % · NBSP 5.6 % · zero-width 5.2 % · homoglyph 4.2 % | FROZEN_TEST adversarial blocks | W2; refutes "partly handled" |
| M-14 | Streaming round-trip, TEST | 1,213/1,240 exact; 26 failures from long-token alias splits (W5), 2 from gate context loss (W4) | FROZEN_TEST + stream-only replay | Refutes ADR-0008's "impossible by construction" |
| M-15 | Detection latency, TEST, CPU, detection only | p50 2.3 ms · p95 4.2 ms · p99 6.8 ms | FROZEN_TEST | Consistent with N-1 for the non-NER path; end-to-end unmeasured (Q-17) |
| M-16 | DEV leak before field-context detection (A-04) | 26.1 % (bank account recall 0.31, secret 0.70, compensation precision 0.61) | `eval.run_det --split dev` at `b510f12` | Supports A-04 (large effect) |
| M-17 | Unit/integration tests | 809 passed, 1 strict xfail (the documented no-NER gap) | `pytest tests` | — |
| M-18 | T21 restore-ledger scenario | Truncated-history follow-up does not leak; without the ledger it does | `tests/test_core.py::test_t21_*`, `tests/test_gateway.py` | Supports ADR-0010 (unit level only) |
| M-19 | Verhoeff pass rate on random 12-digit ids | ~12 % in generated negatives | `eval/synth/test_generate.py` | Corroborates KNOWN ~10 % (A-02) |
| M-20 | E-UTIL output quality | **not measured** | `eval.run_util` (needs an API key) | Q-02 open |

## Part B — External claims (C-NN)

Class per PROVENANCE.md. "→" = what the claim bears on. Grades come from SOURCE_REGISTER.md.

| ID | Claim | Class | Source(s) | → Bears on |
|---|---|---|---|---|
| C-01 | Cloudflare AI Gateway DLP buffers the **complete** provider response before inspection when response scanning is on; request-only DLP does not buffer | FACT-P | S-02 | ADR-0008 motivation; Q-12 |
| C-02 | Cloudflare DLP actions are pass/flag/block (no redaction); tool args/results are scanned only as text in the JSON body | FACT-P | S-02 | COMPETITIVE; ADR-0009 |
| C-03 | Cloudflare Guardrails on REST endpoints logs but does **not enforce** on streamed responses; on gateway endpoints it buffers and de-streams | FACT-P | S-03 | ADR-0008 |
| C-04 | SAP AI Core unmasking needs the full `MASKED_ENTITY` tag; split tags are moved whole into the next chunk; smaller chunks reduce filtering and unmasking accuracy | FACT-P | S-17 | ADR-0008 hold-back; consistent with W4 |
| C-05 | SAP restores pseudonymised placeholders inside `tool_calls` arguments and re-masks them on the way back in | FACT-P | S-18 | ADR-0008 / ADR-0009 tool args |
| C-06 | SAP masking "can't guarantee that all such information is identified"; 27 entity types, many locale-limited (person names English-only) | FACT-P | S-19 | DESIGN §9.1; Indian-name coverage |
| C-07 | Google Model Armor has streaming sanitize RPCs; its API has **no** tool-call field | FACT-P (absence for tool calls) | S-08 | COMPETITIVE; ADR-0009 |
| C-08 | AWS Bedrock Guardrails masking is one-way tags; async streaming cannot mask; tool interactions are unchecked by default | CLAIM-S | S-06, S-07, S-97 | ADR-0009; ADR-0008 |
| C-09 | Salesforce disables data masking for agents; masking caps context at 65,536 tokens | CLAIM-S | S-15, S-16 | Q-02 (utility cost is real enough that a major vendor turns masking off) |
| C-10 | Salesforce's audit trail stores original prompt, masked prompt and response | CLAIM-S | S-13, S-95 | ADR-0007 deliberately differs (content-free) |
| C-11 | Uber: inconsistent placeholders broke caching and RAG and reduced quality; added latency | CLAIM-S | S-100, S-101 | ADR-0002 consistency requirement |
| C-12 | Google SDP offers deterministic AES-SIV and FPE (FFX) reversible transforms with KMS-wrapped keys | CLAIM-S | S-10, S-11 | A-11; ADR-0003 Phase 3 |
| C-13 | Presidio "cannot guarantee" all sensitive information is found; the repo moved to `data-privacy-stack`, MIT | FACT-P | S-43, S-44 | DESIGN §9.1; Q-03 |
| C-14 | Presidio has no native async engine; NeMo wraps it to avoid blocking the event loop | CLAIM-S (+ FACT-P absence in PyPI description) | S-82, S-46 | Q-03 |
| C-15 | llm-guard archived 9 Jul 2026; guardrails_pii archived | FACT-P | S-48, S-49 | Reject both (S-01 recommended guardrails_pii) |
| C-16 | LiteLLM's Presidio guardrail fails **open** on analyzer error (#30728) | CLAIM-S | S-74 | ADR-0007 fail-closed matrix |
| C-17 | LiteLLM does not restore placeholders in `tool_calls` arguments (#31950) | CLAIM-S | S-73 | ADR-0008 tool-arg handling |
| C-18 | LiteLLM leaks values split across SSE chunks (#41611) and fixes it by buffering the whole stream (#41936) | **UNVERIFIED** (N; sources conflict) | S-75, S-76, S-77 | ADR-0008 cites it: **must not carry weight** |
| C-19 | knowledgator gliner-pii F1 75.5 / 76.8 / 81.0 / 83.3 (edge→large), Apache-2.0 | CLAIM-S | S-68, S-69 | Q-04 |
| C-20 | urchade gliner_multi_pii-v1 F1 ≈ 0.38 on a third-party benchmark (SPY) | CLAIM-S | S-88 | Q-04 (candidate likely weak) |
| C-21 | nvidia/gliner-PII is under the NVIDIA Open Model License (not Apache) | CLAIM-S | S-71 | Licence screen for Q-04 |
| C-22 | GLiNER CPU latency ≈ 125 ms median per ~500 B | CLAIM-S | S-87 | ADR-0005 detection cache; N-1 |
| C-23 | A Verhoeff check digit accepts ~10 % of random 12-digit strings | KNOWN | ENGINEERING_FACTS | A-02; Q-05 |
| C-24 | NIST SP 800-38G Rev.1 (draft) removes FF3/FF3-1 and keeps FF1 | CLAIM-S | S-37, S-36 | A-12 reject |
| C-25 | `cryptography` provides AES-SIV (`AESSIV`); latest 50.0.1, dual Apache/BSD licence | CLAIM-S (API) / FACT-P (version, licence) | S-62, S-63 | A-11 |
| C-26 | DPDP has no pseudonymisation carve-out; breach reporting to the Board within 72 h (Rule 7) | KNOWN / CLAIM-S | ENGINEERING_FACTS, S-42, S-108 | R-13; Q-13 |
| C-27 | Masking and pseudonymisation cause minimal quality loss on personal writing tasks (97–99 % masking); utility loss is task-dependent | CLAIM-S | S-90, S-91 | Q-02 |
| C-28 | OWASP LLM Top 10 (2025) lists Sensitive Information Disclosure and Excessive Agency | UNVERIFIED by us (N; widely KNOWN) | S-33, S-34 | T-12, ADR-0009 |
| C-29 | Microsoft Purview DSPM for AI detects, classifies and blocks; no evidence it masks prompts | CLAIM-S (absence) | S-21 | COMPETITIVE |
| C-30 | Skyflow: vault with policy-gated detokenisation | CLAIM-V | S-27 | ADR-0003 (vault deferred) |
| C-31 | Model Armor latency ≈ 50–200 ms same-region | CLAIM-S (anecdotal) | S-99 | Competitive latency context only |
| C-32 | The source report proposes AES-SIV/FPE tokens *in the prompt*, a vault at the core, OPA for policy, and Presidio + GLiNER as the detector base | FACT-P (about the report's content) | S-01 | ADR-0002, 0003, 0005, 0006 depart from it |

## Part C — What our measurements say about external claims

| External claim | Our measurement | Verdict |
|---|---|---|
| C-06, C-13: detection cannot be guaranteed | M-01, M-10: 2.56 % of characters and 14.6 % of documents leak on TEST | **Confirmed in our own system**; the limitation is universal, not vendor-specific |
| C-04: small stream chunks reduce accuracy (SAP) | M-14 / W4: our streaming gate misses context-dependent PII that the non-streamed gate catches | **Independently consistent**: bounded-context streaming inspection is structurally weaker |
| C-01, C-03: vendors buffer or stop enforcing on streams | M-14: our hold-back restores 1,213/1,240 exactly, with 2 defect classes | Our approach is more ambitious and **currently less safe than buffering** for context-dependent types (Q-12) |
| C-11: placeholders must be consistent across turns | ADR-0002 aliases are deterministic per scope (unit tests) | **Not measured with a real model** (Q-01) |
| C-23: Verhoeff ~10 % false accept | M-19: ~12 % in generated negatives | Consistent |
| C-22: NER costs ~100 ms+ per 500 B on CPU | M-15: the non-NER path is 2.3 ms p50 | The NER path will dominate latency; the ADR-0005 cache is required, not optional |
| C-27, C-09: utility cost | M-20: not measured | **Open (Q-02)**; the central gap named by the external review |
| C-32: Presidio + GLiNER as the base | M-08: EDM gives 1.000 on roster names without either; M-07: external names need ML | Partly supported: ML is needed **only** for names outside the roster; Presidio's role unproven (Q-03) |
| C-32: vault at the core | ADR-0003 + M-18: stateless per-request map plus a keyed-hash ledger closes T-21 without a plaintext vault | Our departure is supported at unit level; not load-tested |
