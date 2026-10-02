# NER profile (synthetic text, content-free)

Host: 4 CPUs, spaCy 3.8.16, thinc 8.3.13, numpy 2.4.6 (scipy-openblas), torch 2.14 (4 threads by default), model en_core_web_lg
(parser/lemmatizer/tagger/attribute_ruler/senter disabled). Script: `scripts/ner_profile.py`. No OMP/MKL/OPENBLAS env set by default.

## Single call (no contention), median ms
| chars | nlp() | NerDetector.detect |
|---|---|---|
| 256 | 9 | 9 |
| 1024 | 28 | 29 |
| 2048 | 53 | 55 |
| 8192 | 202 | 202 |
| 32768 | 821 | 812 |

Linear, ~0.025 ms/char, CPU utilisation 1.00 core (single-threaded inference). Per-call spaCy `pipe` overhead is negligible
(`nlp(x)` x3 56 ms vs `pipe([x]*3)` 55 ms).

## Concurrency: K threads each calling `nlp()` on a 2048-char text (12 calls each)
| threads | calls/s | mean latency | p95 | process CPU (cores) |
|---|---|---|---|---|
| 1 | 17.4-18.1 | 55 ms | 56 ms | 1.00 |
| 2 | 7.5-8.8 | 227-264 ms | 298-314 ms | 1.28 |
| 4 | 3.0-5.2 | 763-1317 ms | 1051-3528 ms | 1.52-1.56 |
| 8 | 5.4-7.5 | 1030-1456 ms | 1723-3541 ms | 1.51 |

Aggregate throughput FALLS 3-6x as threads are added although 4 cores exist (CPU use stays about 1.5 cores): this is GIL contention
plus BLAS thread oversubscription. With `OMP/MKL/OPENBLAS_NUM_THREADS=1` the same test gives 13.3 / 10.1 / 13.6 calls/s at 2/4/8 threads
(about 2x better, mean latency 146 / 388 / 558 ms) but is still below the 1-thread rate, i.e. the GIL (spaCy/thinc Python-level
work) remains the larger part; threads cannot scale this workload. torch reports 4 threads by default (pinned to 1 in the pool workers).

## Per-view repetition and redundancy
`Protector.detect_text` runs NER on views named base / casefold_titled / despaced. For a normal ticket `_views` returns only the "base"
view (1 NER call). For an obfuscated ticket (homoglyph + spaced letters) it returns base + despaced (2 distinct texts of ~1.96k chars,
so ~2x cost, ~110 ms). The 3x repetition therefore only happens for inputs that produce differing views; identical view texts are
not de-duplicated by the old code (each view string is a separate `detect`), now they are deduped and batched in one call
(`NerDetector.detect_many`, `NerPool.detect_many`).

## Root cause of the load-test collapse
Per request NER costs ~55-80 ms of GIL-bound CPU (see 8 workers x 2 KB). With 8 threads the aggregate rate drops to ~4-7 NER calls/s
(vs 18/s single-thread), so a burst of 8 requests takes 1.5-3 s each (observed p50 1.5 s, p99 up to 6.6 s at concurrency 8);
requests exceed `protect_timeout_s` (10 s) once queued behind slower neighbours, timed-out workers keep their slots, and the rest are
rejected with gateway_busy.
