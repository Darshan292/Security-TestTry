## E-DET — ner_test2

Items: 425

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| BANK_ACCOUNT | 25 | 1.000 | 0.867–1.000 | 1.000 | 0.98/0.9/150 | UNDERPOWERED |
| COMPENSATION | 100 | 0.790 | 0.700–0.858 | 1.000 | 0.95/0.85/200 | UNDERPOWERED |
| DATE_OF_BIRTH | 25 | 1.000 | 0.867–1.000 | 1.000 | — | no gate |
| EMAIL | 150 | 1.000 | 0.975–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| IFSC | 25 | 1.000 | 0.867–1.000 | 1.000 | — | no gate |
| PAN | 25 | 1.000 | 0.867–1.000 | 1.000 | 0.98/0.9/150 | UNDERPOWERED |
| PERSON | 1500 | 0.133 | 0.116–0.151 | 1.000 | 0.9/0.85/400 | FAIL |
| PHONE | 300 | 1.000 | 0.987–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| UPI_ID | 25 | 1.000 | 0.867–1.000 | 1.000 | 0.98/0.9/150 | UNDERPOWERED |

PERSON recall by name source: external 0.133 (n=1500, CI 0.116–0.151)
Character leak rate: **0.5152** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.87 · p95 5.57 · p99 6.43 · max 7.1
Stream round-trip (random chunking): exact 425/425, case-insensitive 425/425
Document exposure (>= 1 raw gold value in outbound body): **97.41%** (414/425)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

