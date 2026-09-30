"""E-STREAM-LEAK (EVALUATION_PLAN amendment 2026-09-30b, item 3).

For each document: protect it, then simulate a model output made of the protected (aliased)
text followed by raw "echoes" (the original text around k random gold values, as a model
that saw or guessed them might write). Stream that output through the restorer + output gate
in INCREMENTAL mode with >= 5 random chunkings and compare with BUFFERED mode.

Gate: for every document and chunking, streamed client output == buffered client output.
Also reported: stream-only raw leaks (gold values visible when streamed but not when buffered)
and alias fragments (partial alias text delivered to the client).

    python -m eval.run_stream --data eval/data2 --split dev2 [--chunkings 5] [--one-shot-test]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path

from eval.run_det import CTX, build_protector, guard_split, load
from pgw.stream import Restorer

FRAGMENT = re.compile(r"\[(?:[A-Z_]{2,20})_[0-9A-Z]{0,5}(?![0-9A-Z\]])|(?<![\[A-Z_])[A-Z_]{3,20}_[0-9][0-9A-Z]{0,4}\]")


def simulate_output(item: dict, aliased: str, rng: random.Random, k: int = 3) -> str:
    text, spans = item["text"], [s for s in item["spans"] if s["end"] - s["start"] >= 4]
    echoes = []
    for s in rng.sample(spans, k=min(k, len(spans))):
        echoes.append(text[max(0, s["start"] - 60): s["end"] + 10])
    return aliased + ("\n\nNotes:\n" + "\n".join(echoes) if echoes else "")


def run(r: Restorer, text: str, cuts: list[int]) -> str:
    out, prev = [], 0
    for c in cuts + [len(text)]:
        out.append(r.feed(text[prev:c]))
        prev = c
    out.append(r.finish())
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="eval/data2")
    ap.add_argument("--split", default="dev2")
    ap.add_argument("--chunkings", type=int, default=5)
    ap.add_argument("--one-shot-test", action="store_true")
    ap.add_argument("--ner", default=None)
    ap.add_argument("--out", default="eval/results")
    a = ap.parse_args()
    data = Path(a.data)
    guard_split(a.split, a.one_shot_test, Path(a.out), data, tag="stream")
    prot = build_protector(data, True, ner=a.ner)
    rng = random.Random(4242)
    n = mismatched_docs = stream_only_leaks = fragments = 0
    t_stream = t_buf = 0.0
    for it in load(data / f"{a.split}.jsonl"):
        p = prot.protect([{"role": "user", "content": it["text"]}], CTX)
        out = simulate_output(it, p.messages[0]["content"], rng)
        t0 = time.perf_counter()
        want = run(Restorer(p.alias_map, roster=prot.roster, mode="buffered"), out, [])
        t_buf += time.perf_counter() - t0
        gold = {it["text"][s["start"]:s["end"]] for s in it["spans"] if s["end"] - s["start"] >= 4}
        doc_bad = False
        for _ in range(a.chunkings):
            cuts = sorted(rng.sample(range(1, len(out)), k=min(len(out) - 1, rng.randint(2, 80)))) if len(out) > 3 else []
            t0 = time.perf_counter()
            got = run(Restorer(p.alias_map, roster=prot.roster, mode="incremental"), out, cuts)
            t_stream += time.perf_counter() - t0
            if got != want:
                doc_bad = True
                # any gold value OR any word (>= 3 chars) of one that the stream shows more often than
                # the buffered output (partial leaks count: "bhavesh" of "bhavesh chauhan")
                parts = gold | {w for g in gold for w in re.findall(r"\w{3,}", g)}
                stream_only_leaks += sum(1 for g in parts if got.count(g) > want.count(g))
                fragments += len(FRAGMENT.findall(got)) - len(FRAGMENT.findall(want))
        mismatched_docs += doc_bad
        n += 1
    res = {"split": a.split, "docs": n, "chunkings_per_doc": a.chunkings,
           "docs_stream_differs_from_buffered": mismatched_docs,
           "stream_only_raw_leaks": stream_only_leaks, "alias_fragments": max(fragments, 0),
           "gate_pass": mismatched_docs == 0,
           "restore_cpu_ms_per_doc": {"buffered": 1000 * t_buf / max(n, 1),
                                      "incremental_per_chunking": 1000 * t_stream / max(n * a.chunkings, 1)}}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / f"stream_{a.split}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
