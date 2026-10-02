# Gateway load test (content-free)

In-process (httpx ASGITransport, no network), single host, mock upstream (`MockLLMUpstream('summarise')`), synthetic ~2048-char tickets, 200 requests per row after one warm-up. Latency and throughput count 200 responses only; with NER on, concurrent protection contends for the GIL and can exceed protect_timeout_s (protection_timeout), and timed-out workers hold their slot, producing further gateway_busy.  503 `gateway_busy` is the worker-slot limit (`max_concurrent_protect`) working as designed. Not a capacity claim: no real LLM latency, TLS, or network; all clients share one event loop and one Python process with the gateway.

| NER | concurrency | requests | successful (200) req/s | p50 ms | p95 ms | p99 ms | status counts |
|---|---|---|---|---|---|---|---|
| off | 1 | 200 | 47.8 | 20 | 24 | 30 | {'200': 200} |
| off | 8 | 200 | 45.2 | 167 | 233 | 294 | {'200': 200} |
| off | 32 | 200 | 25.9 | 295 | 304 | 304 | {'200': 8, '503:gateway_busy': 192} |
| en_core_web_lg | 1 | 200 | 12.4 | 79 | 89 | 98 | {'200': 200} |
| en_core_web_lg | 8 | 200 | 0.0 | nan | nan | nan | {'503:gateway_busy': 192, '503:protection_timeout': 8} |
| en_core_web_lg | 32 | 200 | 4.6 | 1591 | 1755 | 1755 | {'200': 8, '503:gateway_busy': 192} |

## 2026-10-02: 200 requests per row, repeat x2

| NER | concurrency | run | successful (200) req/s | p50 ms | p95 ms | p99 ms | status counts |
|---|---|---|---|---|---|---|---|
| en_core_web_lg in-process | 1 | 1 | 12.6 | 78 | 86 | 95 | {'200': 200} |
| en_core_web_lg in-process | 1 | 2 | 12.8 | 77 | 84 | 88 | {'200': 200} |
| en_core_web_lg in-process | 4 | 1 | 4.2 | 988 | 1308 | 2304 | {'200': 200} |
| en_core_web_lg in-process | 4 | 2 | 3.9 | 976 | 1264 | 3080 | {'200': 200} |
| en_core_web_lg in-process | 8 | 1 | 5.0 | 1540 | 2447 | 3062 | {'200': 200} |
| en_core_web_lg in-process | 8 | 2 | 3.3 | 1623 | 2329 | 6633 | {'200': 150, '503:gateway_busy': 42, '503:protection_timeout': 8} |
| en_core_web_lg in-process | 16 | 1 | 0.0 | nan | nan | nan | {'503:gateway_busy': 192, '503:protection_timeout': 8} |
| en_core_web_lg in-process | 16 | 2 | 0.0 | nan | nan | nan | {'503:gateway_busy': 193, '503:protection_timeout': 7} |

## 2026-10-02: 200 requests per row, repeat x2

| NER | concurrency | run | successful (200) req/s | p50 ms | p95 ms | p99 ms | status counts |
|---|---|---|---|---|---|---|---|
| en_core_web_lg pool x4 | 1 | 1 | 14.5 | 68 | 77 | 83 | {'200': 200} |
| en_core_web_lg pool x4 | 1 | 2 | 14.8 | 66 | 81 | 84 | {'200': 200} |
| en_core_web_lg pool x4 | 4 | 1 | 30.3 | 130 | 155 | 168 | {'200': 200} |
| en_core_web_lg pool x4 | 4 | 2 | 30.7 | 127 | 152 | 167 | {'200': 200} |
| en_core_web_lg pool x4 | 8 | 1 | 36.9 | 212 | 261 | 289 | {'200': 200} |
| en_core_web_lg pool x4 | 8 | 2 | 35.6 | 219 | 264 | 304 | {'200': 200} |
| en_core_web_lg pool x4 | 16 | 1 | 24.0 | 287 | 329 | 329 | {'200': 8, '503:gateway_busy': 192} |
| en_core_web_lg pool x4 | 16 | 2 | 22.4 | 344 | 354 | 354 | {'200': 8, '503:gateway_busy': 192} |

Notes for the section above: "in-process" = NerDetector called from the gateway's worker threads (old behaviour, retained numbers above);
"pool x4" = `NerPool` with 4 worker processes (BLAS/OMP/torch threads pinned to 1), queue 8, per-call timeout min(8s, 0.8*protect_timeout_s).
The 4-CPU host also runs the load generator, the gateway and the mock upstream in one process, so pool numbers are a lower bound on
what 4 cores give NER alone. Two runs per point; run-to-run spread is small for pool (<=5% rps) and large for in-process at
concurrency 4-8 (p99 2.3-6.6 s; 150-200 of 200 succeed). At concurrency 16 the 8 worker slots (`max_concurrent_protect`) reject the
surplus with 503 gateway_busy by design (all 192 non-200 rows), so successful rps there only counts the 8 admitted requests.
