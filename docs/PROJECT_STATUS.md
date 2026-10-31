# Project status and hand-off (2026-10-30)

Written so the work can be resumed from GitHub alone: a cloud container (and every cache, model download and
gitignored file in it) can disappear at any time. Everything needed to continue is in this repository or is
reproducible from it, as listed below.

## Branches (none merged into main yet)

| Branch | Contains |
|---|---|
| `main` | mandatory contracts, signed config (merged earlier) |
| `ml/foundation` | ML slot (ONNX detector, content gate), llama-server route, training notebooks, tester regression fixes |
| `security/hrms-contracts` | + free-text structure guard (exports refused), RECORDS route (HRMS field contracts, local computation) |
| `feature/oss-adoption` | **latest; contains everything above** + plugin slots (python-stdnum, phonenumbers, detect-secrets), eval bench (nervaluate, Presidio as eval backend), small-group disclosure control (durable, per tenant/app/purpose/dataset), ADDRESS type, BIOES/Viterbi runtime, external-data guard, open-detector dev bench, private LLM bench |

Older branches (`security/*`, `feature/hr-workspace`, `feature/showcase-ui`, `backup/pre-mandatory-contracts`) are
ancestors of the above.

## Where the evidence is

- Open-detector dev bench: `docs/ML_EVALUATION.md` (newest section) and raw outputs in
  `eval/ml/bench_results/2026-10-30/`.
- In-house CPU LLM bench: `docs/PRIVATE_LLM_BENCH.md`.
- Security properties and their limits: `SECURITY_BOUNDARY.md` (§11 structure guard, §12 RECORDS + small groups,
  §13 plugins), `docs/HRMS_CONTRACTS.md`, `docs/PLUGINS.md`, `docs/ML_ARCHITECTURE.md`.
- Tester rerun runbook: `docs/TESTER_RERUN.md` (verified 2026-10-30: both bundle digests reproduced from the pinned revisions; production-mode smoke test passed for both configurations).

## Headline numbers (DEV, synthetic; not release numbers)

Tickets with any leak, tuned dev set → unfamiliar dev3build: current stack 8.6% → 37%; + openai/privacy-filter
1.5% → 22%; + GLiNER-PII (urchade/gliner_multi_pii-v1, fp32) 0.5% → 13%. PERSON recall on dev3build: 48% → 89% (PF)
→ 96% (GLiNER). Cost per 512 tokens at 4 threads: PF q4 0.44 s / 2.7 GB, GLiNER fp32 1.6 s / 3.4 GB per worker.
Privacy-filter `account_number` must be mapped to IGNORE (only 126/968 hits are bank accounts); `private_date` stays
IGNORE (74% of hits are not DOB). GLiNER int8 is unusable (recall 0.017). In-house Qwen2.5-1.5B Q4_K_M on 4 vCPU:
~4.5 tok/s, 30–40 s per summary, one request at a time, 2.7 GB.

The external tester's unseen sets (C, D, and a planned set E kept by the tester) are the release evidence, not these.

## Rebuilding a fresh environment

1. `python -m venv .venv` and install every lock with hashes: `requirements/{runtime,ner(+ner-model),ml,plugins,eval,dev}.txt`
   (see INSTALL.md, "Running the full suite").
   Bundle-building tools (repack, GLiNER export) use a separate venv from `requirements/export.txt` (hashed; torch +cpu
   from download.pytorch.org): see docs/TESTER_RERUN.md section 0.
2. Regenerate corpora (gitignored, hash-checked against their MANIFESTs):
   `python -m eval.synth.generate --out eval/data --seed 1337`, `python -m eval.synth2.generate --out eval/data2 --seed 20260930`,
   `gunzip -k eval/data3/dev3build.jsonl.gz`, `(cd eval/eutil && python build.py)`.
3. Model bundles (not in git; GBs): rebuild from the pinned Hugging Face revisions with
   `scripts/ml/build_external_bundle.py` (`repack_onnx.py` for privacy-filter) and compare the manifest digests in
   docs/ML_EVALUATION.md. Network hosts needed: huggingface.co, *.hf.co (CDN), download.pytorch.org,
   download-r2.pytorch.org; IndicNER is gated (accept terms, read-only HF_TOKEN, revoke afterwards).
4. Full suite: `python -m pytest -q -p no:cacheprovider` (≈3,417 passed, 4 skipped, 10 xfailed on 2026-10-30).

## Lost and why (lesson)

The re-audit's 127-ticket sealed held-out set was gitignored and lived only in a container that was reset; only its
aggregate results (SECURITY_BOUNDARY §8) survive. Held-out data is therefore kept by the external tester, never by the
team or the agent; only hashes go into the repo.

## Round 8 (2026-10-31)

- Tester's set E (273 cases + 40 benign, fingerprint in eval/EXTERNAL_SETS.md) without a model: 38% of identifier
  cases leak, 28/28 special-category cases cross (content gate untrained), 14/14 singling-out cases leak.
- Small groups: answers are audited as set algebra over all answers in the (tenant, dataset) scope (pgw/product/
  setaudit.py; brute-force verified): no group of 1..k-1 records is determinable by any linear combination of printed
  counts; suppressed cells keep a verified alternative. Identity keys normalised (case, DOMAIN\, @UPN). Rate limits
  per app (the gateway has no end-user identity), no reset (compaction keeps record sets), DB 0600 with HMACed
  predicates and a high-water mark in PGW_STATE_DIR.
- ADDRESS: production requires an ML ADDRESS label or a sealed `address_coverage: pin_only` (shipped profile v5 says
  pin_only); /healthz and pgw_report state the coverage.
- Tester runbook: requirements/export.txt (hashed), fail-fast blocks.

## Round 9 (2026-10-31)

- Listings: every count a listing lets the reader count (listed rows, rows not listed, each attribute class incl.
  "attributes withheld") is a history cell audited by setaudit R1/R2; the tester's listing-then-Sales attack is refused.
- Audit budget is wall-clock (sealed `audit_max_ms`, default 1500 ms) plus the step cap; 422 `audit_budget` is a
  separate, honest refusal. Our own workload (1,500 users, random roster questions, 4 parallel runs): p95 ~1.1 s,
  max 1.56 s; budget refusals ~0.7%; BUT 23–40% of questions are refused because they would reveal a small group
  given earlier answers (fine-grained filters over a growing history). Usability cost to be judged with HR users.
- Identity keys: strict sealed charset after normalisation; any missing/invalid key refuses the dataset.
- Rate limits per (app, end user), per app and per scope, shared across processes in SQLite; refused queries metered;
  end user asserted by the app via HMAC-bound headers (pgw/enduser.py); 429 with Retry-After.
- History mark MACed with a key-store key plus a monotonic copy in the rollback state; recover needs an operator and a
  stopped gateway; all store waits bounded (3 s, 503).
- ADDRESS pin_only: tab-row regression fixed (root cause: block overlapping an already protected name was dropped);
  `| / ; tab`, own-line PIN, anchored lower-case covered; no-PIN/no-house-marker addresses need ML.
- Flaky test: request-id hex collided with short canaries; matcher now removes only gateway random tokens.

## Open items (in priority order)

1. Tester reruns C, D and E with (A) stack + privacy-filter q4 and (B) stack + GLiNER fp32 (needs HF/PyTorch hosts
   allowed in the tester's environment).
2. Train the content gate (special-category text) on free Kaggle GPU: notebooks/kaggle 01 + 03, docs/ML_TRAINING.md.
3. 300–500 real tickets labelled in-house by HR reviewers (consent/DPO sign-off) — the only evidence that decides
   which detector to trust.
4. If C/D/E still show name leaks after GLiNER: fine-tune GLiNER on Indian HR text on Kaggle.
5. Decisions pending: card issuer-range fix (JCB/UnionPay/Diners/Discover/Maestro typed IDENTIFIER, still masked);
   city before PIN stays unmasked; rounding of published counts (built, off); per-app rate limits are shared by all
   users of an app (end-user identity is not propagated to the gateway).
6. Not protected: background knowledge; l-diversity; inference from suppression choices; tenants/datasets
   deliberately separated; free-text inline name–pay pairs without ML.
