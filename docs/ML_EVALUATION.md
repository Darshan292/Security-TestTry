# ML evaluation: harness, protocol, release gate and labelling guide

`eval/ml/` evaluates any detection backend span by span, the same way for today's stack and for any model.

## 1. What is measured

| Metric | Definition |
|---|---|
| Precision / recall / F1, **exact** | a prediction counts only with the same start, end and type as a gold span |
| Precision / recall / F1, **overlap** | a prediction overlapping a gold span of the same type is correct; a gold span overlapped by a prediction of its type is found |
| per type, per genre | both of the above, by gateway type and by genre (`genre`, else `family`, else `slice`) |
| **Leak rate** | share of gold values (the gold surface string) that still occur in the protected text (exact substring, or the same digits for digit-bearing values). Type-agnostic: masked under the wrong type is not a leak. Overall, per type, per genre |
| **Over-masking** | share of annotated benign spans touched by a prediction; share of characters outside every gold span that were masked |
| **Latency** | wall time per example on the evaluation machine: p50, p95, max (ms). Meaningful only on the production CPU class |

Backends (`--backend`): `current` (today's stack), `pipeline_ml` (today's stack + ML, with precedence),
`ml_checksum` (ML + ID regex + roster, legacy heuristics off), `ml_only` (the model alone, for model selection). The
model is passed with `--model` as the same mapping the deployment profile uses; its artefact is verified against its
sha256 exactly as in production.

```bash
python -m eval.ml.run --role dev --data eval/data3/dev3build.jsonl --format pgw --backend current
python -m eval.ml.run --role dev --data dev.jsonl --backend ml_only --model pii-v1.yaml --out dev_ml.json
```

## 2. Dataset formats (adapters)

- `--format pgw`: our corpus JSONL: `{"id", "text", "spans": [{"start", "end", "type"}], "genre"?, "benign"?}`.
- `--format bio`: token/BIO JSONL exported from a Hugging Face dataset (`dataset.to_json(...)`):
  `{"id", "tokens": [...], "ner_tags": [...], "genre"?, "spaces"?}`. Integer tags need `--label-names O,B-PER,...`;
  map dataset types to gateway types with `--type-map PER=PERSON,EMAIL=EMAIL`. Text = tokens joined by spaces.
- `--format human`: the human-labelled real-ticket format (section 5). Only `status: agreed` rows are scored;
  disputed rows are counted in the output.

## 3. Protocol

1. **Dev set = model selection.** Training, choosing a model, choosing `min_score_by_type` and the gate threshold
   happen only on dev data (synthetic corpora, public datasets). Any number of runs.
2. **Release gate = never tuning.** The external tester's sets and the human-labelled real sample are evaluated with
   `--role release` only, once per release candidate, with the thresholds (section 4) fixed beforehand. Nothing seen
   in a release-gate result is fed back into training or thresholds for the same release; a failed candidate needs
   new dev work and, for the next attempt, a fresh real sample.
3. The harness enforces what it can: `--role dev` refuses the held-out corpora of the regex releases (`test*`,
   `ner_test*`, `dev3val`, sealed archives) and every path listed in `release_sets`; `--role release` prints only
   aggregates (no per-example output) and appends a record (dataset sha256, backend, model sha256, time, verdict) to
   `eval/ml/results/release_runs.jsonl` (git-ignored; copy it into the release record).
4. Existing rules still hold (CLAUDE.md, EVALUATION_PLAN.md): dev3val only through `eval.run_v3`; test3 only in its
   one-shot run.

## 4. Release gate (configuration, set by humans)

`eval/ml/release_gate.yaml` holds the thresholds: maximum leak rate (overall and per type), minimum overlap recall
and precision per type, maximum over-masking of benign strings, maximum p95 latency, minimum number of real examples.
**All are null as shipped.** They are a risk decision for the DPO, HR and the security owner, not a technical fact,
and this repository does not propose values. Until they are set, a release run reports `UNDECIDED` and lists the
missing thresholds. Set them in a reviewed change before the first release run, and record who decided, when and why
as a dated amendment in EVALUATION_PLAN.md.

## 5. Human-labelled real tickets (inside the boundary)

Real tickets never leave the boundary and never enter this repository. HR/DPO staff label them on an approved machine
inside the boundary; the harness runs there too.

### Format (`--format human`, one JSON object per line)

```json
{"id": "rs-0001", "genre": "payroll_email", "text": "<the ticket text exactly as exported>",
 "spans": [{"start": 5, "end": 17, "type": "PERSON"}, {"start": 40, "end": 54, "type": "AADHAAR"}],
 "benign": [{"start": 60, "end": 68}],
 "labeller": "L03", "reviewer": "L07", "status": "agreed", "labelled_on": "2026-11-15", "guide_version": "1"}
```

- `start`/`end`: character offsets into `text` (Python string indices, end exclusive). A labelling tool that shows
  the text and records selections produces them; never retype the text.
- `labeller`/`reviewer`: pseudonymous ids of the two people; `status`: `agreed` when the second person accepts every
  span, else `disputed` (resolve by discussion, then set `agreed`, or leave it disputed: it is not scored).
- Store the file with the access controls of the source system; delete it per the DPO's retention decision.

### Labelling guide (version 1)

Label **every** occurrence of a value that identifies or describes a person, with these types:

| Type | Label | Do not label |
|---|---|---|
| PERSON | names of people, including first name alone, surname with honorific (label the name, not "Mr"), initials with surname, names in signatures, e-mail display names | company, team, product, place names; job titles alone ("HR Manager") |
| EMAIL, PHONE | personal or work addresses and numbers of people | role mailboxes and helpdesk numbers listed by the contract (`org_contacts`): mark them `benign` |
| EMPLOYEE_ID | employee codes | ticket, document, PO numbers: mark them `benign` (or IDENTIFIER if they identify a person's case) |
| AADHAAR, PAN, BANK_ACCOUNT, IFSC, CARD, UPI_ID | the number or ID as written, separators included | |
| COMPENSATION | a person's pay amounts (salary, CTC, bonus, arrears) | policy limits, published caps, prices: `benign` |
| DATE_OF_BIRTH | birth dates | other dates |
| SECRET | passwords, tokens, keys | |
| IDENTIFIER | other numbers that identify a person or their case (passport, policy, claim number) | |

Rules: label the shortest span that contains the whole value (no surrounding spaces or punctuation); label every
repetition; when unsure whether something is personal, label it and add a note column in your tool (the reviewer
decides); mark as `benign` a sample of strings that look personal but are not (role mailboxes, company IDs, public
constants), so over-masking can be measured. Two people label independently, then compare; report the agreement rate
with the results.

## 6. Cross-checks and third-party backends (dev only)

Optional dependencies live in `requirements/eval.in` / `eval.txt` (hashed); every test that needs them skips cleanly
when they are missing. None of them is imported by `pgw/`.

### 6.1 nervaluate cross-check (MIT)

`python -m eval.ml.run ... --nervaluate` adds nervaluate's four SemEval-2013 schemes (strict, exact, partial, type), overall
and per type, to the result (`eval/ml/nerval.py`). `tests/test_nervaluate_crosscheck.py` pins, on fixed fixtures:

| nervaluate | repo | relation |
|---|---|---|
| strict | exact | identical precision / recall / F1, overall and per type |
| type (`ent_type`) | overlap | identical when matches are one-to-one |
| exact (boundaries, type ignored), partial (0.5 credit) | none | no repo analogue |

Where they differ (each pinned by a test): nervaluate matches one prediction to one gold span, the repo's overlap is
many-to-one (two short predictions inside one long gold both count as correct here, the second is spurious there); a
right-boundary wrong-type prediction is a hit in nervaluate's type-blind schemes but a miss plus a false positive here;
a type with gold and no predictions has precision 0.0 here, 0 there (and 1.0 here when it has no gold either); nervaluate
offsets are end-inclusive (the adapter converts).

### 6.2 Presidio backend (MIT; evaluation only)

`--backend presidio` (Presidio alone) and `--backend union` (today's stack plus Presidio spans that overlap none of the
stack's) use `presidio-analyzer` with the installed spaCy model `en_core_web_lg` and Presidio's India recognizers
(IN_AADHAAR, IN_PAN, IN_GSTIN, IN_PASSPORT, IN_VOTER, IN_VEHICLE_REGISTRATION), which the default registry leaves off
(`eval/ml/presidio_backend.py`). Mapping (reviewed): PERSON, EMAIL_ADDRESS->EMAIL, PHONE_NUMBER->PHONE, IN_AADHAAR->AADHAAR,
IN_PAN->PAN, CREDIT_CARD->CARD; IN_PASSPORT, IN_VOTER, IN_VEHICLE_REGISTRATION and IN_GSTIN->IDENTIFIER. Every other Presidio
entity (LOCATION, ORGANIZATION, DATE_TIME, URL, US_*, ...) is ignored and counted under `backend_notes.ignored_entities`;
results under `--presidio-min-score` (default 0.4) are dropped and counted. Presidio has no recognizer for UPI ids, IFSC,
bank accounts, employee ids, compensation, birth dates or secrets, so it can only ever add to those types' misses.

No network I/O: the one Presidio component that fetches (the e-mail recognizer's `tldextract` call downloads the public-suffix
list) is replaced by a subclass using a snapshot-only extractor; `tests/test_presidio_backend.py` blocks sockets while it
builds the engine and analyses text, and shows that the upstream default would have touched the network.

### 6.3 Dev comparison: current stack vs Presidio vs union

**Dev only, not a release number.** Dev corpora are what the regexes and heuristics were tuned on; the ml/train rows are
synthetic genres. Generated by `python scripts/ml/compare_dev_backends.py --sample 1000 --ml-train-docs 1000`
(deterministic every-k-th sample of up to 1000 rows per corpus; "current" is the stack with no NER, as shipped by default;
"+ spaCy NER" adds `en_core_web_lg` to it, the fair comparison for Presidio's PERSON, which is the same model).
Precision/recall are the repo's overlap definition; leak rate is the share of gold values still visible after masking.

**dev.jsonl** (1000 documents)

| backend | overlap P | overlap R | exact P | exact R | leak rate | non-PII chars masked | p50 ms |
|---|---|---|---|---|---|---|---|
| current stack | 0.839 | 0.914 | 0.827 | 0.904 | 0.086 | 0.0430 | 4.0 |
| current + spaCy NER | 0.844 | 0.974 | 0.833 | 0.964 | 0.026 | 0.0440 | 18.4 |
| Presidio | 0.770 | 0.428 | 0.683 | 0.380 | 0.551 | 0.0325 | 24.9 |
| union (current + Presidio) | 0.796 | 0.968 | 0.775 | 0.946 | 0.036 | 0.0607 | 30.6 |

Overlap recall / precision by type (current | current + NER | Presidio | union):

| type | gold | current R | +NER R | Presidio R | union R | current P | +NER P | Presidio P | union P |
|---|---|---|---|---|---|---|---|---|---|
| PERSON | 2901 | 0.776 | 0.932 | 0.638 | 0.917 | 0.966 | 0.961 | 0.768 | 0.822 |
| EMAIL | 435 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.967 | 1.000 |
| PHONE | 407 | 1.000 | 1.000 | 0.968 | 1.000 | 1.000 | 1.000 | 0.620 | 0.927 |
| AADHAAR | 122 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.758 | 1.000 |
| PAN | 264 | 1.000 | 1.000 | 0.989 | 1.000 | 1.000 | 1.000 | 0.856 | 0.936 |
| BANK_ACCOUNT | 376 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| IFSC | 344 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| CARD | 176 | 1.000 | 1.000 | 0.920 | 1.000 | 1.000 | 1.000 | 0.779 | 1.000 |
| UPI_ID | 119 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| SECRET | 197 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| EMPLOYEE_ID | 1041 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| COMPENSATION | 813 | 1.000 | 1.000 | 0.000 | 1.000 | 0.841 | 0.841 | 0.000 | 0.841 |
| DATE_OF_BIRTH | 340 | 1.000 | 1.000 | 0.000 | 1.000 | 0.958 | 0.958 | 0.000 | 0.958 |

**dev2.jsonl** (1000 documents)

| backend | overlap P | overlap R | exact P | exact R | leak rate | non-PII chars masked | p50 ms |
|---|---|---|---|---|---|---|---|
| current stack | 0.883 | 0.962 | 0.882 | 0.961 | 0.033 | 0.0532 | 3.8 |
| current + spaCy NER | 0.874 | 0.974 | 0.872 | 0.972 | 0.022 | 0.0568 | 16.9 |
| Presidio | 0.825 | 0.442 | 0.778 | 0.417 | 0.536 | 0.0372 | 24.0 |
| union (current + Presidio) | 0.857 | 0.982 | 0.850 | 0.974 | 0.013 | 0.0659 | 27.4 |

Overlap recall / precision by type (current | current + NER | Presidio | union):

| type | gold | current R | +NER R | Presidio R | union R | current P | +NER P | Presidio P | union P |
|---|---|---|---|---|---|---|---|---|---|
| PERSON | 1521 | 0.787 | 0.861 | 0.602 | 0.915 | 0.878 | 0.819 | 0.721 | 0.745 |
| EMAIL | 651 | 1.000 | 1.000 | 0.943 | 1.000 | 0.962 | 0.962 | 0.919 | 0.962 |
| PHONE | 1436 | 1.000 | 1.000 | 0.993 | 1.000 | 0.997 | 0.997 | 0.861 | 0.986 |
| AADHAAR | 497 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.947 | 1.000 |
| PAN | 723 | 1.000 | 1.000 | 0.996 | 1.000 | 1.000 | 1.000 | 0.964 | 0.968 |
| BANK_ACCOUNT | 378 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| IFSC | 336 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| CARD | 129 | 1.000 | 1.000 | 0.899 | 1.000 | 1.000 | 1.000 | 0.712 | 1.000 |
| UPI_ID | 385 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| SECRET | 330 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| EMPLOYEE_ID | 1449 | 0.970 | 0.970 | 0.000 | 0.970 | 1.000 | 1.000 | 0.000 | 1.000 |
| COMPENSATION | 830 | 1.000 | 1.000 | 0.000 | 1.000 | 0.977 | 0.977 | 0.000 | 0.977 |
| DATE_OF_BIRTH | 1045 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |

**dev3build.jsonl** (900 documents)

| backend | overlap P | overlap R | exact P | exact R | leak rate | non-PII chars masked | p50 ms |
|---|---|---|---|---|---|---|---|
| current stack | 0.626 | 0.498 | 0.611 | 0.486 | 0.371 | 0.0270 | 6.4 |
| current + spaCy NER | 0.622 | 0.582 | 0.606 | 0.568 | 0.287 | 0.0345 | 31.7 |
| Presidio | 0.690 | 0.285 | 0.589 | 0.242 | 0.688 | 0.0191 | 44.5 |
| union (current + Presidio) | 0.604 | 0.577 | 0.578 | 0.552 | 0.289 | 0.0410 | 51.3 |

Overlap recall / precision by type (current | current + NER | Presidio | union):

| type | gold | current R | +NER R | Presidio R | union R | current P | +NER P | Presidio P | union P |
|---|---|---|---|---|---|---|---|---|---|
| PERSON | 3374 | 0.482 | 0.719 | 0.496 | 0.703 | 0.807 | 0.722 | 0.693 | 0.705 |
| EMAIL | 384 | 0.997 | 0.997 | 0.948 | 0.997 | 1.000 | 1.000 | 1.000 | 1.000 |
| PHONE | 360 | 1.000 | 1.000 | 0.981 | 1.000 | 0.909 | 0.909 | 0.523 | 0.684 |
| AADHAAR | 115 | 0.556 | 0.556 | 1.000 | 0.556 | 1.000 | 1.000 | 0.975 | 1.000 |
| PAN | 211 | 1.000 | 1.000 | 0.995 | 1.000 | 1.000 | 1.000 | 0.820 | 0.854 |
| BANK_ACCOUNT | 349 | 0.948 | 0.948 | 0.000 | 0.948 | 0.962 | 0.962 | 0.000 | 0.962 |
| IFSC | 336 | 1.000 | 1.000 | 0.000 | 1.000 | 0.918 | 0.918 | 0.000 | 0.918 |
| CARD | 4 | 1.000 | 1.000 | 0.750 | 1.000 | 1.000 | 1.000 | 0.231 | 1.000 |
| UPI_ID | 29 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| SECRET | 31 | 0.968 | 0.968 | 0.000 | 0.968 | 0.545 | 0.545 | 0.000 | 0.545 |
| EMPLOYEE_ID | 1172 | 0.622 | 0.622 | 0.000 | 0.622 | 0.797 | 0.797 | 0.000 | 0.797 |
| COMPENSATION | 1832 | 0.222 | 0.222 | 0.000 | 0.222 | 0.697 | 0.697 | 0.000 | 0.697 |
| DATE_OF_BIRTH | 757 | 0.325 | 0.325 | 0.000 | 0.325 | 0.984 | 0.984 | 0.000 | 0.984 |

**ml/train synthetic (1000 docs, seed 7)** (1000 documents)

| backend | overlap P | overlap R | exact P | exact R | leak rate | non-PII chars masked | p50 ms |
|---|---|---|---|---|---|---|---|
| current stack | 0.950 | 0.825 | 0.938 | 0.815 | 0.159 | 0.0212 | 5.7 |
| current + spaCy NER | 0.944 | 0.868 | 0.925 | 0.853 | 0.116 | 0.0264 | 23.9 |
| Presidio | 0.787 | 0.341 | 0.643 | 0.287 | 0.635 | 0.0505 | 32.7 |
| union (current + Presidio) | 0.896 | 0.862 | 0.866 | 0.834 | 0.129 | 0.0547 | 42.1 |

Overlap recall / precision by type (current | current + NER | Presidio | union):

| type | gold | current R | +NER R | Presidio R | union R | current P | +NER P | Presidio P | union P |
|---|---|---|---|---|---|---|---|---|---|
| PERSON | 4446 | 0.632 | 0.794 | 0.490 | 0.769 | 0.987 | 0.951 | 0.677 | 0.793 |
| EMAIL | 939 | 1.000 | 1.000 | 1.000 | 1.000 | 0.968 | 0.968 | 0.968 | 0.968 |
| PHONE | 1059 | 0.993 | 0.993 | 0.983 | 0.993 | 0.996 | 0.996 | 0.760 | 0.948 |
| AADHAAR | 234 | 0.974 | 0.974 | 0.812 | 0.974 | 1.000 | 1.000 | 0.884 | 1.000 |
| PAN | 1318 | 0.999 | 0.999 | 0.965 | 0.999 | 1.000 | 1.000 | 0.970 | 0.984 |
| BANK_ACCOUNT | 1143 | 0.958 | 0.958 | 0.000 | 0.958 | 0.987 | 0.987 | 0.000 | 0.987 |
| IFSC | 1101 | 0.996 | 0.996 | 0.000 | 0.996 | 1.000 | 1.000 | 0.000 | 1.000 |
| UPI_ID | 76 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| EMPLOYEE_ID | 2293 | 0.930 | 0.930 | 0.000 | 0.930 | 1.000 | 1.000 | 0.000 | 1.000 |
| COMPENSATION | 3432 | 0.710 | 0.710 | 0.000 | 0.710 | 0.978 | 0.978 | 0.000 | 0.978 |
| DATE_OF_BIRTH | 393 | 0.873 | 0.873 | 0.000 | 0.873 | 1.000 | 1.000 | 0.000 | 1.000 |
| IDENTIFIER | 162 | 1.000 | 1.000 | 0.216 | 1.000 | 0.221 | 0.223 | 1.000 | 0.221 |

Presidio min score 0.4; unmapped Presidio entities are ignored and counted in the JSON output (`notes.ignored_entities`).

Reading it (dev data, so indicative only):
- Presidio alone is far behind the stack: overlap recall 0.29 to 0.44 on the dev corpora (0.34 on ml/train synthetic) against 0.50 to 0.96, and
  its precision is lower on PHONE (0.52 to 0.86), PAN and PERSON. It has nothing for 8 of the 14 gateway types.
- Union (stack + Presidio) beats the stack alone on PERSON recall (dev 0.78 -> 0.92, dev2 0.79 -> 0.92) and on overall leak rate
  (dev 0.086 -> 0.036, dev2 0.033 -> 0.013), but the stack with spaCy NER already gets most of that gain (dev PERSON recall 0.93) at
  higher precision; union adds false positives (non-PII characters masked rises from 0.043 to 0.061 on dev; PHONE precision falls).
- Where Presidio scores better is a labelling artefact, not a detection gain: on dev3build the stack finds all 115 Aadhaar
  numbers but reports 51 of them as AADHAAR_LIKE (the cue-less checksum backstop, by design a different type), which the
  type-strict scorer counts as a miss; Presidio calls them AADHAAR. The union keeps the stack's verdict (0.556).
- Nothing here changes the product: adoption of any of these is a separate decision after held-out measurement (ML_TRAINING.md).
