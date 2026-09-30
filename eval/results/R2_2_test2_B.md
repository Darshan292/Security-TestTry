/tmp/claude-0/venv/lib/python3.11/site-packages/torch/jit/_script.py:1491: FutureWarning: `torch.jit.script` is deprecated. Please switch to `torch.compile` or `torch.export`.
  warnings.warn(
## E-DET — test2-ner_en_core_web_lg

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
| PERSON | 3848 | 0.943 | 0.936–0.950 | 0.940 | 0.9/0.85/400 | PASS |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 0.924 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.828 (n=669, CI 0.798–0.855), mixed 0.918 (n=1247, CI 0.902–0.932), roster 0.999 (n=1932, CI 0.997–1.000)
Character leak rate: **0.0610** (gate ≤ 0.01)
Negatives: 64/420 items with ≥1 false positive; by type {'SECRET': 42, 'PERSON': 126, 'EMPLOYEE_ID': 1}
Latency (detect only, CPU, ms): p50 16.24 · p95 46.60 · p99 132.13 · max 4898.8
Stream round-trip (random chunking): exact 1753/1755, case-insensitive 1753/1755
Document exposure (>= 1 raw gold value in outbound body): **20.11%** (353/1755)
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
| PERSON | 3848 | 0.930 | 0.921–0.937 | 0.970 | 0.9/0.85/400 | PASS |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.794 (n=669, CI 0.761–0.823), mixed 0.895 (n=1247, CI 0.877–0.911), roster 0.999 (n=1932, CI 0.997–1.000)
Character leak rate: **0.0627** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 15.86 · p95 48.68 · p99 136.22 · max 160.8
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **27.04%** (361/1335)
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
| PERSON | 3848 | 0.931 | 0.922–0.938 | 0.969 | 0.9/0.85/400 | PASS |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.792 (n=669, CI 0.760–0.821), mixed 0.898 (n=1247, CI 0.880–0.914), roster 0.999 (n=1932, CI 0.997–1.000)
Character leak rate: **0.0625** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 15.95 · p95 48.50 · p99 140.43 · max 170.2
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **26.89%** (359/1335)
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
| PERSON | 3848 | 0.931 | 0.923–0.939 | 0.970 | 0.9/0.85/400 | PASS |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.801 (n=669, CI 0.769–0.830), mixed 0.897 (n=1247, CI 0.879–0.913), roster 0.998 (n=1932, CI 0.995–0.999)
Character leak rate: **0.0644** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 31.47 · p95 99.23 · p99 290.70 · max 347.6
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **26.82%** (358/1335)
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
| PERSON | 3848 | 0.944 | 0.936–0.951 | 0.971 | 0.9/0.85/400 | PASS |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.833 (n=669, CI 0.802–0.859), mixed 0.918 (n=1247, CI 0.902–0.932), roster 0.999 (n=1932, CI 0.997–1.000)
Character leak rate: **0.0577** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 17.17 · p95 52.11 · p99 136.47 · max 177.0
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **26.44%** (353/1335)
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
| PERSON | 3848 | 0.726 | 0.712–0.740 | 0.960 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.517 (n=669, CI 0.479–0.555), mixed 0.589 (n=1247, CI 0.562–0.616), roster 0.887 (n=1932, CI 0.872–0.901)
Character leak rate: **0.1018** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 15.66 · p95 47.12 · p99 134.29 · max 169.4
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **50.11%** (669/1335)
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
| PERSON | 3848 | 0.944 | 0.936–0.951 | 0.971 | 0.9/0.85/400 | PASS |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.988 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.833 (n=669, CI 0.802–0.859), mixed 0.918 (n=1247, CI 0.902–0.932), roster 0.999 (n=1932, CI 0.997–1.000)
Character leak rate: **0.0692** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 16.91 · p95 55.73 · p99 163.49 · max 198.2
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **31.84%** (425/1335)
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
| PERSON | 3848 | 0.943 | 0.935–0.950 | 0.967 | 0.9/0.85/400 | PASS |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.986 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.825 (n=669, CI 0.794–0.852), mixed 0.918 (n=1247, CI 0.902–0.932), roster 0.999 (n=1932, CI 0.997–1.000)
Character leak rate: **0.0707** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 16.42 · p95 52.46 · p99 161.80 · max 198.4
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **34.83%** (465/1335)
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
| PERSON | 3848 | 0.943 | 0.936–0.950 | 0.971 | 0.9/0.85/400 | PASS |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.828 (n=669, CI 0.798–0.855), mixed 0.918 (n=1247, CI 0.902–0.932), roster 0.999 (n=1932, CI 0.997–1.000)
Character leak rate: **0.0610** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 15.77 · p95 49.00 · p99 137.56 · max 196.3
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **26.44%** (353/1335)
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
| PERSON | 3848 | 0.943 | 0.936–0.950 | 0.920 | 0.9/0.85/400 | PASS |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.828 (n=669, CI 0.798–0.855), mixed 0.918 (n=1247, CI 0.902–0.932), roster 0.999 (n=1932, CI 0.997–1.000)
Character leak rate: **0.1018** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 16.31 · p95 50.86 · p99 133.74 · max 159.7
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **32.21%** (430/1335)
Egress invariant: fires 27, true catches 2, false alarms 25 (gate: 0 false alarms; never a recall metric)

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
| PERSON | 3848 | 0.234 | 0.221–0.248 | 0.881 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 0.988 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.160 (n=669, CI 0.134–0.190), mixed 0.192 (n=1247, CI 0.172–0.215), roster 0.287 (n=1932, CI 0.267–0.307)
Character leak rate: **0.2423** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 15.45 · p95 47.30 · p99 129.28 · max 148.8
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **76.40%** (1020/1335)
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
| PERSON | 3848 | 0.864 | 0.852–0.874 | 0.902 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 0.154 | 0.139–0.170 | 0.842 | 0.98/0.95/150 | FAIL |
| SECRET | 600 | 0.473 | 0.434–0.513 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 0.537 | 0.474–0.598 | 1.000 | 0.98/0.9/150 | FAIL |

PERSON recall by name source: external 0.528 (n=669, CI 0.490–0.565), mixed 0.833 (n=1247, CI 0.811–0.853), roster 0.999 (n=1932, CI 0.997–1.000)
Character leak rate: **0.7058** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 16.42 · p95 50.57 · p99 160.05 · max 188.4
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **92.88%** (1240/1335)
Egress invariant: fires 281, true catches 4, false alarms 277 (gate: 0 false alarms; never a recall metric)

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
| PERSON | 3848 | 0.943 | 0.936–0.950 | 0.969 | 0.9/0.85/400 | PASS |
| PHONE | 2042 | 0.044 | 0.036–0.054 | 1.000 | 0.98/0.95/150 | FAIL |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.831 (n=669, CI 0.801–0.858), mixed 0.917 (n=1247, CI 0.901–0.931), roster 0.999 (n=1932, CI 0.996–1.000)
Character leak rate: **0.4048** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 15.53 · p95 49.24 · p99 125.42 · max 150.6
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **85.02%** (1135/1335)
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
| PERSON | 3848 | 0.254 | 0.240–0.268 | 0.801 | 0.9/0.85/400 | FAIL |
| PHONE | 2042 | 1.000 | 0.998–1.000 | 0.989 | 0.98/0.95/150 | PASS |
| SECRET | 600 | 0.848 | 0.817–0.875 | 1.000 | 0.98/0.9/150 | FAIL |
| UPI_ID | 244 | 1.000 | 0.984–1.000 | 1.000 | 0.98/0.9/150 | PASS |

PERSON recall by name source: external 0.166 (n=669, CI 0.140–0.196), mixed 0.192 (n=1247, CI 0.171–0.214), roster 0.325 (n=1932, CI 0.304–0.346)
Character leak rate: **0.5235** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 15.95 · p95 49.55 · p99 134.80 · max 201.4
Stream round-trip (random chunking): exact 0/0, case-insensitive 0/0
Document exposure (>= 1 raw gold value in outbound body): **82.62%** (1103/1335)
Egress invariant: fires 48, true catches 0, false alarms 48 (gate: 0 false alarms; never a recall metric)

