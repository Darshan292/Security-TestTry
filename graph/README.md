# Provenance graph

A navigation and relationship layer over the design, research, code and tests.
It is **generated**; the markdown tables are the source of truth.

```bash
python graph/build_provenance_graph.py                       # research tables -> provenance.graph.json + GRAPH_REPORT.md
# code layer: run Graphify on a scratch copy so its cache stays out of the repo
mkdir -p /tmp/g && cp -r pgw tests eval /tmp/g/ && (cd /tmp/g && graphify update . --no-cluster)
python graph/link_code_layer.py /tmp/g/graphify-out/graph.json   # -> graph/merged.graph.json (lossless)

graphify explain "ADR-0008" --graph graph/merged.graph.json   # every claim, measurement, threat, test on a decision
graphify path "S-17" "stream.py" --graph graph/merged.graph.json            # source -> claim -> ADR -> component -> code
graphify path "M-14" "test_stream_any_chunking()" --graph graph/merged.graph.json
```

`graphify path` matches arguments against node **labels**: provenance nodes start with their
ID (`S-17 …`, `ADR-0008 …`); code nodes are labelled by file or symbol (`stream.py`, `canon()`).

## Files

| File | What | Lossless? |
|---|---|---|
| `provenance.graph.json` | Provenance layer (315 nodes, 353 edges at `b3d6cef`) | Yes: the analytical source |
| `merged.graph.json` | Provenance + Graphify code layer (1,118 nodes, 2,607 edges; 25 cross-layer links) | Yes: built by `link_code_layer.py`, **not** `graphify merge-graphs` |
| `GRAPH_REPORT.md` | Validation: dangling IDs, decision-support audit, weak evidence, uncovered weaknesses | — |
| `view/provenance.html` | Interactive picture of the provenance layer (Graphify `cluster-only`, 102 communities) | **No**: Graphify merges parallel edges (353 → 323). Use it to look around, never to count |

Verified limitations of Graphify for this use (2026-09-30, graphifyy 0.9.72):
`merge-graphs` prefixes ids and collapsed 144 edges; `cluster-only` on the merged graph refused
to run (fuzzy dedup would drop 11 nodes); both are why the merge is done by our own script.

## Why two layers, and why not let Graphify infer the provenance

Graphify maps **code** deterministically (tree-sitter AST, `_origin: ast`, confidence `EXTRACTED`).
For **documents** it runs an LLM semantic pass and tags guessed edges `INFERRED`. This project's
requirement is to keep *facts, external claims, inferences and our decisions* strictly apart,
so the provenance layer must not contain guessed edges. Hence:

| Layer | Built by | Edge confidence | What it answers |
|---|---|---|---|
| Provenance | `build_provenance_graph.py`, reading only explicit IDs in table rows | `EXTRACTED`, with file and line on every edge | Why did we decide X? What evidence, of what quality? What does a measurement refute? |
| Code | `graphify update` (tree-sitter) | `EXTRACTED` (`_origin: ast`) | Which functions implement a component; what imports what |
| Link between them | `COMPONENT_FILES` map in the builder (`K-alias` → `pgw/alias.py`) | `EXTRACTED` | From a decision down to the code, and back |

Graphify's document/LLM pass is deliberately **not** run over `research/`. If it ever is, its
`INFERRED` edges must stay in a separate graph and never be merged into the provenance layer.

## Model

The suggested linear chain (Source → Claim → Evidence → Algorithm → Requirement → Threat → Decision
→ Component → Test → Metric) was changed in one place. Evidence does not only flow *into*
decisions: our own measurements come *out of* tests on components, and flow **back** to confirm
or refute both external claims and decisions. The graph therefore contains a validation loop:

```
Source ─asserts→ Claim ─bears_on/supports→ Decision ─mitigates→ Threat
                   ▲                         │  ─satisfies→ Requirement
      corroborates/contradicts               │  ─implemented_by→ Component ─(code layer)→ functions
                   │                         │  ─tested_by→ Test
              Measurement ◄──measured_by─────┘
                   │  (produced by Tests over the frozen corpus)
                   ├─challenges→ Decision ─requires_amendment→ (ADR amendment)
                   └─evidence_for→ Algorithm ─implemented_by→ Component
Weakness ◄─closes─ Proposed decision ◄─motivates─ Measurement
Question ─blocks→ Decision / Weakness     Threat ─controlled_by→ Decision
```

| Node kind | ID | Epistemic class carried on the node |
|---|---|---|
| source | `S-NN` | From the access grade: P → FACT-P, S → CLAIM-S, N → UNVERIFIED, I → FACT-M |
| claim | `C-NN` | As graded in EVIDENCE_MATRIX Part B |
| measurement | `M-NN` | FACT-M |
| algorithm | `A-NN` | DECISION (our verdict) |
| decision | `ADR-NNNN` | DECISION, plus a `status` (VALIDATED … CONTRADICTED, PROVISIONAL) |
| proposed_decision | `PD-N` | PROPOSED (not accepted) |
| question | `Q-NN` | OPEN / CLOSED |
| weakness | `W-N` | FACT-M |
| threat / requirement | `T-N` / `F-N`, `N-N` | from THREAT_MODEL / DESIGN |
| component / test | `K-…` / `X-test_…` | resolved to a code file and line |

## Validation (in `GRAPH_REPORT.md`, regenerated on every build)

- Dangling IDs (referenced, never defined)
- Test references that don't resolve to a function in `tests/`
- **Decision support audit**: every ADR with no FACT-P/FACT-M support is listed as `**none**`
  and must carry "rationale PROVISIONAL" in ARCHITECTURE_DECISIONS.md
- Weak evidence (UNVERIFIED, CLAIM-V) pointed at decisions
- Measured weaknesses with no proposed decision
