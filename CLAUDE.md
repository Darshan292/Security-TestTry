# ESU Privacy Gateway — Claude Code notes

**Phase 1 vertical slice exists (`pgw/`) and has been evaluated. See SECURITY_EVALUATION.md.** Read [README.md](README.md) for the document order.

## Evaluation integrity (do not break)

- Tune only on `dev`/`tune`. `test` is read once per release via `python -m eval.run_det --split test --one-shot-test`, which writes `eval/results/TEST_LOCK.json`. R1 `test` and R2 `test2`/`ner_test2` are **spent** (see LOCK files): the next release needs a third fresh corpus.
- Never commit `eval/data/*.jsonl` (realistic fake secrets); the generator plus MANIFEST hashes reproduce them.

## Provenance

Research files use the IDs and epistemic classes in `research/PROVENANCE.md`. After editing any of them, run
`python graph/build_provenance_graph.py` and fix every dangling ID it reports in `graph/GRAPH_REPORT.md`.
Never merge LLM-`INFERRED` edges into the provenance graph.

## Decision hygiene

Decisions live in `ADR/NNNN-*.md`. Each must have:
- A header with `Status:` and `Date:` fields (or inherited from amendments)
- An "Alternatives rejected" section

When proposing a new decision:
1. Assign the next sequential number (check the highest existing ADR)
2. Draft ADR/NNNN-*.md with the structure above
3. Add a row to the table in [DESIGN.md § 10](DESIGN.md#10-decision-index)
4. Update [README.md](README.md) item 2 to list it

## Evaluation gates

Gates are pre-registered in [EVALUATION_PLAN.md](EVALUATION_PLAN.md). They may only change via a **dated amendment** inside that same file; do not edit gates in ROADMAP without recording why.

## Data policy

**Never put real personal data in this repository.** Use synthetic examples only. Verify before committing.

## Corpus v3 (Release 3)

- `eval/data3/*.jsonl.gz` hold dev3build/dev3val/roster (gunzip before use; plain `.jsonl` stays gitignored). test3 lives only in `eval/v3/sealed_v3.tar.enc`.
- **Never decrypt, list or read the sealed archive or `/home/user/v3-sealed/`** unless running the one-shot test3 evaluation of `EVALUATION_PLAN` amendment 2026-10-01. dev3val is read through `eval.run_v3` only (max 5 logged runs).
