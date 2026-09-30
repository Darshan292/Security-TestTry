# Provenance scheme for the research-to-decision files

Every statement in `SOURCE_REGISTER.md`, `EVIDENCE_MATRIX.md`, `ALGORITHM_ANALYSIS.md`,
`COMPETITIVE_LANDSCAPE.md`, `OPEN_QUESTIONS.md` and `ARCHITECTURE_DECISIONS.md` carries a stable ID
and an epistemic class. The IDs are what the graph (`graph/`) links; do not renumber.

## ID prefixes

| Prefix | Node | Defined in |
|---|---|---|
| `S-NN` | Source (document, repo, page, paper, dataset) | SOURCE_REGISTER.md |
| `C-NN` | Claim made *by a source* about the world | EVIDENCE_MATRIX.md |
| `M-NN` | Our own measurement (a number from frozen results) | EVIDENCE_MATRIX.md |
| `A-NN` | Algorithm / technique | ALGORITHM_ANALYSIS.md |
| `F-n`, `N-n` | Functional / non-functional requirement | DESIGN.md §2 (existing IDs) |
| `T-n` | Threat | THREAT_MODEL.md (existing T1–T21) |
| `ADR-NNNN` | Architecture decision | ADR/ (existing) |
| `Q-NN` | Open question | OPEN_QUESTIONS.md |
| `W-n` | Measured weakness | SECURITY_EVALUATION.md §5 |
| `K-name` | Component (code module) | `pgw/…` path |
| `X-name` | Test or evaluation gate | `tests/…::name` or EVALUATION_PLAN gate (E-DET …) |

## Epistemic classes (every claim/row gets exactly one)

| Class | Meaning | May support a decision alone? |
|---|---|---|
| **FACT-P** | Verified by us against a primary source (vendor docs, spec, source code, standard, PyPI metadata) | Yes |
| **FACT-M** | Measured by us, reproducible from this repo (frozen results, tests) | Yes |
| **CLAIM-S** | Stated by a secondary source (search summary, blog, third-party write-up); primary not reached | Only with an explicit risk note |
| **CLAIM-V** | Vendor or project self-description (marketing, README claims, star counts) | No |
| **KNOWN** | Background knowledge not re-verified in this project (`[K]` in ENGINEERING_FACTS) | No; must be listed in OPEN_QUESTIONS if a decision depends on it |
| **INFERENCE** | Our reasoning from other nodes; must cite what it infers from | Only together with its premises |
| **DECISION** | A choice we made; must cite the evidence and the alternatives rejected | n/a |
| **UNVERIFIED** | Looked for and not found, or contradictory | No |

Rules:
1. A DECISION must trace to at least one FACT-P / FACT-M node, or declare itself
   provisional and link a `Q-NN`.
2. A number is FACT-M only if a frozen result file or a test in this repo reproduces it.
3. Secondary evidence is never upgraded by repetition. Three blog posts quoting one vendor page are one CLAIM-S.
4. Absence findings ("no tool-call field in the proto") are FACT-P about absence, not about behaviour.
