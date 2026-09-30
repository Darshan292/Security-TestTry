# Competitive Landscape: LLM privacy layers vs the ESU gateway

- **Date:** 2026-09-30 | **Scheme:** `research/PROVENANCE.md` | **Sources:** `research/SOURCE_REGISTER.md` | **Measurements:** `research/EVIDENCE_MATRIX.md` Part A
- **Our code:** commit `a51c22d`, synthetic corpus only. Our numbers are FACT-M; every competitor number is not.

**Scope.** Comparison of commercial and open-source LLM privacy layers (masking, DLP, guardrails) against our gateway on eight capabilities. Built only from repo text; nothing was re-fetched. **Most vendor evidence is secondary (CLAIM-S)** because the vendor doc hosts (help.salesforce.com, developers.cloudflare.com, docs.aws.amazon.com, help.sap.com, docs.cloud.google.com) were blocked. FACT-P exists only where a raw.githubusercontent.com docs mirror, the Model Armor proto, PyPI JSON or a quoted GitHub banner was reached: Cloudflare (S-02, S-03), SAP (S-17, S-18, S-19), Google proto (S-08), Presidio (S-43, S-46), llm-guard (S-48), guardrails_pii (S-49), NeMo/LiteLLM PyPI (S-60, S-56). **No vendor publishes PII-masking latency or accuracy numbers** in anything we reached (SAP S-19 and Cloudflare S-02 are FACT-P for that absence; the rest is absence in secondary sources).

**Cell convention.** Each cell ends with `[class source]`. `[UNVERIFIED]` = looked for, not found, or contradictory. Absence findings are FACT-P about the document only, never about behaviour (PROVENANCE rule 4). Adoption cells are `[DECISION OSS]` (our choice from `OSS_COMPONENTS.md`), not evidence.

**Conflicts resolved (VERIFICATION wins over INDUSTRY_PATTERNS).** (1) Cloudflare is two features: DLP (pass/flag/block; buffers only with response scanning; tool args scanned as text) and Guardrails (REST: logged, not enforced; gateway: buffered, de-streamed). INDUSTRY's per-vendor note "SSE and tool-call coverage UNVERIFIED" and the "Ignore in Guardrails" action are stale or belong to Guardrails. (2) SAP streaming, tool calls and entity list are documented (S-17..S-19), not UNVERIFIED as the INDUSTRY note says. (3) Presidio: "no native async API found", not "True async UNVERIFIED". (4) Purview and Azure Content Safety are not described as PII masking. (5) LiteLLM #41611, PR #41936 and docs PR #1790 (ENGINEERING §3 `[V]`) are grade N in the register; not used as evidence here.

## 1. Capability matrix: commercial and internal systems

| System | Where it sits | Detection method | Reversible? | Consistent across turns? | Streaming behaviour | Tool-call coverage | Audit | Stated limits |
|---|---|---|---|---|---|---|---|---|
| Salesforce Einstein Trust Layer | Gateway between grounding and LLM [CLAIM-S S-13] | Pattern (ML names, regex, context words) and field-based [CLAIM-S S-14] | Yes, demasking step after LLM [CLAIM-S S-13] | UNVERIFIED [UNVERIFIED] | UNVERIFIED, no snippet mentions streaming (Q-18) [UNVERIFIED] | Masking disabled for agents and agent-invoked prompt templates [CLAIM-S S-15] | Stores original prompt, masked prompt, response [CLAIM-V S-95] | Entity/language lists; no 100% accuracy; quality loss [CLAIM-S S-14]; 65,536-token cap with masking [CLAIM-S S-16] |
| Cloudflare AI Gateway DLP | AI Gateway, request and response bodies [FACT-P S-02] | DLP profiles [CLAIM-S S-05] | No: Pass / Flag / Block only, no redaction [FACT-P S-02] | n/a, no substitution is performed [FACT-P S-02] | Buffers full response when response scanning is on; request-only scanning does not buffer [FACT-P S-02] | Tool args/results scanned only as text present in the JSON body [FACT-P S-02] | Flag sets `cf-aig-dlp` header; Block returns 400 and discards response [FACT-P S-02] | TTFT rises with full generation time; no latency figure [FACT-P S-02] |
| Cloudflare AI Gateway Guardrails (separate feature) | AI Gateway [FACT-P S-03] | Content-safety evaluation, not PII masking [CLAIM-S S-04] | n/a [CLAIM-S S-04] | n/a [CLAIM-S S-04] | REST endpoints: response evaluated and logged, not enforced; gateway endpoints: buffered and returned non-streamed [FACT-P S-03] | UNVERIFIED [UNVERIFIED] | Evaluates and logs result [FACT-P S-03] | Streaming enforcement gap [FACT-P S-03] |
| AWS Bedrock Guardrails | Model-boundary guardrail [CLAIM-S S-06] | Built-in ML/context PII detection plus custom regex [CLAIM-S S-06] | BLOCK or MASK to tags (`[NAME-1]`), one-way; reversible only via external tokenization pattern [CLAIM-S S-96] | UNVERIFIED [UNVERIFIED] | Sync buffers then scans; async releases chunks and cannot mask [CLAIM-S S-07] | Not checked by default; Strands SDK workaround [CLAIM-S S-97] | UNVERIFIED [UNVERIFIED] | Async can leak unfiltered chunks; latency and pricing not found [CLAIM-S S-07] |
| Google Model Armor + SDP | API to sanitize prompts and responses; inspect + de-identify templates (advanced mode) [CLAIM-S S-09] | SDP inspection templates [CLAIM-S S-09] | Only with a reversible SDP method (AES-SIV/FPE); caller runs re-identify itself [CLAIM-S S-12] | Deterministic transforms give referential integrity [CLAIM-S S-10] | `StreamSanitizeUserPrompt` / `StreamSanitizeModelResponse` RPCs exist; behaviour not in proto [FACT-P S-08] | No tool-call field in proto; behaviour unknown (Q-18) [FACT-P S-08] | UNVERIFIED [UNVERIFIED] | No Google latency figure; only anecdote 50-200 ms same region [CLAIM-S S-99] |
| Microsoft Presidio (library) | Library: analyzer + anonymizer; repo moved to data-privacy-stack [FACT-P S-43] | Regex, NER, context; GLiNER/transformers extras [CLAIM-S S-47] | Anonymizer; batch deanonymization added in 2.2.361 [CLAIM-S S-47] | UNVERIFIED [UNVERIFIED] | No streaming layer found; no native async API [FACT-P S-46] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | Warns detection cannot guarantee coverage [FACT-P S-43] |
| Microsoft PII Shield (proxy) | Privacy proxy in front of each LLM call [CLAIM-V S-98] | UNVERIFIED [UNVERIFIED] | Swaps PII for placeholders; restore not confirmed [CLAIM-V S-98] | "Stable placeholders" claimed [CLAIM-V S-98] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | None stated in what we saw [UNVERIFIED] |
| Microsoft Purview DSPM for AI | Governance layer over AI apps [CLAIM-S S-21] | 300+ built-in sensitive info types classify prompts/responses [CLAIM-S S-21] | No masking or reversibility statement found (absence) [CLAIM-S S-21] | n/a [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | Governance, audit, DLP block [CLAIM-S S-21] | Azure AI Content Safety PII masking not found [UNVERIFIED] |
| SAP AI Core orchestration | Optional module in orchestration workflow, SAP Data Privacy Integration [FACT-P S-19] | 27 entity types, many locale-limited [FACT-P S-19] | Pseudonymization (`MASKED_ENTITY_ID`) unmasked in response; anonymization (`MASKED_ENTITY`) irreversible [FACT-P S-19] | UNVERIFIED [UNVERIFIED] | Unmasking works on streamed pseudonymized output; split tag moved whole into next chunk; smaller chunks reduce accuracy [FACT-P S-17] | Tool args unmasked on return and re-pseudonymized when sent back; not with anonymization [FACT-P S-18] | UNVERIFIED [UNVERIFIED] | Names English-only, addresses US-only; cannot guarantee all PII found; no latency [FACT-P S-19] |
| Skyflow | Vault; tokenize before LLM [CLAIM-V S-27] | "50+ types, multiple languages" [CLAIM-V S-27] | Policy-gated detokenization [CLAIM-V S-27] | Per-column token maps [CLAIM-V S-27] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | No limits stated; docs page S-26 not opened [UNVERIFIED] |
| Protecto | Tokenize, call LLM, restore for authorised users [CLAIM-V S-102] | UNVERIFIED [UNVERIFIED] | Yes, for authorised users [CLAIM-V S-102] | "Semantically meaningful tokens"; consistency not stated [CLAIM-V S-102] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] |
| Private AI | No primary retrieved (S-30 not opened) [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] |
| Nightfall | External SaaS API; third-party transit claim unconfirmed (S-29 not opened) [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] |
| Lakera Guard | API service [CLAIM-V S-24] | ML detectors via one API [CLAIM-V S-24] | Can block or mask; no restore stated [CLAIM-V S-24] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | Returns match locations via payload parameter [CLAIM-V S-24] | UNVERIFIED [UNVERIFIED] |
| Uber GenAI Gateway (internal system, not a product) | OpenAI-API-mirroring gateway; redactor before third-party vendors [CLAIM-S S-100] | UNVERIFIED [UNVERIFIED] | Yes, stored mapping used to un-redact [CLAIM-S S-100] | No: inconsistent placeholders broke caching and RAG (Uber's report) [CLAIM-S S-100] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | Added latency, quality loss; no numbers retrieved [CLAIM-S S-100] |
| Kong AI PII Sanitizer | Gateway plugin (Gateway 3.10+, needs AI Proxy) [CLAIM-V S-23] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | Enterprise-only licence; self-hosted container [CLAIM-V S-23] |

Notes: Uber S-101 (InfoQ) restates S-100, so it is one claim, not two. Cloudflare canonical pages were not rendered; the mirror is the docs source branch, so live wording could differ (register warning A5).

## 2. Capability matrix: open source

| Project | License | Status | Reversible restore | Streaming | Tool calls | Known defects | Our adoption decision |
|---|---|---|---|---|---|---|---|
| Presidio | MIT [FACT-P S-46] | Active; 2.2.364 on 2026-07-22; moved to data-privacy-stack, MCR images stale [FACT-P S-46] | Anonymizer and batch deanonymization (2.2.361) [CLAIM-S S-47] | No streaming layer; no native async (use executor) [FACT-P S-46] | UNVERIFIED [UNVERIFIED] | Sync engine blocks event loop unless wrapped (absence evidence) [FACT-P S-46] | ADOPT: one detector, pin version [DECISION OSS] |
| LiteLLM Presidio guardrail | MIT; `enterprise/` under separate licence [CLAIM-S S-55] | Active; PyPI 1.103.1, date unverified [FACT-P S-56] | `output_parse_pii` literal string replace on per-request table, counters restart each request [CLAIM-S S-55] | `/v1/messages` output masking skipped; placeholders split across frames; patches #42351, #43345, #42476 [CLAIM-S S-78] | #31950: placeholders not restored in `tool_calls` arguments [CLAIM-S S-73] | #30728: fails open on analyzer error [CLAIM-S S-74] | REJECT in front of core [DECISION OSS] |
| Portkey gateway | MIT [CLAIM-S S-52] | Active; v1.15.2 (Jan 2025) on releases tab, may be stale [CLAIM-S S-54] | UNVERIFIED [UNVERIFIED] | Page claims streaming guardrail support [CLAIM-V S-52] | UNVERIFIED [UNVERIFIED] | None found [UNVERIFIED] | REFERENCE ONLY [DECISION OSS] |
| NeMo Guardrails | Apache-2.0 [FACT-P S-60] | Active; 0.24.1 on 2026-09-16 [FACT-P S-60] | Detect and mask; no restore seen [CLAIM-S S-25] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | Presidio blocked async loop, fixed in PR #2400 [CLAIM-S S-82] | REFERENCE ONLY [DECISION OSS] |
| Envoy AI Gateway | Apache-2.0 [CLAIM-S S-57] | Active; rebranded Agent Router; v1.1.0 date suspect [CLAIM-S S-58] | No OSS PII plugin found (secondary absence) [CLAIM-S S-57] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | None found [UNVERIFIED] | None recorded in OSS_COMPONENTS [UNVERIFIED] |
| llm-guard | MIT [FACT-P S-48] | ARCHIVED 2026-07-09; PyPI 0.3.16 (2025-05-19) [FACT-P S-48] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | Unmaintained, HF models included [FACT-P S-48] | REJECT [DECISION OSS] |
| guardrails_pii | Apache-2.0 [FACT-P S-49] | ARCHIVED [FACT-P S-49] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | Archived though the ESU survey recommends it [FACT-P S-49] | REJECT (pattern reference) [DECISION OSS] |
| carnaval | Apache-2.0 [CLAIM-S S-50] | Beta v0.2.3, 1 star, 42 commits [CLAIM-S S-50] | Reversible anonymization, AES-256-GCM [CLAIM-V S-50] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | "Enterprise production use" claim unverified [CLAIM-V S-50] | REFERENCE ONLY [DECISION OSS] |
| Anonproxy | MIT [CLAIM-S S-66] | 7 stars, 57 commits [CLAIM-S S-66] | Tolerant restoration [CLAIM-V S-66] | Reassembles surrogates split across stream chunks [CLAIM-V S-66] | UNVERIFIED [UNVERIFIED] | None found [UNVERIFIED] | REFERENCE ONLY [DECISION OSS] |
| llm-proxy-pii-rust | AGPL-3.0 + commercial [CLAIM-S S-65] | Last commit not verified [UNVERIFIED] | Reversible `[EMAIL_1]`, fail-closed [CLAIM-V S-65] | Listed in a search result, not confirmed on page [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | Self-reported ~20 ms structured-only, ~4.7 s/turn with NER (~563 MB) [CLAIM-V S-65] | REJECT: AGPL [DECISION OSS] |
| truefoundry custom-guardrails-template | UNVERIFIED [UNVERIFIED] | Not archived, 2 stars [CLAIM-S S-51] | Presidio redaction; no restore stated [CLAIM-S S-51] | UNVERIFIED [UNVERIFIED] | UNVERIFIED [UNVERIFIED] | None found [UNVERIFIED] | None recorded in OSS_COMPONENTS [UNVERIFIED] |

LiteLLM #41611 (split-chunk leak), PR #41936 (full-buffer fix) and docs PR #1790 are grade N and are not evidence here [UNVERIFIED]. The GLiNER model-card F1 figures (75.5 to 83.25) are vendor-reported (CLAIM-V, S-68), and one third-party F1 of ~0.38 for another checkpoint is CLAIM-S (S-88).

## 3. Our position

Commercial columns:

| System | Where it sits | Detection method | Reversible? | Consistent across turns? | Streaming behaviour | Tool-call coverage | Audit | Stated limits |
|---|---|---|---|---|---|---|---|---|
| ESU gateway (this repo, commit a51c22d) | OpenAI-compatible gateway, sole holder of provider keys; 809 tests pass, 1 xfail [FACT-M M-17] | Regex/checksum/context/field-name plus roster EDM, no ML yet; TEST leak 2.56% vs gate 1%; roster names recall 1.000 [FACT-M M-01, M-08] | Scoped HMAC aliases rebuilt per request; streaming round trip exact on 1,213/1,240 TEST items [FACT-M M-14] | Deterministic per conversation scope; restore ledger tested at unit level only, real-LLM copy fidelity unmeasured (Q-01) [FACT-M M-18] | Bounded hold-back, gate before restore; 26 alias-split failures (W5), 2 gate-context losses (W4) [FACT-M M-14] | Args buffered whole, gated, restored; gateway tests only, no real agent loop measured [FACT-M M-17] | Content-free egress ledger; egress invariant 0 true catches, 8 false alarms on TEST (W9) [FACT-M M-09] | 14.6% of TEST documents send a raw value; spaced-letter evasion leaks 38.0%; utility not measured [FACT-M M-10, M-13, M-20] |

OSS columns:

| Project | License | Status | Reversible restore | Streaming | Tool calls | Known defects | Adoption decision |
|---|---|---|---|---|---|---|---|
| ESU gateway (this repo, commit a51c22d) | Not stated in the evidence files [UNVERIFIED] | Phase 1 slice; verdict: not secure enough for real HRIS data, synthetic-data pilot only [FACT-M M-01] | Yes, stateless scoped aliases [FACT-M M-14] | Round trip exact 1,213/1,240 [FACT-M M-14] | Gateway tests only [FACT-M M-17] | W1..W9 below [FACT-M M-01] | n/a (ours) [FACT-M M-17] |

Measured weaknesses (SECURITY_EVALUATION §5). We are measured worse than the competitors' documented behaviour on streaming correctness (W4, W5) and lack the ML detector that every surveyed vendor with name detection uses. Competitors' accuracy is not published, so no accuracy comparison is possible; we cannot say we are better or worse.

| W | Weakness | Measurement |
|---|---|---|
| W1 | Names outside the roster: main leak (PERSON 1.96% of TEST chars; 1,160/1,160 DEV misses external) | [FACT-M M-02, M-07] |
| W2 | Evasion: spaced 38.0%, obfuscated email 14.4%, case 5.6%, NBSP 5.6% | [FACT-M M-13] |
| W3 | Structured-type generalisation: AADHAAR 0.918, SECRET 0.916 (gate 0.98, both FAIL) | [FACT-M M-05, M-06] |
| W4 | Streaming output gate loses context; raw Aadhaar reached client in 2 TEST items | [FACT-M M-14] |
| W5 | Minified JSON over 256 chars splits aliases in streaming restore (26 TEST items) | [FACT-M M-14] |
| W6 | E-UTIL never run; output-quality equivalence unmeasured | [FACT-M M-20] |
| W7 | Synthetic data only, about 6 free-text templates per split; upper bound on real recall | no M-ID (unmeasured; Q-07) |
| W8 | Precision on unseen templates: 164/250 negative items flagged | [FACT-M M-11] |
| W9 | Egress invariant false-fires on alias text: 8 false alarms | [FACT-M M-09] |

Also measured: detection latency p50 2.3 ms, p95 4.2 ms, p99 6.8 ms, detection only, CPU, not end-to-end (Q-17) [FACT-M M-15]. Match with SECURITY_EVALUATION §7: structural parity plus stateless aliases, purpose binding and a content-free audit trail as differences; those "beyond the norm" comparisons inherit the S grade of the vendor claims they rest on.

## 4. Gaps nobody documents solving

| Gap | Who fails, how | What we do | Measured? |
|---|---|---|---|
| Streaming plus reversible masking | Cloudflare DLP buffers whole response [FACT-P S-02]; Guardrails logs but does not enforce, or de-streams [FACT-P S-03]; AWS async cannot mask [CLAIM-S S-07]; SAP re-chunks split tags and says small chunks cut accuracy [FACT-P S-17]; LiteLLM streaming patched repeatedly [CLAIM-S S-78]; Salesforce, Google, Uber unknown [UNVERIFIED] | Bounded hold-back with gate before restore; ADR-0008 "impossible by construction" is refuted | Yes, defective: 1,213/1,240 exact, W4, W5 [FACT-M M-14]; buffered alternative open (Q-12) |
| Tool and agent boundaries | Salesforce disables masking for agents [CLAIM-S S-15]; AWS unchecked by default [CLAIM-S S-97]; Cloudflare text-only [FACT-P S-02]; SAP pseudonymization only [FACT-P S-18]; Google proto has no tool field [FACT-P S-08]; LiteLLM misses `tool_calls` args [CLAIM-S S-73] | Tool results treated as input; args buffered, gated, restored (ADR-0009) | Tests only, no real agent loop [FACT-M M-17] |
| Cross-turn consistency | Uber inconsistent placeholders broke caching/RAG [CLAIM-S S-100]; LiteLLM counters restart per request [CLAIM-S S-55]; Google deterministic transforms are the exception [CLAIM-S S-10] | Deterministic scoped aliases plus restore ledger for re-ingested output (ADR-0010) | Unit level only; alias collision fix after M-12; real-LLM fidelity open (Q-01) [FACT-M M-18] |
| Published accuracy and latency | No vendor figures in anything reached: SAP page lists none and disclaims completeness [FACT-P S-19]; Cloudflare only qualitative [FACT-P S-02]; Salesforce "no 100%" [CLAIM-S S-14]; third-party latency anecdotes [CLAIM-S S-103] | Publish per-type recall with CIs, leak rate, latency | Yes, but synthetic and DEV-tuned; TEST spent; end-to-end latency open (Q-17) [FACT-M M-01, M-15] |
| Output utility cost of masking | Salesforce disables masking for agents [CLAIM-S S-15]; Uber reports quality loss without numbers [CLAIM-S S-100] | E-UTIL designed, gate at most 5% | No, never run (Q-02) [FACT-M M-20] |

## 5. What would change our design

| Competitor fact, if verified | Would change | Q-ID |
|---|---|---|
| Salesforce streams with incremental demasking, or buffers | ADR-0008: validates hold-back if incremental; supports buffered default for RESTRICTED if it buffers | Q-18, Q-12 |
| Google Model Armor covers tool-call payloads (proto shows no field) | ADR-0009 tool boundary reference; possible build-vs-buy input | Q-18 |
| SAP quantifies accuracy loss from small chunks, or publishes latency | ADR-0008 hold-back sizing; N-1 targets | Q-17, Q-18 |
| Cloudflare canonical pages differ from the docs mirror | Every Cloudflare row (FACT-P becomes CLAIM-S until re-read) | Q-20 |
| Any vendor publishes end-to-end masking latency or recall on a public benchmark | N-1 and leak-gate framing; comparability claims | Q-17, Q-04 |
| Uber quantifies inconsistent-placeholder breakage | ADR-0002 alias format weight; E-FMT design | Q-01 |
| Presidio gains native async or NER hosting that beats direct GLiNER | ADR-0005 (drop or keep Presidio) | Q-03 |
| Independent recall for Apache NER checkpoints on Indian and HR names | ADR-0005 NER choice; W1 fix | Q-04 |
| LiteLLM #41611 and PR #41936 verified (currently grade N) | ADR-0008 evidence base and the pass-through-is-leaky argument | Q-21 |
| An OSS PII plugin found for Kong or Envoy (absence is secondary only) | ADR-0001 build-vs-adopt; OSS decisions | Q-22 |
| PII Shield or Skyflow documents restore semantics, streaming and tool handling | ADR-0002 layering and ADR-0003 (vault versus ephemeral) | Q-23 |
| Purview or Content Safety shown to mask prompts | Row reclassification only; no ADR change expected | Q-24 |
| Any vendor publishes utility loss for masked business text | E-UTIL gate and ADR-0002 format | Q-02 |
