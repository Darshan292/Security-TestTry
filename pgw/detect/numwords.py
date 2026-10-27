"""Numbers written in words (audit S5/S6 residual): a digit-by-digit view, and amounts in Indian units.

1. Digit-by-digit view (`digit_view`). An identifier spoken digit by digit ("seven one five two, eight three nine
   zero, six four four one", "nine eight four five double zero ...") becomes digits in a D0 view, so every
   structured recognizer (phone, account, Aadhaar, ...) and the identifier-shape backstop see it exactly as if it had
   been written in digits. Tokens: the English digit words (zero / oh / nil / nought, one ... nine), "double X" and
   "triple X", and digit groups already written in digits, joined by spaces, hyphens or commas, in any case.
   Spaces and hyphens join; a comma starts a new digit group ("7152 8390 6441"). Only a run that spells out at
   least MIN_WORD_DIGITS digits in words is rewritten, so prose ("one or two days", "one two-day workshop") never
   is. A run longer than MAX_RUN_DIGITS digits (a flood, or a table of words) is left alone, like a digit run longer
   than any identifier.
   Offsets: every digit of a word maps to the first character of that word ("double" for the first digit of "double
   five", "five" for the second), and the pipeline extends a span that ends inside a word to the end of that word,
   so the replaced original text is exactly the words that were read.

2. Amounts with Indian units (`unit_amounts`). A number phrase in words or digits that contains lakh / lac / crore
   (singular, plural or "cr"), with "thousand" and "hundred" allowed only around them, is a money amount:
   "thirty one lakh eighty five thousand", "1.2 crore", "one crore twenty lakh", "12 lakh 50 thousand".

Linear: each regex is applied once with possessive repetition; the per-run work is proportional to the run.
"""
from __future__ import annotations

import re

MIN_WORD_DIGITS = 6
MAX_RUN_DIGITS = 40

_DIGIT_WORDS = {"zero": "0", "oh": "0", "nil": "0", "nought": "0", "naught": "0", "one": "1", "two": "2",
                "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_DW = "(?:" + "|".join(sorted(_DIGIT_WORDS, key=len, reverse=True)) + ")"
_REPEAT = {"double": 2, "triple": 3}
# one token: "double five" | a digit word | a digit group
_TOK = rf"(?:(?:double|triple)[ \t\-]+{_DW}(?![A-Za-z0-9])|{_DW}(?![A-Za-z0-9])|\d+(?![A-Za-z0-9.]|\.\d))"
_SEP = r"(?:[ \t]*,[ \t]*|[ \t]*-[ \t]*|[ \t]+)"
_RUN = re.compile(rf"(?i)(?<![A-Za-z0-9.,\-]){_TOK}(?:{_SEP}{_TOK})*+")
_PART = re.compile(rf"(?i)(?P<rep>double|triple)[ \t\-]+(?P<rw>{_DW})|(?P<w>{_DW})|(?P<d>\d+)")


def _parts(run: str):
    """(start, end, digits, word_start_for_extra_digits | None, is_word) per token of a run."""
    for m in _PART.finditer(run):
        if m.group("rep"):
            d = _DIGIT_WORDS[m.group("rw").lower()]
            yield m.start(), m.end(), d * _REPEAT[m.group("rep").lower()], m.start("rw"), True
        elif m.group("w"):
            yield m.start(), m.end(), _DIGIT_WORDS[m.group("w").lower()], None, True
        else:
            yield m.start(), m.end(), m.group("d"), None, False


def digit_view(s: str, omap: list[int]) -> tuple[str, list[int]] | None:
    """The D0 view of `s` (with its offset map `omap`, as normalize() returns) with digit-by-digit runs rewritten as
    digits; None when there is no such run."""
    low = s.lower()
    if sum(w in low for w in _DIGIT_WORDS) < 2:       # fast prefilter (str.find), no regex scan of a flood
        return None
    out: list[str] = []
    om: list[int] = []
    pos = 0
    changed = False
    for m in _RUN.finditer(s):
        parts = list(_parts(m.group()))
        word_digits = sum(len(p[2]) for p in parts if p[4])
        total = sum(len(p[2]) for p in parts)
        if word_digits < MIN_WORD_DIGITS or total > MAX_RUN_DIGITS:
            continue
        base = m.start()
        out.append(s[pos:base])
        om.extend(omap[pos:base])
        prev_end = None
        for a, b, digits, extra_at, is_word in parts:
            if prev_end is not None and "," in s[base + prev_end:base + a]:
                out.append(" ")                                          # a comma starts a new digit group
                om.append(omap[base + prev_end])
            out.append(digits)
            if not is_word:                                              # a digit group: kept as written
                om.extend(omap[base + a:base + b])
            else:                                                        # "double five": 5 -> "double", 5 -> "five"
                om.append(omap[base + a])
                om.extend([omap[base + (a if extra_at is None else extra_at)]] * (len(digits) - 1))
            prev_end = b
        pos = m.end()
        changed = True
    if not changed:
        return None
    out.append(s[pos:])
    om.extend(omap[pos:])
    return "".join(out), om


def to_digits(value: str) -> str:
    """The digits a value spells, words included ("nine eight 45" -> "9845"); used to canonicalise identifiers."""
    out = []
    for m in _PART.finditer(value):
        if m.group("rep"):
            out.append(_DIGIT_WORDS[m.group("rw").lower()] * _REPEAT[m.group("rep").lower()])
        elif m.group("w"):
            if re.fullmatch(r"(?i)" + _DW, m.group("w")) and _word_bounded(value, m.start(), m.end()):
                out.append(_DIGIT_WORDS[m.group("w").lower()])
        else:
            out.append(m.group("d"))
    return "".join(out)


_ONLY_DIGIT_WORDS = re.compile(rf"(?i)[ \t\-,]*(?:(?:double|triple)[ \t\-]+)?{_DW}(?:[ \t\-,]+(?:(?:double|triple)[ \t\-]+)?{_DW})*+[ \t\-,]*")


def only_digit_words(s: str) -> bool:
    """True when `s` is nothing but digit words ("Six Zero Two"): never a person's name."""
    return bool(_ONLY_DIGIT_WORDS.fullmatch(s))


def _word_bounded(s: str, a: int, b: int) -> bool:
    return (a == 0 or not s[a - 1].isalpha()) and (b == len(s) or not s[b].isalpha())


# ---------------------------------------------------------------------------------------- amounts with Indian units

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
           "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000, "crs": 10_000_000}
_BIG = 100_000          # lakh and above: the scales that make a phrase an Indian money amount
_NUMWORD = "|".join(sorted([*_UNITS, *_TENS], key=len, reverse=True))
_SCALEWORD = "|".join(sorted(_SCALES, key=len, reverse=True))
_ATOK = (rf"(?:(?:{_NUMWORD}|{_SCALEWORD}|and|a)(?![A-Za-z0-9])"
         rf"|\d+(?:\.\d+)?(?![0-9]|\.\d|,\d)(?=[ \t\-]|(?:{_SCALEWORD})(?![A-Za-z0-9])|[^A-Za-z0-9]|$))")
_ASEP = rf"(?:[ \t]*-[ \t]*|[ \t]+|(?<=\d)(?=(?:{_SCALEWORD})(?![A-Za-z0-9])))"
_APHRASE = re.compile(rf"(?i)(?<![A-Za-z0-9,])(?<!\d\.){_ATOK}(?:{_ASEP}{_ATOK})*+")
_ATOKEN = re.compile(rf"(?i)(?P<w>{_NUMWORD}|{_SCALEWORD}|and|a)(?![A-Za-z0-9])|(?P<n>\d+(?:\.\d+)?)")
_MAX_PHRASE = 200


def _tokens(phrase: str):
    """(start, end, kind, value): kind d (a numeral), n (a units word), t (a tens word), s (a scale), c ("and"),
    a ("a")."""
    for m in _ATOKEN.finditer(phrase):
        if m.group("n"):
            yield m.start(), m.end(), "d", float(m.group("n"))
            continue
        w = m.group("w").lower()
        if w in _UNITS:
            yield m.start(), m.end(), "n", float(_UNITS[w])
        elif w in _TENS:
            yield m.start(), m.end(), "t", float(_TENS[w])
        elif w in _SCALES:
            yield m.start(), m.end(), "s", float(_SCALES[w])
        else:
            yield m.start(), m.end(), "c" if w == "and" else "a", 1.0


def _phrases(toks: list) -> list[list]:
    """Split a token run into amount phrases. A phrase starts with a number (or "a" before a scale); a scale follows
    a number; a units word may follow a tens word ("twenty two"); a new number follows a scale or "and" (only
    between a scale and a number). Anything else closes the phrase: two numerals or two units words in a row ("2024
    2 lakh", "two three lakh") are separate numbers, not one amount."""
    out: list[list] = []
    cur: list = []

    def close():
        while cur and cur[-1][2] in "ca":
            cur.pop()
        if cur:
            out.append(list(cur))
        cur.clear()

    for i, t in enumerate(toks):
        k = t[2]
        nxt = toks[i + 1][2] if i + 1 < len(toks) else None
        prev = cur[-1][2] if cur else None
        if k == "a":
            if nxt == "s" and prev in (None, "s", "c"):
                cur.append(t)
            else:
                close()
        elif k == "c":
            if prev == "s" and nxt in ("d", "n", "t", "a"):
                cur.append(t)
            else:
                close()
        elif k == "s":
            if prev in ("d", "n", "t", "a"):
                cur.append(t)
            else:
                close()
        elif prev in (None, "s", "c") or (prev == "t" and k == "n" and 0 < t[3] < 10):
            cur.append(t)
        else:
            close()
            cur.append(t)
    close()
    return out


def _value(ph: list) -> float:
    total, current = 0.0, 0.0
    for _, _, k, v in ph:
        if k in ("d", "n", "t"):
            current += v
        elif k == "a":
            current = 1.0
        elif k == "s":
            if v == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * v
                current = 0.0
    return total + current


def unit_amounts(text: str) -> list[tuple[int, int, float]]:
    """(start, end, value in rupees) of every number phrase with a lakh/crore scale."""
    out: list[tuple[int, int, float]] = []
    low = text.lower()
    if "lakh" not in low and "lac" not in low and "cr" not in low:
        return out
    for m in _APHRASE.finditer(text):
        if len(m.group()) > _MAX_PHRASE:
            continue
        toks = list(_tokens(m.group()))
        if not any(t[2] == "s" and t[3] >= _BIG for t in toks):
            continue
        for ph in _phrases(toks):
            if not any(t[2] == "s" and t[3] >= _BIG for t in ph):
                continue
            out.append((m.start() + ph[0][0], m.start() + ph[-1][1], _value(ph)))
    return out


def amount_value(value: str) -> float | None:
    """Rupee value of an amount written with Indian units in words or digits ("twenty two lakh", "1.2 crore")."""
    found = unit_amounts(value)
    return found[0][2] if found else None
