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

(Filled in at freeze time.)
