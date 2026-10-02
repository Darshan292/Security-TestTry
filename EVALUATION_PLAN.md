# Evaluation Plan — pre-registered

- **Status:** Proposed (Phase 0). Gates below are frozen before any system run;
  changing a gate after seeing results requires a dated amendment with reason.
- **Date:** 2026-09-30

A demo that masks "John Doe" is not evidence. This plan defines what counts.

## 0. Rules

1. **Held-out means held-out.** Gold data is split 60/20/20 (dev/tune/test) by
   *document template and name source*, not by row. Thresholds are tuned on
   `tune`; gates are read once on `test` per release.
2. **Gold is authored before the system runs on it**, by someone who did not
   write the detectors, and frozen (hash recorded).
3. **No LLM judge for detection metrics.** Span gold only. LLM judges are
   allowed for E-UTIL only, and must be calibrated against ≥ 100 human-rated
   items (report agreement).
4. **Report per entity type**, never only micro-averaged. A 0.97 micro-F1 that
   hides 0.60 recall on BANK_ACCOUNT is a failure.
5. **Synthetic-data bias is declared in every report.** Real HRIS data will not
   be available for evaluation; §E-DET-R describes the only acceptable proxy.

## E-DET — detection quality (gate for every release)

Corpus (synthetic, generated then hand-corrected):

| Slice | Size (min) | Content |
|---|---|---|
| HR free text | 1,500 docs | case notes, emails, appraisal comments, grievance summaries; Indian + global names incl. single-token names, initials, Hinglish |
| HRIS structured | 1,000 payloads | Workday/UKG-shaped JSON & XML (worker, comp, absence), CSV exports |
| Integration/debug | 500 items | logs, stack traces, auth headers, EIB/Studio-style XML, curl commands with tokens |
| Negatives | 1,000 items | text dense in look-alikes: invoice numbers, 12-digit order ids, public company names, dates, amounts that are not compensation |

Name sources must include ≥ 30 % names **absent** from any EDM dictionary, so
NER is actually tested.

**Statistical power (amendment 2026-09-30, before any run).** A recall gate
is meaningless if the test split holds 40 positives (one miss = 0.975). Minimum
*positive instances per type in the test split*: **≥ 150** for each HIGH type,
EMAIL, PHONE, EMPLOYEE_ID (zero misses at n = 150 bounds the true miss rate at
≈ 2 % with 95 % confidence, rule of three); **≥ 400** for PERSON; **≥ 200** for
COMPENSATION. Generators are sized to hit these after the 60/20/20 split. Every
per-type result is reported with its Wilson 95 % interval; a gate passes on the
point estimate **and** requires the interval's lower bound to be within 0.03 of
the gate. Types that cannot reach the minimum n are reported as *underpowered*,
not as passing.

Metrics: per-type precision, recall, F1 (exact span and overlap); **leak rate**
= fraction of gold sensitive characters that reach the egress body.

**Gates (test split):**

| Type group | Recall (overlap) | Precision |
|---|---|---|
| HIGH structured (Aadhaar, PAN, bank, card, secrets, passport…) | ≥ 0.98 | ≥ 0.90 |
| EMAIL, PHONE | ≥ 0.98 | ≥ 0.95 |
| PERSON in HR free text | ≥ 0.90 (P1 w/o NER: report only) → ≥ 0.93 (P2) | ≥ 0.85 |
| COMPENSATION | ≥ 0.95 | ≥ 0.85 |
| EMPLOYEE_ID (tenant formats) | ≥ 0.98 | ≥ 0.95 |
| Overall character leak rate on RESTRICTED slices | ≤ 1.0 % | — |

**E-DET-R (realism check):** with client/infosec approval only, run the
detector *inside* the client boundary on a sample of real records and report
**aggregate** per-type counts of detections vs. a human spot-check of 200
records. No raw data leaves; the output is numbers.

## E-ADV — adversarial robustness

Transformations applied to E-DET test items: homoglyphs, zero-width joiners,
spaced letters (`R a h u l`), dotted/obfuscated email (`rahul [at] acme [dot] in`),
digit grouping changes, OCR noise, mixed scripts (Devanagari names), line breaks
inside values, JSON-escaped strings, values split across messages.
Report recall delta vs. clean. **Gate:** HIGH types recall drop ≤ 3 pts.
Base64/hex-encoded payloads: measured and reported, **no gate in Phase 1**
(declared residual risk).

## E-FMT — alias round-trip fidelity (decides ADR-0002's open question)

Candidates: `[PERSON_7QX2MA]`, `<PERSON_1>`, realistic surrogate.
Tasks: summarise, draft email, extract to JSON, translate to Hindi, rewrite
formally. Models: every Phase 1 destination. n ≥ 500 per (format, model).
Metric: fraction of aliases restored exactly; fraction mangled-but-recoverable
(lenient matcher); fraction lost; fabricated-alias rate.
**Decision rule (pre-registered):** keep `[TYPE_xxxxxx]` unless another format
beats it by ≥ 3 pts exact-restore *and* ≥ 2 pts on E-UTIL.

## E-UTIL — task utility

Paired design: same task, same model, raw synthetic input vs. gateway-processed.
Tasks per purpose (HR case summary, email draft, ticket classification, JSON
extraction). Metrics: classification accuracy; extraction field accuracy;
summary quality via calibrated judge + 100 human ratings.
**Gate:** ≤ 5 % relative degradation per purpose, or the purpose's policy is
revisited (e.g. GENERALIZE instead of TOKENIZE).

## E-STREAM — streaming correctness

Golden streams replayed with chunk boundaries at **every offset** of every
alias, multi-choice streams, interleaved tool-call deltas, Anthropic
content-block streams, abrupt termination. **Gate:** 0 raw-alias emissions to
client, 0 un-restored complete aliases, 0 split-value leaks; TTFT overhead
p95 ≤ 50 ms.

### E-STREAM-T21 — re-ingested restored output (ADR-0010)

Multi-turn scripts (≥ 200) where the user turn that introduced a value is
dropped before turn *n*; NER is forced to miss (mock detector); the in-process
detection cache is cleared between turns; turns alternate between two gateway
pods. **Gate: 0 raw restored values in any egress body.** Also asserts
`400 PGW_CONVERSATION_ID_REQUIRED` for restore-enabled apps sending assistant
turns without a conversation id.

## E-EGRESS-FUZZ — bug-leak hunting

Property-based fuzzing (Hypothesis) over request shapes: nested tool results,
repeated values with different casing, overlapping entities, offsets after
normalisation, retries, provider errors mid-request. Oracle: the egress
invariant. **Gate:** 0 violations over 10⁶ generated requests per release.

## E-CANARY — telemetry leakage

Honeytoken PII through every endpoint and error path (including forced
exceptions, timeouts, 4xx/5xx from provider). Grep logs, traces, metrics,
ledger, crash output. **Gate:** 0 hits. Runs in CI and nightly in production.

## E-PERF — latency and throughput

Workloads: 1 KB / 8 KB / 32 KB prompts; 1 / 10 / 30-turn conversations;
structured payloads. Report p50/p95/p99 added latency per stage, TTFT delta,
RPS per pod, NER pool utilisation, cache hit rate.
**Gates:** DESIGN N-1, N-2.

## Reporting

Each release produces `EVAL_REPORT_<version>.md`: gold hash, detector-set
version, policy pack versions, per-type table, failures by category with 20
sampled misses each (synthetic data, so safe to show), and an explicit
"what these numbers do not show" section.

## Amendment 2026-09-30b — Release 2 protocol (registered before any R2 fix is measured)

Trigger: the R1 one-shot TEST (SECURITY_EVALUATION.md) failed the leak gate; R1 TEST is spent.
**No existing gate or metric definition is changed by this amendment.** It adds metrics, and one gate
for streaming, which R1 did not measure end-to-end.

1. **Corpus v2.** New seed, **new templates** written by an author who has not read the detector
   code or the R2 fixes. Splits: `dev2` (tuning allowed), `test2` (one-shot, locked), `ner_test2`
   (free text whose PERSON names are all absent from the roster, for E-NER). The manifest hash is
   committed **before** any R2 fix is evaluated. All R1 splits become dev data.
2. **Held-out adversarial transforms.** The six R1 transforms are known to fixers. The corpus author
   defines ≥ 3 additional transform kinds, which stay undisclosed to fixers until after the `test2` run.
   Both sets are reported separately.
3. **Metrics (definitions fixed now):**
   - *Character leak rate*: unchanged. Gate ≤ 1.0 %.
   - *Document exposure*: the fraction of documents whose protected outbound body contains ≥ 1 raw
     gold value of length ≥ 4 (`eval/replay_egress.py`). Reporting only (a gate is the owner's decision).
   - *Per-type recall/precision*: unchanged gates and minimum n.
   - *E-STREAM-LEAK* (new, **gate**): simulated model outputs (the protected text, plus raw gold
     values the model "echoes", plus aliases) are streamed through the gateway restorer and output
     gate with ≥ 5 random chunkings each. **Gate: for every document and chunking, the client-visible
     streamed output is identical to the buffered output** (0 stream-only leaks, 0 alias fragments).
     Purposes classed RESTRICTED are buffered by policy and are measured anyway.
   - *Egress invariant false-alarm rate*: invariant fires on replayed `test2` traffic where no detected
     value is actually present. Gate: 0. It is never reported as a recall metric.
   - *False-positive rate on negatives*: items with ≥ 1 detection / negative items. Reporting only
     (type precision gates still apply).
   - *E-NER*: per candidate model, PERSON recall and precision on `ner_test2`, and CPU latency
     p50/p95 per 1 KB. Model selection uses `dev2` only.
   - *Latency*: detection p50/p95/p99 with and without NER; gate DESIGN N-1.
4. `test2` is read once, by `eval.run_det --one-shot-test`, on a clean commit.

### Amendment 2026-09-30c — R2 held-out configurations (registered before any test2/ner_test2 read)

Model selection was done on dev splits only (`eval/results/ner_*`). Three fixed configurations are
evaluated once each; no code or threshold changes between them:
- **Config A** — D0 views + D1/D2 + roster EDM/D3 + cues + D5 (no NER). Commit recorded in the lock.
- **Config B** — Config A + D4 `en_core_web_lg` (spaCy 3.8.0) with the name-shape filter.
  On dev2 it lowers leak (0.58 % → 0.44 %) but PERSON precision is 0.824 (< 0.85 gate): a
  security/utility trade the owner must decide; both are reported.
- **Config C** — Config A + `en_core_web_trf`, on `ner_test2` only, as a **reference ceiling**
  (not deployable inline: ~1.5 s/KB p95 CPU).
E-STREAM-LEAK runs once on `test2` with Config A detectors (the output gate does not use NER).
Held-out adversarial kinds run inside the Config A and B `test2` runs.

## Amendment 2026-10-01 — Release 3: corpus v3 and the PD-13 experiment (registered before v3 exists)

Corpus rules are in `eval/v3/SPEC.md`, `eval/v3/CONTAMINATION.md` and `eval/v3/SEPARATION.md`. No existing gate is relaxed.

### Arms (all three run from one commit; spaCy `en_core_web_lg` NER and the roster are identical across arms)

- **A — current architecture:** R2 Config B as of `ddd6e3f` (D0+D1+D2+cues+D3+D4+D5).
- **B — PD-13 minimum:**
  - L0 normalisation views;
  - L1 record parsing with standard parsers;
  - L2 context-free candidates (existing validators, entropy, roster EDM, NER);
  - L3 deterministic field semantics (standard dictionaries + column statistics);
  - L4 release policy (RESTRICTED default-deny for shape-flagged values in person-bearing records);
  - D5 propagation.
  - D1 is removed.
  - No general-purpose LLM. No ML beyond the existing NER, because the minimum experiment omits the ML key mapper and the prose classifier (PD-13 L5).
- **C — B without L3.**

### Metrics

| ID | Definition |
|---|---|
| **CSVE** (COMPLETE-SENSITIVE-VALUE-EXPOSURE), primary | % of documents in which ≥ 1 gold taxonomy value of ≥ 4 characters reaches the outbound body **complete**. Complete means one of two things: (a) the gold surface appears verbatim, or (b) for digit-bearing types (AADHAAR, PAN, BANK_ACCOUNT, CARD, PHONE, EMPLOYEE_ID, COMPENSATION, DOB) its digit sequence appears with only `[ \-./ ]` between digits; for other types its case-folded, whitespace-collapsed form appears |
| CSVE-HIGH | CSVE restricted to AADHAAR, PAN, BANK_ACCOUNT, CARD, SECRET, DATE_OF_BIRTH, COMPENSATION |
| PIE (partial-identifying exposure) | % of documents where a HIGH value is not complete but has ≥ 8 digits of a ≥ 12-digit identifier, or ≥ 50 % of a SECRET's characters, outbound contiguously. Reported only |
| CSVE-OOT | CSVE over OOT spans. Reported only, never gated |
| Character leak | As E-DET (gate ≤ 1 %) |
| Entity recall / precision | Per type, overlap definition of `eval/run_det.py`, with Wilson CIs |
| False positives | Negative items with ≥ 1 detection, and FP spans per 1,000 negative characters |
| Collateral masking | Masked non-gold characters / all non-gold characters, positive items (utility proxy) |
| Adversarial | CSVE and character leak per transform kind, split into known and held-out |
| Latency | Detection p95, serial, single process, same machine for all arms |
| Generalisation gap | CSVE(test3) / CSVE(dev3), Laplace-smoothed ((x+1)/(n+1)), per arm; also by family |
| L3 transfer | ΔFP = FP(C) − FP(B) and ΔCSVE = CSVE(C) − CSVE(B), on dev3 and test3 |
| Key novelty strata | All metrics on test3 split by whether a span's field key occurs in v1/v2 (CONTAMINATION C8) |

Uncertainty: 95 % CIs on test3 come from a **family-cluster bootstrap** (10,000 resamples of the 18 test families, seed 0). Per-family results are always reported.

### Gates (evaluated once, on test3)

| Gate | Condition | Decides |
|---|---|---|
| G1 | CSVE(B) ≤ 0.5 × CSVE(A) **and** the bootstrap CI upper bound of CSVE(B) − CSVE(A) is < 0 | B beats A on unseen families |
| G2 | Negative items with FP: B ≤ 1.5 × A. Collateral masking: B ≤ 2 × A and ≤ 5 % | Precision/utility cost acceptable |
| G3 | Generalisation gap of B ≤ 2.0 | Not overfit to dev3 |
| G4 | For every held-out adversarial kind: CSVE(B) ≤ CSVE(A) + 2 pp | No new evasion weakness |
| G5 | p95 detection latency of B ≤ 250 ms (N-1) | Performance |
| G6 (L3 verdict) | **L3 generalises** iff (a) \|CSVE(C) − CSVE(B)\| ≤ 2 pp on test3 (recall does not depend on vocabulary) **and** (b) ΔFP(test3) ≥ 0.5 × ΔFP(dev3) > 0 (its precision benefit transfers). If (a) fails, PD-13's central claim is **refuted** even if G1 passes. If (b) fails with ΔFP(dev3) > 0, L3 is vocabulary overfitting | Whether L3 is worth keeping |

**PD-13 is adopted only if G1–G6 all pass.** Passing them is **not** readiness. Readiness for real HR data still requires every absolute gate in this plan, plus the following added gate, which is stricter and loosens nothing:
- CSVE-HIGH ≤ 1 % on test3 with bootstrap upper bound ≤ 2 %;
- character leak ≤ 1 %;
- E-STREAM-LEAK;
- E-UTIL;
- E-DET-R.

## Amendment 2026-10-02 — R4 product validation: E-UTIL protocol (registered before any task is authored)

The detection/corpus iteration stops at R4 (owner instruction). E-UTIL measures **usefulness**, not leakage.

- **Data:** a purpose-built synthetic task suite (`eval/eutil/tasks.jsonl`, 40 items, 8 ESU task types × 5). It is written by an agent that does not read `pgw/`, and every item carries deterministic gold.
- **Conditions:**
  - **RAW:** no protection; allowed only because the data is synthetic.
  - **GATEWAY:** the product path exactly as shipped: `pgw.pipeline.Protector` with `policies/hris-default.yaml`, the task's registered purpose, and the suite roster. **No NER**, because `create_app` does not wire it. Blocking applies.
  - **MAX:** maximum-protection transformation. Everything detected is tokenised and nothing is blocked; additionally, every remaining digit-bearing token and every non-initial capitalised token outside a fixed stop-list is aliased.
  - Aliased conditions get the same one-line system note that the aliases are placeholders to keep verbatim.
- **External model:** no provider API key exists in this environment. The external LLM is a Claude Sonnet subagent with no repository access, which sees only the outbound prompt. This is valid for utility. It is **not** valid for provider latency: only gateway-added latency is reported.
- **Metrics (deterministic first):**
  - task correctness (label, JSON field, numeric and fact checks);
  - preservation of required entities after restore;
  - factual equivalence vs the RAW output (paired);
  - completeness (required sections; length ratio);
  - unnecessary masking (task-critical non-sensitive values aliased; unrestored or fabricated aliases);
  - blocked rate;
  - sensitive values present in the outbound prompt;
  - gateway latency (protect + restore).
- **Human review:** a side-by-side packet (`eval/eutil/REVIEW_CASES.md`) for the owner. An LLM judge is not used for scoring.
- **Gate:** the existing E-UTIL gate (≤ 5 % degradation vs RAW, per task type) is applied unchanged and reported per condition.
