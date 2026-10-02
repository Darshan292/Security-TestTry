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
| M-21 | R2 test2 character leak, Config A / B | 8.24 % / **6.10 %** (gate ≤ 1 %; dev2 0.58 %) | FROZEN_R2_TEST2_4a6a75e.json | Refutes ADR-0005 generalisation; W-10 |
| M-22 | R2 test2 document exposure, A / B | 30.1 % / **20.1 %** | same | Headline exposure R2 |
| M-23 | R2 test2 per-type recall (A=B except PERSON) | AADHAAR 0.789, DOB 0.756, SECRET 0.848, COMPENSATION 0.927, BANK 0.940, others 1.000; PERSON 0.828 / 0.943 | same | W-10, W-13 |
| M-24 | ner_test2 external-name recall A / B / C | 0.133 / 0.794 / 0.844 (C = trf, reference) | same | Supports PD-4 (NER) and refutes cue generalisation |
| M-25 | E-STREAM-LEAK on test2 | 9/1,755 docs differ, 5 stream-only fragments: **FAIL** | same | ADR-0008 amendment: RESTRICTED buffered |
| M-26 | Egress invariant, clean test2 | 0 fires, 0 false alarms (R1: 8) | same | Confirms W-9 fix; ADR-0007 |
| M-27 | Held-out adversarial leak, B | line-wrap 70.6 %, HTML entity 52.4 %, OCR 40.5 %, leetspeak 24.2 %, spoken email 10.2 % | same | W-11 |
| M-28 | Known R1 evasions on test2, B | 5.8–6.9 % except case 10.2 % (≈ baseline 6.1 %) | same | Supports D0 views (A-01) |
| M-29 | Detection latency, test2 | A p95 9.2 ms · B p95 47 ms, p99 132 ms | same | Meets N-1 |
| M-30 | Overfit gap dev2 → test2 | char leak A 0.58 % → 8.24 %; rules-only external names 0.992 → 0.133 | dev2 runs + FROZEN_R2 | W-10: rule tuning does not generalise |
| M-31 | Root-cause attribution of test2 leak (B, 13,119 chars) | manual: schema 32.4 %, structure 20.2 %, prose context 16.7 %, external PERSON 15.8 %, parser bugs 14.8 %, lexical 0.2 %; automatic: 28.7 / 18.5 / 27.4 / 16.8 / 8.3 (partial) / 0.2 | `eval.root_cause` → `root_cause_r2.json` | ROOT_CAUSE_ANALYSIS_R2 §A; context-typing failure 69–75 % under both methods |
| M-32 | Attribution method agreement and concentration | methods agree on 51.9 % of leaked chars; 8 of 47 template×type cells = 53 % of leak | same | Only coarse categories are reliable |
| M-33 | Component ablation, marginal leak pp (dev2 / test2 / ner_test2) | D1 +16.6 / +8.0 / +0.1; D2 +15.2 / +23.7 / +32.0; D4 +0.1 / +2.1 / +38.6; D3 +1.7 / +3.4 / 0; D0 ≈ 0 clean, spaced 5.8 → 37.3 % | `eval.run_ablation` → `ablation_r2.md` | D1 overfits; validators and NER generalise (PD-13) |
| M-34 | Minimal configurations, test2 | ENTITY_ONLY 56.9 % leak / 74.3 % docs; RULES_ONLY 16.3 % / 44.8 %; D4_ONLY 88.7 % | same | Entity-centric alone cannot replace rules |
| M-35 | Context-free shape ceiling and cost | 61.9 % of leaked chars shape-flaggable (82.8 % schema, 83.3 % structure, 80.9 % prose, 0 % PERSON, 0 % COMPENSATION); dev2 cost: DATE hits 210/420 negatives, entropy 84, Verhoeff 24 | `eval.root_cause` (`shape_ceiling`, `shape_cost_dev`) | Context needed for precision, not recall (PD-13); refutes "mask every date" as-is (PD-10) |
| M-36 | R3 test3 (18 unseen families) CSVE, A / B / C | 0.527 / **0.361** / 0.382; CSVE-HIGH 0.312 / 0.110 / 0.114; char leak 0.241 / 0.107 / 0.113 | `eval/results/v3_?_test3_adv-all.json` (frozen `717521d`) | PD-13 better than A (bootstrap CI of B − A −0.26 to −0.08) but G1 fails (0.68× vs ≤ 0.5×) |
| M-37 | R3 precision cost, A / B | negative-FP items 0.389 / 0.559; collateral masking 2.0 % / 6.6 %; precision SECRET 0.64 → 0.13, BANK 0.84 → 0.32, DOB 0.98 → 0.44 | same | G2 fails; Q-27 risk materialised |
| M-38 | R3 key-novelty strata (per-value exposure), A / B | seen keys 20.7 % / 4.3 %; **unseen keys 40.6 % / 6.7 %**; no key 27.4 % / 19.4 % | same | A is vocabulary-dependent; inverting the default removes most of that dependence |
| M-39 | R3 L3 verdict (G6) | test3 C − B CSVE +2.15 pp; ΔFP test −2.2 pp vs dev +5.0 pp; standard dictionary typings 81 % non-gold on test3 (15 % on dev3build) | `v3_gates.json`, `v3_l3_provenance_test3.json` | L3 = vocabulary overfitting; G6 fails |
| M-40 | R3 generalisation gap (dev3build → test3 CSVE) | A 1.32×, **B 4.23×** (0.084 → 0.361), C 1.71×; dev3val B 0.233 | `v3_*_dev3build/dev3val/test3` | G3 fails; PD-13 implementation overfit its dev split |
| M-41 | R3 residual composition and latency | 61 % of B's exposed docs are name-only; PERSON 13 % of values exposed; B p95 259 ms (G5 fails), max 93 s on one document | `v3_posthoc_test3.json`, test3 results | Next bottleneck is PERSON in unstructured/spoken/bilingual text; parser DoS risk |
| M-42 | R4 read-only profiling of the "93 s" document | Not reproducible: every test3 item ≤ 1.19 s in all arms; NER is 80 % of cost (one pass per D0 view, ~55 ms/KB, uncapped); quadratic paths in shared code: `normalize._join_digits` (20 KB digits 16 s), `recognizers._cli_and_files` (20 KB token 3 s) | `eval/analysis_r4/` | Corrects M-41's latency claim; DoS vector in the production path (T-18) |
| M-43 | R4 exposure attribution, arm B test3 | PERSON 807 values (prose 379, field 215, non-Latin 213); unshaped forms 290; anchoring 148; segmentation 77; L3 release 26. Ceilings: perfect PERSON → CSVE 0.141, CSVE-HIGH unchanged 0.110; all non-PERSON → CSVE-HIGH 0.000 | `eval/analysis_r4/attribution_b.json` | Stronger NER alone cannot meet readiness (R4 §3) |
| M-44 | R4 adversarial canonicalisation ceiling | Unicode canonicalisation recovers 96–100 % on 9 kinds; reversal view 97 %; decoders 58–97 % at value level (MIME words and JSON need document-level decoding); speech, OCR and worded values 0 % (need reconstruction) | `eval/analysis_r4/adv_canon.json` | V2 layer A/D split (R4 §5) |


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
