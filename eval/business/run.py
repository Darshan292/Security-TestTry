"""Business-quality evaluator for pilot-validation captures (synthetic data only).

    python -m eval.business.run --captures /home/user/pilot_captures/<run> [--judge]

Reads the captures written by scripts/pilot_validation.py (outbound bytes, provider response, restored answer) and
reports, per case, PRIVACY and BUSINESS results separately; a case passes overall only if both pass. Context-
dependent judgements are listed from rubric.yaml as PENDING human review. `--judge` adds an LLM judge as a
secondary signal (never pass/fail). The full report (with excerpts of synthetic text) is written next to the
captures; a content-free summary goes to eval/results/.

Scope: this is the FIXED-SCENARIO acceptance regression suite for the ten pilot scenarios, not a general evaluator.
Its privacy checks look for the declared or expected canary values of each scenario (specs.py raw_values /
never_returned), plus leftover aliases and markers. A privacy PASS does not mean the outbound text holds no other
personal data. Privacy is NOT_EVALUABLE unless every captured outbound exchange is present and parseable
(SECURITY_BOUNDARY.md §5).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

from . import checks as C
from .specs import ROOT, SPECS, Spec


def load_captures(d: Path) -> dict[str, dict]:
    """scenario label -> {answer, outbound, provider_raw}. JSON captures first; the review sheet fills gaps."""
    out: dict[str, dict] = {}
    for f in sorted(d.glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except ValueError:
            continue
        if not isinstance(rec, dict) or "scenario" not in rec:
            continue
        ob = rec.get("outbound_http") or []
        ib = rec.get("inbound_http") or []
        raw = ""
        if ib and ib[0]:
            try:
                raw = json.loads(ib[0])["choices"][0]["message"]["content"] or ""
            except (ValueError, KeyError, IndexError, TypeError):
                raw = ""
        # every exchange is kept: privacy is NOT_EVALUABLE unless all of them can be inspected (checks.py)
        out[rec["scenario"]] = {"answer": rec.get("restored_response") or "", "outbound": list(ob) if ob else None,
                                "provider_raw": raw}
    sheet = d / "human_review.md"
    if sheet.exists():
        for label, body in re.findall(r"^## (.+?) \(HTTP \d+\)\n(.*?)(?=^- Useful\?)", sheet.read_text(), re.M | re.S):
            out.setdefault(label.strip(), {"answer": body.strip(), "outbound": None, "provider_raw": ""})
    return out


def evaluate(spec: Spec, cap: dict | None) -> C.Report:
    rep = C.Report(spec.case)
    if cap is None:
        rep.results.append(C.Result("PRIVACY", "capture", "NOT_EVALUABLE", "no capture for this case"))
        rep.results.append(C.Result("BUSINESS", "capture", "NOT_EVALUABLE", "no capture for this case"))
        return rep
    a = cap["answer"]
    rep.results += C.check_privacy(cap["outbound"], a, spec.raw_values, spec.never_returned)
    rep.results += C.check_facts(a, spec.facts)
    rep.results += C.check_claims(a, spec.claims, spec.source, spec.hypothesis_sections, spec.claim_evidence)
    rep.results.append(C.check_numbers(a, spec.source, spec.extra_numbers))
    rep.results.append(C.check_codes(a, spec.source))
    rep.results += C.check_prohibited(a, spec.prohibited)
    rep.results += C.check_steps(a, spec.steps, spec.required_steps)
    rep.results += C.check_sections(a, spec.sections)
    rep.results += C.check_contains(a, spec.must_contain, spec.must_not_contain)
    rep.results += C.check_format(a, spec.max_lines, spec.max_words)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures", required=True)
    ap.add_argument("--judge", action="store_true", help="add the LLM judge as a secondary signal")
    args = ap.parse_args(argv)
    d = Path(args.captures).resolve()
    caps = load_captures(d)
    rubric = yaml.safe_load((Path(__file__).parent / "rubric.yaml").read_text())["items"]
    if args.judge:
        sys.path.insert(0, str(ROOT))
        from demo.envfile import load_env
        load_env()
        from .judge import judge

    lines, summary = [f"# Business-quality evaluation: `{d.name}`", ""], [f"# Business-quality evaluation: `{d.name}`",
                                                                          "", "| Case | Privacy | Business | Overall | "
                                                                          "Non-passing checks | Human rubric |",
                                                                          "|---|---|---|---|---|---|"]
    totals = {"PASS": 0, "FAIL": 0, "NOT_EVALUABLE": 0}
    for spec in SPECS:
        cap = caps.get(spec.capture)
        rep = evaluate(spec, cap)
        totals[rep.overall] += 1
        items = [r for r in rubric if r["case"] == spec.case]
        fails = [r for r in rep.results if r.status != "PASS"]
        lines += [f"## {spec.case} — {spec.capture}: **{rep.overall}** "
                  f"(privacy {rep.category('PRIVACY')}, business {rep.category('BUSINESS')})", ""]
        for r in fails:
            lines.append(f"- [{r.category}] `{r.check}` {r.status}: {r.detail}"
                         + (f"\n  > {r.evidence}" if r.evidence else ""))
        if not fails:
            lines.append("- all deterministic checks passed")
        for it in items:
            lines.append(f"- [HUMAN REVIEW PENDING] `{it['id']}`: {it['question']}")
        if args.judge and cap and items:
            jv = judge("\n\n".join(o for o in (cap["outbound"] or []) if isinstance(o, str)),
                       cap["provider_raw"] or "", items)
            for iid, v in jv.items():
                lines.append(f"- [LLM judge, secondary only] `{iid}`: {v['verdict']} — {v['reason']}")
        lines.append("")
        summary.append(f"| {spec.case} | {rep.category('PRIVACY')} | {rep.category('BUSINESS')} | {rep.overall} | "
                       f"{', '.join(r.check for r in fails) or '—'} | {len(items)} pending |")
    head = (f"**{totals['PASS']} pass, {totals['FAIL']} fail, {totals['NOT_EVALUABLE']} not evaluable** "
            f"of {len(SPECS)} cases. Overall pass requires PRIVACY and BUSINESS to pass; human rubric items are "
            "listed as pending and are not scored by this tool.")
    lines.insert(2, head + "\n")
    summary += ["", head]
    (d / "business_eval.md").write_text("\n".join(lines) + "\n")
    (ROOT / "eval/results" / f"business_eval_{d.name}.md").write_text("\n".join(summary) + "\n")
    print("\n".join(lines))
    return 1 if totals["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
