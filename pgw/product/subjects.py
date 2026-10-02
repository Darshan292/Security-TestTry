"""PATH A declared subjects: identities the app already knows are protected without relying on detection.

The helpdesk app sends, next to the ticket text, the people involved (requester, assignee, mentioned employees,
other subjects) as a structured `pgw_subjects` field, validated against the purpose's data contract. For every
declared value the gateway builds MATCHERS: patterns over a Unicode-folded view of the text (compatibility forms
such as fullwidth letters, accents/combining marks and zero-width/format characters removed), mapped back to
original offsets. They cover written variants: any case; names joined by spaces, dots, underscores, hyphens,
apostrophes or nothing; name parts; employee IDs with any separator and their digit runs; phone numbers with any
grouping/prefix and their halves; emails in any case.

  1. The matchers' spans are handed to the pipeline as detections, so every occurrence is aliased (or redacted)
     whatever the detectors think.
  2. After protection, the SAME matchers sweep the decoded outbound payload (nested JSON and \\u escapes decoded,
     the gateway's own constant text excluded). Any hit is an egress violation: fail closed, 500, nothing sent.
     Unlike the generic egress invariant this also covers values shorter than four characters.

Declared values live only for the request; aliases are scoped to the contract. NER and the other detectors still
run as the safety net for people the app did not declare. Error messages never contain values.

Known, accepted limits: single initials ("T. Kulkarni" leaves "T."); name parts that are common English words
("Will", "Do") are matched only when capitalised; homoglyphs from other scripts are left to the C4 refusal.
"""
from __future__ import annotations

import functools
import json
import re
import unicodedata
from dataclasses import dataclass

from ..alias import canon
from ..egress import _EMITTED_ALIAS
from ..types import EgressViolation, EntityType as T
from .contracts import SubjectSpec

MAX_LEN = {T.PERSON: 100, T.EMPLOYEE_ID: 32, T.EMAIL: 254, T.PHONE: 24}
MAX_PARTS = 6                 # name parts matched per declared name
MAX_OCCURRENCES = 1000        # distinct declared-subject spans per request; more is refused (cost bound)
FULL_SCORE, PART_SCORE = 1.0, 0.85   # a part never beats a declared or detected whole (e.g. an email)
SOURCE = "C:declared-subject"
_HONORIFICS = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "shri", "smt", "sri", "kumari", "md", "mohd"}
try:
    from spacy.lang.en.stop_words import STOP_WORDS as _SW
    _COMMON = {w.lower() for w in _SW}
except ImportError:   # spaCy absent: a small fallback list; parts in it are matched only when capitalised
    _COMMON = {"a", "an", "and", "as", "at", "be", "by", "do", "for", "in", "is", "it", "may", "no", "of", "on",
               "or", "so", "such", "the", "to", "up", "we", "will", "can", "go", "me", "my", "us"}
_NB, _NA = r"(?<![^\W_])", r"(?![^\W_])"   # boundaries: not preceded/followed by a letter or digit
# After a name: optional digits ("tejaswini99", "kulkarni2026"), then no letter/digit and no "@" (an email that
# contains the name is left to the email detector, so it is aliased and restored as an email).
_NA_NAME = r"\d*(?![^\W_]|@)"
# Letters NFKD does not decompose: transliterated so "Søren Ørsted" and "Łukasz" match "Soren Orsted", "Lukasz".
_TRANSLIT = {"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ß": "ss", "ẞ": "SS", "ı": "i", "æ": "ae",
             "Æ": "AE", "œ": "oe", "Œ": "OE", "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D", "ħ": "h", "Ħ": "H"}
_NAME_JOIN = r"[\s._\-'’]*"
_ROLES = {"system", "user", "assistant", "tool", "developer", "text", "tool_use", "tool_result", "function"}


class SubjectsError(ValueError):
    """The declared subjects do not match the data contract. Messages never contain values."""


Declared = list[tuple[str, T, str]]      # (role, entity type, value)


# --- folding ----------------------------------------------------------------------------------------------------

@functools.lru_cache(maxsize=256)
def fold(text: str) -> tuple[str, tuple[int, ...]]:
    """Compatibility-decomposed text without combining marks or format characters, plus a map from each folded
    character to its index in `text` (so matches can be replaced in the original)."""
    out, omap = [], []
    for i, c in enumerate(text):
        if unicodedata.category(c) == "Cf":          # zero-width spaces/joiners, soft hyphen, bidi marks
            continue
        for d in unicodedata.normalize("NFKD", _TRANSLIT.get(c, c)):
            if unicodedata.category(d) in ("Mn", "Me", "Cf"):
                continue
            out.append(d)
            omap.append(i)
    return "".join(out), tuple(omap)


@dataclass(frozen=True)
class Matcher:
    t: T
    pattern: re.Pattern
    score: float

    def spans(self, text: str) -> list[tuple[int, int]]:
        f, omap = fold(text)
        return [(omap[m.start()], omap[m.end() - 1] + 1) for m in self.pattern.finditer(f) if m.end() > m.start()]


# --- request validation -----------------------------------------------------------------------------------------

def parse(raw, spec: SubjectSpec) -> Declared:
    if not isinstance(raw, dict):
        raise SubjectsError("pgw_subjects must be an object keyed by role")
    unknown = set(raw) - set(spec.roles)
    if unknown:
        raise SubjectsError(f"{len(unknown)} undeclared role(s) in pgw_subjects")
    missing = [r for r in spec.required if not raw.get(r)]
    if missing:
        raise SubjectsError(f"the data contract requires role(s): {', '.join(missing)}")
    out: Declared = []
    for role, items in raw.items():
        items = [items] if isinstance(items, dict) else items
        if not isinstance(items, list) or len(items) > spec.roles[role]:
            raise SubjectsError(f"{role}: expected at most {spec.roles[role]} subject object(s)")
        for i, subj in enumerate(items):
            if not isinstance(subj, dict) or not subj:
                raise SubjectsError(f"{role}[{i}]: expected a non-empty object")
            bad = set(subj) - set(spec.fields)
            if bad:
                raise SubjectsError(f"{role}[{i}]: {len(bad)} field(s) not allowed by the data contract")
            for f, v in subj.items():
                t = spec.fields[f]
                if (not isinstance(v, str) or not v.strip() or len(v) > MAX_LEN.get(t, 100)
                        or any(ord(c) < 32 for c in v)):
                    raise SubjectsError(f"{role}[{i}].{f}: expected a single-line string within the length limit")
                if t is T.PERSON and len(re.sub(r"[\W\d_]", "", fold(v)[0])) < 2:
                    raise SubjectsError(f"{role}[{i}].{f}: not a usable name")
                if t is T.PHONE and len(re.sub(r"\D", "", v)) < 6:
                    raise SubjectsError(f"{role}[{i}].{f}: not a usable phone number")
                if t is T.EMPLOYEE_ID and len(re.sub(r"[\W_]", "", v)) < 3:
                    raise SubjectsError(f"{role}[{i}].{f}: not a usable identifier")
                out.append((role, t, v.strip()))
    return out


# --- matchers ---------------------------------------------------------------------------------------------------

def _word(tok: str) -> tuple[str, int]:
    """Pattern and flags for one name token: case-insensitive, except common English words (capitalised only)."""
    if tok.lower() in _COMMON:
        return "(?:" + re.escape(tok[:1].upper() + tok[1:].lower()) + "|" + re.escape(tok.upper()) + ")", 0
    return re.escape(tok), re.IGNORECASE


def _name_matchers(value: str) -> list[Matcher]:
    toks = re.findall(r"[^\W\d_]+", fold(value)[0])
    if len(toks) == 1:
        p, flags = _word(toks[0])
        return [Matcher(T.PERSON, re.compile(_NB + p + _NA_NAME, flags), FULL_SCORE)]
    out = [Matcher(T.PERSON, re.compile(_NB + _NAME_JOIN.join(map(re.escape, toks)) + _NA_NAME, re.IGNORECASE),
                   FULL_SCORE)]
    g, sur = toks[0], toks[-1]
    if len(g) >= 3 and len(sur) >= 3:                 # handles: tkulkarni, tejaswinik, kulkarnit, kulkarni.tejaswini
        for h in (re.escape(g[0]) + re.escape(sur), re.escape(g) + re.escape(sur[0]),
                  re.escape(sur) + re.escape(g[0]), re.escape(sur) + _NAME_JOIN + re.escape(g)):
            out.append(Matcher(T.PERSON, re.compile(_NB + h + _NA_NAME, re.IGNORECASE), PART_SCORE))
    for word in value.split():                        # "D'Souza", "Mary-Jane": the compound word, any join
        wt = re.findall(r"[^\W\d_]+", fold(word)[0])
        if len(wt) > 1:
            out.append(Matcher(T.PERSON, re.compile(_NB + _NAME_JOIN.join(map(re.escape, wt)) + _NA_NAME,
                                                    re.IGNORECASE), PART_SCORE))
    parts = [t for t in toks if len(t) >= 2 and t.lower() not in _HONORIFICS][:MAX_PARTS]
    for tok in dict.fromkeys(parts):
        p, flags = _word(tok)
        out.append(Matcher(T.PERSON, re.compile(_NB + p + _NA_NAME, flags), PART_SCORE))
    return out


def _id_matchers(value: str) -> list[Matcher]:
    chars = re.sub(r"[\W_]", "", fold(value)[0])
    out = [Matcher(T.EMPLOYEE_ID, re.compile(_NB + r"[\s\W_]{0,3}".join(map(re.escape, chars)) + _NA,
                                             re.IGNORECASE), FULL_SCORE)]
    for run in re.findall(r"\d{4,}", chars):          # "employee 11914" is still the employee
        out.append(Matcher(T.EMPLOYEE_ID, re.compile(r"(?<!\d)" + run + r"(?!\d)"), PART_SCORE))
    return out


def _phone_matchers(value: str) -> list[Matcher]:
    d = canon(T.PHONE, value)
    out = [Matcher(T.PHONE, re.compile(r"(?<!\d)(?:\+?\s*91[\s\-]*|0{1,2}91[\s\-]*|0)?" + r"[\s\-().]*".join(d)
                                       + r"(?!\d)"), FULL_SCORE)]
    if len(d) >= 10:                                   # half a number is still identifying
        for half in (d[: len(d) // 2], d[len(d) // 2:]):
            out.append(Matcher(T.PHONE, re.compile(r"(?<!\d)" + half + r"(?!\d)"), PART_SCORE))
    return out


def matchers(declared: Declared) -> list[Matcher]:
    out: list[Matcher] = []
    for _, t, v in declared:
        if t is T.PERSON:
            out += _name_matchers(v)
        elif t is T.EMPLOYEE_ID:
            out += _id_matchers(v)
        elif t is T.PHONE:
            out += _phone_matchers(v)
        else:
            out.append(Matcher(t, re.compile(r"(?<![\w.+\-])" + re.escape(fold(v)[0]) + r"(?![\w\-])", re.IGNORECASE),
                               FULL_SCORE))
    return list(dict.fromkeys(out))


def count_occurrences(texts: list[str], ms: list[Matcher]) -> int:
    """Distinct non-overlapping declared spans (a signature line with name, ID and phone counts ~3, not ~9)."""
    total = 0
    for t in texts:
        spans = sorted(sp for m in ms for sp in m.spans(t))
        end = -1
        for s, e in spans:
            if s >= end:
                total += 1
            end = max(end, e)
    return total


def found_in(texts: list[str], ms: list[Matcher]) -> bool:
    return any(m.spans(t) for m in ms for t in texts)


# --- payload walking and the outbound sweep ---------------------------------------------------------------------

def _strings(x, acc: list[str], depth: int = 0) -> list[str]:
    if isinstance(x, str):
        acc.append(x)
        s = x.strip()
        if depth < 3 and s[:1] in ("{", "[", '"'):       # JSON inside a string (tool arguments, pasted payloads)
            try:
                _strings(json.loads(s), acc, depth + 1)
            except ValueError:
                pass
    elif isinstance(x, list):
        for v in x:
            _strings(v, acc, depth)
    elif isinstance(x, dict):
        for v in x.values():
            _strings(v, acc, depth)
    return acc


def request_texts(messages) -> list[str]:
    return _strings(messages, [])


_UESC = re.compile(r"\\u([0-9a-fA-F]{4})")


def check_outbound(wire: bytes, ms: list[Matcher], own_text: tuple[str, ...] = ()) -> None:
    """Raise EgressViolation if any declared matcher hits the decoded outbound payload. `own_text` are strings the
    gateway itself put there (placeholder note, model name); they are blanked so they cannot cause false alarms."""
    if not ms:
        return
    try:
        strings = _strings(json.loads(wire), [])
    except ValueError:
        strings = [wire.decode("utf-8", errors="replace")]
    hay = "\n".join(s for s in strings if s not in _ROLES and s not in own_text)
    for o in own_text:
        if o:
            hay = hay.replace(o, " " * len(o))
    hay = hay + "\n" + _UESC.sub(lambda m: chr(int(m.group(1), 16)), hay)   # ensure_ascii-escaped text
    hay = _EMITTED_ALIAS.sub(lambda m: " " * len(m.group(0)), hay)
    hits = sorted({m.t.value for m in ms if m.spans(hay)})
    if hits:
        raise EgressViolation(hits)


def prefer_declared_spelling(prot, declared: Declared) -> None:
    """Restore a declared identity as the app spelled it ("Tejaswini Kulkarni"), not as the ticket happened to
    (e.g. "TEJASWINI KULKARNI"). Only entities that already share the declared value's canonical form change."""
    want = {(t, canon(t, v)): v for _, t, v in declared}
    for key, ent in prot.entities.items():
        if key in want:
            ent.value = want[key]


def ledger_items(declared: Declared) -> list[tuple[T, str]]:
    """What a conversation remembers (as keyed hashes in the restore ledger, never as values) so that later turns
    re-protect a declared identity even if the app does not declare it again: each value, plus name parts."""
    out = [(t, v) for _, t, v in declared]
    for _, t, v in declared:
        if t is T.PERSON:
            out += [(T.PERSON, w) for w in re.findall(r"[^\W\d_]+", fold(v)[0])
                    if len(w) >= 3 and w.lower() not in _HONORIFICS and w.lower() not in _COMMON]
    return out
