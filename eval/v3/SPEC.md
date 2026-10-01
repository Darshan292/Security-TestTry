# Corpus v3 — generation specification (pre-registered 2026-10-01)

Status: **frozen before any v3 family, template or item exists.** Changes only via a dated amendment at
the end of this file, and never after test3 has been generated.

## 1. Purpose

v3 measures whether a detection architecture generalises to **document families it has never seen**.
The corpus is synthetic HR/ERP traffic that an Indian enterprise's applications would send to an external LLM.
It also measures sensitive values outside the current taxonomy. **No real personal data may be used anywhere.**

## 2. Who writes what (see CONTAMINATION.md)

| Role | Writes | May read |
|---|---|---|
| Catalogue author (agent, isolated) | Family catalogue `CATALOGUE.md` (sealed) | This spec, the exclusion list in §9, public standards on the web |
| Family authors ×3 (agents, isolated, one per channel group) | Family generator modules + self-tests (sealed) | This spec, their group's catalogue entries, public standards |
| Adversarial author (agent, isolated) | Transform module (sealed) | This spec §8 only, public sources on real corruption processes |
| Orchestrator | Assembler, validators, audits, family assignment, sealing | Everything **except** sealed content; sees aggregate counts only |

## 3. Item format (JSONL, UTF-8, one item per line)

```json
{"id": "<split>-<family_id>-<n>", "split": "dev3|test3", "family": "<family_id>", "group": "S|C|I",
 "template_id": "<family_id>.<skeleton>", "variant": {"axis": "value", ...},
 "slice": "positive|negative", "name_source": "roster|external|mixed|none",
 "text": "...", "spans": [{"start": 0, "end": 12, "type": "AADHAAR", "subtype": null}]}
```

`spans` are character offsets into `text` (Python `str` indexing), sorted, non-overlapping.
Every occurrence of every planted sensitive value is labelled. An unlabelled planted value fails the generator self-test.

The roster file is `roster.jsonl`, with one employee per line:
`{"employee_id","full_name","first_name","last_name","email","phone"}`. It describes **one new fictional
company** whose name, e-mail domain and employee-ID scheme the catalogue author chooses. The roster is shared by dev3 and test3, because the gateway legitimately has it. About 1,200 employees.

## 4. Taxonomy and labelling conventions

Taxonomy types (the gateway's): `PERSON EMAIL PHONE AADHAAR PAN BANK_ACCOUNT IFSC CARD UPI_ID SECRET
EMPLOYEE_ID COMPENSATION DATE_OF_BIRTH`.

Out-of-taxonomy type: `OOT` with a `subtype`. Use it for personal identifiers a real HR flow carries that the gateway doesn't model: UAN, ESIC IP number, passport, voter ID (EPIC), driving licence, PRAN, vehicle registration, and health-insurance member ID. OOT is reported separately and never enters gates G1–G6.

| Type | Span covers | Notes |
|---|---|---|
| PERSON | The name as written: all name tokens, including `Last, First` and initials (`R. K. Menon`) | Honorifics excluded (`Dr.`, `Shri`, `Mr`). A first-name-only mention of a person is also PERSON. Names in non-Latin script are PERSON. |
| EMAIL, UPI_ID, IFSC, PAN | The whole value | — |
| PHONE | Including country code and separators as written | — |
| AADHAAR | All 12 digits including internal separators | Generated numbers must be Verhoeff-valid and must not start with 0 or 1. **Already masked values (`XXXX XXXX 1234`) are not gold.** |
| BANK_ACCOUNT | The account number | 9–18 digits |
| CARD | The full PAN of the card | Luhn-valid. Masked cards are not gold. |
| SECRET | The secret value only | Not the key name and not the `Bearer `/`Basic ` prefix. For an encoded secret (base64, URL-encoded) the span is the encoded string. A JWT is one SECRET. |
| EMPLOYEE_ID | The ID as written | Roster scheme. Other companies' IDs are not gold. |
| COMPENSATION | Currency + amount + unit (`₹ 18,40,000`, `INR 1.2 lakh`, `24 LPA`) | Never period words (`per month`). Amounts in a pay-component table count. |
| DATE_OF_BIRTH | The date string as written, in any representation, including spreadsheet serial numbers | Other dates (joining, leave, invoice) are **not** gold. They are the realistic hard negatives. |

## 5. Sampling frame for families (the catalogue author derives families from it)

Rows are HR/ERP lifecycle areas: recruitment, onboarding, payroll, statutory compliance (PF/ESI/TDS/gratuity), benefits and insurance, attendance and leave, performance, learning, exit and full-and-final settlement, employee relations, IT and access management, finance and reimbursement, vendor and contractor management.

Columns are channels:
- **S** system exports and files;
- **C** human communications and documents (including text extracted from PDFs, scans or audio);
- **I** integration, developer and operations artefacts.

A **family** is one (purpose, representation) pair, for example "statutory return file in a regulator's delimited format" or "auto-generated meeting transcript". The catalogue lists **36 families: 12 per group**.

The families must:
- be grounded in a real-world representation. Cite the public standard or product documentation where one exists, and never copy real personal data from it;
- not be in the exclusion list (§9), where "same family" means the same purpose *and* the same representation;
- cover at least 9 of the 13 lifecycle areas;
- include at least 4 families with non-Latin script or bilingual content, at least 4 whose real-world processes naturally corrupt text (PDF extraction, OCR, speech-to-text, mail transfer encodings, spreadsheet coercion), and at least 6 that carry OOT identifiers.

## 6. Requirements per family

- **≥ 6 skeletons** (structurally or linguistically distinct templates) **and** ≥ 3 variation axes. Examples of axes: delimiter, quoting, column order, header casing or language, key naming convention, date format, number grouping, encoding artefacts, line endings, field order, optional fields, and phrasing register.
- **75 items:** 60 positive + 15 negative. Negatives use the same representation, carry family-realistic look-alikes (reference numbers, timestamps, hashes, UUIDs, invoice and GL codes, non-DOB dates, company contact numbers, masked values) and have **no** gold spans.
- Every positive item carries ≥ 1 gold span. Across the family there are at least 3 taxonomy types and at least 1 PERSON mention.
- Names mix roster employees and external people (candidates, dependants, landlords, vendors, doctors, references). Target 40–60 % external mentions per family.
- **Concentration caps:** no skeleton contributes more than 25 % of its family's gold characters; no family more than 6 % of its split's gold characters (the assembler enforces and reports this).
- Deterministic: `generate(rng: random.Random, roster, pools, n_pos, n_neg) -> list[item]`, using only the Python standard library and the author's own helpers. **No imports from this repository.**
- A self-test asserts span/value consistency, Verhoeff, Luhn and PAN validity of planted values, no overlapping spans, no unlabelled planted values, and determinism under a fixed seed.

## 7. Values and names

- Name pools: the catalogue author builds fresh pools (≥ 600 first names, ≥ 400 surnames). They must span all regions of India and include Muslim, Christian, Sikh, Parsi, North-East and non-Indian names.
- The assembler draws **disjoint external-name token sets** for dev3 and test3. Roster names are shared.
- Every generated identifier value is unique within the corpus. No value appears in both splits.
- Identifiers must be format-valid where the real format has a check (Verhoeff, Luhn, PAN 4th-letter category, IFSC 5th char `0`). The generator chooses an invalid value deliberately only as an OOT or negative look-alike, and labels it as such.

## 8. Adversarial transforms (independent author)

The adversarial author builds transforms from **real-world corruption and evasion processes**, without reference to any detector.
- **Accidental processes:** PDF text extraction, OCR, speech-to-text, mail transfer encodings, spreadsheet coercion, mojibake, Unicode normalisation forms, terminal wrapping, double escaping.
- **Deliberate evasion:** an insider reformatting a value to get it past a filter.

Requirements:
- ≥ 10 kinds. Each kind is a function `(item, rng) -> item` that rewrites gold values and/or their surroundings **and updates the spans** so that the transformed value is still gold.
- Every kind declares whether the value is still recoverable by a human reader. Non-recoverable transforms (for example a destroyed number) are not gold and are excluded.
- Kinds whose names or mechanisms match a v1/v2 kind (§9) are labelled `known`; all others are `heldout`. Only `heldout` kinds enter gate G4.

## 9. Exclusion list (names only; the authors may not open the corpora)

v1/v2 template ids: everything listed in `eval/v3/EXCLUSIONS.txt`.
v1/v2 adversarial kinds: homoglyph, zero_width, spaced, obfuscated_email, case, nbsp, mixed_separators, native_digits, spoken_email, leetspeak_name, line_wrap, ocr_confusion, html_entity.

## 10. Sizes

36 families × 75 items = 2,700 items, before adversarial transforms. The assembler assigns families to splits (SEPARATION.md): about 1,350 items per split.

## Amendment 2026-10-01b (before any family module exists)

Each span may carry an optional `"key"`: the field name, column header or label that introduces the value as it appears in the text (for example `"dob"`, `"Date of Birth"`, `"GBDAT"`), or `null` for prose. It is annotation only and does not change gold. It supports the key-novelty strata (EVALUATION_PLAN 2026-10-01). Family authors fill it wherever a key or label exists.
