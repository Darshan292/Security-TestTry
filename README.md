# ESU Privacy Gateway — design package

A policy-driven privacy gateway between ESU applications (HRIS/ERP) and LLM
providers. **Status: Phase 1 vertical slice built and evaluated. It is not secure enough for real HR data.**
It fails its own pre-registered leak gate on held-out data: 2.56 % of sensitive characters and
14.6 % of documents leak. Read [SECURITY_EVALUATION.md](SECURITY_EVALUATION.md) first.

> Extracted from the `Evidence-first-universal-knowledge-graph-compiler` repository with its history; this is now the project's home.

## Start here

| If you want… | Read |
|---|---|
| The measured truth about the slice | [SECURITY_EVALUATION.md](SECURITY_EVALUATION.md) |
| Why each decision was made, and how well it is supported | [research/ARCHITECTURE_DECISIONS.md](research/ARCHITECTURE_DECISIONS.md) |
| Evidence and its quality | [research/EVIDENCE_MATRIX.md](research/EVIDENCE_MATRIX.md), [research/SOURCE_REGISTER.md](research/SOURCE_REGISTER.md), [research/PROVENANCE.md](research/PROVENANCE.md) |
| Techniques, competitors, unknowns | [research/ALGORITHM_ANALYSIS.md](research/ALGORITHM_ANALYSIS.md), [research/COMPETITIVE_LANDSCAPE.md](research/COMPETITIVE_LANDSCAPE.md), [research/OPEN_QUESTIONS.md](research/OPEN_QUESTIONS.md) |
| To navigate relationships (source → claim → decision → code → test → metric) | [graph/README.md](graph/README.md) |
| To run it | `python -m pytest tests -q` · `python -m eval.synth.generate --out eval/data` · `python -m eval.run_det` · `python -m pgw.gateway` |

## Design documents, in order

1. [DESIGN.md](DESIGN.md) — the system: requirements, boundary, lifecycle,
   components, entity taxonomy, keys, deployment, limitations.
2. [ADR/](ADR/) — the ten decisions and what was rejected:
   0001 boundary · 0002 aliases · 0003 ephemeral mapping · 0004 purpose ·
   0005 detection · 0006 policy · 0007 egress invariant · 0008 streaming ·
   0009 tools & RAG · 0010 restore ledger.
3. [THREAT_MODEL.md](THREAT_MODEL.md) — adversaries, threats, controls, residual risk.
4. [EVALUATION_PLAN.md](EVALUATION_PLAN.md) — pre-registered gates.
5. [ROADMAP.md](ROADMAP.md) — phases and exit gates.
6. [RISK_REGISTER.md](RISK_REGISTER.md) — technical and delivery risks.
7. [research/](research/) — verification of the source report:
   `INDUSTRY_PATTERNS.md` (how Salesforce, AWS, Google, Cloudflare, SAP, Uber do it),
   `OSS_COMPONENTS.md` (what is alive, archived, licensed how),
   `ENGINEERING_FACTS.md` (Indian ID specs, streaming, crypto, DPDP).

## Where this design departs from the source report

| Report says | Design decides | Why |
|---|---|---|
| Tokenise with AES-SIV/FPE | Model sees short HMAC-derived scoped aliases; crypto only at rest | Ciphertext in prompts costs tokens and quality and is globally linkable (ADR-0002) |
| Encrypted vault at the core | No PII at rest in Phase 1; vault only where proven necessary | Chat APIs re-send history; the vault is the biggest target (ADR-0003) |
| Policy by `context: hr_case` | Purpose declared by a registered app | Otherwise the prompt picks its own policy (ADR-0004) |
| OPA/Rego | In-process signed decision tables | Per-entity hot path; OPA deferred to tool authorisation (ADR-0006) |
| Response DLP | Gate on aliased text *before* restore; bounded incremental streaming | Everyone else either buffers the whole stream or leaks (ADR-0008) |
| (absent) | Egress invariant on the exact outbound bytes | Bugs, not models, cause the embarrassing leaks (ADR-0007) |
| (absent) | Detection cache; NER only on free text | NER on re-sent history is O(n²) and seconds per turn on CPU (ADR-0005) |
| (absent) | Keyed-hash restore ledger per conversation | Restored values re-sent in truncated history would otherwise depend on NER recall (ADR-0010) |
| Recommends `guardrails_pii`, cites `microsoft/presidio` | Archived; Presidio moved to `data-privacy-stack` | research/OSS_COMPONENTS.md |
| Phases 1–6 incl. RAG, agents, console | Phase 1 is deterministic core only, gated | R-01 scope creep is the top risk |
