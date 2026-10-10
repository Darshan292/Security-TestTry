# Business-quality evaluation: `final2-groq`

| Case | Privacy | Business | Overall | Non-passing checks | Human rubric |
|---|---|---|---|---|---|
| A1 | NOT_EVALUABLE | FAIL | FAIL | outbound:raw_values_absent, fact:ticket_id | 1 pending |
| A2 | PASS | FAIL | FAIL | next_step:calculation_logs, next_step:settlement_exceptions | 2 pending |
| A3 | PASS | PASS | PASS | — | 1 pending |
| A4 | PASS | PASS | PASS | — | 0 pending |
| B1 | PASS | PASS | PASS | — | 1 pending |
| B2 | PASS | PASS | PASS | — | 1 pending |
| B3 | PASS | PASS | PASS | — | 1 pending |
| M1 | PASS | FAIL | FAIL | fact:requester | 0 pending |
| M2 | PASS | FAIL | FAIL | fact:addressed_to_requester | 0 pending |
| M3 | PASS | PASS | PASS | — | 1 pending |

**6 pass, 4 fail, 0 not evaluable** of 10 cases. Overall pass requires PRIVACY and BUSINESS to pass; human rubric items are listed as pending and are not scored by this tool.
