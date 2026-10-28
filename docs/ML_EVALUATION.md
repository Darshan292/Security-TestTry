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
