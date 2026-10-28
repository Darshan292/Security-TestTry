# ML training runbook (token classifier and content gate)

Audience: someone who is not an ML engineer. Everything below runs on free Kaggle (or Colab) GPUs; production inference is
CPU-only ONNX int8. **Training data is synthetic only. Read "Limitations" before trusting any number.**

## What gets built

| Artifact | Notebook | Base model (licence) | Output |
|---|---|---|---|
| Templates written by an LLM | `01_generate_templates` | Qwen/Qwen2.5-7B-Instruct (Apache-2.0); alt. mistralai/Mistral-7B-Instruct-v0.3 | `templates.jsonl`, `gate_templates.jsonl` |
| Token classifier (14 entity types) | `02_train_token_classifier` | FacebookAI/xlm-roberta-base (MIT); alt. google/muril-base-cased (Apache-2.0) | `token_classifier/` bundle |
| Content gate (13 labels + NONE) | `03_train_content_gate` | microsoft/Multilingual-MiniLM-L12-H384 (MIT); alt. xlm-roberta-base | `content_gate/` bundle |

How labels are made: the LLM writes **templates** with typed slots (`{{PERSON_1}}`, `{{AADHAAR_1}}`, `{{COMPENSATION_2}}`).
Python (`ml/train/synth.py`) validates the template, fills the slots with generated values (`values.py`: Verhoeff-valid Aadhaar,
Luhn-valid cards, PAN with holder-type letter, IFSC, UPI, Indian phone/amount/date formats, secrets, Indian names across
regions) and records exact character offsets. Labels never come from an LLM. Hard negatives (ticket numbers, statutory limits,
helplines, non-DOB dates, place names, organisations and words that look like names) are filled too and carry no span.
Structured genres (fixed-width payroll, CSV/TSV, salary slip, Form 16, forms, chat, email) are also generated with **no LLM at all**
(`synth.programmatic_records`), so a pipeline works even without notebook 01.

The label set equals the gateway's entity types minus `AADHAAR_LIKE` (a backstop verdict, not a kind of value) and `DERIVED`
(computed in the gateway, never in input text); `tests/ml_train/test_labels.py` derives it from `pgw.types.EntityType`.
Content-gate labels map to the contract stances in `ml/train/labels.py` (`GATE_TO_PURPOSE_DEPENDENT`): HEALTH/DISABILITY/PREGNANCY ->
`health_or_leave_reason`, PERFORMANCE_RATING -> `performance_rating`, CRIMINAL_OR_DISCIPLINARY -> `disciplinary_outcome`. The other
stances (`leave_balance`, `attendance`, `eligibility_result`, `derived_compensation`) are numeric/derived and stay contract declarations.

## Contamination rule (read first)

The sealed held-out set (`eval/**/heldout*.jsonl`, `test*.jsonl`, tester sets) must never be read by the generator, used as few-shot
examples or uploaded. Nothing in `ml/train` or the notebooks reads `eval/`; the package zip refuses data files. After downloading
templates, **run locally** before training on them:

```
python scripts/ml/validate_templates.py templates.jsonl --out templates.clean.jsonl      # also runs the repo detector on unslotted text
python scripts/ml/contamination_check.py --generated templates.clean.jsonl                # 8-gram + exact-value overlap; exit 1 = stop
```
After notebook 02, run it again on `work/data/train.jsonl` (download it from the output) the same way. Exit code 2 means no held-out file
was found (it fails closed; pass `--heldout path` explicitly).

## One-time setup on your machine

```
pip install kaggle                      # then put your API token in ~/.kaggle/kaggle.json (Kaggle > Settings > API > Create New Token)
python scripts/ml/package_for_kaggle.py --owner YOUR_KAGGLE_USERNAME
python scripts/ml/build_notebooks.py --owner YOUR_KAGGLE_USERNAME      # rewrites kernel-metadata.json ids (only needed once)
kaggle datasets create -p dist/kaggle_ml_train --dir-mode zip          # dataset "pgw-ml-train"
```
Kaggle may auto-extract the zip; the notebooks accept either layout.

## Steps, Kaggle web UI

1. **Dataset:** kaggle.com > Datasets > New Dataset > upload `dist/kaggle_ml_train/ml_train.zip` (or the folder) > name it `pgw-ml-train` > Create.
2. **Notebook 01:** Create > New Notebook > File > Import Notebook > upload `notebooks/kaggle/01_generate_templates/01_generate_templates.ipynb`.
   Right panel: *Add Input* > your dataset `pgw-ml-train`. *Session options*: Accelerator **GPU T4 x2**, Internet **On**.
   Hugging Face token: only needed if a model is gated; Qwen2.5-7B-Instruct and the three base models are not. If you must, add it under
   Add-ons > Secrets as `HF_TOKEN` (never paste it into a cell).
3. *Run All*. It prints the licence check, the backend (vLLM fp16 on 2xT4, else transformers 4-bit), a budget estimate, then progress.
   If the session stops, re-run: finished jobs are skipped (state in `templates/state.json`). *Save Version > Save & Run All (Commit)* to keep the output.
4. Download `templates/` from the notebook's Output tab. Run the two **local** checks above. Upload `templates.clean.jsonl` renamed to `templates.jsonl`
   and the cleaned `gate_templates.jsonl` as a new dataset `pgw-templates`.
5. **Notebook 02:** import `02_train_token_classifier.ipynb`, add inputs `pgw-ml-train` and `pgw-templates`, GPU T4 x2 (P100 also works), Internet On, Save & Run All.
   It fails on purpose if the licence is not allowed, if more than 1% of documents cannot be aligned to tokens, or if **int8 recall drops by more than 1 point**.
   Download `token_classifier/`.
6. **Notebook 03:** same with `03_train_content_gate.ipynb`; one T4 is enough. Download `content_gate/`.
7. Colab instead: upload `ml_train.zip` and the templates into `MyDrive/pgw_ml/input/`, open the notebook, choose a T4 runtime. Outputs and checkpoints are written to `MyDrive/pgw_ml/work/`. (Colab free sessions are shorter; checkpoints resume automatically.)

## Equivalent Kaggle CLI

```
kaggle datasets create -p dist/kaggle_ml_train --dir-mode zip
kaggle kernels push -p notebooks/kaggle/01_generate_templates
kaggle kernels status YOUR_KAGGLE_USERNAME/01-generate-templates
kaggle kernels output YOUR_KAGGLE_USERNAME/01-generate-templates -p work/nb01
# local checks, then upload the cleaned templates
mkdir -p dist/pgw_templates && cp work/nb01/templates/templates.clean.jsonl dist/pgw_templates/templates.jsonl && cp work/nb01/templates/gate_templates.jsonl dist/pgw_templates/
kaggle datasets init -p dist/pgw_templates      # set id to YOUR_KAGGLE_USERNAME/pgw-templates, license "other"
kaggle datasets create -p dist/pgw_templates
kaggle kernels push -p notebooks/kaggle/02_train_token_classifier
kaggle kernels push -p notebooks/kaggle/03_train_content_gate
kaggle kernels output YOUR_KAGGLE_USERNAME/02-train-token-classifier -p work/nb02
```
`kernel-metadata.json` enables GPU and Internet and lists the dataset sources; `kaggle kernels push` uses the GPU type from the account default (choose T4 x2 in the UI once if needed).

## Estimated GPU time (free tier is 30 h/week, 12 h per session)

| Notebook | Typical | Notes |
|---|---|---|
| 01 | 2-4 h (vLLM on T4 x2) or 8-10 h (4-bit, one T4) | defaults: 2400 template jobs + 900 gate jobs; stops itself at 10.5 h; the notebook prints a live estimate |
| 02 | 3-5 h | 60k documents, about 4 epochs, early stopping; export, quantisation and latency add ~15 min |
| 03 | 0.5-1 h | 30k short documents |

These are estimates, not measurements: throughput depends on the GPU Kaggle assigns. The notebooks print measured throughput.

## Bringing artifacts back

```
python scripts/ml/import_artifacts.py work/nb02/token_classifier --kind token_classifier
python scripts/ml/import_artifacts.py work/nb03/content_gate --kind content_gate
```
It verifies `sha256sums`, rejects unlisted files, checks the model card says synthetic-only and not validated on real data, checks the recorded int8
recall gate, and prints `model_sha256` (the hash of `model.onnx`) and `bundle_sha256` to pin in the signed profile. The runtime package (`pgw/ml`) is built
separately; if its profile uses a different field name, pin the model file hash. `--dest DIR` copies the verified bundle.

## What the bundle contains

`model.onnx` (int8, dynamic per-channel), tokenizer files, `config.json`, `labels.json` (label order, max length 512, stride 128, operating threshold),
`thresholds.json` (gate only), `metrics.json`, `MODEL_CARD.md`, `sha256sums`. `metrics.json` includes per-type precision/recall/F1 (strict and lenient), a
recall-versus-threshold sweep (entity if P(not O) >= tau; lower tau = more recall, less precision), per-genre recall, fp32-vs-int8 comparison and CPU latency
(ms per 512 tokens at 1 and 4 threads).

## Limitations (honest)

* **Synthetic-only training does not prove real-world recall.** Every number in `metrics.json` is measured on a synthetic development split drawn from
  the same generators and the same LLM family as the training data. It is optimistic, possibly by a lot.
* Splitting by template prevents near-duplicate leakage between train and dev, but not shared blind spots: the generator LLM, the value generators
  and the layouts are all our assumptions about what tickets look like.
* Real tickets contain formats, typos, scripts (Devanagari, Tamil, ...), OCR damage, screenshots and layouts that no generator here anticipates. The token classifier
  was trained on Latin-script text only.
* The content gate labels what the prompt asked for; a lexical cue check drops templates that ignore the instruction, but label noise remains, and
  generic-vs-specific ("a policy about X" vs "this person has X") is a subtle boundary.
* **The release gate is human-labelled, consented, in-boundary real tickets, which this repository cannot provide.** Until recall is measured on them, these
  models are a second line behind the deterministic detectors, never the only control, and no recall claim should be made from these metrics.
* Hard negatives teach the model what not to mask, but ticket numbers can legitimately be `IDENTIFIER` in the gateway (it aliases unknown reference formats unless
  the contract declares them business identifiers); the synthetic negatives use short forms only. Decide that policy in the contract, not in the model.
* Open licences are checked at run time against the Hub model card (`ml/train/licences.py`); re-check them when changing a base model, and never use
  ai4privacy datasets, piiranha, Llama, Gemma or Qwen2.5-3B.
