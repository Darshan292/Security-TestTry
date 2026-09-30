# Algorithm Analysis

- **Date:** 2026-09-30 · **Scheme:** `research/PROVENANCE.md` · **Measurements:** `M-NN` in EVIDENCE_MATRIX.md
- Scope: every technique the source report (S-01) proposes or that we built, with a verdict
  grounded in our measurements where they exist.

## Summary

| ID | Algorithm | Role | Status in code (`a51c22d`) | Verdict | Strongest evidence |
|---|---|---|---|---|---|
| A-01 | NFKC + zero-width strip + confusables fold, with offset map | D0 normalisation | Built: `K-normalize` | Keep; **insufficient** | M-13 (evasion leak 4–38 %) |
| A-02 | Regex + checksum recognizers (Verhoeff, Luhn, PAN structure) | D2 structured IDs | Built: `K-recognizers` | Keep | M-03 (structured types 0.92–1.00 on TEST) |
| A-03 | Context-keyword windows | Precision gate for weak patterns | Built | Keep; brittle on unseen phrasing | M-05, M-06 (AADHAAR/SECRET TEST misses) |
| A-04 | Field-name context detection (JSON/XML/YAML/CSV/TSV keys → type, type-validated values) | D1 | Built: `detect_field_context` | Keep; **lexicon-bound** | M-03 vs M-05 (DEV 1.000 → TEST 0.918 on an unseen key) |
| A-05 | Keyed-hash exact-data matching against the roster (HMAC dictionary, n-gram candidates, per-type shape check) | D3 | Built: `K-edm.RosterIndex` | **Adopt as primary PERSON control** | M-08 (roster recall 1.000, n = 5,766) |
| A-06 | Keyed-hash restore ledger per conversation | D3b (ADR-0010) | Built: `K-edm.RestoreLedger` | Keep | X-test_t21 (unit + gateway) only |
| A-07 | Known-value propagation (D5), including partial-name tokens | Coverage multiplier | Built: `K-pipeline` | Keep | M-04 (PERSON 0.922 TEST); not isolated by ablation |
| A-08 | Heuristic person cues (honorific, greeting, sign-off, preposition + capitalised tokens) | Weak PERSON detector | Built | **Replace with NER**; drop the preposition cue | M-11 (66 % of TEST negatives flagged, 258 PERSON) |
| A-09 | Greedy overlap resolution (score, then length) | Fusion | Built | Adequate for Phase 1 | — |
| A-10 | Scoped HMAC aliases (HKDF per scope; digit-first Crockford suffix, ~28.3 bits) | What the model sees | Built: `K-alias` | Keep (format amended) | M-12 (166 DEV items corrupted before the fix) |
| A-11 | AES-SIV deterministic encryption | Durable vault at rest (Phase 3) | Not built | Keep for Phase 3 | C-25 (CLAIM-S API, S-62; FACT-P version, S-63) |
| A-12 | Format-preserving encryption (FF1; FF3/FF3-1) | Format-strict IDs | Not built | **Reject for Phase 1–3** | C-24 (CLAIM-S, S-37) |
| A-13 | Aho-Corasick sweep of outbound bytes (egress invariant) | Bug oracle | Built: `K-egress` | Keep; **not a recall control**; fix alias-text false alarms | M-09 (0 true catches, 8 false alarms on TEST) |
| A-14 | Bounded hold-back streaming restore | Restore under SSE | Built: `K-stream` | Keep; **fix long-token cut** | M-14 (1,213/1,240; W5) |
| A-15 | Output gate on aliased text (fabrication, D2, EDM) | Response DLP | Built | Keep; **fix streaming context** | W4 |
| A-16 | Generalisation (compensation bands, DOB decade) | Utility-preserving transform | Built: `K-policy` | Keep | Unit tests only; utility unmeasured |
| A-17 | NER: GLiNER family | Contextual PERSON / free text | **Not built** | Phase 2, the next detection step | M-07 (external-name gap); C-19..C-22 (CLAIM-S) |
| A-18 | Presidio analyzer | Detector framework | **Not used** (deviation from ADR-0005) | Re-decide: Q-03 | — |
| A-19 | LLM as the detector | — | Rejected | Sends raw PII to the same trust boundary we protect | INFERENCE from S-01 §16 |
| A-20 | Learned risk fusion | Detector arbitration | Not built | Defer until ≥ 2 statistical detectors exist | — |
| A-21 | Quasi-identifier / k-anonymity risk scoring | Re-identification without names | Not built | Research item | DESIGN §9.2 (INFERENCE) |
| A-22 | Confidential computing / TEE inference | Exact-value tasks | Not built | Out of scope; route to an in-boundary model instead | S-01 §6.7 (CLAIM-S, via S-22) |

## Notes per algorithm (only where the measurement changes the picture)

**A-01 D0 normalisation.** It handles zero-width characters and common homoglyphs (TEST leak 5.2 % and
4.2 % vs 2.6 % clean), but it has **no** handling for spaced letters (38 %), `[at]`/`[dot]` email
obfuscation (14 %), or digit groups separated by spaces or NBSP (5.6 %). Case folding is present
in EDM but not in cue-based PERSON detection (5.6 %). Cheapest high-value fix in the whole system
(W2). Any de-spacing must keep the offset map exact, or the transform step will corrupt text.

**A-02 / A-03 checksums and context.** Verhoeff accepts about 10 % of random 12-digit strings
(KNOWN, ENGINEERING_FACTS). Our generator's order ids pass at about 12 % (FACT-M, `eval/synth` test),
which is consistent. So checksum plus context is necessary, and it fails when context sits outside the
window (TSV column far from the header; `nationalId`). The design alternative (Q-05) is to escalate any
Verhoeff-valid 12-digit number in HRIS-shaped payloads by *policy* rather than by keyword.
That trades precision for recall on the one type where a miss is most costly.

**A-04 field-name context.** It was the largest single improvement (DEV leak 26 % → 2.1 %). It is also
the clearest overfitting surface: DEV 1.000 on every structured type, TEST shows unseen keys
(`nationalId`) and credential-in-URI shapes leaking. The lexicon should come from vendor schemas
(Workday/UKG field catalogues, Q-06), not from the corpus.

**A-05 roster EDM.** This is the result that answers the external review's "PERSON ≥ 0.90 is not
sufficient". For employee names, deterministic matching against an HMAC dictionary reaches 1.000
recall with zero statistical risk, and the dictionary holds no plaintext. Limits: it needs roster sync
(F-11, planned Phase 4, which should move earlier), names absent from the roster (candidates, family,
vendors: M-07), and nicknames and transliterations (untested).

**A-08 cues.** A cheap stop-gap that costs precision on unseen templates (policy titles such as
"Leave and Expense Policy"). Remove the preposition cue once A-17 exists.

**A-10 aliases.** The v1 grammar (six chars `[0-9A-Z]`) collided with English identifiers.
The amended grammar (digit + Crockford, which excludes I/L/O/U) prevents that by construction.
Collision risk at 200 same-type entities ≈ 6×10⁻⁵, detected and resolved deterministically.
Whether models copy `[PERSON_3QX2MA]` faithfully is **unmeasured** (E-FMT, Q-01).

**A-13 egress invariant.** Zero true catches, because it only knows detected values (T-20). It also
raised **8 false alarms on TEST**: the casefolded sweep matched detected values ("date", "ifsc") *inside
our own alias tokens*, which were themselves triggered by a TSV header mis-detection (W9). Its value is against *bugs*
(offset errors, retries, unprocessed fields), which the gateway tests exercise. It must never be
reported as a leak-prevention metric.

**A-14 / A-15 streaming.** Vendors either buffer the whole response (Cloudflare DLP, C-01 FACT-P)
or re-chunk tags (SAP, C-04 FACT-P, which states that small chunks reduce unmasking accuracy). Our bounded
hold-back is more ambitious, and TEST found both of its sharp edges (W4, W5). Fixes are known and
local; after them, E-STREAM must be re-run on a fresh split.

**A-17 NER.** External evidence is all CLAIM-S (C-19..C-22). knowledgator gliner-pii F1 75.5–83.3 (model cards,
benchmark unidentified). urchade gliner_multi_pii-v1 F1 ≈ 0.38 on a third-party benchmark.
About 125 ms per 500 B on CPU (an issue report). None of it is on HR text or Indian names. Adopt only
behind a measurement on a fresh split, with the detection cache (ADR-0005) to keep long histories linear.

**A-18 Presidio.** ADR-0005 planned Presidio as a D2/D4 host. The slice implemented its own
recognizers (India-first, stdlib-only) and does not import Presidio. This is an unrecorded
deviation. Q-03 asks whether Presidio adds anything beyond hosting NER. If not, drop it from
the design and remove the supply-chain surface (T-11).
