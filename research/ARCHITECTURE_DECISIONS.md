# Architecture Decisions — traceability and status

- **Date:** 2026-09-30 · **Scheme:** `research/PROVENANCE.md` · **Code:** `a51c22d`
- The ADR files in `ADR/` hold the reasoning. This file holds the **evidence trail**
  and the **honest status** of each decision after the TEST run.

## Status vocabulary

| Status | Meaning |
|---|---|
| **VALIDATED** | Held-out measurement (FACT-M) supports it |
| **SUPPORTED** | Unit or integration tests support it; no held-out measurement |
| **PARTIAL** | Implemented in part; the unimplemented part is untested |
| **CONTRADICTED** | A measurement shows a stated property is false. Needs an amendment |
| **PROVISIONAL** | Rests on CLAIM-S / KNOWN evidence or an open question |
| **PROPOSED** | Not accepted. Awaiting the owner's review (no code) |

## Accepted decisions

| ADR | Decision (one line) | Evidence for | Evidence against / risk | Threats | Reqs | Components | Tests / gates | Metrics | Status |
|---|---|---|---|---|---|---|---|---|---|
| ADR-0001 | Owned OpenAI-compatible proxy; the only holder of provider keys; pure core library | C-02, C-07 (vendors sit at the same boundary); INFERENCE | Network egress policy not built or tested | T-13, T-15 | F-1, N-8 | K-gateway.app, K-gateway.upstream | `test_gateway::test_auth_and_purpose_and_model`, strict-body tests | M-17 | SUPPORTED |
| ADR-0002 | Model sees short scoped HMAC aliases; crypto only at rest | C-11 (consistency, CLAIM-S) | **M-12**: the v1 alias grammar collided with identifiers. Fidelity with real models unmeasured (Q-01) | T-4, T-14, T-17 | F-4 | K-alias | `test_alias_*`, `test_parse_alias_lenient`, `test_gate_leaves_ordinary_identifiers_alone`, E-FMT (not run) | M-12, M-14 | **CONTRADICTED → amended in code** (digit-first Crockford suffix, ~28.3 bits); ADR text not yet amended; **rationale PROVISIONAL** until E-FMT (Q-01) |
| ADR-0003 | Ephemeral per-request mapping; no PII at rest on the chat path | C-30 (vaults are the target, CLAIM-V); INFERENCE (stateless chat APIs) | Stateful provider APIs unsupported; zeroisation best-effort (T-16) | T-5, T-16 | F-10 (Phase 3) | K-pipeline (`Protected.alias_map`), K-stream | Gateway round-trip tests | M-14 | SUPPORTED by tests; **rationale PROVISIONAL** (INFERENCE + CLAIM-V only) |
| ADR-0004 | Purpose declared by a registered app, never inferred from the prompt | INFERENCE; the external review's concern is addressed by the registry + 403 | Registry key is a bearer secret in Phase 1 (no mTLS yet) | T-15 | F-3 | K-gateway.app (`authenticate`), K-policy (`UnknownPurpose`), `registry/apps.yaml` | `test_auth_and_purpose_and_model` | M-17 | PARTIAL (mTLS not built); **rationale PROVISIONAL** (INFERENCE only) |
| ADR-0005 | Detection cascade D0–D5 + known-value propagation + detection cache | M-03, M-08, M-16 (large effect of D1); C-13 (no guarantee) | **M-07** external names (no NER). **M-13** evasion. **M-05/M-06** unseen shapes. **Deviation:** no Presidio, no NER, no detection cache built (Q-03) | T-2, T-3, T-10, T-18 | F-2, F-9, N-1, N-4 | K-normalize, K-recognizers, K-edm, K-pipeline | E-DET, E-ADV, `test_recognizers` (~780 tests) | M-01..M-08, M-11, M-13, M-15 | PARTIAL; **leak gate FAILED** (M-01) |
| ADR-0006 | In-process decision tables per (purpose, type); OPA deferred | INFERENCE (per-entity hot path) | Pack signing not implemented | T-15 | F-3, F-8 | K-policy, `policies/hris-default.yaml` | `test_generalize`, block/redact tests, gateway 422 test | — | PARTIAL (no signing, no simulator CLI); **rationale PROVISIONAL** (INFERENCE only) |
| ADR-0007 | Egress invariant on exact outbound bytes; fail-closed matrix; content-free telemetry | C-16 (LiteLLM fails open, CLAIM-S); C-10 (Salesforce stores content, contrast) | **M-09**: 0 true catches (expected, T-20) **and 8 false alarms** (W9). Canary sweep of all sinks not built as a suite | T-1, T-9, T-19, T-20 | F-7, N-5, N-6, N-7 | K-egress, K-gateway.app (ledger, `_Reject`, logging) | `test_egress_invariant_catches_bug_leak_in_any_encoding`, `test_block_422_no_value_echo`, egress-ledger-no-values test | M-09 | PARTIAL; W9 open; **rationale PROVISIONAL** (CLAIM-S only; no measured true catch yet) |
| ADR-0008 | Incremental streaming restore with bounded hold-back; gate on aliased text before restore; tool args buffered whole | C-04, C-05 (SAP, FACT-P); C-01, C-03 (vendors buffer, FACT-P); C-17 (CLAIM-S) | C-18 cited in the ADR is UNVERIFIED and must be removed from its rationale. **M-14**: "split-placeholder leaks impossible by construction" is **false** for runs > 256 chars (W5); the sliding-window D2 gate misses context-dependent PII (W4) | T-6, T-7 | F-5, F-6 | K-stream, K-gateway.app (`_stream`) | `test_stream_restore_every_split_offset`, `test_stream_any_chunking`, E-STREAM | M-14 | **CONTRADICTED** (two claims) |
| ADR-0009 | Tool results and RAG are enforcement points | C-05, C-07, C-08, C-02 | RAG not built; tool allow-listing not built; `tools`/`stop` parameters scanned (added after review) | T-7, T-8, T-12 | F-12, F-13 | K-pipeline (`_segments` covers tool role and args), K-gateway.app | tool-call gateway tests, `test_tools_and_stop_with_pii_rejected` | — | PARTIAL (tools only) |
| ADR-0010 | Keyed-hash restore ledger per conversation; conversation id required for multi-turn restore | M-18 (leaks without the ledger, not with it) | Unit level only; the ledger is SQLite, not the planned PostgreSQL | T-21 | F-10 | K-edm.RestoreLedger, K-gateway.app | `test_t21_truncated_history_caught_by_restore_ledger`, gateway multi-turn test | M-18 | SUPPORTED |

Graph audit note (`graph/GRAPH_REPORT.md`): C-32, "the source report proposes X", is recorded as what
ADR-0002/0003/0005/0006 **depart from**, not as support. ADR-0003, ADR-0004 and ADR-0007 currently have
no FACT-P/FACT-M evidence that the *decision* is right (tests show only that the implementation behaves),
so their rationale is marked PROVISIONAL.

## R2 status update (2026-09-30, code `4a6a75e`)

| ADR | R2 change | Held-out evidence | Status now |
|---|---|---|---|
| ADR-0002 | Suffix grammar amended in the ADR text | M-12 fixed (no identifier corruption) | CONTRADICTED → **AMENDED**; rationale PROVISIONAL (Q-01) |
| ADR-0005 | D0 views, structural escalation, D1 field context, D4 NER (spaCy) | M-21, M-23, M-24, M-28, M-30 | PARTIAL; **leak gate FAILED on test2** (6.1–8.2 %) |
| ADR-0007 | Alias blanking before the sweep | M-26 (0 false alarms, clean test2); M-27 availability risk under held-out transforms | PARTIAL |
| ADR-0008 | RESTRICTED buffered by policy; incremental hardened | M-25 (E-STREAM-LEAK FAIL, 9/1,755) | AMENDED; incremental **not approved** for RESTRICTED |

Weaknesses added by R2 (SECURITY_EVALUATION_R2.md): W-10 unseen document formats, W-11 held-out
evasions, W-12 external names at 0.794, W-13 secrets in new places.

| ID | Proposal | Why | Closes |
|---|---|---|---|
| PD-10 | **Value-shape masking under RESTRICTED purposes**: mask Verhoeff-valid 12-digit numbers, Luhn card-length numbers, dates in person-bearing documents (month/year) and high-entropy tokens regardless of context; measure on a third fresh split | M-23, M-30 | W-10, W-13 |
| PD-11 | D0 views for line-wrapped values, HTML entities and OCR confusions (O↔0, l↔1) inside digit runs | M-27 | W-11 |
| PD-12 | Re-evaluate GLiNER when huggingface.co is allowed; keep spaCy `lg` meanwhile | M-24 | W-12 |
| PD-13 | **Invert detection (proposed 2026-10-01, ROOT_CAUSE_ANALYSIS_R2 §C):** context-free candidates (validators, entropy, EDM, NER) for recall; generic parsing into a record graph; field semantics from standard dictionaries + column statistics used to *release*, not to *find*; RESTRICTED default-deny for shape-flagged values in person-bearing records; ML may add masking, never remove it. Absorbs PD-10 (with the date cost of M-35) and PD-11. Not implemented | M-31, M-33, M-34, M-35 | W-10, W-11, W-13 |
| PD-14 | **Third corpus from an independent generator design**, frozen before any PD-13 code; families held out by family, ≥ 10 templates per family; gates pre-registered first (refines PD-6) | M-32 (template concentration); a reseed of synth2 reshuffles spent templates | Evaluation integrity |
| PD-13 status (2026-10-01) | **NOT ADOPTED.** Fails G1, G2, G3, G5 and G6; passes G4 (SECURITY_EVALUATION_R3.md). The default-inversion idea is supported for shaped values (M-38); L3 dictionaries are refuted as a generalising mechanism (M-39) | M-36, M-37, M-38, M-39, M-40, M-41 | — |

## Required amendments (documentation only; the code already reflects the first)

| Amends | Change | Evidence |
|---|---|---|
| ADR-0002 | Suffix grammar `[0-9][Crockford]{5}` (+2 on collision); entropy ~28.3 bits; P(collision, 200 same-type entities) ≈ 6×10⁻⁵ | M-12 |
| ADR-0008 | Withdraw "impossible by construction" until W5 is fixed; state that the streaming D2 gate is weaker than non-streaming for context-dependent types; remove C-18 from the rationale | M-14, C-18 |
| ADR-0005 | Record the Presidio deviation (Q-03); restate spaced/obfuscated evasion as a measured residual (M-13), not "partly handled" | M-13 |
| ADR-0007 | The invariant must exclude alias tokens from its sweep; report its false-alarm rate as an availability metric | M-09 |

## Proposed decisions (awaiting review; nothing implemented)

| ID | Proposal | Why (evidence) | Cost / risk | Closes |
|---|---|---|---|---|
| PD-1 | Make **roster sync (F-11) a Phase 1 prerequisite**, not Phase 4 | M-08: the only control measured at 1.000 | Needs HRIS integration access (Q-10) | W1 for employees |
| PD-2 | **D0 evasion normalisation**: single-letter-run collapse, `[at]`/`[dot]` de-obfuscation, separator-tolerant digit groups, fail closed on ambiguity | M-13 (up to 38 % leak) | Offset-map complexity; precision cost to measure | W2 |
| PD-3 | Default **buffered output mode for RESTRICTED purposes** until W4/W5 are fixed and re-measured | C-01, C-03 (the industry default); M-14 | TTFT equals full generation time for those purposes | W4, W5 (interim) |
| PD-4 | Introduce **NER (GLiNER, Apache-licensed checkpoint)** as D4, benchmarked on a fresh split; then retire the preposition cue | M-07, M-11; C-19, C-20, C-21, C-22 (all CLAIM-S) | CPU latency ~100 ms+ per 500 B (C-22); needs the ADR-0005 cache | W1 for external names; W8 |
| PD-5 | **Structural HIGH-type escalation**: a Verhoeff-valid 12-digit value inside HRIS-shaped payloads, or credentials in URIs, are treated as HIGH regardless of keywords | M-05, M-06 | Precision cost (Q-05) | W3 |
| PD-7 | **Run E-UTIL** on the target model before any pilot (`eval/run_util.py`) | The external review's central gap; M-20 | API cost of about n × 4 calls | W6 |
| PD-8 | **E-DET-R real-data spot check** inside a client boundary (aggregate counts only) | Synthetic-only evidence (R-03) | Needs client infosec approval (Q-07) | W7 |
| PD-9 | **The egress sweep excludes alias tokens; a TSV header-row guard** in field-context detection | M-09 (8 false alarms) | Small; re-measure on a fresh split | W9 |
| PD-6 | **A fresh test split per release** (new seed, new templates, authored before tuning) | This split is spent (SECURITY_EVALUATION §2) | Generator effort | Evaluation integrity |
