# Installation (reproducible)

Python 3.11. All lock files carry exact versions **and sha256 hashes**; install with `--require-hashes`.

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements/runtime.txt     # gateway + library

# optional NER extra (spaCy + en_core_web_lg)
pip install --require-hashes -r requirements/ner.txt -r requirements/ner-model.txt

# development / tests / eval (on top of runtime)
pip install --require-hashes -r requirements/dev.txt
```

`ner.txt` and `dev.txt` are self-contained supersets of `runtime.txt` (they are compiled with it as a constraint), so installing them alone is enough.

## The spaCy model is verified by hand

`en_core_web_lg` is not on PyPI; it is a GitHub release wheel, which `pip-compile` cannot hash-lock. It is pinned manually in `requirements/ner-model.txt` as exact URL plus sha256 (`en_core_web_lg-3.8.0`), and pip enforces that hash under `--require-hashes`. To bump it: download the new wheel from https://github.com/explosion/spacy-models/releases, run `sha256sum` on it, and edit the URL and hash in `ner-model.txt`. The model requires `spacy>=3.8,<3.9`, which `ner.in` enforces.

## Re-locking

Edit the `.in` files, then (from `requirements/`):

```bash
pip install pip-tools
pip-compile --generate-hashes --allow-unsafe --strip-extras -o runtime.txt runtime.in
pip-compile --generate-hashes --allow-unsafe --strip-extras -o ner.txt ner.in
pip-compile --generate-hashes --allow-unsafe --strip-extras -o dev.txt dev.in
```

Run `ner` and `dev` after `runtime`: both use `-c runtime.txt` so shared packages keep identical pins. To upgrade one package, add `--upgrade-package NAME`. Commit the `.txt` files, then run `python -m pytest tests -q`; `tests/test_requirements_lock.py` fails if a third-party import under `pgw/` is not covered by a hashed pin in `runtime.txt`.

Notes: `anthropic` is in the runtime set because `PGW_UPSTREAM=anthropic` imports it lazily. `uvicorn` is needed by `python -m pgw.gateway`.
