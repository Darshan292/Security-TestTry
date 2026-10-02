# Business-correctness review of the real-provider answers (Groq gpt-oss-120b, synthetic data)

- **Source:** `human_review.md` of the final Groq run (11 answers), read against the inputs and the exact outbound prompts.
- **Reviewer:** the engineering agent, as a first-pass reviewer. **This is not the HR/payroll human sign-off.** That review is still required.
- Classified **before** any fix.

## Classification

| Answer | Verdict | What is wrong | Root cause |
|---|---|---|---|
| A1 ticket summary | Correct | Shows `[BANK_ACCOUNT]` marker text (acceptable in an internal summary, poor style) | Transformation/restoration (marker rendering) |
| A2 case explanation | **Partially correct** | Facts are right. It then lists 6 speculative causes and a "Bottom line" that asserts an unverified cause as fact ("approved but not posted… which is why"). It also invents process detail (finance sign-off, sync windows, codes) | **Prompt/contract design**: the task asks "why" with no system data and no instruction to separate facts from hypotheses. This lets LLM behaviour (confident speculation) through |
| A3 email draft | **Incorrect** | Tells the employee to contact HR at **the employee's own email and phone**; leaves the `[UPI_ID]` marker in a document meant for sending; adds `[Company Name]` filler | **Prompt/contract design**: no sender identity or sender contact is supplied, and recipient contacts are passed with no role. The model used the only contacts it had. Also **transformation/restoration**: the C3 redaction marker is rendered verbatim into a deliverable. Not detection |
| A4 ticket (NER-miss names) | Correct (this run) | An earlier run lost the requester: "Raised by:" was aliased as a PERSON | Detector/NER over-masking (latent; not fixed, by instruction) |
| B1 salary comparison | Correct | — | — |
| B2 ESI letter | **Partially correct** | Facts are right, but it is addressed only to "you": no name or ID in an employee-facing explanation | **Prompt/contract design**: the template gives the employee alias but does not say the text is addressed to that person by name |
| B3 pay band | Correct | — | — |
| M turn 1 | Correct | — | — |
| M turn 2 | Correct | — | — |
| M turn 3 | Correct (cosmetic) | Restores "tejaswini kulkarni" in lowercase, because that is how the ticket wrote it and the app did not re-declare the name in turn 3 | Transformation/restoration (spelling of ledger-only names; faithful to the input) |

**Summary:** 7 correct, 3 partially correct, 1 incorrect.

| Root cause | Answers affected |
|---|---|
| Prompt/contract design | 3 failures: A2, A3, B2 |
| Transformation/restoration | 1 failure (A3 markers) plus 2 cosmetic (A1, M3) |
| Detector/NER | 1 latent (A4) |

None of the failures is caused by:
- incorrect data classification;
- local computation;
- detection.

## Fixes chosen (confirmed blockers only; no new PII rules)

1. **A3: contract design.**
   - The `hr_email_draft` contract carries the **sender** (HR Operations role mailbox and helpdesk number, which are organisational C0 values) and task guidance:
     - write from the sender;
     - use only the sender's contact details;
     - never offer the recipient's own contacts as a way to reach HR.
   - The guidance is sealed with the contract and added to the system message after protection, so it is never aliased.
2. **A2: contract guidance.** The `hr_case_explanation` guidance requires three labelled parts: facts from the case, possible causes to verify (explicitly hypotheses, never stated as the conclusion), and next steps. It must not invent systems, timelines or policies.
3. **B2: template.** The `esi_letter` template addresses the employee by name and ID.
4. **Placeholders: restoration.**
   - The placeholder note tells the model that `[TYPE]` markers are withheld values, never to be copied.
   - Any marker that still appears is rendered in words per contract, e.g. `[BANK_ACCOUNT]` becomes "your registered bank account", with "(withheld)" as the default. The marker text therefore never reaches a deliverable, and no value is ever restored.

## After the fixes (real Groq run, NER worker pool on; 90/91 checks)

| Answer | Before | After | Note |
|---|---|---|---|
| A2 case explanation | Partially correct | **Correct** | Facts, then up to three labelled hypotheses, then next steps; no invented systems |
| A3 email draft | Incorrect | **Correct** | HR mailbox and helpdesk number, signed as HR Operations, no employee contacts, no filler; withheld values read "(withheld)" |
| B2 ESI letter | Partially correct | **Correct** | Addressed to the employee by name |
| Placeholders | Marker text in answers | Fixed | `[TYPE]` markers are rendered as "(withheld)"; a contract's own organisational contacts pass the output gate, while other raw emails and phones are still gated |

Two side findings:
- The real model run exposed one gateway defect: the output gate masked the contract's own HR mailbox. It is fixed.
- Still open (utility, not security): in multi-turn, a value the detectors find in the model's reply can be propagated back into earlier turn text (a ticket ID became `[COMPENSATION …]`).

Human HR/payroll sign-off is still required. This review is the engineering first pass.
