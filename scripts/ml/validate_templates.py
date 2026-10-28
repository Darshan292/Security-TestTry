#!/usr/bin/env python3
"""Second-pass template filter, run LOCALLY after downloading templates from Kaggle.

Re-applies ml.train.synth.validate_template and additionally runs the repo's own deterministic detector
(pgw.detect.recognizers.detect_d2) over the text OUTSIDE the slots: if the detector finds any entity there, the LLM wrote
a PII-shaped value itself and the template is dropped (slots are removed first, so slot values never trigger it).
Writes the kept templates and prints counts per drop reason.

Usage: python scripts/ml/validate_templates.py templates.jsonl --out templates.clean.jsonl [--no-detector]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ml.train import genres, io, synth  # noqa: E402


def repo_detector():
    from pgw.detect.recognizers import detect_d2

    def found(text: str) -> bool:
        return bool(detect_d2(text))
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("templates")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-detector", action="store_true", help="skip the repo detector (regex checks only)")
    a = ap.parse_args(argv)
    det = None if a.no_detector else repo_detector()
    kept, reasons = [], collections.Counter()
    for t in io.read_jsonl(a.templates):
        spec = genres.BY_NAME.get(t.get("genre", ""))
        ok, why = synth.validate_template(t["template"], spec, det)
        if ok:
            kept.append(t)
        else:
            reasons[why.split(":")[0]] += 1
    io.write_jsonl(a.out, kept)
    print(json.dumps({"kept": len(kept), "dropped": sum(reasons.values()), "reasons": dict(reasons)}, indent=2))
    return 0 if kept else 1


if __name__ == "__main__":
    sys.exit(main())
