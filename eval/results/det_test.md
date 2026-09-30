## E-DET — test

Items: 1240

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 232 | 0.918 | 0.876–0.947 | 1.000 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 241 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 228 | 1.000 | 0.983–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1125 | 1.000 | 0.997–1.000 | 0.860 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 317 | 1.000 | 0.988–1.000 | 0.988 | — | no gate |
| EMAIL | 720 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 812 | 1.000 | 0.995–1.000 | 0.995 | 0.98/0.95/150 | PASS |
| IFSC | 322 | 1.000 | 0.988–1.000 | 1.000 | — | no gate |
| PAN | 395 | 1.000 | 0.990–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 2911 | 0.922 | 0.912–0.931 | 0.887 | 0.9/0.85/400 | PASS |
| PHONE | 597 | 1.000 | 0.994–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 321 | 0.916 | 0.880–0.942 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 206 | 1.000 | 0.982–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.931 (n=72, CI 0.848–0.970), mixed 0.854 (n=1519, CI 0.835–0.871), roster 1.000 (n=1320, CI 0.997–1.000)
Character leak rate: **0.0256** (gate ≤ 0.01)
Negatives: 164/250 items with ≥1 false positive; by type {'PERSON': 258, 'COMPENSATION': 79}
Latency (detect only, CPU, ms): p50 2.32 · p95 4.17 · p99 6.85 · max 8.5
Stream round-trip (random chunking): exact 1213/1240, case-insensitive 1213/1240

## E-ADV — homoglyph

Items: 990

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 232 | 0.918 | 0.876–0.947 | 1.000 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 241 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 228 | 1.000 | 0.983–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1125 | 1.000 | 0.997–1.000 | 0.915 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 317 | 1.000 | 0.988–1.000 | 0.988 | — | no gate |
| EMAIL | 720 | 0.996 | 0.988–0.999 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 812 | 1.000 | 0.995–1.000 | 0.995 | 0.98/0.95/150 | PASS |
| IFSC | 322 | 1.000 | 0.988–1.000 | 1.000 | — | no gate |
| PAN | 395 | 1.000 | 0.990–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 2911 | 0.850 | 0.836–0.862 | 0.966 | 0.9/0.85/400 | FAIL |
| PHONE | 597 | 1.000 | 0.994–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 321 | 0.916 | 0.880–0.942 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 206 | 1.000 | 0.982–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.903 (n=72, CI 0.813–0.952), mixed 0.722 (n=1519, CI 0.699–0.744), roster 0.993 (n=1320, CI 0.987–0.996)
Character leak rate: **0.0424** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.39 · p95 4.85 · p99 7.57 · max 11.7
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0

## E-ADV — zero_width

Items: 990

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 232 | 0.918 | 0.876–0.947 | 1.000 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 241 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 228 | 1.000 | 0.983–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1125 | 1.000 | 0.997–1.000 | 0.915 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 317 | 0.776 | 0.727–0.818 | 0.984 | — | no gate |
| EMAIL | 720 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 812 | 1.000 | 0.995–1.000 | 0.995 | 0.98/0.95/150 | PASS |
| IFSC | 322 | 1.000 | 0.988–1.000 | 1.000 | — | no gate |
| PAN | 395 | 1.000 | 0.990–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 2911 | 0.856 | 0.843–0.868 | 0.966 | 0.9/0.85/400 | FAIL |
| PHONE | 597 | 1.000 | 0.994–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 321 | 0.916 | 0.880–0.942 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 206 | 1.000 | 0.982–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.903 (n=72, CI 0.813–0.952), mixed 0.730 (n=1519, CI 0.707–0.752), roster 0.998 (n=1320, CI 0.994–1.000)
Character leak rate: **0.0522** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.33 · p95 4.70 · p99 7.10 · max 9.5
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0

## E-ADV — spaced

Items: 990

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 232 | 0.918 | 0.876–0.947 | 1.000 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 241 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 228 | 1.000 | 0.983–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1125 | 1.000 | 0.997–1.000 | 0.915 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 317 | 1.000 | 0.988–1.000 | 0.988 | — | no gate |
| EMAIL | 720 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 812 | 1.000 | 0.995–1.000 | 0.995 | 0.98/0.95/150 | PASS |
| IFSC | 322 | 1.000 | 0.988–1.000 | 1.000 | — | no gate |
| PAN | 395 | 1.000 | 0.990–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 2911 | 0.143 | 0.130–0.156 | 0.830 | 0.9/0.85/400 | FAIL |
| PHONE | 597 | 1.000 | 0.994–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 321 | 0.916 | 0.880–0.942 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 206 | 1.000 | 0.982–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.667 (n=72, CI 0.552–0.765), mixed 0.082 (n=1519, CI 0.070–0.097), roster 0.183 (n=1320, CI 0.163–0.205)
Character leak rate: **0.3800** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.80 · p95 5.71 · p99 7.92 · max 12.0
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0

## E-ADV — obfuscated_email

Items: 990

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 232 | 0.918 | 0.876–0.947 | 1.000 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 241 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 228 | 1.000 | 0.983–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1125 | 1.000 | 0.997–1.000 | 0.915 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 317 | 1.000 | 0.988–1.000 | 0.991 | — | no gate |
| EMAIL | 720 | 0.311 | 0.278–0.346 | 1.000 | 0.98/0.95/150 | FAIL |
| EMPLOYEE_ID | 812 | 1.000 | 0.995–1.000 | 0.995 | 0.98/0.95/150 | PASS |
| IFSC | 322 | 1.000 | 0.988–1.000 | 1.000 | — | no gate |
| PAN | 395 | 1.000 | 0.990–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 2911 | 0.922 | 0.912–0.931 | 0.793 | 0.9/0.85/400 | FAIL |
| PHONE | 597 | 1.000 | 0.994–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 321 | 0.916 | 0.880–0.942 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 206 | 1.000 | 0.982–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.931 (n=72, CI 0.848–0.970), mixed 0.854 (n=1519, CI 0.835–0.871), roster 1.000 (n=1320, CI 0.997–1.000)
Character leak rate: **0.1442** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.25 · p95 4.46 · p99 6.89 · max 8.5
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0

## E-ADV — case

Items: 990

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 232 | 0.918 | 0.876–0.947 | 1.000 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 241 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| CARD | 228 | 1.000 | 0.983–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1125 | 1.000 | 0.997–1.000 | 0.915 | 0.95/0.85/200 | PASS |
| DATE_OF_BIRTH | 317 | 1.000 | 0.988–1.000 | 0.988 | — | no gate |
| EMAIL | 720 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 812 | 1.000 | 0.995–1.000 | 0.995 | 0.98/0.95/150 | PASS |
| IFSC | 322 | 1.000 | 0.988–1.000 | 1.000 | — | no gate |
| PAN | 395 | 1.000 | 0.990–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 2911 | 0.787 | 0.772–0.802 | 0.941 | 0.9/0.85/400 | FAIL |
| PHONE | 597 | 1.000 | 0.994–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 321 | 0.916 | 0.880–0.942 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 206 | 1.000 | 0.982–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.806 (n=72, CI 0.700–0.880), mixed 0.623 (n=1519, CI 0.599–0.647), roster 0.975 (n=1320, CI 0.965–0.982)
Character leak rate: **0.0558** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.15 · p95 4.10 · p99 6.68 · max 8.0
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0

## E-ADV — nbsp

Items: 990

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 232 | 1.000 | 0.984–1.000 | 0.975 | 0.98/0.9/150 | PASS |
| BANK_ACCOUNT | 241 | 0.635 | 0.572–0.693 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 228 | 1.000 | 0.983–1.000 | 0.974 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1125 | 0.892 | 0.872–0.908 | 0.906 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 317 | 1.000 | 0.988–1.000 | 0.988 | — | no gate |
| EMAIL | 720 | 1.000 | 0.995–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 812 | 1.000 | 0.995–1.000 | 0.995 | 0.98/0.95/150 | PASS |
| IFSC | 322 | 1.000 | 0.988–1.000 | 1.000 | — | no gate |
| PAN | 395 | 1.000 | 0.990–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 2911 | 0.922 | 0.912–0.931 | 0.970 | 0.9/0.85/400 | PASS |
| PHONE | 597 | 1.000 | 0.994–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| SECRET | 321 | 0.916 | 0.880–0.942 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 206 | 1.000 | 0.982–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.931 (n=72, CI 0.848–0.970), mixed 0.854 (n=1519, CI 0.835–0.871), roster 1.000 (n=1320, CI 0.997–1.000)
Character leak rate: **0.0564** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.14 · p95 4.32 · p99 6.75 · max 9.9
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
