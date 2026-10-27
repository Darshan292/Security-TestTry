"""Evaluation hygiene: no value from the auditor's regression fixture (tests/fixtures/audit36.py) is written anywhere in
the product code (pgw/), and the fixture says it is a regression check only, not a progress measure.

Values a detector or a sample list was tuned on would make the audit tickets pass for the wrong reason. The check
covers every identifier-like value of the fixture: its ground-truth values (except single names), the generated
roster identifiers, and every token with a digit in the ticket texts. A value is found if it appears in a pgw/ file
as written, case-insensitively, or with its separators removed.
"""
import re
from pathlib import Path

from tests.fixtures import audit36

ROOT = Path(__file__).resolve().parents[1]
_SEPS = re.compile(r"[\s\-./_,:]+")


def _audit_values() -> set[str]:
    vals: set[str] = set()
    for e in audit36.ROSTER:
        vals |= {e["employee_id"], e["email"], e["phone"].replace("+91 ", ""), e["_pan"], e["_aadhaar"], e["_acct"]}
    for c in audit36.CASES:
        for t, v in c["raw"]:
            if t != "name" or " " in v:            # single first names are common words; full names are checked
                vals.add(v)
        for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/.@]*[A-Za-z0-9]", c["text"]):
            if any(ch.isdigit() for ch in tok) and len(tok) >= 6:
                vals.add(tok)
    # identifier literals written with spaces in the ticket texts ("KA 51 AB 4471", "KA05 20190012345")
    for c in audit36.CASES:
        for m in re.finditer(r"(?<![A-Za-z0-9])[A-Z0-9]{2,5}(?: [A-Z0-9]{2,11}){1,4}(?![A-Za-z0-9])", c["text"]):
            if sum(ch.isdigit() for ch in m.group()) >= 4:
                vals.add(m.group())
    return {v for v in vals if len(_SEPS.sub("", v)) >= 6}


def _pgw_texts():
    for p in sorted((ROOT / "pgw").rglob("*")):
        if p.is_file() and p.suffix in (".py", ".json", ".yaml", ".yml", ".txt", ".md", ".jsonl"):
            yield p, p.read_text(encoding="utf-8", errors="replace")


def test_the_value_set_covers_the_auditor_literals_and_the_check_sees_them():
    vals = _audit_values()
    assert len(vals) >= 60
    compact = {_SEPS.sub("", v).lower() for v in vals}
    for c in audit36.CASES:
        if c["id"] in ("I05", "I12", "P07"):        # identifiers outside the taxonomy, the overfitting risk
            assert all(_SEPS.sub("", x).lower() in compact for t, x in c["raw"] if t != "name")
    v = next(x for c in audit36.CASES if c["id"] == "I05" for t, x in c["raw"] if t == "passport")
    assert _hits(f'SAMPLES = ("{v}",)', vals) and _hits(f"# {' '.join(v)}", vals)    # as written, and spaced out


# Files owned by a concurrent change whose COMMENTS still quote audit amounts as examples. Only '#' comment lines may
# match there (checked below); the code may not. Remove an entry once its comments use fresh examples.
COMMENT_ONLY_PENDING: set[str] = set()   # no exemptions: no auditor literal anywhere in pgw/


def _hits(text: str, vals: set[str]) -> list[str]:
    low = text.lower()
    flat = _SEPS.sub("", low)
    plain = {v.lower() for v in vals}
    compact = {_SEPS.sub("", v).lower() for v in vals if len(_SEPS.sub("", v)) >= 8}
    return sorted({v for v in plain if v in low} | {v for v in compact if v in flat})


def test_no_audit36_value_appears_in_pgw():
    vals = _audit_values()
    hits = []
    for path, text in _pgw_texts():
        rel = path.relative_to(ROOT).as_posix()
        if rel in COMMENT_ONLY_PENDING:
            code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
            hits += [(rel + " (code)", v) for v in _hits(code, vals)]
            continue
        hits += [(rel, v) for v in _hits(text, vals)]
    assert not hits, f"auditor fixture values in product code: {hits[:10]}"


def test_audit36_is_marked_regression_only():
    doc = audit36.__doc__ or ""
    assert "REGRESSION FIXTURE ONLY" in doc and "not a progress measure" in doc and "held-out" in doc
