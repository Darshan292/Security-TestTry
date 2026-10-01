"""L2: context-free shape candidates. No field names, no document type: only the shape of the characters.

  DATE          numeric (d/m/y, y-m-d, compact yyyymmdd), month-name and ISO forms. A date followed by a non-zero clock
                time is an event timestamp, not a candidate.
  HIGH_ENTROPY  >= 16 chars, >= 3.5 bits/char, >= 2 character classes. Deviations from PD13_DESIGN.md (dev log, run 3):
                a run of natural-language words joined by separators or camelCase (FieldJustification,
                Content-Transfer-Encoding) is an identifier, not a candidate, and a token without any digit needs
                >= 4.0 bits/char.
  LONG_DIGITS   9-18 contiguous digits
  AMOUNT        Indian or western grouping (>= 4 digits), decimal amounts with two decimals and >= 3 integer digits, and
                amounts carrying a currency or magnitude marker. (The protector also offers a numeric field value of
                >= 4 digits that is not a calendar year, see pgw/pd13/protector.py.)

Candidates carry a provisional type (DATE->DATE_OF_BIRTH, HIGH_ENTROPY->SECRET, LONG_DIGITS->BANK_ACCOUNT,
AMOUNT->COMPENSATION). Overlaps between kinds resolve DATE > AMOUNT > LONG_DIGITS > HIGH_ENTROPY, then longer.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from pgw.types import EntityType as T

PROVISIONAL = {"DATE": T.DATE_OF_BIRTH, "HIGH_ENTROPY": T.SECRET, "LONG_DIGITS": T.BANK_ACCOUNT, "AMOUNT": T.COMPENSATION}
_PRIO = {"DATE": 0, "AMOUNT": 1, "LONG_DIGITS": 2, "HIGH_ENTROPY": 3}


@dataclass(frozen=True)
class Shape:
    start: int
    end: int
    kind: str
    ymd: tuple | None = None      # (year, month, day, two_digit_year) for DATE

    @property
    def type(self) -> T:
        return PROVISIONAL[self.kind]


_MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
        r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_TIME = r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
_D_ISO = re.compile(r"(?<![\d.\-/])((?:19|20)\d{2})([-/.])(0?[1-9]|1[0-2])\2(0?[1-9]|[12]\d|3[01])(?!\d)(?![.\-/]\d)" + f"({_TIME})")
_D_COMPACT = re.compile(r"(?<![\w.\-/])((?:19|20)\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?![\w\-/])" + r"()")
_D_NUM = re.compile(r"(?<![\d.\-/])(0?[1-9]|[12]\d|3[01])([-/.])(0?[1-9]|1[0-2])\2((?:19|20)\d{2}|\d{2})(?!\d)(?![.\-/]\d)")
_D_DMY = re.compile(rf"(?<![\w])(\d{{1,2}})(?:st|nd|rd|th)?[ \-./]*(?:of[ ]+)?({_MON})(?![A-Za-z])\.?[ \-./,]*(\d{{4}}|\d{{2}})(?!\d)", re.I)
_D_MDY = re.compile(rf"(?<![A-Za-z])({_MON})(?![A-Za-z])\.?[ ]+(\d{{1,2}})(?:st|nd|rd|th)?,?[ ]+(\d{{4}})(?!\d)", re.I)


_CLOCK = re.compile(r"[ ,T]+\d{1,2}:\d{2}")


def _timestamp_with_clock(t: str) -> bool:
    t = t.strip("TZ ")
    return bool(t) and bool(re.search(r"[1-9]", t.split("+")[0].split("-")[0].replace(":", "").replace(".", "")))


def dates(s: str) -> list[Shape]:
    out: list[Shape] = []
    for m in _D_ISO.finditer(s):
        if m.group(5) and _timestamp_with_clock(m.group(5)):
            continue
        out.append(Shape(m.start(), m.start(5), "DATE",
                         (int(m.group(1)), int(m.group(3)), int(m.group(4)), False)))
    for m in _D_COMPACT.finditer(s):
        out.append(Shape(m.start(), m.end(), "DATE", (int(m.group(1)), int(m.group(2)), int(m.group(3)), False)))
    for m in _D_NUM.finditer(s):
        y = m.group(4)
        out.append(Shape(m.start(), m.end(), "DATE", (int(y), int(m.group(3)), int(m.group(1)), len(y) == 2)))
    for m in _D_DMY.finditer(s):
        if _CLOCK.match(s, m.end()):
            continue
        y = m.group(3)
        mo = _MONTHS[m.group(2)[:3].lower()]
        out.append(Shape(m.start(), m.end(), "DATE", (int(y), mo, int(m.group(1)), len(y) == 2)))
    for m in _D_MDY.finditer(s):
        if _CLOCK.match(s, m.end()):
            continue
        mo = _MONTHS[m.group(1)[:3].lower()]
        out.append(Shape(m.start(), m.end(), "DATE", (int(m.group(3)), mo, int(m.group(2)), False)))
    return out


# --------------------------------------------------------------------------------------------- amounts
_GROUPED = re.compile(r"(?<![\w.])(?<!\d,)\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?(?![\w]|,\d|\.\d)")
_DECIMAL2 = re.compile(r"(?<![\w.])(?:(?<!\d,)\d{3}|\d{4,})\.\d{2}(?![\w]|\.\d|,\d)")
_UNIT = r"(?:lpa|lakhs?|lacs?|crores?|cr|k|mn|million|bn)"
_MARKED = re.compile(
    rf"(?<![\w.])(?<!\d,)(?:(?:₹|\bRs(?:\.[ ]*|[ ]+)|\bINR[ ]*|US\$[ ]*|\$[ ]*|€[ ]*|£[ ]*|\bUSD[ ]*|\bEUR[ ]*|\bGBP[ ]*)\d[\d,]*(?:\.\d+)?(?:[ ]*{_UNIT}\b)?"
    rf"|\d[\d,]*(?:\.\d+)?[ ]*{_UNIT}\b)", re.I)
_LONG = re.compile(r"(?<![\w.+\-])\d{9,18}(?![\w]|\.\d)")
_TOKEN = re.compile(r"[A-Za-z0-9+/_=\-]{16,}")


def _entropy(tok: str) -> float:
    c = Counter(tok)
    n = len(tok)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def _classes(tok: str) -> int:
    return (any(c.islower() for c in tok) + any(c.isupper() for c in tok) + any(c.isdigit() for c in tok)
            + any(not c.isalnum() for c in tok))


def amounts(s: str) -> list[Shape]:
    out = []
    for rx in (_MARKED, _GROUPED, _DECIMAL2):
        out += [Shape(m.start(), m.end(), "AMOUNT") for m in rx.finditer(s)]
    return out


def long_digits(s: str) -> list[Shape]:
    return [Shape(m.start(), m.end(), "LONG_DIGITS") for m in _LONG.finditer(s)]


_ALPHA_DIGITS = re.compile(r"^([A-Za-z]{3,})\d{1,4}$")
_SEP_SPLIT = re.compile(r"[-_/=+.:]+")
_CAMEL_SPLIT = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def identifier_of_words(tok: str) -> bool:
    """A token of natural-language words joined by separators or camelCase (FieldJustification, Content-Transfer-
    Encoding, /sites/Payroll/_api/web): not a random string. Any digit inside a part, or mostly short fragments, fails."""
    parts = [p for p in _SEP_SPLIT.split(tok) if p]
    letters = long_letters = 0
    for part in parts:
        if part.isdigit():
            continue
        if not part.isalpha():
            m = _ALPHA_DIGITS.match(part)                 # SHA384, AES256, UTF8: a word with a short numeric suffix
            if m is None:
                return False
            part = m.group(1)
        for w in _CAMEL_SPLIT.split(part):
            letters += len(w)
            if len(w) >= 3:
                long_letters += len(w)
    return letters > 0 and long_letters >= 0.7 * letters


def high_entropy(s: str) -> list[Shape]:
    out = []
    for m in _TOKEN.finditer(s):
        tok = m.group()
        if _classes(tok) < 2 or identifier_of_words(tok):
            continue
        # digit-free tokens are almost always identifiers; they need a clearly random alphabet to count
        if _entropy(tok) >= (3.5 if any(c.isdigit() for c in tok) else 4.0):
            out.append(Shape(m.start(), m.end(), "HIGH_ENTROPY"))
    return out


def shapes(s: str) -> list[Shape]:
    """Non-overlapping candidates for one view of the text."""
    cands = dates(s) + amounts(s) + long_digits(s) + high_entropy(s)
    cands.sort(key=lambda c: (_PRIO[c.kind], -(c.end - c.start), c.start))
    kept: list[Shape] = []
    for c in cands:
        if all(c.end <= k.start or c.start >= k.end for k in kept):
            kept.append(c)
    return sorted(kept, key=lambda c: c.start)
