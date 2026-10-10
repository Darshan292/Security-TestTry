# Business-quality evaluation: `final-groq`

| Case | Privacy | Business | Overall | Non-passing checks | Human rubric |
|---|---|---|---|---|---|
| A1 | FAIL | PASS | FAIL | outbound:raw_values_absent, answer:no_raw_redaction_marker | 1 pending |
| A2 | FAIL | FAIL | FAIL | answer:no_raw_redaction_marker, unsupported:not_posted_cause, unsupported:numbers, unsupported:invented_codes, prohibited:manual_settlement_change, prohibited:adhoc_payment, next_step:calculation_logs, next_step:settlement_exceptions, section:facts, section:possible_causes, section:next_steps, format:word_limit | 2 pending |
| A3 | FAIL | FAIL | FAIL | answer:no_raw_redaction_marker, must_contain:hr_contact_email, must_not_contain:template_filler, must_not_contain:recipient_email_as_contact, must_not_contain:recipient_phone, must_not_contain:bare_placeholder | 1 pending |
| A4 | PASS | PASS | PASS | — | 0 pending |
| B1 | PASS | PASS | PASS | — | 1 pending |
| B2 | PASS | FAIL | FAIL | fact:addressed_by_name | 1 pending |
| B3 | PASS | PASS | PASS | — | 1 pending |
| M1 | PASS | PASS | PASS | — | 0 pending |
| M2 | PASS | PASS | PASS | — | 0 pending |
| M3 | PASS | PASS | PASS | — | 1 pending |

**6 pass, 4 fail, 0 not evaluable** of 10 cases. Overall pass requires PRIVACY and BUSINESS to pass; human rubric items are listed as pending and are not scored by this tool.
