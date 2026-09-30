/tmp/claude-0/venv/lib/python3.11/site-packages/torch/jit/_script.py:1491: FutureWarning: `torch.jit.script` is deprecated. Please switch to `torch.compile` or `torch.export`.
  warnings.warn(
## E-DET — ner_test2-ner_en_core_web_trf

Items: 425

| Type | n gold | Recall | 95 % CI | Precision | Gate (R/P/min n) | Status |
|---|---:|---:|---|---:|---|---|
| BANK_ACCOUNT | 25 | 1.000 | 0.867–1.000 | 1.000 | 0.98/0.9/150 | UNDERPOWERED |
| COMPENSATION | 100 | 0.790 | 0.700–0.858 | 1.000 | 0.95/0.85/200 | UNDERPOWERED |
| DATE_OF_BIRTH | 25 | 1.000 | 0.867–1.000 | 1.000 | — | no gate |
| EMAIL | 150 | 1.000 | 0.975–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| IFSC | 25 | 1.000 | 0.867–1.000 | 1.000 | — | no gate |
| PAN | 25 | 1.000 | 0.867–1.000 | 1.000 | 0.98/0.9/150 | UNDERPOWERED |
| PERSON | 1500 | 0.844 | 0.825–0.861 | 0.981 | 0.9/0.85/400 | FAIL |
| PHONE | 300 | 1.000 | 0.987–1.000 | 1.000 | 0.98/0.95/150 | PASS |
| UPI_ID | 25 | 1.000 | 0.867–1.000 | 1.000 | 0.98/0.9/150 | UNDERPOWERED |

PERSON recall by name source: external 0.844 (n=1500, CI 0.825–0.861)
Character leak rate: **0.1061** (gate ≤ 0.01)
Negatives: 0/0 items with ≥1 false positive; by type {}
Latency (detect only, CPU, ms): p50 53.30 · p95 115.63 · p99 129.86 · max 4625.6
Stream round-trip (random chunking): exact 422/425, case-insensitive 425/425
Document exposure (>= 1 raw gold value in outbound body): **39.53%** (168/425)
Egress invariant: fires 0, true catches 0, false alarms 0 (gate: 0 false alarms; never a recall metric)

