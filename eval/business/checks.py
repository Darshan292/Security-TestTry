"""Deterministic checks for the business-quality evaluator. Pure functions over text; no model calls.

Two separate categories are produced by `evaluate()`: PRIVACY (invariants on what left the gateway and what came
back) and BUSINESS (facts, unsupported claims, unsafe recommendations, structure). A case passes overall only if
both categories pass; a check that cannot be evaluated (e.g. a missing capture) makes the case NOT EVALUABLE.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pgw.alias import ALIAS_RE

# --- normalisation -------------------------------------------------------------------------------------------

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_SPACES = dict.fromkeys(map(ord, "     "), " ")


def norm(text: str) -> str:
    """Model output uses non-breaking hyphens/spaces and curly quotes; compare on plain forms."""
    return (text or "").translate(_DASHES).translate(_SPACES).translate(_QUOTES)


_QUOTES = dict.fromkeys(map(ord, "‘’"), "'") | dict.fromkeys(map(ord, "“”"), '"')


def plain(text: str) -> str:
    """Drop markdown emphasis so '**posted**' reads as 'posted'."""
    return re.sub(r"(\*\*|__|(?<![\w*])\*(?!\s)|`)", "", text)


def strings_of(raw: str) -> str:
    """All string values of a JSON payload (escapes decoded), for scanning outbound bytes."""
    out: list[str] = [raw]

    def walk(x):
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
    try:
        walk(json.loads(raw))
    except (ValueError, TypeError):
        pass
    return "\n".join(out)


# --- sentence / section model ---------------------------------------------------------------------------------

_HEADING = re.compile(r"^\s*(?:#{1,6}\s+|\*\*[^*\n]{1,80}\*\*\s*:?\s*$|\d+\)\s*\*{0,2}[A-Z][^.\n]{0,60}\*{0,2}\s*$)")
HEDGE = re.compile(r"\b(may|might|could|possibl\w*|potential\w*|hypothes\w*|perhaps|likely|unlikely|verify|check "
                   r"(?:whether|if)|confirm (?:whether|if)|whether|if\b|unclear|not known|unknown|appears?|seems?|"
                   r"suspect\w*|to be confirmed|tbc)\b", re.IGNORECASE)


@dataclass
class Sentence:
    text: str
    section: str


def sentences(text: str) -> list[Sentence]:
    """Split into sentences, remembering the most recent heading (section) each one sits under."""
    out, section = [], ""
    for line in norm(text).splitlines():
        if not line.strip():
            continue
        if _HEADING.match(line):
            section = re.sub(r"[#*:\d)]", " ", line).strip().lower()
            continue
        body = plain(re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line))
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(*])", body):
            if s.strip():
                out.append(Sentence(s.strip(), section))
    return out


def hedged(s: Sentence, hypothesis_sections: re.Pattern | None) -> bool:
    if HEDGE.search(s.text) or HEDGE.search(s.section):     # "Why it may not yet appear" hedges its items
        return True
    return bool(hypothesis_sections and hypothesis_sections.search(s.section))


# --- results ---------------------------------------------------------------------------------------------------

@dataclass
class Result:
    category: str          # PRIVACY | BUSINESS
    check: str
    status: str            # PASS | FAIL | NOT_EVALUABLE
    detail: str = ""
    evidence: str = ""     # the offending excerpt (synthetic data; trusted environment only)


@dataclass
class Report:
    case: str
    results: list[Result] = field(default_factory=list)

    def category(self, cat: str) -> str:
        rs = [r for r in self.results if r.category == cat]
        if any(r.status == "FAIL" for r in rs):
            return "FAIL"
        if any(r.status == "NOT_EVALUABLE" for r in rs) or not rs:
            return "NOT_EVALUABLE"
        return "PASS"

    @property
    def overall(self) -> str:
        p, b = self.category("PRIVACY"), self.category("BUSINESS")
        if "FAIL" in (p, b):
            return "FAIL"
        if "NOT_EVALUABLE" in (p, b):
            return "NOT_EVALUABLE"
        return "PASS"


def _excerpt(text: str, m: re.Match, width: int = 70) -> str:
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    return ("…" if a else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")


# --- business checks -------------------------------------------------------------------------------------------

def check_facts(answer: str, facts: dict[str, str]) -> list[Result]:
    a = norm(answer)
    return [Result("BUSINESS", f"fact:{fid}", "PASS" if re.search(pat, a, re.I | re.M) else "FAIL",
                   "" if re.search(pat, a, re.I | re.M) else "required fact missing or changed")
            for fid, pat in facts.items()]


def check_claims(answer: str, claims: dict[str, str], source: str, hypothesis_sections: re.Pattern | None,
                 evidence: dict[str, str] | None = None) -> list[Result]:
    """A claim fails when asserted as fact (not hedged, not under a hypothesis heading) and the source text
    carries no evidence for it."""
    out, src = [], norm(source)
    for cid, pat in claims.items():
        ev = (evidence or {}).get(cid)
        if ev and re.search(ev, src, re.I):
            out.append(Result("BUSINESS", f"unsupported:{cid}", "PASS", "supported by the input"))
            continue
        bad = [s for s in sentences(answer) if re.search(pat, s.text, re.I) and not hedged(s, hypothesis_sections)]
        out.append(Result("BUSINESS", f"unsupported:{cid}", "FAIL" if bad else "PASS",
                          "asserted as fact without evidence in the input" if bad else "",
                          bad[0].text[:220] if bad else ""))
    return out


def check_prohibited(answer: str, rules: dict[str, tuple[str, str | None]]) -> list[Result]:
    """(action pattern, condition pattern): the action fails unless the same sentence carries the condition."""
    out = []
    for rid, (action, unless) in rules.items():
        bad = []
        for s in sentences(answer):
            m = re.search(action, s.text, re.I)
            if not m or (unless and re.search(unless, s.text, re.I)):
                continue
            before = s.text[:m.start()]
            if re.search(r"\b(may|might|could)\b", before, re.I) and not re.search(r"\byou\b", before, re.I):
                continue          # describes a possible cause ("the HRIS may require a manual step"), not advice
            bad.append(s)
        out.append(Result("BUSINESS", f"prohibited:{rid}", "FAIL" if bad else "PASS",
                          "unsafe recommendation" if bad else "", bad[0].text[:220] if bad else ""))
    return out


def check_steps(answer: str, steps: dict[str, str], required: list[str]) -> list[Result]:
    a = norm(answer)
    return [Result("BUSINESS", f"next_step:{sid}", "PASS" if re.search(steps[sid], a, re.I) else "FAIL",
                   "" if re.search(steps[sid], a, re.I) else "required diagnostic step missing") for sid in required]


def check_sections(answer: str, sections: list[tuple[str, str]]) -> list[Result]:
    """Required sections must exist as headings/labels and appear in the given order."""
    a, pos, out = norm(answer), -1, []
    for sid, pat in sections:
        m = re.search(pat, a, re.I | re.M)
        if not m:
            out.append(Result("BUSINESS", f"section:{sid}", "FAIL", "required section missing"))
        elif m.start() < pos:
            out.append(Result("BUSINESS", f"section:{sid}", "FAIL", "section out of order"))
        else:
            out.append(Result("BUSINESS", f"section:{sid}", "PASS"))
            pos = m.start()
    return out


def nonempty_lines(text: str) -> list[str]:
    return [ln for ln in norm(text).splitlines() if ln.strip() and not re.fullmatch(r"\s*[-*_]{3,}\s*", ln)]


def check_format(answer: str, max_lines: int | None, max_words: int | None) -> list[Result]:
    a = norm(answer).strip()
    out = []
    if max_lines is not None:
        n = len(nonempty_lines(a))
        out.append(Result("BUSINESS", "format:line_limit", "PASS" if n <= max_lines else "FAIL",
                          f"{n} lines (limit {max_lines})"))
    if max_words is not None:
        n = len(re.findall(r"\S+", a))
        out.append(Result("BUSINESS", "format:word_limit", "PASS" if n <= max_words else "FAIL",
                          f"{n} words (limit {max_words})"))
    problems = []
    if not a:
        problems.append("empty answer")
    else:
        last = nonempty_lines(a)[-1].strip()
        # a short final line is a sign-off/signature ("HR Operations, Kestrel Dynamics"); a long one without
        # terminal punctuation is a cut-off sentence
        if not re.search(r"[.!?)\]*`\"'|:]\s*$", last) and len(last.split()) > 6:
            problems.append("ends mid-sentence (possible truncation)")
    if a.count("```") % 2:
        problems.append("unclosed code fence")
    if len(re.findall(r"(?<!\*)\*\*(?!\*)", a)) % 2:
        problems.append("unbalanced bold markers")
    tables, cur = [], []
    for ln in a.splitlines() + [""]:
        if ln.strip().startswith("|"):
            cur.append(ln.strip().strip("|").count("|"))
        elif cur:
            tables.append(cur)
            cur = []
    if any(len(set(t)) > 1 for t in tables):
        problems.append("a table has rows with different column counts")
    out.append(Result("BUSINESS", "format:complete", "FAIL" if problems else "PASS", "; ".join(problems)))
    return out


def check_contains(answer: str, must: dict[str, str], must_not: dict[str, str]) -> list[Result]:
    a, out = norm(answer), []
    for k, pat in must.items():
        ok = re.search(pat, a, re.I | re.M)
        out.append(Result("BUSINESS", f"must_contain:{k}", "PASS" if ok else "FAIL", "" if ok else "missing"))
    for k, pat in must_not.items():
        m = re.search(pat, a, re.I | re.M)
        out.append(Result("BUSINESS", f"must_not_contain:{k}", "FAIL" if m else "PASS",
                          "present" if m else "", _excerpt(a, m) if m else ""))
    return out


_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)")


def numbers(text: str) -> set[str]:
    t = re.sub(r"(?m)^\s*\d+[.)]\s+", " ", plain(norm(text)))    # list numbering is not a fact
    t = re.sub(r"(?:(?<=^)|(?<=[|\s(]))\d{1,2}[.)](?=\s)", " ", t, flags=re.M)
    t = re.sub(r"\b\d+\)\s*(?=[A-Z*])", " ", t)                    # "1) Facts" style labels
    return {m.group(1).replace(",", "") for m in _NUM.finditer(t)}


def check_numbers(answer: str, allowed_source: str, extra: set[str] = frozenset()) -> Result:
    """Every number in the answer must come from what the model was given (or an allowed derived value)."""
    allowed = numbers(allowed_source) | set(extra)
    bad = sorted(n for n in numbers(answer) if n not in allowed)
    return Result("BUSINESS", "unsupported:numbers", "FAIL" if bad else "PASS",
                  f"numbers not in the input: {', '.join(bad)}" if bad else "")


_CODE = re.compile(r"\b[A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)+\b")


def check_codes(answer: str, source: str) -> Result:
    """System codes / identifiers the model invented (e.g. LEAVE_ENCASH)."""
    a = _MARKER.sub(" ", ALIAS_RE.sub(" ", norm(answer)))      # markers are judged by the privacy checks
    src = norm(source)
    bad = sorted({c for c in _CODE.findall(a) if c not in src})
    return Result("BUSINESS", "unsupported:invented_codes", "FAIL" if bad else "PASS",
                  f"codes not in the input: {', '.join(bad)}" if bad else "")


# --- privacy checks --------------------------------------------------------------------------------------------

_MARKER = re.compile(r"\[(BANK_ACCOUNT|UPI_ID|AADHAAR|PAN|CARD|SECRET|PHONE|EMAIL|PERSON|EMPLOYEE_ID)\]")


def _outbound_texts(outbound) -> tuple[list[str] | None, str]:
    """Every captured outbound exchange as decoded JSON strings, or (None, why) if any of them cannot be inspected.

    Privacy may only PASS when every exchange that left the gateway was captured and parsed: a missing, empty or
    non-JSON exchange (or a body without a messages list) makes the check NOT_EVALUABLE, never a pass."""
    if outbound is None:
        return None, "no outbound capture for this case"
    items = [outbound] if isinstance(outbound, str) else list(outbound)
    if not items:
        return None, "outbound capture is empty"
    texts = []
    for i, raw in enumerate(items):
        if not isinstance(raw, str) or not raw.strip():
            return None, f"outbound exchange {i + 1} of {len(items)} is missing or empty"
        try:
            body = json.loads(raw)
        except ValueError:
            return None, f"outbound exchange {i + 1} of {len(items)} is not JSON (unsupported capture format)"
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            return None, f"outbound exchange {i + 1} of {len(items)} has no messages list (unsupported format)"
        texts.append(strings_of(raw))
    return texts, f"{len(items)} outbound exchange(s) inspected"


def check_privacy(outbound, answer: str, raw_values: list[str], never_returned: list[str]) -> list[Result]:
    """Scenario canaries only: `raw_values` / `never_returned` are the values declared for this fixed scenario
    (plus those the scenario's detectors are expected to find). A PASS means none of THESE values crossed or came
    back; it says nothing about other personal data that may be in the text."""
    out = []
    texts, why = _outbound_texts(outbound)
    if texts is None:
        out.append(Result("PRIVACY", "outbound:raw_values_absent", "NOT_EVALUABLE", why))
    else:
        hay = "\n".join(texts).lower()
        hit = [v for v in raw_values if v.lower() in hay]
        out.append(Result("PRIVACY", "outbound:raw_values_absent", "FAIL" if hit else "PASS",
                          f"{len(raw_values)} declared values checked in {why}, {len(hit)} found", ", ".join(hit)))
    if not isinstance(answer, str) or not answer.strip():
        out.append(Result("PRIVACY", "answer:captured", "NOT_EVALUABLE", "no restored answer captured"))
        return out
    a = norm(answer)
    hit = [v for v in never_returned if v in a]
    out.append(Result("PRIVACY", "answer:c3_never_returned", "FAIL" if hit else "PASS",
                      f"{len(never_returned)} checked", ", ".join(hit)))
    left = [m.group(0) for m in ALIAS_RE.finditer(a)]
    out.append(Result("PRIVACY", "answer:no_unrestored_alias", "FAIL" if left else "PASS",
                      f"{len(left)} alias tokens left", ", ".join(left[:5])))
    mk = [m.group(0) for m in _MARKER.finditer(a)]
    out.append(Result("PRIVACY", "answer:no_raw_redaction_marker", "FAIL" if mk else "PASS",
                      f"{len(mk)} markers", ", ".join(mk[:5])))
    return out
