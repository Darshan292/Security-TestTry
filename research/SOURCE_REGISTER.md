# Source Register

Purpose: one row per distinct source behind the ESU privacy-gateway design package, so that every
claim (`C-NN`), measurement (`M-NN`) and decision can point at a stable `S-NN` and at how well we
actually reached it. Date: 2026-09-30. Scheme and epistemic classes: `research/PROVENANCE.md`.
Built only from text in this repo; no source was re-fetched for this register.

## How to read

- **ID**: `S-01` is the ESU survey report. Others are grouped by Kind in the order vendor docs, standards and law,
  OSS repos and packages (incl. model cards), issues/PRs, papers, blogs and secondary, then internal.
  With 118 sources the two-digit `S-9x` band is not available, so the nine internal eval artifacts are simply the last IDs (S-110 to S-118). IDs are stable; do not renumber.
- **Access grade** (what *we* reached, not what the source is worth):
  **P** primary reached (raw.githubusercontent.com mirror, googleapis proto, PyPI JSON, or a GitHub page whose banner/text our notes quote);
  **S** secondary only (WebSearch summary, page summariser, or blocked host);
  **N** not accessed (named in the survey or notes only);
  **I** internal (this repo's own files).
  Where the notes do not say the primary was fetched, the grade is S or N. Blocked hosts
  (developers.cloudflare.com, help.salesforce.com, developer.salesforce.com, help.sap.com, docs.cloud.google.com,
  huggingface.co, arxiv.org, microsoft.github.io) are S/N unless a raw.githubusercontent.com mirror was fetched; the mirror is then the row's URL and the canonical URL is noted in the title.
- **Accessed how**: `primary-fetched` | `search-summary` | `cited-not-opened` | `internal`.
- **Cited in** abbreviations: ESU = `research/input/ESU_source_report.md`; INDUSTRY = `research/INDUSTRY_PATTERNS.md`;
  OSS = `research/OSS_COMPONENTS.md`; ENGINEERING = `research/ENGINEERING_FACTS.md`; VERIF = `research/VERIFICATION_2026-09-30.md`;
  DESIGN, THREAT (`THREAT_MODEL.md`), RISK (`RISK_REGISTER.md`), ROADMAP, README, SECEVAL (`SECURITY_EVALUATION.md`), `ADR-NNNN` = `ADR/NNNN-*.md`.
  EVALUATION_PLAN.md and CLAUDE.md cite no external source. Name-only citations (for example "Uber's GenAI Gateway", "AWS documents...",
  "DPDP") are attached to the row for the page that carries the claim.
- **Epistemic class** (per PROVENANCE): grade P can support FACT-P; grade I measured results support FACT-M;
  grade S supports CLAIM-S only, with a risk note; a vendor's own description is CLAIM-V (see warnings); grade N supports nothing until opened.
  Three blog posts quoting one vendor page count as one CLAIM-S.
- Not listed as sources: the four `research/*.md` notes (they are our own compilations and the basis of the "Accessed how" column),
  knowledge marked `[K]` in ENGINEERING_FACTS (Indian ID formats, Verhoeff, GSTIN mod-36, AES-SIV usage: background, no citation given),
  and technology names in the stack (Starlette, httpx, pyahocorasick, PostgreSQL, Kubernetes) that are not cited as evidence.
  Cedar, Strands SDK and GDPR appear by name only as scope or workaround mentions and are not sources.
- Deduplication: one row per page. URL variants (trailing punctuation; Salesforce `&language=en_US&type=5` suffix) are merged.
  Distinct pages on one site, and distinct URLs for one instrument (DPDP Act PDF vs landing page), stay separate rows.

## Counts (118 sources)

By access grade and the strongest epistemic class each can support:

| Grade | Rows | Strongest class supported |
|---|---:|---|
| P primary reached | 14 | FACT-P |
| S secondary only | 74 | CLAIM-S (CLAIM-V where vendor self-description) |
| N not accessed | 20 | none until opened |
| I internal | 10 | FACT-M (9 measured/reproducible sources); S-01 survey is CLAIM-S |

By kind: vendor-doc 31, blog 15, oss-repo 20, issue/PR 15, internal 10 (S-01 plus 9 eval artifacts), paper 7, standard 5, law 5, package-index 5, model-card 5.

Kind by grade:

| Kind | P | S | N | I | Total |
|---|---:|---:|---:|---:|---:|
| vendor-doc | 6 | 18 | 7 | 0 | 31 |
| standard | 0 | 2 | 3 | 0 | 5 |
| law | 0 | 1 | 4 | 0 | 5 |
| oss-repo | 4 | 15 | 1 | 0 | 20 |
| model-card | 0 | 5 | 0 | 0 | 5 |
| package-index | 4 | 1 | 0 | 0 | 5 |
| issue/PR | 0 | 12 | 3 | 0 | 15 |
| paper | 0 | 5 | 2 | 0 | 7 |
| blog | 0 | 15 | 0 | 0 | 15 |
| internal | 0 | 0 | 0 | 10 | 10 |
| **Total** | **14** | **74** | **20** | **10** | **118** |

## Register

| ID | Source (title/name) | URL | Kind | Publisher | Accessed how | Access grade | Cited in (files) | What we use it for (≤ 15 words) |
|---|---|---|---|---|---|---|---|---|
| S-01 | ESU source report: Privacy-Preserving LLM Security Gateway for ESU HRIS & ERP Workloads (repo file research/input/ESU_source_report.md; DESIGN calls it ESU_Privacy_Preserving_LLM_Security_Gateway_Report.md) | `research/input/ESU_source_report.md` | internal | ESU (Enterprise Solutions Unit), 2026-09-30 | internal | I | DESIGN, README, ADR-0002, ADR-0003, ADR-0004, ADR-0005, ADR-0006, OSS, VERIF | Survey we verify against; every claim is secondary (CLAIM-S at best). |
| S-02 | Cloudflare AI Gateway DLP (docs source, mirror; canonical page https://developers.cloudflare.com/ai-gateway/features/dlp/ not fetched, host blocked) | https://raw.githubusercontent.com/cloudflare/cloudflare-docs/production/src/content/docs/ai-gateway/features/dlp/index.mdx | vendor-doc | Cloudflare | primary-fetched | P | ESU (canonical URL), VERIF, ADR-0008, SECEVAL | Response scanning buffers streams; actions pass/flag/block, no redaction; tool args scanned as text. |
| S-03 | Cloudflare AI Gateway Guardrails, usage considerations (docs source, mirror; canonical URL not recorded in repo) | https://raw.githubusercontent.com/cloudflare/cloudflare-docs/production/src/content/docs/ai-gateway/features/guardrails/usage-considerations.mdx | vendor-doc | Cloudflare | primary-fetched | P | VERIF, SECEVAL | Streaming enforcement gap: REST logs only, gateway endpoints buffer and de-stream responses. |
| S-04 | Cloudflare AI Gateway Guardrails (canonical docs page) | https://developers.cloudflare.com/ai-gateway/features/guardrails/ | vendor-doc | Cloudflare | search-summary | S | INDUSTRY | Context for Guardrails as a separate feature from DLP; superseded by mirror row. |
| S-05 | Cloudflare One Data Loss Prevention (docs) | https://developers.cloudflare.com/cloudflare-one/data-loss-prevention/ | vendor-doc | Cloudflare | search-summary | S | INDUSTRY | Background on Cloudflare DLP profiles; not the AI Gateway DLP page. |
| S-06 | AWS Bedrock Guardrails, sensitive information filters | https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html | vendor-doc | Amazon Web Services | search-summary | S | ESU, INDUSTRY, ADR-0009 | BLOCK/MASK actions, one-way tags; tool-use fields not covered by default. |
| S-07 | AWS Bedrock Guardrails, use cases (sync vs async streaming) | https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use.html | vendor-doc | Amazon Web Services | search-summary | S | INDUSTRY, ADR-0008 | Async streaming releases unfiltered chunks and cannot mask; sync buffers. |
| S-08 | Google Model Armor API definition (service.proto, googleapis mirror) | https://raw.githubusercontent.com/googleapis/googleapis/master/google/cloud/modelarmor/v1/service.proto | vendor-doc | Google | primary-fetched | P | VERIF, INDUSTRY | Streaming sanitize RPCs exist; absence of any tool-call field; SDP de-identify result. |
| S-09 | Google Model Armor, sanitize prompts and responses (docs) | https://docs.cloud.google.com/model-armor/sanitize-prompts-responses | vendor-doc | Google | search-summary | S | INDUSTRY | Advanced mode with inspect and de-identify templates; latency and streaming unverified. |
| S-10 | Google Sensitive Data Protection, pseudonymization (docs) | https://docs.cloud.google.com/sensitive-data-protection/docs/pseudonymization | vendor-doc | Google | search-summary | S | ESU, INDUSTRY | Deterministic AES-SIV/FPE/HMAC pseudonymization with key-based re-identification, referential integrity. |
| S-11 | Google Sensitive Data Protection, transformations reference (docs) | https://docs.cloud.google.com/sensitive-data-protection/docs/transformations-reference | vendor-doc | Google | search-summary | S | INDUSTRY | Transform catalogue: AES-SIV, FFX FPE, KMS-wrapped keys, surrogate infotype annotations. |
| S-12 | Google Sensitive Data Protection, inspect and de-identify sensitive text (docs) | https://docs.cloud.google.com/sensitive-data-protection/docs/inspect-sensitive-text-de-identify | vendor-doc | Google | search-summary | S | ESU, VERIF | Reversibility requires choosing a reversible method; caller re-identifies itself. |
| S-13 | Salesforce Einstein Trust Layer architecture (help article) | https://help.salesforce.com/s/articleView?id=ai.generative_ai_trust_arch.htm&language=en_US&type=5 | vendor-doc | Salesforce | search-summary | S | INDUSTRY | Pipeline order: grounding, masking, prompt defense, LLM, demasking, audit trail. |
| S-14 | Salesforce data masking considerations (help article) | https://help.salesforce.com/s/articleView?id=sf.generative_ai_trust_data_mask_considerations.htm&language=en_US&type=5 | vendor-doc | Salesforce | search-summary | S | INDUSTRY | Documented limits: entity and language lists, no 100% accuracy, quality degradation. |
| S-15 | Salesforce agents and data masking (help article; also cited with &language=en_US&type=5 suffix) | https://help.salesforce.com/s/articleView?id=ai.agent_trust_data_masking.htm | vendor-doc | Salesforce | search-summary | S | INDUSTRY, VERIF, ADR-0009, SECEVAL | Masking is disabled for agents and agent-invoked prompt templates. |
| S-16 | Salesforce mask data with Einstein Trust Layer (help article) | https://help.salesforce.com/s/articleView?id=ai.generative_ai_mask_data.htm | vendor-doc | Salesforce | search-summary | S | VERIF | 65,536-token context cap when masking is on. |
| S-17 | SAP AI Core generative AI, streaming (docs source, mirror of help.sap.com) | https://raw.githubusercontent.com/SAP-docs/sap-artificial-intelligence/main/docs/sap-ai-core/generativeAI/streaming-3340907.md | vendor-doc | SAP | primary-fetched | P | VERIF, INDUSTRY, SECEVAL | Streaming unmasking works for pseudonymization; split tags re-chunked; small chunks reduce accuracy. |
| S-18 | SAP AI Core, tool calling with other orchestration modules (docs source, mirror) | https://raw.githubusercontent.com/SAP-docs/sap-artificial-intelligence/main/docs/sap-ai-core/generativeAI/tool-calling-with-other-orchestration-modules-177e9fd.md | vendor-doc | SAP | primary-fetched | P | VERIF, INDUSTRY, SECEVAL | Tool-call arguments unmasked on return and re-masked on send; pseudonymization only. |
| S-19 | SAP AI Core, data masking (docs source, mirror) | https://raw.githubusercontent.com/SAP-docs/sap-artificial-intelligence/main/docs/sap-ai-core/generativeAI/data-masking-8b87002.md | vendor-doc | SAP | primary-fetched | P | VERIF, INDUSTRY | 27 locale-limited entity types; MASKED_ENTITY (anonymize) vs MASKED_ENTITY_ID (pseudonymize); no guarantee. |
| S-20 | SAP AI Core, orchestration (help.sap.com page) | https://help.sap.com/docs/sap-ai-core/generative-ai/orchestration | vendor-doc | SAP | search-summary | S | INDUSTRY | Optional data-masking module inside orchestration workflow; superseded by mirror rows. |
| S-21 | Microsoft Purview DSPM for AI, considerations (Learn) | https://learn.microsoft.com/en-us/purview/dspm-for-ai-considerations | vendor-doc | Microsoft | search-summary | S | VERIF, SECEVAL | Detects, classifies, blocks; no masking or reversibility statement found (absence only). |
| S-22 | Microsoft Azure confidential AI (Learn) | https://learn.microsoft.com/en-us/azure/confidential-computing/confidential-ai | vendor-doc | Microsoft | cited-not-opened | N | ESU | Confidential-computing reference in the survey; deferred, not evaluated. |
| S-23 | Kong AI PII Sanitizer plugin (docs) | https://developer.konghq.com/plugins/ai-sanitizer/ | vendor-doc | Kong | search-summary | S | OSS | Enterprise-only plugin, Gateway 3.10+; basis for REJECT decision. |
| S-24 | Lakera Guard, data leakage prevention (docs) | https://docs.lakera.ai/docs/data-leakage-prevention | vendor-doc | Lakera | search-summary | S | INDUSTRY | Vendor-described PII block/mask with match locations; comparison row only. |
| S-25 | NVIDIA NeMo Guardrails, Presidio integration (docs) | https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/third-party/presidio | vendor-doc | NVIDIA | search-summary | S | OSS | Input/output/retrieval sensitive-data rails on Presidio; detect and mask, no restore seen. |
| S-26 | Skyflow, LLM privacy pattern (docs) | https://docs.skyflow.dev/docs/fundamentals/patterns/llm-privacy | vendor-doc | Skyflow | cited-not-opened | N | ESU | Vault and token lifecycle reference cited by the survey; vendor self-description. |
| S-27 | Skyflow PII data privacy vault (product page) | https://www.skyflow.com/product/pii-data-privacy-vault | vendor-doc | Skyflow | search-summary | S | INDUSTRY | Policy-gated detokenization and tokenization claims; vendor claims only. |
| S-28 | Protecto (vendor home page) | https://www.protecto.ai/ | vendor-doc | Protecto | cited-not-opened | N | ESU, INDUSTRY | Survey positions Protecto as AI data control plane; marketing description only. |
| S-29 | Nightfall Developer API overview (help) | https://help.nightfall.ai/developer-api/introduction/overview | vendor-doc | Nightfall | cited-not-opened | N | ESU, INDUSTRY | Detector orchestration reference; notes call third-party-transit claim unverified. |
| S-30 | Private AI (vendor home page) | https://www.private-ai.com/en/ | vendor-doc | Private AI | cited-not-opened | N | ESU, INDUSTRY | Multimodal de-identification vendor named in survey; no primary retrieved. |
| S-31 | Workday security overview (web page) | https://www.workday.com/en-us/why-workday/trust/security.html | vendor-doc | Workday | cited-not-opened | N | ESU | Source-system RBAC and tenant segregation context for authorization design. |
| S-32 | Workday security datasheet (PDF) | https://www.workday.com/content/dam/web/se/documents/datasheets/datasheet-workday-security-se.pdf | vendor-doc | Workday | cited-not-opened | N | ESU | Same Workday security context as the web page; vendor datasheet. |
| S-33 | OWASP GenAI LLM Top 10 (2025) landing page | https://genai.owasp.org/llm-top-10/ | standard | OWASP | cited-not-opened | N | ESU, ADR-0009, ROADMAP | Sensitive Information Disclosure, Improper Output Handling, LLM06 Excessive Agency framing. |
| S-34 | OWASP Top 10 for LLM Applications v2025 (PDF) | https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf | standard | OWASP | cited-not-opened | N | ESU | Full 2025 risk text behind the OWASP citations; not opened by us. |
| S-35 | NIST AI RMF, Generative AI Profile (publication page) | https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence | standard | NIST | cited-not-opened | N | ESU | Generative-AI risk-management framework reference in survey only. |
| S-36 | NIST SP 800-38G (csrc page; shown as withdrawn per notes) | https://csrc.nist.gov/pubs/sp/800/38/g/final | standard | NIST | search-summary | S | ENGINEERING | Original FPE standard status; withdrawn, superseded by Rev.1 draft. |
| S-37 | NIST SP 800-38G Rev.1, second draft (Feb 2025; removes FF3/FF3-1, keeps FF1) | https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-38Gr1-draft.pdf | standard | NIST | search-summary | S | ENGINEERING, ADR-0002 | Basis for rejecting FF3-1 and limiting FPE to FF1; still a draft. |
| S-38 | India Digital Personal Data Protection Act, 2023 (MeitY PDF) | https://www.meity.gov.in/static/uploads/2024/02/Digital-Personal-Data-Protection-Act-2023.pdf | law | Government of India (MeitY) | cited-not-opened | N | ESU, ENGINEERING, DESIGN, THREAT, RISK, ADR-0003, ADR-0004, README | Processor duties, safeguards, purpose limitation; pseudonymised data treated as personal data. |
| S-39 | India Digital Personal Data Protection Act, 2023 (MeitY landing page) | https://www.meity.gov.in/digital-personal-data-protection-act-2023 | law | Government of India (MeitY) | cited-not-opened | N | ESU | Alternate MeitY link for the Act in survey appendix. |
| S-40 | India Digital Personal Data Protection Rules, 2025 (MeitY landing page) | https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa | law | Government of India (MeitY) | cited-not-opened | N | ESU, ENGINEERING | Notified 13 Nov 2025 with phased commencement; Rule 7 breach and Rule 15 transfer. |
| S-41 | India Digital Personal Data Protection Rules, 2025 (MeitY PDF) | https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf | law | Government of India (MeitY) | cited-not-opened | N | ESU | Gazette text of the Rules; not opened by us. |
| S-42 | DPDP Rules 2025, Rule 7 text (third-party site dpdpa.com) | https://www.dpdpa.com/dpdparules/rule7.html | law | dpdpa.com (third party) | search-summary | S | ENGINEERING | Breach notification: principals without delay, Board report within 72 hours. |
| S-43 | Presidio (GitHub, data-privacy-stack; moved from Microsoft) | https://github.com/data-privacy-stack/presidio | oss-repo | data-privacy-stack (formerly Microsoft) | primary-fetched | P | ESU, OSS, ADR-0005, DESIGN, README, SECEVAL | Adopted analyzer; MIT; repo banner confirms move; warns detection cannot guarantee coverage. |
| S-44 | Presidio project transition doc (GitHub) | https://github.com/data-privacy-stack/presidio/blob/main/docs/project_transition.md | oss-repo | data-privacy-stack | primary-fetched | P | ESU, OSS | Confirms ownership move and image move from MCR to ghcr.io. |
| S-45 | presidio-research (GitHub) | https://github.com/data-privacy-stack/presidio-research | oss-repo | data-privacy-stack | search-summary | S | ESU, OSS | Evaluation-data generation and notebooks; benchmark discipline reference. |
| S-46 | presidio-analyzer (PyPI JSON) | https://pypi.org/pypi/presidio-analyzer/json | package-index | PyPI / Presidio maintainers | primary-fetched | P | VERIF | Version 2.2.364 (2026-07-22), MIT; no native async API in description. |
| S-47 | presidio-analyzer (PyPI project page) | https://pypi.org/project/presidio-analyzer/ | package-index | PyPI / Presidio maintainers | search-summary | S | OSS | Extras (gliner, transformers), batch features, Python range; JSON row is the fetched one. |
| S-48 | protectai/llm-guard (GitHub) | https://github.com/protectai/llm-guard | oss-repo | Protect AI | primary-fetched | P | ESU, OSS | Archived 9 Jul 2026 per banner; REJECT as dependency. |
| S-49 | guardrails-ai/guardrails_pii (GitHub) | https://github.com/guardrails-ai/guardrails_pii | oss-repo | Guardrails AI | primary-fetched | P | ESU, OSS, README | Archived; Presidio plus GLiNER validator; pattern reference only. |
| S-50 | carnaval-ai/carnaval (GitHub) | https://github.com/carnaval-ai/carnaval | oss-repo | carnaval-ai | search-summary | S | ESU, OSS | Small reversible-anonymization design reference; 1 star, beta; enterprise claim unverified. |
| S-51 | truefoundry/custom-guardrails-template (GitHub) | https://github.com/truefoundry/custom-guardrails-template | oss-repo | TrueFoundry | search-summary | S | ESU, OSS | FastAPI guardrail-service pattern with Presidio redaction; licence unverified. |
| S-52 | Portkey-AI/gateway (GitHub) | https://github.com/Portkey-AI/gateway | oss-repo | Portkey | search-summary | S | ESU, OSS, ADR-0001 | TypeScript gateway architecture reference; guardrail and streaming claims are self-described. |
| S-53 | Portkey-AI/gateway wiki, Guardrails on the Gateway Framework | https://github.com/Portkey-AI/gateway/wiki/Guardrails-on-the-Gateway-Framework | oss-repo | Portkey | cited-not-opened | N | ESU | Survey citation for gateway-layer guardrails; not opened by us. |
| S-54 | Portkey-AI/gateway releases page | https://github.com/Portkey-AI/gateway/releases | oss-repo | Portkey | search-summary | S | VERIF | v1.15.2 dated Jan 2025; release-tab staleness suspected. |
| S-55 | BerriAI/litellm (GitHub) | https://github.com/BerriAI/litellm | oss-repo | BerriAI | search-summary | S | OSS, ADR-0001, ADR-0002, THREAT | REJECT in front of core; Presidio guardrail known bugs; MIT with enterprise/ carve-out. |
| S-56 | litellm (PyPI JSON) | https://pypi.org/pypi/litellm/json | package-index | PyPI / BerriAI | primary-fetched | P | VERIF | Latest 1.103.1, MIT; upload date not extractable. |
| S-57 | envoyproxy/ai-gateway (GitHub) | https://github.com/envoyproxy/ai-gateway | oss-repo | Envoy project | search-summary | S | OSS | Rebranded Agent Router; no OSS PII plugin found in secondary sources. |
| S-58 | envoyproxy/ai-gateway releases page | https://github.com/envoyproxy/ai-gateway/releases | oss-repo | Envoy project | search-summary | S | VERIF | v1.1.0 shown as latest; year suspect, recheck. |
| S-59 | NVIDIA-NeMo/Guardrails (GitHub) | https://github.com/NVIDIA-NeMo/Guardrails | oss-repo | NVIDIA | search-summary | S | OSS | REFERENCE ONLY: Presidio-based rails, no reversible restore seen. |
| S-60 | nemoguardrails (PyPI JSON) | https://pypi.org/pypi/nemoguardrails/json | package-index | PyPI / NVIDIA | primary-fetched | P | VERIF | 0.24.1 uploaded 2026-09-16, Apache-2.0. |
| S-61 | open-policy-agent/opa (GitHub) | https://github.com/open-policy-agent/opa | oss-repo | CNCF / OPA maintainers | search-summary | S | OSS, ADR-0006, ROADMAP | REFERENCE ONLY; v1.21.1 (avoid 1.21.0); WASM-from-Python and latency unverified. |
| S-62 | pyca/cryptography (GitHub) | https://github.com/pyca/cryptography | oss-repo | PyCA | search-summary | S | OSS, DESIGN, ADR-0002 | AESSIV since 37.0.0 (per notes, raw docs mirror not recorded); Apache-2.0 OR BSD-3. |
| S-63 | cryptography (PyPI JSON) | https://pypi.org/pypi/cryptography/json | package-index | PyPI / PyCA | primary-fetched | P | VERIF | 50.0.1 and dual licence confirmed; release date not extracted. |
| S-64 | urchade/GLiNER (GitHub) | https://github.com/urchade/GLiNER | oss-repo | urchade (GLiNER authors) | search-summary | S | OSS, ESU (GLiNER named, no URL) | GLiNER library, Apache-2.0, ~4k stars; NER engine for Phase 2. |
| S-65 | francesco-stimola/llm-proxy-pii-rust (GitHub) | https://github.com/francesco-stimola/llm-proxy-pii-rust | oss-repo | francesco-stimola | search-summary | S | OSS | Reversible PII proxy; AGPL-3.0 so REJECT; self-reported latency numbers. |
| S-66 | jfreemansh/Anonproxy (GitHub) | https://github.com/jfreemansh/Anonproxy | oss-repo | jfreemansh | search-summary | S | OSS | MIT; tolerant restoration across split stream chunks; design reference, 7 stars. |
| S-67 | malteos/awesome-anonymization-for-llms (GitHub list) | https://github.com/malteos/awesome-anonymization-for-llms | oss-repo | malteos | search-summary | S | OSS | Pointer list to further reversible-PII projects; entries not opened. |
| S-68 | knowledgator/gliner-pii-large-v1.0 (Hugging Face model card) | https://huggingface.co/knowledgator/gliner-pii-large-v1.0 | model-card | Knowledgator | search-summary | S | OSS, VERIF, ADR-0005 | Vendor-reported F1 family 75.5-83.25, Apache-2.0; benchmark dataset unidentified. |
| S-69 | knowledgator/gliner-pii-base-v1.0 README (Hugging Face, resolve URL) | https://huggingface.co/knowledgator/gliner-pii-base-v1.0/resolve/main/README.md?download=true | model-card | Knowledgator | search-summary | S | VERIF | Only place the Apache-2.0 front-matter was seen (search result). |
| S-70 | urchade/gliner_multi_pii-v1 (Hugging Face model card) | https://huggingface.co/urchade/gliner_multi_pii-v1 | model-card | urchade | search-summary | S | VERIF, OSS | Apache-2.0 multilingual PII model; low recall in third-party benchmark. |
| S-71 | nvidia/gliner-PII (Hugging Face model card) | https://huggingface.co/nvidia/gliner-PII | model-card | NVIDIA | search-summary | S | OSS, VERIF | NVIDIA Open Model License, not Apache; benchmarks unverified. |
| S-72 | fastino/gliner2-privacy-filter-PII-multi (Hugging Face model card) | https://huggingface.co/fastino/gliner2-privacy-filter-PII-multi | model-card | Fastino | search-summary | S | VERIF | GLiNER2-PII: Apache-2.0, 205M params, 42 entity types; F1 not retrieved. |
| S-73 | LiteLLM issue #31950 (tool_calls placeholders not restored) | https://github.com/BerriAI/litellm/issues/31950 | issue/PR | BerriAI (LiteLLM) | search-summary | S | OSS, ADR-0008 | Evidence that string-replace restore misses tool_calls arguments. |
| S-74 | LiteLLM issue #30728 (Presidio guardrail fails open on analyzer error) | n/a (number only; no URL in repo) | issue/PR | BerriAI (LiteLLM) | search-summary | S | OSS, ADR-0007 | Motivates fail-closed egress invariant. |
| S-75 | LiteLLM issue #41611 (value split across SSE chunks bypasses guardrail) | https://github.com/BerriAI/litellm/issues/41611 | issue/PR | BerriAI (LiteLLM) | cited-not-opened | N | ENGINEERING, OSS, ADR-0008 | Claimed streaming split-chunk leak; not verifiable by us (see warnings). |
| S-76 | LiteLLM PR #41936 (buffer whole stream, scan, emit once) | https://github.com/BerriAI/litellm/pull/41936 | issue/PR | BerriAI (LiteLLM) | cited-not-opened | N | ENGINEERING | Claimed full-buffer fix for #41611; unverified. |
| S-77 | LiteLLM docs PR #1790 (docs for streaming buffer change) | https://github.com/BerriAI/litellm-docs/pull/1790 | issue/PR | BerriAI (litellm-docs) | cited-not-opened | N | ENGINEERING | Companion docs PR to #41936; unverified. |
| S-78 | LiteLLM PR #42351 (streaming fix seen in search) | n/a (number only; no URL in repo) | issue/PR | BerriAI (LiteLLM) | search-summary | S | OSS, ADR-0008 | Streaming paths repeatedly needing patches; supports pass-through-is-leaky argument. |
| S-79 | LiteLLM PR #43345 (Gemini SSE buffering) | n/a (number only; no URL in repo) | issue/PR | BerriAI (LiteLLM) | search-summary | S | OSS, ADR-0008 | Second example of streaming patch churn. |
| S-80 | LiteLLM issue #42476 (Anthropic passthrough streams bypass post-call hooks) | n/a (number only; no URL in repo) | issue/PR | BerriAI (LiteLLM) | search-summary | S | OSS, ADR-0008 | Streaming bypass example for the pass-through option. |
| S-81 | LiteLLM #41600 (feature request, not the leak) | n/a (number only; no URL in repo) | issue/PR | BerriAI (LiteLLM) | search-summary | S | OSS | Ruled out as the leak issue; kept to prevent mis-citation. |
| S-82 | NVIDIA NeMo Guardrails PR #2400 (Presidio blocks the async event loop) | https://github.com/NVIDIA-NeMo/Guardrails/pull/2400 | issue/PR | NVIDIA | search-summary | S | VERIF | Shows Presidio is sync-only; wrap in thread or process executor. |
| S-83 | microsoft/presidio issues search for 'async' | https://github.com/microsoft/presidio/issues?q=async | issue/PR | Presidio maintainers | search-summary | S | VERIF | Search found no native async engine issue; old org path used. |
| S-84 | Presidio issue #1769 (FastAPI server, open) | n/a (number only; no URL in repo) | issue/PR | Presidio maintainers | search-summary | S | VERIF | REST-server async work, not the analyzer engine. |
| S-85 | Presidio PR #2039 (FastAPI server) | n/a (number only; no URL in repo) | issue/PR | Presidio maintainers | search-summary | S | VERIF | REST-server async work, not the analyzer engine. |
| S-86 | cloudflare/cloudflare-docs issue #28325 | https://github.com/cloudflare/cloudflare-docs/issues/28325 | issue/PR | Cloudflare | search-summary | S | INDUSTRY, VERIF | Docs issue on SSE/tool-call coverage; resolved by mirror docs, do not cite alone. |
| S-87 | foro-sh/pii-mcp issue #41 (GLiNER latency measurement) | https://github.com/foro-sh/pii-mcp/issues/41 | issue/PR | foro-sh | search-summary | S | ENGINEERING, ADR-0005 | GLiNER 125 ms median / 175 ms p95 per ~500 B on M-series CPU. |
| S-88 | GLiNER2-PII paper with SPY benchmark (arXiv 2605.09973) | https://arxiv.org/abs/2605.09973 | paper | arXiv (Fastino authors) | search-summary | S | VERIF, OSS | Third-party F1 ~0.38 for gliner_multi_pii-v1; low recall; unread beyond summary. |
| S-89 | Salesforce zero-retention reference (arXiv 2510.11558; title not recorded) | https://arxiv.org/pdf/2510.11558 | paper | arXiv | search-summary | S | INDUSTRY | Backs contractual zero retention with OpenAI/Azure OpenAI; title unknown. |
| S-90 | Balancing Privacy and Utility in Personal LLM Writing Tasks (ACL PrivateNLP 2025) | https://aclanthology.org/2025.privatenlp-main.3.pdf | paper | ACL Anthology | search-summary | S | ENGINEERING | Masking and pseudonymization cause minimal quality loss; 97-99% entity masking. |
| S-91 | Tau-Eval (arXiv 2506.05979) | https://arxiv.org/pdf/2506.05979 | paper | arXiv | search-summary | S | ENGINEERING | Utility loss from anonymization is task-dependent; justifies our own E-UTIL. |
| S-92 | CAMP consistent synthetic identities for agents (arXiv 2604.16521) | https://arxiv.org/html/2604.16521v1 | paper | arXiv | search-summary | S | ENGINEERING | Consistent identities plus de-masking for multi-turn agents; supports stable aliases. |
| S-93 | RAT-Bench (arXiv 2602.12806) | https://arxiv.org/html/2602.12806v1 | paper | arXiv | cited-not-opened | N | ENGINEERING | Named as relevant; numbers not extracted. |
| S-94 | Survey on LLM privacy/anonymization (arXiv 2508.21587; title not recorded) | https://arxiv.org/pdf/2508.21587 | paper | arXiv | cited-not-opened | N | ENGINEERING | Named as relevant; numbers not extracted. |
| S-95 | Inside the Einstein Trust Layer (Salesforce developer blog, 2023) | https://developer.salesforce.com/blogs/2023/10/inside-the-einstein-trust-layer | blog | Salesforce | search-summary | S | INDUSTRY | Vendor description of masking, demasking, audit trail; self-description. |
| S-96 | Integrate tokenization with Amazon Bedrock Guardrails (AWS ML blog) | https://aws.amazon.com/blogs/machine-learning/integrate-tokenization-with-amazon-bedrock-guardrails-for-secure-data-handling/ | blog | Amazon Web Services | search-summary | S | INDUSTRY | Pattern for reversible tokenization around Bedrock Guardrails. |
| S-97 | Extend Bedrock Guardrails to tool interactions with Strands Agents SDK (AWS security blog) | https://aws.amazon.com/blogs/security/extend-amazon-bedrock-guardrails-to-tool-interactions-using-the-strands-agents-sdk/ | blog | Amazon Web Services | search-summary | S | INDUSTRY, ADR-0009 | Tool interactions sit outside default guardrail boundary; supports tool-result enforcement. |
| S-98 | Introducing PII Shield: a privacy proxy for every LLM call (Microsoft Tech Community) | https://techcommunity.microsoft.com/blog/azuredevcommunityblog/introducing-pii-shield-a-privacy-proxy-for-every-llm-call/4514726 | blog | Microsoft | search-summary | S | INDUSTRY | Proxy swapping PII for stable placeholders; comparison row. |
| S-99 | Your LLM needs a bouncer (Medium, Google Cloud community) | https://medium.com/google-cloud/your-llm-needs-a-bouncer-5c4ee64a3673 | blog | Medium (community author) | search-summary | S | VERIF | Only Model Armor latency anecdotes (50-200 ms same region); not authoritative. |
| S-100 | Uber GenAI Gateway (Uber Engineering blog) | https://www.uber.com/us/en/blog/genai-gateway/ | blog | Uber | search-summary | S | INDUSTRY, ADR-0002, SECEVAL | Gateway PII redactor with mapping; inconsistent placeholders broke caching and RAG. |
| S-101 | Uber GenAI Gateway coverage (InfoQ, 2024-09) | https://www.infoq.com/news/2024/09/uber-genai-gateway-llm-openai/ | blog | InfoQ | search-summary | S | INDUSTRY | Secondary write-up of the Uber blog; one claim, not independent. |
| S-102 | Protect PII in any LLM platform (Protecto blog) | https://www.protecto.ai/blog/protect-pii-in-any-llm-platform/ | blog | Protecto | search-summary | S | INDUSTRY | Tokenize-then-restore-for-authorised-users pattern; vendor self-description. |
| S-103 | PII redaction: LLM gateway vs application (TrueFoundry blog) | https://www.truefoundry.com/blog/pii-redaction-llm-gateway-vs-application | blog | TrueFoundry | search-summary | S | INDUSTRY | Third-party latency ranges (regex sub-ms, NER tens of ms); not authoritative. |
| S-104 | GLiNER2-PII: open-source privacy filtering (Fastino blog) | https://fastino.ai/blog/gliner2-pii-open-source-privacy-filtering-with-pii-detection | blog | Fastino | search-summary | S | OSS | Vendor announcement for GLiNER2-PII models; self-description. |
| S-105 | What is GLiNER (Superlinked glossary) | https://superlinked.com/glossary/what-is-gliner | blog | Superlinked | search-summary | S | ENGINEERING | GLiNER medium-v2.1 107 ms p50 on L4 GPU; not re-checked. |
| S-106 | DPDP Rules 2025 compliance guide (Seclore) | https://www.seclore.com/fundamentals/dpdp-rules-2025-compliance-guide/ | blog | Seclore | search-summary | S | ENGINEERING | Rules notified 13 Nov 2025; 18-month phase-in; vendor guide. |
| S-107 | DPDP Rules 2025 complete compliance guide (Cy5) | https://www.cy5.io/blog/dpdp-rules-2025-complete-compliance-guide-cloud-security/ | blog | Cy5 | search-summary | S | ENGINEERING | Second guide for the DPDP phase-in timeline; not independent. |
| S-108 | Data breach reporting timeline of DPDP Rules 2025 explained (MediaNama) | https://www.medianama.com/2025/11/223-data-breach-reporting-timeline-of-dpdp-rules-2025-explained/ | blog | MediaNama | search-summary | S | ENGINEERING | Secondary confirmation of Rule 7 breach reporting timeline. |
| S-109 | The DPDP cross-border transfer rules aren't live yet (Bar & Bench column) | https://www.barandbench.com/columns/the-dpdp-cross-border-transfer-rules-arent-live-yet-so-why-are-contracts-being-redrafted-as-if-they-are | blog | Bar & Bench | search-summary | S | ENGINEERING | Opinion column: S.16/Rule 15 transfer rules reportedly not yet operational. |
| S-110 | FROZEN_DEV_a51c22d.json (frozen DEV detection and adversarial results, code a51c22d) | `eval/results/FROZEN_DEV_a51c22d.json` | internal | This repo | internal | I | SECEVAL | DEV recall, leak rate and adversarial numbers; basis for FACT-M measurements. |
| S-111 | FROZEN_TEST_a51c22d.json (one-shot held-out TEST results, code a51c22d) | `eval/results/FROZEN_TEST_a51c22d.json` | internal | This repo | internal | I | SECEVAL | Held-out TEST result: 2.56% leak, AADHAAR 0.918, SECRET 0.916 fail. |
| S-112 | TEST_LOCK.json (lock proving one-shot TEST run on clean commit) | `eval/results/TEST_LOCK.json` | internal | This repo | internal | I | SECEVAL | Evidence TEST ran once at a51c22d; corpus hash field empty (runner bug). |
| S-113 | eval/data/MANIFEST.json (SHA-256 of frozen synthetic corpus splits) | `eval/data/MANIFEST.json` | internal | This repo | internal | I | SECEVAL | Corpus integrity hashes; test hash re-verified by hand after run. |
| S-114 | eval/synth/generate.py (synthetic corpus generator, seed 1337) | `eval/synth/generate.py` | internal | This repo | internal | I | SECEVAL | Reproduces byte-identical synthetic corpus for DEV/TUNE/TEST. |
| S-115 | eval/results/det_dev.md (E-DET DEV report) | `eval/results/det_dev.md` | internal | This repo | internal | I | (not cited by name; produced by eval.run_det) | Per-type recall/precision with Wilson CIs on DEV split. |
| S-116 | eval/results/det_test.md (E-DET TEST report) | `eval/results/det_test.md` | internal | This repo | internal | I | (not cited by name; produced by eval.run_det) | Per-type E-DET results on the held-out TEST split. |
| S-117 | eval/results/det_tune.md (E-DET tune-split report; untracked in git) | `eval/results/det_tune.md` | internal | This repo | internal | I | (not cited by name; produced by eval.run_det) | Tune-split results; PERSON 0.749 recall FAIL without NER. |
| S-118 | tests/ pytest suite (test_recognizers.py, test_gateway.py, test_core.py) | `tests/` | internal | This repo | internal | I | SECEVAL | Reproducible unit and gateway tests: 809 passed, 1 xfailed; 30 end-to-end. |

## Source quality warnings

Ordered by how much a design decision could lean on the affected claim.

### A. Sources whose claims conflict, or whose status changed

1. **Presidio ownership and packaging (ESU vs OSS).** The survey's heading and prose say "Microsoft Presidio" and describe the move as
   "transitioning", while `research/OSS_COMPONENTS.md` confirms it (repo banner, PyPI homepage) and adds that
   `mcr.microsoft.com/presidio-*` images are no longer updated (use `ghcr.io/data-privacy-stack/presidio-*`). The survey's URLs already point at
   `data-privacy-stack`; the stale path is `github.com/microsoft/presidio/issues?q=async` in VERIF. Star counts differ: survey ~10.9k, OSS notes ~11.1k
   (both rough, time-sensitive). Use the `data-privacy-stack` rows and PyPI JSON for versions. The old Presidio releases page showed a 2024 date against PyPI's 2026-07-22; PyPI is trusted.
2. **guardrails_pii: recommended but archived.** The survey (§4.5, Appendix A) presents `guardrails-ai/guardrails_pii` as a "useful example of a hybrid
   recognizer stack" and does not mention that it is archived. OSS notes quote "This repository is archived and no longer maintained" and mark it REJECT. Treat as pattern reference only.
3. **LiteLLM streaming leak: #41611, PR #41936, litellm-docs PR #1790.** ENGINEERING §3 lists them as `[V]`, but OSS notes say #41611 and the
   "full-stream buffer" fix could NOT be verified, and ADR-0008 says "cited by one research pass, not verified". #41600 is a feature request, not the leak.
   The verified streaming items are PRs #42351, #43345 and issue #42476, and those are search-summary only. Grade N for the first three; do not use them as FACT-P.
4. **Stale text inside INDUSTRY_PATTERNS.md (per-vendor notes vs comparison table).** The table was updated from VERIF, but the notes still say Cloudflare
   "SSE and tool-call coverage: UNVERIFIED" and cite docs issue #28325, and SAP "Entity list, streaming, latency: UNVERIFIED". VERIF (mirror-fetched) supersedes: DLP scans tool-call text in the JSON body;
   response scanning buffers; SAP streaming unmasking and tool-call handling are documented. ENGINEERING §3 likewise still says "Portkey and SAP AI Core streaming masking behaviour: not found".
5. **Cloudflare conflates two features in the old notes.** DLP (buffers only if response scanning is on; pass/flag/block) and Guardrails (REST endpoints log but do not enforce;
   gateway endpoints buffer and de-stream) are different features. The INDUSTRY note "Ignore in Guardrails" action belongs to Guardrails, not DLP. Mirror rows S-02 and S-03 are the authority.
   Caveat: the mirror is the docs source branch `production`, not the canonical rendered page (blocked), so wording could differ from the live site.
6. **Presidio async.** VERIF corrects OSS: write "no native async API found" (use a thread/process executor), not "True async: UNVERIFIED". Evidence is a PyPI description plus NeMo PR #2400, i.e. absence evidence.
7. **GLiNER model evidence is thin and partly contradictory.** Knowledgator F1 (75.50/76.84/80.99/83.25) is vendor-reported from model-card snippets with no identified benchmark; ADR-0005 relies on it and labels it vendor-reported.
   `urchade/gliner_multi_pii-v1` shows F1 ~0.38 in a third-party benchmark (arXiv 2605.09973), seen only through a search summary; its own-card F1 is unverified. `nvidia/gliner-PII` is not Apache-2.0 (NVIDIA Open Model License).
   Only the base knowledgator card's licence front-matter was seen; the rest is by family statement. GLiNER latency (125 ms per ~500 B) is one GitHub issue on an M-series CPU; the L4 figure (Superlinked) was not re-checked.
8. **Release dates unreliable.** Portkey v1.15.2 (Jan 2025 on a releases tab that may be stale), Envoy AI Gateway v1.1.0 (year given as 2025, suspect), and LiteLLM, cryptography and Presidio dates came through page summarisers. OSS notes: "Release dates from page summaries were sometimes inconsistent."
9. **Kong and Envoy "no OSS PII plugin" and Purview/Content Safety "does not mask"** are absence-of-evidence findings from secondary sources, not proof of absence. Google Model Armor "no tool-call field" is FACT-P about the proto only, not about behaviour (PROVENANCE rule 4).

### B. Sources not reached, or reached only second-hand

10. **All 20 grade-N rows** are named in the survey only: OWASP (2), NIST AI RMF GenAI profile, DPDP Act and Rules MeitY documents (4), Workday (2), Skyflow docs, Protecto home, Nightfall, Private AI,
    Azure confidential AI, the Portkey wiki, LiteLLM #41611 / #41936 / docs #1790, and two arXiv papers. Nothing from them may support a decision; several design statements lean on them by name (OWASP LLM06 in ADR-0009 and ROADMAP; DPDP in DESIGN, THREAT_MODEL, RISK_REGISTER, ADR-0003/0004).
11. **DPDP claims rest on third-party guides.** Timeline, Rule 7 and cross-border points come from Seclore, Cy5, dpdpa.com, MediaNama and a Bar & Bench opinion column. Penalty amounts were flagged `[?]` (one source said 200 crore; the author believes up to 250 crore for safeguard failure). S.8(2), S.8(5), S.16(2) and "no pseudonymisation carve-out" are `[K]`, not verified. R-13 and ADR-0003 depend on that reading; list it in OPEN_QUESTIONS.
12. **Blocked-host vendor pages (Cloudflare canonical, Salesforce, SAP help site, Google docs, AWS docs, Hugging Face, arXiv)** are search-summary only. Salesforce streaming behaviour remains UNVERIFIED (no snippet mentions it); the 65,536-token masking cap is a single snippet.
13. **No vendor publishes PII-masking latency.** The only figures are the Medium anecdote for Model Armor, TrueFoundry's generic ranges and Cloudflare's qualitative statement. Do not cite them as vendor numbers.
14. **Several Hugging Face and GitHub pages are S only** because summarisers, not raw files, were used. Only pages with quoted banner/text in the notes (Presidio repo and transition doc, llm-guard, guardrails_pii) are graded P among GitHub HTML pages; carnaval, Anonproxy, llm-proxy-pii-rust and truefoundry details are page-derived but unquoted, so S.
15. **The ESU survey (S-01) is itself secondary.** It was researched from public web sources, contains time-sensitive numbers (stars, activity), and DESIGN cites it under a different filename (`ESU_Privacy_Preserving_LLM_Security_Gateway_Report.md`) than the repo copy.

### C. Vendor or project self-description only (CLAIM-V; cannot support a decision alone)

16. Skyflow (docs and product page), Protecto (home and blog), Nightfall, Private AI, Workday (page and datasheet), Lakera Guard docs, Kong plugin docs, Portkey repo and wiki ("50+ guardrails", streaming support),
    carnaval ("enterprise production use" claim unverified; 1 star), truefoundry template, Anonproxy, llm-proxy-pii-rust (self-reported ~20 ms and ~4.7 s figures),
    Knowledgator and Fastino model cards and Fastino blog (self-reported F1, licence, parameters), Salesforce developer blog, Microsoft PII Shield blog, TrueFoundry blog, and the Seclore and Cy5 vendor compliance guides. That is 25 rows.
17. **Uber blog and InfoQ** are one claim, not two: the InfoQ article summarises the Uber post. Uber reports latency, quality loss and cache/RAG breakage without numbers (none retrieved), yet ADR-0002 leans on it for "inconsistent placeholders are a documented failure". Treat as CLAIM-S with a risk note.
18. **Salesforce, AWS and Uber findings used in SECURITY_EVALUATION §7** carry "S" there as well; the "we exceed the norm" comparisons inherit that grade.

### D. Internal evidence caveats (S-110 to S-118)

19. TEST split is spent and contaminated (SECEVAL §2): test template names were printed via MANIFEST.json, and failures were analysed after the run. Any release informed by that needs a freshly generated split.
    TEST_LOCK.json recorded an empty corpus hash (runner key-matching bug); the hash was verified by hand.
20. Numbers are on synthetic data only and are an upper bound on real-world recall (RISK_REGISTER R-03). E-UTIL was never run, so no utility measurement exists. `det_tune.md` is untracked in git and `det_dev.md`/`det_test.md` are not cited by name in any other file.
