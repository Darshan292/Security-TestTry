"""Shape backstop for number-shaped identifiers (audit S5/S6). One generic type: IDENTIFIER (C3-like, never restored).

The scheme recognizers (recognizers.py) know Aadhaar, PAN, cards, bank accounts with a cue, ... An identifier of any
other scheme (UAN, ESIC, passport, voter ID, driving licence, GSTIN, IBAN, a policy number) or a known scheme without
its cue word (a bare 12-digit Aadhaar, an account number with no "a/c") used to cross raw. Instead of one regex per
scheme, this module withholds whatever is SHAPED like an identifier and is not recognisably benign.

Candidates are chains of code-like chunks joined by single separators (space, hyphen, dot, slash). A chunk is
code-like if it contains a digit, or is an upper-case word of at most five letters ("ABCDE 1234 F", "KA 51 AB 4471").
Lower-case and Title-case words never join, so prose stays out.
  (a) digits only: 9-18 digits once the separators are removed;
  (b) a checksum-valid (Verhoeff) 12-digit Aadhaar in ANY grouping, also with wider gaps ("2345 - 6789 - 0123") or
      inside a longer digit run;
  (c) letters and at least 4 digits, 7-20 characters once the separators are removed.
Upper-case labels at the edges ("UAN 1012 3456 7890") are left outside the span when the rest still qualifies.

Benign shapes are exempt by general rules, never by listing examples:
  dates in any common format (cut out of a chain, the rest is judged alone), compact dates and timestamps
  (YYYYMMDD[hhmmss]), times, decimal amounts, currency codes (an amount next to INR/USD is money: the COMPENSATION
  detector's job), dotted versions and network addresses (two or more dots and nothing else), section/form/rule
  references ("Section 80C", "Form 16"), IFSC codes (policy ALLOWs them), machine strings (lower-case hex, mixed-case
  base64-like tokens, codes carrying a run of six or more letters, which no personal identifier scheme has).
Phone numbers and amounts that a scheme recognizer already found keep that recognizer's type: backstop spans score
below every recognizer, so any overlapping detection wins resolution.
Company reference formats (ticket and offer ids such as PAY-88231) are exempt only through the signed contract field
`business_identifiers` (pgw.product.contracts), applied by the pipeline with `exempt_business`.

Linear in the input: one possessive chain regex, constant work per chain, and chains longer than _MAX_CHAIN characters
(floods, tables) are skipped.
"""
from __future__ import annotations

import re

from pgw.types import Detection, EntityType as T

from .recognizers import _DATE, _date_ok, verhoeff_valid

SOURCE = "D2c:idshape"
SCORE = 0.4            # below every recognizer, cue, roster and NER score: a known type always wins an overlap

_SEPS = " -./"
# A code-like chunk: (letters)digit(alnum) | an upper-case word of 1-5 letters. Atomic groups keep it linear.
# A digit chunk followed by ":<digit>" is the hour of a time ("I1219 18:05:11"), so it does not join.
_CHUNK = r"(?:(?>[A-Za-z]*)\d(?>[A-Za-z0-9]*)(?!:\d)|[A-Z]{1,5}(?![A-Za-z0-9]))"
_CHAIN = re.compile(r"(?<![A-Za-z0-9])" + _CHUNK + r"(?:[ \-./]" + _CHUNK + r")*+")
_PIECE = re.compile(r"[A-Za-z0-9]+")
_MAX_CHAIN = 120       # a longer chain is a flood or a table row, not one identifier
_MAX_GROUPS = 16       # Verhoeff windows are only searched in digit runs of at most this many groups

# (b) digit groups with gaps of 1-3 separator characters
_DIGRUN = re.compile(r"(?<![A-Za-z0-9])\d+(?:[ \t\-./,]{1,3}\d+)*+(?![A-Za-z0-9])")

_CURRENCY = {"INR", "RS", "USD", "EUR", "GBP", "AED", "SGD", "CAD", "AUD", "JPY", "CHF", "LPA"}
_TIME_WORDS = {"AM", "PM", "IST", "UTC", "GMT", "HRS", "HR", "H"}
_FISCAL = re.compile(r"(?i)(?<![A-Za-z0-9])(?:FY|AY|CY|PY|Q[1-4]|H[12])[ \-]?'?(?:19|20)?\d{2}(?:[\-/](?:19|20)?\d{2})?"
                     r"(?![A-Za-z0-9])")
_YEAR_RANGE = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}[\-/](?:(?:19|20)\d{2}|\d{2})(?![A-Za-z0-9])")
_YEAR = re.compile(r"(?:19[5-9]\d|20[0-3]\d)")
_MASK = re.compile(r"[Xx*]{3,}")           # an already-masked part ("XXXX XXXX XXXX 6701")
_TOLL_FREE = re.compile(r"1(?:800|860)\d{6,7}")   # toll-free / organisation numbers, phone-shaped
_TIME = re.compile(r"(?:[01]?\d|2[0-3])[.:][0-5]\d(?:[.:][0-5]\d)?")
_COMPACT_TS = re.compile(r"(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:\d{2}){0,3}")
_IFSC = re.compile(r"[A-Z]{4}0[A-Z0-9]{6}")
_HEX_LOWER = re.compile(r"[0-9a-f]*[a-f][0-9a-f]*")
_HEX_UPPER = re.compile(r"[0-9A-F]{16,}")
_LETTER_RUN = re.compile(r"[A-Za-z]{6,}")
_REFERENCE = re.compile(
    r"(?i)(?<![a-z])(?:section|sec|s|u/s|form|rule|clause|article|art|schedule|para|paragraph|chapter|annexure|"
    r"annex|appendix|regulation|reg|circular|notification|item|table|figure|fig|page|exhibit)\.?[ \t]*"
    r"(?:no\.?[ \t]*)?$")


def _alpha_only(p: str) -> bool:
    return p.isalpha()


def _qualifies(pieces: list[str], seps: list[str]) -> bool:
    """The shape rule on one chain segment (pieces joined by the separators in `seps`)."""
    compact = "".join(pieces)
    digits = sum(c.isdigit() for c in compact)
    letters = len(compact) - digits
    if letters == 0:
        if not 9 <= digits <= 18:
            return False
        if len(pieces) == 2 and seps == ["."] and len(pieces[1]) <= 2:
            return False                       # a decimal amount
        return not (_COMPACT_TS.fullmatch(compact) or _TOLL_FREE.fullmatch(compact))
    if len(pieces) == 2 and seps == [" "] and pieces[0].isalpha() and pieces[1].isdigit() and len(pieces[1]) <= 5:
        return False                           # a name and a short number: a standard or a reference ("ISO 9001")
    if digits < 4 or not 7 <= len(compact) <= 20:
        return False
    if _IFSC.fullmatch(compact.upper()):
        return False                           # a bank branch code (policy ALLOWs IFSC)
    for p in pieces:
        if _LETTER_RUN.search(p):
            return False                       # a word or a word-like code ("KAFPAYSAL9372")
        if any(c.islower() for c in p) and (any(c.isupper() for c in p) or _HEX_LOWER.fullmatch(p)):
            return False                       # mixed case (base64, MIME, camelCase) or lower-case hex
    return not _HEX_UPPER.fullmatch(compact)


def _segments(chain: str) -> list[list[tuple[int, int]]]:
    """Piece spans of a chain, split at benign pieces: dates, times, currency codes and time words."""
    cut = [False] * len(chain)
    for m in _DATE.finditer(chain):
        if _date_ok(m, chain):
            for i in range(m.start(), m.end()):
                cut[i] = True
    for m in _YEAR_RANGE.finditer(chain):     # a year range ("2026-27", "IALM 2012-14")
        for i in range(m.start(), m.end()):
            cut[i] = True
    for m in _FISCAL.finditer(chain):
        for i in range(m.start(), m.end()):
            cut[i] = True
    for m in _TIME.finditer(chain):
        a, b = m.span()
        if (a == 0 or not chain[a - 1].isalnum()) and (b == len(chain) or not chain[b].isalnum()):
            # "10.30" is a time only between non-digits and not as a part of a longer dotted number
            if not ((a > 1 and chain[a - 1] == "." and chain[a - 2].isdigit()) or
                    (b + 1 < len(chain) and chain[b] == "." and chain[b + 1].isdigit())):
                for i in range(a, b):
                    cut[i] = True
    segs: list[list[tuple[int, int]]] = [[]]
    for m in _PIECE.finditer(chain):
        a, b = m.span()
        p = m.group()
        year_slash = len(p) == 4 and _YEAR.fullmatch(p) and "/" in chain[max(0, a - 1):a] + chain[b:b + 1]
        if (any(cut[a:b]) or p.upper() in _CURRENCY or p.upper() in _TIME_WORDS or _MASK.fullmatch(p)
                or year_slash):        # a year in a dated reference number ("508/8884/2026", "WB/PSARA/2024/122")
            if segs[-1]:
                segs.append([])
            continue
        segs[-1].append((a, b))
    return [s for s in segs if s]


def _judge(chain: str, seg: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Spans (chain offsets) of identifiers in one segment. The whole segment if it qualifies, with edge labels
    trimmed while the rest still qualifies. Otherwise (e.g. a code longer than 20 characters, such as a long IBAN or a
    PF account path) its digit runs are judged alone by rule (a)."""
    def ok(lo: int, hi: int) -> bool:
        pieces = [chain[a:b] for a, b in seg[lo:hi]]
        seps = [chain[seg[i][1]:seg[i + 1][0]] for i in range(lo, hi - 1)]
        if any(len(s) != 1 for s in seps):
            return False
        if len(seps) >= 2 and all(s == "." for s in seps):
            return False                       # a dotted version or a network address
        return _qualifies(pieces, seps)

    lo, hi = 0, len(seg)
    if not ok(lo, hi):
        return _digit_runs(chain, seg, ok)
    while hi - lo > 1:
        first = chain[seg[lo][0]:seg[lo][1]]
        rest_plain = hi - lo == 2 or all(chain[a:b].isdigit() for a, b in seg[lo + 1:hi])
        if _alpha_only(first) and (len(first) >= 3 or rest_plain) and ok(lo + 1, hi):
            lo += 1
            continue
        last = chain[seg[hi - 1][0]:seg[hi - 1][1]]
        rest_plain = hi - lo == 2 or all(chain[a:b].isdigit() for a, b in seg[lo:hi - 1])
        if _alpha_only(last) and (len(last) >= 3 or rest_plain) and ok(lo, hi - 1):
            hi -= 1
            continue
        break
    return [(seg[lo][0], seg[hi - 1][1])]


_INNER_DIGITS = re.compile(r"(?<!\d)\d{9,18}(?!\d)")


def _digit_runs(chain: str, seg: list[tuple[int, int]], ok) -> list[tuple[int, int]]:
    """Rule (a) on the digits of a segment that did not qualify as a whole: maximal runs of all-digit pieces, and
    9-18 digit runs inside a long letter-and-digit piece ("GB29NWBK60161331926819")."""
    out = []
    i = 0
    while i < len(seg):
        a, b = seg[i]
        if not chain[a:b].isdigit():
            if b - a > 20:
                out += [(a + m.start(), a + m.end()) for m in _INNER_DIGITS.finditer(chain, a, b)
                        if not _COMPACT_TS.fullmatch(m.group())]
            i += 1
            continue
        j = i
        while j + 1 < len(seg) and chain[seg[j + 1][0]:seg[j + 1][1]].isdigit():
            j += 1
        if (i, j + 1) != (0, len(seg)) and ok(i, j + 1):
            out.append((seg[i][0], seg[j][1]))
        i = j + 1
    return out


def _aadhaar_windows(text: str, out: list[Detection], taken: list[tuple[int, int]]) -> None:
    """(b): a Verhoeff-valid 12-digit number in any grouping, including a window of a longer run of groups."""
    for m in _DIGRUN.finditer(text):
        run = m.group()
        if len(run) > 3 * _MAX_CHAIN:
            continue
        groups = [(g.start() + m.start(), g.end() + m.start()) for g in re.finditer(r"\d+", run)]
        if len(groups) > _MAX_GROUPS or sum(b - a for a, b in groups) < 12:
            continue
        for i in range(len(groups)):
            n = 0
            for j in range(i, len(groups)):
                n += groups[j][1] - groups[j][0]
                if n >= 12:
                    break
            if n != 12:
                continue
            s, e = groups[i][0], groups[j][1]
            digits = "".join(text[a:b] for a, b in groups[i:j + 1])
            if digits[0] in "01" or not verhoeff_valid(digits):
                continue
            if any(a < e and s < b for a, b in taken):
                continue
            if _benign_context(text, s) or any(_date_ok(d, run) for d in _DATE.finditer(run)
                                               if d.start() + m.start() < e and s < d.end() + m.start()):
                continue
            out.append(Detection(s, e, T.IDENTIFIER, SCORE, SOURCE))
            taken.append((s, e))


def _benign_context(text: str, s: int) -> bool:
    return bool(_REFERENCE.search(text[max(0, s - 24):s]))


def detect(text: str) -> list[Detection]:
    """IDENTIFIER candidates on one string (offsets in that string)."""
    out: list[Detection] = []
    if not text:
        return out
    for m in _CHAIN.finditer(text):
        chain = m.group()
        if len(chain) < 7 or len(chain) > _MAX_CHAIN or sum(c.isdigit() for c in chain) < 4:
            continue
        for seg in _segments(chain):
            for a, b in _judge(chain, seg):
                s, e = a + m.start(), b + m.start()
                if not _benign_context(text, s):
                    out.append(Detection(s, e, T.IDENTIFIER, SCORE, SOURCE))
    _aadhaar_windows(text, out, [(d.start, d.end) for d in out])
    return out


def exempt_business(text: str, dets: list[Detection], patterns) -> list[Detection]:
    """Drop backstop spans that are a company reference format declared in the signed contract (fullmatch)."""
    if not patterns:
        return dets
    return [d for d in dets if not (d.type is T.IDENTIFIER and d.source.startswith(SOURCE)
                                    and any(rx.fullmatch(text[d.start:d.end]) for rx in patterns))]


# ------------------------------------------------------------------------------- business_identifiers (contracts)

# A restricted, linear dialect: anchors, literals, \d, simple classes, (?:a|b) groups of literals, and bounded
# quantifiers ({n} / {n,m}, m <= 12, or ?) on a single atom. No '.', '*', '+', unbounded or nested repetition.
_BID_ATOM = re.compile(r"\\d|\\[\-./_#]|\[(?:[A-Za-z0-9]|[A-Za-z0-9]-[A-Za-z0-9]){1,6}\]|[A-Za-z0-9\-/_#:]"
                       r"|\(\?:[A-Za-z0-9\-]{1,12}(?:\|[A-Za-z0-9\-]{1,12}){0,15}\)")
_BID_QUANT = re.compile(r"\{(\d{1,2})(?:,(\d{1,2}))?\}|\?")
_BID_PREFIX = re.compile(r"\^(?:[A-Z]{2,}|\(\?:[A-Z]{2,}[A-Z0-9]*(?:\|[A-Z]{2,}[A-Z0-9]*)*\))")
BID_MAX_PATTERNS = 20
BID_MAX_LEN = 64
# Generic personal-identifier samples. A business pattern that fullmatches any of them would exempt that whole shape
# from the backstop, so the contract is refused at load.
GENERIC_SAMPLES = (
    "123456789", "1234567890", "123456789012", "12345678901234", "234567890124", "1234 5678 9012", "1234-5678-9012",
    "ABCPK1234F", "ABCPK 1234 F", "abcpk1234f", "Z4872631", "A1234567", "AB1234567", "TNK4821937", "ABC1234567",
    "KA0520190012345", "KA05 20190012345", "KA51AB4471", "KA 51 AB 4471", "22ABCPK1234F1Z5", "GB29NWBK60161331926819",
    "AB123456C", "MH/BAN/0012345/000/0001234", "ABC-1234567", "AB-123456", "XYZ-12345", "QX-2291", "1009123456789",
)


class BusinessPatternError(ValueError):
    pass


def compile_business_identifiers(raw) -> tuple[re.Pattern, ...]:
    """Validate and compile a contract's business_identifiers: anchored (^...$), length-limited, linear patterns
    with a literal upper-case prefix, none of which matches a generic identifier sample."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > BID_MAX_PATTERNS:
        raise BusinessPatternError(f"business_identifiers: a list of at most {BID_MAX_PATTERNS} patterns")
    out = []
    for p in raw:
        if not isinstance(p, str) or not 4 <= len(p) <= BID_MAX_LEN:
            raise BusinessPatternError(f"business_identifiers: each pattern is a string of 4-{BID_MAX_LEN} characters")
        if not (p.startswith("^") and p.endswith("$")):
            raise BusinessPatternError(f"business_identifiers: {p!r} must be anchored with ^ and $")
        if not _BID_PREFIX.match(p):
            raise BusinessPatternError(f"business_identifiers: {p!r} must start with a literal upper-case prefix")
        body, pos, quantifiable = p[1:-1], 0, False
        while pos < len(body):
            q = _BID_QUANT.match(body, pos)
            if q:
                if not quantifiable:
                    raise BusinessPatternError(f"business_identifiers: {p!r}: quantifier without a single atom")
                if q.group(1) is not None and int(q.group(2) or q.group(1)) > 12:
                    raise BusinessPatternError(f"business_identifiers: {p!r}: repetition bound above 12")
                pos, quantifiable = q.end(), False
                continue
            a = _BID_ATOM.match(body, pos)
            if not a:
                raise BusinessPatternError(f"business_identifiers: {p!r}: unsupported syntax at {pos + 1} "
                                           "(literals, \\d, [classes], (?:A|B), bounded {n,m} only)")
            pos, quantifiable = a.end(), not a.group().startswith("(")
        try:
            rx = re.compile(p)
        except re.error as e:
            raise BusinessPatternError(f"business_identifiers: {p!r}: {e}") from None
        hit = [g for g in GENERIC_SAMPLES if rx.fullmatch(g)]
        if hit:
            raise BusinessPatternError(f"business_identifiers: {p!r} also matches the generic identifier {hit[0]!r}")
        out.append(rx)
    return tuple(out)
