# Pilot validation (Path A declared subjects + Path B local compute): real endpoint, invalid key (answers BLOCKED: no ANTHROPIC_API_KEY)

- Model: `claude-opus-5-5` · data: synthetic only · captures: outside the repo
- Gateway latency (ms, excl. provider), per scenario: [54.4, 38.2, 28.1, 25.3, 29.8, 22.0]
- Provider latency (ms): [45.9, 33.6, 32.0, 33.0, 34.7, 32.0]

| Check | Result | Note |
|---|---|---|
| A1 ticket summary: request reached the provider | PASS | 1 exchange(s) |
| A1 ticket summary: no raw protected value in outbound bytes | PASS | 6 values checked, 0 found |
| A1 ticket summary: aliases in outbound | PASS | 4 distinct |
| A1 ticket summary: ledger row with contract, route, integrity | PASS | SENT ticket_summary@3 subjects=3 |
| A1 ticket summary: aliases survive provider formatting | BLOCKED | gateway status 502 |
| A1 ticket summary: restoration | BLOCKED | gateway status 502 |
| A1 ticket summary: C3 never returned | BLOCKED | gateway status 502 |
| A2 HR case explanation: request reached the provider | PASS | 1 exchange(s) |
| A2 HR case explanation: no raw protected value in outbound bytes | PASS | 5 values checked, 0 found |
| A2 HR case explanation: contract RAW/C0 values cross as declared | PASS | 1/1 present |
| A2 HR case explanation: aliases in outbound | PASS | 3 distinct |
| A2 HR case explanation: ledger row with contract, route, integrity | PASS | SENT hr_case_explanation@2 subjects=3 |
| A2 HR case explanation: aliases survive provider formatting | BLOCKED | gateway status 502 |
| A2 HR case explanation: restoration | BLOCKED | gateway status 502 |
| A2 HR case explanation: C3 never returned | BLOCKED | gateway status 502 |
| A3 email drafting: request reached the provider | PASS | 1 exchange(s) |
| A3 email drafting: no raw protected value in outbound bytes | PASS | 7 values checked, 0 found |
| A3 email drafting: aliases in outbound | PASS | 4 distinct |
| A3 email drafting: ledger row with contract, route, integrity | PASS | SENT hr_email_draft@2 subjects=4 |
| A3 email drafting: aliases survive provider formatting | BLOCKED | gateway status 502 |
| A3 email drafting: restoration | BLOCKED | gateway status 502 |
| A3 email drafting: C3 never returned | BLOCKED | gateway status 502 |
| A4 ticket with names NER misses: request reached the provider | PASS | 1 exchange(s) |
| A4 ticket with names NER misses: no raw protected value in outbound bytes | PASS | 7 values checked, 0 found |
| A4 ticket with names NER misses: aliases in outbound | PASS | 5 distinct |
| A4 ticket with names NER misses: ledger row with contract, route, integrity | PASS | SENT ticket_summary@3 subjects=4 |
| A4 ticket with names NER misses: detectors alone (incl. NER) miss declared names | PASS | detectors alone find 0/2 |
| A4 ticket with names NER misses: declared subjects protected where NER misses them | PASS | 0 leaked after declaration |
| A4 ticket with names NER misses: aliases survive provider formatting | BLOCKED | gateway status 502 |
| A4 ticket with names NER misses: restoration | BLOCKED | gateway status 502 |
| A4 ticket with names NER misses: C3 never returned | BLOCKED | gateway status 502 |
| B1 salary comparison: request reached the provider | PASS | 1 exchange(s) |
| B1 salary comparison: no local-compute inputs and INTERNAL outputs in outbound bytes | PASS | 17 values checked, 0 found |
| B1 salary comparison: aliases in outbound | PASS | 6 distinct |
| B1 salary comparison: ledger row with contract, route, integrity | PASS | SENT salary_comparison@2 subjects=None |
| B1 salary comparison: aliases survive provider formatting | BLOCKED | gateway status 502 |
| B1 salary comparison: restoration | BLOCKED | gateway status 502 |
| B1 salary comparison: C3 never returned | BLOCKED | gateway status 502 |
| B2 ESI eligibility letter: request reached the provider | PASS | 1 exchange(s) |
| B2 ESI eligibility letter: no local-compute inputs and INTERNAL outputs in outbound bytes | PASS | 10 values checked, 0 found |
| B2 ESI eligibility letter: contract RAW/C0 values cross as declared | PASS | 2/2 present |
| B2 ESI eligibility letter: aliases in outbound | PASS | 2 distinct |
| B2 ESI eligibility letter: ledger row with contract, route, integrity | PASS | SENT esi_letter@1 subjects=None |
| B2 ESI eligibility letter: aliases survive provider formatting | BLOCKED | gateway status 502 |
| B2 ESI eligibility letter: restoration | BLOCKED | gateway status 502 |
| B2 ESI eligibility letter: C3 never returned | BLOCKED | gateway status 502 |
| provider failure (invalid API key) fails closed | PASS | status 502 |
| provider failure (provider timeout) fails closed | PASS | status 502 |
| non-allowlisted provider destination refused at startup | PASS |  |
| gateway failure (detector crash) fails closed | PASS | status 503, sent 0 |
| gateway failure (egress invariant) fails closed | PASS | status 500, sent 0 |
| gateway failure (protection deadline) fails closed | PASS | status 503, sent 0 |
| declared-subject egress violation fails closed | PASS | status 500, sent 0 |
| no protected value in logs | PASS | 42 values, 0 found |
| no protected value in stderr/traces | PASS | 42 values, 0 found |
| no protected value in audit ledger | PASS | 42 values, 0 found |
| no protected value in error bodies | PASS | 42 values, 0 found |

**39 pass, 0 fail, 18 blocked.**
