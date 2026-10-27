"""D0 normalisation with an offset map back to the original string.

Detectors run on the normalised view; every span is mapped back to the original
text before transformation, so the original surface (including any zero-width
characters an attacker inserted) is what gets replaced.
"""
from __future__ import annotations

import re
import unicodedata

from .detect.numwords import digit_view

ZERO_WIDTH = frozenset("​‌‍⁠﻿­᠎")

# Confusables fold (Cyrillic / Greek / Armenian / IPA / small-cap look-alikes -> Latin). Detection view
# only. This is a static, hand-curated subset in the spirit of the Unicode "confusables" data
# (UTS #39, confusables.txt): every entry is a character whose glyph is (near-)identical to an ASCII
# letter in common fonts. Mathematical alphanumerics, fullwidth forms, Roman-numeral letters etc. are
# NOT listed because NFKC (applied first) already maps them to ASCII.
_CONFUSABLES = {
    # Cyrillic lower
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0443": "y",
    "\u0445": "x", "\u0456": "i", "\u0458": "j", "\u0455": "s", "\u04bb": "h", "\u0501": "d",
    "\u051b": "q", "\u051d": "w", "\u04cf": "l", "\u0475": "v", "\u0491": "r", "\u044c": "b",
    "\u0433": "r", "\u0454": "e", "\u0457": "i", "\u04c0": "I",
    # Cyrillic upper
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M", "\u041d": "H",
    "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T", "\u0425": "X", "\u0406": "I",
    "\u0408": "J", "\u0405": "S", "\u0423": "Y", "\u051a": "Q", "\u051c": "W", "\u0474": "V",
    "\u0417": "3", "\u04ae": "Y", "\u0492": "F", "\u04a0": "K",
    # Greek lower
    "\u03b1": "a", "\u03bf": "o", "\u03c1": "p", "\u03bd": "v", "\u03c4": "t", "\u03b9": "i",
    "\u03ba": "k", "\u03b5": "e", "\u03c5": "u", "\u03c7": "x", "\u03b3": "y", "\u03b7": "n",
    "\u03bc": "u", "\u03c9": "w", "\u03f2": "c", "\u03f3": "j", "\u03f1": "p", "\u03f5": "e",
    "\u03c3": "o", "\u03b2": "b",
    # Greek upper
    "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H", "\u0399": "I",
    "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O", "\u03a1": "P", "\u03a4": "T",
    "\u03a5": "Y", "\u03a7": "X", "\u03f9": "C", "\u037f": "J",
    # Armenian
    "\u0570": "h", "\u0578": "n", "\u057d": "u", "\u0585": "o", "\u0581": "g", "\u0563": "q",
    "\u0566": "q", "\u054d": "U", "\u0555": "O", "\u0531": "U", "\u0547": "N", "\u054c": "R",
    "\u0555": "O", "\u0548": "n",
    # IPA / Latin extended look-alikes and small capitals
    "\u0251": "a", "\u0261": "g", "\u0269": "i", "\u028b": "v", "\u0131": "i", "\u0237": "j",
    "\u01c0": "l", "\u2113": "l", "\u0578": "n",
    "\u1d00": "a", "\u0299": "b", "\u1d04": "c", "\u1d05": "d", "\u1d07": "e", "\u0262": "g",
    "\u029c": "h", "\u026a": "i", "\u1d0a": "j", "\u1d0b": "k", "\u029f": "l", "\u1d0d": "m",
    "\u0274": "n", "\u1d0f": "o", "\u1d18": "p", "\u0280": "r", "\u1d1b": "t", "\u1d1c": "u",
    "\u1d20": "v", "\u1d21": "w", "\u028f": "y", "\u1d22": "z",
}


def normalize(text: str) -> tuple[str, list[int]]:
    """Return (normalised_text, omap) where omap[i] is the original index of normalised char i.

    omap has one extra trailing entry equal to len(text), so end offsets map cleanly.
    """
    out: list[str] = []
    omap: list[int] = []
    for i, ch in enumerate(text):
        if ch in ZERO_WIDTH:
            continue
        n = unicodedata.normalize("NFKC", ch)
        for c in n:
            c = _CONFUSABLES.get(c, c)
            if c > "\x7f" and c.isdigit():          # Devanagari / Arabic-Indic / ... digits -> ASCII
                d = unicodedata.decimal(c, None)
                if d is not None:
                    c = str(d)
            if c.isspace() and c not in "\n\t":   # keep line/column structure for CSV/TSV/logs
                c = " "
            out.append(c)
            omap.append(i)
    omap.append(len(text))
    return "".join(out), omap


def word_end(text: str, end: int) -> int:
    """Extend an original end offset that falls inside a word to the end of that word (numwords view: a digit maps
    to the first character of the word that spells it)."""
    while 0 < end < len(text) and (text[end].isalpha() or text[end] in ZERO_WIDTH) and \
            (text[end - 1].isalpha() or text[end - 1] in ZERO_WIDTH):
        end += 1
    return end


def to_original(omap: list[int], start: int, end: int) -> tuple[int, int]:
    """Map a normalised [start, end) span to the original string."""
    if end <= start:
        return omap[start], omap[start]
    return omap[start], omap[end - 1] + 1


# --- multi-view de-obfuscation ------------------------------------------------------------------
#
# A "view" is (name, text, omap) with the same contract as normalize(): omap[i] is the ORIGINAL index of
# view char i, plus a trailing len(original), so to_original() works on any view span. Views are derived
# from the base view by edits (delete chars / replace a span by one char); composing maps is just reading
# the base omap at the kept positions. A replacement char maps to the FIRST original char of the span it
# replaced ("rahul [at] acme" -> "@" maps to the space before "[at]"), so a span that starts at a
# replacement covers the whole token; a span that ENDS exactly on a replacement covers only the token's
# first char (only bracket/space chars, never a payload char).

_ALNUM = r"[^\W_]"
# >= 3 single alphanumeric chars separated by exactly one space or dot ("R a h u l", "9.8.2", "R.a.h.u.l")
# (an apostrophe or hyphen may also be a spaced-out token: "D ' S i l v a", "A n n e - M a r i e")
_SPACED_RUN = re.compile(
    rf"(?<!{_ALNUM}){_ALNUM}(?:[ .](?:{_ALNUM}(?!{_ALNUM})|['\u2019-](?=[ .]{_ALNUM}))){{2,}}(?<={_ALNUM})")
# a lone initial with a detached dot ("W .   N i n g ...") directly before a spaced run
_SPACED_INIT = re.compile(rf"(?<!{_ALNUM}){_ALNUM} \.(?=[ ]+{_ALNUM}[ .](?:{_ALNUM}|['\u2019-])[ .]{_ALNUM})")
_GAP_OK = re.compile(r"[^\w\n]+")

_DIGIT_SEP = "\\-./\u2010\u2011\u2012\u2013\u2014\u2212_"
# (?<![0-9]): only try a chain at the start of a digit run. Without it a long run with no separator
# (which can never match) was rescanned to its end from every offset, O(n^2). Same matches otherwise.
_DIGIT_CHAIN = re.compile(rf"(?<![0-9])[0-9]+(?:(?: ?[{_DIGIT_SEP}] ?| )[0-9]+)+")
_DIGITS = re.compile(r"[0-9]+")
_JOIN_MIN, _JOIN_MAX = 9, 19

_BR_OPEN, _BR_CLOSE = r"[\[({<]", r"[\])}>]"
_AT = rf"(?:[ ]*{_BR_OPEN}[ ]*at[ ]*{_BR_CLOSE}[ ]*|[ ]+at[ ]+|[ ]*@[ ]*)"
_DOT = rf"(?:[ ]*{_BR_OPEN}[ ]*dot[ ]*{_BR_CLOSE}[ ]*|[ ]+dot[ ]+|\.)"
_AT_NOAT = rf"(?:[ ]*{_BR_OPEN}[ ]*at[ ]*{_BR_CLOSE}[ ]*|[ ]+at[ ]+)"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9_+\-]*[A-Za-z0-9])?"
_EMAIL_RE = re.compile(
    rf"(?<![A-Za-z0-9._%+\-@]){_LABEL}(?:{_DOT}{_LABEL})*{_AT}{_LABEL}(?:{_DOT}{_LABEL})+(?![A-Za-z0-9_\-@])"
    # the domain must not itself lead into another separator-obfuscated address: in
    # "reached at a.b [at] c [dot] in" the address starts at a.b, not at "reached"
    rf"(?!{_AT_NOAT}{_LABEL}(?:{_DOT}))",
    re.I)
_EMAIL_SEP = re.compile(rf"{_AT}|{_DOT}", re.I)
_EMAIL_HINT = re.compile(rf"{_BR_OPEN}[ ]*(?:at|dot)[ ]*{_BR_CLOSE}|\b(?:at|dot)\b|[ ]@[ ]", re.I)
_TLD = re.compile(r"[A-Za-z]{2,24}")
# A spelled-out "x at y dot z" is ordinary English often enough ("look at this dot plot") that its TLD must
# be a real one; bracketed forms ("[at]", "(dot)") are unambiguous and accept any alphabetic TLD.
_TLDS = frozenset("com org net edu gov mil int info biz io co ai app dev xyz tech online site cloud pro name "
                  "mobi email me tv cc us uk in de fr jp cn ru br au ca it es nl se no fi ch at be dk pl "
                  "za sg hk nz ie ae sa kr id my ph th vn tw mx ar cl pe ng ke eg tr il gr pt cz hu ro".split())

_CASE_MIN_LETTERS = 3
_CASE_SHARE = 0.8
# Lowercase-only trigger must mean "virtually no capitals": ordinary prose is ~95 % lowercase
# letters, and title-casing it made person cues fire on 74 % of R1 documents. Upper-case text
# (shouting, CSV headers) still triggers at 80 %.
_LOWER_SHARE = 0.99


def _apply(s: str, omap: list[int], edits: list[tuple[int, int, str]]) -> tuple[str, list[int]]:
    """Apply non-overlapping, sorted (start, end, replacement) edits to (s, omap)."""
    out: list[str] = []
    om: list[int] = []
    pos = 0
    for a, b, rep in edits:
        out.append(s[pos:a])
        om.extend(omap[pos:a])
        if rep:
            out.append(rep)
            om.extend([omap[a]] * len(rep))
        pos = b
    out.append(s[pos:])
    om.extend(omap[pos:])          # includes the trailing len(original) sentinel
    return "".join(out), om


def _despace(s: str, omap: list[int]):
    runs = [(m.start(), m.end(), 1) for m in _SPACED_INIT.finditer(s)]
    runs += [(m.start(), m.end(), 0) for m in _SPACED_RUN.finditer(s)]
    if not runs:
        return None
    runs.sort()
    edits: list[tuple[int, int, str]] = []
    prev_end = None
    for a, b, init in runs:
        if prev_end is not None and a > prev_end:
            gap = s[prev_end:a]
            if gap != " " and _GAP_OK.fullmatch(gap):
                # two adjacent spaced words ("R a h u l  S h a r m a", "S r i v a  ,  G u n"): drop
                # whitespace before punctuation and keep exactly one space after it
                core = gap.strip(" ")
                if not core:
                    edits.append((prev_end, a, " "))
                else:
                    lead = len(gap) - len(gap.lstrip(" "))
                    trail = len(gap) - len(gap.rstrip(" "))
                    if lead:
                        edits.append((prev_end, prev_end + lead, ""))
                    if trail != 1:
                        edits.append((a - trail, a, " " if trail else ""))
        if init:
            edits.append((a + 1, a + 2, ""))
        else:
            for k in range(a + 1, b, 2):                # separators sit at odd offsets in a run
                edits.append((k, k + 1, ""))
        prev_end = b
    edits.sort()
    return _apply(s, omap, edits)


def _join_digits(s: str, omap: list[int]):
    edits: list[tuple[int, int, str]] = []
    for m in _DIGIT_CHAIN.finditer(s):
        groups = [g.span() for g in _DIGITS.finditer(s, m.start(), m.end())]
        n, i = len(groups), 0
        while i < n:
            tot, j = 0, i
            while j < n and tot + groups[j][1] - groups[j][0] <= _JOIN_MAX:
                tot += groups[j][1] - groups[j][0]
                j += 1
            seps = {s[groups[k][1]:groups[k + 1][0]] for k in range(i, j - 1)}
            # All-hyphen chains are ordinary structured codes (GL 7360-1933-03, INV-2024-000123); the base
            # view already recognises every legitimate hyphenated phone/Aadhaar/card format. Joining them
            # produced phone false positives on R1 dev negatives (166). Mixed/odd separators still join.
            if j - i >= 2 and tot >= _JOIN_MIN and seps != {"-"}:
                for k in range(i, j - 1):
                    edits.append((groups[k][1], groups[k + 1][0], ""))
                i = j
            else:
                i += 1
    if not edits:
        return None
    return _apply(s, omap, edits)


def _deobf_email(s: str, omap: list[int]):
    if not _EMAIL_HINT.search(s):
        return None
    edits: list[tuple[int, int, str]] = []
    for m in _EMAIL_RE.finditer(s):
        seps = list(_EMAIL_SEP.finditer(s, m.start(), m.end()))
        # the last separator is the TLD dot; the TLD must be alphabetic for a valid address
        tld = s[seps[-1].end():m.end()] if seps else ""
        if not seps or not _TLD.fullmatch(tld):
            continue
        if not any("@" in x.group() or "at" in x.group().lower() for x in seps):
            continue
        if any(re.fullmatch(r"[ ]+(?:at|dot)[ ]+", x.group(), re.I) for x in seps) and tld.lower() not in _TLDS:
            continue
        for x in seps:
            g = x.group()
            if g == ".":
                continue
            rep = "@" if ("@" in g or g.strip(" []{}()<>").lower() == "at") else "."
            edits.append((x.start(), x.end(), rep))
    if not edits:
        return None
    return _apply(s, omap, edits)


def _titled(s: str):
    lo = sum(1 for c in s if c.islower())
    up = sum(1 for c in s if c.isupper())
    letters = lo + up
    if letters < _CASE_MIN_LETTERS or not (lo >= _LOWER_SHARE * letters or up >= _CASE_SHARE * letters):
        return None
    out = []
    for w in re.split(r"(\s+)", s):
        if not w or w.isspace() or any(c.isdigit() for c in w):
            out.append(w)                       # leave ids like ABCDE1234F alone
            continue
        chars, first = [], True
        for c in w:
            if c.isalpha():
                t = c.upper() if first else c.lower()
                c = t if len(t) == 1 else c     # keep length (e.g. sharp s)
                first = False
            elif c in "'\u2019":
                pass                             # o'brien -> O'brien
            else:
                first = True
            chars.append(c)
        out.append("".join(chars))
    t = "".join(out)
    return t if t != s else None


# Letter-for-digit swaps inside digit-dominant tokens ("2O34 5l78 9O12", "98I0O 12345", "5010O2345678"): O/o -> 0 and
# l/I -> 1. A token qualifies when it has at least two digits, digits are at least half of it, and it holds a swap
# letter; words ("OIL", "lol"), codes with other letters ("X1") and long runs (> _SWAP_MAX: floods) are left alone.
# Same length, so the view shares the base offset map.
_SWAP_TOKEN = re.compile(r"(?<![A-Za-z0-9])[0-9OoIl]{3,}(?![A-Za-z0-9])")
_SWAP = str.maketrans("OoIl", "0011")
_SWAP_MAX = 24


def _digit_swap(s: str) -> str | None:
    if not any(c in s for c in "OoIl"):
        return None
    parts: list[str] = []
    pos = 0
    for m in _SWAP_TOKEN.finditer(s):
        tok = m.group()
        if len(tok) > _SWAP_MAX:
            continue
        digits = sum(c.isdigit() for c in tok)
        if digits < 2 or digits * 2 < len(tok) or digits == len(tok):
            continue
        parts.append(s[pos:m.start()])
        parts.append(tok.translate(_SWAP))
        pos = m.end()
    if not parts:
        return None
    parts.append(s[pos:])
    return "".join(parts)


def views(text: str) -> list[tuple[str, str, list[int]]]:
    """[(view_name, view_text, omap), ...]; the first is always ("base", *normalize(text)).

    Extra views are de-obfuscated renderings of the base (names: despaced, deobfuscated_email,
    despaced_email, digits_joined, numwords, casefold_titled, digit_swap), emitted only when they differ from base.
    numwords spells digit-by-digit word runs as digits (pgw.detect.numwords); a span ending inside a word there must
    be extended to the word's end (word_end). digit_swap reads O/o as 0 and l/I as 1 inside digit-dominant tokens.
    Every omap maps view chars to ORIGINAL indices (trailing len(text)), so to_original() works on any span.
    """
    s, om = normalize(text)
    out: list[tuple[str, str, list[int]]] = [("base", s, om)]
    if not s:
        return out
    ds = _despace(s, om)
    if ds:
        out.append(("despaced", *ds))
    r = _deobf_email(s, om)
    if r:
        out.append(("deobfuscated_email", *r))
    if ds:
        r2 = _deobf_email(*ds)
        if r2:
            out.append(("despaced_email", *r2))
    r = _join_digits(s, om)
    if r:
        out.append(("digits_joined", *r))
    r = digit_view(s, om)
    if r:
        out.append(("numwords", *r))
    t = _titled(s)
    if t:
        out.append(("casefold_titled", t, list(om)))
    w = _digit_swap(s)
    if w:
        out.append(("digit_swap", w, list(om)))
    return out
