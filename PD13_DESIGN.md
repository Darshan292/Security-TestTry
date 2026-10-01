# PD-13 minimum experiment — implementation design (fixed 2026-10-01, before any v3 family exists)

Source: ROOT_CAUSE_ANALYSIS_R2.md §C. Scope: the smallest build that tests whether "context releases, not finds" generalises. It ships as `pgw/pd13/` behind `build_protector(data, ner, l3: bool)`. Arm B is `l3=True` and arm C is `l3=False`. Arm A (the current architecture) is untouched.

## Kept from the current system (identical in A, B and C)
- D0 normalisation views;
- the D2 validators (EMAIL, UPI, PHONE, AADHAAR, PAN, IFSC, CARD, secrets catalogue, EMPLOYEE_ID, currency-marked COMPENSATION, labelled DOB);
- person cues, D3 roster EDM, D4 spaCy `en_core_web_lg` with the name-shape filter;
- D5 propagation, aliasing, policy actions, egress.

## Removed in B and C
- **D1 field-context detection** (`_field_scan` candidates and `_field_values`), via the existing ablation switch semantics.

## Added
| Layer | What | Rules |
|---|---|---|
| L0+ | Extra views: line-unwrap (join a line break inside a token or a digit group), `html.unescape`, OCR digit confusion inside digit-dominant tokens (O→0, l/I→1, S→5, B→8) | Offset maps composed as in D0; property-tested |
| L1 | Record graph. Standard parsers only: `json` (whole, NDJSON, embedded blocks), `xml.etree` (whole or fragments), `csv` (Sniffer per block of ≥ 2 lines with a consistent delimiter count; RFC 4180 quoting), key/value lines (`k: v`, `k = v`, `k => v`), indentation-based YAML-like blocks, and whitespace-aligned fixed-width tables (≥ 3 lines sharing column gaps) | Output: fields `(record_id, key or header or None, value_start, value_end)`. Prose is the remainder. **On ambiguity keep every parse; never drop** |
| L2 | Context-free shape candidates, beyond the D2 validators: `DATE` (numeric, month-name and ISO forms), `HIGH_ENTROPY` (≥ 16 chars, ≥ 3.5 bits/char, ≥ 2 character classes), `LONG_DIGITS` (9–18 digits, not phone- or validator-claimed), `AMOUNT` (Indian or western grouping ≥ 4 digits or decimal amounts, not claimed by other types) | Candidates carry a provisional type: DATE→DATE_OF_BIRTH, HIGH_ENTROPY→SECRET, LONG_DIGITS→BANK_ACCOUNT, AMOUNT→COMPENSATION |
| L3 (B only) | Field semantics. (1) A dictionary maps a normalised key to a concept: one of the 13 types, `RELEASE_DATE`, `RELEASE_ID`, `RELEASE_AMOUNT` or `RELEASE_TEXT`. (2) Column statistics: ≥ 80 % of a column's values validate as X ⇒ column type X; date columns whose years fall within 18–70 years before the document's latest date ⇒ DOB-like, otherwise release-like; ≥ 50 % of values NER/roster-PERSON ⇒ name column | Each dictionary entry records `provenance` (CONTAMINATION C7). Allowed sources: schema.org (Person, PostalAddress, MonetaryAmount); vCard RFC 6350; SAP HCM PA infotype field names; Workday public Get_Workers WSDL; SAP SuccessFactors OData (PerPerson, PerPersonal, EmpJob, EmpCompensation); HR Open Standards; ISO 20022 party/account elements; Indian statutory forms (EPFO, ESIC, Form 16 / 24Q labels); plus `dev3:<item id>` entries |
| L4 | Release policy. A record or document is **person-bearing** if it contains a roster, NER or cue PERSON, an EMPLOYEE_ID, or a personal EMAIL/PHONE. Under RESTRICTED: | 1. D2 validator hits are masked, as today. 2. Shape candidates in person-bearing records, or in person-bearing documents for prose, are **masked unless L3 types them as a RELEASE concept**. 3. HIGH_ENTROPY is masked in every document unless L3 releases it. 4. L3 may **type** a value (for example a name column, or an untyped number under a salary key) but may never release a D2 validator hit. 5. In C nothing is released, so rules 1–3 apply without exceptions |

## Explicitly not in this experiment
- ML key→concept mapping (L3-ML);
- the prose context classifier (L5);
- GLiNER;
- any LLM.

## Evaluation
`eval/run_v3.py` with the gates in `eval/v3_gates.py` (EVALUATION_PLAN 2026-10-01). Development on dev3build only. dev3val is read at most 5 times, logged. test3 is read once per arm, all from one commit.
