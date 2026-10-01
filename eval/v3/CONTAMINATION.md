# Corpus v3 — contamination rules (pre-registered 2026-10-01)

Threats addressed:
- **T-A:** the test is shaped by the detector or by its known failures.
- **T-B:** the implementation is shaped by the test.
- **T-C:** the same author writes both, so both share blind spots.

R2 relied on instructions alone for T-B: test2 templates sat in the repo next to the code. v3 adds mechanical controls.

## Rules

| # | Rule | Control | Verified by |
|---|---|---|---|
| C1 | Corpus authors (catalogue, family and adversarial agents) never read this repository, its corpora, `pgw/`, `eval/synth*`, `eval/results/`, `research/`, `ROOT_CAUSE_ANALYSIS_R2.md` or the security reports | Work only in `/home/user/v3-sealed/`, where they receive copies of SPEC.md and EXCLUSIONS.txt | Audit of agent transcripts for forbidden path reads (`eval/v3/audit_transcripts.py`), reported in FREEZE.md |
| C2 | Generators import only the standard library and their own modules | AST import check | `audit_corpus.py` |
| C3 | The catalogue, test-family generators, test-side adversarial code and test3 data are **sealed**. They are kept outside the repository working tree and committed only as an encrypted archive with plaintext SHA-256 hashes | AES-256-GCM archive `sealed_v3.tar.enc`. The key is given to the owner in chat and kept outside the repo | `FREEZE.md` hashes; decrypt-and-compare after the one-shot run |
| C4 | Agents report to the orchestrator **counts and paths only**: no family names, descriptions, templates or examples | Prompt rule | Orchestrator transcript (no sealed content quoted) |
| C5 | The PD-13 implementer, and every reviewer of PD-13 code, sees **dev3 only**. They never open the sealed archive, `/home/user/v3-sealed/`, or the transcripts of authoring agents | Prompt rule + path audit of the implementer transcript | Reported in the test3 report |
| C6 | Orchestrator blindness: the orchestrator does not read the catalogue, test-family code or test3 items. Validation of test3 is automated and reports aggregates only | Validators print counts | This file's audit section, after freeze |
| C7 | L3 dictionary entries record provenance: `standard:<citation>` or `dev3:<item id>`. Entries added after the dev3 read are allowed but tagged | Data-file schema | test3 report: L3 decisions by provenance |
| C8 | Overlap audit against v1/v2: identical full names, identical identifier values, field-key vocabulary and template 8-gram overlap. Values and full names: 0 allowed. Keys: measured and reported, with results stratified by key seen/unseen in v1/v2 | `audit_corpus.py` | FREEZE.md |
| C9 | Once test3 is read for any arm, no code, threshold, dictionary or policy may change before the remaining arms are run. All three arms run from one commit | Lock files `LOCK_v3_<arm>_test3.json` record the commit | Report |
| C10 | No optimisation against test3 after it is read. Any later change is a new release that needs corpus v4 | — | — |

## Residual risks (disclosed, not solved)

- **The orchestrator (Claude) is contaminated:** it wrote the R2 root-cause analysis and PD-13 and designed this spec. C3–C6 keep it blind to the test families, but the sampling frame and the metrics are its choices.
- **The corpus authors are the same model family as the implementer.** Shared priors about "typical" HR formats may make v3 easier for any LLM-built detector than real traffic would be. Grounding families in cited public standards reduces but does not remove this. Real-data recall (E-DET-R) remains unmeasured.
- Instruction-level controls (C1, C4, C5) are audited after the fact, not enforced by the operating system.

## Audit results

### Deviations recorded during authoring

- **D-1 (2026-10-01 09:19 UTC, C6 partially breached).** After the authoring agents were interrupted by an API usage limit, the orchestrator ran `ls -la families/` to check progress, which printed the **file names** of the 32 family modules written so far into its context. It did not see their contents, the catalogue text, or the dev/test assignment, which had not been computed yet. Consequence: the orchestrator knows the family names (which hint at representations). Mitigations:
  - no family name or representation is passed to the PD-13 implementer;
  - the orchestrator does not edit L3 dictionaries or L1 parsers itself;
  - review comments to the implementer are limited to correctness on dev3 items, and the review log is committed.
  - From this point, progress checks use counts only.
- **D-2 (observation).** PD13_DESIGN.md (`f56a3f9`) names Indian statutory forms as an allowed L3 dictionary source. It was committed before any family existed. The catalogue author, working blind, independently chose some statutory-file families. If any of those land in test3, B's L3 may benefit from a standards source that the designer and the corpus author both chose from shared priors. The test3 report must show L3 decisions by dictionary provenance and by key-novelty stratum, so this effect is visible.

- **D-1 widened at freeze.** The assembler publishes the dev family ids by design. Combined with D-1, the orchestrator can infer **the names of 14 of the 18 test families** by elimination. It has still not read any test family's catalogue entry, code or items. The mitigations in D-1 apply unchanged, and the implementer's prompt contains no family names other than the published dev ids.

- **D-3 (2026-10-01, aborted first test3 attempt; no result observed).** The first one-shot attempt at commit `44d973e` wrote locks for arms A and B, then failed before producing any metric.
  - Arm A crashed after its clean pass in a harness bug: `summarise()` called `max()` on an empty latency list for an adversarial kind that applies to no test3 item. The result file is written only at the end, so none was written.
  - Arm B's process was killed by a session interruption before it printed anything.
  - Arm C never started.
  - The logs contain only a traceback and warnings. **No test3 metric was produced or seen by anyone.**
  - Fix, harness only (`eval/run_v3.py`, `eval/v3_gates.py`; `pgw/` untouched): empty kinds report `docs: 0` and G4 lists them as not applicable; the clean result is persisted before the adversarial passes.
  - The aborted locks are kept as `LOCK_v3_{A,B}_all_test3.aborted-44d973e.json`. All three arms are rerun once from a single new commit.

### Audit results at freeze
See `eval/v3/FREEZE.md`:
- 0 forbidden-path reads by any author (one author's scratchpad was moved unread into the sealed directory);
- stdlib-only generators;
- 0.08 % 8-gram overlap with v1/v2;
- 83 % of field keys unseen in v1/v2;
- 0 identifier collisions with v1/v2.
