# Corpus v3 — freeze record (2026-10-01)

**Status: FROZEN.** Nothing under `eval/data3/MANIFEST.json` or `eval/v3/sealed_v3.tar.enc` may change.
Any change to a generator, the corpus, or the family assignment means corpus v4.

| Item | Value |
|---|---|
| Registration commit (SPEC, CONTAMINATION, SEPARATION, gates) | `f2799b5e22a440ecb1aaf3ad93251b8afc1aef99` |
| Catalogue SHA-256 (sealed) | `c8697701f5ffd9fb7596f607de496dca275555f6f370d8ecc5f7200ae772e7ea` |
| Assignment seed = sha256(commit + catalogue sha)[:16] | `ca4e4437cc265ceb` |
| dev3build.jsonl (900 items, 12 families) | `c3a98303d9fba8ed27812e9777a819061e713b5229318d6936e0fd0be3854aaa` |
| dev3val.jsonl (450 items, 6 families) | `91b6000d8d645cd0c60375256048977211cbd2cd0d035a9a7b4dc31e41ed43ef` |
| roster.jsonl (1,200 employees) | `a0370eab745ddae8c39ef0c4adb9792c58314d5ecf300d2022d9a8d34190fffc` |
| **test3.jsonl (1,350 items, 18 families), sealed** | `9c73fee114d08d65300267a0e2d0af2388deaf3c73e4ff54b679afc286f32391` |
| assignment.json, sealed | `a6e05d19f85a9d4e98b2b794cc98e734907d7e8147b29c5c019da9fd5933f083` |
| Sealed archive plaintext tar | `7cb25a615464bd6acd893c16f131fc0a894ec2117bf5c6f51691e4057841569b` |
| Sealed archive ciphertext (`sealed_v3.tar.enc`, AES-256-GCM) | `88a8a3e8ac7e4ad3aaffcdee18d942792a7fea1096933f0dacc2f55f3f1b3723` |

The round trip was verified: decrypting gives test3.jsonl and assignment.json matching the hashes above.
The key is held outside the repository (`/home/user/v3_key.hex`) and was given to the owner in chat.

## What is published and what is sealed
- **Published:**
  - dev3build, dev3val and the roster (gzipped in `eval/data3/`; plain `.jsonl` files are gitignored);
  - `DEV_FAMILIES.json`;
  - the known adversarial kinds (`eval/v3/adversarial/known.py`).
- **Sealed:**
  - the catalogue, the shared `common/` generators (value and representation distributions), all 36 family generators;
  - the held-out adversarial kinds (15);
  - test3, the assignment and the audit details.

## Authoring
| Role | Model | Tool calls | Forbidden-path references | Self-test |
|---|---|---:|---:|---|
| Catalogue + common | Opus | 35 | 0 | 4,183 / 0 failed |
| Family author S (12) | Sonnet | 81 | 0 | 335,942 / 0 |
| Family author C (12) | Sonnet | 70 | 0 | 282,253 / 0 |
| Family author I (12) | Sonnet | 68 | 0 to repo or corpora; it used the session scratchpad as a work area, which was moved into the sealed directory unread | 221,114 / 0 |
| Adversarial (10 known, 15 held-out) | Sonnet | 16 | 0 (and never opened the catalogue or families) | 113,134 / 0 |

The three family authors were interrupted once by an API usage limit and resumed with their context intact.

## Audit aggregates (`eval/v3/audit_corpus.py`)
- Span structure:
  - out-of-bounds, overlaps, unknown types: 0;
  - unlabelled re-occurrences of planted values: 0;
  - invalid checksums: 0, apart from 9 spoken IFSCs (exempt by design).
- Families: 12 / 6 / 18; every family has 60 positive + 15 negative items, ≥ 6 skeletons, ≥ 3 taxonomy types and PERSON.
- Overlap with v1/v2:
  - word 8-grams: **0.08 %** of v3 8-grams also occur in v1/v2;
  - identifier values: 0;
  - non-identifier chance collisions: test3 has COMPENSATION 110 and DOB 30.
- Field keys: 464 distinct v3 keys, of which **83.4 % are unseen in v1/v2** (77 seen). The vocabulary extractor is over-inclusive, so "seen" is a conservative upper bound.
- Generator imports: only the standard library plus `common`.
- Residual defects: see SPEC amendment 2026-10-01c.
