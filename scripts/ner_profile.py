"""NER profiling (synthetic text only). Prints content-free numbers. Usage: python scripts/ner_profile.py"""
from __future__ import annotations
import os, sys, time, threading, statistics, resource
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from load_test import make_ticket  # noqa: E402
from pgw.detect.ner import NerDetector  # noqa: E402
from pgw.pipeline import _views  # noqa: E402

M = "en_core_web_lg"
det = NerDetector(M)
nlp = det.nlp
det.warm()

def med(f, n=15):
    xs = []
    for _ in range(n):
        t = time.perf_counter(); f(); xs.append((time.perf_counter() - t) * 1000)
    return statistics.median(xs)

def cpu():
    r = resource.getrusage(resource.RUSAGE_SELF); return r.ru_utime + r.ru_stime

print("threads: torch/omp env =", {k: os.environ.get(k) for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")})
try:
    import torch; print("torch threads", torch.get_num_threads())
except Exception as e: print("torch n/a", e)
try:
    from threadpoolctl import threadpool_info; print("threadpools", [(p["internal_api"], p["num_threads"]) for p in threadpool_info()])
except Exception as e: print("threadpoolctl n/a", e)

print("\n[1] single call latency by text size (base view, ms median)")
for size in (256, 1024, 2048, 8192, 32768):
    t = make_ticket(size, 1)
    c0 = cpu(); w0 = time.perf_counter()
    ms = med(lambda: nlp(t), 10)
    util = (cpu() - c0) / (time.perf_counter() - w0)
    print(f"size={size} nlp={ms:.1f}ms detect={med(lambda: det.detect(t), 10):.1f}ms cpu_util={util:.2f}")

print("\n[2] per view, 2048-char ticket")
t = make_ticket(2048, 1)
views = list(_views(t))
for name, v, _ in views:
    print(f"view={name} len={len(v)} nlp={med(lambda: nlp(v), 10):.1f}ms")
ner_views = [v for n, v, _ in views if n in ("base", "casefold_titled", "despaced")]
print("ner views:", [n for n, _, _ in views if n in ("base", "casefold_titled", "despaced")],
      "distinct texts:", len(set(ner_views)), "of", len(ner_views))
print(f"3x sequential detect: {med(lambda: [det.detect(v) for v in ner_views], 8):.1f}ms; "
      f"distinct only: {med(lambda: [det.detect(v) for v in set(ner_views)], 8):.1f}ms; "
      f"pipe(batch of 3): {med(lambda: list(nlp.pipe(ner_views, batch_size=8)), 8):.1f}ms")
print("pipe overhead: nlp(x)x3 vs pipe", med(lambda: [nlp(v) for v in ner_views], 8))

print("\n[3] thread scaling: K threads each doing N nlp() calls of 2048 chars")
N = 12
for K in (1, 2, 4, 8):
    lat = []
    def w():
        for _ in range(N):
            a = time.perf_counter(); nlp(t); lat.append((time.perf_counter() - a) * 1000)
    c0 = cpu(); t0 = time.perf_counter()
    ths = [threading.Thread(target=w) for _ in range(K)]
    [x.start() for x in ths]; [x.join() for x in ths]
    wall = time.perf_counter() - t0
    print(f"threads={K} calls/s={K*N/wall:.1f} mean_lat={statistics.fmean(lat):.0f}ms p95={sorted(lat)[int(.95*len(lat))-1]:.0f}ms cpu_util={(cpu()-c0)/wall:.2f}")

print("\n[4] views on an obfuscated ticket")
t2 = "ticket: ᴘriya  n a i r (KD-11914) wrote: Raised by IMRAN ANSARI. " + make_ticket(1900, 2)
vs = list(_views(t2))
print([(n, len(v)) for n, v, _ in vs], "distinct:", len({v for _, v, _ in vs}))
