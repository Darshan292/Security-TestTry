# Dev bench of open detectors, 2026-10-30 (raw outputs)

Raw outputs behind the "dev bench of open detectors" section of docs/ML_EVALUATION.md: per-corpus matrices
(`*_matrix.json`), label analyses, latency samples and the scripts used (`run_all.sh`, `run2.sh`). DEV data only
(synthetic corpora eval/data dev, eval/data2 dev2, eval/data3 dev3build, ml/train synthetic genres); no held-out,
test or tester data. Model bundles are not stored here (GBs); rebuild them from the pinned revisions with
scripts/ml/build_external_bundle.py and check the manifest digests listed in docs/ML_EVALUATION.md.
