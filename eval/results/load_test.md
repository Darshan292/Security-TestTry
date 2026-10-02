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
