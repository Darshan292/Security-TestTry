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
"""
from __future__ import annotations

import re
from typing import Any

from pgw.types import Detection, EntityType as T

__all__ = ["detect_d2", "detect_fields", "verhoeff_valid", "verhoeff_check_digit", "luhn_valid"]

_NA = r"(?<![A-Za-z0-9])"      # not inside a longer alnum token (left)
_NZ = r"(?![A-Za-z0-9])"       # not inside a longer alnum token (right)

Cand = tuple[int, int, T, float, str]


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


def _email(text: str) -> list[Cand]:
    return [(m.start(), m.end(), T.EMAIL, 0.99, "D2:email") for m in _EMAIL.finditer(text)]


def _upi(text: str) -> list[Cand]:
    return [(m.start(), m.end(), T.UPI_ID, 0.95, "D2:upi") for m in _UPI.finditer(text)]


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


def _phone(text: str) -> list[Cand]:
    out: list[Cand] = [(m.start(), m.end(), T.PHONE, 0.9, "D2:phone_in") for m in _PHONE_IN.finditer(text)]
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
_AADHAAR_KW = re.compile(r"(?<![A-Za-z])(?:aadhaar|aadhar|uidai|uid)(?![A-Za-z])|आधार", re.I)


def _aadhaar(text: str) -> list[Cand]:
    out: list[Cand] = []
    for rx, grouped in ((_AADHAAR_GROUPED, True), (_AADHAAR_CONTIG, False)):
        for m in rx.finditer(text):
            digits = re.sub(r"\D", "", m.group())
            if not verhoeff_valid(digits):
                continue
            ctx = bool(_AADHAAR_KW.search(text[max(0, m.start() - 40):m.start()]))
            if not (grouped or ctx):
                continue
            out.append((m.start(), m.end(), T.AADHAAR, 0.95 if (grouped and ctx) else 0.85, "D2:aadhaar"))
    return out


# --------------------------------------------------------------------------
# PAN / IFSC
# --------------------------------------------------------------------------
_PAN = re.compile(_NA + r"[A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]" + _NZ)
_IFSC = re.compile(_NA + r"[A-Z]{4}0[A-Z0-9]{6}" + _NZ)


def _pan(text: str) -> list[Cand]:
    return [(m.start(), m.end(), T.PAN, 0.95, "D2:pan") for m in _PAN.finditer(text)]


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
                if _iin_ok(d) and luhn_valid(d):
                    out.append((s, e, T.CARD, 0.95, "D2:card"))
    return out


_BANK_KW = re.compile(
    _NA + r"(?:bank[ \t]+account(?:[ \t]+(?:number|num|no\.?))?"
    r"|beneficiary[ \t]+account(?:[ \t]+(?:number|num|no\.?))?"
    r"|account[ \t]*(?:number|num\.?|no\.?|#)"
    r"|a/c(?:[ \t]*(?:number|no\.?))?"
    r"|acct(?:[ \t]*(?:number|no\.?))?)(?![A-Za-z])",
    re.I,
)


def _bank(text: str) -> list[Cand]:
    out: list[Cand] = []
    for kw in _BANK_KW.finditer(text):
        for run in _RUN.finditer(text, kw.end()):
            if run.start() > kw.end() + 30:
                break
            gs = _groups(run)
            first = gs[0][1] - gs[0][0]
            if 9 <= first <= 18:
                out.append((gs[0][0], gs[0][1], T.BANK_ACCOUNT, 0.85, "D2:bank_account"))
                continue
            if len(gs) < 2:
                continue
            total, last = 0, -1
            for k, (s, e) in enumerate(gs):
                if e - s < 3 or total + (e - s) > 18:
                    break
                total += e - s
                last = k
            if last >= 1 and total >= 9:
                out.append((gs[0][0], gs[last][1], T.BANK_ACCOUNT, 0.85, "D2:bank_account"))
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
    ("jwt", re.compile(r"(?<![A-Za-z0-9_\-])eyJ[\w\-]+\.eyJ[\w\-]+\.[\w\-]+")),
    ("pem", re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?"
        r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|\Z)"
    )),
]
_CRED = r"[A-Za-z0-9._~+/=\-]+"
_AUTH = re.compile(
    r"(?<![A-Za-z0-9])authorization[\\\"']*[ \t]*[:=][ \t]*[\\\"']*(?:bearer|basic)[ \t]+(?P<v>" + _CRED + r")", re.I
)
_BEARER = re.compile(_NA + r"bearer[ \t]+(?P<v>[A-Za-z0-9._~+/=\-]{20,})", re.I)
_KV = re.compile(
    r"(?-i:(?<![A-Za-z0-9])|(?<=[a-z])(?=[A-Z]))"
    r"(?:password|passwd|pwd|secret|api[_\-]?key|apikey|client[_\-]?secret)(?:[_\-][A-Za-z0-9_\-]{0,20})?"
    r"[\\\"']*[ \t]*[:=][ \t]*"
    r"(?:\\?\"(?P<dq>[^\"\\\r\n]+)\\?\"|'(?P<sq>[^'\r\n]+)'|(?P<raw>[^\s\"',;]+))",
    re.I,
)
_KV_SKIP = {"true", "false", "null", "none", "undefined"}


def _secret(text: str) -> list[Cand]:
    out: list[Cand] = []
    for name, rx in _SECRET_SIMPLE:
        for m in rx.finditer(text):
            out.append((m.start(), m.end(), T.SECRET, 0.99, f"D2:secret_{name}"))
    for rx in (_AUTH, _BEARER):
        for m in rx.finditer(text):
            v = m.group("v")
            if rx is _BEARER and not any(c.isdigit() for c in v):
                continue
            out.append((m.start("v"), m.end("v"), T.SECRET, 0.99, "D2:secret_auth"))
    for m in _KV.finditer(text):
        g = next(k for k in ("dq", "sq", "raw") if m.group(k) is not None)
        v = m.group(g)
        s, e = m.start(g), m.end(g)
        if g == "raw":
            stripped = v.rstrip("})]")
            e -= len(v) - len(stripped)
            v = stripped
        if not v or v.strip().lower() in _KV_SKIP:
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
]
_EMPID_KW = re.compile(
    _NA + r"(?:employee[\s_\-]*(?:id|no\.?|number|num)|emp[\s_\-]*(?:id|no\.?)|worker[\s_\-]*id|staff[\s_\-]*id|emp\s*#)(?![A-Za-z])",
    re.I,
)
_EMPID_DIRECT = re.compile(r"[\s:=#.\-\"'()\[\]\\]*(?P<t>[A-Za-z0-9]{4,10})" + _NZ)
_EMPID_TOK = re.compile(_NA + r"[A-Za-z0-9]{4,10}" + _NZ)


def _employee_id(text: str) -> list[Cand]:
    out: list[Cand] = []
    for rx in _EMPID_FMT:
        for m in rx.finditer(text):
            out.append((m.start(), m.end(), T.EMPLOYEE_ID, 0.9, "D2:employee_id"))
    for kw in _EMPID_KW.finditer(text):
        lim = kw.end() + 25
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
# COMPENSATION
# --------------------------------------------------------------------------
_NUM = r"\d+(?:,\d{2,3})*(?:\.\d+)?"
_COMP_MARKED = re.compile(
    r"(?:(?P<pre>(?:₹|(?<![A-Za-z0-9])(?i:rs\.?|inr))[ \t]*)|(?<![A-Za-z0-9])(?<!\d[.,]))"
    r"(?P<num>" + _NUM + r")"
    r"(?P<unit>[ \t]*(?:(?i:lakhs?|lacs?|crores?|crs?|lpa|inr)|k|K|L))?"
    r"(?:/-)?" + _NZ
)
_COMP_KW = re.compile(
    _NA + r"(?:salary|salaries|ctc|compensation|pay|basic|gross|net[ \t]+pay|bonus|increment|revision|stipend|hike)(?![A-Za-z])",
    re.I,
)
_COMP_BARE = re.compile(r"(?<![A-Za-z0-9_])(?<!\d[,./\-])" + _NUM + r"(?![A-Za-z0-9_%])(?![/\-]\d)(?!\.\d)")
_YEAR_PRE = re.compile(
    r"(?:\b(?:in|since|effective|from|year|fy|for|during|dated|on|of|by|until|till|wef|w\.e\.f\.?)|[Ff][Yy])[\s:\-]*$", re.I
)


def _compensation(text: str) -> list[Cand]:
    out: list[Cand] = []
    for m in _COMP_MARKED.finditer(text):
        pre, unit = m.group("pre"), m.group("unit")
        if not pre and not unit:
            continue
        if not pre and unit != unit.lstrip() and unit.strip() in ("k", "K", "L"):
            continue  # "12 L" / "5 k" without a currency marker: too ambiguous
        out.append((m.start(), m.end(), T.COMPENSATION, 0.9, "D2:compensation"))
    for kw in _COMP_KW.finditer(text):
        for m in _COMP_BARE.finditer(text, kw.end()):
            if m.start() > kw.end() + 30:
                break
            n = m.group()
            intpart = n.split(".")[0].replace(",", "")
            if "," not in n and len(intpart) < 4:
                continue
            if "," not in n and "." not in n and len(n) == 4 and 1990 <= int(n) <= 2100 \
                    and _YEAR_PRE.search(text[max(0, m.start() - 20):m.start()]):
                continue
            out.append((m.start(), m.end(), T.COMPENSATION, 0.8, "D2:compensation"))
    return out


# --------------------------------------------------------------------------
# DATE_OF_BIRTH
# --------------------------------------------------------------------------
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = re.compile(
    r"(?P<iso>(?P<iy>\d{4})[\-/](?P<im>\d{1,2})[\-/](?P<id>\d{1,2}))"
    r"|(?P<num>(?P<na>\d{1,2})[/\-.](?P<nb>\d{1,2})[/\-.](?P<ny>\d{4}))"
    r"|(?P<dmy>(?P<dd>\d{1,2})(?:st|nd|rd|th)?[ \-]+" + _MON + r"\.?,?[ \-]+(?P<dy>\d{4}))"
    r"|(?P<mdy>" + _MON + r"\.?[ \-]+(?P<md>\d{1,2})(?:st|nd|rd|th)?,?[ \-]+(?P<my>\d{4}))",
    re.I,
)
_DOB_KW = re.compile(
    _NA + r"(?:dob|d\.o\.b\.?|date[ \t]+of[ \t]+birth|born|birth[ \t]*date)(?![A-Za-z])", re.I
)


def _date_ok(m: re.Match, text: str) -> bool:
    if m.start() > 0 and text[m.start() - 1].isalnum():
        return False
    if m.end() < len(text) and text[m.end()].isalnum() and not (m.group("iso") and text[m.end()] == "T"):
        return False
    if m.group("iso"):
        y, mo, d = int(m.group("iy")), int(m.group("im")), int(m.group("id"))
        return 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31
    if m.group("num"):
        a, b, y = int(m.group("na")), int(m.group("nb")), int(m.group("ny"))
        return 1900 <= y <= 2100 and 1 <= a <= 31 and 1 <= b <= 31 and (a <= 12 or b <= 12)
    if m.group("dmy"):
        return 1 <= int(m.group("dd")) <= 31 and 1900 <= int(m.group("dy")) <= 2100
    return 1 <= int(m.group("md")) <= 31 and 1900 <= int(m.group("my")) <= 2100


def _dob(text: str) -> list[Cand]:
    out: list[Cand] = []
    for kw in _DOB_KW.finditer(text):
        end = min(len(text), kw.end() + 30 + 40)
        for m in _DATE.finditer(text, kw.end(), end):
            if m.start() > kw.end() + 30:
                break
            if _date_ok(m, text):
                out.append((m.start(), m.end(), T.DATE_OF_BIRTH, 0.9, "D2:dob"))
                break
    return out


# --------------------------------------------------------------------------
# Fusion
# --------------------------------------------------------------------------
def _resolve(cands: list[Cand]) -> list[Detection]:
    order = sorted(cands, key=lambda c: (-c[3], -(c[1] - c[0]), c[0]))
    taken: list[Cand] = []
    for c in order:
        if c[1] <= c[0]:
            continue
        if all(c[1] <= t[0] or c[0] >= t[1] for t in taken):
            taken.append(c)
    taken.sort(key=lambda c: (c[0], c[1]))
    return [Detection(s, e, ty, sc, src) for s, e, ty, sc, src in taken]


def _overlaps(c: Cand, others: list[Cand]) -> bool:
    return any(c[0] < o[1] and o[0] < c[1] for o in others)


def detect_d2(text: str) -> list[Detection]:
    """Deterministic detections: non-overlapping, sorted by start."""
    if not text:
        return []
    cards = _cards(text)
    aadhaar = _aadhaar(text)
    bank = [b for b in _bank(text) if not _overlaps(b, cards) and not _overlaps(b, aadhaar)]
    cands: list[Cand] = []
    for part in (
        _email(text), _upi(text), _phone(text), aadhaar, _pan(text), _ifsc(text), cards, bank,
        _secret(text), _employee_id(text), _compensation(text), _dob(text),
    ):
        cands.extend(part)
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
