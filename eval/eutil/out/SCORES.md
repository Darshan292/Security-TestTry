# E-UTIL scores

Deterministic scoring; no LLM judge. Degradation is relative to RAW (E-UTIL gate: <= 5 %, reported, not enforced). `all` counts blocked items as incorrect; `unblocked` excludes them.

## Per condition

| group | n | blocked | correct (all) | correct (unblocked) | mean score | entity pres. | fact. equiv. | sections | len ratio | crit masked | unrestored | fabricated | forbidden | items w/ sensitive out | degr. (all) | degr. (unblocked) | gate <=5% | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| raw | 40 | 0.0% | 82.5% | 82.5% | 91.2% | 82.9% | n/a | 100.0% | n/a | 0 | 0 | 0 | 0 | 36 | n/a | n/a | n/a | n/a | n/a |
| gateway | 40 | 30.0% | 37.5% | 53.6% | 47.3% | 65.5% | 75.6% | 100.0% | 1.2491 | 4 | 0 | 0 | 0 | 4 | 54.5% | 35.1% | False | 6.158 | 14.822 |
| max | 40 | 0.0% | 55.0% | 55.0% | 65.9% | 100.0% | 63.1% | 100.0% | 1.2544 | 133 | 0 | 0 | 0 | 4 | 33.3% | 33.3% | False | 8.352 | 13.731 |

## Task type: case_explanation

| group | n | blocked | correct (all) | correct (unblocked) | mean score | entity pres. | fact. equiv. | sections | len ratio | crit masked | unrestored | fabricated | forbidden | items w/ sensitive out | degr. (all) | degr. (unblocked) | gate <=5% | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| raw | 5 | 0.0% | 60.0% | 60.0% | 85.3% | 20.0% | n/a | n/a | n/a | 0 | 0 | 0 | 0 | 5 | n/a | n/a | n/a | n/a | n/a |
| gateway | 5 | 40.0% | 60.0% | 100.0% | 60.0% | 60.0% | 100.0% | n/a | 1.214 | 1 | 0 | 0 | 0 | 1 | 0.0% | -66.7% | True | 9.568 | 10.8 |
| max | 5 | 0.0% | 40.0% | 40.0% | 57.3% | 100.0% | 66.7% | n/a | 1.0204 | 22 | 0 | 0 | 0 | 0 | 33.3% | 33.3% | False | 11.046 | 13.487 |

## Task type: classification

| group | n | blocked | correct (all) | correct (unblocked) | mean score | entity pres. | fact. equiv. | sections | len ratio | crit masked | unrestored | fabricated | forbidden | items w/ sensitive out | degr. (all) | degr. (unblocked) | gate <=5% | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| raw | 5 | 0.0% | 100.0% | 100.0% | 100.0% | n/a | n/a | n/a | n/a | 0 | 0 | 0 | 0 | 5 | n/a | n/a | n/a | n/a | n/a |
| gateway | 5 | 40.0% | 60.0% | 100.0% | 60.0% | n/a | n/a | n/a | 1.0 | 0 | 0 | 0 | 0 | 1 | 40.0% | 0.0% | False | 2.904 | 3.218 |
| max | 5 | 0.0% | 100.0% | 100.0% | 100.0% | n/a | n/a | n/a | 1.0 | 4 | 0 | 0 | 0 | 2 | 0.0% | 0.0% | True | 3.194 | 3.358 |

## Task type: comparison

| group | n | blocked | correct (all) | correct (unblocked) | mean score | entity pres. | fact. equiv. | sections | len ratio | crit masked | unrestored | fabricated | forbidden | items w/ sensitive out | degr. (all) | degr. (unblocked) | gate <=5% | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| raw | 5 | 0.0% | 80.0% | 80.0% | 93.3% | 100.0% | n/a | n/a | n/a | 0 | 0 | 0 | 0 | 4 | n/a | n/a | n/a | n/a | n/a |
| gateway | 5 | 0.0% | 40.0% | 40.0% | 63.3% | 100.0% | 66.7% | n/a | 1.9592 | 1 | 0 | 0 | 0 | 0 | 50.0% | 50.0% | False | 4.38 | 7.322 |
| max | 5 | 0.0% | 20.0% | 20.0% | 45.0% | 100.0% | 48.3% | n/a | 1.9704 | 17 | 0 | 0 | 0 | 0 | 75.0% | 75.0% | False | 5.483 | 6.785 |

## Task type: email_drafting

| group | n | blocked | correct (all) | correct (unblocked) | mean score | entity pres. | fact. equiv. | sections | len ratio | crit masked | unrestored | fabricated | forbidden | items w/ sensitive out | degr. (all) | degr. (unblocked) | gate <=5% | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| raw | 5 | 0.0% | 60.0% | 60.0% | 70.0% | 81.0% | n/a | 100.0% | n/a | 0 | 0 | 0 | 0 | 5 | n/a | n/a | n/a | n/a | n/a |
| gateway | 5 | 60.0% | 20.0% | 50.0% | 30.0% | 40.0% | 100.0% | 100.0% | 1.3499 | 0 | 0 | 0 | 0 | 1 | 66.7% | 16.7% | False | 6.593 | 10.827 |
| max | 5 | 0.0% | 60.0% | 60.0% | 60.0% | 100.0% | 50.0% | 100.0% | 0.976 | 17 | 0 | 0 | 0 | 1 | 0.0% | 0.0% | True | 9.48 | 11.158 |

## Task type: extraction

| group | n | blocked | correct (all) | correct (unblocked) | mean score | entity pres. | fact. equiv. | sections | len ratio | crit masked | unrestored | fabricated | forbidden | items w/ sensitive out | degr. (all) | degr. (unblocked) | gate <=5% | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| raw | 5 | 0.0% | 100.0% | 100.0% | 100.0% | 100.0% | n/a | 100.0% | n/a | 0 | 0 | 0 | 0 | 5 | n/a | n/a | n/a | n/a | n/a |
| gateway | 5 | 80.0% | 0.0% | 0.0% | 0.0% | 20.0% | n/a | n/a | 1.0 | 0 | 0 | 0 | 0 | 1 | 100.0% | 100.0% | False | 4.021 | 6.118 |
| max | 5 | 0.0% | 40.0% | 40.0% | 68.0% | 100.0% | 50.0% | 100.0% | 2.0338 | 6 | 0 | 0 | 0 | 1 | 60.0% | 60.0% | False | 4.796 | 7.982 |

## Task type: policy_qa

| group | n | blocked | correct (all) | correct (unblocked) | mean score | entity pres. | fact. equiv. | sections | len ratio | crit masked | unrestored | fabricated | forbidden | items w/ sensitive out | degr. (all) | degr. (unblocked) | gate <=5% | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| raw | 5 | 0.0% | 80.0% | 80.0% | 90.0% | n/a | n/a | n/a | n/a | 0 | 0 | 0 | 0 | 2 | n/a | n/a | n/a | n/a | n/a |
| gateway | 5 | 0.0% | 40.0% | 40.0% | 50.0% | n/a | 75.0% | n/a | 0.9568 | 2 | 0 | 0 | 0 | 0 | 50.0% | 50.0% | False | 4.509 | 5.171 |
| max | 5 | 0.0% | 60.0% | 60.0% | 60.0% | n/a | 75.0% | n/a | 0.941 | 15 | 0 | 0 | 0 | 0 | 25.0% | 25.0% | False | 4.458 | 5.259 |

## Task type: report_generation

| group | n | blocked | correct (all) | correct (unblocked) | mean score | entity pres. | fact. equiv. | sections | len ratio | crit masked | unrestored | fabricated | forbidden | items w/ sensitive out | degr. (all) | degr. (unblocked) | gate <=5% | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| raw | 5 | 0.0% | 100.0% | 100.0% | 100.0% | 100.0% | n/a | 100.0% | n/a | 0 | 0 | 0 | 0 | 5 | n/a | n/a | n/a | n/a | n/a |
| gateway | 5 | 0.0% | 40.0% | 40.0% | 53.0% | 100.0% | 53.0% | 100.0% | 1.0901 | 0 | 0 | 0 | 0 | 0 | 60.0% | 60.0% | False | 9.587 | 15.821 |
| max | 5 | 0.0% | 40.0% | 40.0% | 46.0% | 100.0% | 46.0% | 100.0% | 0.9495 | 24 | 0 | 0 | 0 | 0 | 60.0% | 60.0% | False | 12.281 | 13.673 |

## Task type: summarization

| group | n | blocked | correct (all) | correct (unblocked) | mean score | entity pres. | fact. equiv. | sections | len ratio | crit masked | unrestored | fabricated | forbidden | items w/ sensitive out | degr. (all) | degr. (unblocked) | gate <=5% | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| raw | 5 | 0.0% | 80.0% | 80.0% | 91.0% | 100.0% | n/a | n/a | n/a | 0 | 0 | 0 | 0 | 5 | n/a | n/a | n/a | n/a | n/a |
| gateway | 5 | 20.0% | 40.0% | 50.0% | 62.0% | 80.0% | 85.4% | n/a | 1.1508 | 0 | 0 | 0 | 0 | 0 | 50.0% | 37.5% | False | 9.795 | 19.28 |
| max | 5 | 0.0% | 80.0% | 80.0% | 91.0% | 100.0% | 90.0% | n/a | 1.1443 | 28 | 0 | 0 | 0 | 0 | 0.0% | 0.0% | True | 9.315 | 18.411 |

