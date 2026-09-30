# Open Questions

- **Date:** 2026-09-30 · **Scheme:** `research/PROVENANCE.md`
- A question is closed only by a FACT-P or FACT-M node, or by a named owner's decision.
  "We think" is not a closure.

Priority: **P0** blocks any real-data use · **P1** blocks the next phase gate · **P2** improves a decision.

| ID | Question | Why it matters | Blocks | Current best evidence (class) | How it gets closed | Owner | P |
|---|---|---|---|---|---|---|---|
| Q-01 | Do target models copy `[TYPE_3QX2MA]` aliases faithfully, versus `<PERSON_1>` or surrogates? | Restore fidelity and fabricated-alias rate decide ADR-0002's format | ADR-0002 closure; E-FMT gate | No published study (S-01 lit review; INFERENCE). Lenient restore exists but is untested on real models | Run E-FMT: n ≥ 500 per format per model | Engineering, with an API key | P1 |
| Q-02 | How much does masking degrade business output? | The external review's central critique; the ≤ 5 % gate | Phase 1 exit (E-UTIL) | Literature says it is task-dependent (CLAIM-S); `eval/run_util.py` built, never run | Run `eval/run_util.py` on the target model, n ≥ 200, paired | Engineering, with an API key | **P0** |
| Q-03 | Keep Presidio in the design, or drop it? | The slice deviates from ADR-0005 with no ADR; supply-chain surface (T-11) | ADR-0005 amendment | The slice reached structured-type TEST recall 0.92–1.00 without Presidio (FACT-M) | Decide whether Presidio's NER hosting or recognizers beat a direct GLiNER integration on a fresh split | Architect | P1 |
| Q-04 | Which NER model, and what recall on Indian and HR names at what CPU latency? | W1 is the dominant leak | Phase 2 gate | Model-card F1 75–83 (CLAIM-S); third-party F1 0.38 for another model (CLAIM-S); ~125 ms per 500 B CPU (CLAIM-S) | Benchmark 2–3 Apache-licensed checkpoints on a **fresh** split with names absent from the roster | Engineering | P1 |
| Q-05 | Should a Verhoeff-valid 12-digit number in any HRIS-shaped payload be treated as Aadhaar with no keyword? | TEST AADHAAR recall 0.918 came from out-of-window context | W3 fix | Random 12-digit ids pass Verhoeff at ~10–12 % (KNOWN + FACT-M) | Policy decision on the precision cost; measure the false-positive rate on negatives | Privacy office + architect | P1 |
| Q-06 | Can we get authoritative Workday/UKG field catalogues for the field lexicon? | The lexicon is tuned to our own corpus, and TEST showed unseen keys leak | W3 fix; F-11 | None (UNVERIFIED) | Obtain tenant schema exports (field names only, no data) | ESU integration team | P1 |
| Q-07 | What is recall on **real** HR text? | Everything here is synthetic; about 6 free-text templates per split | Any real-data use | None | E-DET-R: run the detector inside the client boundary, report only aggregate counts versus a 200-record human spot check | Client infosec | **P0** |
| Q-08 | Who is the pilot app owner, and which purposes will they register? | R-02: a platform without a user | Phase 0 exit | None | Written commitment from one app owner | Project lead | **P0** |
| Q-09 | Will infosec and privacy sign off the trust boundary and the provider list? | The gateway alone is not a compliance control | Phase 0 exit | Design package exists | Review meeting; record the decision | Project lead | **P0** |
| Q-10 | How are roster sync, nicknames, transliterations and ex-employees handled? | Roster EDM is the strongest control (M-08), and it is only as good as the roster | ADR-0005 D3 in production | Tested only against the synthetic roster (FACT-M) | Design the F-11 sync. Measure nickname and transliteration recall on a fresh split | Engineering + HRIS team | P1 |
| Q-11 | What retention, region and training terms apply at each candidate provider? | Masking reduces exposure; contracts govern the rest | Provider list in the app registry | Not researched (UNVERIFIED) | Legal review of the provider DPAs | Legal | **P0** |
| Q-12 | Is bounded-hold-back streaming a real requirement, or is buffered output acceptable for RESTRICTED data? | Buffered mode removes W4 and W5 by construction (Cloudflare's choice, FACT-P) | ADR-0008 amendment | Chat UX preference (INFERENCE) | Ask the pilot app owner; measure TTFT both ways | Product + architect | P1 |
| Q-13 | Is pseudonymised output personal data in the provider's hands under DPDP? | Decides the contract posture | Legal framing (DESIGN §9.5) | The Act has no pseudonymisation carve-out (KNOWN) | Legal opinion | Legal | P1 |
| Q-14 | What are the DPDP penalty figures for a safeguard failure (₹200 crore vs ₹250 crore)? | Risk quantification only | — | Conflicting secondary sources (CLAIM-S) | Read the primary Act schedule | Legal | P2 |
| Q-15 | Can quasi-identifier combinations (role + location + grade) be scored cheaply? | Re-identification without any name (DESIGN §9.2) | — | Not studied | Spike: k-anonymity-style estimate over ALLOWed attributes | Research | P2 |
| Q-16 | Which KMS/HSM is available in the ESU deployment environment? | The master key is currently an environment variable | Phase 3 | UNVERIFIED | Ask platform/infra | Infra | P1 |
| Q-17 | What is end-to-end latency against a real provider, streaming and not? | N-1 targets were set before measurement | N-1 | Detection only: p95 4.2 ms (FACT-M); end-to-end unmeasured | E-PERF with a real upstream | Engineering | P1 |
| Q-18 | Salesforce streaming masking behaviour; Google Model Armor tool-call coverage | Competitive completeness only | — | UNVERIFIED (primary docs blocked) | Fetch the primary docs from an unblocked network | Research | P2 |
| Q-19 | Is the NIST SP 800-38G Rev.1 FF3 removal final? | Only matters if FPE is ever needed | A-12 | Draft (CLAIM-S) | Check the NIST status page | Research | P2 |
| Q-20 | Does the canonical Cloudflare docs page match the GitHub docs mirror we read? | Every Cloudflare FACT-P rests on the mirror (S-02, S-03) | COMPETITIVE_LANDSCAPE grading | Mirror fetched (FACT-P for the mirror) | Fetch developers.cloudflare.com from an unblocked network | Research | P2 |
| Q-21 | Are LiteLLM #41611 / PR #41936 real, and do they say what ENGINEERING_FACTS claims? | ADR-0008 cites them (C-18) | ADR-0008 amendment | Grade N, sources conflict (UNVERIFIED) | Open the GitHub pages; until then drop them from ADR-0008 | Research | P2 |
| Q-22 | Does an OSS PII plugin exist for Kong or Envoy AI Gateway? | Build-versus-adopt for ADR-0001 | ADR-0001 | Absence seen only in secondary sources (CLAIM-S) | Search the plugin registries directly | Research | P2 |
| Q-23 | Do PII Shield or Skyflow document restore semantics, streaming and tool handling? | Would inform ADR-0002/0003 layering | ADR-0002, ADR-0003 | Vendor self-description only (CLAIM-V) | Read the primary product docs | Research | P2 |
| Q-24 | Does Purview or Azure Content Safety mask prompts? | Competitive row accuracy only | — | Absence (CLAIM-S) | Read the primary Learn docs | Research | P2 |

| Q-25 | Is context-free value-shape masking (PD-10) acceptable on precision/utility for RESTRICTED HR purposes? | Rule tuning does not generalise (M-30); shape masking might | PD-10 | None yet | Fresh-split measurement + E-UTIL with a real model | Architect + privacy office | **P0** |
| Q-26 | May huggingface.co be allowed in the build environment (for GLiNER)? | Only spaCy could be benchmarked; best deployable external-name recall is 0.794 | PD-12 | environment policy (FACT-M: host refused) | Environment owner decision | Owner | P1 |

## Closed in this iteration

| ID | Question | Closed by |
|---|---|---|
| Q-12 | Bounded-hold-back streaming vs buffered for RESTRICTED | Decided: buffered by policy; incremental fails E-STREAM-LEAK (M-25) |
| Q-04 | NER model choice (partially) | spaCy `en_core_web_lg` (inline), trf reference; external recall 0.794 / 0.844 (M-24); GLiNER untested (Q-26) |
| Q-00a | Does roster-based EDM make PERSON detection viable for employees? | FACT-M: recall 1.000, n = 5,766 (M-08) |
| Q-00b | Can the egress invariant be counted as a leak control? | FACT-M: no. 0 true catches while 14.6 % of TEST documents leaked; 8 false alarms (M-09) |
| Q-00c | Does DEV performance on rule-based detectors transfer to unseen templates? | FACT-M: no for HIGH types with unseen keys or shapes (M-05, M-06), and precision collapses (M-11) |
