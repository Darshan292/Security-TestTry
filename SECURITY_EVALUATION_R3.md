# Security Evaluation — Release 3 (corpus v3, PD-13 experiment)

- **Date:** 2026-10-01 · **Code under test:** `ff30928` (all three arms; locks in `eval/results/LOCK_v3_?_all_test3.json`)
- **Corpus:** v3, frozen at `7371bd6`. test3 is 1,350 documents from 18 held-out families: 1,080 positive and 270 negative. sha256 `9c73fee1…`.
- **Protocol:** EVALUATION_PLAN amendment 2026-10-01; one shot per arm, all from one commit. Results were frozen at `717521d` before any analysis.
- **Integrity deviations:** D-1 to D-4 in `eval/v3/CONTAMINATION.md`. No test3 metric was seen before the freeze.
  - The aborted (D-3) and restart-interrupted (D-4) attempts were rerun from the identical commit.
  - The interrupted clean outputs for A and B were set aside unopened. After the freeze they were compared with the reruns and are **identical** (latency excepted).

## Verdict

**PD-13 is not adopted: it fails G1, G2, G3, G5 and G6 and passes only G4.** It is clearly better than the current architecture: CSVE 0.361 vs 0.527, a family-bootstrap difference with 95 % CI −0.261 to −0.084. But it misses the pre-registered margin, over-masks, overfits its dev split, and L3 did not generalise. **Nothing here is a readiness claim.** Both arms expose a complete sensitive value in more than a third of held-out documents.

## 1. A / B / C results (test3, clean)

| Metric | A current | B PD-13 | C PD-13 − L3 |
|---|---:|---:|---:|
| **CSVE** (docs with ≥ 1 complete value) | 0.527 | **0.361** | 0.382 |
| CSVE-HIGH | 0.312 | **0.110** | 0.114 |
| Character leak (micro / family-macro) | 0.241 / 0.270 | **0.107 / 0.152** | 0.113 / 0.162 |
| PIE (partial identifying) | 0.000 | 0.000 | 0.000 |
| CSVE-OOT (out-of-taxonomy IDs; not gated) | 0.891 | 0.378 | 0.382 |
| Negative items with FP | **0.389** | 0.559 | 0.537 |
| FP spans / 1k negative chars | 2.27 | 1.68 | 1.65 |
| Collateral masking | **2.0 %** | 6.6 % | 7.0 % |

Per-value exposure by key stratum:

| Key stratum | A | B | C |
|---|---:|---:|---:|
| Key seen in v1/v2 | 20.7 % | 4.3 % | 4.5 % |
| **Key unseen in v1/v2** | **40.6 %** | **6.7 %** | 8.0 % |
| No key (prose, speech) | 27.4 % | 19.4 % | 19.6 % |

## 2. Gates

| Gate | Result | Numbers |
|---|---|---|
| G1 (B ≤ 0.5 × A and CI < 0) | **FAIL** | 0.361 / 0.527 = 0.68×; CI of B − A [−0.261, −0.084], so B is better but short of the margin |
| G2 (negative FP ≤ 1.5× A; collateral ≤ 2× A and ≤ 5 %) | **FAIL** | negative FP 1.44× (passes); collateral 6.6 % vs 4.1 % allowed |
| G3 (dev→test gap of B ≤ 2) | **FAIL** | B 4.23 (dev3build 0.084 → 0.361); A 1.32; C 1.71 |
| G4 (held-out kinds: B ≤ A + 2 pp) | **PASS** | 14/14 applicable kinds; `script_rendering` applies to no test3 item |
| G5 (p95 ≤ 250 ms) | **FAIL** | B 259 ms (A 226) |
| G6 (L3 generalises) | **FAIL** | (a) C − B = +2.15 pp > 2 pp; (b) ΔFP test −2.2 pp vs dev +5.0 pp, so L3's precision benefit reversed |

## 3. Per family (CSVE A / B / C; negative items with FP A / B / C, out of 15)

| Family | CSVE | Negative FP |
|---|---|---|
| s_tpa_claims_mis_export | 0.75 / **0.09** / 0.25 | 7 / 9 / 2 |
| s_etds_24q_caret_return | 0.77 / **0.28** / 0.43 | 1 / 2 / 1 |
| i_openapi_spec_with_examples | 0.35 / **0.08** / 0.13 | 15 / 15 / 15 |
| i_scim_provisioning_exchange | 0.16 / **0.01** / 0.04 | 2 / 10 / 10 |
| i_jupyter_attrition_notebook | 0.59 / **0.23** / 0.23 | 13 / 3 / 1 |
| s_jobportal_resume_export_html_xls | 0.79 / **0.37** / 0.37 | 5 / 3 / 3 |
| s_ics_hr_calendar_feed | 0.63 / 0.40 / 0.40 | 6 / **15** / 15 |
| i_har_capture_payroll_portal | 0.37 / 0.25 / 0.25 | 15 / 15 / 15 |
| s_lineprinter_payroll_register | 0.80 / 0.67 / 0.68 | 3 / 3 / 3 |
| s_mt940_salary_reconciliation | 0.79 / 0.75 / 0.75 | 0 / **12** / 12 |
| c_epf_nomination_form_ocr | 0.47 / 0.39 / 0.37 | 0 / 0 / 0 |
| c_domestic_enquiry_proceedings | 0.32 / 0.27 / 0.27 | 9 / 9 / 9 |
| c_helpline_call_asr_codemixed | 0.75 / 0.72 / 0.72 | 10 / 12 / 12 |
| c_voicemail_transcription_notice | 0.64 / 0.63 / 0.63 | 6 / 10 / 14 |
| c_bilingual_appointment_letter | 0.80 / 0.80 / 0.80 | 3 / 8 / 8 |
| c_review_meeting_vtt_transcript | 0.37 / **0.40** / 0.40 | 0 / **12** / 12 |
| i_powershell_offboarding_transcript | 0.16 / 0.16 / 0.16 | 2 / 4 / 4 |
| i_logcat_ess_attendance_app | 0.00 / 0.00 / 0.00 | 8 / 9 / 9 |

### Per entity (recall / precision; n gold)

| Type | n | A | B | C |
|---|---:|---|---|---|
| PERSON | 6,231 | 0.790 / 0.556 | 0.862 / 0.803 | 0.850 / 0.818 |
| COMPENSATION | 2,206 | 0.216 / 0.594 | **0.920** / 0.456 | 0.911 / 0.453 |
| EMPLOYEE_ID | 1,267 | 0.942 / 0.962 | 0.967 / 0.963 | 0.967 / 0.963 |
| PHONE | 854 | 0.938 / 0.888 | 0.940 / 0.772 | 0.940 / 0.888 |
| DATE_OF_BIRTH | 771 | 0.406 / 0.981 | **0.799** / 0.443 | 0.782 / 0.312 |
| EMAIL | 739 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| BANK_ACCOUNT | 465 | 0.275 / 0.842 | **0.742** / 0.315 | 0.742 / 0.287 |
| SECRET | 348 | 0.888 / 0.637 | 0.948 / **0.128** | 0.948 / 0.123 |
| IFSC | 345 | 0.809 / 0.924 | same | same |
| PAN | 294 | 1.000 / 1.000 | same | same |
| AADHAAR | 71 | 0.958 / 1.000 | **0.873** / 1.000 | 0.873 / 1.000 |
| UPI_ID | 66 | 0.955 / 1.000 | same | same |

Complete values exposed (post-hoc, `eval/results/v3_posthoc_test3.json`):

| Type | A | B |
|---|---:|---:|
| COMPENSATION | 78 % | 7.4 % |
| DATE_OF_BIRTH | 59 % | 16 % |
| BANK_ACCOUNT | 65 % | 18 % |
| PERSON | 21 % | 13 % |
| AADHAAR | 3 values | 0 |

**61 % of B's exposed documents expose only a person name** (296 of 487).

## 4. Adversarial (CSVE on transformed positive documents; clean positives: A ≈ 0.66, B ≈ 0.45)

| Kind | Class | A | B | C |
|---|---|---:|---:|---:|
| bidi_controls | held-out | 0.989 | **1.000** | 1.000 |
| url_encoding | held-out | 0.964 | 0.973 | 0.973 |
| markup_run_split | held-out | 0.970 | 0.906 | 0.973 |
| reversed_value | held-out | 0.918 | 0.902 | 0.965 |
| combining_marks | held-out | 0.981 | 0.962 | 0.990 |
| mime_encoded_word | held-out | 0.986 | 0.859 | 0.865 |
| utf8_as_cp1252 | held-out | 0.872 | 0.819 | 0.814 |
| pdf_cell_glue | held-out | 0.957 | 0.787 | 0.795 |
| amount_date_prose | held-out | 0.875 | 0.747 | 0.735 |
| json_escapes | held-out | 0.857 | 0.668 | 0.761 |
| pdf_page_break | held-out | 0.735 | 0.595 | 0.604 |
| mail_qp | held-out | 0.799 | 0.561 | 0.572 |
| sheet_coercion | held-out | 0.719 | 0.516 | 0.537 |
| compat_forms | held-out | 0.665 | 0.457 | 0.484 |
| script_rendering | held-out | n/a | n/a | n/a |
| markup_entities | known | 0.988 | 0.713 | 0.715 |
| pdf_glyph_gaps / stt_dictation / scan_glyph_errors | known | 0.97 / 0.96 / 0.94 | 0.99 / 0.98 / 0.93 | ≈ B |
| case_games / terminal_wrap | known | 0.85 / 0.80 | 0.77 / 0.61 | ≈ B |
| homoglyph / zw / unicode_spaces / digit_script | known | 0.68 / 0.69 / 0.67 / 0.67 | 0.48 / 0.48 / 0.46 / 0.46 | ≈ B |

G4 passes only because it is relative. In absolute terms, 8 of 14 held-out kinds leave B at ≥ 0.75 CSVE, and bidi controls, URL encoding, markup splits and reversed values defeat every arm almost entirely.

## 5. Streaming
**Not measured on v3.** No v3 streaming run was registered, and adding one after the fact would be an unregistered test3 read. The R2 status stands: E-STREAM-LEAK FAIL, so RESTRICTED purposes are buffered.

## 6. Latency (detection, serial, same machine)

| | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| A | 52 ms | 226 ms | 591 ms | 11.6 s |
| B | 65 ms | **259 ms** | 647 ms | **93.3 s** |
| C | 63 ms | 257 ms | 633 ms | 5.4 s |

v3 documents are long (about 3 KB on average), so A is also much slower than on test2.

**Correction (2026-10-02, R4 read-only profiling, `eval/analysis_r4/`):**
- **The maxima do not reproduce.** Run serially, every test3 item takes at most 1.19 s in every arm (A, B and C alike: the same document, sized 22 KB), and p50/p95/p99 match the table above. The 93 s, 11.6 s and 5.4 s maxima are best explained by external stalls during runs disturbed by container restarts. That is not proven, and the harness does not record which item was slowest.
- **About 80 % of detection time is spaCy.** NER runs once per D0 view (3 passes). It scales linearly at about 55 ms/KB with no input-size cap.
- **Real DoS shapes exist in shared code, used by all arms including A:** `normalize._join_digits` is quadratic on long digit runs (a 20 KB run takes 16 s), and `recognizers._cli_and_files` is quadratic on long whitespace-free tokens (20 KB takes 3 s).

## 7. Generalisation gap (CSVE, dev3build → dev3val → test3)

| Arm | dev3build | dev3val | test3 | test / dev (smoothed) |
|---|---:|---:|---:|---:|
| A | 0.398 | 0.529 | 0.527 | 1.32 |
| B | **0.084** | 0.233 | 0.361 | **4.23** |
| C | 0.223 | 0.340 | 0.382 | 1.71 |

L3 provenance on test3 (`v3_l3_provenance_test3.json`):

| Entries | Correctly typed | Typed non-gold | Typed as the wrong gold type | Wrong releases |
|---|---:|---:|---:|---:|
| Standard-sourced | 611 (19 %) | **2,684 (81 %)** | — | 0 |
| dev3-learned (generic keys) | 896 (69 %) | 277 | 122 | 9 |

On dev3build the standard-sourced entries were 85 % correct. No unfair standards advantage (D-2) is visible: the standard entries mostly *hurt* precision on test3.

## 8. Root-cause interpretation (INFERENCE from the measurements above)

1. **Inverting the default works for values with a recognisable shape, on unseen families.**
   - Exposure under keys never seen before fell from 40.6 % (A) to 6.7 % (B). A's exposure doubles from seen to unseen keys; B's barely moves.
   - COMPENSATION, DOB and BANK exposure fell by 70–90 %.
   - This part of the R2 hypothesis is confirmed.
2. **The residual is now names in unstructured, spoken, code-mixed and bilingual text.**
   - 61 % of B's exposed documents are name-only. The C-group families (ASR, voicemail, VTT, bilingual letters) barely move under any arm.
   - CSVE is per document, so one leaked name exposes the document. G1 cannot pass without a much stronger PERSON layer. spaCy `lg` is the only one available here; GLiNER is blocked.
3. **Default-deny without accurate anchoring over-masks.**
   - Precision: SECRET 0.13, BANK 0.32, DOB 0.44. Collateral masking is 6.6 %.
   - Calendar, MT940 and VTT negatives are hit hard.
   - The Q-27 risk materialised: deciding "person-bearing" at record or document level is too coarse.
4. **L3 was vocabulary overfitting.**
   - Its dev benefit (−13.9 pp CSVE, −5 pp FP) shrank to −2.1 pp CSVE on test, and its effect on false positives reversed.
   - Standard dictionaries typed far more non-gold fields than gold ones on unseen families.
   - "Field semantics from dictionaries" does not generalise in this form. The column statistics and shape layers did the useful work.
5. **B itself overfit dev3build** (gap 4.2×). The implementer's generic-but-tuned heuristics (table credibility, unwrap, fixed-width) were fitted on 12 families, and dev3val (0.233) had already warned.
6. **Evasion is unsolved.** Bidi controls, URL encoding, reversed values and markup splits defeat all arms. Structural normalisation views are still missing.

No fixes were implemented and no new experiment was started.
