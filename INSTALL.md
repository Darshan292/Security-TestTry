# Installation (reproducible)

Python 3.11. All lock files carry exact versions **and sha256 hashes**; install with `--require-hashes`.

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements/runtime.txt     # gateway + library

# optional NER extra (spaCy + en_core_web_lg)
pip install --require-hashes -r requirements/ner.txt -r requirements/ner-model.txt

# optional ML extra (pgw/ml: ONNX Runtime CPU detectors, content gate; docs/ML_ARCHITECTURE.md)
pip install --require-hashes -r requirements/ml.txt

# optional plugins extra (pgw/plugins: python-stdnum, phonenumbers, detect-secrets; docs/PLUGINS.md)
pip install --require-hashes -r requirements/plugins.txt

# development / tests / eval (on top of runtime, ml and plugins)
pip install --require-hashes -r requirements/dev.txt
```

`ner.txt` and `dev.txt` are **not** supersets of `runtime.txt`: they are compiled with it only as a constraint (`-c`), so shared packages get the same pins but runtime packages are not included. Always install `runtime.txt` first, then the extras you need.

## The spaCy model is verified by hand

`en_core_web_lg` is not on PyPI; it is a GitHub release wheel, which `pip-compile` cannot hash-lock. It is pinned manually in `requirements/ner-model.txt` as exact URL plus sha256 (`en_core_web_lg-3.8.0`), and pip enforces that hash under `--require-hashes`. To bump it: download the new wheel from https://github.com/explosion/spacy-models/releases, run `sha256sum` on it, and edit the URL and hash in `ner-model.txt`. The model requires `spacy>=3.8,<3.9`, which `ner.in` enforces.

## Re-locking

Edit the `.in` files, then (from `requirements/`):

```bash
pip install pip-tools
pip-compile --generate-hashes --allow-unsafe --strip-extras -o runtime.txt runtime.in
pip-compile --generate-hashes --allow-unsafe --strip-extras -o ner.txt ner.in
pip-compile --generate-hashes --allow-unsafe --strip-extras -o ml.txt ml.in
pip-compile --generate-hashes --allow-unsafe --strip-extras -o plugins.txt plugins.in
pip-compile --generate-hashes --allow-unsafe --strip-extras -o dev.txt dev.in
```

Run `ner`, `ml`, `plugins` and `dev` after `runtime` (and `dev` after `ml`): they use `-c runtime.txt` (`dev` also `-c ml.txt`) so shared packages keep identical pins. `ml.txt` holds onnxruntime, tokenizers and numpy (and tokenizers' own dependency huggingface-hub, which the gateway never calls: models are loaded from the pinned local directory only); `dev.txt` adds `onnx` for the test-time tiny models. `plugins.txt` (compiled with `-c runtime.txt`) holds the plugin adapters' libraries; tests/test_plugins.py skips without them. To upgrade one package, add `--upgrade-package NAME`. Commit the `.txt` files, then run `python -m pytest tests -q`; `tests/test_requirements_lock.py` fails if a third-party import under `pgw/` is not covered by a hashed pin in `runtime.txt`.

Notes: `anthropic` is in the runtime set because `PGW_UPSTREAM=anthropic` imports it lazily. `uvicorn` is needed by `python -m pgw.gateway`.

## Running the full suite

Reference run (this repository, `feature/oss-adoption` + the ONNX walker tests, Python 3.11, all extras installed: `runtime.txt`, `ml.txt`, `plugins.txt`, `eval.txt`, `dev.txt`, `ner.txt` + `ner-model.txt`, plus the gitignored corpora below): `python -m pytest -q -p no:cacheprovider -rs` gives 3,387 collected, 3,372 passed, 4 skipped, 10 xfailed (before the ONNX walker tests were added: 3,356 collected). The only skip in the reference run is `tests/ml_train/test_notebooks.py` (4 tests; needs `nbformat`, which is in no requirements file). `-rs` prints the reason of every skip: an auditor whose count differs should compare that list first.

Optional packages. A missing package removes tests from collection (module-level `importorskip`/skip at import time, or tests collected only when the import works) or turns them into skips. Measured by blocking one package at a time on the reference environment (collected count drop from 3,387; the onnx/onnxruntime rows include the 31 walker tests, so 104 on the pre-change suite):

| Package | Needed by | Collected tests lost without it |
|---|---|---|
| `onnx` (dev.in) | tests/test_ml.py, test_ml_bioes.py, test_ml_gateway.py, test_ml_eval.py, test_ml_onnx_walker.py, test_bench_detectors.py, ml_train/test_bundle_roundtrip.py | 135 |
| `onnxruntime` (ml.in) | same modules as `onnx` | 135 |
| `tokenizers` (ml.in) | tests/test_ml*.py, test_bench_detectors.py, ml_train/test_bundle_roundtrip.py | 104 |
| `spacy` (ner.in) | tests/test_declared_subjects.py, test_ner.py, test_ner_pool.py, test_gateway_hardening.py (whole modules; also needs a model, see next row) | 116 |
| `en_core_web_lg` (ner-model.txt) | skipif in test_declared_subjects.py, test_ner.py, test_ner_pool.py, test_gateway_hardening.py, test_presidio_backend.py | included above when spacy is absent; with spacy but no model the model-dependent tests skip |
| `phonenumbers` (plugins.in) | tests/test_plugins.py and plugin parametrisations | 63 |
| `python-stdnum` (plugins.in) | tests/test_plugins.py, ml_train/test_faker_source.py (Aadhaar/PAN/GSTIN checks) | 55 |
| `detect-secrets` (plugins.in) | tests/test_plugins.py | 55 |
| `nervaluate` (eval.in) | tests/test_nervaluate_crosscheck.py | 8 |
| `presidio-analyzer` (eval.in) | tests/test_presidio_backend.py (also needs spacy and `en_core_web_lg`) | 8 |
| `faker` (eval.in) | tests/ml_train/test_faker_source.py (8 tests; a module-level skip when absent) | 8 |
| `nbformat` (not in any lock file) | tests/ml_train/test_notebooks.py: 4 tests skip, the other 11 still run | 0 (4 skips) |

Rows overlap (one test module can need several packages), so the drops are not additive. Gitignored corpora: `eval/data/dev.jsonl` (tests/test_ml.py adds 150 texts to a corpus if present; it does not change the count), `eval/data3/dev3build.jsonl` (tests/test_pd13_dictionary.py reads it; it is regenerated from `eval/data3/dev3build.jsonl.gz`); a missing corpus fails or alters those tests rather than skipping them. A few timing tests (for example `tests/test_transparency_report.py::test_size_is_bounded_for_a_huge_number_of_spans_and_building_is_fast`, 500 ms budget) can fail on a loaded machine.

A run that collects 3,295 tests with 7 skips (an external tester's) is consistent with some of the rows above being absent, for example `faker`, `nervaluate` and `presidio-analyzer` missing (three module-level skips, 24 fewer tests: 3,332 and 7 skips), but no single combination of the measured rows reproduces 3,295 exactly; an auditor should send `pip freeze` and the `-rs` list so the remainder can be attributed.
