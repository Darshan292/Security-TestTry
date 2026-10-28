"""Benchmark any OpenAI-compatible chat endpoint (llama.cpp llama-server, vLLM, Ollama, pgw.private_llm) on YOUR
hardware: latency, time to first token and generation speed, at a given concurrency.

    python scripts/bench_private_llm.py --url http://127.0.0.1:8090/v1 --model qwen2.5-7b-instruct \
        --requests 20 --concurrency 2 --max-tokens 256 [--stream] [--prompt-file prompts.txt] [--json out.json]

Prompts: synthetic HR-ticket-like texts built in (no personal data), or one prompt per line of --prompt-file. Run it
against the in-boundary endpoint from inside the boundary; it sends nothing anywhere else (no proxy from the
environment). Reported per run: latency p50/p95/max, time to first token p50/p95 (--stream only), completion tokens
per second per request (usage.completion_tokens / generation time; with --stream and no usage, counted chunks), and
aggregate throughput (all completion tokens / wall time). Failed requests are counted, never dropped.

Numbers are only meaningful for the machine, model file, quantisation, context size and thread settings used: record
them with the result (docs/ML_ARCHITECTURE.md, "CPU budget").
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

PROMPTS = [
    "Summarise this HR ticket in two sentences: An employee asks why the March payslip shows a lower HRA than "
    "February, and whether the rent receipts uploaded on the portal were considered.",
    "Draft a polite reply to an employee who wants to know how many casual leaves carry over to next year under "
    "the leave policy, and where to check the current balance.",
    "Classify this request as payroll, leave, benefits or other, and explain in one line: My reimbursement claim "
    "for the certification exam is pending approval for three weeks.",
    "Rewrite this note in plain English for a new joiner: Form 16 Part B is issued by the employer after Q4 TDS "
    "returns are filed; Part A is downloaded from TRACES.",
]


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 1)


def one(client: httpx.Client, url: str, model: str, prompt: str, max_tokens: int, stream: bool,
        api_key: str | None) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "temperature": 0.0, "stream": stream}
    if stream:
        body["stream_options"] = {"include_usage": True}
    h = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    t0 = time.perf_counter()
    ttft, tokens, chunks = None, None, 0
    try:
        if not stream:
            r = client.post(f"{url}/chat/completions", json=body, headers=h)
            r.raise_for_status()
            data = r.json()
            tokens = (data.get("usage") or {}).get("completion_tokens")
        else:
            with client.stream("POST", f"{url}/chat/completions", json=body, headers=h) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    ev = json.loads(line[6:])
                    if ev.get("usage"):
                        tokens = ev["usage"].get("completion_tokens", tokens)
                    delta = ((ev.get("choices") or [{}])[0].get("delta") or {}).get("content")
                    if delta:
                        chunks += 1
                        if ttft is None:
                            ttft = time.perf_counter() - t0
    except (httpx.HTTPError, ValueError) as e:
        return {"ok": False, "error": type(e).__name__}
    total = time.perf_counter() - t0
    tokens = tokens if tokens is not None else (chunks or None)
    gen = total - (ttft or 0.0)
    return {"ok": True, "latency_s": total, "ttft_s": ttft, "tokens": tokens,
            "tok_per_s": (tokens / gen) if tokens and gen > 0 else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="base URL ending in /v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--requests", type=int, default=12)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--stream", action="store_true", help="measure time to first token")
    ap.add_argument("--prompt-file")
    ap.add_argument("--api-key")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--json", help="also write the summary here")
    a = ap.parse_args(argv)
    url = a.url.rstrip("/")
    prompts = PROMPTS
    if a.prompt_file:
        with open(a.prompt_file, encoding="utf-8") as fh:
            prompts = [line.strip() for line in fh if line.strip()]
    client = httpx.Client(timeout=a.timeout, trust_env=False)
    try:
        ident = client.get(f"{url}/models").json()
    except (httpx.HTTPError, ValueError) as e:
        print(f"endpoint not reachable: {type(e).__name__}", file=sys.stderr)
        return 1
    for i in range(a.warmup):
        one(client, url, a.model, prompts[i % len(prompts)], min(32, a.max_tokens), False, a.api_key)
    results: list[dict] = []
    lock = threading.Lock()

    def job(i: int) -> None:
        r = one(client, url, a.model, prompts[i % len(prompts)], a.max_tokens, a.stream, a.api_key)
        with lock:
            results.append(r)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, a.concurrency)) as ex:
        list(ex.map(job, range(a.requests)))
    wall = time.perf_counter() - t0
    ok = [r for r in results if r["ok"]]
    lat = [r["latency_s"] * 1000 for r in ok]
    ttft = [r["ttft_s"] * 1000 for r in ok if r["ttft_s"] is not None]
    tps = [r["tok_per_s"] for r in ok if r["tok_per_s"]]
    toks = sum(r["tokens"] or 0 for r in ok)
    summary = {
        "endpoint_models": [m.get("id") for m in (ident.get("data") or []) if isinstance(m, dict)],
        "model": a.model, "requests": a.requests, "concurrency": a.concurrency, "max_tokens": a.max_tokens,
        "stream": a.stream, "ok": len(ok), "failed": len(results) - len(ok),
        "errors": sorted({r["error"] for r in results if not r["ok"]}),
        "latency_ms": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "max": round(max(lat), 1) if lat else None},
        "ttft_ms": {"p50": pct(ttft, 0.5), "p95": pct(ttft, 0.95)} if a.stream else None,
        "tokens_per_s_per_request": {"mean": round(statistics.mean(tps), 2) if tps else None,
                                     "p50": pct(tps, 0.5)},
        "aggregate_tokens_per_s": round(toks / wall, 2) if wall > 0 else None,
        "wall_s": round(wall, 2),
    }
    out = json.dumps(summary, indent=1)
    print(out)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
