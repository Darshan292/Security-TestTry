#!/usr/bin/env python3
"""CPU latency and RAM of one detector bundle on a ~N-token text (default 512), one process per call (so RSS is clean).

    python scripts/ml/bench_latency.py BUNDLE_DIR --threads 1 [--tokens 512] [--runs 9]

The text is built from the first dev documents (eval/data/dev.jsonl) until the bundle's tokenizer counts N tokens (token
models: model tokens; span models: model tokens of the words, prompt excluded). Prints one JSON line: load seconds, warm
p50/min/max ms per call, peak RSS MB (ru_maxrss) after load and after the runs. Single-request latency of the in-process
runtime; the gateway's worker pool adds queueing and IPC (docs/ML_ARCHITECTURE.md section 6).
"""
from __future__ import annotations

import argparse
import json
import resource
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--tokens", type=int, default=512)
    ap.add_argument("--runs", type=int, default=9)
    a = ap.parse_args(argv)
    from pgw.ml import artifact
    from pgw.ml.span_model import OnnxSpanModel
    from pgw.ml.token_classifier import OnnxTokenClassifier
    t0 = time.perf_counter()
    art = artifact.load(a.bundle, artifact.digest_dir(a.bundle)[0])
    cls = OnnxSpanModel if art.task == "span-classification" else OnnxTokenClassifier
    m = cls(art, intra_op_threads=a.threads)
    load_s = time.perf_counter() - t0
    rss_load = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    text, n = "", 0
    for line in (ROOT / "eval/data/dev.jsonl").open(encoding="utf-8"):
        text += json.loads(line)["text"] + "\n"
        n = len(m.tok.encode(text, add_special_tokens=False).ids)
        if n >= a.tokens:
            break
    ids = m.tok.encode(text, add_special_tokens=False)
    if n > a.tokens:                                  # cut to exactly N tokens on a character boundary
        text = text[:ids.offsets[a.tokens - 1][1]]
        n = len(m.tok.encode(text, add_special_tokens=False).ids)
    m.detect(text)                                    # warm
    ms = []
    for _ in range(a.runs):
        t = time.perf_counter()
        m.detect(text)
        ms.append((time.perf_counter() - t) * 1000)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(json.dumps({"bundle": Path(a.bundle).name, "threads": a.threads, "tokens": n, "load_s": round(load_s, 1),
                      "p50_ms": round(statistics.median(ms)), "min_ms": round(min(ms)), "max_ms": round(max(ms)),
                      "peak_rss_mb_after_load": round(rss_load), "peak_rss_mb": round(rss)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
