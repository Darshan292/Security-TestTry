## E-DET — test2

Items: 1755

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.789 | 0.757–0.818 | 0.994 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.940 | 0.923–0.953 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.927 | 0.912–0.939 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 1.000 | 0.996–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.828 | 0.815–0.839 | 1.000 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 0.924 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.555 (n=669, CI 0.517–0.592), mixed 0.710 (n=1247, CI 0.684–0.734), roster 0.998 (n=1932, CI 0.995–0.999)
Character leak rate: **0.0824** (gate ≤ 0.01)
Negatives: 43/420 items with ≥1 false positive; by type {'SECRET': 42, 'EMPLOYEE_ID': 1}
Latency (detect only, CPU, ms): p50 3.31 · p95 9.21 · p99 31.01 · max 44.7
Stream round-trip (random chunking): exact 1753/1755, case-insensitive 1753/1755
Document exposure (>= 1 raw gold value in outbound body): **30.09%** (528/1755)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

## E-ADV — homoglyph

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.789 | 0.757–0.818 | 0.994 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.940 | 0.923–0.953 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.927 | 0.912–0.939 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 1.000 | 0.996–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.822 | 0.810–0.834 | 1.000 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.549 (n=669, CI 0.511–0.586), mixed 0.698 (n=1247, CI 0.672–0.723), roster 0.997 (n=1932, CI 0.994–0.999)
Character leak rate: **0.0831** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 3.56 · p95 10.22 · p99 36.23 · max 50.3
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **39.93%** (533/1335)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

## E-ADV — zero_width

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.789 | 0.757–0.818 | 0.994 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.940 | 0.923–0.953 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.927 | 0.912–0.939 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 0.999 | 0.994–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.823 | 0.811–0.835 | 0.999 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.547 (n=669, CI 0.509–0.584), mixed 0.701 (n=1247, CI 0.675–0.726), roster 0.997 (n=1932, CI 0.994–0.999)
Character leak rate: **0.0833** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 3.72 · p95 10.85 · p99 40.76 · max 51.3
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **40.00%** (534/1335)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

## E-ADV — spaced

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.789 | 0.757–0.818 | 0.994 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.940 | 0.923–0.953 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.926 | 0.912–0.938 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 1.000 | 0.996–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.822 | 0.809–0.833 | 1.000 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.547 (n=669, CI 0.509–0.584), mixed 0.697 (n=1247, CI 0.671–0.722), roster 0.997 (n=1932, CI 0.994–0.999)
Character leak rate: **0.0970** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 6.47 · p95 19.08 · p99 51.96 · max 68.9
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **40.07%** (535/1335)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

## E-ADV — obfuscated_email

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.789 | 0.757–0.818 | 0.994 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.940 | 0.923–0.953 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.926 | 0.912–0.938 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 1.000 | 0.996–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.828 | 0.815–0.839 | 1.000 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.555 (n=669, CI 0.517–0.592), mixed 0.710 (n=1247, CI 0.684–0.734), roster 0.998 (n=1932, CI 0.995–0.999)
Character leak rate: **0.0780** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 4.36 · p95 15.05 · p99 35.25 · max 45.8
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **39.55%** (528/1335)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

## E-ADV — case

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.789 | 0.757–0.818 | 0.994 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.940 | 0.923–0.953 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.927 | 0.912–0.939 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 1.000 | 0.996–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.713 | 0.699–0.727 | 1.000 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.495 (n=669, CI 0.457–0.533), mixed 0.562 (n=1247, CI 0.534–0.589), roster 0.886 (n=1932, CI 0.871–0.900)
Character leak rate: **0.1047** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 3.59 · p95 10.97 · p99 36.38 · max 49.5
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **51.24%** (684/1335)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

## E-ADV — nbsp

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.997 | 0.989–0.999 | 0.990 | 0.98/0.9/150 | PASS |
| BANK_ACCOUNT | 1010 | 0.898 | 0.878–0.915 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 0.953 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.862 | 0.844–0.879 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 1.000 | 0.996–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.828 | 0.815–0.839 | 1.000 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.988 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.555 (n=669, CI 0.517–0.592), mixed 0.710 (n=1247, CI 0.684–0.734), roster 0.998 (n=1932, CI 0.995–0.999)
Character leak rate: **0.0902** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 4.34 · p95 15.69 · p99 50.40 · max 84.6
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **44.94%** (600/1335)
Egress invariant: fires 5, true catches 5, false alarms 0 (gate: 0 false alarms; never a recall metric)

## E-ADV held-out — mixed_separators

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.763 | 0.729–0.793 | 0.992 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.905 | 0.885–0.922 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 0.994 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.925 | 0.911–0.938 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.623 | 0.595–0.651 | 1.000 | — | no gate |
| EMAIL | 1237 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 1.000 | 0.996–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.828 | 0.815–0.839 | 0.997 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.986 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.555 (n=669, CI 0.517–0.592), mixed 0.710 (n=1247, CI 0.684–0.734), roster 0.998 (n=1932, CI 0.995–0.999)
Character leak rate: **0.0914** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 4.17 · p95 14.55 · p99 47.84 · max 86.3
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **47.79%** (638/1335)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

## E-ADV held-out — native_digits

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.789 | 0.757–0.818 | 0.994 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.940 | 0.923–0.953 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.927 | 0.912–0.939 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 1.000 | 0.996–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.828 | 0.815–0.839 | 1.000 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.555 (n=669, CI 0.517–0.592), mixed 0.710 (n=1247, CI 0.684–0.734), roster 0.998 (n=1932, CI 0.995–0.999)
Character leak rate: **0.0824** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 3.54 · p95 10.61 · p99 35.64 · max 45.7
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **39.55%** (528/1335)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

## E-ADV held-out — spoken_email

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.789 | 0.757–0.818 | 0.994 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.940 | 0.923–0.953 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.925 | 0.910–0.937 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 0.790 | 0.766–0.812 | 1.000 | 0.98/0.95/150 | FAIL |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 1.000 | 0.996–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.828 | 0.815–0.839 | 0.945 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.555 (n=669, CI 0.517–0.592), mixed 0.710 (n=1247, CI 0.684–0.734), roster 0.998 (n=1932, CI 0.995–0.999)
Character leak rate: **0.1226** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 3.93 · p95 15.51 · p99 36.91 · max 47.0
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **45.09%** (602/1335)
Egress invariant: fires 19, true catches 2, false alarms 17 (gate: 0 false alarms; never a recall metric)

## E-ADV held-out — leetspeak_name

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.789 | 0.757–0.818 | 0.994 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.940 | 0.923–0.953 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.927 | 0.912–0.939 | 0.999 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 1.000 | 0.996–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.210 | 0.198–0.223 | 1.000 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 0.988 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.133 (n=669, CI 0.109–0.161), mixed 0.141 (n=1247, CI 0.123–0.162), roster 0.282 (n=1932, CI 0.262–0.302)
Character leak rate: **0.2447** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 3.35 · p95 10.17 · p99 27.48 · max 34.0
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **77.30%** (1032/1335)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

## E-ADV held-out — line_wrap

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.000 | 0.000–0.006 | 0.000 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.058 | 0.046–0.075 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 0.006 | 0.001–0.034 | 0.200 | 0.98/0.9/150 | FAIL |
| COMPENSATION | 1489 | 0.743 | 0.721–0.765 | 0.973 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.030 | 0.022–0.042 | 1.000 | — | no gate |
| EMAIL | 1237 | 0.468 | 0.440–0.496 | 0.814 | 0.98/0.95/150 | FAIL |
| EMPLOYEE_ID | 2024 | 0.019 | 0.014–0.026 | 0.487 | 0.98/0.95/150 | FAIL |
| IFSC | 987 | 0.000 | -0.000–0.004 | — | — | no gate |
| PAN | 1044 | 0.000 | 0.000–0.004 | — | 0.98/0.9/150 | FAIL |
| PERSON | 3848 | 0.700 | 0.685–0.714 | 0.928 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 0.154 | 0.139–0.170 | 0.842 | 0.98/0.95/150 | FAIL |
| SECRET | 600 | 0.473 | 0.434–0.513 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 0.537 | 0.474–0.598 | 1.000 | 0.98/0.9/150 | FAIL |

PERSON recall by name source: external 0.084 (n=669, CI 0.065–0.107), mixed 0.569 (n=1247, CI 0.541–0.596), roster 0.998 (n=1932, CI 0.995–0.999)
Character leak rate: **0.7347** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 2.84 · p95 9.03 · p99 19.24 · max 23.4
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **94.23%** (1258/1335)
Egress invariant: fires 249, true catches 1, false alarms 248 (gate: 0 false alarms; never a recall metric)

## E-ADV held-out — ocr_confusion

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.078 | 0.060–0.101 | 1.000 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.000 | 0.000–0.004 | — | 0.98/0.9/150 | FAIL |
| CARD | 161 | 0.000 | 0.000–0.023 | — | 0.98/0.9/150 | FAIL |
| COMPENSATION | 1489 | 0.926 | 0.912–0.938 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 1.000 | 0.997–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| EMPLOYEE_ID | 2024 | 0.361 | 0.341–0.382 | 0.947 | 0.98/0.95/150 | FAIL |
| IFSC | 987 | 0.221 | 0.196–0.248 | 1.000 | — | no gate |
| PAN | 1044 | 0.020 | 0.013–0.031 | 0.189 | 0.98/0.9/150 | FAIL |
| PERSON | 3848 | 0.828 | 0.815–0.839 | 1.000 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 0.044 | 0.036–0.054 | 1.000 | 0.98/0.95/150 | FAIL |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.555 (n=669, CI 0.517–0.592), mixed 0.710 (n=1247, CI 0.684–0.734), roster 0.998 (n=1932, CI 0.995–0.999)
Character leak rate: **0.4262** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 3.02 · p95 8.97 · p99 19.35 · max 25.0
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **86.82%** (1159/1335)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

## E-ADV held-out — html_entity

Items: 1335

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| AADHAAR | 678 | 0.789 | 0.757–0.818 | 0.994 | 0.98/0.9/150 | FAIL |
| BANK_ACCOUNT | 1010 | 0.938 | 0.921–0.951 | 1.000 | 0.98/0.9/150 | FAIL |
| CARD | 161 | 1.000 | 0.977–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| COMPENSATION | 1489 | 0.669 | 0.645–0.692 | 1.000 | 0.95/0.85/200 | FAIL |
| DATE_OF_BIRTH | 1163 | 0.756 | 0.730–0.780 | 1.000 | — | no gate |
| EMAIL | 1237 | 0.112 | 0.096–0.131 | 1.000 | 0.98/0.95/150 | FAIL |
| EMPLOYEE_ID | 2024 | 1.000 | 0.997–1.000 | 0.980 | 0.98/0.95/150 | PASS |
| IFSC | 987 | 1.000 | 0.996–1.000 | 1.000 | — | no gate |
| PAN | 1044 | 1.000 | 0.996–1.000 | 1.000 | 0.98/0.9/150 | PASS |
| PERSON | 3848 | 0.240 | 0.227–0.254 | 0.889 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.135 (n=669, CI 0.111–0.162), mixed 0.167 (n=1247, CI 0.147–0.189), roster 0.324 (n=1932, CI 0.304–0.345)
Character leak rate: **0.5248** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 3.39 · p95 9.97 · p99 30.07 · max 47.7
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **82.92%** (1107/1335)
Egress invariant: fires 42, true catches 0, false alarms 42 (gate: 0 false alarms; never a recall metric)

