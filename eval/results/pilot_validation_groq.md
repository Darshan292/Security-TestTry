# Pilot validation (Path A declared subjects + Path B local compute): real provider (groq, openai/gpt-oss-120b)

- Model: `openai/gpt-oss-120b` · data: synthetic only · captures: outside the repo
- Policy/contract integrity digest: `cc474a009a74b762`
- Gateway latency (ms, excl. provider), per scenario: [40.9, 49.8, 35.1, 27.9, 35.7, 27.1, 29.5]
- Provider latency (ms): [1422.6, 1511.5, 1481.3, 547.5, 750.4, 553.6, 661.8]

| Check | Result | Note |
|---|---|---|
| A1 ticket summary: request reached the provider | PASS | 1 exchange(s) |
| A1 ticket summary: no raw protected value in outbound bytes | PASS | 6 values checked, 0 found |
| A1 ticket summary: aliases in outbound | PASS | 4 distinct |
| A1 ticket summary: ledger row with contract, route, integrity | PASS | SENT ticket_summary@3 subjects=3 |
| A1 ticket summary: aliases survive provider formatting | PASS | 4 exact alias uses in answer, 0 mangled |
| A1 ticket summary: restoration (every alias the model used came back as the real value) | PASS | 4 alias uses in the answer, 0 left unrestored |
| A1 ticket summary: answer names the expected people (content, model-dependent) | PASS | 1/1 |
| A1 ticket summary: C3 never returned | PASS | 2 checked |
| A2 HR case explanation: request reached the provider | PASS | 1 exchange(s) |
| A2 HR case explanation: no raw protected value in outbound bytes | PASS | 5 values checked, 0 found |
| A2 HR case explanation: contract RAW/C0 values cross as declared | PASS | 1/1 present |
| A2 HR case explanation: aliases in outbound | PASS | 3 distinct |
| A2 HR case explanation: ledger row with contract, route, integrity | PASS | SENT hr_case_explanation@3 subjects=3 |
| A2 HR case explanation: aliases survive provider formatting | PASS | 3 exact alias uses in answer, 0 mangled |
| A2 HR case explanation: restoration (every alias the model used came back as the real value) | PASS | 3 alias uses in the answer, 0 left unrestored |
| A2 HR case explanation: answer contains required item 1 (business correctness) | PASS |  |
| A2 HR case explanation: answer does not contain a forbidden item (business correctness) | PASS | ok |
| A2 HR case explanation: answer names the expected people (content, model-dependent) | PASS | 1/1 |
| A2 HR case explanation: C3 never returned | PASS | 1 checked |
| A3 email drafting: request reached the provider | PASS | 1 exchange(s) |
| A3 email drafting: no raw protected value in outbound bytes | PASS | 7 values checked, 0 found |
| A3 email drafting: aliases in outbound | PASS | 4 distinct |
| A3 email drafting: ledger row with contract, route, integrity | PASS | SENT hr_email_draft@3 subjects=4 |
| A3 email drafting: aliases survive provider formatting | PASS | 1 exact alias uses in answer, 0 mangled |
| A3 email drafting: restoration (every alias the model used came back as the real value) | PASS | 1 alias uses in the answer, 0 left unrestored |
| A3 email drafting: answer contains required item 1 (business correctness) | PASS |  |
| A3 email drafting: answer does not contain a forbidden item (business correctness) | PASS | ok |
| A3 email drafting: answer does not contain a forbidden item (business correctness) | PASS | ok |
| A3 email drafting: answer does not contain a forbidden item (business correctness) | PASS | ok |
| A3 email drafting: answer does not contain a forbidden item (business correctness) | PASS | ok |
| A3 email drafting: answer does not contain a forbidden item (business correctness) | PASS | ok |
| A3 email drafting: answer names the expected people (content, model-dependent) | PASS | 1/1 |
| A3 email drafting: C3 never returned | PASS | 2 checked |
| A4 ticket with names NER misses: request reached the provider | PASS | 1 exchange(s) |
| A4 ticket with names NER misses: no raw protected value in outbound bytes | PASS | 7 values checked, 0 found |
| A4 ticket with names NER misses: aliases in outbound | PASS | 5 distinct |
| A4 ticket with names NER misses: ledger row with contract, route, integrity | PASS | SENT ticket_summary@3 subjects=4 |
| A4 ticket with names NER misses: detectors alone (incl. NER) miss declared names | PASS | detectors alone find 0/2 |
| A4 ticket with names NER misses: declared subjects protected where NER misses them | PASS | 0 leaked after declaration |
| A4 ticket with names NER misses: aliases survive provider formatting | PASS | 3 exact alias uses in answer, 0 mangled |
| A4 ticket with names NER misses: restoration (every alias the model used came back as the real value) | PASS | 3 alias uses in the answer, 0 left unrestored |
| A4 ticket with names NER misses: answer names the expected people (content, model-dependent) | PASS | 2/2 |
| A4 ticket with names NER misses: C3 never returned | PASS | 0 checked |
| B1 salary comparison: request reached the provider | PASS | 1 exchange(s) |
| B1 salary comparison: no local-compute inputs and INTERNAL outputs in outbound bytes | PASS | 17 values checked, 0 found |
| B1 salary comparison: aliases in outbound | PASS | 6 distinct |
| B1 salary comparison: ledger row with contract, route, integrity | PASS | SENT salary_comparison@2 subjects=None |
| B1 salary comparison: aliases survive provider formatting | PASS | 3 exact alias uses in answer, 0 mangled |
| B1 salary comparison: restoration (every alias the model used came back as the real value) | PASS | 3 alias uses in the answer, 0 left unrestored |
| B1 salary comparison: answer names the expected people (content, model-dependent) | PASS | 2/2 |
| B1 salary comparison: C3 never returned | PASS | 0 checked |
| B2 ESI eligibility letter: request reached the provider | PASS | 1 exchange(s) |
| B2 ESI eligibility letter: no local-compute inputs and INTERNAL outputs in outbound bytes | PASS | 10 values checked, 0 found |
| B2 ESI eligibility letter: contract RAW/C0 values cross as declared | PASS | 2/2 present |
| B2 ESI eligibility letter: aliases in outbound | PASS | 2 distinct |
| B2 ESI eligibility letter: ledger row with contract, route, integrity | PASS | SENT esi_letter@2 subjects=None |
| B2 ESI eligibility letter: aliases survive provider formatting | PASS | 1 exact alias uses in answer, 0 mangled |
| B2 ESI eligibility letter: restoration (every alias the model used came back as the real value) | PASS | 1 alias uses in the answer, 0 left unrestored |
| B2 ESI eligibility letter: answer contains required item 1 (business correctness) | PASS |  |
| B2 ESI eligibility letter: answer names the expected people (content, model-dependent) | PASS | 1/1 |
| B2 ESI eligibility letter: C3 never returned | PASS | 0 checked |
| B3 pay band position: request reached the provider | PASS | 1 exchange(s) |
| B3 pay band position: no raw protected value in outbound bytes | PASS | 11 values checked, 0 found |
| B3 pay band position: contract RAW/C0 values cross as declared | PASS | 2/2 present |
| B3 pay band position: aliases in outbound | PASS | 2 distinct |
| B3 pay band position: ledger row with contract, route, integrity | PASS | SENT band_position@1 subjects=None |
| B3 pay band position: aliases survive provider formatting | PASS | 2 exact alias uses in answer, 0 mangled |
| B3 pay band position: restoration (every alias the model used came back as the real value) | PASS | 2 alias uses in the answer, 0 left unrestored |
| B3 pay band position: answer names the expected people (content, model-dependent) | PASS | 1/1 |
| B3 pay band position: C3 never returned | PASS | 0 checked |
| M turn 1: no raw value (incl. undeclared-this-turn names) in outbound | PASS | status 200, 0 found |
| M turn 1: restored answer has no aliases left | PASS | 0 left |
| M turn 2: no raw value (incl. undeclared-this-turn names) in outbound | PASS | status 200, 0 found |
| M turn 2: restored answer has no aliases left | PASS | 0 left |
| M turn 3: no raw value (incl. undeclared-this-turn names) in outbound | PASS | status 200, 0 found |
| M turn 3: restored answer has no aliases left | PASS | 0 left |
| M: alias stability (same aliases, same positions, for turn-1 text in turns 2 and 3) | PASS | 6 aliases per turn |
| M: turn-1 text protected byte-identically in later turns (utility) | FAIL | differs: a value detected in the model's reply was propagated into earlier text |
| M: restore ledger holds the conversation as keyed hashes only | PASS | 9 rows, no plaintext |
| M: history without a conversation id is refused (fail closed) | PASS | status 400 |
| provider failure (invalid API key) fails closed | PASS | status 502 |
| provider failure (provider timeout) fails closed | PASS | status 502 |
| non-allowlisted provider destination refused at startup | PASS |  |
| gateway failure (detector crash) fails closed | PASS | status 503, sent 0 |
| gateway failure (egress invariant) fails closed | PASS | status 500, sent 0 |
| gateway failure (protection deadline) fails closed | PASS | status 503, sent 0 |
| declared-subject egress violation fails closed | PASS | status 500, sent 0 |
| no protected value in logs | PASS | 47 values, 0 found |
| no protected value in stderr/traces | PASS | 47 values, 0 found |
| no protected value in audit ledger | PASS | 47 values, 0 found |
| no protected value in error bodies | PASS | 47 values, 0 found |

**90 pass, 1 fail, 0 blocked.**
