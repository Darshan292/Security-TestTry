"""Request protection: normalise -> detect (D1, D2, D3, D3b) -> propagate (D5) -> decide -> transform.

Works on the OpenAI Chat Completions message list. Anything in a content
position that we do not understand is rejected, never forwarded un-inspected.
"""
from __future__ import annotations

from bisect import bisect_left
import copy
import json
import os
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .alias import AliasAllocator, canon, scope_key
from .detect import idshape, recognizers, structure
from .detect import names as _names
from .detect.lexicon import ends_name, is_english
from .detect.numwords import only_digit_words
from .detect.edm import RestoreLedger, RosterIndex
from . import ablation as _abl
from . import normalize as _normalize_mod
from .normalize import normalize, to_original, word_end


def _views(text: str):
    """D0 views if available (R2), else the single base view."""
    if hasattr(_normalize_mod, "views") and _abl.get().D0:
        return _normalize_mod.views(text)
    return [("base", *normalize(text))]
from .policy import PolicyPack, generalize
from .types import Action, BlockedRequest, Context, Detection, Entity, EntityType


class UnsupportedContent(Exception):
    """400: a field in a content position the gateway cannot inspect."""


class ConversationIdRequired(Exception):
    """400: multi-turn restore without a conversation id (ADR-0010 rule 1)."""


ALLOWED_MESSAGE_KEYS = {"role", "content", "name", "tool_calls", "tool_call_id"}
ALLOWED_ROLES = {"system", "developer", "user", "assistant", "tool"}


# --- keys ---------------------------------------------------------------------

@dataclass(frozen=True)
class Keys:
    master: bytes

    @classmethod
    def from_env(cls) -> "Keys":
        hexkey = os.environ.get("PGW_MASTER_KEY")
        if not hexkey:
            raise RuntimeError("PGW_MASTER_KEY (64 hex chars) is required")
        return cls(bytes.fromhex(hexkey))

    # Single-master mode (tests/demos): one implicit key id for every tenant and the platform ("*").
    # Per-tenant keys with rotation: pgw.keys.TenantKeyring (same interface).
    KEY_ID = "v1"

    def active_id(self, tenant: str) -> str:
        return self.KEY_ID

    def has(self, tenant: str, key_id: str) -> bool:
        return key_id == self.KEY_ID

    def derive(self, tenant: str, purpose: str, key_id: str | None = None) -> bytes:
        if key_id not in (None, self.KEY_ID):
            raise KeyUnavailable("unknown key id")
        return hkdf(self.master, tenant, purpose)


class KeyUnavailable(Exception):
    """No key for this tenant / key id. Requests fail closed (503); never another tenant's key."""


def hkdf(master: bytes, tenant: str, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=f"pgw/{purpose}/v1/{tenant}".encode()).derive(master)


# --- weak PERSON cues (Phase 1 has no NER) ---------------------------------------

# A name never crosses a line break: the words inside a cue name are joined by spaces or tabs only, so a capitalised
# word that starts the next line ("Mother is unwell") is never swallowed. The cue itself may sit on the line before
# (a sign-off). pgw.pipeline._resolve enforces the same rule for every PERSON span, whatever produced it.
_CAP = r"[A-Z][a-z'’\-]+(?:[ \t]+[A-Z]\.)?"
_HONORIFIC = re.compile(rf"\b(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt|Sri|Prof)\.?[ \t]+((?:{_CAP})(?:[ \t]+{_CAP}){{0,2}})")
_GREETING = re.compile(rf"\b(?:Dear|Hi|Hello|Hey)[ \t]+((?:{_CAP})(?:[ \t]+{_CAP}){{0,2}})")
_SIGNOFF = re.compile(rf"(?:Regards|Thanks|Thank you|Best|Sincerely|Cheers),?[ \t]*\n\s*((?:{_CAP})(?:[ \t]+{_CAP}){{0,2}})")
# Preposition + >= 2 capitalised tokens ("email to Rahul Sharma"). Over-masks some org names;
# that costs utility, a miss costs a leak. Precision is measured in E-DET.
_PREP = re.compile(rf"\b(?:to|for|from|with|cc|about|regarding|by|and|between|of)[ \t]+((?:{_CAP})(?:[ \t]+{_CAP}){{1,2}})")
_NOT_NAMES = {"team", "all", "everyone", "sir", "madam", "there", "folks", "hr", "manager", "guys",
              "support", "admin", "the", "this", "that", "please", "thanks", "regards",
              # forms of address in Indian chat ("hi bhai", "thanks yaar")
              "bhai", "bhaiya", "bhaiyya", "didi", "yaar", "ji", "bro", "dude", "mam", "maam", "dost", "sirji"}


_NAME_TOKEN = re.compile(r"(?:[A-Z][a-z]+(?:-[A-Z][a-z]+)*|[A-Z]['’][A-Z][a-z]+|[A-Z][a-z]+['’][a-z]+|[A-Z]\.)$")
_CAPS_TOKEN = re.compile(r"[A-Z]{2,}(?:['’-][A-Z]+)*$")


def _name_shaped(span: str) -> bool:
    """Shape filter for statistical NER hits (R2, dev2): spaCy tags code identifiers such as
    AWS_SECRET_ACCESS_KEY, BankRepo.save(BankRepo.java or payroll_rw as PERSON. Accept a span
    only if every token is a Title-case name word or an initial, or the span is >= 2 all-caps
    alphabetic words (ANINDITA ROUT). Digits, _ ( ) / = and camelCase never pass."""
    toks = span.split()
    if not toks or len(toks) > 5:
        return False
    if all(_NAME_TOKEN.match(t) for t in toks):
        return True
    return len(toks) >= 2 and all(_CAPS_TOKEN.match(t) for t in toks)


_DIGIT_GROUP = re.compile(r"\d+")


def _card_grouped(text: str, s: int, e: int) -> bool:
    """A card number written with separators is grouped like one (every group at least three digits, as the base
    view's card rule requires); the digits_joined view may only remove the separators of such a group."""
    return all(len(g) >= 3 for g in _DIGIT_GROUP.findall(text, s, e))


_PREV_WORD = re.compile(r"([^\W\d_][\w'’\-]*)[ \t]+$")
_SENT_START = re.compile(r"(?:^|[.!?:;\n\"“(\[]|^\W*)\s*$")


def _of_inside_proper_phrase(text: str, prep_start: int) -> bool:
    """'of' joining two capitalised phrases ("Head of Talent Acquisition", "Payment of Wages Act", "Ministry of
    Labour") is a connector inside an organisation, role or instrument name, not a preposition introducing a person.
    True when the word before 'of' is capitalised and is not itself the first word of a sentence or line (where
    capitalisation says nothing: "Salary of Rahul Sharma ...")."""
    m = _PREV_WORD.search(text, max(0, prep_start - 60), prep_start)
    if m is None or not m.group(1)[0].isupper():
        return False
    return not _SENT_START.search(text, max(0, m.start() - 4), m.start())


_CUE_TOKEN = re.compile(r"\S+")


def _cue_name(text: str, start: int, end: int, prep: bool) -> tuple[int, int] | None:
    """The name inside a cue match: trailing role, document, time and function words and verb forms are not part of
    it ("Leena Fernandes Head" -> "Leena Fernandes"; lexicon.ends_name); a name never runs on to the next line. A
    preposition cue made only of English words, one of them such a word ("for Payroll Operations"), is no
    name; English words that are also surnames ("with Rose Thomas") are kept."""
    nl = text.find("\n", start, end)
    toks = [m.span() for m in _CUE_TOKEN.finditer(text, start, end if nl == -1 else nl)]
    while len(toks) > 1 and ends_name(text[toks[-1][0]:toks[-1][1]]):
        toks.pop()
    words = [text[a:b].rstrip(".") for a, b in toks]
    if prep and all(is_english(w) for w in words) and any(ends_name(w) for w in words):
        return None
    if all(_names.is_place(w) for w in words):
        return None                 # "with Karnataka Shops and ... Act": a place, not a person
    return (toks[0][0], toks[-1][1]) if toks else None


def detect_person_cues(text: str) -> list[Detection]:
    out = []
    if not _abl.get().CUES:
        return out
    for rx, score in ((_HONORIFIC, 0.85), (_GREETING, 0.7), (_SIGNOFF, 0.7), (_PREP, 0.6)):
        for m in rx.finditer(text):
            name = m.group(1)
            if name.split()[0].lower() in _NOT_NAMES:
                continue
            if rx is _PREP and text.startswith("of", m.start()) and _of_inside_proper_phrase(text, m.start()):
                continue
            span = _cue_name(text, m.start(1), m.end(1), rx is _PREP)
            if span is not None:
                out.append(Detection(*span, EntityType.PERSON, score, "D2:person_cue"))
    # kinship and honorific constructions ("s/o Late Anwar Qureshi", "Prof. Dr. Anirban Chakraborty")
    out += _names.kinship_cues(text)
    return out


# --- legal-instrument titles ------------------------------------------------------
# A capitalised sequence ending in an instrument head word, optionally followed by a year, names a statute, rule
# set, code, scheme or policy ("the Payment of Wages Act, 1936", "Employees' Pension Scheme, 1995", "Leave Policy").
# "Code on Wages" (head word first, then "on" + capitalised words) is the same. Such a title is never a PERSON:
# statistical and cue detections that fall in it are dropped, and partial-name propagation does not enter it.
_LEGAL_HEAD = re.compile(r"(?<![\w'’\-])(?:Acts?|Rules|Regulations?|Codes?|Schemes?|Polic(?:y|ies)|Ordinances?|"
                         r"Amendments?|Bills?|Orders?)(?![\w'’\-])")
_TITLE_CONNECT = {"of", "and", "for", "on", "in", "to", "at", "&"}
_TITLE_YEAR = re.compile(r",?[ \t]*\(?(?:1[89]|20)\d{2}\)?(?!\d)")
_TCAP = r"[A-Z][\w'’\-]*"
_HEAD_FIRST = re.compile(rf"[ \t]+on[ \t]+{_TCAP}(?:(?:[ \t]+(?:of|and|for|the|&))*[ \t]+{_TCAP})*")
# Detections nothing can contradict: the roster, a contract declaration, the conversation's restore ledger.
_STRONG_SOURCES = ("D3:", "C:", "D3b:")


def _one_line(text: str, dets: list[Detection]) -> list[Detection]:
    """A PERSON span never crosses a line break. A span that does is cut at each break: a weak detection (cue, NER)
    keeps its first non-blank line only, so the capitalised first word of the next line ("Mother is unwell") is not
    swallowed; a strong one (roster, contract, conversation ledger) keeps every line piece, so nothing it protects is
    released. A name genuinely continued on the next line (a signature) is found by _names.signature_lines, which
    reads that line on its own terms."""
    out: list[Detection] = []
    for d in dets:
        if d.type is not EntityType.PERSON or "\n" not in text[d.start:d.end]:
            out.append(d)
            continue
        strong = d.source.startswith(_STRONG_SOURCES)
        pos = d.start
        for piece in text[d.start:d.end].split("\n"):
            a = pos + len(piece) - len(piece.lstrip())
            b = pos + len(piece.rstrip())
            pos += len(piece) + 1
            if b > a:
                out.append(Detection(a, b, d.type, d.score, d.source))
                if not strong:
                    break
    return out


_MAX_TITLE_TOKENS = 30


def _title_tokens(text: str, head_start: int, words: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    """Tokens of the capitalised phrase that ends right before a head word, walking left over `words` (the
    text's whitespace-separated tokens). A singular possessive ("Priya's Leave Policy") ends the walk: it belongs
    to an owner, not to the title. A plural possessive ("Employees' State Insurance Act") is part of the title.
    Parenthesised parts may contain commas."""
    out: list[tuple[int, int, str]] = []
    depth, nxt = 0, head_start
    j = bisect_left(words, (head_start,)) - 1
    for _ in range(_MAX_TITLE_TOKENS):
        if j < 0:
            break
        s, e = words[j]
        j -= 1
        if e > head_start:            # the head word is glued to the token before it ("(Amendment")
            e = head_start
        tok = text[s:e]
        if not tok or "\n" in text[e:nxt]:
            break
        depth += tok.count(")")
        word = tok.strip("()")
        if word.endswith(","):
            if depth <= 0:
                break
            word = word[:-1]
        depth -= tok.count("(")
        if depth < 0 or not word or word[-1] in ".;:!?\"”":
            break
        if word.lower() in _TITLE_CONNECT:
            if not out and word.lower() in ("and", "&"):   # "Rahul and Bill": a list, not a title
                break
            out.append((s, e, "c"))
        elif word[0].isupper() and word[0].isalpha():
            if re.search(r"['’]s$", word):
                break
            out.append((s, e, "p" if tok.startswith("(") else "w"))
        else:
            break
        nxt = s
    out.reverse()
    while out and out[0][2] == "c":
        out.pop(0)
    return out


def legal_titles(text: str, dets: list[Detection]) -> tuple[list[tuple[int, int]], list[Detection]]:
    """(title spans, `dets` without the weak PERSON hits that fall in a title).

    Weak PERSON hits (cues, NER) that overlap a title are dropped, with one exception: a hit that starts the
    capitalised run, ends before the head word and is followed by another capitalised word of the run ("Priya
    Raghunathan Leave Policy") is an owner written in front of the title. It is kept and the title starts after
    it. Strong hits (roster, declared, restore ledger) are never dropped and are never part of a title."""
    if not _LEGAL_HEAD.search(text):
        return [], dets
    persons = [d for d in dets if d.type is EntityType.PERSON]
    words = [m.span() for m in re.finditer(r"\S+", text)]
    spans: list[tuple[int, int]] = []
    for h in _LEGAL_HEAD.finditer(text):
        if any(d.source.startswith(_STRONG_SOURCES) and d.start < h.end() and h.start() < d.end for d in persons):
            continue   # the head word is a known person's name ("Bill"): not a title
        toks = _title_tokens(text, h.start(), words)
        tail = _HEAD_FIRST.match(text, h.end())
        trimmed = True
        while trimmed and toks:
            trimmed = False
            for d in persons:
                if d.end <= toks[0][0] or d.start >= h.start():
                    continue
                rest = [t for t in toks if t[0] >= d.end]
                owner = d.start <= toks[0][0] and d.end <= h.start() and bool(rest) and rest[0][2] == "w"
                if d.source.startswith(_STRONG_SOURCES) or owner:
                    toks = rest
                    while toks and toks[0][2] == "c":
                        toks.pop(0)
                    trimmed = True
                    break
        if not toks and not tail:
            continue
        # The span ends at the head word (plus a year). Words after "Code on" are left alone: a name written there
        # ("Leave Policy on Priya Raghunathan's case") stays a name.
        y = None if tail else _TITLE_YEAR.match(text, h.end())
        spans.append((toks[0][0] if toks else h.start(), y.end() if y else h.end()))
    if not spans:
        return [], dets
    return spans, [d for d in dets if not (d.type is EntityType.PERSON and not d.source.startswith(_STRONG_SOURCES)
                                           and _overlaps_any(d.start, d.end, spans))]


def _outside(dets: list[Detection], spans: list[tuple[int, int]]) -> list[Detection]:
    """`dets` without the weak hits in `spans` (table header rows). Strong hits (roster, declared, restore ledger) stay:
    a value the app declared is protected wherever it is written."""
    if not spans:
        return dets
    return [d for d in dets if d.source.startswith(_STRONG_SOURCES) or not _overlaps_any(d.start, d.end, spans)]


def _overlaps_any(s: int, e: int, spans: list[tuple[int, int]]) -> bool:
    return any(s < te and ts < e for ts, te in spans)


# --- segments -----------------------------------------------------------------

@dataclass
class Segment:
    role: str
    text: str
    setter: Callable[[str], None]
    # where the string sits in the request: (message index, content part index or None, field). field is
    # "content", "name" or "tool_calls[k].function.arguments". Used only for the transparency report's offsets.
    loc: tuple = ()


def _segments(messages: list[dict]) -> list[Segment]:
    segs: list[Segment] = []
    for mi, msg in enumerate(messages):
        if not isinstance(msg, dict) or set(msg) - ALLOWED_MESSAGE_KEYS or msg.get("role") not in ALLOWED_ROLES:
            raise UnsupportedContent("unsupported message shape")
        role = msg["role"]
        content = msg.get("content")
        if isinstance(content, str):
            segs.append(Segment(role, content, lambda v, m=msg: m.__setitem__("content", v), (mi, None, "content")))
        elif isinstance(content, list):
            for pi, part in enumerate(content):
                if not isinstance(part, dict) or part.get("type") != "text" or set(part) - {"type", "text"}:
                    raise UnsupportedContent("only text content parts are supported")
                segs.append(Segment(role, part["text"], lambda v, p=part: p.__setitem__("text", v),
                                    (mi, pi, "content")))
        elif content is not None:
            raise UnsupportedContent("content must be string, list or null")
        if isinstance(msg.get("name"), str):
            segs.append(Segment(role, msg["name"], lambda v, m=msg: m.__setitem__("name", v), (mi, None, "name")))
        for ti, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") if isinstance(tc, dict) else None
            if not isinstance(fn, dict) or not isinstance(fn.get("arguments"), str):
                raise UnsupportedContent("unsupported tool_call shape")
            segs.append(Segment(role, fn["arguments"], lambda v, f=fn: f.__setitem__("arguments", v),
                                (mi, None, f"tool_calls[{ti}].function.arguments")))
    return segs


def _field_values(text: str) -> list[tuple[EntityType, str]]:
    """D1: values under sensitive field names in JSON segments."""
    s = text.lstrip()
    if not s[:1] in ("{", "["):
        return []
    try:
        obj = json.loads(s)
    except ValueError:
        return []
    out = []
    for path, t in recognizers.detect_fields(obj):
        v = obj
        for k in path:
            v = v[k]
        if isinstance(v, bool) or v is None:
            continue
        sv = str(v)
        if sv.strip():
            out.append((t, sv))
    return out


# --- propagation ----------------------------------------------------------------

_STOP_TOKENS = {"mr", "mrs", "ms", "dr", "shri", "smt", "kumar", "bin", "van", "von", "de", "da"}
_HONORIFICS = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "shri", "smt", "sri", "kumari"}


def _partial_forms(surface: str) -> list[str]:
    """Shorter ways to write a detected name: each name word ("Priya", "Raghunathan")."""
    return [w for w in re.findall(r"[^\W\d_][\w'’\-]*", surface) if len(w) >= 3 and w.lower() not in _STOP_TOKENS]


def _initials(surfaces) -> dict[str, set[str]]:
    """surname -> initials of the given names it was detected with ("Priya Raghunathan": raghunathan -> {p})."""
    out: dict[str, set[str]] = {}
    for surface in surfaces:
        names = [w for w in re.findall(r"[^\W\d_]+", surface) if w.lower() not in _HONORIFICS]
        if len(names) >= 2 and len(names[0]) >= 2 and len(names[-1]) >= 3:
            out.setdefault(names[-1].casefold(), set()).add(names[0][0].casefold())
    return out


_INITIAL_BEFORE = re.compile(r"(?<![\w.'’])([^\W\d_])\.?[ \t]+$")


def _extend_initials(text: str, dets: list[Detection], initials: dict[str, set[str]]) -> list[Detection]:
    """Initial plus surname ("P. Raghunathan" after "Priya Raghunathan"): a detected surname takes in the initial
    written before it when that initial matches a given name it was detected with, so no initial is left behind."""
    out: list[Detection] = []
    for d in dets:
        if d.type is EntityType.PERSON and text[d.start:d.end].casefold() in initials:
            m = _INITIAL_BEFORE.search(text, max(0, d.start - 4), d.start)
            if (m and m.group(1).casefold() in initials[text[d.start:d.end].casefold()]
                    and (not out or out[-1].end <= m.start())):
                d = Detection(m.start(), d.end, d.type, d.score, d.source)
        out.append(d)
    return out


def _occurrences(text: str, t: EntityType, surface: str) -> list[tuple[int, int]]:
    if len(surface) < 2:
        return []
    flags = re.IGNORECASE if t in (EntityType.PERSON, EntityType.EMAIL, EntityType.UPI_ID) else 0
    pre = r"(?<![\w@])" if surface[0].isalnum() else ""
    post = r"(?![\w@])" if surface[-1].isalnum() else ""
    return [(m.start(), m.end()) for m in re.finditer(pre + re.escape(surface) + post, text, flags)]


_POSSESSIVE = re.compile(r"[^\W\d_]{2,}['’]s$")


def _drop_possessive(text: str, dets: list[Detection]) -> list[Detection]:
    """"Raghunathan's" -> "Raghunathan" + "'s": the possessive is grammar, not part of the name, so the name gets the
    same alias as everywhere else and the restored text keeps its "'s"."""
    return [Detection(d.start, d.end - 2, d.type, d.score, d.source)
            if d.type is EntityType.PERSON and _POSSESSIVE.search(text, d.start, d.end) else d for d in dets]


# --- one alias per person: partial mentions -------------------------------------------
# Within a request, and across the turns of a conversation, a partial mention of a full name (a name word, the
# surname with an honorific, an initial plus surname, any case) gets the full name's alias when exactly one known
# full name contains it. When two different full names contain it ("Priya Raghunathan" and "Priya Menon" both
# contain "Priya"), the partial keeps an alias of its own: it is still replaced, but it is not guessed to be
# either person. In a conversation that ambiguity is remembered: once a partial was ambiguous it stays so.

def _name_key(surface: str) -> tuple[str, ...]:
    v = re.sub(r"['’]s\b", "", canon(EntityType.PERSON, surface))
    return tuple(w for w in re.findall(r"[^\W\d_]+", v) if w not in _HONORIFICS)


def _is_full(k: tuple[str, ...]) -> bool:
    return sum(len(w) >= 2 for w in k) >= 2


def _is_part(p: tuple[str, ...], f: tuple[str, ...]) -> bool:
    """p is a shorter way to write f: its words appear in f in order, a single letter standing for an initial."""
    if p == f or not p or len(p) > len(f) or not any(len(w) >= 3 for w in p):
        return False
    i = 0
    for w in p:
        while i < len(f) and not (w == f[i] or (len(w) == 1 and f[i].startswith(w))):
            i += 1
        if i == len(f):
            return False
        i += 1
    return True


def _link_keys(f: tuple[str, ...]) -> list[str]:
    """The partial forms of a full name that a later turn may use on their own."""
    out = [w for w in f if len(w) >= 3 and w not in _STOP_TOKENS]
    if len(f) >= 2 and len(f[-1]) >= 3:
        out.append(f"{f[0][0]} {f[-1]}")
    return out


def _name_roots(keys: list[tuple[str, ...]]) -> dict[tuple[str, ...], set[tuple[str, ...]]]:
    """For every name key that is not itself a top-level full name: the full names it can stand for. A full name
    that is part of no longer one is top level; one that is part of exactly one longer name stands for that."""
    fulls = [k for k in keys if _is_full(k)]
    idx: dict[str, list[tuple[str, ...]]] = {}
    for f in fulls:
        for w in set(f):
            idx.setdefault(w, []).append(f)

    def containing(k: tuple[str, ...], pool: dict | None = None) -> list[tuple[str, ...]]:
        return [f for f in idx.get(max(k, key=len), ()) if _is_part(k, f) and (pool is None or f in pool)]

    maximal = {f: None for f in fulls if not containing(f)}
    out: dict[tuple[str, ...], set[tuple[str, ...]]] = {}
    root: dict[tuple[str, ...], tuple[str, ...]] = {f: f for f in maximal}
    for f in fulls:
        if f not in maximal:
            c = containing(f, maximal)
            root[f] = c[0] if len(c) == 1 else f
            if root[f] != f:
                out[f] = {root[f]}
    for k in keys:
        if k not in root:
            out[k] = {root[f] for f in containing(k)}
    return out


def _resolve(dets: list[Detection]) -> list[Detection]:
    """Non-overlapping set: higher score wins, then longer span. A propagated PERSON part never splits a longer
    PERSON detection that contains it ("Ramaswamy", known from "Ms Ramaswamy", inside "Devika Ramaswamy"): the
    container takes the part's score, so the whole name stays one entity."""
    props = sorted((d.start, d.end, d.score) for d in dets
                   if d.type is EntityType.PERSON and d.source == "D5:propagation")
    if props:
        boosted = []
        for d in dets:
            if d.type is EntityType.PERSON and d.source != "D5:propagation":
                i = bisect_left(props, (d.start,))
                best = d.score
                while i < len(props) and props[i][0] < d.end:
                    ps, pe, sc = props[i]
                    if pe <= d.end and pe - ps < d.end - d.start:
                        best = max(best, sc)
                    i += 1
                if best > d.score:
                    d = Detection(d.start, d.end, d.type, best, d.source)
            boosted.append(d)
        dets = boosted
    kept: list[Detection] = []
    for d in sorted(dets, key=lambda d: (-d.score, -(d.end - d.start), d.start)):
        if all(d.end <= k.start or d.start >= k.end for k in kept):
            kept.append(d)
    return sorted(kept, key=lambda d: d.start)


def _alias_label(ctx: Context) -> str:
    return f"alias/{ctx.alias_ns}" if ctx.alias_ns else "alias"


# Grown names propagated per request: each costs one scan of every segment, so a flood of distinct name variants must
# not multiply work (the grown names are still replaced where they were found).
_MAX_GROWN = 32


# --- the pipeline ---------------------------------------------------------------

@dataclass
class Protected:
    messages: list[dict]
    scope_id: str
    entities: dict[tuple[EntityType, str], Entity]
    alias_map: dict[str, Entity] = field(default_factory=dict)   # "TYPE_SUFFIX" -> entity
    # The inspected strings (texts as handed to detection) and their final resolved detections, in the same order.
    # References only (no copies); read by the gateway's transparency report, never sent anywhere.
    segments: list = field(default_factory=list, repr=False)
    detections: list = field(default_factory=list, repr=False)

    @property
    def sensitive_values(self) -> list[tuple[EntityType, str]]:
        """Everything the egress invariant must not see (ADR-0007)."""
        out = []
        for e in self.entities.values():
            if e.action is not Action.ALLOW:
                out.extend((e.type, s) for s in e.sources)
        return out

    def type_counts(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for e in self.entities.values():
            c[f"{e.type.value}:{e.action.value}"] = c.get(f"{e.type.value}:{e.action.value}", 0) + 1
        return c


# --- ML precedence (docs/ML_ARCHITECTURE.md) ------------------------------------------------------------------------
# With an ML detector configured, overlapping detections are resolved by tier first, then by score and length:
#   tier 0  facts and validated identifiers: roster, contract-declared values, the conversation's restore ledger, and
#           the ID regexes: checksummed (Aadhaar and the cue-less Aadhaar backstop: Verhoeff; card: Luhn) plus the
#           strict-format schemes whose partial masking would leak the rest of the value (PAN, IFSC, UPI, email,
#           phone, secrets)
#   tier 1  statistical models: the ML detector (D4:ml) and spaCy NER (D4:ner)
#   tier 2  heuristics: person cues and name growth, context-cued numbers and amounts, the identifier-shape backstop
# Scores are mapped into disjoint bands (tier 0: 0.9-1.0, tier 1: 0.5-0.88, tier 2: 0-0.49), monotone within a tier,
# so the existing overlap resolution (`_resolve`) and propagation keep working unchanged. Without an ML detector no
# score is touched (behaviour identical to the stack before the ML layer).
ID_REGEX_TYPES = frozenset({EntityType.AADHAAR, EntityType.AADHAAR_LIKE, EntityType.CARD, EntityType.PAN,
                            EntityType.IFSC, EntityType.UPI_ID, EntityType.EMAIL, EntityType.PHONE, EntityType.SECRET})
# Legacy name and amount heuristics inside D2, switched off with `legacy_heuristics: false`.
_HEURISTIC_D2 = ("D2:person_", "D2:compensation")
_MODEL_VIEWS = ("base", "casefold_titled", "despaced")


def precedence_tier(d: Detection) -> int:
    src = d.source
    if src.startswith(_STRONG_SOURCES) or (src.startswith(("D2:", "D2c:")) and d.type in ID_REGEX_TYPES):
        return 0
    if src.startswith("D4:"):
        return 1
    return 2


def _banded(d: Detection) -> Detection:
    s = min(1.0, max(0.0, float(d.score)))
    band = (0.9 + 0.1 * s, 0.5 + 0.38 * s, 0.49 * s)[precedence_tier(d)]
    return Detection(d.start, d.end, d.type, band, d.source)


class Protector:
    def __init__(self, policy: PolicyPack, keys: Keys, roster: RosterIndex | None = None,
                 ledger: RestoreLedger | None = None, ner=None, ml=None, heuristics: bool = True, plugins=None):
        self.policy = policy
        self.keys = keys
        self.roster = roster
        self.ledger = ledger
        self.ner = ner          # D4 (pgw.detect.ner.NerDetector) or None; its errors propagate = fail closed
        # D4:ml (pgw.ml.runtime.MlEntityStage / DetectorPool) or None; its errors propagate = fail closed
        self.ml = ml
        # legacy name/amount heuristics; False only with an ML detector (measurement: ML + checksum regex only)
        self.heuristics = heuristics or ml is None
        # pgw.plugins.PluginSet or None (docs/PLUGINS.md): shadow plugins are compared and counted only; union-mode
        # recognizers add spans that never release a character the built-in masks; their errors propagate = fail closed
        self.plugins = plugins

    def detect_text(self, text: str) -> list[Detection]:
        """D0 views + D2 + person cues + D3 (+ D4 NER) on one string; offsets in the ORIGINAL string.

        Every detector runs on every D0 view (base, de-spaced, de-obfuscated, digit-joined,
        case-restored); each view's offset map sends spans back to the original text, and
        overlaps are resolved later, so an evasion only has to be undone by one view.
        """
        out = []
        views = list(_views(text))
        # A subclass with its own detect_text (pgw.pd13: its own L2 shape layer and release rules) owns shape detection.
        backstop = _abl.get().D2 and type(self).detect_text is Protector.detect_text
        ner_res: dict[str, list[Detection]] = {}
        if self.ner is not None and _abl.get().D4:
            # one batched call over the distinct NER view texts (a pool dedupes/batches; errors propagate)
            ner_views = [v for n, v, _ in views if n in ("base", "casefold_titled", "despaced")]
            if ner_views:
                many = getattr(self.ner, "detect_many", None)
                res = many(ner_views) if many is not None else [self.ner.detect(v) for v in dict.fromkeys(ner_views)]
                ner_res = dict(zip(ner_views if many is not None else dict.fromkeys(ner_views), res))
        ml_res: dict[str, list[Detection]] = {}
        if self.ml is not None and _abl.get().D4:
            ml_views = list(dict.fromkeys(v for n, v, _ in views if n in _MODEL_VIEWS))
            if ml_views:   # one batched call; any failure propagates (fail closed)
                ml_res = dict(zip(ml_views, self.ml.detect_many(ml_views), strict=True))
        for name, view, omap in views:
            dets = recognizers.detect_d2(view)
            if self.heuristics:
                dets += detect_person_cues(view)
            else:
                dets = [d for d in dets if not d.source.startswith(_HEURISTIC_D2)]
            if name in ("base", "numwords", "digit_swap") and backstop:
                dets += idshape.detect(view)     # number-shaped identifier backstop (lowest score: loses overlaps)
            if name == "base" and _abl.get().CUES and self.heuristics:
                dets += _names.lowercase_cues(view)      # "hi tejas", "ask devraj", "s/o ramprasad yadav"
            if self.roster is not None and _abl.get().D3:
                dets += self.roster.detect(view)
                if name == "base":
                    # roster first/last names written in lower case ("ask devraj"): the roster's single-name rule needs
                    # a capital, so it also reads a copy with non-English lower-case words capitalised (same offsets)
                    titled = _names.titled_unknown(view)
                    if titled is not None:
                        dets += [d for d in self.roster.detect(titled) if d.type is EntityType.PERSON
                                 and view[d.start:d.end] != titled[d.start:d.end]]
            if name in ("base", "casefold_titled", "despaced") and view in ner_res:
                dets += [d for d in ner_res[view] if _name_shaped(view[d.start:d.end])]
            if name in _MODEL_VIEWS and view in ml_res:
                dets += ml_res[view]
            if self.ml is not None:
                dets = [_banded(d) for d in dets]
            for d in dets:
                if d.type is EntityType.PERSON and not d.source.startswith(_STRONG_SOURCES) \
                        and only_digit_words(view[d.start:d.end]):
                    continue                     # "Six Zero Two": a number spoken in words, not a name
                s, e = to_original(omap, d.start, d.end)
                if name == "numwords":
                    e = word_end(text, e)        # a digit maps to the start of the word that spells it
                elif name == "digits_joined" and d.type is EntityType.CARD and not _card_grouped(text, s, e):
                    continue                     # a Luhn hit glued from short digit groups is not a card
                if e > s:
                    out.append(Detection(s, e, d.type, d.score, f"{d.source}@{name}" if name != "base" else d.source))
        if self.plugins is not None:   # on the original text only, after (and compared with) the built-in detection
            out += self.plugins.apply(text, out)
        return _one_line(text, out)

    def protect(self, messages: list[dict], ctx: Context,
                declared: list[tuple] | None = None) -> Protected:
        """declared: values a data contract states explicitly (e.g. names and IDs from a structured payload).
        They are protected wherever they occur, independent of detection; they can only add protection."""
        messages = copy.deepcopy(messages)
        segs = _segments(messages)

        if ctx.restore_output and not ctx.conversation_id and any(s.role == "assistant" for s in segs):
            raise ConversationIdRequired()

        per_seg = self._detect_segments(segs, ctx, declared) if declared else self._detect_segments(segs, ctx)
        names = [n for n in (getattr(d, "name", "") if hasattr(d, "spans") else
                             (d[1] if d[0] is EntityType.PERSON else "") for d in declared or ()) if n]
        return self._transform(messages, segs, per_seg, ctx, names)

    def analyze(self, messages: list[dict], ctx: Context) -> list[list[Detection]]:
        """Final resolved spans per segment (for evaluation); does not transform."""
        return self._detect_segments(_segments(copy.deepcopy(messages)), ctx)

    def _detect_segments(self, segs: list[Segment], ctx: Context,
                         declared: list[tuple] | None = None) -> list[list[Detection]]:
        # 1. Detect per segment (plus contract-declared values, which need no detection).
        per_seg: list[list[Detection]] = [self.detect_text(s.text) for s in segs]
        bids = getattr(self.policy.purposes.get(ctx.purpose), "business_identifiers", ())
        if bids:   # company reference formats from the signed contract are not identifiers of a person
            per_seg = [idshape.exempt_business(s.text, d, bids) for s, d in zip(segs, per_seg, strict=True)]
        for item in declared or ():
            if hasattr(item, "spans"):   # a declared-subject matcher (pgw.product.subjects): spans, any variant
                for i, seg in enumerate(segs):
                    per_seg[i] += [Detection(s, e, item.t, item.score, "C:declared-subject")
                                   for s, e in item.spans(seg.text)]
                continue
            t, v, *score = item               # (type, surface) or (type, surface, score)
            if v and v.strip():
                sc = score[0] if score else 1.0
                for i, seg in enumerate(segs):
                    per_seg[i] += [Detection(s, e, t, sc, "C:declared") for s, e in _occurrences(seg.text, t, v)]
        if self.ledger is not None and ctx.conversation_id:
            # name parts this conversation linked to a person in an earlier turn ("Saurabh", "S. Kulshreshtha")
            ns = f"{_alias_label(ctx)}/{self._alias_key_id(ctx)}"
            for i, dets in enumerate(self.ledger.name_link_detect(ctx, ns, [s.text for s in segs])):
                per_seg[i] += dets
            normed = [normalize(s.text) for s in segs]
            for i, dets in enumerate(self.ledger.detect(ctx, [n for n, _ in normed])):
                omap = normed[i][1]
                per_seg[i] += [Detection(*to_original(omap, d.start, d.end), d.type, d.score, d.source)
                               for d in dets]

        # Legal-instrument titles are not people (weak hits in them dropped; propagation does not enter them).
        titles: list[list[tuple[int, int]]] = []
        for i, seg in enumerate(segs):
            per_seg[i] = _one_line(seg.text, per_seg[i])     # contract and ledger matches too
            spans, per_seg[i] = legal_titles(seg.text, per_seg[i])
            titles.append(spans)
            if _abl.get().CUES and self.heuristics:   # a column of names in a pasted table: its other cells too
                per_seg[i] += _names.table_columns(seg.text, _resolve(per_seg[i]))
        # The header row of a recognised table holds field labels, never values ("Gross" is not a person).
        headers = [structure.header_rows(seg.text) for seg in segs]
        per_seg = [_outside(d, h) for d, h in zip(per_seg, headers, strict=True)]

        if not _abl.get().D5:   # ablation: no propagation step at all (still resolve overlaps)
            return [_resolve(_drop_possessive(seg.text, d)) for seg, d in zip(segs, per_seg, strict=True)]

        # 2. Known values: everything detected anywhere, plus D1 field values.
        known: dict[tuple[EntityType, str], float] = {}
        for seg, dets in zip(segs, per_seg):
            for d in _resolve(dets):
                if d.source == "C:declared-subject":   # matchers already cover every variant; propagating each
                    continue                          # surface again would multiply work (DoS) for nothing
                known[(d.type, seg.text[d.start:d.end])] = max(d.score, known.get((d.type, seg.text[d.start:d.end]), 0))
            for t, v in (_field_values(seg.text) if _abl.get().D1 else []):
                known[(t, v)] = max(0.95, known.get((t, v), 0))
        # PERSON partial mentions: "Rahul" after "Rahul Sharma" (D5); "R. Sharma" is completed after resolution.
        for (t, surface), sc in list(known.items()):
            if t is EntityType.PERSON:
                for part in _partial_forms(surface):
                    known.setdefault((t, part), sc - 0.05)

        # 3. D5 propagation: every known value, everywhere (a PERSON never inside a legal title).
        for i, seg in enumerate(segs):
            for (t, surface), sc in known.items():
                per_seg[i] += [Detection(s, e, t, sc, "D5:propagation") for s, e in _occurrences(seg.text, t, surface)
                               if t is not EntityType.PERSON or not _overlaps_any(s, e, titles[i])]
            per_seg[i] = _resolve(_drop_possessive(seg.text, per_seg[i]))
        initials = _initials(s for t, s in known if t is EntityType.PERSON)
        if initials:
            per_seg = [_extend_initials(seg.text, dets, initials) for seg, dets in zip(segs, per_seg, strict=True)]
        if _abl.get().CUES and self.heuristics:
            per_seg = self._grow_names(segs, per_seg, titles)
        return [_outside(d, h) for d, h in zip(per_seg, headers, strict=True)]   # propagation never enters a header

    def _grow_names(self, segs: list[Segment], per_seg: list[list[Detection]],
                    titles: list[list[tuple[int, int]]]) -> list[list[Detection]]:
        """Name words written next to a detected name join it ("Shalini" before an aliased "Phadnis", a middle name, a
        surname on the next signature line); the grown names and their new words are then found everywhere else in
        the request, as in step 3. Never inside a legal title."""
        grown: dict[str, float] = {}
        for i, seg in enumerate(segs):
            dets = _names.expand(seg.text, per_seg[i])
            sig = _names.signature_lines(seg.text, dets)
            if sig:
                dets = _resolve(dets + sig)
            dets = [d for d in dets if d.type is not EntityType.PERSON or d.source.startswith(_STRONG_SOURCES)
                    or not _overlaps_any(d.start, d.end, titles[i])]
            before = {(d.start, d.end) for d in per_seg[i]}
            for d in dets:
                if d.type is EntityType.PERSON and (d.start, d.end) not in before and len(grown) < _MAX_GROWN:
                    grown[seg.text[d.start:d.end]] = max(d.score, grown.get(seg.text[d.start:d.end], 0))
            per_seg[i] = dets
        if not grown:
            return per_seg
        extra: dict[str, float] = {}
        for surface, sc in grown.items():
            extra.setdefault(surface, sc)
            for part in _partial_forms(surface):
                extra.setdefault(part, sc - 0.05)
            if len(extra) >= 4 * _MAX_GROWN:
                break
        for i, seg in enumerate(segs):
            add = [Detection(s, e, EntityType.PERSON, sc, "D5:propagation") for surface, sc in extra.items()
                   for s, e in _occurrences(seg.text, EntityType.PERSON, surface)
                   if not _overlaps_any(s, e, titles[i])]
            if add:
                per_seg[i] = _resolve(_drop_possessive(seg.text, per_seg[i] + add))
        return per_seg

    def _alias_key_id(self, ctx: Context) -> str:
        """Active key id for new conversations; an existing conversation keeps the key id pinned in the
        restore ledger while that key still exists, so aliases stay stable across a key rotation.
        Raises KeyUnavailable when the tenant has no key (fail closed)."""
        active = self.keys.active_id(ctx.tenant)
        if self.ledger is None or not ctx.conversation_id:
            return active
        pinned = self.ledger.pinned_key(ctx)
        if pinned is not None and pinned != active and self.keys.has(ctx.tenant, pinned):
            return pinned
        self.ledger.pin_key(ctx, active)
        return active

    def _merge_partial_names(self, entities: dict, alloc: AliasAllocator, ctx: Context, ns: str,
                             declared_names: list[str]) -> dict[str, Entity]:
        """Give every way of writing one person that person's alias (rules above `_name_key`). Returns, for each
        alias shared by several ways of writing, the entity it restores to: the full name (as detected, or as
        declared even when the request only writes a part of it), or, when only the conversation's links know the
        full name, the longest partial written in this request."""
        ents: dict = {k: e for k, e in entities.items()
                      if k[0] is EntityType.PERSON and e.action is Action.TOKENIZE and e.alias}
        names = {k: _name_key(e.value) for k, e in ents.items()}
        by_name: dict[tuple[str, ...], tuple] = {}
        for k, nk in names.items():
            if nk:
                by_name.setdefault(nk, k)
        if ents:                              # PERSON is aliased in this request
            for v in declared_names:          # a declared full name stands for its parts even if never written whole
                nk = _name_key(v)
                if _is_full(nk) and nk not in by_name:
                    k = ("declared", v)
                    ents[k] = Entity(EntityType.PERSON, v, Action.TOKENIZE, alloc.get(EntityType.PERSON, v))
                    by_name[nk] = k
        roots = _name_roots(list(by_name))
        conv = self.ledger is not None and bool(ctx.conversation_id)
        links = self.ledger.name_links(ctx, ns, [" ".join(nk) for nk in roots]) if conv and roots else {}
        holder: dict[str, Entity] = {}
        record: dict[str, set[str]] = {}

        def share(nk: tuple[str, ...], target: str, owner: Entity | None) -> bool:
            for k, k_nk in names.items():
                if k_nk != nk:
                    continue
                ent = ents[k]
                if owner is None and not alloc.claim(ent.type, ent.value, target):
                    return False                 # the linked alias is taken in this request: keep its own
                ent.alias = target
                cur = holder.get(target)
                hold = owner or (ent if cur is None or len(ent.value) > len(cur.value) else cur)
                hold.sources.update(ent.sources, cur.sources if cur is not None else ())
                holder[target] = hold
            return True

        for nk, rep in by_name.items():           # "Dr Priya Raghunathan" and "PRIYA RAGHUNATHAN": one name key
            if nk not in roots:
                share(nk, ents[rep].alias, ents[rep])
        for nk, cands in roots.items():
            key = " ".join(nk)
            aliases = {ents[by_name[r]].alias for r in cands}
            linked = links.get(key)
            if linked == "" or len(aliases | ({linked} if linked else set())) > 1:
                record.setdefault(key, set()).add("")      # ambiguous, now or earlier in the conversation
                continue
            if aliases:
                target = next(iter(aliases))
                share(nk, target, ents[by_name[next(iter(cands))]])
            elif not linked or not share(nk, linked, None):
                continue
            else:
                target = linked
            record.setdefault(key, set()).add(target)
        if conv:
            for nk, k in by_name.items():
                if nk not in roots:               # a full name: its partial forms point to it from now on
                    for part in _link_keys(nk):
                        record.setdefault(part, set()).add(ents[k].alias)
            if record:
                self.ledger.record_name_links(ctx, ns, {p: (next(iter(a)) if len(a) == 1 else "")
                                                        for p, a in record.items()})
        return holder

    def _transform(self, messages, segs, per_seg, ctx, declared_names: list[str] | None = None) -> Protected:
        # 4. Decide.
        entities: dict[tuple[EntityType, str], Entity] = {}
        for seg, dets in zip(segs, per_seg):
            for d in dets:
                surface = seg.text[d.start:d.end]
                key = (d.type, canon(d.type, surface))
                ent = entities.get(key)
                if ent is None:
                    ent = entities[key] = Entity(d.type, surface, self.policy.decide(ctx.purpose, d.type))
                ent.sources.add(surface)
        blocked = [e.type for e in entities.values() if e.action is Action.BLOCK_REQUEST]
        if blocked:
            raise BlockedRequest(blocked)

        # 5. Transform.
        scope_id = ctx.conversation_id or secrets.token_hex(16)
        # Contracted purposes get their own alias key: the same person is unlinkable across purposes.
        label = _alias_label(ctx)
        kid = self._alias_key_id(ctx)
        alloc = AliasAllocator(scope_key(self.keys.derive(ctx.tenant, label, kid), scope_id))
        prot = Protected(messages, scope_id, entities, segments=segs, detections=per_seg)
        for key, ent in entities.items():
            if ent.action is Action.TOKENIZE:
                ent.alias = alloc.get(ent.type, ent.value)
        for key, ent in entities.items():  # second pass: collisions may have re-assigned aliases
            if ent.action is Action.TOKENIZE:
                ent.alias = alloc.get(ent.type, ent.value)
        holder = self._merge_partial_names(entities, alloc, ctx, f"{label}/{kid}", declared_names or [])
        for ent in entities.values():
            if ent.action is Action.TOKENIZE:
                ent.replacement = ent.alias
                prot.alias_map[ent.alias.strip("[]").upper()] = holder.get(ent.alias, ent)
            elif ent.action is Action.REDACT:
                ent.replacement = f"[{ent.type.value}]"
            elif ent.action is Action.GENERALIZE:
                ent.replacement = generalize(ent.type, ent.value)

        for seg, dets in zip(segs, per_seg):
            text = seg.text
            for d in sorted(dets, key=lambda d: d.start, reverse=True):
                ent = entities[(d.type, canon(d.type, text[d.start:d.end]))]
                if ent.replacement is not None:
                    text = text[:d.start] + ent.replacement + text[d.end:]
            if text != seg.text:
                seg.setter(text)
        return prot
