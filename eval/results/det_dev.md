## E-DET — dev

Items: 3977

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| BANK_ACCOUNT | 1536 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 739 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 3241 | 1.000 | 0.999–1.000 | 1.000 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 1211 | 1.000 | 0.997–1.000 | 0.949 | — | no gate |
| EMAIL | 1598 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 4192 | 1.000 | 0.999–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| IFSC | 1384 | 1.000 | 0.997–1.000 | 1.000 | — | no gate |
| PAN | 1178 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 11453 | 0.945 | 0.940–0.949 | 0.959 | 0.9/0.85/400 | PASS |
| PHONE | 1573 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 714 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| UPI_ID | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.768 (n=607, CI 0.732–0.800), mixed 0.923 (n=6400, CI 0.916–0.929), roster 1.000 (n=4446, CI 0.999–1.000)
Character leak rate: **0.0211** (gate ≤ 0.01)
Negatives: 45/750 items with ≥1 false positive; by type {'PERSON': 73}
Latency (detect only, CPU, ms): p50 2.28 · p95 5.07 · p99 9.73 · max 17.8
Stream round-trip (random chunking): exact 3836/3977, case-insensitive 3976/3977

## E-ADV — homoglyph

Items: 3227

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| BANK_ACCOUNT | 1536 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 739 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 3241 | 1.000 | 0.999–1.000 | 1.000 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 1211 | 1.000 | 0.997–1.000 | 0.949 | — | no gate |
| EMAIL | 1598 | 0.998 | 0.994–0.999 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 4192 | 1.000 | 0.999–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| IFSC | 1384 | 1.000 | 0.997–1.000 | 1.000 | — | no gate |
| PAN | 1178 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 11453 | 0.914 | 0.909–0.919 | 0.963 | 0.9/0.85/400 | PASS |
| PHONE | 1573 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 714 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| UPI_ID | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.755 (n=607, CI 0.719–0.787), mixed 0.876 (n=6400, CI 0.867–0.884), roster 0.991 (n=4446, CI 0.988–0.993)
Character leak rate: **0.0332** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.82 · p95 5.94 · p99 12.44 · max 19.2
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0

## E-ADV — zero_width

Items: 3227

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| BANK_ACCOUNT | 1536 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 739 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 3241 | 1.000 | 0.998–1.000 | 1.000 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 1211 | 1.000 | 0.997–1.000 | 0.950 | — | no gate |
| EMAIL | 1598 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 4192 | 1.000 | 0.999–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| IFSC | 1384 | 1.000 | 0.997–1.000 | 1.000 | — | no gate |
| PAN | 1178 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 11453 | 0.920 | 0.915–0.925 | 0.961 | 0.9/0.85/400 | PASS |
| PHONE | 1573 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 714 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| UPI_ID | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.755 (n=607, CI 0.719–0.787), mixed 0.882 (n=6400, CI 0.874–0.890), roster 0.999 (n=4446, CI 0.997–0.999)
Character leak rate: **0.0297** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.76 · p95 5.81 · p99 12.29 · max 19.5
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0

## E-ADV — spaced

Items: 3227

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| BANK_ACCOUNT | 1536 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 739 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 3241 | 1.000 | 0.999–1.000 | 1.000 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 1211 | 1.000 | 0.997–1.000 | 0.949 | — | no gate |
| EMAIL | 1598 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 4192 | 1.000 | 0.999–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| IFSC | 1384 | 1.000 | 0.997–1.000 | 1.000 | — | no gate |
| PAN | 1178 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 11453 | 0.114 | 0.109–0.120 | 0.778 | 0.9/0.85/400 | FAIL |
| PHONE | 1573 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 714 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| UPI_ID | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.120 (n=607, CI 0.097–0.149), mixed 0.120 (n=6400, CI 0.112–0.128), roster 0.105 (n=4446, CI 0.097–0.115)
Character leak rate: **0.4548** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.92 · p95 6.00 · p99 12.52 · max 20.5
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0

## E-ADV — obfuscated_email

Items: 3227

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| BANK_ACCOUNT | 1536 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 739 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 3241 | 1.000 | 0.999–1.000 | 1.000 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 1211 | 1.000 | 0.997–1.000 | 0.949 | — | no gate |
| EMAIL | 1598 | 0.065 | 0.054–0.078 | 1.000 | 0.98/0.95/150 | FAIL |
| EMPLOYEE_ID | 4192 | 1.000 | 0.999–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| IFSC | 1384 | 1.000 | 0.997–1.000 | 1.000 | — | no gate |
| PAN | 1178 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 11453 | 0.945 | 0.940–0.949 | 0.826 | 0.9/0.85/400 | FAIL |
| PHONE | 1573 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 714 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| UPI_ID | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.768 (n=607, CI 0.732–0.800), mixed 0.923 (n=6400, CI 0.916–0.929), roster 1.000 (n=4446, CI 0.999–1.000)
Character leak rate: **0.1268** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.47 · p95 5.33 · p99 11.03 · max 28.1
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0

## E-ADV — case

Items: 3227

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| BANK_ACCOUNT | 1536 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 739 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 3241 | 1.000 | 0.999–1.000 | 1.000 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 1211 | 1.000 | 0.997–1.000 | 0.949 | — | no gate |
| EMAIL | 1598 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 4192 | 1.000 | 0.999–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| IFSC | 1384 | 1.000 | 0.997–1.000 | 1.000 | — | no gate |
| PAN | 1178 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 11453 | 0.851 | 0.844–0.857 | 0.958 | 0.9/0.85/400 | FAIL |
| PHONE | 1573 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 714 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| UPI_ID | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.616 (n=607, CI 0.577–0.654), mixed 0.790 (n=6400, CI 0.780–0.799), roster 0.971 (n=4446, CI 0.965–0.975)
Character leak rate: **0.0515** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.47 · p95 5.35 · p99 11.05 · max 18.3
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0

## E-ADV — nbsp

Items: 3227

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 450 | 1.000 | 0.992–1.000 | 0.951 | 0.98/0.9/150 | PASS |
| BANK_ACCOUNT | 1536 | 0.605 | 0.581–0.630 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 739 | 1.000 | 0.995–1.000 | 0.970 | 0.98/0.9/150 | PASS |
| COMPENSATION | 3241 | 0.816 | 0.803–0.829 | 0.999 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1211 | 1.000 | 0.997–1.000 | 0.949 | — | no gate |
| EMAIL | 1598 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 4192 | 1.000 | 0.999–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| IFSC | 1384 | 1.000 | 0.997–1.000 | 1.000 | — | no gate |
| PAN | 1178 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 11453 | 0.945 | 0.940–0.949 | 0.965 | 0.9/0.85/400 | PASS |
| PHONE | 1573 | 1.000 | 0.998–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 714 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| UPI_ID | 450 | 1.000 | 0.992–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.768 (n=607, CI 0.732–0.800), mixed 0.923 (n=6400, CI 0.916–0.929), roster 1.000 (n=4446, CI 0.999–1.000)
Character leak rate: **0.0655** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.51 · p95 5.44 · p99 10.97 · max 17.0
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
