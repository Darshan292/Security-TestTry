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

## Dev bench of open detectors, 2026-10-30 (dev only, not a release number)

Three open detectors run through the gateway's own CPU runtime slot (`OnnxTokenClassifier` / `OnnxSpanModel`, pinned by the
`pgw-ml-artifact/1` manifest digest) and scored on **dev data only**: `eval/data/dev.jsonl`, `eval/data2/dev2.jsonl`,
`eval/data3/dev3build.jsonl` and 1000 synthetic `ml/train` documents (seed 7). 1000 documents per corpus (every k-th row of
dev and dev2; all 900 of dev3build). No held-out, test, tester or dev3val file was opened (the scripts refuse them from the
path). Numbers are overlap recall / precision as defined in section 1. These corpora are synthetic and share generators with the
stack's own tests; they say nothing about real tickets.

Harness: `scripts/ml/bench_matrix.py` (backends `current`, `pipeline_ml`, `ml_only` of `eval/ml/backends.py`; "+" means the
gateway pipeline with the ML slot, i.e. its merge precedence: ID regex > ML > heuristics), `scripts/ml/bench_label_analysis.py`,
`scripts/ml/bench_latency.py`. ML output was memoised per text across configs (`--cache-ml`), so the **latency columns of the
combined configs exclude the model**; model latency is in the latency table below. spaCy is `en_core_web_lg`.

### Bundles (every download pinned to the repository's commit; sha256 of every file in each MODEL_CARD.md)

| bundle | source @ revision | licence | precision | manifest digest (pin) |
|---|---|---|---|---|
| `pf_int8_v2` | openai/privacy-filter @ 7ffa9a043d54d1be65afb281eddf0ffbe629385b, `onnx/model_quantized.onnx` + data, repacked | Apache-2.0 | int8 (official) | `70d89252c39a8a6bc58799cbbd9cf6af8d1a3c6ed4eacd75b73ca0a309d5c9ef` |
| `pf_q4_v2` | same, `onnx/model_q4.onnx` + data, repacked | Apache-2.0 | q4 (official) | `cbabf1f661ea8180a48690ae4c341a38f2ea260b0ebae88ef55770258fe46b28` |
| `pf_int8_argmax` | same weights as pf_int8_v2 without the `viterbi` block (ablation) | Apache-2.0 | int8 | `df7b2db78e734ae83e30b9ca63356315989212bf62caca38342c0dd88dbdeba2` |
| `gliner_fp32_v2` | urchade/gliner_multi_pii-v1 @ 1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d, exported with gliner 0.2.29 `export_to_onnx` (encoder tokenizer microsoft/mdeberta-v3-base @ a0484667b22365f84929a935b5e50a51f71f159d, MIT) | Apache-2.0 | fp32 | `1222c2e7e822bd9d3a569780e804798b3f6434863e9e24819ac26276ad4bd1c3` |
| `gliner_int8` | quantize_dynamic of the above | Apache-2.0 | int8 | `827fc448bbf2ebd6cb490aa0fd56b7fbfb1a9f7cc45d20601f16cb1556f1eb59` (**rejected, see below**) |
| `indicner_int8` | ai4bharat/IndicNER @ b56cb5e9bdf37cb0d653e658b3d4dbe563bebf34 (gated, fetched with a token), torch.onnx.export opset 17 + quantize_dynamic | MIT | int8 | `583cc4870a1a4baf0c3741746629114f8c363f9c5c3da1ea4a9716b6b511f08a` |
| `indicner_fp32` | same, not quantised | MIT | fp32 | `fafd74289c676c75cc8a88683259258ef43f107ff67b98962857ed4c6356ff28` |

Bundles are under `/home/user/models/` (outside git, never committed). The digests above are for the bundles with the label
maps in the table below (private_address -> ADDRESS); the bundle's label map is part of `train_meta.json`, which is in the
digest, so changing a mapping changes the pin.

### What runs and what does not

- **openai/privacy-filter int8 and q4: run** after repacking to one file (parity max abs diff 0 against ONNX Runtime's own
  load of the external-data model). Graph inputs `input_ids`, `attention_mask`; output `[B, T, 33]`. No runtime change beyond
  BIOES + Viterbi + the external-data guard was needed. Settings: `max_length` 512, `stride` 64, `aggregation` token, no
  special tokens, default Viterbi operating point (all biases 0).
- **GLiNER multi PII v1 fp32: runs** through `OnnxSpanModel` with no runtime change: the export has exactly the six inputs the
  runtime feeds (`input_ids, attention_mask, words_mask, text_lengths, span_idx, span_mask`, output `logits`).
  `max_length` 384 (the model's training length), `max_words` 128, threshold 0.5. **int8 is unusable:** dynamic int8
  quantisation of the DeBERTa graph collapses it (31 spans on 200 dev documents, recall 0.017 against 0.734 for fp32). fp32 only.
- **IndicNER: runs** (gated repo, fetched with `HF_TOKEN`). BERT-multilingual token classifier, labels PER/LOC/ORG; only PER maps
  (LOC and ORG are IGNORE). Score threshold 0.3 (at 0.5 it drops most English names; confidences on Latin-script English are low).

### Label map for privacy-filter (reviewed, `ml/train/detectors.py` `PRIVACY_FILTER_MAP`)

| model label | gateway type | note |
|---|---|---|
| private_person | PERSON | |
| private_email | EMAIL | |
| private_phone | PHONE | also fires on Aadhaar-like 12-digit numbers (below) |
| account_number | **BANK_ACCOUNT in the bundle; IGNORE recommended** | fires on every long identifier (below) |
| secret | SECRET | precision 0.75-0.82 alone, 0.09-0.11 on dev3build in the union |
| private_date | IGNORE | measurement only (below) |
| private_address | ADDRESS | no corpus labels ADDRESS: not scored (below) |
| private_url | IGNORE | no URL type |

### Results (dev only)

PERSON overlap recall / precision (dev only; 1000 documents per corpus)

| config | dev | dev2 | dev3build | ml/train synth |
|---|---|---|---|---|
| current | 0.776 / 0.966 | 0.787 / 0.878 | 0.482 / 0.807 | 0.632 / 0.987 |
| current+spacy | 0.932 / 0.961 | 0.861 / 0.819 | 0.719 / 0.722 | 0.794 / 0.954 |
| pf alone | 0.864 / 0.947 | 0.817 / 0.914 | 0.800 / 0.884 | 0.820 / 0.853 |
| current+pf | 0.961 / 0.943 | 0.920 / 0.754 | 0.890 / 0.774 | 0.937 / 0.872 |
| current+pf [acct=IGNORE] | 0.961 / 0.943 | 0.922 / 0.755 | 0.891 / 0.769 | 0.942 / 0.872 |
| current+spacy+pf [acct=IGNORE] | 0.975 / 0.935 | 0.936 / 0.711 | 0.923 / 0.696 | 0.961 / 0.862 |
| current+pf [acct+secret IGNORE] | 0.961 / 0.943 | 0.922 / 0.755 | 0.892 / 0.769 | 0.942 / 0.872 |
| gliner alone | 0.794 / 0.967 | 0.872 / 0.817 | 0.926 / 0.943 | 0.907 / 0.996 |
| current+gliner | 0.987 / 0.954 | 0.983 / 0.771 | 0.964 / 0.825 | 0.990 / 0.988 |
| current+spacy+gliner | 0.997 / 0.944 | 0.988 / 0.725 | 0.967 / 0.725 | 0.992 / 0.955 |
| indicner alone | 0.678 / 0.926 | 0.636 / 0.778 | 0.627 / 0.948 | 0.606 / 0.853 |
| current+indicner | 0.926 / 0.952 | 0.928 / 0.805 | 0.847 / 0.846 | 0.914 / 0.922 |
| current+spacy+indicner | 0.968 / 0.944 | 0.955 / 0.756 | 0.906 / 0.755 | 0.942 / 0.892 |

All types: overlap recall / precision / leak rate

| config | dev | dev2 | dev3build | ml/train synth |
|---|---|---|---|---|
| current | 0.914 / 0.839 / 0.086 | 0.962 / 0.883 / 0.033 | 0.498 / 0.626 / 0.371 | 0.825 / 0.950 / 0.159 |
| current+spacy | 0.974 / 0.844 / 0.026 | 0.974 / 0.874 / 0.022 | 0.582 / 0.622 / 0.287 | 0.868 / 0.945 / 0.116 |
| pf alone | 0.507 / 0.629 / 0.300 | 0.335 / 0.442 / 0.332 | 0.388 / 0.508 / 0.398 | 0.390 / 0.564 / 0.393 |
| current+pf | 0.895 / 0.748 / 0.015 | 0.795 / 0.685 / 0.012 | 0.576 / 0.521 / 0.207 | 0.780 / 0.765 / 0.072 |
| current+pf [acct=IGNORE] | 0.974 / 0.822 / 0.015 | 0.921 / 0.803 / 0.012 | 0.634 / 0.622 / 0.216 | 0.896 / 0.892 / 0.074 |
| current+spacy+pf [acct=IGNORE] | 0.979 / 0.820 / 0.010 | 0.923 / 0.795 / 0.010 | 0.645 / 0.599 / 0.205 | 0.901 / 0.889 / 0.070 |
| current+pf [acct+secret IGNORE] | 0.974 / 0.825 / 0.015 | 0.922 / 0.809 / 0.012 | 0.634 / 0.635 / 0.222 | 0.896 / 0.892 / 0.074 |
| gliner alone | 0.734 / 0.871 / 0.213 | 0.489 / 0.812 / 0.441 | 0.579 / 0.709 / 0.363 | 0.553 / 0.830 / 0.393 |
| current+gliner | 0.968 / 0.790 / 0.005 | 0.910 / 0.774 / 0.003 | 0.729 / 0.606 / 0.128 | 0.906 / 0.904 / 0.044 |
| current+spacy+gliner | 0.972 / 0.788 / 0.002 | 0.910 / 0.766 / 0.002 | 0.730 / 0.580 / 0.127 | 0.906 / 0.897 / 0.044 |
| indicner alone | 0.261 / 0.926 / 0.733 | 0.100 / 0.778 / 0.885 | 0.222 / 0.948 / 0.781 | 0.162 / 0.853 / 0.840 |
| current+indicner | 0.965 / 0.836 / 0.028 | 0.933 / 0.828 / 0.011 | 0.625 / 0.667 / 0.242 | 0.893 / 0.932 / 0.083 |
| current+spacy+indicner | 0.982 / 0.835 / 0.012 | 0.937 / 0.819 / 0.007 | 0.646 / 0.642 / 0.221 | 0.901 / 0.925 / 0.075 |

Per type, dev (R / P): current | current+pf (account_number->BANK_ACCOUNT) | current+pf (account_number IGNORE) | current+gliner

| type | gold | current | current+pf | current+pf [acct=IGNORE] | current+gliner |
|---|---|---|---|---|---|
| AADHAAR | 122 | 1.00 / 1.00 | 0.80 / 1.00 | 0.80 / 1.00 | 0.80 / 0.75 |
| BANK_ACCOUNT | 376 | 1.00 / 1.00 | 0.98 / 0.24 | 0.98 / 1.00 | 0.84 / 0.95 |
| CARD | 176 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| COMPENSATION | 813 | 1.00 / 0.84 | 0.95 / 0.83 | 0.98 / 0.84 | 0.99 / 0.74 |
| DATE_OF_BIRTH | 340 | 1.00 / 0.96 | 1.00 / 0.96 | 1.00 / 0.96 | 1.00 / 0.82 |
| EMAIL | 435 | 1.00 / 1.00 | 1.00 / 0.98 | 1.00 / 0.98 | 1.00 / 0.90 |
| EMPLOYEE_ID | 1041 | 1.00 / 1.00 | 0.63 / 1.00 | 0.98 / 1.00 | 0.98 / 0.93 |
| IFSC | 344 | 1.00 / 1.00 | 0.46 / 1.00 | 1.00 / 1.00 | 0.84 / 0.97 |
| PAN | 264 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.97 |
| PERSON | 2901 | 0.78 / 0.97 | 0.96 / 0.94 | 0.96 / 0.94 | 0.99 / 0.95 |
| PHONE | 407 | 1.00 / 1.00 | 0.90 / 0.95 | 0.95 / 0.95 | 0.93 / 0.92 |
| SECRET | 197 | 1.00 / 1.00 | 1.00 / 0.81 | 1.00 / 0.81 | 0.99 / 0.87 |
| UPI_ID | 119 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.72 |

Per type, dev2 (R / P): current | current+pf (account_number->BANK_ACCOUNT) | current+pf (account_number IGNORE) | current+gliner

| type | gold | current | current+pf | current+pf [acct=IGNORE] | current+gliner |
|---|---|---|---|---|---|
| AADHAAR | 497 | 1.00 / 1.00 | 0.15 / 1.00 | 0.15 / 1.00 | 0.15 / 0.50 |
| BANK_ACCOUNT | 378 | 1.00 / 1.00 | 0.98 / 0.16 | 0.98 / 1.00 | 0.80 / 0.99 |
| CARD | 129 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| COMPENSATION | 830 | 1.00 / 0.98 | 0.98 / 0.98 | 0.99 / 0.98 | 1.00 / 0.77 |
| DATE_OF_BIRTH | 1045 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.98 |
| EMAIL | 651 | 1.00 / 0.96 | 1.00 / 0.86 | 1.00 / 0.86 | 1.00 / 0.92 |
| EMPLOYEE_ID | 1449 | 0.97 / 1.00 | 0.60 / 1.00 | 0.94 / 1.00 | 0.86 / 0.91 |
| IFSC | 336 | 1.00 / 1.00 | 0.70 / 1.00 | 1.00 / 1.00 | 0.98 / 0.86 |
| PAN | 723 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.97 |
| PERSON | 1521 | 0.79 / 0.88 | 0.92 / 0.75 | 0.92 / 0.75 | 0.98 / 0.77 |
| PHONE | 1436 | 1.00 / 1.00 | 0.55 / 0.97 | 0.97 / 0.98 | 0.95 / 1.00 |
| SECRET | 330 | 1.00 / 1.00 | 0.99 / 0.80 | 1.00 / 0.80 | 1.00 / 0.95 |
| UPI_ID | 385 | 1.00 / 1.00 | 0.76 / 1.00 | 0.76 / 1.00 | 0.79 / 0.87 |

Per type, dev3build (R / P): current | current+pf (account_number->BANK_ACCOUNT) | current+pf (account_number IGNORE) | current+gliner

| type | gold | current | current+pf | current+pf [acct=IGNORE] | current+gliner |
|---|---|---|---|---|---|
| AADHAAR | 115 | 0.56 / 1.00 | 0.51 / 1.00 | 0.51 / 1.00 | 0.51 / 0.20 |
| BANK_ACCOUNT | 349 | 0.95 / 0.96 | 0.94 / 0.12 | 0.93 / 0.96 | 0.49 / 0.80 |
| CARD | 4 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.44 |
| COMPENSATION | 1832 | 0.22 / 0.70 | 0.20 / 0.69 | 0.21 / 0.68 | 0.50 / 0.66 |
| DATE_OF_BIRTH | 757 | 0.33 / 0.98 | 0.33 / 0.98 | 0.33 / 0.98 | 0.56 / 0.77 |
| EMAIL | 384 | 1.00 / 1.00 | 1.00 / 0.99 | 1.00 / 0.99 | 1.00 / 0.96 |
| EMPLOYEE_ID | 1172 | 0.62 / 0.80 | 0.21 / 0.65 | 0.59 / 0.80 | 0.69 / 0.57 |
| IFSC | 336 | 1.00 / 0.92 | 0.72 / 0.89 | 1.00 / 0.92 | 0.96 / 0.39 |
| OOT | 591 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 0.00 |
| PAN | 211 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.82 |
| PERSON | 3374 | 0.48 / 0.81 | 0.89 / 0.77 | 0.89 / 0.77 | 0.96 / 0.82 |
| PHONE | 360 | 1.00 / 0.91 | 0.96 / 0.86 | 0.97 / 0.86 | 0.97 / 0.81 |
| SECRET | 31 | 0.97 / 0.55 | 1.00 / 0.11 | 1.00 / 0.11 | 0.97 / 0.19 |
| UPI_ID | 29 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.21 |

Per type, ml/train synth (R / P): current | current+pf (account_number->BANK_ACCOUNT) | current+pf (account_number IGNORE) | current+gliner

| type | gold | current | current+pf | current+pf [acct=IGNORE] | current+gliner |
|---|---|---|---|---|---|
| AADHAAR | 234 | 0.97 / 1.00 | 0.77 / 1.00 | 0.77 / 1.00 | 0.77 / 0.72 |
| BANK_ACCOUNT | 1143 | 0.96 / 0.99 | 0.95 / 0.32 | 0.93 / 0.99 | 0.89 / 0.95 |
| COMPENSATION | 3432 | 0.71 / 0.98 | 0.68 / 0.98 | 0.70 / 0.98 | 0.76 / 0.91 |
| DATE_OF_BIRTH | 393 | 0.87 / 1.00 | 0.87 / 1.00 | 0.87 / 1.00 | 0.97 / 0.73 |
| EMAIL | 939 | 1.00 / 0.97 | 1.00 / 0.97 | 1.00 / 0.97 | 1.00 / 0.96 |
| EMPLOYEE_ID | 2293 | 0.93 / 1.00 | 0.40 / 1.00 | 0.91 / 1.00 | 0.84 / 0.89 |
| IDENTIFIER | 162 | 1.00 / 0.36 | 0.22 / 0.20 | 1.00 / 0.37 | 1.00 / 0.51 |
| IFSC | 1101 | 1.00 / 1.00 | 0.52 / 1.00 | 0.98 / 1.00 | 0.96 / 0.89 |
| PAN | 1318 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.94 |
| PERSON | 4446 | 0.63 / 0.99 | 0.94 / 0.87 | 0.94 / 0.87 | 0.99 / 0.99 |
| PHONE | 1059 | 0.99 / 1.00 | 0.94 / 0.97 | 0.98 / 0.97 | 0.93 / 0.99 |
| UPI_ID | 76 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.81 |


### Findings

1. **PERSON is where an open detector helps.** The stack alone gets PERSON recall 0.48-0.79 (spaCy NER adds 0.07-0.24);
   adding privacy-filter to the stack reaches 0.89-0.96 and GLiNER 0.96-0.99, at precision 0.75-0.99 (PERSON precision of the
   stack alone is 0.81-0.99; every addition costs precision, most on dev2/dev3build where non-person capitalised strings are
   frequent). GLiNER is the strongest PERSON detector on all four corpora, alone (0.79-0.93 recall) and in the union.
   IndicNER alone is weak on these English-script synthetic names (0.61-0.68) and is a smaller gain than spaCy.
2. **`account_number` -> BANK_ACCOUNT breaks the union.** The label covers PAN, IFSC, Aadhaar, card, employee ids, UPI and
   unlabelled numbers (dev, 500 documents: 968 hits, of which 126 overlap gold BANK_ACCOUNT, 195 EMPLOYEE_ID, 110 PAN, 103 IFSC,
   61 CARD, 49 AADHAAR, 290 no gold span). BANK_ACCOUNT precision falls to 0.12-0.32 and IFSC / EMPLOYEE_ID / UPI recall by
   up to 0.55 because the ML tier outranks the heuristic tier. With `account_number: IGNORE`, current+pf recovers (dev overall recall
   0.895 -> 0.974, dev2 0.795 -> 0.921, ml/train 0.780 -> 0.896) and the stack keeps those types. The leak rate is lower either way
   (a wrong type is not a leak) which is why the leak rate alone would mislead here. **Do not map account_number to a type.**
3. **Remaining losses in the union even with account_number ignored:** AADHAAR typed recall 1.00 -> 0.795 (dev), 0.149 (dev2),
   0.513 (dev3build). This is mostly **not** the open detector: with any ML slot configured (checked with IndicNER, which detects
   no numbers) the pipeline reports some cue-less 12-digit Aadhaar numbers as `AADHAAR_LIKE` instead of `AADHAAR` (same span, still
   masked, a different type; the type-strict scorer counts it as a miss). It is the same artefact the stack's own dev3build row shows
   (0.556) and it appears for GLiNER too (dev2 0.149). A further part is privacy-filter's `private_phone` covering Aadhaar numbers
   (dev2 sample: 40 of 198 gold Aadhaar). EMPLOYEE_ID and UPI recall dips (0.02-0.24 on dev2/dev3build) were not investigated.
4. **`private_date`:** it finds nearly every DATE_OF_BIRTH (98% of gold DOB on dev, 97% dev2, 44% dev3build) but most of its
   hits are not labelled DOB: of 626 hits on 500 dev documents 162 overlap gold DOB, 464 overlap nothing (74%); dev2 422 of 632
   are DOB, 168 no gold span, 42 on EMPLOYEE_ID; dev3build 193 of 697 are DOB, 501 no gold span. Mapping it to DATE_OF_BIRTH
   would over-mask joining dates, effective dates and the like. Kept IGNORE; the numbers are in
   `pf_label_analysis.json` (not committed; reproduced by `scripts/ml/bench_label_analysis.py`).
5. **`private_address`:** none of the dev corpora labels addresses, so recall/precision cannot be computed; the stack's rule
   detector finds no address in 500 dev or 500 dev2 documents (privacy-filter reports 32 and 11 spans there: cities, room lines). On dev3build
   (500 documents) privacy-filter reports 103 address spans in 43 documents and the stack's `places.py` rule reports 37 in 22 documents;
   32 of the stack's 37 are overlapped by a privacy-filter span, 21 of privacy-filter's 103 overlap a stack span. Privacy-filter
   spans include bare city names ("Pune", "Bengaluru"), "Floor 8, Room 270", full postal lines, and also numbers
   (an amount, a document id: 31 on COMPENSATION, 16 on DATE_OF_BIRTH gold, 7 on EMPLOYEE_ID in dev3build). A labelled address set is
   needed before ADDRESS can be scored; the map already points the label at ADDRESS so the bundle is ready.
6. **Viterbi vs argmax (privacy-filter alone, same weights, dev samples):** Viterbi lowers PERSON recall by 0.8-2.5 points and raises
   PERSON precision by 2.2-8.0 points (dev 0.864 / 0.947 vs 0.872 / 0.925; dev2 0.817 / 0.914 vs 0.828 / 0.834; dev3build 0.800 / 0.884 vs
   0.825 / 0.852; ml/train 0.820 / 0.853 vs 0.837 / 0.830). Overall alone: precision 0.629 vs 0.581 (dev). The README's decoding
   is kept. The biases are untuned (default 0.0); a recall-leaning operating point is a dev-tuning task for the owner.
7. **q4 vs int8** (privacy-filter alone, 500-document samples of dev and dev2, so not the identical sample as int8): overall R / P
   0.508 / 0.635 (dev) and 0.365 / 0.476 (dev2) against 0.507 / 0.629 and 0.335 / 0.442 for int8 on its 1000 documents; PERSON
   recall 0.850 / 0.851 against 0.864 / 0.817. No accuracy loss visible; q4 uses 2.7 GB instead of 4.7 GB and is no slower. Recommended
   if privacy-filter is used; a same-sample comparison would be cleaner before relying on it.
8. **Precision cost of unions:** non-PII characters masked rise from 0.043 (dev, stack) to 0.057 (+pf, account ignored) and 0.060
   (+gliner); dev2 0.053 -> 0.067 / 0.078. The leak rate falls (dev 0.086 -> 0.015 with pf, 0.005 with gliner; dev3build 0.371 ->
   0.216 / 0.128). The stack + spaCy already gets leak 0.026 on dev; the open detectors improve on it for PERSON and on dev3build.

### CPU latency and RAM (in-process runtime, one request, 512 tokens, this 4-core VM, 7 warm runs, median)

| bundle | 1 thread | 4 threads | peak RSS (incl. load) | load |
|---|---|---|---|---|
| privacy-filter int8 | 1041 ms | 386 ms | 4.7 GB | 11 s |
| privacy-filter q4 | 1164 ms | 440 ms | 2.7 GB | 7 s |
| GLiNER fp32 (prompt of 15 labels + 512 text tokens) | 3976 ms | 1561 ms | 3.4 GB | 8 s |
| IndicNER int8 | 443 ms | 222 ms | 0.54 GB | 1.3 s |

Per document in the dev runs (4 threads, whole documents, windows of 512 tokens): privacy-filter alone p50 155-235 ms, p95 310-940 ms;
GLiNER p50 245-730 ms, p95 about 1.0-2.3 s; IndicNER p50 45-145 ms. The stack alone is 3-8 ms (p50), spaCy adds about 15-25 ms.
Each gateway worker holds its own copy of the model: the RAM column is per worker (privacy-filter int8 spends about 3 GB
above its 1.6 GB file at load because the bytes, the parsed graph and the session coexist).

### Runtime changes made for this bench (`tests/test_ml_bioes.py`)

- `labels.json` scheme BIOES and an optional `viterbi` block (six transition biases, constrained BIOES Viterbi per window,
  `pgw.ml.decode.viterbi_labels`); the README of privacy-filter specifies this decoding.
- External-data ONNX: **repacked**, not supported in the format. The official int8 and q4 files are below the 2 GiB protobuf
  limit, so `scripts/ml/repack_onnx.py` rewrites them to one `model.onnx` (inputs pinned by sha256 before parsing, each file read
  once into memory, plain file names only, no symlinks, offset/length checked, parity check); this keeps one digest-covered file loaded
  from verified bytes. fp32 and fp16 cannot be repacked.
- `pgw.ml.onnx_util.reject_external_data`: **onnxruntime 1.31.1 loads external-data files of a model built from bytes from the
  process working directory** (measured: the same model loads when the data file is in the cwd and fails otherwise). A
  `model.onnx` with external references would pass the manifest digest and load a file the digest does not cover. The runtime now
  refuses such a model; a test places the data file in the cwd. (A docstring in a merged branch claiming ORT refuses this was wrong
  and has been corrected.)
- Reviewed label maps for privacy-filter, GLiNER (adds `address` -> ADDRESS) and IndicNER; ADDRESS allowed as a target of
  external-detector maps (trained models still exclude it, `ml/train/labels.py`).
- Tooling: `scripts/ml/build_external_bundle.py` (token and span bundles incl. Viterbi), `bench_matrix.py`, `bench_label_analysis.py`,
  `bench_latency.py`, `format_matrix.py`.

### For whoever reruns the held-out sets C and D (do this reading first)

- Nothing here is a release number and nothing was tuned on held-out data. Thresholds are the defaults (0.5; IndicNER 0.3); no
  `min_score_by_type` was tuned and the Viterbi biases are 0.
- The bundle digests above only match bundles built by `scripts/ml/build_external_bundle.py` from the pinned revisions; rebuild, do
  not reuse a different export. GLiNER and IndicNER ONNX files are exports made here (torch 2.15 CPU, gliner 0.2.29, transformers
  5.16.1); the export commands and the hashes of every input file are in each MODEL_CARD.md.
- Use `account_number: IGNORE` for privacy-filter in any union run (the bundle's own map says BANK_ACCOUNT because that is the reviewed
  default; override it in the profile `labels`, or the union loses IFSC/EMPLOYEE_ID/UPI/AADHAAR recall and BANK_ACCOUNT precision).
  Expect AADHAAR typed recall to drop (AADHAAR_LIKE relabel, finding 3) with any ML slot configured.
- `private_date` stays IGNORE; `private_address` -> ADDRESS has no labelled data yet, so report address hits, not recall.
- Per-type results differ strongly by corpus (dev3build is much harder: stack PERSON recall 0.48); compare each detector on the
  same documents (the sampling here is every k-th row, 1000 per corpus).
- GLiNER int8 and IndicNER at 0.5 are not usable; do not "fix" them by changing the export.
- Open-detector spans are lower precision than the stack's: expect more over-masking (non-PII characters masked 0.043 -> 0.057-0.078).
- Every worker holds a full model copy: 4.7 GB (pf int8), 2.7 GB (q4), 3.4 GB (GLiNER fp32); at 1 thread a 512-token window costs
  about 1 s (pf), 4 s (GLiNER).
