#!/usr/bin/env python3
"""Contamination check: generated training data vs the sealed held-out / tester sets. Runs LOCALLY only.

The held-out files are never uploaded and never read by the generator; this script is the only thing that opens them,
and it only prints counts (never held-out text or values). It fails (exit 1) when generated data is too close:

  * 8-gram overlap: word 8-grams (lower-cased, \\w+ tokens, slot markers removed). A generated record is "near-duplicate"
    when >= --doc-ratio of its 8-grams occur in the held-out text. Fail if the share of such records > --max-doc-frac.
  * exact value overlap: normalised span values (alphanumerics, lower-cased). For identifier types (AADHAAR, PAN,
    BANK_ACCOUNT, CARD, UPI_ID, EMAIL, PHONE, SECRET, IDENTIFIER, EMPLOYEE_ID) any value of >= --min-len characters (11 for EMPLOYEE_ID)
    found in the held-out set counts; more than --max-id-overlap (default 0) fails. For PERSON, full names (>= 2
    tokens) are compared and the share of distinct generated full names found in held-out may not exceed --max-name-frac
    (names come from public lists, so some chance collisions are expected). COMPENSATION and DATE_OF_BIRTH are not
    compared (too few distinct values; they collide by chance and prove nothing).

Exit codes: 0 pass, 1 contamination above threshold, 2 usage error or no held-out files found (fail closed).

Usage:
  python scripts/ml/contamination_check.py --generated work/train.jsonl work/dev.jsonl
  python scripts/ml/contamination_check.py --generated work/*.jsonl --heldout eval/data2/test.jsonl my_tester_set.jsonl
Default held-out discovery under eval/: heldout*.jsonl[.gz], test*.jsonl[.gz], ner_test*.jsonl[.gz] (and *.json.gz variants).
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ID_TYPES = ("AADHAAR", "PAN", "BANK_ACCOUNT", "CARD", "UPI_ID", "EMAIL", "PHONE", "SECRET", "IDENTIFIER", "EMPLOYEE_ID")
DEFAULT_PATTERNS = ("eval/**/heldout*.jsonl", "eval/**/heldout*.jsonl.gz", "eval/**/test*.jsonl", "eval/**/test*.jsonl.gz",
                    "eval/**/ner_test*.jsonl", "eval/**/ner_test*.jsonl.gz")
_SLOT = re.compile(r"\{\{[^}]*\}\}")
_WORD = re.compile(r"\w+", re.UNICODE)
N = 8
# Employee ids come in short company styles ("EID-123456"): a chance collision at 8-9 characters says nothing, so they
# only count from 11 normalised characters up. Every other identifier type counts from --min-len.
TYPE_MIN_LEN = {"EMPLOYEE_ID": 11}


def _open(p):
    return gzip.open(p, "rt", encoding="utf-8") if str(p).endswith(".gz") else open(p, encoding="utf-8")


def read_records(path):
    with _open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def text_of(rec: dict) -> str:
    t = rec.get("text")
    if t is None:
        t = rec.get("template", "")
    return _SLOT.sub(" ", t)


def ngrams(text: str, n: int = N):
    w = [x.lower() for x in _WORD.findall(text)]
    return {hash(tuple(w[i:i + n])) for i in range(len(w) - n + 1)}


def norm_value(v: str) -> str:
    return "".join(c for c in v.lower() if c.isalnum())


def norm_name(v: str) -> str:
    return " ".join(_WORD.findall(v.lower()))


def discover(root: Path = ROOT) -> list[Path]:
    found: set[Path] = set()
    for pat in DEFAULT_PATTERNS:
        found.update(Path(p) for p in glob.glob(str(root / pat), recursive=True))
    return sorted(found)


def load_heldout(paths) -> dict:
    grams: set[int] = set()
    ids: set[str] = set()
    names: set[str] = set()
    n_rec = 0
    for p in paths:
        for rec in read_records(p):
            n_rec += 1
            text = rec.get("text", "")
            grams |= ngrams(text)
            for s in rec.get("spans", []) or []:
                v = text[s["start"]:s["end"]]
                if s.get("type") in ID_TYPES:
                    ids.add(norm_value(v))
                elif s.get("type") == "PERSON" and len(v.split()) >= 2:
                    names.add(norm_name(v))
    return {"grams": grams, "ids": ids, "names": names, "records": n_rec}


def check(generated_paths, heldout: dict, doc_ratio=0.3, max_doc_frac=0.01, min_len=8, max_id_overlap=0, max_name_frac=0.05) -> dict:
    n = near = 0
    id_hits: dict[str, int] = {}
    gen_names: set[str] = set()
    name_hits: set[str] = set()
    worst = 0.0
    for p in generated_paths:
        for rec in read_records(p):
            n += 1
            g = ngrams(text_of(rec))
            if g:
                ratio = len(g & heldout["grams"]) / len(g)
                worst = max(worst, ratio)
                if ratio >= doc_ratio:
                    near += 1
            text = rec.get("text", "")
            for s in rec.get("spans", []) or []:
                v = text[s["start"]:s["end"]]
                t = s.get("type")
                if t in ID_TYPES:
                    nv = norm_value(v)
                    if len(nv) >= max(min_len, TYPE_MIN_LEN.get(t, 0)) and nv in heldout["ids"]:
                        id_hits[t] = id_hits.get(t, 0) + 1
                elif t == "PERSON" and len(v.split()) >= 2:
                    nn = norm_name(v)
                    gen_names.add(nn)
                    if nn in heldout["names"]:
                        name_hits.add(nn)
    n_id = sum(id_hits.values())
    name_frac = len(name_hits) / len(gen_names) if gen_names else 0.0
    res = {"generated_records": n, "heldout_records": heldout["records"], "near_duplicate_records": near,
           "near_duplicate_frac": round(near / n, 5) if n else 0.0, "worst_doc_ngram_ratio": round(worst, 4),
           "identifier_value_overlaps": id_hits, "identifier_value_overlap_total": n_id,
           "distinct_generated_full_names": len(gen_names), "full_name_overlap": len(name_hits), "full_name_overlap_frac": round(name_frac, 5),
           "thresholds": {"doc_ratio": doc_ratio, "max_doc_frac": max_doc_frac, "min_len": min_len,
                          "max_id_overlap": max_id_overlap, "max_name_frac": max_name_frac}}
    failures = []
    if n and near / n > max_doc_frac:
        failures.append(f"{near}/{n} records are near-duplicates of held-out text (> {max_doc_frac:.2%})")
    if n_id > max_id_overlap:
        failures.append(f"{n_id} identifier values also occur in the held-out set (max {max_id_overlap})")
    if name_frac > max_name_frac:
        failures.append(f"{name_frac:.1%} of generated full names occur in the held-out set (max {max_name_frac:.0%})")
    res["failures"] = failures
    res["passed"] = not failures
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generated", nargs="+", required=True, help="generated JSONL files (records or templates)")
    ap.add_argument("--heldout", nargs="*", default=None, help="held-out / tester JSONL files (default: discover under eval/)")
    ap.add_argument("--doc-ratio", type=float, default=0.3)
    ap.add_argument("--max-doc-frac", type=float, default=0.01)
    ap.add_argument("--min-len", type=int, default=8)
    ap.add_argument("--max-id-overlap", type=int, default=0)
    ap.add_argument("--max-name-frac", type=float, default=0.05)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)
    gen = [Path(p) for g in a.generated for p in (sorted(glob.glob(g)) or [g])]
    missing = [p for p in gen if not p.exists()]
    if missing:
        print(f"generated file not found: {missing[0]}", file=sys.stderr)
        return 2
    held = [Path(p) for p in a.heldout] if a.heldout else discover()
    held = [p for p in held if p.exists()]
    if not held:
        print("no held-out files found (looked under eval/); refusing to pass without a comparison. "
              "Pass --heldout explicitly.", file=sys.stderr)
        return 2
    if set(map(Path.resolve, held)) & set(map(Path.resolve, gen)):
        print("a file is both generated and held-out", file=sys.stderr)
        return 2
    res = check(gen, load_heldout(held), a.doc_ratio, a.max_doc_frac, a.min_len, a.max_id_overlap, a.max_name_frac)
    res["heldout_files"] = len(held)
    print(json.dumps(res, indent=2, sort_keys=True))
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(res, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if res["passed"]:
        print("PASS: no contamination above threshold")
        return 0
    print("FAIL: " + "; ".join(res["failures"]), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
