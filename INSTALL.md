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
