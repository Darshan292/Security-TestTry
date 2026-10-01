# Root-Cause and Architecture Analysis — Release 2 leakage

- **Date:** 2026-10-01 · **Code analysed:** `40d3303` + measurement-only ablation switches (`pgw/ablation.py`; all-on reproduces the frozen numbers exactly)
- **Status:** analysis only. **No detector was added and PD-10 was not implemented.** This is not a security-readiness statement: the system still fails its leak gate (M-21).
- **Reproduce:** `python -m eval.root_cause` → `eval/results/root_cause_r2.json` (no raw values written) · `python -m eval.run_ablation` → `eval/results/ablation_r2.{json,md}`

## 0. Data-integrity statement (read first)

| Data | Role here | Integrity consequence |
|---|---|---|
| test2 / ner_test2 (spent, R2) | Diagnosis only: attribution, ablation, shape ceiling | **No parameter was tuned on them.** But this document is *informed* by test2 failures, so test2 can no longer validate any architecture proposed here. |
| dev2 | Ablation and false-positive cost of hypothetical rules | Tuned split, so it understates leakage (0.44 % vs 6.10 %) |
| "Fresh DEV" (requested) | **Not available** | `eval/synth2/generate.py --seed N` only reshuffles the same template pool between dev and test, so a new seed would put test2 templates into "dev". No fresh DEV exists without a new generator. This is the reason for the third corpus (§E). |

Epistemic classes follow `research/PROVENANCE.md`: measured = FACT-M; adjudications and recommendations = DECISION/INFERENCE.

## A. Root cause of the 6.10 % leakage (Config B, test2: 13,119 of 215,077 gold characters)

### A.1 Attribution (Q1)

Every leaked character is assigned once by each of two methods:

- **Automatic method.** An ordered rule (partial span > propagation miss > person > value unparseable even when labelled > structured line with or without a concept label > prose).
- **Manual method.** A human adjudication per template and type (`MANUAL` in `eval/root_cause.py`, recorded as a DECISION).

| Category | Manual | Automatic | What it looks like |
|---|---:|---:|---|
| Missing schema knowledge | **32.4 %** | 28.7 % | SAP `GBDAT/ANSAL/ICNUM`, vCard `BDAY/FN/N`, `slack_webhook:` URL-path tokens, `signature key=`, `D_O_B`/`GOV_ID` |
| Unseen structural format | **20.2 %** | 18.5 % | Unlabelled positional flat files; label on one line with the value below it |
| Missing semantic/context in prose | **16.7 %** | 27.4 % | "the api key … is `<hex>`", "it should be `<date>`" |
| External PERSON | **15.8 %** | 16.8 % | Bullet-list names, chat speakers, `Last, First`, `referrer` |
| Detector/parser bugs | **14.8 %** | 8.3 % (partial spans only) | Hand-rolled CSV and markdown parsing (quoted commas, mixed tab/comma); ambiguity resolved to *nothing* |
| Unseen lexical form | 0.2 % | 0.2 % | Dotted Aadhaar |
| Obfuscation/evasion | **0 %** (clean split, by construction) | 0 % | Measured separately in A.3 |

**How reliable this split is (FACT-M):**

1. **The two methods agree on only 51.9 % of leaked characters.** The boundaries between "schema", "structure" and "semantic" are soft. For example, a key the system doesn't know inside free text could be put in any of the three.
2. **The leak is concentrated.** 8 of the 47 template × type cells hold 53 % of it; the largest single cell, `chat_paste_credentials` SECRET, holds 11.3 %. The shares therefore describe this one author's template mix, not HR traffic.

**What survives both methods:**

| Coarse cause | Manual | Automatic |
|---|---:|---:|
| **Context-dependent typing failed** (schema + structure + semantic) | **69.3 %** | **74.6 %** |
| PERSON entity not found | 15.8 % (+4.2 % PERSON inside parser-bug templates) | 16.8 % (+ most of the 8.3 % partial spans) |
| Value representation itself (lexical/obfuscation) | 0.2 % | 0.2 % |

### A.2 The decisive measurement: context was needed to *decide*, not to *find* (Q1, Q5)

`eval/root_cause.py` also asks of every leaked span whether a context-free value-shape test (analysis only, not wired into `pgw`) would have flagged it. The three tests are: a Verhoeff-valid 12-digit number, a date, or a high-entropy token of 16 or more characters.

| Manual category | Leaked chars | Shape-flaggable | Share |
|---|---:|---:|---:|
| Missing schema knowledge | 4,253 | 3,523 | **82.8 %** |
| Unseen structure | 2,647 | 2,204 | **83.3 %** |
| Missing semantic context | 2,185 | 1,768 | **80.9 %** |
| Parser bugs | 1,939 | 591 | 30.5 % |
| External PERSON | 2,067 | 0 | 0 % |
| **All** | **13,119** | **8,114** | **61.9 %** |

By type, the shape tests flag every leaked AADHAAR character (1,720/1,720), 97 % of DOB characters (2,509/2,577) and 99 % of SECRET characters (3,865/3,915). They flag none of PERSON (3,068), COMPENSATION (1,268), most of BANK_ACCOUNT (461) or PHONE (90).

**Cost of those same tests on dev2 (non-gold text; FACT-M):**

| Test | False-positive spans | Documents affected | Negative documents affected |
|---|---:|---:|---:|
| DATE | 816 | 326 | **210/420** |
| HIGH_ENTROPY | 499 | 218 | 84 |
| VERHOEFF12 | 31 | 24 | 24 |

**Root cause (A), stated precisely.** About 70 % of the leak is values whose shape is already suspicious: valid Aadhaar, dates, high-entropy secrets. They leak because the architecture **requires the context to be recognised before it will mask**. When the container, key or phrasing is unfamiliar, the default outcome is *send*. The remaining leak is names that aren't in the roster and the model misses (~24 % including the CSV cases), plus numeric attributes (compensation and bank) whose shape is meaningless without the record they sit in (~13 %).

The shape tests are not a fix. Their dev2 cost shows that masking every date would hit half of all negative documents. Context is still required, but for **precision** (deciding to release a value), not for **recall** (finding it).

### A.3 Adversarial leakage (separate from the clean 6.10 %)

Held-out transforms (M-27, ablation table) give line-wrap 70.2 %, HTML entity 52.2 %, OCR confusion 40.9 % and leetspeak 23.8 %, under every configuration. ENTITY_ONLY is worse on all of them. These are failures of deterministic normalisation (no unwrap, `html.unescape` or digit-confusion view), not of semantics.

## Q2. Correct solution type per category

| Category | Primary solution | Secondary | Evidence |
|---|---|---|---|
| Schema knowledge (32 %) | **Value-shape candidates + schema/ontology** (standard data dictionaries: SAP PA infotypes, vCard RFC 6350, HR Open/Workday field names) used to *release* or *type* | ML key→concept mapping for keys outside the dictionaries | 82.8 % shape-flaggable |
| Unseen structure (20 %) | **Generic parsing into records** (real CSV/TSV sniffing, JSON/XML/YAML, kv, fixed-width) + **column-statistics typing** (all values in a column checksum-valid → typed column) | — | 83.3 % shape-flaggable; positional files have no labels at all |
| Semantic context in prose (17 %) | **Shape candidates + default-deny in person-bearing documents** under RESTRICTED | ML contextual classifier only for the shapeless residue (~0.4k chars: "it should be X") | 80.9 % shape-flaggable |
| External PERSON (16 %) | **NER + entity resolution** (name variants, `Last, First`, initials) anchored to records | Policy: any name-shaped value in a person-record "name" column | 0 % shape-flaggable; D4 is the only component that moves ner_test2 (M-33) |
| Parser bugs (15 %) | **Replace hand-rolled parsing** with standard parsers; on ambiguity, **mask** rather than drop | — | 30.5 % shape-flaggable; the rest is CSV names and compensation |
| Lexical (0.2 %) | Deterministic normalisation | — | — |
| Obfuscation (adversarial) | **Deterministic normalisation views** (unwrap, entity decode, OCR digit confusion) | Leetspeak names: NER on a de-leeted view | M-27 |
| Compensation and bank in unseen layouts (~13 %) | **Record anchoring + policy**: numeric attributes of a person record are RESTRICTED unless their column concept is known to be safe | Local computation (send bands or deltas rather than values) where the purpose allows | Shape tests flag 0 % |

## Q3. Ablation (dev2 = tuned, test2 = diagnostic only, ner_test2 = external names)

Marginal effect of removing one component from Config B. Positive numbers mean the component helps. FACT-M from `eval/results/ablation_r2.md`.

| Removed | Leak pp (dev2 / test2 / ner) | Exposed docs (dev2 / test2 / ner) | Main recall loss on test2 |
|---|---|---|---|
| D0 normalisation | +0.08 / +0.15 / +3.20 | +4 / +6 / +4 | Clean ≈ 0; **spaced 5.8 → 37.3 %**, mixed separators 6.5 → 20.7 %, obfuscated email 5.3 → 19.8 % |
| D1 field context | **+16.60** / +7.99 / +0.06 | +442 / +282 / +1 | DOB −43 pp, AADHAAR −16 pp |
| D2 recognisers | +15.23 / **+23.70** / +32.00 | +731 / +605 / +157 | BANK −64 pp, COMP −41 pp, SECRET −40 pp |
| Person cues | +0.27 / +0.10 / +1.64 | +73 / +23 / +13 | Small; its dev2 false-positive burden (87 items) did not recur on test2 |
| D3 roster EDM | +1.68 / +3.39 / 0 | +194 / +220 / 0 | PERSON −12 pp (employees) |
| D4 NER | +0.14 / +2.14 / **+38.63** | +31 / +175 / +211 | PERSON −12 pp; external names −66 pp |
| D5 propagation | +0.15 / +0.18 / +1.43 | +49 / +8 / +32 | PERSON −1.5 pp |

| Minimal configuration (test2) | Char leak | Doc exposure | Note |
|---|---:|---:|---|
| FULL_B | 6.10 % | 20.1 % | — |
| RULES_ONLY (D0+D1+D2+cues+D5) | 16.27 % | 44.8 % | PERSON recall 48 % |
| ENTITY_ONLY (D0+D3+D4+D5) | **56.85 %** | 74.3 % | Every structured type at 0 recall; 71 egress false alarms |
| D4_ONLY | 88.70 % | 76.0 % | — |

**Readings (INFERENCE from FACT-M):**

- **D1 is the overfit component.** Its value halves from dev2 to test2 (16.6 → 8.0 pp), because it encodes the keys and layouts the fixers saw.
- **D2 and D4 matter more on held-out data than on dev data.** Validators and models generalise; vocabularies do not.
- **D0 is free on clean text and essential under attack.**
- **No single family suffices:** ENTITY_ONLY is 57 % and RULES_ONLY is 16 %.

## Q4. Multi-metric view (Config B unless stated)

| Metric | dev2 | test2 | Source |
|---|---:|---:|---|
| Document exposure (≥ 1 raw value) | 3.2 % | **20.1 %** | M-22 |
| Entity recall: AADHAAR / DOB / SECRET / COMP / BANK / PERSON | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 / 0.971 | 0.789 / 0.756 / 0.848 / 0.927 / 0.940 / 0.943 | M-23 |
| External-name recall | — | 0.794 (ner_test2) | M-24 |
| PERSON precision | 0.824 | 0.940 | M-23 |
| Negatives with ≥ 1 false positive | 109/420 | 64/420 | ablation |
| Adversarial (worst held-out) | — | line-wrap 70 %, HTML 52 %, OCR 41 % | M-27 |
| Detection latency p50/p95/p99 (serial, frozen) | — | 16 / 47 / 132 ms (A: 3.3 / 9.2 / 31) | M-29 (ablation latencies were measured in parallel and are relative only) |
| Egress false alarms (clean) | — | 0 | M-26 |
| E-UTIL | not run | not run | M-20 |

## B. What is fundamentally wrong with the current detection architecture

1. **Recall is conditional on recognising the context.** A value is masked only if some rule recognises its key, layout or phrasing. In an open world of containers, the failure mode of an unknown context is *leak*. Because 82 % of the context-failure leak has a suspicious shape, the system often "knew" a value was dangerous-looking and still sent it. Unknown contexts should cost **utility, not security**.
2. **Format knowledge is written as code over a closed vocabulary.** D1 is a growing set of regexes and keyword lists. It is the component that loses half its value on unseen data (M-33), and every release adds the formats the last test exposed (M-30: 14× dev→test gap).
3. **No document model.** Parsing is ad hoc per detector, and ambiguity ends in "no detection" (14.8 % of the leak). There is no notion of record, column or person row, so the system cannot reason that a row describing a person makes its 12-digit and date fields that person's attributes.
4. **Entity-centric thinking is already the right model for names, and only for names.** It cannot type numbers. Replacing the rules with an entity representation gives 57 % leak (ENTITY_ONLY).
5. **Normalisation is incomplete**: no view for line-wraps, HTML entities or OCR digit confusion (A.3).

The forwarded hypothesis ("overfitting to representations and formats") is **half right**. The overfit is to **contexts and containers**, not to value representations: the lexical share is 0.2 %, and validators (D2) generalise better than anything else. A "semantic understanding" upgrade is *not* the main lever. Measured data says the cheapest lever is to **invert the default** and let context *release* values.

## C. Proposed architecture (PD-13; nothing implemented)

```
L0  Normalisation views   existing D0 + unwrap, html.unescape, OCR digit-confusion   [deterministic]
L1  Document → record graph   standard parsers (csv.Sniffer, json, xml, yaml, kv, vCard, fixed-width);
                              ambiguity ⇒ keep every parse, never drop                 [deterministic]
L2  Candidate generation (recall layer, context-free)
      validators: Verhoeff, Luhn, PAN, IFSC, UPI, email, phone, dates, entropy, secrets catalogue
      entities:   roster EDM (D3), NER (D4), entity resolution of name variants         [det. + ML for NER]
L3  Field semantics (typing layer)
      key normalisation → standard dictionaries (SAP PA, RFC 6350, HR Open) → abstain;
      column statistics (all values Verhoeff-valid ⇒ AADHAAR column);
      ML key→concept mapper only for keys outside the dictionaries                     [det. + small ML]
L4  Policy = release decision over (record has person anchor?, concept, value shape, data_class)
      RESTRICTED: shape candidates inside person-bearing records are masked unless L3 types them
      as a releasable concept; dates generalised (month/year), never dropped silently  [deterministic]
L5  Prose residue   ML contextual classifier for shapeless attributes in prose ("it should be X") [ML, last]
D5, aliasing, restore ledger, streaming, egress invariant: unchanged                   [deterministic]
```

Its central property: removing L3 or L5 lowers precision, not recall. **This is a hypothesis.** The dev2 shape cost (210/420 negatives contain a date) shows that "person-bearing record" anchoring in L4 must be accurate, or dates will over-mask. That accuracy is unmeasured.

## D. What stays deterministic and what needs ML

| Deterministic (auditable, testable) | ML (bounded, measured separately) |
|---|---|
| L0 normalisation; L1 parsing; L2 validators and checksums, entropy; roster EDM; L3 dictionaries and column statistics; L4 policy; propagation; aliasing; restore ledger; streaming; egress invariant | D4 NER for names outside the roster (0.794 today; GLiNER untested, Q-26) · L3 key→concept mapping for keys not in the dictionaries · L5 prose context for shapeless attributes (~3 % of today's leak) · optional learned column typing |

Rule: **ML may add masking but never remove it** for a shape-flagged value in a RESTRICTED person-bearing record. ML errors then cost precision, not leakage.

## E. Minimum experiment to prove the new architecture works

1. **Build a third corpus first and freeze it** (PD-14), before any L1–L4 code exists.
   - It needs a new generator design by an author who has not seen this analysis.
   - It must contain document families absent from v1 and v2.
   - Each family needs at least 10 templates so that no single template dominates (today 8 cells hold 53 % of the leak).
   - Split it into dev3 and test3 by family, plus held-out adversarial kinds.
   - Record the hashes in a manifest before the run.
2. **Build the smallest candidate:** L1 standard parsers, L2 shape candidates (existing validators), L3 dictionaries for only 3 standards plus column statistics, and L4 release policy. Keep D0/D2/D3/D4/D5. **Remove D1.**
3. **Pre-register gates** in EVALUATION_PLAN (a dated amendment) *before* touching dev3. All of them are compared with Config B on test3, one shot:
   - document exposure ≥ 50 % lower;
   - false-positive items on negatives ≤ 1.5× Config B;
   - dev3→test3 leak ratio ≤ 2× (today ≈ 14×);
   - no held-out adversarial kind worse than Config B;
   - p95 detection latency ≤ 250 ms.
4. **Ablate L3 on test3.** The architecture's claim holds only if removing L3 changes **precision much more than recall**. If recall drops sharply, the system is still context-conditional and the claim is refuted.
5. **E-UTIL** on the over-masked outputs as soon as an API key exists. Over-masking that destroys task usefulness is a failure even if leakage passes.

Falsification: if the step-4 ablation shows L3 carrying recall, or test3 exposure is not ≥ 50 % lower, PD-13 is rejected and recorded as CONTRADICTED.

## Linkage

New evidence: M-31 to M-35 in `research/EVIDENCE_MATRIX.md`. Proposals: PD-13 (absorbs PD-10 and PD-11) and PD-14 in `research/ARCHITECTURE_DECISIONS.md`. Open questions: Q-27 and Q-28; Q-25 is updated. Weaknesses W-10 to W-13 are unchanged.
