# Pilot validation (Path A declared subjects + Path B local compute): mock self-test (NOT provider validation)

- Model: `claude-opus-5-5` · data: synthetic only · captures: outside the repo
- Gateway latency (ms, excl. provider), per scenario: [44.5, 38.7, 27.6, 25.7, 27.6, 21.3]
- Provider latency (ms): [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

| Check | Result | Note |
|---|---|---|
| A1 ticket summary: request reached the provider | PASS | 1 exchange(s) |
| A1 ticket summary: no raw protected value in outbound bytes | PASS | 6 values checked, 0 found |
| A1 ticket summary: aliases in outbound | PASS | 4 distinct |
| A1 ticket summary: ledger row with contract, route, integrity | PASS | SENT ticket_summary@3 subjects=3 |
| A1 ticket summary: aliases survive provider formatting | PASS | 4 exact alias uses in answer, 0 mangled |
| A1 ticket summary: restoration | PASS | 1/1 expected values restored, 0 aliases left |
| A1 ticket summary: C3 never returned | PASS | 2 checked |
| A2 HR case explanation: request reached the provider | PASS | 1 exchange(s) |
| A2 HR case explanation: no raw protected value in outbound bytes | PASS | 5 values checked, 0 found |
| A2 HR case explanation: contract RAW/C0 values cross as declared | PASS | 1/1 present |
| A2 HR case explanation: aliases in outbound | PASS | 3 distinct |
| A2 HR case explanation: ledger row with contract, route, integrity | PASS | SENT hr_case_explanation@2 subjects=3 |
| A2 HR case explanation: aliases survive provider formatting | PASS | 3 exact alias uses in answer, 0 mangled |
| A2 HR case explanation: restoration | PASS | 1/1 expected values restored, 0 aliases left |
| A2 HR case explanation: C3 never returned | PASS | 1 checked |
| A3 email drafting: request reached the provider | PASS | 1 exchange(s) |
| A3 email drafting: no raw protected value in outbound bytes | PASS | 7 values checked, 0 found |
| A3 email drafting: aliases in outbound | PASS | 4 distinct |
| A3 email drafting: ledger row with contract, route, integrity | PASS | SENT hr_email_draft@2 subjects=4 |
| A3 email drafting: aliases survive provider formatting | PASS | 4 exact alias uses in answer, 0 mangled |
| A3 email drafting: restoration | PASS | 1/1 expected values restored, 0 aliases left |
| A3 email drafting: C3 never returned | PASS | 2 checked |
| A4 ticket with names NER misses: request reached the provider | PASS | 1 exchange(s) |
| A4 ticket with names NER misses: no raw protected value in outbound bytes | PASS | 7 values checked, 0 found |
| A4 ticket with names NER misses: aliases in outbound | PASS | 5 distinct |
| A4 ticket with names NER misses: ledger row with contract, route, integrity | PASS | SENT ticket_summary@3 subjects=4 |
| A4 ticket with names NER misses: detectors alone (incl. NER) miss declared names | PASS | detectors alone find 0/2 |
| A4 ticket with names NER misses: declared subjects protected where NER misses them | PASS | 0 leaked after declaration |
| A4 ticket with names NER misses: aliases survive provider formatting | PASS | 5 exact alias uses in answer, 0 mangled |
| A4 ticket with names NER misses: restoration | PASS | 2/2 expected values restored, 0 aliases left |
| A4 ticket with names NER misses: C3 never returned | PASS | 0 checked |
| B1 salary comparison: request reached the provider | PASS | 1 exchange(s) |
| B1 salary comparison: no local-compute inputs and INTERNAL outputs in outbound bytes | PASS | 17 values checked, 0 found |
| B1 salary comparison: aliases in outbound | PASS | 6 distinct |
| B1 salary comparison: ledger row with contract, route, integrity | PASS | SENT salary_comparison@2 subjects=None |
| B1 salary comparison: aliases survive provider formatting | PASS | 6 exact alias uses in answer, 0 mangled |
| B1 salary comparison: restoration | PASS | 2/2 expected values restored, 0 aliases left |
| B1 salary comparison: C3 never returned | PASS | 0 checked |
| B2 ESI eligibility letter: request reached the provider | PASS | 1 exchange(s) |
| B2 ESI eligibility letter: no local-compute inputs and INTERNAL outputs in outbound bytes | PASS | 10 values checked, 0 found |
| B2 ESI eligibility letter: contract RAW/C0 values cross as declared | PASS | 2/2 present |
| B2 ESI eligibility letter: aliases in outbound | PASS | 2 distinct |
| B2 ESI eligibility letter: ledger row with contract, route, integrity | PASS | SENT esi_letter@1 subjects=None |
| B2 ESI eligibility letter: aliases survive provider formatting | PASS | 2 exact alias uses in answer, 0 mangled |
| B2 ESI eligibility letter: restoration | PASS | 1/1 expected values restored, 0 aliases left |
| B2 ESI eligibility letter: C3 never returned | PASS | 0 checked |
| non-allowlisted provider destination refused at startup | PASS |  |
| gateway failure (detector crash) fails closed | PASS | status 503, sent 0 |
| gateway failure (egress invariant) fails closed | PASS | status 500, sent 0 |
| gateway failure (protection deadline) fails closed | PASS | status 503, sent 0 |
| declared-subject egress violation fails closed | PASS | status 500, sent 0 |
| no protected value in logs | PASS | 42 values, 0 found |
| no protected value in stderr/traces | PASS | 42 values, 0 found |
| no protected value in audit ledger | PASS | 42 values, 0 found |
| no protected value in error bodies | PASS | 42 values, 0 found |

**55 pass, 0 fail, 0 blocked.**
