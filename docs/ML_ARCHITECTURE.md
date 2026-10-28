# ML architecture (model-agnostic foundation)

Status: the infrastructure is built and tested with tiny, randomly initialised models generated at test time. **No
trained model exists yet, and no model weights are in this repository.** Nothing here claims a detection quality: that
is measured with [ML_EVALUATION.md](ML_EVALUATION.md) once a model is trained.

## 1. Why

Four rounds of hand-written detectors reached a ceiling: on unseen genres 35-47 % of values leaked (external review,
SECURITY_BOUNDARY.md §8). The answer is not a fifth round of regexes. It is to stop relying on detection for the
guarantee, and to use a learned detector as an additional layer.

## 2. Architecture

| # | Layer | Role | Where |
|---|---|---|---|
| 1 | **Structured fields** for anything that leaves | the guarantee: contracts state per field what may leave (LOCAL_COMPUTE, declared subjects) | `pgw/product/contracts.py`, `local_compute.py` |
| 2 | **In-house CPU LLM** for free text | the guarantee for free text that cannot be made safe: it never leaves (PRIVATE route) | `deploy/LLAMA_SERVER.md`, `private_upstream_url` |
| 3 | **ML PII detector** + checksummed-ID regex | defence in depth on what is sent externally; regex only where a checksum or strict format makes it exact | `pgw/ml/` (`ml_detector`) |
| 4 | **ML special-category content gate** | keeps sensitive topics in-house even when the input is signed | `pgw/ml/gate.py` (`content_gate_model`, contract `content_gate`) |
| 5 | **Human evaluation** as the release gate | a model ships only on the DPO-approved real sample and the external tester's sets | `eval/ml/`, [ML_EVALUATION.md](ML_EVALUATION.md) |

### What is the guarantee and what is defence in depth

- **Guarantee (enforced in code, independent of any model):** mandatory signed contracts; free text leaves only with
  a valid source signature, otherwise it is refused or stays in-house; C4 and gated content stay in-house; the egress
  check refuses any detected or declared value in the outbound bytes; C3 never returns to the app; every failure
  fails closed.
- **Defence in depth (measured, never guaranteed):** what the ML detector finds in text that is allowed to leave, and
  what the content gate classifies as special-category. A model that misses a value lets it leave exactly as the
  regex layer did; that is why the release gate measures leaks on real data and why routing, not detection, carries
  the guarantee.

## 3. Interfaces (any future model drops in)

```python
# pgw/ml/base.py
Span(start: int, end: int, type: str, score: float)          # code-point offsets into the exact input; model label
SpanDetector.detect(text) -> list[Span]; detect_many(texts); warm()
ContentGate.classify(text) -> {label: score}; classify_many(texts); warm()
MlUnavailable                                                # any failure; the caller fails closed
```

Backends:

- `OnnxTokenClassifier` (`pgw/ml/token_classifier.py`): ONNX Runtime CPU, fp32 or int8; HF `tokenizers`
  tokenizer.json; BIO / BILOU / IO decoding (lenient: a stray I- starts an entity, it is never dropped); word
  aggregation `first` or `token`; sliding windows of `max_length` tokens overlapping by `stride`, each token taking
  the prediction from the window where it has most context; character offsets from the tokenizer (lone surrogates
  replaced length-preservingly); batching of windows across texts.
- `OnnxSpanModel` (`pgw/ml/span_model.py`): GLiNER-style span model without torch: the GLiNER ONNX export signature
  (input_ids, attention_mask, words_mask, text_lengths, span_idx, span_mask -> logits [B, W, K, C]), prompt
  `<<ENT>> label ... <<SEP>> words`, sigmoid, greedy flat decoding, word windows. Tested with a synthetic graph of the
  same signature only; validate a real export on dev data before use.
- `OnnxSequenceClassifier` (`pgw/ml/gate.py`): sequence classification, multi-label (sigmoid) or single-label
  (softmax), max over windows.
- `MlEntityStage` (`pgw/ml/runtime.py`) maps model labels to gateway types with the profile's `labels` and applies
  `min_score_by_type`; a label the profile does not pin, or a span outside the text, raises `MlUnavailable`.

## 4. Pipeline integration and precedence

`Protector(..., ner=spacy_or_None, ml=stage_or_None, heuristics=True)`:

- The ML detector runs on contracted purposes, on the same normalised views as spaCy (base, case-restored,
  de-spaced), in one batched call. `ner_model` set + `ml_detector` set = both run ("alongside"); `ner_model: null` +
  `ml_detector` = ML only ("instead"; production then needs no `ner_off_justification`).
- **Precedence** (only when an ML detector is configured; otherwise no score is touched): tier 0 roster, declared
  values, restore ledger and the ID regexes (checksummed: Aadhaar Verhoeff incl. the cue-less backstop, card Luhn;
  strict-format: PAN, IFSC, UPI, email, phone, secrets) > tier 1 models (D4:ml, D4:ner) > tier 2 heuristics (person
  cues, name growth, context-cued numbers and amounts, identifier-shape backstop). Implemented as disjoint score
  bands, so overlap resolution and propagation are unchanged (`pgw.pipeline.precedence_tier`).
- `legacy_heuristics: false` (profile; needs `ml_detector`) switches off the name and amount heuristics (person cues,
  lower-case cues, table columns, name growth, `D2:person_*`, `D2:compensation*`) so the team can measure
  "ML + ID regex" against today's stack. The heuristics are not deleted.
- Without an ML model configured, behaviour is identical (`tests/test_ml.py::test_no_ml_configured_means_unchanged_
  behaviour`, and the full pre-existing suite).

## 5. Content gate

Contract field (sealed, signed with the contract):

```yaml
content_gate: {labels: [health, disciplinary], threshold: 0.5, action: PRIVATE}   # or REFUSE
```

After the source signature is verified, the gateway classifies all request text. If any listed label scores at or
above the threshold: PRIVATE sends the request to the in-boundary model only (EXT_PSEUDO contracts; needs the private
endpoint), REFUSE answers 422 `special_category_content`. A gate that cannot answer: 503 `content_gate_unavailable`,
nothing sent. Reported as stage `content_gate` and check `content_gate` in `pgw_report` (only for contracts that
declare a gate). Startup stops when a contract declares a gate but no `content_gate_model` is configured, or names a
label the model does not have.

## 6. Runtime and CPU budget

Production: CPU only, 4-16 cores, no GPU. Each model runs in its own bounded pool of worker processes
(`pgw/ml/pool.py`, the `ner_pool` pattern): spawn (no fork), model built once per worker from the verified bytes,
BLAS threads pinned to 1, ONNX Runtime `intra_op_threads` / `inter_op_threads` from the profile, thread spinning off,
queue limit (`queue_max`, full = immediate 503), per-call timeout (`timeout_s`, capped at 0.8 x `protect_timeout_s`),
crashed or timed-out workers killed and replaced.

**Core budget:** `ner_workers x 1 + ml_detector.workers x intra_op_threads + content_gate_model.workers x
intra_op_threads + max_concurrent_protect (pipeline threads)` should not exceed the gateway's cores; the in-house LLM
runs on separate cores or a separate host (`deploy/LLAMA_SERVER.md`).

**Latency per request** (all inside `protect_timeout_s`, default 10 s): content gate (one pass, max over windows) +
ML detector (`views x windows` forward passes; usually one view, and `ceil(tokens / (max_length - stride))` windows)
+ regex and propagation (today's cost) + the provider call. The numbers depend on the model size, int8 vs fp32,
sequence length and the CPU, so they are **measured, not assumed**: `eval/ml/run.py` reports p50/p95 per request on
the machine it runs on, and the release gate holds a `max_latency_p95_ms` that the team sets. Measure on the
production CPU class before setting `timeout_s` and `workers`.

## 7. Artefact lifecycle

1. **Train** on Kaggle/Colab free GPUs from an open model under a commercial-friendly licence (record base model,
   licence, data, code commit). Training data: synthetic and public data only, never real personal data outside the
   boundary.
2. **Export** to ONNX (`optimum-cli export onnx` or `torch.onnx.export`), then **quantise int8**
   (`onnxruntime.quantization.quantize_dynamic`). Weights inline (< 2 GiB; external data files are refused).
3. **Package** the directory: `model.onnx`, `tokenizer.json`, `labels.json` (format in `pgw/ml/artifact.py`),
   `MODEL_CARD.md` (provenance, licence, training data, export command, dev results). Nothing else is needed.
4. **Pin**: `python -m pgw.ml.artifact <dir>` prints the manifest sha256 and the labels. Put `path`, `sha256`, the
   label map and per-type thresholds in `ml_detector` (or `content_gate_model`) in `registry/deployment.yaml`; bump
   `version` and `config_serial`; review; re-seal and sign. The model directory itself is copied to the hosts out of
   band (read-only); the signed pin is what makes it trusted.
5. **Evaluate**: dev set for selection and thresholds; then the release gate (external tester's sets + the
   human-labelled real sample), once, with thresholds set beforehand ([ML_EVALUATION.md](ML_EVALUATION.md)).
6. **Release** only on a PASS verdict; keep the previous artefact and profile for rollback (a rollback is a new
   signed profile with a higher `config_serial`).

## 8. Threat model of the ML parts

| Threat | Control | Residual |
|---|---|---|
| Model tampering or swap (file replaced on disk, wrong version deployed) | sha256 manifest pinned in the signed profile; checked in the parent at startup and again in every worker from the exact bytes it loads (no TOCTOU); symlinks, subdirectories, extra or missing files change the digest or are refused; startup stops | someone who can sign the profile can pin a bad model: same custody as every signed setting (OPERATIONS.md) |
| Backdoored or poisoned model (trained to ignore a trigger) | provenance in MODEL_CARD.md; training only from recorded sources; release gate on data the trainer never saw | not detectable by hashing; the guarantee does not depend on the model (section 2) |
| Adversarial inputs (homoglyphs, spacing, case, code-switching) | the model runs on the D0 normalised views like every detector; the ID regexes keep exact checksummed IDs; precedence keeps them on top | evasion that no view undoes; measured by the adversarial slices of the evaluation |
| Resource exhaustion (huge inputs, many windows) | gateway size limits; queue limit; per-call timeout; bounded workers; protect deadline | a burst gets 503s (availability, not disclosure) |
| Model crash, hang, garbage output | worker killed and replaced; wrong label, out-of-range span, wrong result count or label set -> `MlUnavailable` -> 503, nothing sent | none for disclosure (fail closed) |
| Gate evasion (sensitive topic phrased to score low) | threshold set on dev data; the gate is in addition to the signed-source rule and C4 routing | a missed topic goes the normal protected route, as before the gate |
| Supply chain of the runtime | onnxruntime/tokenizers/numpy pinned with hashes (requirements/ml.txt); the gateway never downloads models (tokenizers' hub client is unused) | as for every dependency (pip-audit in scripts/check.sh) |
