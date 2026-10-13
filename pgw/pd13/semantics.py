"""L3: field semantics (arm B only). Deterministic; two sources of evidence.

1. The key dictionary (``dictionary.json``): a normalised key maps to a concept. A taxonomy concept TYPES the
   field value (the value must still fit the concept's loose shape); a RELEASE_* concept marks the value as
   releasable (consulted by L4 for shape candidates only).
2. Column statistics over fields that share a column id:
   - >= 80 % of the values validate as X (Verhoeff, PAN, IFSC, e-mail, phone, Luhn, UPI) => column type X;
   - >= 50 % of the values overlap a PERSON detection (roster / NER / cue) => name column;
   - date columns: the years fall 18-70 years before the latest date (document or today) => DOB-like (typed
     DATE_OF_BIRTH); otherwise release-like (RELEASE_DATE).

L3 never removes a detection: it adds typed spans and release marks. L4 decides what a release mark may release.
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from pgw.detect.recognizers import luhn_valid, verhoeff_valid
from pgw.types import EntityType as T

from .dictionary import RELEASE_CONCEPTS, Dictionary
from .fieldview import name_shaped_cell
from .records import Field, Graph, is_placeholder
from .shapes import Shape

_PAN = re.compile(r"^[A-Za-z]{3}[ABCFGHLJPTabcfghljpt][A-Za-z]\d{4}[A-Za-z]$")
_IFSC = re.compile(r"^[A-Za-z]{4}0[A-Za-z0-9]{6}$")
_EMAIL = re.compile(r"^[^\s@<>\"',;]+@[^\s@<>\"',;]+\.[A-Za-z]{2,}$")
_UPI = re.compile(r"^[A-Za-z0-9._\-]{2,64}@[A-Za-z][A-Za-z0-9]{1,30}$")
_PHONE = re.compile(r"^\+?[\d][\d \-().]{7,18}\d$")
_NAME_NEG = {"system", "admin", "administrator", "user", "unknown", "null", "none", "n/a", "na", "tbd", "nil", "self",
             "yes", "no", "true", "false", "active", "inactive", "male", "female"}


@dataclass
class L3Result:
    typed: list[tuple[int, int, T, str]] = field(default_factory=list)       # (start, end, type, source)
    released: list[tuple[int, int, str]] = field(default_factory=list)       # (start, end, RELEASE_*)


def _digits(v: str) -> str:
    return re.sub(r"\D", "", v)


def _trim(text: str, a: int, b: int) -> tuple[int, int]:
    while a < b and text[a] in " \t\"'`,;<>":
        a += 1
    while b > a and text[b - 1] in " \t\"'`,;<>.":
        b -= 1
    return a, b


def valid_as(t: T, v: str) -> bool:
    """Loose shape check: does the value look like an instance of the type?"""
    v = v.strip()
    if not v or is_placeholder(v):
        return False
    d = _digits(v)
    if t is T.PERSON:
        words = v.split()
        letters = sum(unicodedata.category(c)[0] in "LM" for c in v)
        return (2 <= len(v) <= 80 and len(words) <= 6 and letters >= 0.7 * len(v.replace(" ", ""))
                and "@" not in v and v.lower() not in _NAME_NEG and not any(c.isdigit() for c in v))
    if t is T.EMAIL:
        return bool(_EMAIL.match(v))
    if t is T.UPI_ID:
        return bool(_UPI.match(v))
    if t is T.PHONE:
        return bool(_PHONE.match(v)) and 8 <= len(d) <= 15
    if t is T.AADHAAR:
        return len(d) == 12 and bool(re.fullmatch(r"[\d \-]+", v))
    if t is T.PAN:
        return bool(_PAN.match(v))
    if t is T.IFSC:
        return bool(_IFSC.match(v))
    if t is T.CARD:
        return 13 <= len(d) <= 19 and bool(re.fullmatch(r"[\d \-]+", v))
    if t is T.BANK_ACCOUNT:
        return (len(d) >= 6 and len(v) <= 40 and sum(c.isdigit() for c in v) >= 0.6 * len(v.replace(" ", ""))) or bool(
            re.fullmatch(r"[A-Z]{2}\d{2}[A-Za-z0-9 ]{10,30}", v))
    if t is T.SECRET:
        return len(v) >= 6
    if t is T.EMPLOYEE_ID:
        return 3 <= len(v) <= 24 and any(c.isdigit() for c in v) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _\-/.]*", v))
    if t is T.COMPENSATION:
        return any(c.isdigit() for c in v) and len(v) <= 32 and not re.search(r"[A-Za-z]{4,}", re.sub(
            r"(?i)lakhs?|lacs?|crores?|per|annum|month|year|inr|usd|rs", "", v))
    if t is T.DATE_OF_BIRTH:
        return any(c.isdigit() for c in v) and len(v) <= 40
    return False


def _column_validator(t_name: str, v: str) -> bool:
    d = _digits(v)
    if t_name == "AADHAAR":
        return len(d) == 12 and d[0] in "23456789" and re.fullmatch(r"[\d \-]+", v) is not None and verhoeff_valid(d)
    if t_name == "PAN":
        return bool(_PAN.match(v))
    if t_name == "IFSC":
        return bool(_IFSC.match(v))
    if t_name == "EMAIL":
        return bool(_EMAIL.match(v))
    if t_name == "CARD":
        return 13 <= len(d) <= 19 and re.fullmatch(r"[\d \-]+", v) is not None and luhn_valid(d)
    if t_name == "UPI_ID":
        return bool(_UPI.match(v)) and "." not in v.split("@")[1]
    if t_name == "PHONE":
        return bool(_PHONE.match(v)) and 10 <= len(d) <= 13 and v.strip()[:1] in "+0123456789" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)
    return False


_COL_TYPES = [("AADHAAR", T.AADHAAR), ("PAN", T.PAN), ("IFSC", T.IFSC), ("EMAIL", T.EMAIL), ("CARD", T.CARD),
              ("UPI_ID", T.UPI_ID), ("PHONE", T.PHONE)]


def resolve_year(y: int, two_digit: bool, month: int, day: int, ref: _dt.date) -> _dt.date | None:
    try:
        if not two_digit:
            return _dt.date(y, month, day)
        for century in (2000, 1900):
            d = _dt.date(century + y, month, day)
            if d <= ref:
                return d
        return _dt.date(1900 + y, month, day)
    except ValueError:
        return None


def years_before(d: _dt.date, ref: _dt.date) -> float:
    return (ref - d).days / 365.25


class Semantics:
    def __init__(self, dictionary: Dictionary | None = None, today: _dt.date | None = None):
        self.dictionary = dictionary or Dictionary.load()
        self.today = today

    # ----------------------------------------------------------------------------------------------
    def analyse(self, text: str, graph: Graph, shapes: list[Shape], person_spans: list[tuple[int, int]]) -> L3Result:
        res = L3Result()
        today = self.today or _dt.date.today()
        fields = graph.fields
        # person evidence per field
        ps = sorted(person_spans)

        def person_cover(a: int, b: int) -> float:
            cov = 0
            for s, e in ps:
                if s >= b:
                    break
                if e > a:
                    cov += min(b, e) - max(a, s)
            return cov / max(1, b - a)

        cols: dict[tuple, list[Field]] = defaultdict(list)
        for f in fields:
            if f.col is not None:
                cols[f.col].append(f)

        seen_typed: dict[tuple[int, int], T] = {}

        def add_typed(a, b, t, src):
            a, b = _trim(text, a, b)
            if b > a and valid_as(t, text[a:b]) and seen_typed.get((a, b)) != t:
                seen_typed[(a, b)] = t
                res.typed.append((a, b, t, src))

        col_person = {}
        for cid, fl in cols.items():
            vals = [f for f in fl if not is_placeholder(text[f.start:f.end])]
            col_person[cid] = (sum(person_cover(f.start, f.end) >= 0.5 for f in vals) / len(vals)) if vals else 0.0

        # 1. dictionary
        dated_by_dict: set[tuple[int, int]] = set()
        for f in fields:
            e = self.dictionary.lookup(f.path, f.key)
            if e is None:
                continue
            a, b = f.start, f.end
            dated_by_dict.add((a, b))                     # the dictionary decided this field; statistics do not override
            if e.concept in RELEASE_CONCEPTS:
                res.released.append((a, b, e.concept))
                continue
            if e.needs_evidence:
                v = text[a:b]
                weak = all(name_shaped_cell(seg) for seg in v.split("/") if seg.strip()) and bool(v.strip())
                ev = person_cover(a, b) >= 0.5 or (f.col in col_person and col_person[f.col] >= 0.5) or weak
                if not ev:
                    continue
            t = T(e.concept)
            add_typed(a, b, t, "L3:dict")

        # 2. column statistics
        for cid, fl in cols.items():
            vals = [f for f in fl if not is_placeholder(text[f.start:f.end])]
            if len(vals) < 2 or len({text[f.start:f.end] for f in vals}) < 2:
                continue
            for name, t in _COL_TYPES:
                ok = sum(_column_validator(name, text[_trim(text, f.start, f.end)[0]:_trim(text, f.start, f.end)[1]])
                         for f in vals)
                if ok >= 0.8 * len(vals):
                    for f in vals:
                        add_typed(f.start, f.end, t, "L3:column")
                    break
            else:
                if col_person[cid] >= 0.5:
                    for f in vals:
                        add_typed(f.start, f.end, T.PERSON, "L3:column")

        # 3. date columns (also single dated fields): DOB-like vs release-like
        dates = [(s, resolve_year(s.ymd[0], s.ymd[3], s.ymd[1], s.ymd[2], today)) for s in shapes if s.kind == "DATE"]
        dates = [(s, d) for s, d in dates if d is not None]
        ref = max([d for _, d in dates] + [today])         # latest date: the document's, or today if later
        for cid, fl in cols.items():
            vals = [f for f in fl if not is_placeholder(text[f.start:f.end])]
            hits = []
            for f in vals:
                for s, d in dates:
                    if s.start >= f.start - 1 and s.end <= f.end + 1 and (f.end - f.start) <= (s.end - s.start) + 6:
                        hits.append((f, s, d))
                        break
            if not hits or len(hits) < 0.8 * len(vals):
                continue
            inside = sum(18 <= years_before(d, ref) <= 70 for _, _, d in hits)
            dob_like = inside >= 0.5 * len(hits)
            for f, s, d in hits:
                if (f.start, f.end) in dated_by_dict:
                    continue
                if dob_like:
                    add_typed(s.start, s.end, T.DATE_OF_BIRTH, "L3:column_date")
                else:
                    res.released.append((s.start, s.end, "RELEASE_DATE"))
        return res
