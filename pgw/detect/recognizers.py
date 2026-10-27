"""D1 (field-name) and D2 (deterministic regex + checksum) recognizers.

Pure functions, stdlib only. Offsets are code-point indices into the exact
string given to `detect_d2`.

Design notes
- Every recognizer emits candidates; `_resolve` removes overlaps (higher score
  wins, tie -> longer span, tie -> earlier start).
- "Word boundary" here means: never start/end inside a longer alphanumeric
  token (`(?<![A-Za-z0-9])` / `(?![A-Za-z0-9])`), which also covers digit runs.
- Checksums (Verhoeff, Luhn) are filters, never proof; Aadhaar additionally
  needs the 4-4-4 grouping or a context keyword.
- `detect_field_context` (source "D1:field_ctx", score 0.95) finds values by the field name attached to
  them (JSON-ish, XML, key: value, CSV/TSV) and validates the value against the field's type. It is called
  from `detect_d2`. D0 (pgw.normalize) turns every newline/tab into a space before the pipeline calls us,
  so the CSV logic also has a "flattened" mode that rebuilds rows from the header; TSV needs real tabs.
"""
from __future__ import annotations

import bisect
import collections
import functools
import math
import re
from typing import Any

from pgw import ablation as _ablation
from pgw.types import Detection, EntityType as T

from .numwords import unit_amounts
from .lexicon import ends_name, is_english
from .places import addresses

__all__ = ["detect_d2", "detect_fields", "detect_field_context", "verhoeff_valid", "verhoeff_check_digit", "luhn_valid",
           "iban_valid"]

_NA = r"(?<![A-Za-z0-9])"      # not inside a longer alnum token (left)
_NZ = r"(?![A-Za-z0-9])"       # not inside a longer alnum token (right)

Cand = tuple[int, int, T, float, str]


@functools.lru_cache(maxsize=8)
def _header_spans(text: str) -> tuple:
    """Spans of lines that are the header row of a CSV/TSV table (cells are field names, never values)."""
    spans = []
    pos = 0
    for ln in text.split("\n"):
        end = pos + len(ln)
        if len(ln) < 600:
            d = "\t" if ln.count("\t") >= 2 else ("|" if ln.count("|") >= 2 else ("," if ln.count(",") >= 3 else (";" if ln.count(";") >= 3 else None)))
            if d:
                cells = [c.strip().strip('"') for c in ln.split(d)]
                if len(cells) >= 3 and sum(bool(_HDR_ALPHA.fullmatch(c)) for c in cells) >= 0.8 * len(cells) \
                        and sum(_classify_key(c) is not None for c in cells if _HDR_ALPHA.fullmatch(c)) >= 2:
                    spans.append((pos, end))
        pos = end + 1
    return tuple(spans)


def _in_header_line(text: str, pos: int) -> bool:
    return any(a <= pos <= b for a, b in _header_spans(text))


# --------------------------------------------------------------------------
# Checksums
# --------------------------------------------------------------------------
_VD = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VP = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_VINV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


@functools.lru_cache(maxsize=4096)
def verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VD[c][_VP[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(payload: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(payload)):
        c = _VD[c][_VP[(i + 1) % 8][int(ch)]]
    return str(_VINV[c])


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# --------------------------------------------------------------------------
# EMAIL / UPI
# --------------------------------------------------------------------------
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}(?![A-Za-z0-9\-])"
)

_UPI_PSP = sorted(
    "ybl okaxis oksbi okhdfcbank okicici paytm upi ibl axl apl ptyes ptsbi icici sbi "
    "hdfcbank axisbank kotak yesbank idfcbank freecharge jupiteraxis".split(),
    key=len, reverse=True,
)
_UPI = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._\-]{2,64}@(?:" + "|".join(_UPI_PSP) + r")(?!\.?[A-Za-z0-9\-])",
    re.I,
)


_HEXISH = re.compile(r"[0-9a-fA-F]{24,}")
# "logo@2x.png": asset names / package pins, not mailboxes (none of these is a real TLD)
_FILE_TLD = {"png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "css", "js", "json", "ts", "tsx", "jsx", "woff",
             "woff2", "ttf", "eot", "map", "avif", "tiff", "html", "htm", "txt", "yml", "yaml", "xml", "gz", "tgz"}


def _email(text: str) -> list[Cand]:
    out: list[Cand] = []
    for m in _EMAIL.finditer(text):
        local = m.group().split("@", 1)[0]
        if text[max(0, m.start() - 3):m.start()] == "://" or _HEXISH.fullmatch(local):
            continue  # URL userinfo / DSN keys / hashes, not a mailbox
        if m.group().rsplit(".", 1)[-1].lower() in _FILE_TLD:
            continue
        if re.search(r"://[^\s/'\"<>]*$", text[max(0, m.start() - 256):m.start()]) and \
                re.search(r"://[^\s/'\"<>]*:[^\s/'\"<>]*$", text[max(0, m.start() - 256):m.start() + len(local)]):
            continue  # userinfo of scheme://user:pass@host
        out.append((m.start(), m.end(), T.EMAIL, 0.99, "D2:email"))
    return out


_UPI_ANY = re.compile(
    r"(?<![A-Za-z0-9._%+\-])(?P<h>[A-Za-z0-9._\-]{2,64})@(?P<p>[A-Za-z][A-Za-z0-9]{1,30})(?!\.?[A-Za-z0-9\-@])")
_UPI_CTX = re.compile(
    r"(?<![A-Za-z])(?:upi|vpa|bhim|gpay|google[ \t]*pay|phonepe|paytm|pay(?:ment)?[ \t]+to|credit[ \t]+to|transfer[ \t]+to|"
    r"send[ \t]+to|payee|pay[ \t]+via|collect[ \t]+request)(?![A-Za-z])", re.I)


def _upi(text: str) -> list[Cand]:
    out: list[Cand] = [(m.start(), m.end(), T.UPI_ID, 0.95, "D2:upi") for m in _UPI.finditer(text)]
    if "@" in text:
        for m in _UPI_ANY.finditer(text):
            h = m.group("h")
            if m.group("p").lower() in _UPI_PSP:
                continue                                    # known PSP: already scored above
            if h.isdigit() and re.fullmatch(r"[6-9]\d{9}", h):
                out.append((m.start(), m.end(), T.UPI_ID, 0.97, "D2:upi_ctx"))       # <mobile>@<psp>
            elif _UPI_CTX.search(text[max(0, m.start() - 40):m.start()]):
                out.append((m.start(), m.end(), T.UPI_ID, 0.97, "D2:upi_ctx"))
    return out


# --------------------------------------------------------------------------
# PHONE
# --------------------------------------------------------------------------
_PHONE_IN = re.compile(
    _NA
    + r"(?:(?:\(\+91\)|\+91|91|0)[ \-]?)?"
    + r"[6-9](?:\d{4}[ \-]?\d{5}|\d{3}[ \-]?\d{3}[ \-]?\d{3}|\d{2}[ \-]?\d{3}[ \-]?\d{4})"
    + _NZ
)
_PHONE_INTL = re.compile(_NA + r"\+[1-9](?:[ .()\-]{0,2}\d){7,14}" + _NZ)
# Indian landlines: STD (0 + 2-4 digits, hyphen/space/parenthesised) + subscriber, 11 digits in all.
_PHONE_LL = re.compile(
    _NA + r"(?<!\d,)(?:\(0[1-9]\d{1,3}\)[ \-]?|0[1-9]\d{1,3}[ \-])(?:\d[ \-]?){5,7}\d" + _NZ)
_PHONE_NEG_CTX = re.compile(
    r"(?<![A-Za-z])(?:order|awb|tracking|shipment|invoice|ticket|txn|utr|sku|ref|reference|consignment|"
    r"po|pallet|serial|imei|meeting|conference|webinar|room|zoom|bridge)(?![A-Za-z])"
    r"(?:[\s_\-.]*(?:id|no|number|num|#))?[\s_:=#\-.]*$", re.I)
_PHONE_POS_CTX = re.compile(r"(?<![A-Za-z])(?:phone|mobile|mob|cell|contact|call|whatsapp|tel|ph)(?![A-Za-z])", re.I)
_WA_PREFIX = re.compile(r"(?:wa\.me/|whatsapp\.com/send\?phone=|tel:)$", re.I)


def _url_path(text: str, s: int, e: int) -> bool:
    """A digit run that is a URL path segment (zoom.us/j/07605122133) is not a phone."""
    if (s > 0 and text[s - 1] == "/") or (e < len(text) and text[e] == "/"):
        tok_start = s
        while tok_start > 0 and not text[tok_start - 1].isspace() and text[tok_start - 1] not in "\"'<>()":
            tok_start -= 1
        return not _WA_PREFIX.search(text[tok_start:s])
    return False


def _phone(text: str) -> list[Cand]:
    out: list[Cand] = []
    for m in _PHONE_IN.finditer(text):
        s, e = m.start(), m.end()
        if _url_path(text, s, e):
            continue
        raw = m.group()
        if not raw.lstrip("(").startswith("+"):     # order ids / AWBs / meeting ids are common look-alikes
            pre = text[max(0, s - 22):s]
            if _PHONE_NEG_CTX.search(pre) and not _PHONE_POS_CTX.search(pre):
                continue
        out.append((s, e, T.PHONE, 0.9, "D2:phone_in"))
    for m in _PHONE_LL.finditer(text):
        raw = m.group()
        if sum(ch.isdigit() for ch in raw) != 11 or _url_path(text, m.start(), m.end()) \
                or re.search(r"\d{4}-\d{2}-\d{2}", raw):
            continue
        pre = text[max(0, m.start() - 22):m.start()]
        if _PHONE_NEG_CTX.search(pre) and not _PHONE_POS_CTX.search(pre):
            continue
        std_end = raw.index(")") + 1 if raw.startswith("(") else re.search(r"[ \-]", raw).end()
        if len(re.findall(r"[ \-]", raw[std_end:])) > 2:
            continue
        out.append((m.start(), m.end(), T.PHONE, 0.85, "D2:phone_landline"))
    for m in _PHONE_INTL.finditer(text):
        n = sum(ch.isdigit() for ch in m.group())
        if 8 <= n <= 15:
            out.append((m.start(), m.end(), T.PHONE, 0.8, "D2:phone_intl"))
    return out


# --------------------------------------------------------------------------
# AADHAAR
# --------------------------------------------------------------------------
_AADHAAR_GROUPED = re.compile(
    _NA + r"(?<!\d{4}[ \-])[2-9]\d{3}[ \-]\d{4}[ \-]\d{4}" + _NZ + r"(?![ \-]\d{4})"
)
_AADHAAR_CONTIG = re.compile(_NA + r"[2-9]\d{11}" + _NZ)
_AADHAAR_KW = re.compile(
    r"(?<![A-Za-z])(?:aadhaar|aadhar|uidai|uid)(?![A-Za-z])|आधार|"
    r"(?<![A-Za-z])(?:national|nat|natl|govt|government|identity|identification|kyc|tax|id[ _\-]?proof)[ _\-]*"
    r"(?:id|identifier|number|no|num|card|proof)(?![A-Za-z])", re.I)


def _aadhaar(text: str) -> list[Cand]:
    out: list[Cand] = []
    for rx, grouped in ((_AADHAAR_GROUPED, True), (_AADHAAR_CONTIG, False)):
        for m in rx.finditer(text):
            digits = re.sub(r"\D", "", m.group())
            if not verhoeff_valid(digits):
                continue
            lo = max(0, m.start() - 120)
            ctx = any(not _in_header_line(text, lo + k.start()) for k in _AADHAAR_KW.finditer(text[lo:m.start()]))
            if not (grouped or ctx):
                continue
            out.append((m.start(), m.end(), T.AADHAAR, 0.95 if (grouped and ctx) else 0.85, "D2:aadhaar"))
    return out


# --------------------------------------------------------------------------
# PAN / IFSC
# --------------------------------------------------------------------------
_PAN = re.compile(_NA + r"[A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]" + _NZ)
_IFSC = re.compile(_NA + r"[A-Z]{4}0[A-Z0-9]{6}" + _NZ)


_PAN_ANY = re.compile(_NA + r"[A-Za-z]{3}[ABCFGHLJPTabcfghljpt][A-Za-z][0-9]{4}[A-Za-z]" + _NZ)
_PAN_KW = re.compile(r"(?<![A-Za-z])(?:pan|permanent[ \t]+account[ \t]+(?:number|no\.?))(?![A-Za-z])", re.I)


def _pan(text: str) -> list[Cand]:
    out = [(m.start(), m.end(), T.PAN, 0.95, "D2:pan") for m in _PAN.finditer(text)]
    for m in _PAN_ANY.finditer(text):      # lower/mixed case only with an explicit PAN label just before
        if not m.group().isupper() and _PAN_KW.search(text[max(0, m.start() - 30):m.start()]):
            out.append((m.start(), m.end(), T.PAN, 0.85, "D2:pan_ctx"))
    return out


def _ifsc(text: str) -> list[Cand]:
    return [(m.start(), m.end(), T.IFSC, 0.9, "D2:ifsc") for m in _IFSC.finditer(text)]


# --------------------------------------------------------------------------
# CARD / BANK_ACCOUNT
# --------------------------------------------------------------------------
# A "run" is digit groups joined by single spaces/hyphens.
_RUN = re.compile(_NA + r"(?<!\d[ \-])\d+(?:[ \-]\d+)*" + _NZ)


def _iin_ok(d: str) -> bool:
    if d[0] == "4":
        return True
    if d[:2] in ("34", "37", "36", "38", "60", "65", "81", "82"):
        return True
    if d[:3] == "508":
        return True
    if "51" <= d[:2] <= "55":
        return True
    if "2221" <= d[:4] <= "2720":
        return True
    return False


def _groups(run: re.Match) -> list[tuple[int, int]]:
    return [(run.start() + g.start(), run.start() + g.end()) for g in re.finditer(r"\d+", run.group())]


_CARD_NEG = re.compile(
    r"(?<![A-Za-z])(?:imei|tracking|awb|pallet|serial|s/n|sn|order|po|shipment|consignment|waybill|docket|sscc|barcode)"
    r"(?![A-Za-z])", re.I)


def _cards(text: str) -> list[Cand]:
    out: list[Cand] = []
    for run in _RUN.finditer(text):
        gs = _groups(run)
        for i in range(len(gs)):
            total = 0
            for j in range(i, len(gs)):
                total += gs[j][1] - gs[j][0]
                if total > 19:
                    break
                if total < 13:
                    continue
                s, e = gs[i][0], gs[j][1]
                span = text[s:e]
                if j > i:
                    seps = {text[gs[k][1]] for k in range(i, j)}
                    if len(seps) != 1 or any(gs[k][1] - gs[k][0] < 3 for k in range(i, j + 1)):
                        continue
                d = re.sub(r"\D", "", span)
                if _iin_ok(d) and luhn_valid(d) and not _CARD_NEG.search(text[max(0, s - 25):s]):
                    out.append((s, e, T.CARD, 0.95, "D2:card"))
    return out


_BANK_KW = re.compile(
    _NA + r"(?:(?:bank|beneficiary|salary|savings?|current|payee)[ \t]+)?"
    r"(?P<w>account|acct|acc|a/c)"
    r"(?P<q>[ \t]*(?:number|num|no\.?|nbr|#))?(?![A-Za-z])",
    re.I,
)
# bare "account"/"acc" (no qualifier) only counts when the digits follow almost immediately
_BANK_FILLER = re.compile(r"(?:[ \t:=#.\-\"'()]|\b(?:is|hai|no|number|num|details?|nbr|ka|the|new|my)\b)*", re.I)


def _bank_run(text: str, run: re.Match) -> tuple[int, int] | None:
    gs = _groups(run)
    first = gs[0][1] - gs[0][0]
    if 9 <= first <= 18:
        return gs[0]
    if len(gs) < 2:
        return None
    total, last = 0, -1
    for k, (s, e) in enumerate(gs):
        if e - s < 3 or total + (e - s) > 18:
            break
        total += e - s
        last = k
    if last >= 1 and total >= 9:
        return gs[0][0], gs[last][1]
    return None


_TIMESTAMP_RUN = re.compile(r"(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{0,6}$")
_BANK_TOKEN = re.compile(r"(?<![\w.,+\-/])\d{9,18}(?![\w.]|,\d)")
_BANK_AMOUNT = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{2,3})+|\d{3,})\.\d{2}(?![\w.])")


def _ifsc_lines(text: str):
    """(line_start, line_end, ifsc_match) for every line/record carrying a valid-shaped IFSC."""
    for m in _IFSC.finditer(text):
        ls = text.rfind("\n", 0, m.start()) + 1
        le = text.find("\n", m.end())
        yield ls, (len(text) if le == -1 else le), m


def _bank_file(text: str) -> list[Cand]:
    """Fixed-width / delimited bank records: the single 9-18 digit run on an IFSC line is the account number."""
    out: list[Cand] = []
    seen: set[int] = set()
    for ls, le, im in _ifsc_lines(text):
        if ls in seen or le - ls > 400:
            continue
        seen.add(ls)
        line = text[ls:le]
        cands = [m for m in _BANK_TOKEN.finditer(line)
                 if not (m.start() < im.end() - ls and m.end() > im.start() - ls) and not _TIMESTAMP_RUN.match(m.group())]
        if len(cands) > 1:
            nonphone = [m for m in cands if not re.fullmatch(r"[6-9]\d{9}", m.group())]
            cands = nonphone if len(nonphone) == 1 else []
        if len(cands) == 1:
            out.append((ls + cands[0].start(), ls + cands[0].end(), T.BANK_ACCOUNT, 0.9, "D2:bank_ifsc_line"))
    return out


_DOC_PAY_LOOSE = re.compile(r"(?i)(?:salary|payroll|payout|stipend|bonus|wages?|remuneration|incentive|reimbursement)")
_BANK_NAME = re.compile(r"[ ]+(?P<n>[A-Z][A-Za-z'.\-]*(?:[ ][A-Z][A-Za-z'.\-]*){0,4})(?=[ ]{2,}|[ ]*$|[ ]+[\d,]+\.\d{2}|[ ]*,)")


def _bank_file_names(text: str) -> list[Cand]:
    """NEFT-style records: the capitalised words right after the IFSC are the beneficiary name."""
    out: list[Cand] = []
    seen: set[int] = set()
    for ls, le, im in _ifsc_lines(text):
        if ls in seen or le - ls > 400:
            continue
        seen.add(ls)
        m = _BANK_NAME.match(text, im.end(), le)
        if m and m.group("n").lower() not in _NOT_NAMES and len(m.group("n")) >= 3:
            out.append((m.start("n"), m.end("n"), T.PERSON, 0.9, "D2:bank_file_name"))
    return out


def _bank_file_amounts(text: str) -> list[Cand]:
    """The amount on a bank-file record is compensation only if the document speaks of salary / payroll / payout."""
    out: list[Cand] = []
    if not _IFSC.search(text) or not _DOC_PAY_LOOSE.search(text):
        return out
    seen: set[int] = set()
    for ls, le, im in _ifsc_lines(text):
        if ls in seen or le - ls > 400:
            continue
        seen.add(ls)
        ams = [m for m in _BANK_AMOUNT.finditer(text, ls, le)]
        if ams:
            m = ams[-1]
            out.append((m.start(), m.end(), T.COMPENSATION, 0.9, "D2:compensation_bankfile"))
    return out


def _bank(text: str) -> list[Cand]:
    out: list[Cand] = _bank_file(text)
    for kw in _BANK_KW.finditer(text):
        if _in_header_line(text, kw.start()):
            continue
        bare = kw.group("q") is None and kw.group("w").lower() in ("account", "acc") \
            and not re.match(r"(?i)(bank|beneficiary|salary|savings?|current|payee)", kw.group())
        if bare:
            f = _BANK_FILLER.match(text, kw.end())
            run = _RUN.match(text, f.end())
            span = _bank_run(text, run) if run else None
            if span:
                out.append((span[0], span[1], T.BANK_ACCOUNT, 0.85, "D2:bank_account"))
            continue
        for run in _RUN.finditer(text, kw.end()):
            if run.start() > kw.end() + 30:
                break
            span = _bank_run(text, run)
            if span:
                out.append((span[0], span[1], T.BANK_ACCOUNT, 0.85, "D2:bank_account"))
    return out


# --------------------------------------------------------------------------
# SECRET
# --------------------------------------------------------------------------
_SECRET_SIMPLE: list[tuple[str, re.Pattern]] = [
    ("openai", re.compile(_NA + r"sk-[A-Za-z0-9_\-]{20,}")),
    ("aws", re.compile(_NA + r"(?:AKIA|ASIA)[0-9A-Z]{16}" + _NZ)),
    ("github", re.compile(_NA + r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("github_pat", re.compile(_NA + r"github_pat_[A-Za-z0-9_]{22,}")),
    ("slack", re.compile(_NA + r"xox[abposr]-[A-Za-z0-9\-]{10,}")),
    ("google_api", re.compile(_NA + r"AIza[0-9A-Za-z_\-]{35}")),
    ("google_oauth", re.compile(_NA + r"ya29\.[0-9A-Za-z_\-]{20,}")),
    ("stripe", re.compile(_NA + r"(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}")),
    ("stripe_whsec", re.compile(_NA + r"whsec_[0-9A-Za-z]{16,}")),
    ("twilio", re.compile(_NA + r"SK[0-9a-f]{32}" + _NZ)),
    ("sendgrid", re.compile(_NA + r"SG\.[0-9A-Za-z_\-]{16,}\.[0-9A-Za-z_\-]{16,}")),
    ("gitlab", re.compile(_NA + r"glpat-[0-9A-Za-z_\-]{20,}")),
    ("npm", re.compile(_NA + r"npm_[0-9A-Za-z]{36}")),
    ("mailgun", re.compile(_NA + r"key-[0-9a-f]{32}" + _NZ)),
    ("shopify", re.compile(_NA + r"shp(?:at|ss|ca|pa)_[0-9a-f]{32}")),
    ("digitalocean", re.compile(_NA + r"dop_v1_[0-9a-f]{64}")),
    ("age", re.compile(_NA + r"AGE-SECRET-KEY-1[0-9A-Z]{58}")),
    ("azure_key", re.compile(r"(?<=AccountKey=)[0-9A-Za-z+/]{30,}={0,2}")),
    ("azure_sas", re.compile(r"(?<=SharedAccessKey=)[0-9A-Za-z+/]{20,}={0,2}")),
    ("jwt", re.compile(r"(?<![A-Za-z0-9_\-])eyJ[\w\-]+\.eyJ[\w\-]+\.[\w\-]+")),
    ("pem", re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?"
        r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|\Z)"
    )),
]
_CRED = r"[A-Za-z0-9._~+/=\-]+"
_AUTH = re.compile(
    r"(?<![A-Za-z0-9])authorization[\\\"']*[ \t]*[:=][ \t]*[\\\"']*(?:bearer|basic|token)[ \t]+(?P<v>" + _CRED + r")", re.I
)
_BEARER = re.compile(_NA + r"bearer[ \t]+(?P<v>[A-Za-z0-9._~+/=\-]{20,})", re.I)
_KV = re.compile(
    r"(?-i:(?<![A-Za-z0-9])|(?<=[a-z])(?=[A-Z]))"
    r"(?:password|passwd|pwd|secret|api[_\-]?key|apikey|client[_\-]?secret)(?:[_\-][A-Za-z0-9_\-]{0,20})?"
    r"[\\\"']*[ \t]*(?:::|[:=])[ \t]*"
    r"(?:\\?\"(?P<dq>[^\"\\\r\n]+)\\?\"|'(?P<sq>[^'\r\n]+)'|(?P<raw>[^\s\"',;]+))",
    re.I,
)
_KV_SKIP = {"true", "false", "null", "none", "undefined"}
# casual chat: "pass Monsoon@8765", "password is xY7#kq9", "pwd: abc123"
_PASS_CASUAL = re.compile(
    _NA + r"(?:pass(?:word|wd)?|pwd)(?:[ \t]*[:=][ \t]*|[ \t]+(?:is[ \t]+|was[ \t]+)?)(?P<v>[^\s\"'<>;]{6,})", re.I)
_PASS_PRE_NEG = re.compile(
    r"(?:boarding|bus|train|metro|season|day|gate|free|hall|entry|parking|travel|lift|access|guest|vip|mountain|rail|"
    r"fast|press|back|by|over|hall|security|visitor)[ \t]+$", re.I)
_CURL_U = re.compile(r"(?<![\w\-])(?:-u|--user)[ \t]+\\?[\"']?[^\s:'\"\\]+:(?P<v>[^\s'\"\\]+)")
_PLACEHOLDER = re.compile(
    r"^(?:\*+.*|\{[^}]*\}|%[sd]|%\(.*\)s|pass|pwd|user|username|<[^>]*>|x{3,}|<[^>]*>|\{\{.*\}\}|\$\{.*\}|\$[A-Za-z_][\w]*|%[\w]+%|\[?redacted\]?|changeme|change[_\- ]?me|"
    r"placeholder|your[_\- ].*|example|dummy|\.{3,}|null|none|true|false|required|optional|n/?a|undefined|empty|"
    r"hidden|masked|secret|password|token|string|xxxx.*)$", re.I)


def _placeholder(v: str) -> bool:
    v = v.strip()
    return len(v) < 4 or bool(_PLACEHOLDER.match(v))


# scheme://user:password@host  (postgresql, mysql, mongodb+srv, redis://:pw@, amqp, sftp, smtp, https basic auth, jdbc:...)
# The password may itself contain '@', '#', '?' ... so it runs to the LAST '@' that is followed by a host.
_URI_CRED = re.compile(
    r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]{1,30}://(?P<user>[^\s/:@;'\"<>]*):(?P<v>[^\s/;'\"<>]+)@"
    r"(?=[A-Za-z0-9_\-.\[\]]+(?::\d+)?(?:[/?#\s'\"<>,;)\\]|$))")
_QS_PARAM = re.compile(r"(?<=[?&;])(?P<k>[A-Za-z0-9_.\-\[\]]{1,40})=(?P<v>[^&\s'\"<>#;]{4,})")
_QS_EXTRA_SECRET_KEYS = {"sig", "signature", "auth", "xamzsignature", "xamzcredential", "xgoogsignature", "sas", "jwt", "otp"}


def _uri_secrets(text: str) -> list[Cand]:
    out: list[Cand] = []
    for m in _URI_CRED.finditer(text):
        v = m.group("v")
        if len(v) < 3 or _PLACEHOLDER.match(v):
            continue
        out.append((m.start("v"), m.end("v"), T.SECRET, 0.99, "D2:secret_uri"))
    for m in _QS_PARAM.finditer(text):
        kw = _key_words(m.group("k"))
        if not (_classify_words(kw) is T.SECRET or "".join(kw) in _QS_EXTRA_SECRET_KEYS):
            continue
        v = m.group("v").rstrip(".,)")
        if len(v) < 4 or _PLACEHOLDER.match(v):
            continue
        out.append((m.start("v"), m.start("v") + len(v), T.SECRET, 0.99, "D2:secret_qs"))
    return out


# --- credential files and CLI flags
# host = one or more [A-Za-z0-9_.-*] chars containing at least one letter/dot/star. The original
# `X*[letter]X*` form was ambiguous (n ways to split the run) and cost O(n^2) on a long token with no ':'
# (it runs at every line start); the lookahead states the same constraint but only scans a digit/_/- prefix.
_PGPASS = re.compile(
    r"(?m)^[ \t]*(?P<host>(?=[0-9_\-]*[A-Za-z.*])[A-Za-z0-9_.\-*]+):(?:\d{1,5}|\*):[A-Za-z0-9_.\-*]+:[A-Za-z0-9_.\-@*\\]+:"
    r"(?P<v>\S[^\r\n]*?)[ \t]*$")
_CLI_LONG = re.compile(
    r"(?<![\w\-])--(?:password|passwd|pass|pwd)(?:=|[ \t]+)(?:'(?P<sq>[^'\r\n]+)'|\"(?P<dq>[^\"\r\n]+)\"|(?P<raw>[^\s'\"]+))")
_CLI_LOGIN_LINE = re.compile(
    r"(?<![\w\-])(?:docker[ \t]+login|podman[ \t]+login|helm[ \t]+registry[ \t]+login|az[ \t]+login|oc[ \t]+login|"
    r"kubectl[ \t]+config|login|connect|sshpass|smbclient|ldap\w+|beeline|cqlsh|snowsql)(?![\w\-])")
_CLI_P_SPACED = re.compile(r"(?<![\w\-])-p[ \t]+(?:'(?P<sq>[^'\r\n]+)'|\"(?P<dq>[^\"\r\n]+)\"|(?P<raw>[^\s'\"]+))")
_CLI_MYSQL_LINE = re.compile(r"(?<![\w\-])(?:mysql|mysqldump|mysqladmin|mariadb|mysqlpump)(?![\w\-])")
_CLI_P_ATTACHED = re.compile(r"(?<![\w\-])-p(?P<raw>[^\s\-'\"][^\s'\"]*)")
_CLI_OTHER = re.compile(r"(?<![\w\-])(?:-P|-a|-W)[ \t]+(?P<raw>[^\s'\"\-][^\s'\"]*)")
_CLI_OTHER_LINE = re.compile(r"(?<![\w\-])(?:sqlcmd|redis-cli|redis-cli\.exe|isql|bcp|sqlplus)(?![\w\-])")
_NETRC = re.compile(r"(?m)(?:^|(?<=\s))password[ \t]+(?P<v>[^\s'\"]+)")


def _cli_and_files(text: str) -> list[Cand]:
    out: list[Cand] = []

    def add(m, name):
        for g in ("sq", "dq", "raw", "v"):
            try:
                v = m.group(g)
            except IndexError:
                continue
            if v is not None:
                if len(v) >= 3 and not _placeholder(v) and not v.startswith(("-", "$", "%")) \
                        and not (v.isdigit() and len(v) <= 5):
                    out.append((m.start(g), m.end(g), T.SECRET, 0.99, "D2:secret_" + name))
                return

    for m in _PGPASS.finditer(text):
        if not m.group("v").startswith(("//", "/")) and not _placeholder(m.group("v")):
            out.append((m.start("v"), m.end("v"), T.SECRET, 0.99, "D2:secret_pgpass"))
    for m in _CLI_LONG.finditer(text):
        add(m, "cli")
    if "-p" in text or "-P" in text or "-a" in text:
        pos = 0
        for ln in text.split("\n"):
            if len(ln) < 2000:
                if _CLI_LOGIN_LINE.search(ln):
                    for m in _CLI_P_SPACED.finditer(ln):
                        add(_Off(m, pos), "cli")
                if _CLI_MYSQL_LINE.search(ln):
                    for m in _CLI_P_ATTACHED.finditer(ln):
                        add(_Off(m, pos), "cli")
                if _CLI_OTHER_LINE.search(ln):
                    for m in _CLI_OTHER.finditer(ln):
                        add(_Off(m, pos), "cli")
            pos += len(ln) + 1
    if "password" in text:
        for m in _NETRC.finditer(text):
            ctx = text[max(0, m.start() - 200):m.start()]
            if re.search(r"(?<![A-Za-z])(?:machine|login)(?![A-Za-z])", ctx):
                add(m, "netrc")
    return out


class _Off:
    """A regex match whose offsets are shifted by a line start."""

    def __init__(self, m: re.Match, off: int):
        self._m, self._o = m, off

    def group(self, g):
        return self._m.group(g)

    def start(self, g):
        return self._m.start(g) + self._o

    def end(self, g):
        return self._m.end(g) + self._o


def _secret(text: str) -> list[Cand]:
    out: list[Cand] = _uri_secrets(text)
    for name, rx in _SECRET_SIMPLE:
        for m in rx.finditer(text):
            out.append((m.start(), m.end(), T.SECRET, 0.99, f"D2:secret_{name}"))
    for rx in (_AUTH, _BEARER):
        for m in rx.finditer(text):
            v = m.group("v")
            if rx is _BEARER and not any(c.isdigit() for c in v):
                continue
            out.append((m.start("v"), m.end("v"), T.SECRET, 0.99, "D2:secret_auth"))
    for m in _PASS_CASUAL.finditer(text):
        v = m.group("v")
        if m.start() > 0 and text[m.start() - 1] in "?&":
            v = v.split("&")[0]                       # query-string parameter boundary
        v = v.rstrip(".,;:)]!?")
        mixed = any(c.islower() for c in v) and any(c.isupper() for c in v[1:])
        if len(v) < 6 or not (any(c.isdigit() for c in v) or any(c in "!@#$%^&*_+=~?|" for c in v) or mixed):
            continue
        if _PASS_PRE_NEG.search(text[max(0, m.start() - 12):m.start()]) or _placeholder(v):
            continue
        out.append((m.start("v"), m.start("v") + len(v), T.SECRET, 0.99, "D2:secret_pass"))
    out += _cli_and_files(text)
    for m in _CURL_U.finditer(text):
        if not _placeholder(m.group("v")):
            out.append((m.start("v"), m.end("v"), T.SECRET, 0.99, "D2:secret_curl_u"))
    for m in _KV.finditer(text):
        g = next(k for k in ("dq", "sq", "raw") if m.group(k) is not None)
        v = m.group(g)
        s, e = m.start(g), m.end(g)
        if _PLACEHOLDER.match(v.strip()):
            continue
        if g == "raw" and m.start() > 0 and text[m.start() - 1] in "?&" and "&" in v:
            e -= len(v) - v.index("&")                  # inside a query string '&' separates parameters
            v = v[:v.index("&")]
        if g == "raw":
            stripped = v.rstrip("})].,:!?")
            e -= len(v) - len(stripped)
            v = stripped
        if not v or v.strip().lower() in _KV_SKIP or _PLACEHOLDER.match(v.strip()):
            continue
        out.append((s, e, T.SECRET, 0.99, "D2:secret_kv"))
    return out


# --------------------------------------------------------------------------
# EMPLOYEE_ID
# --------------------------------------------------------------------------
_EMPID_FMT = [
    re.compile(_NA + r"(?i:EMP)[\-_]?\d{4,8}" + _NZ),
    re.compile(_NA + r"E\d{6}" + _NZ),
    re.compile(_NA + r"(?i:WD)-\d{6,8}" + _NZ),
    re.compile(_NA + r"[A-Z]{2,3}/\d{2}/\d{3,5}" + _NZ),        # tenant code / joining-year / serial
]
_EMPID_KW = re.compile(
    _NA + r"(?:employee[\s_\-]*(?:id|no\.?|number|num)|emp[\s_\-]*(?:id|no\.?)|worker[\s_\-]*id|staff[\s_\-]*id|emp\s*#)(?![A-Za-z])",
    re.I,
)
_EMPID_DIRECT = re.compile(r"[ \t:=#.\-\"'()\[\]\\]*(?P<t>[A-Za-z0-9]{4,10})" + _NZ)
_EMPID_TOK = re.compile(_NA + r"[A-Za-z0-9]{4,10}" + _NZ)


def _employee_id(text: str) -> list[Cand]:
    out: list[Cand] = []
    for rx in _EMPID_FMT:
        for m in rx.finditer(text):
            out.append((m.start(), m.end(), T.EMPLOYEE_ID, 0.9, "D2:employee_id"))
    for kw in _EMPID_KW.finditer(text):
        if _in_header_cell(text, kw.start(), kw.end()) or _in_header_line(text, kw.start()):
            continue
        nl = text.find("\n", kw.end())
        lim = min(kw.end() + 25, nl if nl != -1 else len(text))
        d = _EMPID_DIRECT.match(text, kw.end())
        if d and d.start("t") <= lim and (any(c.isdigit() for c in d.group("t")) or d.group("t").isupper()):
            out.append((d.start("t"), d.end("t"), T.EMPLOYEE_ID, 0.9, "D2:employee_id"))
            continue
        for tk in _EMPID_TOK.finditer(text, kw.end()):
            if tk.start() > lim:
                break
            if any(c.isdigit() for c in tk.group()):
                out.append((tk.start(), tk.end(), T.EMPLOYEE_ID, 0.9, "D2:employee_id"))
                break
    return out


# --------------------------------------------------------------------------
# IBAN (ISO 13616): country code, two check digits, a country-specific number of alphanumerics; mod-97 == 1
# --------------------------------------------------------------------------
# Total IBAN length per country (the SWIFT IBAN registry; structural knowledge, no account is listed).
_IBAN_LEN = {cc: int(n) for cc, n in (x.split(":") for x in """
AD:24 AE:23 AL:28 AT:20 AZ:28 BA:20 BE:16 BG:22 BH:22 BI:27 BR:29 BY:28 CH:21 CR:22 CY:28 CZ:24 DE:22 DJ:27 DK:18
DO:28 EE:20 EG:29 ES:24 FI:18 FK:18 FO:18 FR:27 GB:22 GE:22 GI:23 GL:18 GR:27 GT:28 HN:28 HR:21 HU:28 IE:22 IL:23
IQ:23 IS:26 IT:27 JO:30 KW:30 KZ:20 LB:28 LC:32 LI:21 LT:20 LU:20 LV:21 LY:25 MC:27 MD:24 ME:22 MK:19 MN:20 MR:27
MT:31 MU:30 NI:28 NL:18 NO:15 OM:23 PK:24 PL:28 PS:29 PT:25 QA:29 RO:24 RS:22 RU:33 SA:24 SC:31 SD:18 SE:24 SI:19
SK:24 SM:27 SO:23 ST:25 SV:28 TL:23 TN:24 TR:26 UA:29 VA:22 VG:24 XK:20 YE:30""".split())}
# Start: two letters (one case) and two digits, not inside a longer token. The rest may be grouped any way: single
# spaces or hyphens between any characters ("DE89 3704 0044 0532 0130 00", "SA03-8000-...", "AE07 033 123456 ...").
_IBAN_START = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z]{2}|[a-z]{2})[ \-]?\d{2}(?=[ \-]?[A-Za-z0-9])")


def iban_valid(compact: str) -> bool:
    """mod-97 check of an IBAN without separators (any case)."""
    s = compact.upper()
    if len(s) < 15 or not s[:2].isalpha() or not s[2:4].isdigit() or not s.isalnum() or not s.isascii():
        return False
    r = 0
    for c in s[4:] + s[:4]:
        for d in (str(int(c, 36)) if c.isalpha() else c):
            r = (r * 10 + ord(d) - 48) % 97
    return r == 1


def _iban(text: str) -> list[Cand]:
    out: list[Cand] = []
    n = len(text)
    for m in _IBAN_START.finditer(text):
        need = _IBAN_LEN.get(text[m.start():m.start() + 2].upper())
        if need is None:
            continue
        chars = [c for c in text[m.start():m.end()] if c.isalnum()]
        p = m.end()
        while p < n and len(chars) < need:
            c = text[p]
            if c.isascii() and c.isalnum():
                chars.append(c)
                p += 1
            elif c in " -" and p + 1 < n and text[p + 1].isascii() and text[p + 1].isalnum() and text[p - 1].isalnum():
                p += 1
            else:
                break
        if len(chars) == need and (p == n or not text[p].isalnum()) and iban_valid("".join(chars)):
            out.append((m.start(), p, T.BANK_ACCOUNT, 0.97, "D2:iban"))
    return out


# --------------------------------------------------------------------------
# COMPENSATION
# --------------------------------------------------------------------------
_NUM = r"\d+(?:,\d{2,3})*(?:\.\d+)?"
_CUR_PRE = (r"(?:₹|€|£|US\$|(?<![A-Za-z0-9])\$|"
            r"(?<![A-Za-z0-9])(?i:rs\.?|inr|usd|eur|gbp|aed|sgd|cad|aud|chf|jpy))")
_UNIT = r"(?:(?i:lakhs?|lacs?|crores?|crs?|lpa|inr|usd|eur|gbp|aed|sgd|dollars?|euros?|millions?|mn|billions?|bn)|k|K|L)"
# A currency written AFTER the number: "48,500/-" (the Indian rupee dash), "62,300 rs", "82,400 rupees", "2.4 million
# rupees". Such an amount is money by its structure alone (see _compensation).
_SFX = r"(?:/-|[ \t]*(?i:rupees?|rs\.?|inr)(?![A-Za-z]))"
_COMP_MARKED = re.compile(
    r"(?:(?P<pre>" + _CUR_PRE + r"[ \t]*)|(?<![A-Za-z0-9])(?<!\d[.,]))"
    r"(?P<num>" + _NUM + r")"
    r"(?P<unit>[ \t]*" + _UNIT + r")?"
    r"(?P<sfx>" + _SFX + r")?" + r"(?:/-)?" + r"(?![A-Za-z0-9])"
)
_COMP_KW = re.compile(
    _NA + r"(?:salary|salaries|ctc|compensation|pay|basic|gross|net[ \t]+pay|bonus|increment|revision|stipend|hike)(?![A-Za-z])",
    re.I,
)
_COMP_BARE = re.compile(r"(?<![A-Za-z0-9_])(?<!\d[,./\-])" + _NUM + r"(?![A-Za-z0-9_%])(?![/\-]\d)(?!\.\d)")
_CUR_BEFORE = re.compile(r"(?:₹|€|£|\$|(?<![A-Za-z])(?i:rs\.?|inr|usd|eur|gbp|aed|sgd))[ \t]*$")
_YEAR_PRE = re.compile(
    r"(?:\b(?:in|since|effective|from|year|fy|for|during|dated|on|of|by|until|till|wef|w\.e\.f\.?)|[Ff][Yy])[\s:\-]*$"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[ \t\-/]+(?:\d{1,2}(?:st|nd|rd|th)?,?[ \t\-/]+)?$"
    r"|\d{1,2}(?:st|nd|rd|th)?,?[ \t]+$", re.I)

# Pay-context vocabulary (used for currency-marked amounts, item "precision").
_PAY_CTX = re.compile(
    r"(?<![a-z])(?:salary|salaries|sal|ctc|comp|compensation|payroll|pay|payouts?|stipend|bonus|increment|hike|wages?|"
    r"award|incentive|reward|"
    r"remuneration|package|earnings?|lpa|per\s+annum|p\.a\.?|buyout|severance|gratuity|take[\s-]?home|"
    r"offer(?:ed)?|fixed|variable|gross|net|basic|allowance|arrears|current|expected|revision|revised|in[\s-]?hand)(?![a-z])")
_NONCOMP_CTX = re.compile(
    r"(?<![a-z])(?:invoices?|gst\w*|igst|cgst|sgst|order(?!\s+to\b)s?|prices?|total|subtotal|shares?|stocks?|"
    r"closed\s+at|revenue|budget|actual|variance|turnover|sales|receipt|mrp|discount|tax|charges?|fees?|quotation|"
    r"purchase|vendor|profit|loss|ebitda|valuation|funding|capex|opex|margin|spend|spending|spent|"
    r"expenditure|above|below)(?![a-z])")
# Limit words (ceiling, cap, limit, threshold, ...) are not in this list: whether a limit governs an amount is
# decided structurally by _statutory_amount, so a person's salary written next to a limit word stays compensation.
_PER_ANNUM = re.compile(r"[ \t]*(?:per[ \t]+(?:annum|year|yr)|p\.?a\.?\b|/[ \t]*(?:annum|year|yr)|a[ \t]+year)", re.I)


def _ctx_norm(s: str) -> str:
    """Lower-case, split camelCase and underscores so 'annualBasePay' reads 'annual base pay'."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z])", " ", s)
    return s.replace("_", " ").lower()


def _nearest(rx: re.Pattern, before: str, after: str) -> int | None:
    best = None
    for m in rx.finditer(before):
        d = len(before) - m.end()
        best = d if best is None or d < best else best
    for m in rx.finditer(after):
        d = m.start()
        best = d if best is None or d < best else best
    return best


_HDR_SEG = re.compile(r"[A-Za-z _./#()&\-]{0,40}")


def _in_header_cell(text: str, kw_start: int, kw_end: int) -> bool:
    """Keyword sits in a CSV header cell (",Fixed Pay,"): the *next* cell is not its value."""
    cs = text.rfind(",", max(0, kw_start - 45), kw_start)
    return cs != -1 and bool(_HDR_SEG.fullmatch(text[cs + 1:kw_end])) and text[cs + 1:cs + 2] not in (" ", "")


def _comp_ctx_ok(text: str, s: int, e: int, radius: int = 80) -> bool:
    """Pay-context within `radius` chars on either side, and no *closer* non-compensation context."""
    before = _ctx_norm(text[max(0, s - radius):s])
    after = _ctx_norm(text[e:e + radius])
    pay = _nearest(_PAY_CTX, before, after)
    if pay is None:
        return False
    neg = _nearest(_NONCOMP_CTX, before, after)
    return neg is None or pay < neg


# --- statutory and limit constants -------------------------------------------------------------------------
# "the bonus ceiling under the Payment of Bonus Act is Rs 7,000 per month" states a public constant, not anyone's
# pay, even though "bonus" is nearer to the amount than "ceiling". An amount is such a constant when, in its own
# clause, (a) a limit word governs it from the left ("ceiling ... is Rs X", "capped at Rs X", "minimum wage of
# Rs X") with no other amount in between, (b) it is the subject of a limit predicate ("Rs X per month is the wage
# ceiling"), or (c) the clause is statutory ("under the ... Act", "as per the Act", "prescribed", "statutory").
# Never when a specific person anchors it in the same clause with no other amount in between: a pronoun ("her",
# "his", "their"), a possessive ("the employee's", "Priya's") or a two-word personal name. So "her salary of Rs
# 18,40,000 exceeds the ceiling" stays compensation, and so does "her bonus is capped at Rs 50,000".
_LIMIT = (r"(?:ceilings?|caps?|capped|limits?|limited|thresholds?|maximum|minimum|max\.?|min\.?|floor|"
          r"not\s+exceeding|not\s+more\s+than|not\s+less\s+than|at\s+most|at\s+least)")
_LIMIT_LEFT = re.compile(r"(?<![a-z])" + _LIMIT + r"(?![a-z])", re.I)
_LIMIT_RIGHT = re.compile(r"\s*(?:(?:per|a|an|each)\s+\w+\s+)?(?:is|are|was|were|being|remains|becomes|as)\s+"
                          r"(?:the|a|an)?\s*(?:[\w'’-]+\s+){0,4}?" + _LIMIT + r"(?![a-z])", re.I)
_LEGAL_TITLE = re.compile(r"(?:[A-Z][\w'’-]*\s+(?:(?:of|and|for|on|the|&)\s+)*)*(?:Acts?|Rules|Regulations?|Codes?|"
                          r"Schemes?|Polic(?:y|ies)|Ordinances?|Amendments?|Bills?|Orders?)\b(?:,?\s*\(?(?:1[89]|20)\d\d\)?)?")
_STATUTE = re.compile(r"(?<![a-z])(?:statutory|statute|prescribed|notified|minimum\s+wages?|wage\s+ceiling|"
                      r"under\s+(?:the|this|that|section\s+\S+\s+of\s+the)\s+\x00|as\s+per\s+(?:the\s+)?(?:\x00|law|rules))",
                      re.I)
_PERSON_ANCHOR = re.compile(
    r"(?<![\w'’])(?i:her|his|their|him|she|he|my|your)(?![\w'’])|[A-Za-z]{2,}['’]s(?![\w])"
    r"|(?<![\w'’])[A-Z][a-z]+\s+[A-Z][a-z]+(?![\w'’])"
    r"|(?<![\w'’])(?i:current|expected|offered|revised|proposed|existing|present|last[\s-]+drawn|take[\s-]?home)"
    r"(?:\s+[a-z]+)?\s+(?i:ctc|salary|pay|compensation|package|basic|gross|fixed|bonus|stipend)(?![\w'’])")
_PUBLIC_WORDS = {"government", "state", "central", "union", "department", "ministry", "corporation", "board",
                 "minimum", "maximum", "wage", "wages", "india", "indian", "labour", "commissioner", "authority",
                 "rs", "inr", "per", "month", "annum", "the", "this", "that", "under", "esi", "epf", "pf",
                 "employer", "employers", "company", "organisation", "organization", "establishment", "act", "rule",
                 "scheme", "fund", "policy", "code", "today", "year"}
_CLAUSE_END = re.compile(r"(?<!\b[A-Z])(?<!\b(?i:rs|no|mr|ms|dr|vs|pa))(?<!\b(?i:p\.a))[.;:!?](?=\s|$)|\n"
                         r"|,\s+(?=(?i:but|and|so|which|while|whereas|though|although|hence|because|since|whereby)\b)"
                         r"|\s(?i:but|whereas|while)\s")
_MONEY = re.compile(r"(?:₹|(?<![A-Za-z])(?:rs\.?|inr))\s*\d|\d[\d,]*(?:\.\d+)?\s*(?:lakhs?|lacs?|crores?|lpa)\b", re.I)


_TITLE_YEAR_PRE = re.compile(r"\b(?:Acts?|Rules|Regulations?|Codes?|Schemes?|Polic(?:y|ies)|Ordinances?|"
                             r"Amendments?|Bills?|Orders?),?[ \t]*\(?$")


def _clause(text: str, s: int, e: int, radius: int = 200) -> tuple[int, int]:
    lo = max(0, s - radius)
    cs = lo
    for m in _CLAUSE_END.finditer(text, lo, s):
        cs = m.end()
    m = _CLAUSE_END.search(text, e, min(len(text), e + radius))
    return cs, (m.start() if m else min(len(text), e + radius))


def _anchored_to_person(text: str, s: int, e: int, cs: int, ce: int) -> bool:
    masked = _LEGAL_TITLE.sub(lambda m: "\x00" * len(m.group()), text[cs:ce])
    for m in _PERSON_ANCHOR.finditer(masked):
        w = m.group()
        if " " in w and (w[0].islower() or w.split()[1][0].islower()
                         or {x.lower() for x in w.split()} & _PUBLIC_WORDS):
            continue
        if "'" in w or "’" in w:
            if w.lower().rstrip("s").rstrip("'’") in _PUBLIC_WORDS:
                continue
        a, b = m.start() + cs, m.end() + cs
        between = text[b:s] if b <= s else text[e:a]
        if not _MONEY.search(between):
            return True
    return False


def _statutory_amount(text: str, s: int, e: int) -> bool:
    """True when the amount at [s, e) is a statutory or limit constant (rules above), not a person's pay."""
    cs, ce = _clause(text, s, e)
    if _anchored_to_person(text, s, e, cs, ce):
        return False
    before = text[cs:s]
    for m in _LIMIT_LEFT.finditer(before):
        if not _MONEY.search(before[m.end():]):
            return True
    if _LIMIT_RIGHT.match(text, e, ce):
        return True
    masked = _LEGAL_TITLE.sub(lambda m: "\x00", text[cs:ce])
    return bool(_STATUTE.search(masked))


_MARKED_CHECKS = 512    # context checks per string for currency-marked amounts; beyond that an amount is kept (fail
                        # closed, bounded time: a flood of "12,345/-" would otherwise cost ~0.2 ms per amount)


def _compensation(text: str) -> list[Cand]:
    out: list[Cand] = _bank_file_amounts(text)
    triggers: list[int] | None = None
    checks = 0
    for m in _COMP_MARKED.finditer(text):
        pre, unit, sfx = m.group("pre"), m.group("unit"), m.group("sfx")
        if not pre and not unit and not sfx:
            continue
        if not pre and not sfx and unit != unit.lstrip() and unit.strip() in ("k", "K", "L"):
            continue  # "12 L" / "5 k" without a currency marker: too ambiguous
        u = unit.strip().lower() if unit else ""
        if not pre and not sfx and u.rstrip("s") in ("million", "mn", "billion", "bn"):
            continue  # "5 million" alone is a count as often as money
        checks += 1
        if checks > _MARKED_CHECKS:
            out.append((m.start(), m.end(), T.COMPENSATION, 0.9, "D2:compensation"))
            continue
        annum = bool(u == "lpa" or _PER_ANNUM.match(text, m.end()))
        if not annum and not _comp_ctx_ok(text, m.start(), m.end()):
            # No pay word nearby. A currency written after the number ("48,500/-", "62,300 rs", "3,450 INR",
            # "2.4 million rupees") is money by structure; a currency before it ("Rs 54,000") counts when its clause
            # is about a person ("credited to her account"). Business money (invoice, GST, revenue, ...) and counts
            # stay out either way.
            after_cur = bool(sfx) or u == "inr"
            if _business_ctx(text, m.start(), m.end()) or _count_follows(text, m.end()):
                continue
            if not pre and len(m.group("num").split(".")[0].replace(",", "")) < 3:
                continue  # "12/-", "5 rs": too small to be pay, and too common in other notations
            if not after_cur:
                cs, ce = _clause(text, m.start(), m.end())
                if not (pre and len(m.group("num").split(".")[0].replace(",", "")) >= 4
                        and _OWN_PAY.search(text, cs, ce) and _MONEY_VERB.search(text, cs, ce)):
                    continue
        if triggers is None:
            triggers = [t.start() for t in _STATUTORY_TRIGGER.finditer(text)]
        i = bisect.bisect_left(triggers, m.start() - 200)      # the statutory rules all need a trigger word in reach
        if i < len(triggers) and triggers[i] < m.end() + 200 and _statutory_amount(text, m.start(), m.end()):
            continue
        if sfx and not pre and not unit:
            # only a currency after the number: the span is the number ("[alias]/-", "[alias] rupees"), scored like a
            # pay-keyword amount
            out.append((m.start(), m.start("sfx"), T.COMPENSATION, 0.8, "D2:compensation"))
            continue
        out.append((m.start(), m.end(), T.COMPENSATION, 0.9, "D2:compensation"))
    for kw in _COMP_KW.finditer(text):
        if _in_header_cell(text, kw.start(), kw.end()) or _in_header_line(text, kw.start()):
            continue  # CSV/TSV header cell
        if text[kw.end():kw.end() + 1] in ("-", "_") and text[kw.end() + 1:kw.end() + 2].isdigit():
            continue  # ticket / identifier such as PAY-35616
        nl = text.find("\n", kw.end())
        line_cut = kw.end() + 30
        if nl != -1 and text[kw.end():nl].strip():
            line_cut = min(line_cut, nl)      # the keyword's own line holds its value; do not read the next line
        for m in _COMP_BARE.finditer(text, kw.end()):
            if m.start() > line_cut:
                break
            n = m.group()
            intpart = n.split(".")[0].replace(",", "")
            if "," not in n and len(intpart) < 4:
                continue
            if _CUR_BEFORE.search(text[max(0, m.start() - 6):m.start()]):
                continue  # currency-marked: the marked path (with its pay-context rule) decides
            if m.start() >= 2 and text[m.start() - 1] in " \t-/." and text[m.start() - 2].isdigit():
                continue  # tail of a phone number / date / id
            if "," not in n and "." not in n and len(n) == 4 and 1990 <= int(n) <= 2100 \
                    and _YEAR_PRE.search(text[max(0, m.start() - 24):m.start()]):
                continue
            if "," not in n and "." not in n and len(n) == 4 and _TITLE_YEAR_PRE.search(text, max(0, m.start() - 16),
                                                                                       m.start()):
                continue  # the year of a statute: "Payment of Bonus Act, 1965"
            if _statutory_amount(text, m.start(), m.end()):
                continue
            out.append((m.start(), m.end(), T.COMPENSATION, 0.8, "D2:compensation"))
    return out


# --- amounts about a person's own pay, with no pay word ("I was paid 28,750 for March", "My in-hand is 71,250") ------
# A thousands-grouped amount below one lakh (1,000-99,999; the Indian and Western groupings agree there) with no
# currency or pay word is money about a person when its clause has BOTH a personal anchor (I, me, my, his, her, their,
# a possessive name) and a word of receiving or deducting money. Business contexts, counts ("12,500 requests") and
# statutory constants stay out.
_OWN_PAY = re.compile(r"(?<![\w'’])(?:I|(?i:me|my|mine|his|her|hers|their|him|she|he))(?![\w'’])|[A-Z][a-z]+['’]s(?![\w])")
_MONEY_VERB = re.compile(r"(?<![a-z])(?:credited|credit|received|receive|receiving|got|get|getting|deducted|came|"
                         r"reached|arrived|landed|"
                         r"(?:was|were|been|be|got|get|getting|being)\s+paid|paid\s+(?:me|him|her|us|them)|"
                         r"deduction|debited|transferred|deposited|earn|earns|earned|earning|drew|draw|drawn|"
                         r"in[\s-]?hand|take[\s-]?home|reimbursed|disbursed|withheld|cut)(?![a-z])", re.I)
_SMALL_GROUPED = re.compile(r"(?<![\w.,/\-])[1-9]\d?,\d{3}(?:\.\d{1,2})?(?![\w%]|[.,/\-]\d)")


def _personal_amounts(text: str) -> list[Cand]:
    out: list[Cand] = []
    if "," not in text:
        return out
    budget = _SHAPE_CHECKS
    for m in _SMALL_GROUPED.finditer(text):
        a, b = m.span()
        if budget <= 0:
            break
        budget -= 1
        if _count_follows(text, b) or _CUR_BEFORE.search(text, max(0, a - 6), a):
            continue
        cs, ce = _clause(text, a, b)
        if not (_OWN_PAY.search(text, cs, ce) and _MONEY_VERB.search(text, cs, ce)):
            continue
        if _business_ctx(text, a, b) or _statutory_amount(text, a, b):
            continue
        out.append((a, b, T.COMPENSATION, 0.75, "D2:compensation_personal"))
    return out


# --- amounts by shape: Indian digit grouping and Indian units (e.g. "18,40,000", "nineteen lakh") ------------
# The lakh grouping d{1,2}(,dd)+,ddd is how Indian money is written; it is never a date, a phone or an ID format. Such a
# number (>= 1,00,000 by construction) is an amount with or without a currency cue, and so is a Western-grouped number
# >= 100,000 (_WESTERN_SHAPE). So is a number phrase, in words or
# digits, with lakh / lac / crore ("seventeen lakh forty thousand", "1.2 crore"; pgw.detect.numwords). Both are
# COMPENSATION-class (C2: aliased and restored on EXT_PSEUDO, generalised elsewhere), except
#   - a statutory or limit constant (_statutory_amount), and sealed public_constants (held out before detection);
#   - a count: the amount is directly followed by a unit of count ("1,50,000 employees", "2 lakh users");
#   - business money: a non-pay context (invoice, total, budget, revenue, purchase, ...) is nearer than any pay
#     context, the same rule as currency-marked amounts (_comp_ctx_ok);
#   - with a currency cue ("Rs 18,40,000", "INR 2 lakh", "18,40,000 INR") the amount is decided exactly as the
#     currency-marked path decides it: pay context or per annum required.
_LAKH_GROUPED = re.compile(r"(?<![\w,])(?<!\d\.)[1-9]\d?(?:,\d\d)+,\d{3}(?:\.\d{1,2})?(?![\w]|[.,]\d)")
_WESTERN_GROUPED = re.compile(r"(?<![\w,])(?<!\d\.)[1-9]\d{0,2}(?:,\d{3})+(?:\.\d{1,2})?(?![\w]|[.,]\d)")
# Western grouping >= 100,000 without a cue ("2,100,000") counts too. Measured on dev3build arm A with NER: gates
# identical with and without it (neg_fp_item_rate 0.7056, collateral_masking 0.0336; one Western amount >= 100,000
# in the corpus, a statutory one), so it is kept for recall.
_WESTERN_SHAPE = True
_COUNT_NOUNS = frozenset(
    "employees employee people persons person staff workers members users customers clients candidates applicants "
    "students participants visitors subscribers followers patients beneficiaries households families citizens voters "
    "records rows entries lines files documents pages items units pieces copies transactions requests tickets calls "
    "messages emails sms downloads views visits clicks installs devices accounts shares votes hours days weeks months "
    "years minutes seconds km kms kilometres kilometers miles steps points sq sqft tonnes tons kg litres liters "
    "seats jobs vacancies positions roles hires posts applications responses complaints queries orders cases "
    "tickets issues".split())
_COUNT_AFTER = re.compile(r"\+?[ \t]*(?:-[ \t]*)?(?:new|more|active|registered|total|odd|plus)?[ \t]*([A-Za-z]+)")


def _count_follows(text: str, e: int) -> bool:
    m = _COUNT_AFTER.match(text, e)
    return bool(m) and m.group(1).lower() in _COUNT_NOUNS


_CUE_AFTER = re.compile(r"[ \t]*(?i:lpa|inr|usd|eur|gbp|aed|sgd|cad|aud|chf|jpy|dollars?|euros?)(?![A-Za-z])")


def _business_ctx(text: str, s: int, e: int, radius: int = 80) -> bool:
    """A non-pay money context is nearer than any pay context (or there is no pay context)."""
    before = _ctx_norm(text[max(0, s - radius):s])
    after = _ctx_norm(text[e:e + radius])
    neg = _nearest(_NONCOMP_CTX, before, after)
    if neg is None:
        return False
    pay = _nearest(_PAY_CTX, before, after)
    return pay is None or neg <= pay


# Words without which _statutory_amount cannot hold (its limit, predicate and statute rules all need one of them).
_STATUTORY_TRIGGER = re.compile(r"(?<![a-z])(?:" + _LIMIT + r"|statut\w*|prescribed|notified|wages?|under|as\s+per)"
                                r"(?![a-z])", re.I)
_SHAPE_CHECKS = 256     # full statutory checks per string; beyond that an amount is kept (fail closed, bounded time)


def _amount_shapes(text: str) -> list[Cand]:
    out: list[Cand] = []
    rxs = (_LAKH_GROUPED, _WESTERN_GROUPED) if _WESTERN_SHAPE else (_LAKH_GROUPED,)
    spans: list[tuple[int, int]] = []
    for rx in rxs:
        for m in rx.finditer(text):
            if rx is _WESTERN_GROUPED and float(m.group().replace(",", "")) < 100_000:
                continue
            spans.append(m.span())
    words = {(a, b) for a, b, _ in unit_amounts(text)}
    spans += words
    if not spans:
        return out
    triggers = [m.start() for m in _STATUTORY_TRIGGER.finditer(text)]
    budget = _SHAPE_CHECKS
    for a, b in sorted(spans):
        if _count_follows(text, b):
            continue
        cue = _CUR_BEFORE.search(text, max(0, a - 6), a) or _CUE_AFTER.match(text, b)
        if cue:
            if not (_PER_ANNUM.match(text, b) or _comp_ctx_ok(text, a, b)):
                continue
        elif _business_ctx(text, a, b):
            continue
        i = bisect.bisect_left(triggers, a - 200)      # a clause never reaches beyond 200 characters (_clause)
        if i < len(triggers) and triggers[i] < b + 200 and budget > 0:
            budget -= 1
            if _statutory_amount(text, a, b):
                continue
        # a unit phrase scores like a currency-marked amount, so "14 lakh 50 thousand" is one amount, not "14 lakh"
        out.append((a, b, T.COMPENSATION, 0.9 if (a, b) in words else 0.75, "D2:compensation_shape"))
    return out


# --------------------------------------------------------------------------
# DATE_OF_BIRTH
# --------------------------------------------------------------------------
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = re.compile(
    r"(?P<iso>(?P<iy>\d{4})[\-/](?P<im>\d{1,2})[\-/](?P<id>\d{1,2}))"
    r"|(?P<cmp>(?P<cy>(?:19|20)\d{2})(?P<cm>0[1-9]|1[0-2])(?P<cd>0[1-9]|[12]\d|3[01]))"
    r"|(?P<num>(?P<na>\d{1,2})[/\-.](?P<nb>\d{1,2})[/\-.](?P<ny>\d{4}))"
    r"|(?P<num2>(?P<n2a>\d{1,2})[/\-.](?P<n2b>\d{1,2})[/\-.](?P<n2y>\d{2})(?!\d))"
    r"|(?P<dmy>(?P<dd>\d{1,2})(?:st|nd|rd|th)?[ \-]+" + _MON + r"\.?,?[ \-]+(?P<dy>\d{4}))"
    r"|(?P<mdy>" + _MON + r"\.?[ \-]+(?P<md>\d{1,2})(?:st|nd|rd|th)?,?[ \-]+(?P<my>\d{4}))",
    re.I,
)
_DOB_KW = re.compile(
    _NA + r"(?:dob|d\.o\.b\.?|date[ \t_]+of[ \t_]+birth|birth[ \t_]*date|birthday|birth|born|jan(?:a)?m(?:[ \t]+tithi)?)"
    r"(?![A-Za-z])", re.I)
# "DOB 12/03/1984, not 1985-01-02": a correction pair is two DOB values.
_DOB_MORE = re.compile(r"[ \t]*[,;]?[ \t]*(?:not|instead[ \t]+of|vs\.?|and|to|was|from|->|→|previously|earlier)?[ \t]*[,:]?[ \t]*")


def _date_ok(m: re.Match, text: str) -> bool:
    if m.start() > 0 and text[m.start() - 1].isalnum():
        return False
    if m.end() < len(text) and text[m.end()].isalnum() and not (m.group("iso") and text[m.end()] == "T"):
        return False
    if m.group("iso"):
        y, mo, d = int(m.group("iy")), int(m.group("im")), int(m.group("id"))
        return 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31
    if m.group("cmp"):
        return True
    if m.group("num2"):
        a, b = int(m.group("n2a")), int(m.group("n2b"))
        return 1 <= a <= 31 and 1 <= b <= 31 and (a <= 12 or b <= 12)
    if m.group("num"):
        a, b, y = int(m.group("na")), int(m.group("nb")), int(m.group("ny"))
        return 1900 <= y <= 2100 and 1 <= a <= 31 and 1 <= b <= 31 and (a <= 12 or b <= 12)
    if m.group("dmy"):
        return 1 <= int(m.group("dd")) <= 31 and 1900 <= int(m.group("dy")) <= 2100
    return 1 <= int(m.group("md")) <= 31 and 1900 <= int(m.group("my")) <= 2100


# Short and spoken forms, accepted only in a birth context (_dob): "23rd Jul '91", "14-Aug-89", "1st Jan'90",
# "5th of March, 1988", "March 3rd, '95", "twenty-third of July 1991", "Thirty First December 1979".
_ORD_WORD = (r"(?:(?:twenty|thirty)[ \t\-]?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)|first|second|"
             r"third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|"
             r"sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth)")
_SDAY = r"(?:(?P<d>\d{1,2})(?:st|nd|rd|th)?|(?P<dw>" + _ORD_WORD + r"))"
_SDAY2 = r"(?:(?P<d2>\d{1,2})(?:st|nd|rd|th)?|(?P<dw2>" + _ORD_WORD + r"))"
_SYEAR = r"(?:['’][ \t]?\d{2}|(?:19|20)\d{2}|\d{2})"
_DATE_SHORT = re.compile(
    r"(?<![\w])(?:" + _SDAY + r"(?:[ \t]+of[ \t]+|[ \t\-./]*)" + _MON + r"\.?,?[ \t\-./]*" + _SYEAR +
    r"|" + _MON + r"\.?[ \t\-]+" + _SDAY2 + r",?[ \t]*" + _SYEAR + r")(?![\w])", re.I)


def _short_ok(m: re.Match) -> bool:
    d = m.group("d") or m.group("d2")
    return d is None or 1 <= int(d) <= 31


def _dob(text: str) -> list[Cand]:
    out: list[Cand] = []
    for kw in _DOB_KW.finditer(text):
        if _in_header_cell(text, kw.start(), kw.end()) or _in_header_line(text, kw.start()):
            continue  # CSV/TSV header cell: the next cell is not its value
        end = min(len(text), kw.end() + 60 + 40)
        first = next((m for m in _DATE.finditer(text, kw.end(), end) if m.start() <= kw.end() + 60 and _date_ok(m, text)),
                     None)
        short = next((m for m in _DATE_SHORT.finditer(text, kw.end(), end) if m.start() <= kw.end() + 60 and _short_ok(m)),
                     None)
        if short is not None and (first is None or short.start() < first.start()
                                  or (short.start() == first.start() and short.end() > first.end())):
            out.append((short.start(), short.end(), T.DATE_OF_BIRTH, 0.9, "D2:dob"))
            continue
        for m in () if first is None else (first,):
            out.append((m.start(), m.end(), T.DATE_OF_BIRTH, 0.9, "D2:dob"))
            pos = m.end()
            for _ in range(2):
                pos = _DOB_MORE.match(text, pos).end()
                m2 = _DATE.match(text, pos)
                if not m2 or not _date_ok(m2, text):
                    break
                out.append((m2.start(), m2.end(), T.DATE_OF_BIRTH, 0.9, "D2:dob"))
                pos = m2.end()
            break
    return out


# --------------------------------------------------------------------------
# D1 in free text: values found by the FIELD NAME attached to them
# --------------------------------------------------------------------------
_AMOUNT = "AMOUNT"   # pseudo-type: COMPENSATION only with pay context nearby
_IDDOC = "IDDOC"     # pseudo-type: identity-document concept; the value's shape decides AADHAAR / PAN
_IDDOC_WORDS = {"national", "nat", "natl", "nationalid", "govt", "government", "governmentid", "govtid", "identity", "identification",
                "uid", "ssn", "passport", "voter", "licence", "license", "kyc", "taxid", "nric", "uin", "tin", "proof"}
_PERSONMULTI = "PERSONMULTI"   # pseudo-type: PERSON only when the value is two or more capitalised words
_PERSON_MULTI_KEYS = {"user", "owner", "assignee", "author", "sender", "recipient", "reporter", "requestedby", "approvedby",
                      "createdby", "actor", "customer", "client", "agent", "operator"}
_WEAKSECRET = "WEAKSECRET"   # pseudo-type: only a high-entropy value under a secret-ish key is a SECRET
_WEAK_WORDS = {"session", "seed", "cookie", "salt", "pepper", "hmac", "sas"}
_WEAK_KEY_NEG = {"primary", "foreign", "public", "sort", "partition", "cache", "object", "name", "type", "id", "index", "order",
                 "lookup", "translation", "i18n", "count", "status", "length", "size", "expiry", "expires", "ttl", "timeout",
                 "duration", "enabled", "flag", "label", "text", "path", "url"}
_IDDOC_QUAL = {"id", "number", "no", "num", "card", "document", "doc", "code", "identifier", "proof", "details"}
_IDDOC_SINGLE = {"uid", "ssn", "kyc", "nationalid", "govtid", "governmentid", "taxid", "nric", "uin", "identity", "identification"}
_IDDOC_NEG = {"type", "issuer", "country", "expiry", "expires", "issue", "issued", "date", "valid", "validity", "authority",
              "name", "status", "verified", "verification", "photo", "image", "place", "state", "document_type"}
# keys whose 12-digit values are known non-Aadhaar identifiers: never escalated structurally
_ESC_NEG = {"order", "invoice", "ref", "reference", "txn", "transaction", "tracking", "awb", "utr", "ticket", "shipment", "po",
            "consignment", "imei", "serial", "sku", "meeting", "conference", "room", "webinar", "pallet", "batch", "lot", "rrn",
            "arn", "cheque", "check", "gst", "pincode", "zip", "request", "job", "case", "session", "trace", "correlation",
            "amount", "total", "price", "phone", "mobile", "account", "acct", "card", "otp", "timestamp", "ts", "epoch"}

_SECRET_WORDS = {"password", "passwd", "pwd", "passcode", "passphrase", "secret", "secrets", "token", "apikey", "credential", "credentials"}
_SECRET_NEG = {"type", "expires", "expiry", "expiration", "ttl", "policy", "length", "count", "age", "hint", "label",
               "placeholder", "endpoint", "url", "uri", "field", "name", "status", "reset", "required", "enabled",
               "changed", "date", "updated", "modified", "created", "last", "at", "time", "question", "manager",
               "rotation", "strength", "min", "max", "flag", "is", "has", "file", "path", "provider"}
_ACCT_NEG = {"type", "name", "holder", "status", "category", "manager", "balance", "limit", "currency", "open", "opened",
             "closed", "branch", "executive", "owner", "group", "head", "mapping", "code", "class"}
_COMP_WORDS = {"salary", "salaries", "ctc", "compensation", "bonus", "stipend", "wage", "wages", "remuneration",
               "increment", "hike", "package", "earnings", "earning", "payout", "gross", "basic", "base"}
_COMP_NEG = {"grade", "band", "level", "type", "frequency", "currency", "structure", "slab", "date", "effective", "reason",
             "status", "code", "id", "cycle", "group", "period", "mode", "slip", "scale", "month", "day", "days", "calendar",
             "component", "rule", "percent", "percentage", "pct", "ratio", "letter", "template", "plan", "review"}
_AMOUNT_NEG = {"invoice", "tax", "gst", "total", "price", "order", "refund", "fee", "charge", "balance", "limit", "credit",
               "debit", "loan", "emi", "payment", "cgst", "sgst", "igst", "discount", "shipping", "subtotal", "mrp", "cost"}
_NAME_PREFIX = {"full", "legal", "first", "last", "middle", "preferred", "worker", "employee", "emp", "manager", "supervisor",
                "display", "given", "family", "candidate", "beneficiary", "holder", "cardholder", "nominee", "spouse",
                "father", "mother", "referrer", "reporting", "approver", "requester", "requestor", "contact", "person",
                "applicant", "payee", "hrbp", "sur", "account", "card", "reviewer", "interviewer", "recruiter", "actor"}
_NAME_SINGLE = {"member", "dependent", "manager", "supervisor", "approver", "cardholder", "actor", "requester", "requestor", "payee",
                "beneficiary", "nominee", "spouse", "candidate", "applicant", "employee", "worker", "surname",
                "interviewer", "recruiter", "hrbp", "reviewer"}
_NOT_NAMES = {"system", "admin", "administrator", "user", "null", "none", "n/a", "na", "unknown", "active", "inactive",
              "pending", "approved", "rejected", "yes", "no", "true", "false", "employee", "manager", "hr", "team", "all",
              "self", "other", "tbd", "hrbp", "worker", "candidate", "support", "service", "bot", "system user"}
_KEY_QUALIFIERS = {"father", "mother", "husband", "wife", "guardian", "emergency", "alternate", "primary", "secondary",
                   "personal", "official", "office", "home", "new", "old", "previous", "current", "bank", "company", "nominee"}
_ORG_PARENTS_TXT = {"company", "organization", "org", "department", "cost_center", "location", "job_profile", "business_unit",
                    "division", "team", "supervisory_organization", "position", "workertype", "worker_type", "legal_entity",
                    "info", "collection", "request", "item", "items", "environment", "project", "workspace", "service", "app",
                    "application", "connector", "connectors", "integration", "integrations", "system", "tool", "api", "endpoint",
                    "repo", "repository", "package", "plugin", "module", "tenant", "product", "vendor", "config", "settings"}


_KW_AC = re.compile(r"(?i)\ba\s*/\s*c\b")
_KW_DOB = re.compile(r"(?i)\bd\.\s*o\.\s*b\b\.?")
_KW_ACR = re.compile(r"([A-Z]+)([A-Z][a-z])")
_KW_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KW_SPLIT = re.compile(r"[^A-Za-z0-9]+")


@functools.lru_cache(maxsize=8192)
def _key_words_t(key: str) -> tuple:
    s = _KW_AC.sub(" account ", key)
    s = _KW_DOB.sub(" dob ", s)
    if ":" in s:
        s = s.rsplit(":", 1)[-1]
    s = _KW_ACR.sub(r"\1 \2", s)
    s = _KW_CAMEL.sub(" ", s)
    out: list[str] = []
    for w in _KW_SPLIT.split(s.lower()):
        if not w:
            continue
        if out and out[-1] == "e" and w == "mail":
            out[-1] = "email"
        else:
            out.append(w)
    return tuple(out)


def _key_words(key: str) -> list[str]:
    return list(_key_words_t(str(key)))


def _has_seq(words: list[str], *seq: str) -> bool:
    n = len(seq)
    return any(tuple(words[i:i + n]) == seq for i in range(len(words) - n + 1))


def _classify_words(words: list[str]):
    """Field-name words -> EntityType / _AMOUNT / None."""
    W = set(words)
    if not W:
        return None
    if (W & _SECRET_WORDS or W & {"pw", "pswd"} or (len(words) > 1 and words[-1] == "pass")
            or _has_seq(words, "api", "key") or _has_seq(words, "access", "key")
            or _has_seq(words, "private", "key") or _has_seq(words, "secret", "key") or "accesskey" in W) \
            and not W & _SECRET_NEG:
        return T.SECRET
    if any(len(w) > 8 and w.endswith("password") or len(w) > 6 and w.endswith("passwd") for w in W) \
            and not W & _SECRET_NEG:
        return T.SECRET
    if W & {"email", "emailid", "emailaddress", "mail", "mailid"}:
        return T.EMAIL
    if W & {"aadhaar", "aadhar", "aadhaarnumber", "uidai"}:
        return T.AADHAAR
    if W & {"pan", "pannumber", "pancard"} and not W & {"india"} - W:
        return T.PAN
    if W & {"ifsc", "ifsccode"}:
        return T.IFSC
    if W & {"upi", "vpa"} or ("address" in W and W & {"payment", "virtual", "pay", "upi"}):
        return T.UPI_ID
    if not W & _IDDOC_NEG and ((W & _IDDOC_WORDS and (W & _IDDOC_QUAL or W & _IDDOC_SINGLE))
                               or ("tax" in W and W & {"id", "number", "no"})):
        return _IDDOC
    if "card" in W and W & {"number", "no", "num"} and not W & {"pan"}:
        return T.CARD
    if (W & {"account", "acct", "bankaccount"} or ("acc" in W and W & {"no", "number", "num"})
            or ("ac" in W and W & {"bank", "no", "number", "num", "salary", "payroll"})) and not W & _ACCT_NEG:
        return T.BANK_ACCOUNT
    if W & {"phone", "mobile", "cell", "telephone", "tel", "mob", "contact", "phoneno", "mobileno"} \
            and not (W & {"name", "person"} and "contact" in W and not W & {"phone", "mobile", "cell", "tel", "no", "number"}):
        return T.PHONE
    if W & {"dob", "birth", "birthday", "birthdate", "born", "janm", "janam"} and not W & {"place", "country", "city", "state", "certificate"}:
        return T.DATE_OF_BIRTH
    if W & _COMP_WORDS and not W & _COMP_NEG:
        return T.COMPENSATION
    if "pay" in W and not W & _COMP_NEG:
        return T.COMPENSATION
    if W & {"amount", "amt"} and not W & _AMOUNT_NEG:
        return _AMOUNT
    if (W & {"employee", "emp", "worker", "staff", "associate", "personnel", "person"} and W & {"id", "no", "number", "num", "code"}) \
            or W & {"empcode", "empid", "empno", "employeeid", "workerid", "employeenumber", "employeecode"} \
            or words == ["emp"]:
        return T.EMPLOYEE_ID
    if (W & _WEAK_WORDS or (words[-1] == "key" and len(W) > 1)) and not W & (_WEAK_KEY_NEG | _SECRET_NEG):
        return _WEAKSECRET
    if words[-1] in ("name", "names") and set(words[:-1]) <= _NAME_PREFIX:
        return T.PERSON
    if words[-1] in _PERSON_MULTI_KEYS and set(words[:-1]) <= _NAME_PREFIX:
        return _PERSONMULTI
    if words[-1] in _NAME_SINGLE and set(words[:-1]) <= (_NAME_PREFIX | {"reporting", "direct", "line", "hr", "functional",
                                                                          "skip", "dotted", "approving", "primary"}):
        return T.PERSON
    if words[-1] == "holder" and set(words[:-1]) <= {"account", "card", "policy"}:
        return T.PERSON
    return None


@functools.lru_cache(maxsize=8192)
def _classify_key(key: str):
    return _classify_words(_key_words(key))


# ---- value validators ----------------------------------------------------------
_R_EMAIL = re.compile(r"[^\s@<>\"',;()\[\]|]+@[^\s@<>\"',;()\[\]|]+")
_R_PHONE_GEN = re.compile(r"\(?\+?\d{1,4}\)?(?:[ \-.]\(?\d{2,5}\)?){1,4}|\d{10,15}")
_R_AADHAAR = re.compile(r"[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}(?!\d)")
_R_PAN = re.compile(r"[A-Za-z]{3}[ABCFGHLJPTabcfghljpt][A-Za-z]\d{4}[A-Za-z](?![A-Za-z0-9])")
_R_IFSC = re.compile(r"[A-Za-z]{4}0[A-Za-z0-9]{6}(?![A-Za-z0-9])")
_R_BANK = re.compile(r"\d{9,18}(?!\d)|\d{3,6}(?:[ \-]\d{3,6}){1,4}(?!\d)")
_R_UPI = re.compile(r"[A-Za-z0-9._\-]{2,64}@[A-Za-z][A-Za-z0-9]{1,30}(?![A-Za-z0-9.])")
_R_CARD = re.compile(r"\d(?:[ \-]?\d){12,18}(?!\d)")
_R_COMP = re.compile(
    r"(?P<pre>" + _CUR_PRE + r"[ \t]*)?(?P<num>" + _NUM + r")(?P<unit>[ \t]*" + _UNIT + r")?(?:/-)?(?![A-Za-z0-9])(?![\-/.:]\d)")
_R_EMPID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-/]{3,19}(?![A-Za-z0-9_\-/])")
_TOK = r"(?:[^\W\d_]|['’])(?:[^\W\d_]|['’.\-])*"
_R_NAME = re.compile(_TOK + r"(?:(?:,[ \t]*|[ \t]+)" + _TOK + r"){0,4}")
_PARTICLES = {"bin", "van", "von", "de", "da", "al", "el", "la", "le", "der", "den", "di", "du", "ben", "ibn", "mac", "mc"}


def _digits(v: str) -> str:
    return re.sub(r"\D", "", v)


def _boundary_ok(text: str, e: int) -> bool:
    return e >= len(text) or not (text[e].isalnum() or text[e] == "_")


def _match_value(t, text: str, pos: int, end: int, whole: bool, bare_name: bool = False) -> tuple[int, int] | None:
    """Type-validated value starting at `pos` inside text[pos:end]. `whole` => must span the region."""
    if pos >= end:
        return None
    if t is T.EMAIL:
        m = _R_EMAIL.match(text, pos, end)
        if not m:
            return None
        s2, e2 = m.start(), m.end()
        while e2 > s2 and text[e2 - 1] in ".:":
            e2 -= 1
        if not re.search(r"@[^@]*\.[A-Za-z]{2,}$", text[s2:e2]) and not whole:
            return None
        res = (s2, e2)
    elif t is T.PHONE:
        m = None
        for rx in (_PHONE_IN, _PHONE_LL, _PHONE_INTL):
            m = rx.match(text, pos, end)
            if m:
                break
        if not m:
            m = _R_PHONE_GEN.match(text, pos, end)
            if m and not (10 <= len(_digits(m.group())) <= 15):
                m = None
        if not m:
            return None
        res = (m.start(), m.end())
    elif t is T.AADHAAR:
        m = _R_AADHAAR.match(text, pos, end)
        if not m or not verhoeff_valid(_digits(m.group())) or not _boundary_ok(text, m.end()):
            return None
        res = (m.start(), m.end())
    elif t in (T.PAN, T.IFSC):
        m = (_R_PAN if t is T.PAN else _R_IFSC).match(text, pos, end)
        if not m:
            return None
        res = (m.start(), m.end())
    elif t is T.BANK_ACCOUNT:
        m = _R_BANK.match(text, pos, end)
        if not m or not (9 <= len(_digits(m.group())) <= 18) or not _boundary_ok(text, m.end()):
            return None
        res = (m.start(), m.end())
    elif t is T.UPI_ID:
        m = _R_UPI.match(text, pos, end)
        if not m:
            return None
        res = (m.start(), m.end())
    elif t is T.CARD:
        m = _R_CARD.match(text, pos, end)
        if not m:
            return None
        d = _digits(m.group())
        if not (13 <= len(d) <= 19 and _iin_ok(d) and luhn_valid(d)):
            return None
        res = (m.start(), m.end())
    elif t is T.DATE_OF_BIRTH:
        m = _DATE.match(text, pos, end)
        if not m or not _date_ok(m, text):
            return None
        res = (m.start(), m.end())
    elif t is T.COMPENSATION:
        m = _R_COMP.match(text, pos, end)
        if not m:
            return None
        num = m.group("num")
        big = "," in num or len(num.split(".")[0]) >= 4
        if not (m.group("pre") or m.group("unit") or big):
            return None
        res = (m.start(), m.end())
    elif t is T.EMPLOYEE_ID:
        m = _R_EMPID.match(text, pos, end)
        if not m or not any(c.isdigit() for c in m.group()):
            return None
        if re.fullmatch(r"\d{10,}", m.group()) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", m.group()) \
                or re.fullmatch(r"\d{1,4}[/\-]\d{1,2}[/\-]\d{1,4}", m.group()):
            return None
        res = (m.start(), m.end())
    elif t is T.PERSON:
        m = _R_NAME.match(text, pos, end)
        if not m:
            return None
        s2, e2 = m.start(), m.end()
        toks = list(re.finditer(_TOK, text[s2:e2]))
        if not whole:
            # a token directly followed by ':'/'=' is the next key, not part of the name
            while len(toks) > 1 and re.match(r"[ \t]*[:=/]", text[s2 + toks[-1].end():s2 + toks[-1].end() + 3]):
                toks.pop()
            # "... Father's name: X": the last token qualifies the next key
            if len(toks) > 1 and re.match(r"[ \t]+names?[ \t]*[:=]", text[s2 + toks[-1].end():s2 + toks[-1].end() + 12]) \
                    and re.sub(r"['’]s?$", "", toks[-1].group().lower()) in (_NAME_PREFIX | _KEY_QUALIFIERS):
                toks.pop()
            # follow-on tokens must look like the first one (Capitalised names, not "... is on leave")
            keep = 1
            first_up = text[s2 + toks[0].start()].isupper()
            for tk in toks[1:]:
                w = tk.group()
                if (not first_up) or w[0].isupper() or w.lower() in _PARTICLES:
                    keep += 1
                else:
                    break
            toks = toks[:keep]
            e2 = s2 + toks[-1].end()
            if text[e2 - 1] == ",":
                e2 -= 1
        n = len(toks)
        if not 1 <= n <= 5:
            return None
        surface = text[s2:e2].strip().rstrip(",")
        if surface.lower() in _NOT_NAMES or len(surface) < 2:
            return None
        if bare_name and not text[s2].isupper():
            return None
        res = (s2, s2 + len(surface))
    elif t is T.SECRET:
        if whole:
            m = re.compile(r"[^\r\n]+").match(text, pos, end)
        else:
            m = re.compile(r"[^\s\"',;<>]+").match(text, pos, end)
        if not m:
            return None
        v = m.group()
        if _placeholder(v) or (whole and len(v.split()) > 4 and v.islower()) or v[0] in "([{":
            return None
        res = (m.start(), m.end())
    else:
        return None
    if whole:
        if text[res[1]:end].strip(" \t\r\n,;."):
            return None
    elif t not in (T.EMAIL, T.SECRET) and not _boundary_ok(text, res[1]):
        return None
    return res


def _emit_typed(out: list, text: str, t, pos: int, end: int, whole: bool, *, bare_name: bool = False,
                amount_ctx: bool = False) -> None:
    while pos < end and text[pos] in " \t":
        pos += 1
    while end > pos and text[end - 1] in " \t\r":
        end -= 1
    if t is _PERSONMULTI:
        r = _match_value(T.PERSON, text, pos, end, whole)
        if r is not None:
            toks = text[r[0]:r[1]].replace(",", " ").split()
            if len(toks) >= 2 and all(w[0].isupper() for w in toks if w.lower() not in _PARTICLES):
                out.append((r[0], r[1], T.PERSON, 0.95, "D1:field_ctx"))
        return
    if t is _WEAKSECRET:
        r = _match_value(T.SECRET, text, pos, end, whole)
        if r is not None and _high_entropy(text[r[0]:r[1]]):
            out.append((r[0], r[1], T.SECRET, 0.95, "D1:field_ctx"))
        return
    if t is _IDDOC:
        for cand in (T.AADHAAR, T.PAN):
            r = _match_value(cand, text, pos, end, whole)
            if r is not None:
                out.append((r[0], r[1], cand, 0.95, "D1:field_ctx"))
                return
        return
    if t is _AMOUNT:
        t = T.COMPENSATION
        r = _match_value(t, text, pos, end, whole)
        if r is None or not _amount_ctx(text, r[0], r[1]):
            return
    else:
        r = _match_value(t, text, pos, end, whole, bare_name)
    if r is not None:
        out.append((r[0], r[1], t, 0.95, "D1:field_ctx"))


def _high_entropy(v: str) -> bool:
    """Random-looking token: >= 16 chars, no spaces, more than one character class, Shannon entropy >= 3 bits/char."""
    if len(v) < 16 or " " in v or "/" == v[0] or v.startswith(("http", "www.")):
        return False
    classes = sum([any(c.islower() for c in v), any(c.isupper() for c in v), any(c.isdigit() for c in v),
                   any(not c.isalnum() for c in v)])
    if classes < 2 or not (any(c.isdigit() for c in v) or any(not c.isalnum() and c not in "-_" for c in v)):
        return False
    freq = collections.Counter(v)
    ent = -sum(n / len(v) * math.log2(n / len(v)) for n in freq.values())
    return ent >= 3.0


def _escalate(out: list, text: str, pos: int, end: int, key_words: list) -> None:
    """Structural escalation: a whole Verhoeff-valid 12-digit value in an HRIS-shaped payload is an Aadhaar
    even when its key is unknown (nationalId, uid-like columns, opaque headers). Known non-identity keys skip."""
    if set(key_words) & _ESC_NEG:
        return
    while pos < end and text[pos] in " \t":
        pos += 1
    while end > pos and text[end - 1] in " \t\r":
        end -= 1
    r = _match_value(T.AADHAAR, text, pos, end, True)
    if r is not None:
        out.append((r[0], r[1], T.AADHAAR, 0.85, "D1:field_ctx"))


_AMOUNT_CTX = re.compile(
    r"(?<![a-z])(?:salary|salaries|sal|ctc|compensation|pay|payroll|wages?|stipend|bonus|remuneration|earnings?|package|"
    r"increment|hike|bank\s+advice|payouts?|award|incentive|reward|comp)(?![a-z])")
_AMOUNT_ABS_NEG = re.compile(r"(?<![a-z])(?:invoices?|gst\w*|order(?!\s+to\b)s?|prices?|shares?|stocks?|revenue|budget|purchase|limits?|"
                             r"threshold|spend|spending)(?![a-z])")


_DOC_PAY = re.compile(r"(?<![a-z])(?:salary|salaries|payroll|payouts?|stipend|bonus|wages?|remuneration|incentive|reimbursement)(?![a-z])")


def _amount_ctx(text: str, s: int, e: int) -> bool:
    if not _AMOUNT_CTX.search(_ctx_norm(text[max(0, s - 200):s] + " " + text[e:e + 200])):
        # a bank file (records carrying IFSC codes) whose document speaks of salary / payroll / payout
        if not ((_IFSC.search(text) and _DOC_PAY_LOOSE.search(text))
                or (_DOC_PAY.search(_ctx_norm(text)) and not _AMOUNT_ABS_NEG.search(_ctx_norm(text)))):
            return False
    return not _AMOUNT_ABS_NEG.search(_ctx_norm(text[max(0, s - 80):s] + " " + text[e:e + 80]))


# ---- scanners --------------------------------------------------------------------
_HINT_KEYS = {"type", "idtype", "id_type", "documenttype", "document_type", "doctype", "doc_type", "kind", "category",
              "field", "fieldname", "field_name", "attribute", "attr", "label", "key", "property", "name", "identifiertype",
              "identifier_type", "nationalidtype"}
_HINT_VALUE_KEYS = {"value", "id", "number", "newvalue", "new_value", "oldvalue", "old_value", "from", "to", "before", "after",
                    "currentvalue", "current_value", "previousvalue", "previous_value", "identifier", "val", "idnumber",
                    "id_number", "data", "content"}
_DIFF_KEYS = {"from", "to", "oldvalue", "old_value", "newvalue", "new_value", "before", "after"}

_JP_DQ = re.compile(
    r"\\?\"(?P<key>[^\"\\\r\n]{1,64})\\?\"[ \t]*:[ \t\r\n]*(?:\\?\"(?P<sv>[^\"\\\r\n]*)\\?\"|(?P<nv>-?\d[\d.]*)(?![\w.]))")
_JP_SQ = re.compile(r"'(?P<key>[^'\r\n]{1,64})'[ \t]*:[ \t\r\n]*(?:'(?P<sv>[^'\r\n]*)'|(?P<nv>-?\d[\d.]*)(?![\w.]))")
_ORG_PARENT_NORM = {re.sub(r"[^a-z0-9]", "", k) for k in _ORG_PARENTS_TXT}


def _parent_key(text: str, pos: int) -> str | None:
    """Key of the JSON object enclosing `pos` (best effort, bounded look-back)."""
    depth = 0
    lo = max(0, pos - 4000)
    for i in range(pos - 1, lo - 1, -1):
        c = text[i]
        if c in "}]":
            depth += 1
        elif c in "{[":
            if depth == 0:
                pre = text[max(0, i - 120):i].rstrip()
                if pre.endswith("["):
                    pre = pre[:-1].rstrip()
                m = re.search(r"[\"']([^\"'\\\r\n]{1,64})[\"'\\]*\s*:$", pre)
                if m:
                    return re.sub(r"[^a-z0-9]", "", _ctx_norm(m.group(1)))
                if c == "[":
                    continue
                return None
            depth -= 1
    return None


def _json_pairs(text: str) -> list[dict]:
    pairs = []
    for rx in (_JP_DQ, _JP_SQ):
        for m in rx.finditer(text):
            if m.group("sv") is not None:
                vs, ve = m.start("sv"), m.end("sv")
            else:
                vs, ve = m.start("nv"), m.end("nv")
            pairs.append({"ks": m.start("key"), "key": m.group("key"), "vs": vs, "ve": ve, "str": m.group("sv") is not None,
                          "ps": m.start(), "pe": m.end()})
    pairs.sort(key=lambda p: p["ps"])
    return pairs


def _same_object(text: str, a: dict, b: dict) -> bool:
    lo, hi = (a, b) if a["ps"] <= b["ps"] else (b, a)
    return not re.search(r"[{}\[\]]", text[lo["pe"]:hi["ps"]])


def _scan_json(text: str, out: list) -> None:
    if ":" not in text:
        return
    pairs = _json_pairs(text)
    types: list = [None] * len(pairs)
    for i, p in enumerate(pairs):
        types[i] = _classify_key(p["key"])
    for i, p in enumerate(pairs):
        t = types[i]
        kn = re.sub(r"[^a-z0-9_]", "", "_".join(_key_words(p["key"])))
        if t is not None:
            bare = _key_words(p["key"]) == ["name"]
            if bare:
                par = _parent_key(text, p["ps"])
                if par in _ORG_PARENT_NORM:
                    continue
            _emit_typed(out, text, t, p["vs"], p["ve"], True, bare_name=bare)
            continue
        # sibling hints: {"type": "AADHAAR", "id": "..."} / {"field": "Birth_Date", "new_value": "..."}
        if kn.replace("_", "") in {k.replace("_", "") for k in _HINT_VALUE_KEYS} or kn in _HINT_VALUE_KEYS:
            hint = None
            for j in range(max(0, i - 3), min(len(pairs), i + 4)):
                q = pairs[j]
                if j == i or not q["str"] or not _same_object(text, p, q):
                    continue
                qk = "_".join(_key_words(q["key"]))
                if qk.replace("_", "") in {k.replace("_", "") for k in _HINT_KEYS}:
                    h = _classify_words(_key_words(text[q["vs"]:q["ve"]]))
                    if h is not None:
                        hint = h
                        break
            if hint is not None:
                _emit_typed(out, text, hint, p["vs"], p["ve"], True)
                continue
        # audit-style {"from": ..., "to": ...}: shape decides
        if kn in _DIFF_KEYS:
            mates = [q for j, q in enumerate(pairs) if j != i and abs(j - i) <= 2 and _same_object(text, p, q)
                     and "_".join(_key_words(q["key"])) in _DIFF_KEYS]
            if mates:
                val = text[p["vs"]:p["ve"]].strip()
                r = _match_value(T.BANK_ACCOUNT, text, p["vs"], p["ve"], True)
                if r is not None and re.fullmatch(r"\d{9,18}", val):
                    out.append((r[0], r[1], T.BANK_ACCOUNT, 0.95, "D1:field_ctx"))
                    continue
                m = _R_COMP.match(val)
                if m and (m.group("pre") or m.group("unit")) and m.end() == len(val):
                    out.append((p["vs"], p["vs"] + len(val), T.COMPENSATION, 0.95, "D1:field_ctx"))
                    continue
        _escalate(out, text, p["vs"], p["ve"], _key_words(p["key"]))


_XML_EL = re.compile(
    r"<(?P<tag>[A-Za-z_][\w.\-]*(?::[\w.\-]+)?)(?P<attrs>(?:\s[^<>]*?)?)>(?P<val>[^<>]{1,400})</(?P=tag)\s*>")
_XML_TYPE_ATTR = re.compile(r"(?:^|[\s:])[\w.\-]*[Tt]ype\s*=\s*[\"']([^\"']*)[\"']")


_XML_TAG = re.compile(r"<(/?)([A-Za-z_][\w.\-]*(?::[\w.\-]+)?)(?:\s[^<>]*?)?(/?)>")
_PARTY_ANC = ("cdtr", "dbtr", "ultmt", "initgpty", "pty", "ownr", "rcpt", "payee", "payer", "beneficiary")


def _xml_ancestors(text: str, pos: int) -> list[str]:
    """Names of the elements still open at `pos` (nearest last), best effort over a bounded look-back."""
    stack: list[str] = []
    for m in _XML_TAG.finditer(text, max(0, pos - 500), pos):
        if m.group(3):
            continue
        if m.group(1):
            if stack and stack[-1] == m.group(2):
                stack.pop()
        else:
            stack.append(m.group(2))
    return stack


def _scan_xml(text: str, out: list) -> None:
    if "</" not in text:
        return
    hint_t, hint_end = None, -1
    for m in _XML_EL.finditer(text):
        tag = m.group("tag")
        words = _key_words(tag)
        t = _classify_words(words)
        val = text[m.start("val"):m.end("val")].strip()
        if t is None:
            for a in _XML_TYPE_ATTR.finditer(m.group("attrs")):
                t = _classify_words(_key_words(a.group(1)))
                if t is not None:
                    break
        if t is None and words and set(words) <= {"id", "value", "number", "identifier", "val", "no"} and hint_t is not None \
                and m.start() - hint_end < 300:
            t = hint_t                                           # <ID>IND-AADHAAR</ID> ... <ID>value</ID>
        if t is None and words == ["nm"]:
            anc = [a.lower() for a in _xml_ancestors(text, m.start())]
            if any(a.startswith(_PARTY_ANC) for a in anc[-3:]):
                t = T.PERSON
        if t is None:
            for a in reversed(_xml_ancestors(text, m.start())[-4:]):
                ta = _classify_words(_key_words(a))
                if ta in (T.BANK_ACCOUNT, T.PHONE, T.EMAIL, T.DATE_OF_BIRTH, T.IFSC, T.PAN, T.AADHAAR, T.CARD, T.UPI_ID, _IDDOC):
                    t = ta
                    break
        if len(val) < 40 and t is None:
            vt = _classify_words(_key_words(val))
            if vt in (T.AADHAAR, T.PAN, _IDDOC, T.BANK_ACCOUNT, T.PHONE, T.EMAIL, T.DATE_OF_BIRTH):
                hint_t, hint_end = vt, m.end()
        if t is None:
            _escalate(out, text, m.start("val"), m.end("val"), words)
            continue
        bare = words == ["name"]
        if bare:
            prev = re.search(r"<(?:[\w.\-]+:)?([\w.\-]+)[^<>]*>\s*$", text[max(0, m.start() - 200):m.start()])
            if prev and re.sub(r"[^a-z0-9]", "", _ctx_norm(prev.group(1))) in _ORG_PARENT_NORM:
                continue
        _emit_typed(out, text, t, m.start("val"), m.end("val"), True, bare_name=bare)


def _split_cells(text: str, s: int, e: int, delim: str) -> list[tuple[int, int]]:
    cells: list[tuple[int, int]] = []
    i = s
    while True:
        j = i
        while j < e and text[j] == " ":
            j += 1
        if j < e and text[j] == '"':
            k = j + 1
            while k < e:
                if text[k] == '"':
                    if k + 1 < e and text[k + 1] == '"':
                        k += 2
                        continue
                    break
                k += 1
            cells.append((j + 1, min(k, e)))
            m = text.find(delim, k, e)
            if m == -1:
                break
            i = m + 1
        else:
            m = text.find(delim, i, e)
            end = e if m == -1 else m
            cells.append((i, end))
            if m == -1:
                break
            i = m + 1
    return cells


_HEADER_CELL = re.compile(r"[A-Za-z][A-Za-z0-9 _./#()&\-]{0,40}")


# Legend tables: "c1 = employee id, c2 = PAN", "col 3: PAN", "column 4 - date of birth"
_LEGEND = re.compile(
    r"(?<![A-Za-z0-9])(?:(?:c|col|column|field|fld|f)[ _\-]?(?P<n1>\d{1,3})|#(?P<n2>\d{1,3}))"
    r"[ \t]*(?:=|:|-|–|→|->|is)[ \t]*(?P<mean>[A-Za-z][A-Za-z0-9 ./_'’\-]{1,40}?)(?=[,;\n)|\]]|[ \t]{2,}|[ \t]+(?:c|col|column|field)[ _\-]?\d|$)",
    re.I)
_COL_LABEL = re.compile(r"(?:c|col|column|field|fld|f)?[ _\-]?(\d{1,3})", re.I)


def _legend(text: str) -> dict:
    """{column number: entity type} from legend lines such as 'c4 = date of birth'."""
    if not re.search(r"(?i)(?:c|col|column|field|fld|f)[ _\-]?\d|#\d", text):
        return {}
    leg = {}
    for m in _LEGEND.finditer(text):
        t = _classify_key(m.group("mean").strip())
        if t is not None:
            leg[int(m.group("n1") or m.group("n2"))] = t
    return leg


def _table_delim(line: str) -> str | None:
    best, cnt = None, 1
    for d in ("\t", "|", ",", ";"):
        c = line.count(d)
        if c > cnt:
            best, cnt = d, c
    return best


def _scan_csv(text: str, out: list, neg: list | None = None) -> None:
    if not any(d in text for d in (",", "\t", "|", ";")):
        return
    legend = None
    lines: list[tuple[int, int]] = []
    pos = 0
    for ln in text.split("\n"):
        lines.append((pos, pos + len(ln)))
        pos += len(ln) + 1
    i = 0
    nl = len(lines)
    while i < nl:
        s, e = lines[i]
        line = text[s:e]
        delim = _table_delim(line)
        if delim is None:
            i += 1
            continue
        if i > 0 and lines[i - 1][1] > lines[i - 1][0] and _table_delim(text[lines[i - 1][0]:lines[i - 1][1]]) == delim \
                and text[lines[i - 1][0]:lines[i - 1][1]].count(delim) == line.count(delim) \
                and any(not is_english(w) for w in re.findall(r"[A-Z][a-z]{2,}", line)):
            i += 1
            continue    # a row inside a table holding a name ("Tushar Wagle,Pune,Manager") is data, not a header
        cells = _split_cells(text, s, e, delim)
        if delim == "|" and cells and not text[cells[0][0]:cells[0][1]].strip():
            cells = cells[1:]                                   # "| a | b |" tables
        if delim == "|" and cells and not text[cells[-1][0]:cells[-1][1]].strip():
            cells = cells[:-1]
        heads = [text[a:b].strip() for a, b in cells]
        if len(cells) < 3 or sum(bool(_HEADER_CELL.fullmatch(h)) for h in heads) < 0.7 * len(heads):
            i += 1
            continue
        col_types = [_classify_key(h) if _HEADER_CELL.fullmatch(h) else None for h in heads]
        if not any(col_types):
            if legend is None:
                legend = _legend(text)
            if legend:
                for k, h in enumerate(heads):
                    lm = _COL_LABEL.fullmatch(h)
                    if lm and int(lm.group(1)) in legend:
                        col_types[k] = legend[int(lm.group(1))]
            if not any(col_types):
                i += 1
                continue
        n = len(cells)
        j = i + 1
        matched = 0
        while j < nl:
            rs, re_ = lines[j]
            if not text[rs:re_].strip():
                break
            if _in_header_line(text, rs):
                j += 1
                continue                              # a repeated header row is not data
            rcells = _split_cells(text, rs, re_, delim)
            if delim == "|" and len(rcells) == n + 2 and not text[rcells[0][0]:rcells[0][1]].strip():
                rcells = rcells[1:-1]
            if len(rcells) == n:
                matched += 1
                for (a, b), t, h in zip(rcells, col_types, heads):
                    if t is not None and b > a:
                        _emit_typed(out, text, t, a, b, True, bare_name=(_key_words(h) == ["name"]))
                    elif t is None and b > a:
                        kw = _key_words(h)
                        if neg is not None and set(kw) & _ESC_NEG:
                            neg.append((a, b))            # reference / order / invoice column: digits here are not PII
                        _escalate(out, text, a, b, kw)
            j += 1
        i = j if matched else i + 1                   # a prose line that only looked like a header must not eat the table

# ---- flattened CSV (D0 turns every newline into a space, so rows are only separated by " ") ----------
_TOKEN = re.compile(r'"(?:[^"]|"")*"\S*|\S+')
_HDR_ALPHA = re.compile(r"[A-Za-z][A-Za-z _./#()&\-]{0,40}")


def _comma_chunks(text: str) -> list[tuple[int, int]] | None:
    """Quote-aware comma split. None when quotes are unbalanced (not CSV-shaped)."""
    chunks, start, inq = [], 0, False
    for i, ch in enumerate(text):
        if ch == '"':
            inq = not inq
        elif ch == "," and not inq:
            chunks.append((start, i))
            start = i + 1
    if inq:
        return None
    chunks.append((start, len(text)))
    return chunks


def _suffix_class(words_text: str):
    toks = words_text.split()
    for n in (3, 2, 1):
        if len(toks) >= n:
            t = _classify_key(" ".join(toks[-n:]))
            if t is not None:
                return t
    return None


def _prefix_class(words_text: str):
    toks = words_text.split()
    for n in (1, 2, 3):
        if len(toks) >= n:
            t = _classify_key(" ".join(toks[:n]))
            if t is not None:
                return t
    return None


_HDR_TAIL = {"no", "no.", "number", "num", "id", "name", "address", "code", "date", "ctc", "pay", "salary", "amount", "credit",
             "limit", "ifsc", "account", "a/c", "email", "e-mail", "phone", "mobile", "joining", "birth", "of", "manager",
             "supervisor", "holder", "annual", "gross", "net", "fixed", "type", "status", "location", "department"}


def _header_tail_len(words: list[str]) -> int:
    """How many leading tokens of an all-alphabetic merged chunk belong to the header cell."""
    n = 1
    while n < min(len(words) - 1, 4) and words[n].lower() in _HDR_TAIL:
        n += 1
    return n


def _emit_cell(out: list, text: str, t, a: int, b: int, bare: bool) -> None:
    """One whole cell [a, b) (quotes stripped if present)."""
    while a < b and text[a] in " \t":
        a += 1
    while b > a and text[b - 1] in " \t":
        b -= 1
    if b - a >= 2 and text[a] == '"' and text[b - 1] == '"':
        a, b = a + 1, b - 1
    if t is not None and b > a:
        _emit_typed(out, text, t, a, b, True, bare_name=bare)


def _emit_prefix_cell(out: list, text: str, t, a: int, b: int) -> None:
    """A cell that starts at `a` and is followed by unrelated text (the next row) inside [a, b)."""
    if text[a:a + 1] == '"':
        close = text.find('"', a + 1, b)
        if close != -1:
            _emit_typed(out, text, t, a + 1, close, True)
        return
    _emit_typed(out, text, t, a, b, False)


_STRONG = frozenset({_IDDOC, T.EMAIL, T.PHONE, T.AADHAAR, T.PAN, T.IFSC, T.BANK_ACCOUNT, T.CARD, T.DATE_OF_BIRTH, T.UPI_ID,
                     T.EMPLOYEE_ID, T.COMPENSATION})


def _flat_rows(text: str, chunks: list, ctext: list, i0: int, n: int, max_rows: int | None = None):
    """Type the cells of a flattened table whose header is chunks[i0:i0+n] (n columns).

    Returns (detections, ok, bad): `ok`/`bad` count strong-typed cells that did / did not validate,
    which is how the caller decides between candidate column counts.
    """
    K = len(chunks)
    per = n - 1
    M0 = i0 + n - 1
    heads = [c.strip() for c in ctext[i0:i0 + n - 1]]
    mtoks = [(chunks[M0][0] + m.start(), chunks[M0][0] + m.end()) for m in _TOKEN.finditer(text[chunks[M0][0]:chunks[M0][1]])]
    if len(mtoks) < 1:
        return [], 0, 0
    words = [text[a:b] for a, b in mtoks]
    lead = 0
    while lead < len(words) and _HDR_ALPHA.fullmatch(words[lead]):
        lead += 1
    if 0 < lead < len(words):
        t_hdr = lead                                       # "Location 12/03/2026": data starts at the first non-word
    else:
        t_hdr = _header_tail_len(words) if len(words) > 1 else 1
    types: list = [_suffix_class(heads[0])] + [_classify_key(h) for h in heads[1:]]
    types.append(_prefix_class(" ".join(words[:t_hdr])))
    if not any(types):
        return [], 0, 0
    out: list = []
    ok = bad = 0

    def count(t, probe):
        nonlocal ok, bad
        if t in _STRONG:
            if probe:
                ok += 1
            else:
                bad += 1

    end_k = K if max_rows is None else min(K, M0 + per * max_rows + 1)
    merged: list[tuple[int, list]] = []
    for k in range(M0, end_k):
        rel = k - M0
        a, b = chunks[k]
        if rel % per != 0:
            col = rel % per
            probe: list = []
            _emit_cell(probe, text, types[col], a, b, _key_words(heads[col]) == ["name"])
            count(types[col], probe)
            out.extend(probe)
            if types[col] is None:
                _escalate(out, text, a, b, _key_words(heads[col]))
            continue
        tk = [(a + m.start(), a + m.end()) for m in _TOKEN.finditer(text[a:b])]
        if k == K - 1 and rel > 0:                           # tail: last col of the final row (+ trailing prose)
            if types[-1] not in (None, T.PERSON) and tk:
                probe = []
                _emit_prefix_cell(probe, text, types[-1], tk[0][0], b)
                count(types[-1], probe)
                out.extend(probe)
            continue
        merged.append((k, tk))
    t0, a_last = types[0], types[-1]
    bare0 = _key_words(heads[0]) == ["name"]
    later = [tk for k, tk in merged if k != M0]
    tail = None
    ka, kb = chunks[K - 1]
    if K - 1 > M0 and (K - 1 - M0) % per == 0:
        tail = [(ka + m.start(), ka + m.end()) for m in _TOKEN.finditer(text[ka:kb])]
    samples = later + ([tail] if tail and len(tail) <= 4 else [])
    common = 0
    if len(samples) >= 3:
        while all(len(tk) > common for tk in samples) and \
                len({text[tk[common][0]:tk[common][1]] for tk in samples}) == 1:
            common += 1
        if tail and len(tail) <= 4 and common >= len(tail):
            common = len(tail)
    prefixes: dict[tuple, int] = {}
    for tk in samples:
        ws = tuple(text[a:b] for a, b in tk)
        for n_ in range(1, len(ws) + 1):
            prefixes[ws[:n_]] = prefixes.get(ws[:n_], 0) + 1
    for k, tk in merged:
        if not tk:
            continue
        if k == M0:
            t_lo, t_fix = t_hdr, (t_hdr if 0 < lead < len(words) else None)
        else:
            t_lo, t_fix = 0, (common if common else None)
            if t_fix is None and len(samples) >= 3:
                ws = tuple(text[a:b] for a, b in tk)
                rep_len = max((n_ for n_ in range(1, len(ws)) if prefixes.get(ws[:n_], 0) >= 2), default=0)
                t_fix = rep_len or None
            if a_last not in (None, T.PERSON):               # previous row's last cell (typed, prefix-validated)
                probe = []
                _emit_prefix_cell(probe, text, a_last, tk[0][0], tk[-1][1])
                count(a_last, probe)
                out.extend(probe)
                if probe:                                    # the next row's first cell starts after it
                    nxt = [i for i, (a_, b_) in enumerate(tk) if a_ >= probe[-1][1]]
                    t_fix = nxt[0] if nxt else None
                    t_lo = t_fix or 0
        if t0 is None:
            continue
        if k != M0 and a_last is T.PERSON and t0 is T.PERSON and t_fix is None:
            # two names glued together: prefer a wider gap (CRLF), else split down the middle
            best = None
            for t in range(1, len(tk)):
                pa, pb = [], []
                _emit_cell(pa, text, T.PERSON, tk[0][0], tk[t - 1][1], False)
                _emit_cell(pb, text, T.PERSON, tk[t][0], tk[-1][1], bare0)
                if not pa or not pb:
                    continue
                score = (tk[t][0] - tk[t - 1][1] >= 2, -abs(len(tk) / 2 - t))
                if best is None or score > best[0]:
                    best = (score, pa, pb)
            if best:
                out.extend(best[1] + best[2])
            continue
        cands = [t_fix] if t_fix is not None else list(range(t_lo, len(tk)))
        found: list = []
        for t in cands:
            if t >= len(tk):
                continue
            _emit_cell(found, text, t0, tk[t][0], tk[-1][1], bare0)
            if found:
                break
        out.extend(found)
        count(t0, found)
    return out, ok, bad


def _scan_csv_flat(text: str, out: list) -> None:
    if text.count(",") < 6 or text.count("\n") >= 3:
        return
    chunks = _comma_chunks(text)
    if not chunks or len(chunks) < 6:
        return
    K = len(chunks)
    ctext = [text[a:b] for a, b in chunks]
    hl = [bool(_HDR_ALPHA.fullmatch(c.strip())) for c in ctext]
    i0 = 0
    while i0 < K - 3:
        if not hl[i0]:
            i0 += 1
            continue
        j = i0
        while j < K and hl[j]:
            j += 1
        if j - i0 < 2 or j >= K - 1:
            i0 = max(j, i0 + 1)
            continue
        first = ctext[i0].split()
        if len(first) > 4:                                   # prose glued to the first header cell
            ctext[i0] = " ".join(first[-3:])
        best = None
        for n in range(3, min(j - i0 + 1, 20) + 1):
            if i0 + n - 1 >= K:
                break
            _, ok, bad = _flat_rows(text, chunks, ctext, i0, n, max_rows=4)
            score = ok - 2 * bad
            if ok and (best is None or score > best[0]):
                best = (score, n)
        if best is None:
            i0 = j
            continue
        res, _, _ = _flat_rows(text, chunks, ctext, i0, best[1])
        out.extend(res)
        return


_SQL_INSERT = re.compile(
    r"INSERT[ \t\r\n]+(?:OR[ \t]+\w+[ \t]+)?INTO[ \t\r\n]+[\w.`\"\[\]]+[ \t\r\n]*\((?P<cols>[^()]*)\)[ \t\r\n]*VALUES[ \t\r\n]*"
    r"(?P<vals>\((?:'(?:[^']|'')*'|[^()'])*\)(?:[ \t\r\n]*,[ \t\r\n]*\((?:'(?:[^']|'')*'|[^()'])*\))*)", re.I)
_SQL_TUPLE = re.compile(r"\((?:'(?:[^']|'')*'|[^()'])*\)")
_SQL_VALUE = re.compile(r"[ \t\r\n]*(?:'(?P<q>(?:[^']|'')*)'|(?P<n>[^,)'\s][^,)]*?))[ \t\r\n]*(?=,|\)$)")
_DUP_ENTRY = re.compile(r"Duplicate entry '(?P<v>[^']+)' for key '(?P<k>[^']+)'", re.I)


def _scan_sql(text: str, out: list) -> None:
    if "into" not in text.lower() and "Duplicate" not in text:
        return
    for m in _SQL_INSERT.finditer(text):
        cols = [c.strip().strip('`"[]') for c in m.group("cols").split(",")]
        types = [_classify_key(c) for c in cols]
        if not any(types):
            continue
        for tm in _SQL_TUPLE.finditer(text, m.start("vals"), m.end("vals")):
            body_s, body_e = tm.start() + 1, tm.end() - 1
            pos, k = body_s, 0
            # split on commas outside quotes
            cells, cur, inq = [], body_s, False
            for idx in range(body_s, body_e):
                ch = text[idx]
                if ch == "'":
                    inq = not inq
                elif ch == "," and not inq:
                    cells.append((cur, idx))
                    cur = idx + 1
            cells.append((cur, body_e))
            if len(cells) != len(cols):
                continue
            for (a, b), t in zip(cells, types):
                if t is None:
                    continue
                while a < b and text[a] in " \t\r\n":
                    a += 1
                while b > a and text[b - 1] in " \t\r\n":
                    b -= 1
                if b - a >= 2 and text[a] == "'" and text[b - 1] == "'":
                    a, b = a + 1, b - 1
                if b > a:
                    _emit_typed(out, text, t, a, b, True)
    for m in _DUP_ENTRY.finditer(text):
        t = _classify_key(m.group("k"))
        if t is not None:
            _emit_typed(out, text, t, m.start("v"), m.end("v"), True)


# ---- labelled person names in prose (high-precision structural cues)
# A name token is capped at 64 chars ({0,63} after the capital). Unbounded `*` rescanned a long [A-Za-z'-] run from every
# capital in it (O(n^2) on 100 KB of "A" or the title-cased view of "-p-p-p..."). No real name token is near 64 chars.
_PT = r"[A-Z][A-Za-z'’\-]{0,63}\.?"
_PNAME = _PT + r"(?:[ \t]+" + _PT + r"){0,3}"
_PERSON_CUES = [
    ("angle_email", re.compile(r"(?P<n>" + _PNAME + r")[\"']?[ \t]*<[^<>\s@]+@[^<>\s]+>")),
    ("wrote", re.compile(r"(?m)^[ >\t]*(?P<n>" + _PNAME + r")[ \t]+wrote:")),
    ("role", re.compile(
        r"(?i:chaired[ \t]+by|approved[ \t]+by|signed[ \t]+off[ \t]+by|reporting[ \t]+manager|vendor[ \t]+contact|"
        r"point[ \t]+of[ \t]+contact|contact[ \t]+person|spoke[ \t]+(?:to|with)|referred[ \t]+by|nominated[ \t]+by|"
        r"escalated[ \t]+to|handover[ \t]+to|attn|attention)[ \t]*[:\-]?[ \t]+(?P<n>" + _PNAME + r")")),
    ("dependent", re.compile(
        r"(?m)^[ \t]*\d+[.)][ \t]+(?P<n>" + _PNAME + r")[ \t]*\((?i:spouse|child|son|daughter|father|mother|dependent|parent|"
        r"sibling|brother|sister|partner|wife|husband)\)")),
    ("numbered_profile", re.compile(r"(?m)^[ \t]*\d+[.)][ \t]+(?P<n>" + _PT + r"[ \t]+" + _PNAME + r")[ \t]+[-–—][ \t]+\d")),
    ("before_dob", re.compile(r"(?P<n>" + _PNAME + r")[ \t]*\((?i:dob|d\.o\.b\.?|born|date of birth)\b")),
    ("possessive", re.compile(
        r"(?P<n>" + _PT + r")['’]s[ \t]+(?i:personal|official|work|home|new|old|current|primary)?[ \t]*"
        r"(?i:mobile|phone|number|email|e-mail|aadhaar|aadhar|pan|dob|salary|ctc|bank|account|a/c|ifsc|upi|address|passport)\b")),
    ("before_empid", re.compile(
        r"(?P<n>" + _PNAME + r")[ \t]*[\[(][ \t]*(?:EMP[-_]?\d{4,8}|E\d{6}|WD-\d{6,8}|[A-Z]{2}/\d{2}/\d{3,5})[ \t]*[\])]")),
]


def _person_cues(text: str) -> list[Cand]:
    out: list[Cand] = []
    if len(text) > 200_000:
        return out
    for name, rx in _PERSON_CUES:
        for m in rx.finditer(text):
            n = m.group("n").strip().rstrip(".")
            if n.lower() in _NOT_NAMES or len(n) < 3 or n.split()[0].lower() in _CUE_NOT_FIRST \
                    or (name == "possessive" and n.lower() in _ROLE_NOUNS):
                continue
            words = n.split()
            while len(words) > 1 and ends_name(words[-1]):
                words.pop()              # "Approved by Leena Fernandes Head of ...": "Head" is not part of the name
            n = n[:n.rfind(words[-1]) + len(words[-1])] if len(words) < len(n.split()) else n
            out.append((m.start("n"), m.start("n") + len(n), T.PERSON, 0.85, "D2:person_" + name))
    return out


_ROLE_NOUNS = {"referee", "candidate", "employee", "manager", "employer", "company", "customer", "client", "applicant", "member",
               "user", "vendor", "recruiter", "team", "department", "payroll", "hrbp", "subject", "sender", "recipient", "owner",
               "landlord", "tenant", "nominee", "contact", "supervisor", "approver", "reviewer", "interviewer", "hr"}
_CUE_NOT_FIRST = {"the", "this", "that", "please", "dear", "hi", "hello", "regards", "thanks", "team", "from", "to", "cc", "subject",
                  "re", "fwd", "sent", "date", "on", "at", "in", "and", "or", "for", "with", "ceo", "cfo", "hr", "it"}


_KV_SEP = re.compile(r"[ \t]{0,3}(?:::|[:=])[ \t]{0,6}")
_KV_KEY_TAIL = re.compile(r"((?:[A-Za-z][\w\-/.'’]*[ \t]+){0,2}[A-Za-z][\w\-/.'’]*)$")
_SNAKE_KEY = re.compile(r"(?<![\w])(?P<key>[A-Za-z]+(?:_[A-Za-z]+)+)[ \t]+(?P<q>['\"]?)")


def _scan_kv(text: str, out: list) -> None:
    """YAML / INI / env / 'Key: value' / 'key=value' in free text and logs."""
    for m in _KV_SEP.finditer(text):
        a = m.start()
        if a > 0 and text[a - 1] in "\"'\\":
            continue                      # JSON-ish: handled by the pair scanner
        if text[m.end():m.end() + 2] == "//":
            continue                      # https://
        k = _KV_KEY_TAIL.search(text[max(0, a - 70):a])
        if not k:
            continue
        toks = k.group(1).split()
        while len(toks) > 1 and not re.fullmatch(r"[A-Za-z][A-Za-z'’/.\-_]{0,24}", toks[-2]):
            toks = toks[-1:]                 # the words before the key are someone's value, not part of the key
        t = None
        for n in (3, 2, 1):
            if len(toks) >= n:
                t = _classify_key(" ".join(toks[-n:]))
                if t is not None:
                    key_words = _key_words(" ".join(toks[-n:]))
                    if key_words == ["name"] and len(toks) > n:
                        prev = _key_words(re.sub(r"['’]s$", "", toks[-n - 1]))
                        if prev and not set(prev) <= _NAME_PREFIX:
                            t = None      # "Bank Name", "Department Name", "File Name": not a person
                    break
        vs = m.end()
        le = text.find("\n", vs)
        le = len(text) if le == -1 else le
        if t is _WEAKSECRET and "=" in m.group() and not toks[-1].isupper():
            continue                       # log-style "session=abc": only env-style KEYS (UPPER_SNAKE=) count
        if t is None:
            if not toks:
                continue
            _escalate(out, text, vs, le, _key_words(" ".join(toks[-2:])))
            continue
        if not text[vs:le].strip():
            # a label line whose value sits on a later line of the same block ("Aadhaar:\n\n   2341 ...")
            q, nls = vs, 0
            while q < len(text) and text[q] in " \t\r\n":
                nls += text[q] == "\n"
                q += 1
            if q < len(text) and nls <= 4 and t in _STRONG:
                le2 = text.find("\n", q)
                le2 = len(text) if le2 == -1 else le2
                if not re.match(r"[A-Za-z][\w ./'’\-]{0,40}[ \t]*[:=]", text[q:le2]):
                    _emit_typed(out, text, t, q, le2, False, bare_name=(key_words == ["name"] or (t is T.PERSON and key_words[-1] not in ("name", "names"))))
            continue
        if text[vs:vs + 1] in (" ", "\t"):
            continue
        if vs < le and text[vs] in "\"'":
            q = text[vs]
            close = text.find(q, vs + 1, le)
            if close != -1:
                _emit_typed(out, text, t, vs + 1, close, True, bare_name=(key_words == ["name"] or (t is T.PERSON and key_words[-1] not in ("name", "names"))))
            continue
        _emit_typed(out, text, t, vs, le, False, bare_name=(key_words == ["name"] or (t is T.PERSON and key_words[-1] not in ("name", "names"))))
    # Snake_Case identifiers followed directly by the value: "Date_of_Birth 01 Nov 1996", "Bank_Account_Number '123...'"
    for m in _SNAKE_KEY.finditer(text):
        t = _classify_key(m.group("key"))
        if t is None or t is T.PERSON:
            continue
        vs = m.end()
        le = text.find("\n", vs)
        le = len(text) if le == -1 else le
        if m.group("q"):
            close = text.find(m.group("q"), vs, le)
            if close != -1:
                _emit_typed(out, text, t, vs, close, True)
            continue
        _emit_typed(out, text, t, vs, le, False)


def detect_field_context(text: str) -> list[Detection]:
    """Values found by the field name attached to them (JSON-ish, XML, key: value, CSV/TSV).

    Every hit is type-validated against its field (Verhoeff, digit counts, date parse, ...)
    and scored 0.95, source "D1:field_ctx".
    """
    if not text:
        return []
    return _resolve(_field_scan(text)[0])


def _field_scan(text: str) -> tuple[list[Cand], list[tuple[int, int]]]:
    cands: list[Cand] = []
    neg: list[tuple[int, int]] = []
    _scan_json(text, cands)
    _scan_xml(text, cands)
    _scan_csv(text, cands, neg)
    _scan_csv_flat(text, cands)
    _scan_kv(text, cands)
    _scan_sql(text, cands)
    cands.extend(_person_cues(text))
    return cands, neg


# --------------------------------------------------------------------------
# Fusion
# --------------------------------------------------------------------------
def _resolve(cands: list[Cand]) -> list[Detection]:
    """Greedy by (score, length): O(n log n) - accepted spans stay disjoint, so only the two neighbours matter."""
    order = sorted(cands, key=lambda c: (-c[3], -(c[1] - c[0]), c[0]))
    starts: list[int] = []
    ends: list[int] = []
    taken: list[Cand] = []
    for c in order:
        if c[1] <= c[0]:
            continue
        i = bisect.bisect_right(starts, c[0])
        if i > 0 and ends[i - 1] > c[0]:
            continue
        if i < len(starts) and starts[i] < c[1]:
            continue
        starts.insert(i, c[0])
        ends.insert(i, c[1])
        taken.append(c)
    taken.sort(key=lambda c: (c[0], c[1]))
    return [Detection(s, e, ty, sc, src) for s, e, ty, sc, src in taken]


class _SpanIndex:
    """Any-overlap queries against a set of (possibly overlapping) spans in O(log n)."""

    def __init__(self, spans: list[Cand]):
        srt = sorted(spans, key=lambda c: c[0])
        self._starts = [c[0] for c in srt]
        self._max_end: list[int] = []
        m = -1
        for c in srt:
            m = max(m, c[1])
            self._max_end.append(m)

    def overlaps(self, c: Cand) -> bool:
        i = bisect.bisect_left(self._starts, c[1])          # spans starting before c ends
        return i > 0 and self._max_end[i - 1] > c[0]


def _overlaps(c: Cand, others: list[Cand]) -> bool:
    return any(c[0] < o[1] and o[0] < c[1] for o in others)


_REF_SENSITIVE = frozenset({T.PHONE, T.BANK_ACCOUNT, T.CARD, T.AADHAAR, T.COMPENSATION})


def detect_d2(text: str) -> list[Detection]:
    """Deterministic detections: non-overlapping, sorted by start."""
    if not text:
        return []
    fc_cands, neg_spans = _field_scan(text)
    if not _ablation.get().D1:   # ablation: drop D1 candidates only; CSV negative-span suppression of D2 is kept
        fc_cands = []
    field_ctx = [(d.start, d.end, d.type, d.score, d.source) for d in _resolve(fc_cands)]
    neg_ix = _SpanIndex([(a, b, None, 0, "") for a, b in neg_spans]) if neg_spans else None
    cards = _cards(text)
    aadhaar = _aadhaar(text)
    bank_ctx = _bank(text)
    card_ix, aad_ix = _SpanIndex(cards), _SpanIndex(aadhaar)
    bank = [b for b in bank_ctx if not card_ix.overlaps(b) and not aad_ix.overlaps(b)]
    # An explicit account label beats a Luhn-valid contiguous digit run (random 15/16-digit
    # account numbers pass Luhn ~10 % of the time); grouped 4-4-4-4 stays a card.
    lab_ix = _SpanIndex(bank_ctx + [c for c in field_ctx if c[2] is T.BANK_ACCOUNT])
    cards = [c for c in cards if not (text[c[0]:c[1]].isdigit() and lab_ix.overlaps(c))]
    card_ix = _SpanIndex(cards)
    kept = set(bank)
    bank += [b for b in bank_ctx if b not in kept and not card_ix.overlaps(b) and not aad_ix.overlaps(b)]
    phones = _phone(text)
    bank_spans = {(b[0], b[1]) for b in bank + [c for c in field_ctx if c[2] is T.BANK_ACCOUNT]}
    phones = [p for p in phones if (p[0], p[1]) not in bank_spans]
    cands: list[Cand] = []
    hdr = _header_spans(text)
    for part in () if not _ablation.get().D2 else (
        _email(text), _upi(text), phones, aadhaar, _pan(text), _ifsc(text), cards, bank,
        _secret(text), _employee_id(text), _compensation(text), _amount_shapes(text), _personal_amounts(text), _dob(text),
        _bank_file_names(text), _iban(text), addresses(text),
    ):
        # header cells of a CSV/TSV are field names, never values
        cands.extend(c for c in part if not (hdr and any(a <= c[0] < b for a, b in hdr))
                     and not (neg_ix and c[2] in _REF_SENSITIVE and neg_ix.overlaps(c)))
    cands.extend(field_ctx)
    return _resolve(cands)


# --------------------------------------------------------------------------
# D1: field names
# --------------------------------------------------------------------------
_FIELD_TABLE: dict[str, T] = {}


def _reg(t: T, *names: str) -> None:
    for n in names:
        _FIELD_TABLE[n] = t


_reg(T.PERSON, "name", "full_name", "legal_name", "first_name", "last_name", "preferred_name", "worker_name",
     "employee_name", "manager_name", "middle_name", "given_name", "family_name", "surname", "display_name")
_reg(T.EMAIL, "email", "work_email", "personal_email", "email_address", "e_mail", "official_email")
_reg(T.PHONE, "phone", "mobile", "phone_number", "mobile_number", "phone_no", "mobile_no", "contact_number",
     "cell", "work_phone", "personal_phone", "telephone")
_reg(T.EMPLOYEE_ID, "employee_id", "worker_id", "emp_id", "employee_number", "employee_no", "emp_no", "staff_id")
_reg(T.COMPENSATION, "salary", "base_pay", "ctc", "annual_salary", "compensation", "gross_pay", "net_pay", "bonus",
     "annual_ctc", "monthly_salary", "base_salary", "gross_salary", "net_salary", "basic_pay", "basic_salary",
     "variable_pay", "stipend")
_reg(T.DATE_OF_BIRTH, "dob", "date_of_birth", "birth_date", "birthdate", "d_o_b")
_reg(T.AADHAAR, "aadhaar", "aadhaar_number", "aadhar", "aadhar_number", "aadhaar_no", "uid")
_reg(T.PAN, "pan", "pan_number", "pan_no", "pan_card")
_reg(T.BANK_ACCOUNT, "bank_account", "account_number", "account_no", "bank_account_number", "bank_account_no")
_reg(T.IFSC, "ifsc", "ifsc_code")
_reg(T.UPI_ID, "upi", "upi_id", "vpa")
_reg(T.CARD, "card_number", "credit_card", "debit_card", "credit_card_number", "debit_card_number")
_reg(T.SECRET, "password", "passwd", "api_key", "apikey", "secret", "client_secret", "secret_key", "access_token",
     "auth_token", "private_key")

_ORG_PARENTS = {"company", "organization", "org", "department", "cost_center", "location", "job_profile"}


def _norm_key(key: Any) -> str:
    s = str(key)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return s


def _is_leaf_value(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, str):
        return bool(v.strip())
    return isinstance(v, (int, float))


def detect_fields(obj: Any) -> list[tuple[tuple, T]]:
    """D1: (path, EntityType) for leaf values under known sensitive keys.

    Scalars inside a list inherit the type of the key that holds the list.
    """
    out: list[tuple[tuple, T]] = []

    def walk(o: Any, path: tuple, inherited: T | None, parent_key: str | None) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                nk = _norm_key(k)
                t = _FIELD_TABLE.get(nk)
                if t is T.PERSON and nk == "name" and parent_key in _ORG_PARENTS:
                    t = None
                p = path + (k,)
                if isinstance(v, (dict, list)):
                    walk(v, p, t, nk)
                elif t is not None and _is_leaf_value(v):
                    out.append((p, t))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                p = path + (i,)
                if isinstance(v, (dict, list)):
                    walk(v, p, inherited, parent_key)
                elif inherited is not None and _is_leaf_value(v):
                    out.append((p, inherited))

    walk(obj, (), None, None)
    return out
