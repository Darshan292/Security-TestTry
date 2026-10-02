"""In-process load test of the gateway (ASGI, single host, mock upstream). Synthetic text only.

    python scripts/load_test.py --concurrency 1 8 32 --requests 200 --size 2048 [--ner en_core_web_lg] [--out eval/results/load_test.md]

Measures end-to-end handler latency including protection, egress check and restore; NOT network or a real LLM.
Results are content-free (latencies and status counts only).
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pgw.gateway.app import create_app  # noqa: E402
from pgw.gateway.settings import Settings  # noqa: E402
from pgw.gateway.upstream import MockLLMUpstream  # noqa: E402

KEY = "pilot-hr-helpdesk-key-change-me"
SUBJECTS = {"requester": {"name": "Imran Ansari", "employee_id": "KD-11914"}}
FILLER = ("Ticket PAY-90412 | Raised by: Imran Ansari (KD-11914). The August arrears were not credited; "
          "payroll re-ran the file and the amount is still missing from the payslip. Please check the cycle. ")


def make_ticket(size: int, i: int) -> str:
    text = f"Request {i}. " + FILLER * (size // len(FILLER) + 1)
    return text[:size]


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


async def run_level(ner: str | None, conc: int, n: int, size: int, workers: int = 0) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        s = Settings(registry_path=str(ROOT / "registry/apps.yaml"), policy_path=str(ROOT / "policies/hris-default.yaml"),
                     roster_path=str(ROOT / "tests/fixtures/slice_roster.jsonl"),
                     ledger_db_path=f"{tmp}/ledger.sqlite", egress_ledger_path=f"{tmp}/egress.jsonl",
                     master_key=os.urandom(32).hex(), upstream=MockLLMUpstream("summarise"), ner_model=ner, ner_workers=workers)
        app = create_app(s)
        sem = asyncio.Semaphore(conc)
        lat: list[float] = []
        codes: collections.Counter = collections.Counter()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw", timeout=120) as c:
            async def one(i: int) -> None:
                body = {"model": "mock-1", "messages": [{"role": "user", "content": make_ticket(size, i)}],
                        "pgw_subjects": SUBJECTS}
                h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary"}
                async with sem:
                    t = time.perf_counter()
                    r = await c.post("/v1/chat/completions", headers=h, json=body)
                    d = time.perf_counter() - t
                key = str(r.status_code)
                if r.status_code != 200:
                    try:
                        e = r.json().get("error", {})
                        key += ":" + str(e.get("code") or e.get("type") or e.get("message", ""))[:30] if isinstance(e, dict) else ""
                    except ValueError:
                        pass
                codes[key] += 1
                if r.status_code == 200:
                    lat.append(d)
            await one(-1)      # warm-up (model load, caches); counted but not timed below
            codes.clear(); lat.clear()
            t0 = time.perf_counter()
            await asyncio.gather(*(one(i) for i in range(n)))
            wall = time.perf_counter() - t0
    import multiprocessing as _mp
    for ch in _mp.active_children():      # NerPool workers of this level
        ch.kill()
    ms = [x * 1000 for x in lat]
    return {"ner": (ner or "off") + (f" pool x{workers}" if ner and workers else " in-process" if ner else ""), "conc": conc, "n": n, "size": size, "wall": wall, "rps": len(lat) / wall,
            "ok": len(ms), "p50": pct(ms, 50) if ms else float("nan"), "p95": pct(ms, 95) if ms else float("nan"),
            "p99": pct(ms, 99) if ms else float("nan"), "mean": statistics.fmean(ms) if ms else float("nan"),
            "codes": dict(sorted(codes.items()))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, nargs="+", default=[1, 8, 32])
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--size", type=int, default=2048, help="ticket size in characters")
    ap.add_argument("--ner", nargs="*", default=["none"], help="ner_model values to test; 'none' = off")
    ap.add_argument("--ner-workers", type=int, default=0, help="0 = in-process NerDetector threads; N = NerPool processes")
    ap.add_argument("--repeat", type=int, default=1, help="repeat each point this many times (variance)")
    ap.add_argument("--append", action="store_true", help="append a dated section to --out instead of overwriting")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rows = []
    for ner in a.ner:
        for c in a.concurrency:
          for rep in range(a.repeat):
            r = asyncio.run(run_level(None if ner == "none" else ner, c, a.requests, a.size, a.ner_workers))
            r["rep"] = rep + 1
            rows.append(r)
            print(f"ner={r['ner']} rep={rep + 1} conc={c} rps={r['rps']:.1f} p50={r['p50']:.0f}ms p95={r['p95']:.0f}ms "
                  f"p99={r['p99']:.0f}ms codes={r['codes']}", flush=True)
    if a.out and a.append:
        import datetime
        lines = ["", f"## {datetime.date.today().isoformat()}: {a.requests} requests per row, repeat x{a.repeat}", "",
                 "| NER | concurrency | run | successful (200) req/s | p50 ms | p95 ms | p99 ms | status counts |",
                 "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['ner']} | {r['conc']} | {r['rep']} | {r['rps']:.1f} | {r['p50']:.0f} | {r['p95']:.0f} | "
                         f"{r['p99']:.0f} | {r['codes']} |")
        with open(a.out, "a") as f:
            f.write("\n".join(lines) + "\n")
    elif a.out:
        lines = ["# Gateway load test (content-free)", "",
                 "In-process (httpx ASGITransport, no network), single host, mock upstream (`MockLLMUpstream('summarise')`), "
                 f"synthetic ~{a.size}-char tickets, {a.requests} requests per row after one warm-up. "
                 "Latency and throughput count 200 responses only; with NER on, concurrent protection contends for the GIL and can exceed protect_timeout_s (protection_timeout), and timed-out workers hold their slot, producing further gateway_busy.  503 `gateway_busy` is the worker-slot limit "
                 "(`max_concurrent_protect`) working as designed. Not a capacity claim: no real LLM latency, TLS, or network; "
                 "all clients share one event loop and one Python process with the gateway.", "",
                 "| NER | concurrency | requests | successful (200) req/s | p50 ms | p95 ms | p99 ms | status counts |",
                 "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['ner']} | {r['conc']} | {r['n']} | {r['rps']:.1f} | {r['p50']:.0f} | {r['p95']:.0f} | "
                         f"{r['p99']:.0f} | {r['codes']} |")
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
