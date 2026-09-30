# Privacy-Preserving LLM Security Gateway for ESU HRIS & ERP Workloads

**Prepared for:** Enterprise Solutions Unit (ESU)  
**Focus:** HRIS, ERP, Workday/UKG-style enterprise data, RAG and agentic AI  
**Report date:** 30 September 2026  
**Research scope:** Enterprise products, open-source implementations, algorithms, architecture patterns, and an implementable ESU reference design

---

## 1. Executive Summary

Enterprise AI creates a specific problem for ESU-style workloads: the useful input often contains exactly the information that an external LLM should not receive in raw form.

Examples include:

- employee names, IDs and contact details
- compensation and payroll information
- bank/account information
- dates of birth and government identifiers
- work location and manager hierarchy
- performance/appraisal information
- absence/leave information
- HR cases and employee relations data
- customer/vendor identities and ERP records
- internal integration payloads and credentials
- confidential business rules and documents

The solution should **not** be designed as a simple "PII remover before the LLM." Mature enterprise products use multiple detection and enforcement layers, and the strongest privacy architectures separate four concerns:

1. **Detection** — identify sensitive content using deterministic, statistical and contextual methods.
2. **Policy** — decide whether the content should be allowed, transformed, blocked, computed locally, or routed through a more trusted execution environment.
3. **Transformation** — replace sensitive values with redactions, pseudonyms or cryptographic tokens while preserving the semantic relationships needed by the task.
4. **Controlled reconstruction** — inspect the LLM response and, only after policy checks, restore authorized original values.

The enterprise pattern is therefore:

```text
User / Application
        |
        v
+-----------------------------+
| ESU Privacy Gateway         |
|                             |
| Normalize                   |
| Regex / checksum            |
| Exact-data matching         |
| NER / ML                    |
| Context scoring             |
| Secret detection            |
| Entity resolution           |
| Policy engine               |
| Tokenization / masking      |
| Encrypted token vault       |
+-------------+---------------+
              |
              | protected prompt only
              v
        External LLM
              |
              v
+-------------+---------------+
| Response Security Gateway   |
| PII / secret scan           |
| Token validation            |
| Policy enforcement          |
| Controlled re-identification|
+-------------+---------------+
              |
              v
       User / Application
```

This report concludes that ESU should **reuse proven open-source components where they are strong and build the differentiating policy/orchestration layer in-house** rather than attempting to create a new PII-recognition library from scratch.

A practical open-source foundation is:

- **Microsoft Presidio / Data Privacy Stack Presidio** for contextual PII detection and anonymization.
- **GLiNER or another local NER model** for flexible entity recognition and domain-specific entities.
- **Regex + checksum + rule recognizers** for structured identifiers.
- **HMAC-SHA-256 / deterministic encryption / AES-SIV-style tokenization** for stable pseudonyms and referential integrity.
- **An encrypted vault or token map** kept inside the ESU-controlled security boundary.
- **OPA or an equivalent policy engine** for deterministic allow/block/transform/routing decisions.
- **An OpenAI-compatible gateway pattern** inspired by projects such as Portkey.
- **A second response-side inspection stage** to catch output leakage and validate token restoration.

The most important design insight is that the gateway should be **task-aware**. It should ask: *Does the model actually need the raw private value?* If not, tokenize or generalize it. If the task requires an exact sensitive value, do not try to fool the model with masking; instead perform the sensitive operation locally, use an authorized internal service, or use a confidential-computing/private-inference path.

---

## 2. Problem Definition for ESU

### 2.1 The ESU-specific problem

ESU workloads can involve enterprise platforms such as HRIS and ERP systems, where AI use cases are highly useful but the underlying records can contain personal, financial, operational and confidential information.

A naive design is:

```text
Workday / UKG / ERP
        |
        v
     Prompt
        |
        v
   Public LLM API
```

The problem is not only "will the provider train on my data?" The broader question is **how much sensitive data is exposed to the model-provider boundary at all**.

A stronger design is:

```text
Workday / UKG / ERP
        |
        v
 ESU Privacy Gateway
        |
  protected representation
        |
        v
    External LLM
```

The gateway becomes the trust boundary.

### 2.2 Why HRIS and ERP are unusually difficult

HRIS/ERP data is often highly relational. A worker is not merely a name; the record may include:

```text
Worker
 ├─ Worker ID
 ├─ Name
 ├─ Email
 ├─ Manager
 ├─ Organization
 ├─ Location
 ├─ Job Profile
 ├─ Grade
 ├─ Compensation
 ├─ Benefits
 ├─ Absence
 ├─ Performance
 └─ Business Process history
```

The privacy risk therefore comes from both **individual fields** and **combinations of fields**.

For example:

```text
"John Doe"
```

is one sensitive entity.

But this combination may identify the same person even after the name is removed:

```text
Male
42
VP Engineering
Mumbai
Acme Corp
L6
₹38 lakh
```

This is the quasi-identifier / re-identification problem. The gateway therefore needs context and relationship awareness, not only string matching.

---

## 3. Current Enterprise Landscape

### 3.1 Google Sensitive Data Protection

Google's Sensitive Data Protection provides one of the clearest reference architectures for enterprise de-identification. Its documentation describes detection using built-in/custom information-type detectors and supports several cryptographic pseudonymization techniques.

The documented reversible/irreversible techniques include:

- deterministic encryption using AES-SIV
- format-preserving encryption using FPE-FFX
- cryptographic hashing using HMAC-SHA-256

Google explicitly documents that deterministic tokenization can preserve referential integrity, meaning the same source value can produce the same token, and that authorized systems can re-identify values with the appropriate cryptographic key. Google also recommends AES-SIV as its preferred reversible method among its supported approaches.  
Source: https://docs.cloud.google.com/sensitive-data-protection/docs/pseudonymization

This is the strongest reference for the **cryptographic transformation** portion of the ESU architecture.

### 3.2 Cloudflare AI Gateway DLP

Cloudflare's current AI Gateway DLP scans both incoming AI prompts and outgoing AI responses. Policies can specify how detected sensitive data is handled. Cloudflare describes this as integrating its existing DLP detection engines into AI traffic inspection and applying protection without requiring application changes.  
Source: https://developers.cloudflare.com/ai-gateway/features/dlp/

This is the strongest reference for the **inline AI gateway / request-response enforcement** portion.

Important distinction: Cloudflare's documented AI Gateway DLP workflow is primarily about detection and policy enforcement; it should not be treated as evidence that all Cloudflare DLP flows use reversible tokenization of every entity.

### 3.3 AWS Bedrock Guardrails

AWS documents a sensitive-information filter that is context-dependent and ML-based, supplemented by configurable regular expressions. It can block or anonymize detected information. AWS also explicitly documents a limitation: certain tool-use fields/results are not covered by the sensitive-information filter.  
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html

This is important for ESU because agentic AI can move sensitive information through **tool calls and tool results**, not just prompts.

### 3.4 Microsoft Presidio

Presidio is an open-source framework for PII de-identification across text and images. Its project documentation describes predefined/custom recognizers using Named Entity Recognition, regular expressions, rule-based logic, checksum validation and contextual information. It also supports multiple NLP engines and deployment styles.  
Source: https://github.com/data-privacy-stack/presidio

Presidio is now transitioning from Microsoft ownership to an independent community-owned project under the `data-privacy-stack` organization and remains MIT licensed.  
Source: https://github.com/data-privacy-stack/presidio/blob/main/docs/project_transition.md

This is the strongest open-source foundation for the **detection/anonymization engine**.

### 3.5 Skyflow

Skyflow's architecture is a useful reference for a privacy-vault model around LLMs: sensitive values remain in a controlled vault, tokens are used downstream, and authorized workflows can re-identify values.  
Reference: https://docs.skyflow.dev/docs/fundamentals/patterns/llm-privacy

This is the reference architecture to study for the **privacy vault / token lifecycle / authorization** layer.

### 3.6 Protecto

Protecto positions its platform as an AI data control plane spanning LLMs, agents and MCP-style workflows, with data discovery, policy enforcement and tokenization/de-identification patterns.  
Reference: https://www.protecto.ai/

For ESU, the conceptual value of Protecto is that it treats privacy as a **data control plane around AI**, not merely as a prompt preprocessor.

### 3.7 Nightfall

Nightfall's developer tooling is a useful enterprise DLP reference because it combines ML detectors with regex and dictionary approaches and emphasizes detector configuration, confidence and contextual rules.  
Reference: https://help.nightfall.ai/developer-api/introduction/overview

The key lesson is that enterprise DLP is usually **detector orchestration**, not one model.

### 3.8 Private AI

Private AI provides enterprise de-identification and positions its technology for text, documents, images and audio, including use cases where sensitive data must be sanitized before reaching third-party model providers.  
Reference: https://www.private-ai.com/en/

This supports a broader design principle: eventually the ESU gateway should be able to sanitize more than plain prompt text.

---

## 4. Open-Source Projects Worth Studying

This section deliberately separates **mature foundations** from **newer projects whose architecture is interesting but whose maturity evidence is much weaker**.

### 4.1 Microsoft Presidio / Data Privacy Stack Presidio — primary foundation

Repository: https://github.com/data-privacy-stack/presidio

Current project signals observed during research:

- open source, MIT licensed
- approximately 10.9k GitHub stars at research time
- approximately 1.3k forks at research time
- text and image de-identification
- regex/rule/checksum/NER/contextual recognizers
- custom recognizer support
- pluggable NLP engines

The repository explicitly warns that automated detection cannot guarantee that all sensitive information is found, which is exactly why ESU should not rely on a single PII detector.  
Source: https://github.com/data-privacy-stack/presidio

**Use in ESU:** detection + baseline anonymization, not the complete gateway.

### 4.2 Presidio Research

Repository: https://github.com/data-privacy-stack/presidio-research

Purpose: evaluate recognizer precision/recall and identify detection errors.

**Use in ESU:** build the benchmark/evaluation discipline into the project instead of demoing on five handcrafted examples.

### 4.3 Portkey AI Gateway — gateway architecture reference

Repository: https://github.com/Portkey-AI/gateway

Current repository signals observed during research:

- MIT licensed
- approximately 13.1k stars at research time
- approximately 1.3k forks
- gateway middleware architecture
- provider routing
- guardrail plugins
- request/response handlers
- access control concepts
- logging/analytics
- enterprise deployment patterns

Portkey's repository and wiki explicitly describe integrated guardrails and request/response transformations at the gateway layer.  
Sources:
- https://github.com/Portkey-AI/gateway
- https://github.com/Portkey-AI/gateway/wiki/Guardrails-on-the-Gateway-Framework

**Use in ESU:** learn how to make the privacy layer an API gateway rather than a Python utility library.

### 4.4 TrueFoundry Custom Guardrails

Repository: https://github.com/truefoundry/custom-guardrails-template

The repository provides a FastAPI guardrail server with Presidio-based PII redaction, local model-based validation, typed request/response contracts and configurable Indian/US/UK/Australian recognizers. It also demonstrates how guardrails can be integrated with gateway policy contracts.  
Source: https://github.com/truefoundry/custom-guardrails-template

**Use in ESU:** reference for a deployable guardrail microservice and regional/entity-specific policy configuration.

### 4.5 Guardrails AI PII

Repository: https://github.com/guardrails-ai/guardrails_pii

The validator currently combines Presidio and GLiNER, supporting configurable entity types and local operation.  
Source: https://github.com/guardrails-ai/guardrails_pii

**Use in ESU:** useful example of a hybrid recognizer stack.

### 4.6 Carnaval — direct conceptual match, low maturity

Repository: https://github.com/carnaval-ai/carnaval

The project describes a seven-stage local-first reversible anonymization pipeline using regex, deny-lists, GLiNER and an AES-256-GCM encrypted vault, followed by response reinjection. It also claims multi-occurrence consistency. The repository states it is beta/stable-beta and currently very small in GitHub adoption.  
Source: https://github.com/carnaval-ai/carnaval

**Use in ESU:** architectural inspiration for the exact pre-LLM -> vault -> re-injection pattern.

**Do not use its small project size as evidence that the architecture itself is enterprise-proven.**

### 4.7 LLM Guard — study historically, do not use as the strategic foundation

Repository: https://github.com/protectai/llm-guard

This project previously provided a broad LLM security toolkit, including sanitization, PII anonymization and prompt-security scanners. However, the repository was archived by the owner on **9 July 2026** and is read-only.  
Source: https://github.com/protectai/llm-guard

**Use in ESU:** inspect ideas and scanners where helpful; do not make an archived repository a core production dependency.

---

## 5. Algorithm Stack

The correct answer to "pure NLP or complex NLP?" is:

> **Neither alone. Use a detector cascade and confidence/policy fusion.**

### 5.1 Layer 0 — normalization

Before detection:

- Unicode normalization
- whitespace normalization
- email/URL normalization where safe
- structured JSON/XML parsing
- OCR/ASR preprocessing for multimodal content
- canonicalization of common obfuscations

Reason: attackers and accidental formatting frequently break naive recognizers.

### 5.2 Layer 1 — deterministic recognizers

Use cheap, explainable detectors first.

Examples:

- regex
- checksums
- Luhn validation
- country-specific ID rules
- phone number syntax
- email syntax
- IP/URL parsing
- JWT/API-key patterns
- database identifier rules
- internal ID formats

For ESU, this layer should include domain-specific recognizers for identifiers used in HRIS/ERP integrations.

### 5.3 Layer 2 — exact-data matching

Use a protected reference set for known sensitive values.

Recommended pattern:

```text
known sensitive value
        |
        v
HMAC-SHA-256(secret, canonical_value)
        |
        v
stable lookup key
```

This can support matching without exposing a plaintext lookup corpus to a remote service.

Candidate use cases:

- employee IDs
- customer IDs
- vendor IDs
- contract IDs
- internal project identifiers
- known customer names
- controlled business terms

### 5.4 Layer 3 — NER / transformer recognition

Use a local NER model to detect contextual entities that do not have clean syntax.

Potential technologies:

- Presidio's NLP engine integrations
- GLiNER
- spaCy / Stanza where appropriate
- task-specific transformer NER models

The purpose is not to run a huge LLM. The purpose is to recognize semantic entity types locally.

### 5.5 Layer 4 — contextual scoring

A candidate should be evaluated using nearby context.

Example:

```text
"Account: 123456789"
```

versus:

```text
"Employee bank account: 123456789"
```

The raw pattern may be identical; the risk is not.

Contextual scoring should consider:

- surrounding words
- field names
- document type
- source system
- application identity
- user role
- task type
- previously resolved entity type
- co-occurring entities

### 5.6 Layer 5 — entity resolution and referential integrity

The gateway should create canonical entity identities for the scope in which consistency is required.

Example:

```text
John Doe -> P_001
John Doe -> P_001
john.doe@company.com -> E_001
```

The model sees stable references rather than unrelated placeholders.

### 5.7 Layer 6 — risk fusion

Instead of trusting one score, calculate a combined decision:

```text
risk = f(
    regex_confidence,
    checksum_valid,
    exact_match,
    NER_score,
    contextual_score,
    source_sensitivity,
    entity_combination,
    user/task policy
)
```

A simple initial implementation can be a weighted rules engine. A learned fusion model can be added later.

---

## 6. Transformation Strategies

Different data requires different handling.

### 6.1 Redaction

```text
John Doe <john@company.com>
        |
        v
[PERSON] <[EMAIL]>
```

Use when the exact identity is irrelevant.

Advantages:
- simple
- strong privacy boundary
- easy to reason about

Weakness:
- loses semantics and relationship identity

### 6.2 Stable pseudonyms

```text
John Doe -> PERSON_001
```

All occurrences of the same entity map to the same protected identifier.

Use for:

- summarization
- classification
- document extraction
- conversation analysis
- relationship analysis

### 6.3 Deterministic cryptographic tokenization

Conceptual flow:

```text
plaintext
   |
   v
cryptographic transform + key
   |
   v
stable token
```

Google documents AES-SIV deterministic encryption as a reversible tokenization method that preserves referential integrity, and HMAC-SHA-256 as a one-way method.  
Source: https://docs.cloud.google.com/sensitive-data-protection/docs/pseudonymization

Use this when stable correlation is required and the raw value must not enter the model.

### 6.4 Format-preserving encryption

Use when a downstream application strictly needs the same character set/shape.

Example:

```text
4111-1111-1111-1111
        |
        v
5839-7421-0953-6184
```

Google documents FPE-FFX support but also notes its security trade-offs relative to AES-SIV-style deterministic encryption. It should therefore be used only where format preservation is an actual requirement.  
Source: https://docs.cloud.google.com/sensitive-data-protection/docs/pseudonymization

### 6.5 Generalization/bucketing

For analytics tasks:

```text
₹38,47,112
    |
    v
₹35L-₹40L
```

or:

```text
47 years
    |
    v
40-49
```

Use when the model needs distributional/contextual information but not the exact value.

### 6.6 Local computation instead of masking

This is critical.

Suppose the user asks:

```text
"What is John's exact salary?"
```

Tokenizing the salary and expecting an LLM to infer the answer is logically impossible.

The correct flow is:

```text
User request
    |
    v
Policy engine
    |
    v
Authorized internal lookup
    |
    v
Exact result
    |
    v
LLM receives only the minimum result needed
```

Do not create a false impression that anonymization can magically preserve information that the model never received.

### 6.7 Confidential computing/private inference

For workloads where exact private values truly must participate in model inference, consider:

- local LLM inference
- confidential VMs / TEEs
- confidential inference architectures
- secure internal model endpoints

Microsoft describes confidential AI as hardware-based protection for data/models during use and identifies confidential inferencing as a way to protect inference requests/responses within a trusted execution environment.  
Source: https://learn.microsoft.com/en-us/azure/confidential-computing/confidential-ai

---

## 7. HRIS / ERP Scenario Matrix

| Scenario | Raw sensitive data required by LLM? | Preferred control | Example |
|---|---:|---|---|
| Summarize employee case | No | Tokenize names/IDs | `EMP_001` |
| Draft employee email | Usually no | Stable tokens | `PERSON_001` |
| Classify HR ticket | Usually no | PII detection + tokenization | preserve issue, hide identity |
| Compare two employees | No raw identity needed | Consistent pseudonyms | `PERSON_001` vs `PERSON_002` |
| Find worker by employee ID | Usually no | Local lookup | internal API resolves locally |
| State exact salary | Exact value needed | Local computation/authorized retrieval | LLM receives derived result |
| Analyze salary bands | Exact values usually unnecessary | Bucketing/generalization | `₹30L-₹35L` |
| Payroll summary | Usually no | Structured aggregation + masking | summary only |
| Workday integration troubleshooting | Often no | Secret scanner + tokenization | remove auth headers/tokens |
| Analyze XML payload | Often no | Field-level transformation | preserve schema, tokenize values |
| RAG over HR documents | Sensitive docs involved | Pre-ingest sanitization + access policy | protected chunks |
| Agent tool call | May include sensitive parameters | Tool gateway + allow-list | policy before tool execution |
| LLM response | Can re-leak PII | Response DLP | block/re-mask/reidentify |
| Executive dashboard | Often aggregate | Generalization + minimum necessary data | role-based output |
| Employee profile generation | High sensitivity | Strong pseudonymization or private model | no raw PII to external model |

---

## 8. Recommended ESU Architecture

### 8.1 Logical architecture

```text
                               ESU TRUST BOUNDARY

 +-------------+       +------------------------------------------+
 | Workday     |       |           ESU PRIVACY GATEWAY           |
 | UKG/Kronos  | ----> |                                          |
 | ERP/HRIS     |       |  [1] Normalizer                         |
 +-------------+       |        |                                 |
                         |        v                                 |
 +-------------+       |  [2] Detector Cascade                  |
 | Application | ----> |    regex / checksum                     |
 | / Portal    |       |    exact match / HMAC                   |
 +-------------+       |    NER / GLiNER                         |
                         |    contextual classifier                |
 +-------------+       |    secret scanner                       |
 | RAG / Docs  | ----> |        |                                 |
 +-------------+       |        v                                 |
                         |  [3] Entity Resolver                    |
                         |        |                                 |
                         |        v                                 |
                         |  [4] Policy Engine                      |
                         |    allow / tokenize / block             |
                         |    generalize / local compute / TEE     |
                         |        |                                 |
                         |        v                                 |
                         |  [5] Transformer                        |
                         |    pseudonyms / tokens / masks           |
                         |        |                                 |
                         |        v                                 |
                         |  [6] Encrypted Token Vault              |
                         +-------------------+----------------------+
                                             |
                                             | protected prompt
                                             v
                                   +---------------------+
                                   | External / Internal |
                                   |       LLM            |
                                   +----------+----------+
                                              |
                                              | response
                                              v
                         +-------------------+----------------------+
                         |       RESPONSE GATE / DLP              |
                         |                                        |
                         | PII scan -> token integrity -> policy |
                         |       -> authorized reconstruction     |
                         +-------------------+--------------------+
                                             |
                                             v
                                       Application/User
```

### 8.2 Enforcement points

The gateway should enforce privacy at four points:

```text
1. User/input boundary
2. RAG/document ingestion boundary
3. Tool/agent execution boundary
4. LLM response boundary
```

This is preferable to protecting only the initial prompt.

AWS explicitly documents tool-use fields/results that its sensitive-information filter does not inspect, demonstrating why tool boundaries require separate controls.  
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html

---

## 9. Policy Engine Design

A deterministic policy engine should decide what happens to each detected entity.

Example policy model:

```yaml
policies:
  - entity: CREDIT_CARD
    action: BLOCK

  - entity: API_KEY
    action: BLOCK

  - entity: PERSON
    context: hr_case
    action: TOKENIZE

  - entity: EMAIL
    context: email_drafting
    action: TOKENIZE

  - entity: SALARY
    context: salary_band_analysis
    action: GENERALIZE

  - entity: SALARY
    context: exact_lookup
    action: LOCAL_COMPUTE

  - entity: EMPLOYEE_ID
    context: worker_lookup
    action: LOCAL_LOOKUP

  - source: HRIS
    classification: RESTRICTED
    action: TOKENIZE
```

Policy decisions should be based on at least:

- entity type
- source system
- user identity/role
- application
- task type
- data classification
- destination model/provider
- geographic/compliance policy
- whether reversibility is required
- whether the model actually needs the value

The gateway should support **fail-closed** behavior for high-risk policies.

---

## 10. Token Vault Design

### 10.1 What should be stored

For reversible workflows, maintain a secure mapping such as:

```text
scope_id
entity_type
protected_token
ciphertext_of_original
key_version
created_at
expiry
request_id
policy_id
```

### 10.2 What should NOT be done

Do not store plaintext mappings in:

- application logs
- debugging logs
- LLM observability platforms
- analytics events
- exception traces
- client-visible telemetry

### 10.3 Key management

Prefer an established KMS/HSM-backed key hierarchy in enterprise deployment.

Conceptually:

```text
KMS/HSM root key
      |
      +-- vault encryption key
      |
      +-- tokenization key
      |
      +-- signing/integrity key
```

Google documents using Cloud KMS-wrapped keys and states that directly supplied raw cryptographic keys are not recommended.  
Source: https://docs.cloud.google.com/sensitive-data-protection/docs/pseudonymization

### 10.4 Scope of referential integrity

Do not automatically make a token globally stable forever.

A more secure architecture can have scopes such as:

```text
request scope
conversation scope
application scope
customer scope
tenant scope
```

This reduces cross-context linkability.

Google documents the idea of using a context tweak to scope deterministic tokens to a particular context, which can prevent the same source value from being correlated across unrelated contexts.  
Source: https://docs.cloud.google.com/sensitive-data-protection/docs/pseudonymization

---

## 11. Response-Side Protection

The response must be treated as untrusted content.

Example:

```text
LLM output
   |
   +--> Is raw PII present?
   |
   +--> Are protected tokens intact?
   |
   +--> Did the model invent protected identities?
   |
   +--> Did it transform a token unexpectedly?
   |
   +--> Does output violate policy?
```

Only after passing validation should the system consider re-identification.

Example:

```text
LLM:
"PERSON_001 has completed the appraisal."

Gateway:
PERSON_001 -> John Doe

Final:
"John Doe has completed the appraisal."
```

But if the model outputs:

```text
"John Doe has completed the appraisal."
```

without a valid protected-token reference, the system should treat that as a possible leakage event rather than assuming it is safe to return.

Cloudflare's AI Gateway DLP and AWS Guardrails both illustrate why request and response inspection should be first-class controls.  
Sources:
- https://developers.cloudflare.com/ai-gateway/features/dlp/
- https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html

---

## 12. RAG Security Model

RAG substantially expands the attack surface.

A secure architecture should consider:

```text
Documents
  |
  v
ingestion scanner
  |
  +--> PII / secrets / confidential data detection
  |
  +--> classification
  |
  +--> sanitization / tokenization
  |
  +--> ACL metadata
  |
  v
Embedding / index
  |
  v
retrieval
  |
  v
privacy-aware context assembly
  |
  v
LLM
```

Important: hiding PII in the user prompt is not enough if the retriever can return raw HRIS or ERP documents into the model context.

Similarly, sending the raw document to an embedding provider is not a privacy solution. A vector representation should not automatically be assumed to be anonymous.

For ESU, document/RAG privacy should therefore be designed as **pre-ingestion protection + retrieval-time authorization + prompt-time transformation**.

---

## 13. Agent / Tool Security

Enterprise AI agents create an additional boundary:

```text
LLM
 |
 +--> Workday API
 +--> UKG API
 +--> ERP API
 +--> SQL
 +--> ServiceNow
 +--> File storage
```

A privacy gateway should not allow the model to directly call unrestricted enterprise APIs.

Instead:

```text
LLM
 |
 v
Tool Gateway
 |
 v
Policy check
 |
 +--> parameter validation
 +--> identity/role check
 +--> data minimization
 +--> audit
 +--> rate limit
 |
 v
Enterprise API
```

A tool request such as:

```json
{
  "employee_id": "EMP_001",
  "action": "get_salary"
}
```

should be resolved through an authorized internal service, not by giving the LLM raw credentials to Workday/ERP.

This aligns with OWASP's 2025 risk categories around Sensitive Information Disclosure, Improper Output Handling and Excessive Agency.  
Sources:
- https://genai.owasp.org/llm-top-10/
- https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf

---

## 14. Security Threat Model

The ESU gateway should explicitly defend against at least these failure modes:

### T1 — accidental PII transmission

Application sends raw HRIS/ERP data to external LLM.

**Control:** pre-LLM detector + transformation.

### T2 — detector miss

PII exists but no recognizer finds it.

**Control:** detector cascade + evaluation datasets + human review for high-risk workloads.

### T3 — false positive

A non-sensitive value is incorrectly masked.

**Control:** contextual scoring + policy thresholds + explainability.

### T4 — token linkability

Stable pseudonyms allow unintended cross-request correlation.

**Control:** scope-limited tokenization/context tweak.

### T5 — re-identification abuse

An unauthorized service or user requests token restoration.

**Control:** vault ACLs + KMS + audit + short-lived scope.

### T6 — prompt/response leakage

Model reconstructs or repeats raw PII.

**Control:** response-side DLP and leakage detection.

### T7 — tool leakage

LLM passes PII to an enterprise or third-party tool.

**Control:** tool gateway with policy enforcement.

### T8 — RAG leakage

Retriever inserts raw restricted data into context.

**Control:** retrieval ACLs + sanitization + source classification.

### T9 — logging leakage

Raw data appears in gateway telemetry.

**Control:** structured-safe logging and PII-free observability.

### T10 — malicious manipulation of recognizers

Users intentionally alter text to evade detectors.

**Control:** canonicalization, adversarial test suites, multiple recognizers.

### T11 — supply-chain compromise

An open-source model/library is compromised.

**Control:** pinned versions, SBOM, vulnerability scanning, signed artifacts where available.

### T12 — excessive agency

The model performs consequential HR/ERP actions without sufficient authorization.

**Control:** explicit tool policies, least privilege, approval workflows.

---

## 15. Open-Source Reference Stack for ESU

A practical initial stack:

```text
API Gateway / Service
    FastAPI or equivalent

Detection
    Microsoft/Data Privacy Stack Presidio
    Regex / checksum recognizers
    GLiNER local model
    Custom ESU recognizers

Exact match
    HMAC-SHA-256
    secure indexed lookup

Policy
    OPA / Rego or typed internal policy engine

Transformation
    deterministic cryptographic tokens
    reversible encryption only where authorized
    redaction / masking / generalization

Vault
    PostgreSQL or dedicated vault service
    encrypted payloads
    KMS/HSM-backed keys in enterprise deployment

RAG
    local embeddings/index where policy requires
    ACL-aware retriever

LLM Gateway
    OpenAI-compatible API
    Portkey-style middleware architecture as reference

Observability
    OpenTelemetry-compatible telemetry
    never log raw protected values

Deployment
    Docker/Kubernetes
    internal network
    service-to-service mTLS where available
```

### Important dependency principle

Do not let the architecture become dependent on one ML package.

The detection interfaces should be pluggable:

```text
Detector
  |
  +-- RegexDetector
  +-- ChecksumDetector
  +-- ExactMatchDetector
  +-- PresidioDetector
  +-- GLiNERDetector
  +-- SecretDetector
  +-- CustomHRISDetector
```

Then detector implementations can change independently of the policy engine and vault.

---

## 16. Why a Pure NLP Solution Is the Wrong Architecture

A pure NLP model fails on structured secrets.

```text
Credit card number
Aadhaar
PAN
JWT
API key
Employee ID
```

These are often better detected with syntax/checksum/pattern logic.

A pure regex solution fails on contextual entities.

```text
"our senior developer from the Pune team"
```

The person may be inferable from context even without a clean name pattern.

A large LLM as the detector creates another problem:

```text
raw PII
   |
   v
external LLM detector
```

The privacy objective has already failed.

Therefore the right model is:

```text
Deterministic + statistical + contextual + policy
```

---

## 17. Evaluation Framework

This project should not be judged by a demo that masks "John Doe" correctly.

Build an evaluation corpus that mirrors ESU data classes using synthetic data first.

### 17.1 Detection metrics

Measure separately:

- precision
- recall
- F1
- false-positive rate
- false-negative rate
- entity boundary accuracy
- per-entity-type performance

### 17.2 Privacy metrics

Measure:

- raw PII transmission rate
- secret transmission rate
- re-identification success under attacker assumptions
- token collision rate
- cross-scope linkability
- unauthorized deanonymization attempts

### 17.3 Utility metrics

Measure:

- task completion rate
- answer correctness
- structured extraction accuracy
- summary similarity/quality
- classification accuracy
- tool-call correctness

### 17.4 Performance metrics

Measure:

- P50/P95/P99 gateway latency
- throughput
- CPU/RAM utilization
- model inference latency
- vault lookup latency
- response buffering cost

### 17.5 Security test categories

Create tests for:

```text
exact PII
near-miss PII
misspelled names
obfuscated email
Unicode variants
OCR errors
mixed languages
nested JSON/XML
long documents
code + secrets
RAG retrieval leakage
tool parameter leakage
model output leakage
prompt injection around PII
cross-tenant token collisions
```

Presidio's research/evaluation project is a useful model for treating detector quality as a measurable engineering problem.  
Reference: https://github.com/data-privacy-stack/presidio-research

---

## 18. Proposed ESU MVP

The MVP should be narrow enough to deploy, but broad enough to demonstrate enterprise value.

### Phase 1 — text gateway

Support:

```text
OpenAI-compatible API
      |
      v
PII detector
      |
      v
policy
      |
      v
tokenize
      |
      v
LLM
      |
      v
restore / block
```

Entity set:

- person
- email
- phone
- address
- employee ID
- government ID patterns relevant to India
- bank/account data
- salary/compensation
- dates of birth
- secrets/API keys
- internal project/customer identifiers

### Phase 2 — contextual intelligence

Add:

- GLiNER/local NER
- context scoring
- entity resolution
- detector arbitration
- explainable risk score

### Phase 3 — cryptographic vault

Add:

- deterministic tokenization
- encrypted mapping store
- scoped tokens
- key rotation
- authorized re-identification
- audit trail

### Phase 4 — enterprise sources

Add adapters for:

- Workday
- UKG/Kronos
- ERP APIs
- ServiceNow/ticketing
- CSV/XML/JSON
- document repositories

Do this with generic connector interfaces rather than hard-coding one vendor.

### Phase 5 — RAG and agents

Add:

- pre-index sanitization
- retrieval ACLs
- tool gateway
- tool parameter inspection
- tool output inspection
- agent traces

### Phase 6 — governance console

Provide a web UI showing:

```text
Requests scanned
PII events
Entities detected
Transformations
Blocked requests
Model/provider used
Policy triggered
Re-identifications
Detection confidence
False-positive overrides
Latency
```

Do not show raw PII by default in the console.

---

## 19. Example End-to-End Flow

### Input

```text
Draft an email to Rahul Sharma at rahul.sharma@company.com
saying that his salary revision from ₹18,40,000 to ₹20,10,000
will be effective from 1 October.
```

### Detection

```text
Rahul Sharma          -> PERSON
rahul.sharma@...      -> EMAIL
₹18,40,000            -> COMPENSATION
₹20,10,000            -> COMPENSATION
1 October             -> DATE
```

### Policy

Suppose the policy says:

```text
PERSON         -> TOKENIZE
EMAIL          -> TOKENIZE
COMPENSATION   -> TOKENIZE
DATE           -> preserve
```

### Protected prompt

```text
Draft an email to PERSON_001 at EMAIL_001 saying that his
salary revision from COMP_001 to COMP_002 will be effective
from 1 October.
```

### LLM response

```text
Subject: Salary Revision Update

Hi PERSON_001,

Your salary revision from COMP_001 to COMP_002 will be
 effective from 1 October.
```

### Response validation

All protected tokens are valid and policy permits restoration.

### Re-identification

```text
PERSON_001 -> Rahul Sharma
EMAIL_001  -> rahul.sharma@company.com
COMP_001   -> ₹18,40,000
COMP_002   -> ₹20,10,000
```

### Final response

The user receives the intended business output while the external LLM saw only the protected representation.

This is the central proof-of-concept that ESU should demonstrate.

---

## 20. Hard Technical Limitations

### 20.1 Perfect detection is impossible

Presidio explicitly warns that automated detection cannot guarantee complete coverage.  
Source: https://github.com/data-privacy-stack/presidio

Therefore the product should communicate **risk reduction**, not absolute anonymity.

### 20.2 Pseudonymization is not anonymization in the strictest sense

A deterministic token can remain linkable.

If:

```text
John Doe -> TOKEN_A
```

appears in thousands of prompts, an observer can learn relationships around TOKEN_A.

The gateway must therefore control token scope and lifetime.

### 20.3 Utility/privacy trade-off

If the LLM cannot see a private fact, it cannot reason over that exact private fact.

This must be handled by routing or local computation, not by pretending masking preserves information.

### 20.4 Re-identification itself is sensitive

A system that can restore PII is effectively a privileged decryption service.

It therefore requires:

- strong authorization
- auditability
- key management
- data retention controls
- explicit purpose binding

### 20.5 LLM-generated output can contain new sensitive content

The response scanner must detect both:

1. sensitive information copied from input/context, and
2. sensitive information newly generated by the model.

---

## 21. Governance / Compliance Context for India

For an India-based ESU deployment, the design should be mapped to the organization's legal and contractual obligations rather than treating the technical gateway as a substitute for governance.

India's Digital Personal Data Protection Rules, 2025 were notified on 13 November 2025. The notification establishes a phased commencement schedule, with different rules becoming effective at different intervals.  
Sources:
- https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa
- https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf

The Digital Personal Data Protection Act also places obligations around security safeguards and covers processing performed by data processors on behalf of a data fiduciary.  
Source: https://www.meity.gov.in/static/uploads/2024/02/Digital-Personal-Data-Protection-Act-2023.pdf

This report is an engineering architecture document, not legal advice. ESU should involve the appropriate information-security, privacy, legal and client-contract stakeholders before using real production HRIS/ERP data.

---

## 22. Standards / Security References

### OWASP

OWASP's 2025 LLM guidance includes Sensitive Information Disclosure as a top risk and also addresses issues such as improper output handling and excessive agency.  
Sources:
- https://genai.owasp.org/llm-top-10/
- https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf

### NIST

NIST's Generative AI Profile provides a risk-management framework for identifying and managing generative-AI risks across the lifecycle.  
Source: https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence

### Workday security context

Workday documents role-based access controls, configurable security, and tenant-based logical segregation in its platform. This is relevant because an AI privacy gateway should respect the source system's authorization model rather than creating a second uncontrolled access path.  
Sources:
- https://www.workday.com/en-us/why-workday/trust/security.html
- https://www.workday.com/content/dam/web/se/documents/datasheets/datasheet-workday-security-se.pdf

---

## 23. Recommended Project Positioning

Do not position the project as:

> "An AI PII masker."

That is too small, and mature tools already exist.

A stronger engineering positioning is:

> **Policy-Driven Privacy Gateway for Enterprise AI**

or:

> **Privacy-Preserving AI Gateway for HRIS/ERP and Agentic Workloads**

Technical one-line description:

> A local-first, policy-driven security gateway that detects sensitive enterprise data, applies context-aware transformations, cryptographically preserves required relationships, prevents leakage across LLM/RAG/tool boundaries, and performs controlled re-identification only inside the trusted enterprise boundary.

---

## 24. What ESU Should Build vs. Reuse

| Capability | Reuse OSS / existing technology | Build ESU-specific layer |
|---|---|---|
| Regex detection | Yes | Domain patterns |
| Checksum validation | Yes | Country/vendor rules |
| NER | Yes | Model selection/tuning |
| Basic PII anonymization | Presidio | ESU policy wrappers |
| Flexible entity detection | GLiNER | ESU entity taxonomy |
| Exact matching | Cryptographic primitives | Secure enterprise lookup |
| Tokenization | Established crypto | Scope/key lifecycle |
| Vault | Existing DB/security primitives | Token governance |
| Policy engine | OPA or equivalent | HRIS/ERP policy packs |
| LLM gateway | Portkey-style architecture | ESU privacy gateway |
| Response DLP | Existing recognizers | Restoration policy |
| RAG controls | Existing vector DB stack | ACL/privacy integration |
| Tool security | Gateway patterns | Workday/ERP-specific policy |
| Evaluation | Presidio Research concepts | ESU benchmark corpus |
| Governance UI | Generic observability | ESU privacy console |

The central idea is **not to reinvent cryptography or PII recognition**. The differentiated IP is the **enterprise decision layer that understands source system, entity type, task purpose, policy, transformation, and execution path**.

---

## 25. Priority Backlog

### Priority 0 — Security boundary

1. Define trust boundary.
2. Ensure raw HRIS/ERP data never reaches external providers before inspection.
3. Remove raw data from logs.
4. Define fail-closed policy for high-risk categories.

### Priority 1 — Core detection

5. Implement detector interface.
6. Integrate Presidio.
7. Add regex/checksum/custom ESU recognizers.
8. Add local GLiNER/NER.
9. Build detector arbitration/context scoring.

### Priority 2 — Privacy transformations

10. Stable scoped placeholders.
11. Cryptographic tokenization.
12. Encrypted vault.
13. Authorized re-identification.
14. Key/version management.

### Priority 3 — Enterprise enforcement

15. OpenAI-compatible gateway.
16. Policy engine.
17. Request + response scanning.
18. Tool gateway.
19. RAG ingestion/retrieval controls.

### Priority 4 — Evidence

20. Synthetic HRIS/ERP benchmark.
21. Precision/recall/F1 measurements.
22. Privacy-leakage tests.
23. Latency/throughput benchmark.
24. Red-team scenarios.

### Priority 5 — Demonstration

25. Architecture dashboard.
26. Policy simulator.
27. Live before/after prompt demonstration.
28. Audit trace without raw PII.
29. Workday/ERP mock adapter.

---

## 26. Final Assessment

The industry evidence supports the architecture strongly, but it also exposes an important gap in simplistic implementations.

The problem is **not** merely:

```text
"How do I detect PII before sending text to an LLM?"
```

The enterprise question is:

```text
"Given a business task, what information does the model actually need,
what can be transformed without damaging task utility, what must be
computed inside the trusted boundary, and how do we prevent the data
from escaping through prompts, RAG, tools, logs or responses?"
```

The major platforms point to different pieces of this architecture:

- **Google** demonstrates mature cryptographic pseudonymization and re-identification patterns.
- **Cloudflare** demonstrates AI-gateway DLP across both request and response traffic.
- **AWS** demonstrates contextual sensitive-information detection and makes the tool-use boundary limitation visible.
- **Microsoft/Data Privacy Stack Presidio** provides a strong open-source detection/anonymization foundation.
- **Skyflow** demonstrates the privacy-vault pattern.
- **Protecto and Nightfall** show the broader AI data-control/DLP direction.
- **Portkey** demonstrates how these controls can live inside a production AI gateway.
- **TrueFoundry** demonstrates a practical guardrail service architecture.
- **Carnaval** demonstrates a direct reversible-anonymization pattern, but with much lower open-source maturity evidence.

For ESU, the strongest project is therefore not another standalone PII library. It is an **enterprise privacy control plane in front of AI** with strong HRIS/ERP awareness, cryptographic tokenization, task-aware routing, RAG/tool protection, and measurable privacy/utility guarantees.

---

# Appendix A — Reference Open-Source Repositories

| Project | URL | Role |
|---|---|---|
| Presidio | https://github.com/data-privacy-stack/presidio | PII detection/de-identification |
| Presidio Research | https://github.com/data-privacy-stack/presidio-research | Detection evaluation |
| Portkey Gateway | https://github.com/Portkey-AI/gateway | AI gateway architecture |
| TrueFoundry Custom Guardrails | https://github.com/truefoundry/custom-guardrails-template | Guardrail service pattern |
| Guardrails AI PII | https://github.com/guardrails-ai/guardrails_pii | Presidio + GLiNER pattern |
| Carnaval | https://github.com/carnaval-ai/carnaval | Reversible anonymization reference |
| LLM Guard (archived) | https://github.com/protectai/llm-guard | Historical scanner reference only |

---

# Appendix B — Enterprise Product References

| Product | Reference |
|---|---|
| Google Sensitive Data Protection | https://docs.cloud.google.com/sensitive-data-protection/docs/pseudonymization |
| Google de-identify/re-identify text | https://docs.cloud.google.com/sensitive-data-protection/docs/inspect-sensitive-text-de-identify |
| Cloudflare AI Gateway DLP | https://developers.cloudflare.com/ai-gateway/features/dlp/ |
| AWS Bedrock Guardrails sensitive filters | https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html |
| Skyflow LLM privacy | https://docs.skyflow.dev/docs/fundamentals/patterns/llm-privacy |
| Protecto | https://www.protecto.ai/ |
| Nightfall Developer API | https://help.nightfall.ai/developer-api/introduction/overview |
| Private AI | https://www.private-ai.com/en/ |
| Workday Security | https://www.workday.com/en-us/why-workday/trust/security.html |

---

# Appendix C — Security / Governance References

- OWASP GenAI / LLM Top 10 (2025): https://genai.owasp.org/llm-top-10/
- OWASP LLM02 Sensitive Information Disclosure PDF: https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf
- NIST Generative AI Profile: https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence
- India Digital Personal Data Protection Act, 2023: https://www.meity.gov.in/digital-personal-data-protection-act-2023
- India Digital Personal Data Protection Rules, 2025: https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa
- Microsoft Confidential AI: https://learn.microsoft.com/en-us/azure/confidential-computing/confidential-ai

---

## Research note

This report was researched against publicly available sources available as of **30 September 2026**. GitHub stars, repository activity and product capabilities can change; the architectural conclusions are based on the documented features and patterns cited above rather than on repository popularity alone.
