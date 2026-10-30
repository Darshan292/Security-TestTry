#!/usr/bin/env python3
"""Markdown tables from bench_matrix.py JSON results (overlap recall/precision per type; overall; leak rate; latency).

    python scripts/ml/format_matrix.py result.json [result2.json ...] [--types PERSON,EMAIL,...]
Configs with the same name in several files are merged (later file wins). Dev numbers only."""
from __future__ import annotations

import argparse
import json


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--types")
    a = ap.parse_args(argv)
    res: dict = {}
    docs: dict = {}
    for f in a.files:
        d = json.load(open(f))
        docs.update(d["docs"])
        for corp, cfgs in d["results"].items():
            res.setdefault(corp, {}).update(cfgs)
    for corp, cfgs in res.items():
        names = list(cfgs)
        print(f"\n**{corp}** ({docs.get(corp, '?')} documents; overlap recall R / precision P; dev only)\n")
        print("| config | overall R | overall P | leak rate | non-PII chars masked | p50 ms | p95 ms |")
        print("|---|---|---|---|---|---|---|")
        for n in names:
            r = cfgs[n]
            o = r["overlap"]["overall"]
            om = r.get("over_masking", {})
            print(f"| {n} | {o['recall']:.3f} | {o['precision']:.3f} | {r['leak_rate']['overall']['rate']:.3f} | "
                  f"{om.get('non_pii_chars_masked', float('nan')):.4f} | {r['latency_ms']['p50']} | {r['latency_ms']['p95']} |")
        types = a.types.split(",") if a.types else [t for t, row in cfgs[names[0]]["overlap"]["by_type"].items() if row["gold"]]
        print("\n| type | gold | " + " | ".join(f"{n} R | {n} P" for n in names) + " |")
        print("|---|---|" + "---|" * (2 * len(names)))
        for t in types:
            row0 = next((cfgs[n]["overlap"]["by_type"].get(t) for n in names if cfgs[n]["overlap"]["by_type"].get(t)), None)
            cells = []
            for n in names:
                r = cfgs[n]["overlap"]["by_type"].get(t)
                cells += [f"{r['recall']:.3f}", f"{r['precision']:.3f}"] if r else ["-", "-"]
            print(f"| {t} | {row0['gold'] if row0 else 0} | " + " | ".join(cells) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
