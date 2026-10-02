# E-UTIL (R4 product validation): can ESU's HR tasks use an external LLM through the gateway?

- **Date:** 2026-10-02 · **Protocol:** EVALUATION_PLAN amendment 2026-10-02 (registered before the tasks were written).
- **Data:** 40 synthetic tasks, 8 ESU task types × 5 (`eval/eutil/tasks.jsonl`). The author never saw `pgw/` (transcript audited).
- **External model:** a Claude Sonnet subagent with no repository access, seeing only the outbound prompt. Answer agents were audited: each read only its own batch file. **Disclosed limitation:** this is not the production provider. Provider latency is not measured; only gateway-added latency is.
- **Scoring:** deterministic (`eval/eutil/run_eutil.py`, `out/scores.json`, `out/SCORES.md`). Side-by-side cases for the owner's human review are in `REVIEW_CASES.md`. **Human review is pending (owner).**

## 1. Results

| | RAW | GATEWAY (as shipped) | MAX protection |
|---|---:|---:|---:|
| Correct, all items | 82.5 % | **37.5 %** | 55.0 % |
| Correct, unblocked items only | 82.5 % | 53.6 % | 55.0 % |
| Blocked (422) | 0 | **12 / 40** | 0 |
| Required entities present after restore | 82.9 % | 65.5 % | **100 %** |
| Factual equivalence with RAW (paired) | — | 75.6 % | 63.1 % |
| Task-critical non-personal values masked | 0 | 4 | **133** |
| Items with a sensitive value in the outbound prompt | 36 / 40 | 4 / 40 | 4 / 40 |
| Unrestored or fabricated placeholders | — | 0 / 0 | 0 / 0 |
| Gateway-added latency p50 / p95 (no NER) | — | 6 / 15 ms | 8 / 14 ms |
| E-UTIL gate (≤ 5 % degradation) | — | **fail** | fail overall; **pass** for classification, summarisation, email drafting |

RAW's 82.5 % is the ceiling of this deterministic checker (exact-substring facts). Use the paired comparisons, not the absolute numbers.

### Per task type (correct, all items: RAW / GATEWAY / MAX)

| Task type | RAW | GATEWAY | MAX | What decides it |
|---|---|---|---|---|
| Classification | 100 % | 60 % (2 blocked) | **100 %** | Needs no values at all |
| Summarisation | 80 % | 40 % | **80 %** | Names and IDs restored; facts survive |
| Email drafting | 60 % | 20 % (3 blocked) | **60 %** | Placeholders are copied into the draft and restored |
| Case explanation | 60 % | 60 % (2 blocked) | 40 % | Works while non-personal numbers stay visible; MAX broke a leave ledger |
| Policy Q&A | 80 % | 40 % | 60 % | Fine without personal data; eligibility questions fail when the deciding value is hidden |
| Extraction | 100 % | 0 % (4 blocked) | 40 % | Copying values works; **reformatting** a value (DOB to ISO) cannot be done on a placeholder |
| Comparison | 80 % | 40 % | 20 % | **Arithmetic on hidden values is impossible** (CTC difference, ages) |
| Report generation | 100 % | 40 % | 40 % | Aggregates over salaries and arrears are impossible on placeholders or bands |

## 2. What the evidence says (FACT-M, plus INFERENCE where marked)

1. **Pseudonymising identity values costs almost nothing.** Names, IDs, emails and phones were restored at 100 % under MAX, with 0 fabricated or unrestored placeholders. Classification, summarisation and drafting lost nothing measurable.
2. **Blocking a request because a HIGH identifier is present destroys utility without adding protection.**
   - 12 / 40 shipped-gateway requests were refused because a PAN, bank account, Aadhaar, UPI or secret appeared somewhere in the context.
   - Most of those tasks did not need the identifier (for example a ticket summary that should not mention the bank account at all).
   - *Inference:* **redact identifiers the task does not need; block or route locally only when the task needs them.**
3. **Computation over a personal quantity cannot happen on the external model,** because the model never sees the value. This covers salary differences, band statistics, arrears, age or wage eligibility, and date and format conversion of a DOB. With aliasing the model refuses or returns null; with generalisation (bands, decades) the answer is wrong. These tasks need **local computation**.
4. **Masking non-personal numbers and dates breaks reasoning,** and is the main cost of "maximum protection" (133 task-critical values masked). Examples: leave ledgers, policy thresholds, ticket dates. The shipped gateway also mis-typed a public **policy threshold (₹21,000) as COMPENSATION**.
5. **Unreliable content stays a leak.** Devanagari dependant names left raw through the shipped gateway, which has no NER (consistent with R3 M-41). Content that detection cannot cover reliably needs **private/local inference** under RESTRICTED purposes.
6. **Requests that are not LLM work should be blocked.** "Give me all PANs and bank accounts as CSV" is a database export, not language work.
7. **Latency is not the constraint.** Gateway-added p95 is 15 ms without NER (N-1 allows 50 ms).

## 3. Task routing classes (derived from the 40 items)

| Class | Meaning | Items (n) |
|---|---|---|
| **EXT-PSEUDO**: external LLM with pseudonymisation | Identity values aliased and restored; non-personal content sent as is; unneeded HIGH identifiers redacted | All 5 classification; all 5 summarisation; email drafting 01, 02, 04 (05 after removing the secret); case explanation 01–05 (04/05 after redaction); policy Q&A 01–03; comparison 03–04; report generation 03 and 05 (**26**) |
| **LOCAL-COMPUTE**: exact values needed for arithmetic, thresholds or format conversion | Computed inside the boundary. The external LLM may write the language around derived, non-identifying results | comparison 01, 02, 05; policy Q&A 04, 05; report generation 01, 02, 04; extraction 03 (net-pay arithmetic) (**9**) |
| **PRIVATE-LLM**: in-boundary model | The substance is personal data in content detection cannot cover reliably (non-Latin script, scans, speech), or extraction that transforms personal values | extraction 01 (bank-change request to JSON); extraction 04 (Devanagari dependants + DOB reformat); extraction 05 (CV parsing to a personal-field schema); email drafting 03 (TPA endorsement carrying a dependant's identifiers) (**4**) |
| **BLOCK** | Not an LLM task, or the purpose forbids it | extraction 02 (bulk PAN/bank export); and in any class, credentials are never sent (removed, then the task proceeds) (**1** + a rule) |

Total 26 + 9 + 4 + 1 = 40. extraction-01 worked under MAX by copying placeholders. Because it carries bank details, the **contract**, not the experiment, puts it in PRIVATE-LLM or a deterministic parser.

## 4. Limitations
- **The external model is a stand-in** (a Claude subagent), not the production provider.
- **40 items; there are no confidence intervals at this size.** This is a routing study, not a benchmark.
- **The deterministic checker is strict** (substring facts, digits-only numbers, no lakh conversion). Absolute levels understate everyone. The paired comparisons carry the conclusions.
- **MAX is a deliberately blunt upper bound on protection,** not a proposed design.
