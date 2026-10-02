# Pilot validation: real endpoint, invalid key (answers BLOCKED: no ANTHROPIC_API_KEY)

- Model: `claude-opus-5-5` · data: synthetic only · captures: outside the repo
- Gateway latency (ms, excl. provider), per scenario: [48.5, 30.4, 55.0, 24.0, 18.9]
- Provider latency (ms): [39.1, 31.8, 34.0, 35.8, 33.0]

| Check | Result | Note |
|---|---|---|
| A1 ticket summary: request reached the provider | PASS | 1 exchange(s) |
| A1 ticket summary: no raw protected value in outbound bytes | PASS | 6 values checked, 0 found |
| A1 ticket summary: aliases in outbound | PASS | 4 distinct |
| A1 ticket summary: ledger row with contract, route, integrity | PASS | SENT |
| A1 ticket summary: aliases survive provider formatting | BLOCKED | gateway status 502 |
| A1 ticket summary: restoration | BLOCKED | gateway status 502 |
| A1 ticket summary: C3 never returned | BLOCKED | gateway status 502 |
| A2 HR case explanation: request reached the provider | PASS | 1 exchange(s) |
| A2 HR case explanation: no raw protected value in outbound bytes | PASS | 5 values checked, 0 found |
| A2 HR case explanation: contract RAW/C0 values cross as declared | PASS | 1/1 present |
| A2 HR case explanation: aliases in outbound | PASS | 3 distinct |
| A2 HR case explanation: ledger row with contract, route, integrity | PASS | SENT |
| A2 HR case explanation: aliases survive provider formatting | BLOCKED | gateway status 502 |
| A2 HR case explanation: restoration | BLOCKED | gateway status 502 |
| A2 HR case explanation: C3 never returned | BLOCKED | gateway status 502 |
| A3 email drafting: request reached the provider | PASS | 1 exchange(s) |
| A3 email drafting: no raw protected value in outbound bytes | PASS | 7 values checked, 0 found |
| A3 email drafting: aliases in outbound | PASS | 4 distinct |
| A3 email drafting: ledger row with contract, route, integrity | PASS | SENT |
| A3 email drafting: aliases survive provider formatting | BLOCKED | gateway status 502 |
| A3 email drafting: restoration | BLOCKED | gateway status 502 |
| A3 email drafting: C3 never returned | BLOCKED | gateway status 502 |
| B1 salary comparison: request reached the provider | PASS | 1 exchange(s) |
| B1 salary comparison: no raw protected value in outbound bytes | PASS | 17 values checked, 0 found |
| B1 salary comparison: aliases in outbound | PASS | 6 distinct |
| B1 salary comparison: ledger row with contract, route, integrity | PASS | SENT |
| B1 salary comparison: aliases survive provider formatting | BLOCKED | gateway status 502 |
| B1 salary comparison: restoration | BLOCKED | gateway status 502 |
| B1 salary comparison: C3 never returned | BLOCKED | gateway status 502 |
| B2 ESI eligibility letter: request reached the provider | PASS | 1 exchange(s) |
| B2 ESI eligibility letter: no raw protected value in outbound bytes | PASS | 10 values checked, 0 found |
| B2 ESI eligibility letter: contract RAW/C0 values cross as declared | PASS | 2/2 present |
| B2 ESI eligibility letter: aliases in outbound | PASS | 2 distinct |
| B2 ESI eligibility letter: ledger row with contract, route, integrity | PASS | SENT |
| B2 ESI eligibility letter: aliases survive provider formatting | BLOCKED | gateway status 502 |
| B2 ESI eligibility letter: restoration | BLOCKED | gateway status 502 |
| B2 ESI eligibility letter: C3 never returned | BLOCKED | gateway status 502 |
| provider failure (invalid API key) fails closed | PASS | status 502 |
| provider failure (provider timeout) fails closed | PASS | status 502 |
| non-allowlisted provider destination refused at startup | PASS |  |
| gateway failure (detector crash) fails closed | PASS | status 503, sent 0 |
| gateway failure (egress invariant) fails closed | PASS | status 500, sent 0 |
| gateway failure (protection deadline) fails closed | PASS | status 503, sent 0 |
| no protected value in logs | PASS | 37 values, 0 found |
| no protected value in stderr/traces | PASS | 37 values, 0 found |
| no protected value in audit ledger | PASS | 37 values, 0 found |
| no protected value in error bodies | PASS | 37 values, 0 found |

**32 pass, 0 fail, 15 blocked.**
