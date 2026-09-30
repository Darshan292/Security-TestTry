# Security Evaluation — Release 2

- **Date:** 2026-09-30 · **Code under test:** `4a6a75e` (every lock in `eval/results/LOCK_*.json` records it)
- **Corpus:** v2, frozen before any R2 fix was measured on it (`9dd6e58`; author never saw the detector code).
  test2 sha256 `19505614b6405f91…`, ner_test2 `1820b120cf4f0086…`. The R1 TEST split was not reused.
- **Protocol:** EVALUATION_PLAN amendments 2026-09-30b/c, registered before any held-out read. Each
  held-out run happened exactly once, per pre-declared configuration.
- **Frozen results:** `eval/results/FROZEN_R2_TEST2_4a6a75e.json`, `eval/results/R2_*.md`.

## Verdict

**Not secure enough for real HRIS data. It fails the pre-registered character-leak gate on held-out data by 6–8×.**
R2 fixed every R1 defect it targeted, but fresh templates exposed the same structural weakness:
rule-based detection does not generalise to formats it has not seen.

## Headline (test2, held out)

| Metric | Gate | Config A (rules + roster) | **Config B (A + spaCy `en_core_web_lg`)** |
|---|---|---:|---:|
| Character leak | ≤ 1 % | 8.24 % | **6.10 %** |
| Documents with ≥ 1 raw value sent | none | 30.1 % | **20.1 %** |
| E-STREAM-LEAK (streamed == buffered) | 0 differing docs | 9/1,755 differ, 5 stream-only fragments. **FAIL** → RESTRICTED stays buffered | same |
| Egress invariant false alarms (clean test2) | 0 | **0** | **0** |
| False positives on negatives | reporting only | 43/420 (SECRET 42) | 64/420 (+PERSON) |
| Detection latency p50/p95/p99 | N-1: p95 ≤ 250 ms | 3.3 / 9.2 / 31 ms | 16 / 47 / 132 ms |
| E-UTIL | ≤ 5 % degradation | **not run** (no API key) | **not run** |

Per type on test2 (A and B identical except PERSON): recall AADHAAR **0.789**, DOB **0.756**,
SECRET **0.848**, COMPENSATION **0.927**, BANK_ACCOUNT **0.940**; CARD, EMAIL, EMPLOYEE_ID, IFSC,
PAN, PHONE, UPI_ID **1.000**. PERSON 0.828 (A) → **0.943, precision 0.940** (B).

**External names (ner_test2: 1,500 names, none in the roster):** A **0.133** · B **0.794** · C (`en_core_web_trf`,
reference only, ~1.5 s/KB) 0.844. Character leak on that split: 51.5 % / 12.9 % / 10.6 %.

## DEV vs TEST: the overfitting, measured

| | dev2 (tuned) | test2 (held out) |
|---|---:|---:|
| Char leak, Config A | 0.58 % | **8.24 %** |
| Doc exposure, Config A | 4.97 % | **30.1 %** |
| External-name recall, rules only | 0.992 | **0.133** (ner_test2) |
| PERSON precision, Config B | 0.824 (fails gate) | 0.940 (passes) |

## Adversarial (test2, character leak)

| Transform | Known to fixers? | A | B |
|---|---|---:|---:|
| homoglyph / zero-width / spaced | yes | 8.3 / 8.3 / 9.7 % | 6.3 / 6.3 / 6.4 % |
| obfuscated email / NBSP / case | yes | 7.8 / 9.0 / 10.5 % | 5.8 / 6.9 / 10.2 % |
| native digits / mixed separators | **held out** | 8.2 / 9.1 % | 6.1 / 7.1 % |
| spoken email | **held out** | 12.3 % | 10.2 % |
| OCR confusion (O↔0, l↔1) | **held out** | 42.6 % | 40.5 % |
| HTML entities | **held out** | 52.5 % | 52.4 % |
| leetspeak names | **held out** | 24.5 % | 24.2 % |
| line-wrapped values | **held out** | **73.5 %** | **70.6 %** |

The known R1 evasions are now close to the clean baseline: spaced text went from 38 % leak in R1 to ≈ baseline.
Four of the seven held-out evasions defeat the system.
Under held-out transforms the egress invariant also fires heavily (line-wrap 248–281, HTML 42–48, spoken
email 17–25 fires). Its true/false split for these is unreliable, because the sweep is case-insensitive and
the classification is not, so they are reported as **availability risk**, not as catches.

## What R2 fixed (each linked to evidence)

| R1 weakness | Fix | Evidence |
|---|---|---|
| W4 streaming gate lost context; W5 long-token alias splits | Whole-output gate, delimiter-safe cuts, whole-line release, straddle guard, name hold-back; **RESTRICTED buffered by policy** | Regressions fail on R1 code (194/200, 100/100) and pass on R2; E-STREAM-LEAK 9/1,755 residual |
| W9 egress false alarms | Alias tokens blanked before the sweep; TSV header guard | test2 clean: 0 false alarms (R1: 8) |
| W2 evasion | D0 views (despace, de-obfuscate, digit-join, case) with composed offset maps (property-tested) | Known transforms ≈ baseline on test2 |
| W3 structure | Field-concept classifier, structural Aadhaar escalation, URI/query/provider secrets, pipe/SQL/legend tables, IFSC-line accounts, multi-currency | R1-TEST-as-dev AADHAAR 0.918 → 0.991, SECRET 0.916 → 1.000; test2 still misses unseen formats |
| W1 external names | D4 NER (spaCy `lg`, name-shape filter) | ner_test2 external recall 0.133 → 0.794 |
| T-18 DoS | `_resolve` made linear | 4,000 k:v lines 883 → 252 ms; perf regression test |
| Roster EDM in JSON; tenant ID formats | Punctuation-aware tokens; ID shapes learned from the roster | dev2 roster recall 0.886 → 1.000; test2 roster 0.998, EMPLOYEE_ID 1.000 |

## Remaining security failures (ranked)

1. **Unseen document formats** (test2): positional flat files, SAP infotype dumps, labelled paper forms,
   vCards → AADHAAR 0.789, DOB 0.756. Context-dependent typing does not generalise.
2. **Held-out evasions:** line-wrapped values (≈ 71–74 %), HTML entities (≈ 52 %), OCR confusion (≈ 41–43 %),
   leetspeak names (≈ 24 %).
3. **External person names:** best deployable model gives 0.794 recall (20 % of unseen names leak);
   GLiNER could not be tested (huggingface.co blocked).
4. **Secrets in new places:** webhook tokens in URL paths, `signature key=`, credentials pasted in chat (SECRET 0.848).
5. **Incremental streaming** fails its gate. It is safe only because RESTRICTED purposes are buffered.
6. **Unmeasured:** E-UTIL (business usefulness), real-data recall (E-DET-R), end-to-end latency.

| W | Weakness (R2) | Evidence |
|---|---|---|
| W10 | Unseen document formats defeat context-dependent typing | M-23, M-30 |
| W11 | Held-out evasions (line wrap, HTML entities, OCR, leetspeak) | M-27 |
| W12 | External names: 0.794 recall at best deployable | M-24 |
| W13 | Secrets in new places (webhook paths, signature keys, chat pastes) | M-23 |

## Readiness

**Not ready for a controlled pilot with real HR data, even inside a client boundary.** It fails the
pre-registered leak gate by 6–8× on held-out synthetic data, and 20 % of documents would still
send at least one raw value. It **is** usable for a controlled **synthetic-data** pilot (Config B,
buffered output), to measure E-UTIL and integration behaviour. No gate was revised.

## The single highest-priority problem

**Recall on unseen formats for high-risk values.** Every release so far adds rules for the formats it has
seen, and every fresh held-out set brings new formats that leak. The fix has to stop depending on
recognising the format: under RESTRICTED purposes, mask **by value shape regardless of context**. That
means any Verhoeff-valid 12-digit number, any Luhn-valid card-length number, any date in a person-bearing
document (generalised to month/year), and any high-entropy token, accepting the precision cost (Q-05).
It must then be measured on a *third* fresh split.
