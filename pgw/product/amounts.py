"""Numbers in free text, as values, in any written unit (SECURITY_BOUNDARY.md §6 N6).

LOCAL_COMPUTE withholds exact C2/C3 inputs, but the caller's free-text `instruction` could restate one of them in
another written form ("2.45 million rupees" for a withheld ₹24,50,000). String matching cannot see that, so the
instruction's numbers are parsed to values and compared numerically (pgw.product.local_compute).

`numbers(text)` returns (start, end, value, unit_given) for every number phrase:
  digits        2450000, 24,50,000 (Indian grouping), 2,450,000, 24.5, .5, Unicode digits
  units         hundred, thousand / k, lakh / lakhs / lac / lacs / L / LPA, crore / crores / cr / crs,
                million / mn / m / mil, billion / bn; spelt out or attached ("24.5L", "2.45M", "2450k")
  words         "twenty four lakh fifty thousand", "two point four five million", "a million", "one crore twenty
                lakh", and mixtures ("24 lakh 50 thousand")
Single-letter and abbreviated units (k, L, m, cr, mn, bn, mil, LPA) count only directly after a numeral.
`unit_given` is False for a bare numeral; the caller decides which implied scales to try.
Linear: one tokenising regex pass and constant work per token.
"""
from __future__ import annotations

import re

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_SCALE_WORDS = {"hundred": 1e2, "thousand": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
                "crore": 1e7, "crores": 1e7, "million": 1e6, "millions": 1e6, "billion": 1e9, "billions": 1e9}
# abbreviations: only right after a numeral
_SCALE_ABBR = {"k": 1e3, "l": 1e5, "lpa": 1e5, "cr": 1e7, "crs": 1e7, "m": 1e6, "mn": 1e6, "mil": 1e6, "bn": 1e9}

_NUM = r"\d{1,3}(?:[,，]\d{2,3})+(?!\d)(?:\.\d+)?|\d+(?:\.\d+)?|(?<![\w.])\.\d+"
_TOKEN = re.compile(rf"(?P<num>{_NUM})|(?P<word>[A-Za-z]+)")
_GAP = re.compile(r"[ \t\-]{0,3}")    # what may separate two tokens of one phrase
MAX_TEXT = 20_000


def _numeral(s: str) -> float:
    return float(s.replace(",", "").replace("，", ""))


def _classify(text: str):
    """Tokens as (start, end, kind, value): kind n (a numeral), w (a number word), t (a tens word), s (a scale),
    p ("point"), c ("and"), a ("a" before a scale word), x (anything else). Abbreviated units only after a numeral."""
    out = []
    for m in _TOKEN.finditer(text):
        s, e, num, word = m.start(), m.end(), m.group("num"), (m.group("word") or "").lower()
        if num is not None:
            out.append((s, e, "n", _numeral(num)))
        elif word in _UNITS:
            out.append((s, e, "w", float(_UNITS[word])))
        elif word in _TENS:
            out.append((s, e, "t", float(_TENS[word])))
        elif word in _SCALE_WORDS:
            out.append((s, e, "s", _SCALE_WORDS[word]))
        elif word in _SCALE_ABBR and out and out[-1][2] == "n" and _GAP.fullmatch(text, out[-1][1], s):
            out.append((s, e, "s", _SCALE_ABBR[word]))
        elif word in ("point", "and", "a"):
            out.append((s, e, {"point": "p", "and": "c", "a": "a"}[word], 0.0))
        else:
            out.append((s, e, "x", 0.0))
    return out


def numbers(text: str) -> list[tuple[int, int, float, bool]]:
    if not text or len(text) > MAX_TEXT:
        return []
    toks = _classify(text)
    out: list[tuple[int, int, float, bool]] = []
    i, n = 0, len(toks)

    def joined(j: int) -> bool:           # token j follows token j-1 within one phrase
        return j < n and _GAP.fullmatch(text, toks[j - 1][1], toks[j][0]) is not None

    while i < n:
        s0, _, k, _ = toks[i]
        if k not in "nwta" or (k == "a" and not (joined(i + 1) and toks[i + 1][2] == "s")):
            i += 1
            continue
        total, cur, last_scale, unit, end = 0.0, None, float("inf"), False, s0
        tens_open = False
        j = i
        while j < n:
            _, e, kj, v = toks[j]
            if j > i and not joined(j):
                break
            if kj == "n":
                if cur is not None:
                    break                                         # two numerals in a row: two numbers
                if total and not (joined(j + 1) and toks[j + 1][2] == "s" and toks[j + 1][3] < last_scale):
                    break                                         # a numeral after a scale joins only with a smaller one
                cur, tens_open = v, False
            elif kj in "wt":
                if cur is None:
                    if total and v >= last_scale:
                        break
                    cur, tens_open = v, kj == "t"
                elif tens_open and kj == "w" and v < 10:
                    cur, tens_open = cur + v, False
                elif cur >= 100 and cur % 100 == 0 and v < 100:   # "two hundred five"
                    cur, tens_open = cur + v, kj == "t"
                else:
                    break                                         # "four twenty": two numbers
            elif kj == "a":
                if cur is not None or not (joined(j + 1) and toks[j + 1][2] == "s"):
                    break
                cur = 1.0
            elif kj == "s":
                c = 1.0 if cur is None else cur
                if cur is None and total:
                    break
                if v == 100:
                    cur = c * 100
                elif v < last_scale:
                    total, cur, last_scale = total + c * v, None, v
                else:
                    break                                         # "2 lakh 3 lakh": a new number
                unit, tens_open = True, False
            elif kj == "p":
                frac, jj = "", j + 1
                while jj < n and joined(jj) and (toks[jj][2] == "w" and toks[jj][3] < 10 or
                                                 (toks[jj][2] == "n" and text[toks[jj][0]:toks[jj][1]].isdigit())):
                    frac += text[toks[jj][0]:toks[jj][1]] if toks[jj][2] == "n" else str(int(toks[jj][3]))
                    jj += 1
                if cur is None or not frac:
                    break
                cur = cur + float("0." + frac)
                end, j = toks[jj - 1][1], jj
                continue
            elif kj == "c":
                if not (total and joined(j + 1) and toks[j + 1][2] in "wt"):
                    break                                         # "and" only inside a phrase in words
            else:
                break
            end = e
            j += 1
        if end > s0:
            out.append((s0, end, total + (cur or 0.0), unit))
        i = max(j, i + 1)
    return out


_YEARISH = range(1900, 2101)


def candidates(value: float, unit_given: bool, text: str) -> tuple[float, ...]:
    """The values a written number may stand for. With a unit: itself. A bare numeral may also carry an implied
    unit (24.5 meaning lakh, 2.45 meaning million, 2450 meaning thousand) when it has at least two significant
    digits; a whole number that reads as a year gets no implied unit."""
    if unit_given:
        return (value,)
    sig = len(re.sub(r"\D", "", text).strip("0"))
    if sig < 2:
        return (value,)
    scales = (1e5, 1e6, 1e7) if value.is_integer() and int(value) in _YEARISH else (1e3, 1e5, 1e6, 1e7)
    return (value, *(value * s for s in scales))


def near(x: float, v: float, tolerance: float = 0.005) -> bool:
    """x equals v or is within ±tolerance (relative) of it."""
    return x == v or abs(x - v) <= tolerance * abs(v)
