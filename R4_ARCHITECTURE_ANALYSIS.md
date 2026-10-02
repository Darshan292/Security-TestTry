# R4 Architecture Analysis — before any R4 code

- **Date:** 2026-10-02 · **Inputs:** R1–R3 frozen results; read-only post-hoc analysis of the spent test3 (`eval/analysis_r4/`, aggregates only); a full repository refresh.
- **Not done:** no code, no test4, no tuning on test3.
- Every number is FACT-M from the files cited. Every recommendation is a DECISION proposal for owner review.

## 0. Step back first: are we still building the product?

The product (DESIGN.md:10-17) is a policy-controlled gateway: request → detection → protection → policy → LLM → response gate → controlled restore, with no PII at rest. Three releases have measured **one stage** of that lifecycle: offline detection recall.

The rest of Phase 1 (ROADMAP.md:13) has **never been measured**:
- E-UTIL, meaning whether the LLM is still useful on aliased text (Q-02);
- E-FMT, alias fidelity with a real model;
- restoration and the T21 ledger beyond unit tests;
- E-STREAM-T21, E-EGRESS-FUZZ, E-CANARY;
- end-to-end latency through `pgw/gateway`;
- tool and agentic flows.

The detector loop (find a format → add a rule → new corpus → new format) is the loop the owner warned about. R3 showed that it cannot converge by detection alone (§3 below). **V2 must therefore be judged by whether it removes whole classes of exposure by construction, not by whether it detects more.**

## 1. What R3 validates (with R1/R2 where they agree)

| Status | Capability | Evidence |
|---|---|---|
| **Proven** | Format validators (checksums, structural formats) generalise across unseen corpora | EMAIL 1.00, PAN 1.00, UPI 0.955, EMPLOYEE_ID 0.94–0.97 on test3 in every arm; D2 is the component whose value *grows* on held-out data (M-33) |
| **Proven** | Roster exact-match for employees | R1/R2 roster recall 1.000 (M-08); R3 EMPLOYEE_ID ≈ 0.97 |
| **Proven** | Shape-first, default-deny removes dependence on field vocabulary for shaped values | Per-value exposure under **unseen keys** 40.6 % (A) → 6.7 % (B); COMPENSATION exposure 78 % → 7 %, DOB 59 % → 16 %, BANK 65 % → 18 % (M-38) |
| **Proven** | Blind, sealed, one-shot evaluation detects overfitting that dev data hides | Dev→test gaps of 14× (R2) and 4.2× (R3 B). Determinism verified across interrupted reruns |
| Partially supported | Generic structure parsing | Large wins on structured families (claims MIS 0.75 → 0.09, 24Q 0.77 → 0.28), but its heuristics were tuned on 12 dev families (gap 4.2×) |
| Partially supported | Canonicalisation views | Known evasions ≈ clean baseline (R2 M-28; R3 known kinds ≈ B's clean 0.45). Held-out encodings and structure transforms still fail |
| Partially supported | NER for names outside the roster | 0.79 (R2) / 0.86 (R3 B), with precision 0.80; weak on non-Latin script, speech and prose |
| Partially supported | Validator-based column statistics; L3 *release* of non-personal identifiers | Releases 1.1 % wrong (9 of 812) on test3; release-to-gold exposure 26 DOB values |
| **Disproven** | Context or field-name vocabulary as a recall mechanism | D1's value halves dev→test (M-33); A's exposure doubles from seen to unseen keys (M-38) |
| **Disproven** | Standard data dictionaries as a generalising typing layer (L3) | On test3, standard entries typed **2,684 non-gold fields vs 611 gold (19 % precision)**, against 85 % on dev3build (M-39) |
| **Disproven** | A binary "person-bearing record or document" scope as the precision control | Collateral masking 6.6 %; precision SECRET 0.13, BANK 0.32, DOB 0.44 (M-37) |
| **Disproven** | Dev metrics as a predictor of held-out exposure | M-30, M-40 |
| **Disproven** (R1/R2) | Egress invariant as a leak control; incremental streaming for RESTRICTED | M-09, M-25 |
| **Unmeasured** | Utility, restoration fidelity, real-data recall, end-to-end gateway behaviour | Q-02, Q-01, Q-07, Q-17 |

## 2. Where the remaining exposure comes from (arm B, test3, clean)

Every complete-exposed gold value (1,348 values in 487 documents) was attributed once (`eval/analysis_r4/attribution_b.json`).

| Class | Values | What it is |
|---|---:|---|
| PERSON in prose | 379 | No PERSON detection fired |
| PERSON in a structured field | 215 | A name cell that no detector recognised |
| PERSON in non-Latin script | 213 | Devanagari, Bengali and similar |
| Unshaped value forms | 290 | Spoken or spelled digits ("nine eight four…": PHONE 51, EMPLOYEE_ID 27, BANK 9, IFSC 9); worded amounts; compact iCalendar dates `19900312T000000Z` (41); bare integers as salaries (58); year or day-month fragments (28); short passwords (18) |
| Anchoring/scope (candidate existed; record judged not person-bearing) | 148 | COMPENSATION 88, BANK 53 |
| Parsing/segmentation (shaped value missed in context) | 77 | IFSC 57 (glued), BANK 20 |
| L3 release of a true value | 26 | DOB in date columns judged "release-like" |
| Normalisation | 0 | Clean data, by construction; the adversarial results are in §5 |

### Counterfactual ceilings

Each class is removed as if perfectly fixed, at **zero precision cost**, which is optimistic.

| Fixed perfectly | CSVE (0.361) | CSVE-HIGH (0.110) |
|---|---:|---:|
| Stronger PERSON (all three PERSON classes) | **0.141** | **0.110 (unchanged)** |
| Value shape / reconstruction | 0.317 | **0.045** |
| Anchoring + policy (scope + L3 release) | 0.356 | 0.079 |
| Parsing / segmentation | 0.347 | 0.100 |
| Normalisation (clean data) | 0.361 | 0.110 |
| Everything except PERSON | 0.297 | **0.000** |
| Everything | 0 | 0 |

## 3. Is stronger NER the answer? No.

- **Perfect PERSON detection** takes CSVE from 0.361 to **0.141**, which is 0.27× A's 0.527. **G1's ratio would pass** (the bootstrap CI was not computed).
- **CSVE-HIGH stays at 0.110**, because PERSON is not a HIGH type. The readiness gate (CSVE-HIGH ≤ 1 %) fails **11×**, so names alone cannot make the system ready.
- **Conversely, fixing everything except PERSON** drives CSVE-HIGH to 0 but leaves CSVE at 0.297 (0.56× A, so G1 fails).
- **Both problems are independent and both are required.** The HIGH problem is not an NER problem at all: it is reconstruction (spoken or encoded values), shape coverage, anchoring and parsing.
- **"Perfect NER" is not attainable in this setting.** About 40 % of the name residual is speech transcripts, non-Latin script and code-mixed text. The product answer for those content classes has to come partly from **policy** (§7-E), not only from recall.

## 4. The "93-second document" (read-only profiling, `eval/analysis_r4/`)

| Hypothesis | Verdict | Evidence |
|---|---|---|
| A document property of B | **No** | Run serially, every test3 item takes ≤ 1.19 s in all arms. The slowest item (22 KB HAR capture) is the same in A, B and C. p50/p95/p99 reproduce R3 within 10–15 %. |
| External stall during the disturbed R3 runs | Most likely, not proven | Maxima of 5–80× any item, with an unchanged p99; container restarts in the same window |
| NER | **Dominant real cost** | 80 % of the slowest document. spaCy runs once per D0 view (3 × 22 KB). Linear (~55 ms/KB) and **unbounded**: no size cap |
| Parser complexity / L3 / memory | No | `records.parse` 0.03 s; L3 0.014 s; 69 MB peak; linear scaling |
| Regex/loop complexity | **Yes, a real DoS vector, in shared code** | `normalize._join_digits` is quadratic on digit runs (20 KB → 16 s; B pays it twice). `recognizers._cli_and_files` is quadratic on long tokens (20 KB → 3 s). Both are reachable by any caller, in arm A too |

The R3 report's latency section is corrected accordingly.

## 5. Adversarial failures: what kind of fix each needs

The ceiling is the share of transformed gold values whose canonicalised form equals the original (`adv_canon.json`). "Context" means the value itself was unchanged and the transform broke boundaries around it.

| Kind | Recovered by | Ceiling | Right layer |
|---|---|---:|---|
| bidi controls, combining marks, compatibility forms, homoglyphs, zero-width, Unicode spaces, digit scripts, case, PDF glyph gaps | Unicode canonicalisation | 96–100 % | **A. Canonicalisation** |
| bidi (RTL-reordered part), reversed values | Reversal view | 97–100 % | **A** (as a bounded view), not detector changes |
| HTML entities, URL encoding, quoted-printable, JSON escapes, MIME encoded-words, mojibake | Decoders | 97 / 58 / 78 / 6 / 0 / 0 % at value level | **A. Decoder views over the whole document with offset maps.** The low value-level numbers mean decoding must happen at document level (encoded-word boundaries, `+`-for-space, nested escapes), not per value |
| markup run splits | Tag stripping | 97 % | **B. Structure reconstruction** (text-of-markup view) |
| PDF cell glue, PDF page break, terminal wrap | Value unchanged; boundaries broken | — | **B + C:** reconstruction joins, and candidate generation that does not require token boundaries |
| speech dictation, amounts and dates in words | Not recoverable by canonicalisation | 0 % | **D. Entity reconstruction** (number-, letter- and date-word parsing; deterministic, multilingual lexicons) |
| OCR glyph errors | 0 % | — | **D:** confusion-tolerant matching against candidates, with bounded ambiguity |
| spreadsheet coercion | Partly destroyed values | — | **E. Policy:** an unrecoverable numeric value in a person record is masked as numeric |

**Conclusion:** none of these needs new type-specific detectors.
- About two-thirds of the adversarial surface is a deterministic canonicalisation/decoding problem.
- About one-sixth is structure reconstruction.
- The rest is entity reconstruction or policy.

## 6. L3: remove or redesign? Decided by the test3 provenance

| L3 part | test3 measurement | Decision |
|---|---|---|
| Dictionary **typing** from public standards | 611 correct vs 2,684 non-gold (19 %); dev3build 85 % | **Delete.** It is vocabulary overfitting (M-39) |
| Dictionary typing from dev3 keys | 896 correct, 277 non-gold, 122 other-gold (69 %) | **Delete** as a mechanism. It works only because those keys were seen; that is the D1 failure again |
| Dictionary **release** (non-personal IDs, for example trace IDs) | 803 correct, 9 wrong (1.1 %) | **Keep the idea, narrowly:** release only provably non-personal *shapes* (UUIDs, hashes, trace and message IDs). Do this by format, not by key name |
| Date-column statistics ("DOB-like vs release-like") | 26 true DOBs released | **Redesign:** generalise any date in a person record (month/year) instead of guessing which dates are birth dates |
| Validator column statistics (≥ 80 % of a column validates) | Part of the large wins on structured families | **Keep:** it relies on values, not names |

**L3 as a "field semantics" layer is removed.** Its surviving parts move into C (column-level validation) and E (release of non-personal formats). Field names never create recall and never release a value that has a sensitive shape.

## 7. Architecture V2: seven layers, with field names out of the security decision

**Principle:** every value-like token that reaches egress has to be **explained** (validated non-personal, declared by a data contract, or allowed by policy) or it is transformed. Detection improves precision and utility; it is not what keeps a value from egress.

| Layer | What it does | Det./ML | Where | Attack / failure modes | Latency | R3 evidence |
|---|---|---|---|---|---|---|
| **A. Canonicalisation** | One linear-time stage. Unicode NFKC, confusables, Cf/bidi removal, native digits. Bounded document-level decoders (HTML, URL, QP, MIME words, JSON strings, base64 depth ≤ 2, mojibake repair) and reversal and unwrap views. All of it with composed offset maps, a size cap and a time budget; when the budget is exceeded, fail closed | Deterministic | Local | Decode bombs and nesting (cap depth and size); offset-map bugs (property tests); budget exhaustion (fail closed, not open) | Few ms/KB, linear. **Replaces** the D0 view explosion (3 NER passes, quadratic digit join) | §5 ceiling; §4 quadratic paths; M-28 |
| **B. Structure reconstruction** | Standard parsers into a record graph (JSON, XML, CSV/TSV/delimited, kv, YAML-like, fixed-width, markup text). Keep every parse on ambiguity. No heuristics fitted to corpora | Deterministic | Local | Ambiguous layouts (keep all parses); adversarial nesting (caps); mis-segmentation (C must not need boundaries) | ~1.5 ms/KB measured | Structured-family wins (M-36); 77 segmentation misses; markup-split and PDF-glue kinds |
| **C. Candidate generation** | Everything value-like, independent of field names. Validators/checksums; value shapes (dates in all standard encodings including iCalendar and spreadsheet serials, amounts, long digits, entropy); spoken, spelled and worded numerals and letters; roster exact match; NER **once** on canonical prose segments only; column-level validation statistics | Deterministic + **local** ML (NER only) | Local | Shape gaps (new encodings), NER misses in non-Latin script or speech, false positives (handled in D/E), NER cost (one pass, prose only, size cap) | Validators ~2 ms/KB; NER ~17 ms/KB per pass, prose only | Unshaped forms 290 values (HIGH 0.110 → 0.045 if covered); validators generalise (M-33) |
| **D. Entity/value resolution** | Merge candidates across views and occurrences. Resolve type and confidence. Link values to persons and records (anchoring by co-occurrence in the record graph, not by a document-wide binary flag). Propagation (D5); restore-ledger matching (D3b); confusion-tolerant OCR matching | Deterministic | Local | Wrong anchoring (over- or under-masking); type confusion (it matters less, because E masks unknown types) | ms | Anchoring 148 values (HIGH → 0.079); precision collapse (M-37) |
| **E. Risk policy** | Signed decision tables over (purpose, data class, type and confidence, anchoring, content class). **Positive model:** for registered first-party apps, a **data contract** declares fields and purposes; undeclared value-like content defaults to transform. RESTRICTED: unexplained candidates in person context are transformed; dates generalised; destroyed values masked. Content classes that cannot be protected reliably (speech transcripts, non-Latin free text under RESTRICTED) go to **route-local / summarise-locally / refuse** per purpose | Deterministic | Local | Over-masking that kills utility (measure E-UTIL); policy misconfiguration (simulator + tests, ADR-0006); contract drift (validate payloads against contracts) | < 1 ms | Name residual 61 % of exposed documents (M-41) and NER not perfectible (§3); default-deny precision lessons (M-37) |
| **F. Transformation / tokenisation** | Scoped HMAC aliases (ADR-0002), generalisation, redaction; per-request restore map; `never_restore`; restore ledger | Deterministic | Local | Alias collisions (amended grammar); the model fabricating aliases; restore to the wrong caller | ms | ADR-0002/0003/0010 (unit level only; E-FMT and restoration unmeasured) |
| **G. Egress enforcement and response gate** | Canonical serialisation, then the invariant sweep **on A-canonicalised outbound bytes** (closing the case-insensitivity mismatch, M-27); content-free ledger; fail closed. On response: output gate, buffered for RESTRICTED, then controlled restore | Deterministic | Local; the LLM is the only external hop | A bug-only control (not recall); availability (false alarms); stream splitting | Linear, ms | M-09, M-26, M-25 |

ML stays local and is confined to C (NER). No general-purpose LLM is used in the protection path.

### Delete rather than improve

| Delete | Why (evidence) |
|---|---|
| D1 field-context detection (keyword and key lists, ~2,000 lines in `recognizers.py`) | Overfits: M-33, M-38. Recall must not depend on field names |
| L3 dictionary typing, standard and dev-learned (`pd13/dictionary.json` typing entries) | 19 % precision on unseen families (M-39) |
| Date-column "DOB vs release" guessing | 26 true DOBs released; generalise instead |
| PD-13 corpus-tuned heuristics (table credibility, unwrap and fixed-width thresholds) | Gap 4.2× (M-40); replace with standard parsers plus boundary-free candidates |
| D0 multi-view × detector cross-product (`despaced`, `digits_joined`, `casefold_titled` running all detectors and NER per view) | 3× NER cost and a quadratic digit join (§4); subsumed by one canonical stage |
| Person-cue regexes (honorific, greeting, preposition) | Marginal recall (+0.1 pp test2), high FP on dev2 (M-33); NER plus anchoring covers this |
| Binary person-bearing scope in PD-13 L4 | Replaced by graded anchoring (D) and contracts (E) |

**Keep, unchanged in principle:** validators, roster EDM, aliases, restore ledger, buffered RESTRICTED output, egress ledger, fail-closed matrix.

## 8. The three highest-priority engineering problems

1. **One bounded canonicalisation and decoding stage (layer A), in linear time with fail-closed budgets.**
   - Capability: protection independent of representation and encoding. It also closes the quadratic DoS paths and the 3× NER cost.
   - Why it generalises: it targets encodings defined by standards (Unicode, RFC 2047/2045, URL, HTML, JSON), not the examples that exposed them.
   - Its ceiling covers about two-thirds of the adversarial surface (§5).
2. **Recall that depends on neither field names nor NER (layers C/D, plus E for what cannot be recalled).**
   - Capability: reconstruction of spoken, spelled and worded values, every standard date encoding, and candidate generation that needs no token boundaries over the reconstructed structure.
   - Ceiling: CSVE-HIGH 0.110 → 0.045 from value forms alone, and 0 together with anchoring and parsing (§2).
   - For content classes where names cannot be recalled reliably (speech, non-Latin free text), the answer is **policy** (route-local, local summarisation, refusal), not a bigger model.
3. **Precision without vocabulary (layers D/E).**
   - Capability: graded record anchoring plus **first-party data contracts** (a positive security model: apps declare fields; anything undeclared and value-like is transformed).
   - Without this, default-deny is unusable: collateral masking 6.6 %, SECRET precision 0.13.
   - Why it generalises: contracts are owned by the integrating app, not inferred from text.

**Outside detection, and overdue:** run the product path end to end (gateway → restore → stream → ledger → egress fuzz). None of Phase 1's non-detection exit criteria has a result.

## 9. The one experiment to run before implementing V2

**E-UTIL at the protection frontier.** This is dev3 data only, with no new corpus.
- **Tasks:** realistic HR/ERP tasks on dev3 documents (summarise, classify, draft a reply, extract non-sensitive facts).
- **Conditions, sent to the target external model:**
  1. raw (synthetic, so allowed; this is the utility ceiling);
  2. arm A protection;
  3. arm B protection;
  4. **maximal protection:** every value-like candidate transformed and dates generalised. This is the V2 default-transform posture.
- **Scoring:** automatic field checks plus a blinded judge.
- **Gates, to be pre-registered:** utility retained relative to raw. Gates must be fixed before the run.

**Why this experiment and not another detector test:** it decides V2's central design trade-off.
- If maximal protection keeps acceptable utility, V2 adopts **default-transform**: every unexplained value-like token is aliased. Precision work in D/E then becomes an optimisation, and the CSVE problem reduces to recall of *value-like* tokens, which §2 shows is tractable.
- If utility collapses, precision is the product's critical path, and D/E get the design budget before anything else.

E-UTIL has been unmeasured for three releases. It needs an **API key for the target provider (owner action)**. The harness `eval/run_util.py` exists.
