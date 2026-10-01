"""CONTAMINATION C1/C5 audit: scan agent transcripts (JSONL) for tool calls touching forbidden paths.

    python eval/v3/audit_transcripts.py --role author  T1.jsonl T2.jsonl ...
    python eval/v3/audit_transcripts.py --role implementer T.jsonl

Prints only counts and the offending tool inputs' forbidden path tokens (never file contents).
"""
import argparse, json, re, sys

FORBID = {
    # authors must not see the system under test or older corpora
    "author": [r"security-testtry", r"Evidence-first-universal-knowledge-graph-compiler", r"/tmp/claude-0", r"\.claude/projects"],
    # implementers must not see sealed material or authoring transcripts
    "implementer": [r"v3-sealed", r"sealed_v3", r"subagents/agent-", r"/tasks/a[0-9a-f]+\.output", r"test3\.jsonl"],
}


def scan(path, pats):
    hits, n = [], 0
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        m = o.get("message") or {}
        for b in (m.get("content") if isinstance(m, dict) and isinstance(m.get("content"), list) else []):
            if isinstance(b, dict) and b.get("type") == "tool_use":
                n += 1
                s = json.dumps(b.get("input", {}))
                for p in pats:
                    for mt in re.finditer(r"[\w./~-]*" + p + r"[\w./-]*", s):
                        hits.append((b.get("name"), mt.group(0)[:160]))
    return n, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=FORBID, required=True)
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    bad = 0
    for f in a.files:
        n, hits = scan(f, FORBID[a.role])
        bad += len(hits)
        print(f"{f.rsplit('/', 1)[-1]}: {n} tool calls, {len(hits)} forbidden-path references")
        for name, tok in sorted(set(hits)):
            print(f"   {name}: {tok}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
