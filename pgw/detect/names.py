"""Person-name structure: words next to a known name, kinship and honorific constructions, lowercase names, table
columns of names. Linguistic and structural rules only; no name is listed anywhere in this module.

The one lexical signal is pgw.detect.lexicon.is_english: a capitalised word next to a name, or a lower-case word in a
name position, is taken as a name word only when it is NOT an ordinary English word. That keeps "Kiran Bhatt Joined
the Finance Team" and "hi team" intact while "Shalini Phadnis" and "ask devraj" are names. Names that are also English
words ("Rose", "Will") are missed by these rules (other detectors may still find them).

Every function is linear in its input: single-pass regexes with bounded repetition and bounded walks per match.
"""
from __future__ import annotations

import re
from bisect import bisect_left

from pgw.types import Detection, EntityType as T

from .lexicon import ends_name, is_english
from .places import is_place

# Forms of address, written before a name ("Prof. Dr.", "Dr (Mrs)", "Lt. Col.", "Late Smt."). General vocabulary.
HONORIFICS = ("mr", "mrs", "ms", "miss", "mx", "dr", "prof", "shri", "shree", "sri", "smt", "shrimati", "kumari", "kum",
              "km", "sushri", "selvi", "thiru", "thirumathi", "tmt", "late", "lt", "col", "capt", "cpt", "maj", "gen",
              "brig", "cdr", "adv", "er", "hon", "justice", "sir", "rev", "fr", "pt", "pandit", "ustad", "janab",
              "begum", "sardar", "sardarni", "retd", "dr.", "mrs.")
_HON_SET = frozenset(HONORIFICS)
_HON = r"(?i:" + "|".join(sorted({h.rstrip(".") for h in HONORIFICS}, key=len, reverse=True)) + r")"
# one honorific: "Dr", "Dr.", "(Mrs)", "(Retd.)"
_HON1 = r"(?:\([ \t]*" + _HON + r"\.?[ \t]*\)|" + _HON + r"(?![A-Za-z])\.?)"
_HON_CHAIN = re.compile(r"(?<![\w'’\-])" + _HON1 + r"(?:[ \t]*" + _HON1 + r"){0,3}[ \t]*")

# Kinship markers: s/o, d/o, w/o, h/o, c/o (son/daughter/wife/husband of, care of), S.O., "son of", "widow of" ...
_KIN = re.compile(r"(?<![\w/])(?:(?i:[sdwhc][ \t]*/[ \t]*o|[sdw]\.[ \t]*o\.)(?![\w/])"
                  r"|(?i:son|daughter|wife|husband|widow|widower|care|ward)[ \t]+(?i:of)(?![\w]))[ \t]*[:\-.]?[ \t]*")
_LATE = re.compile(r"(?<![\w'’\-])(?:\([ \t]*(?i:late)[ \t]*\)|(?i:late)(?![\w'’\-]))[ \t]*")

_CAP_WORD = r"[A-Z][a-z]+(?:['’\-][A-Za-z][a-z]+)*"
_UP_WORD = r"[A-Z]{2,}(?:['’\-][A-Z]+)*"
_INIT = r"[A-Z]\."
_CAP_TOK = re.compile(rf"(?:{_INIT}|{_CAP_WORD}|{_UP_WORD})(?![\w'’\-])")
_LOW_TOK = re.compile(r"[a-z][a-z'’]{2,30}(?![\w@\-]|\.[a-z])")
_GAP = re.compile(r"[ \t]{1,2}")
_VOWEL = re.compile(r"[aeiouy]")

_MAX_RUN = 5
_MAX_NAME_WORDS = 4      # a merged name never exceeds this many words (a list of names is not one name)

# capitalised words that are not English words but are not personal names either: weekdays/months are English;
# these are document and organisation tokens that WordNet does not list
_NOT_NAME_CAP = frozenset("""pvt ltd llp inc corp hrms hris hrbp bgv kyc uan esic epf pf ctc lpa pan otp noc fnf
team teams dept""".split())


def _name_word(tok: str) -> bool:
    """A capitalised token that is a plausible name word: an initial, or a word that is not ordinary English."""
    if re.fullmatch(_INIT, tok):
        return True
    w = tok.strip("'’").lower()
    return len(w) >= 2 and w not in _HON_SET and w not in _NOT_NAME_CAP and not is_english(w)


def _hon(tok: str) -> bool:
    return tok.lower().rstrip(".") in _HON_SET


def _cap_run(text: str, pos: int, limit: int = _MAX_RUN) -> list[tuple[int, int]]:
    """Capitalised tokens (initials, Title-case or UPPER words) from `pos`, separated by one or two spaces/tabs."""
    out: list[tuple[int, int]] = []
    p = pos
    while len(out) < limit:
        m = _CAP_TOK.match(text, p)
        if not m:
            break
        out.append(m.span())
        g = _GAP.match(text, m.end())
        if not g:
            break
        p = g.end()
    return out


def _trim(text: str, toks: list[tuple[int, int]], lead_ok: bool) -> list[tuple[int, int]]:
    """Drop trailing role, document, time and function words (lexicon.ends_name), and leading English words unless
    `lead_ok`; keep the run only if a non-English name word remains ("Baldev Raj Chauhan", "Arvind Bose" stay whole)."""
    while toks and (ends_name(text[toks[-1][0]:toks[-1][1]]) or _hon(text[toks[-1][0]:toks[-1][1]])
                    or _place(text[toks[-1][0]:toks[-1][1]])):
        toks = toks[:-1]
    if not lead_ok:
        while toks and not _name_word(text[toks[0][0]:toks[0][1]]):
            toks = toks[1:]
    if not any(_name_word(text[a:b]) and not re.fullmatch(_INIT, text[a:b]) for a, b in toks):
        return []
    return toks


def _low_run(text: str, pos: int, limit: int = 3) -> list[tuple[int, int]]:
    """Lower-case non-English tokens from `pos` (a lower-case name: "ramprasad yadav")."""
    out: list[tuple[int, int]] = []
    p = pos
    while len(out) < limit:
        m = _LOW_TOK.match(text, p)
        if not m or not _low_name_word(m.group()):
            break
        out.append(m.span())
        g = _GAP.match(text, m.end())
        if not g:
            break
        p = g.end()
    return out


def _low_name_word(w: str) -> bool:
    w = w.rstrip("'’")
    if w.endswith(("'s", "’s")):
        w = w[:-2]
    return len(w) >= 3 and bool(_VOWEL.search(w)) and w.isalpha() and not is_english(w)


def _det(a: int, b: int, score: float, src: str) -> Detection:
    return Detection(a, b, T.PERSON, score, src)


# --- kinship and honorific constructions --------------------------------------------------------------------------

def kinship_cues(text: str) -> list[Detection]:
    """Names in kinship and honorific constructions.

    - after an honorific chain: "Prof. Dr. Anirban Chakraborty", "Dr (Mrs) Hemlata Joglekar", "Shri R. Gopalan";
    - after a kinship marker, optionally "late" and honorifics: "s/o Late Anwar Qureshi", "w/o Baldev Raj Chauhan",
      "daughter of late Shri Venkat Rao Naidu"; after s/o, d/o, w/o, c/o also a lower-case name ("s/o ramprasad");
    - the name right before a kinship marker: "Applicant Farhan Qureshi s/o ..." -> "Farhan Qureshi";
    - after "late": "late Kishore Lal Mathur" (the first word must not be an English word: "late Monday" is not a name).
    A run of capitalised words loses trailing role, document, time and function words and verb forms ("Kiran Bhatt
    Joined" -> "Kiran Bhatt", lexicon.ends_name) and must keep at least one word that is not an English word.
    """
    out: list[Detection] = []
    if len(text) > 200_000:
        return out
    for m in _HON_CHAIN.finditer(text):
        if m.end() == m.start() or not text[m.start():m.end()].strip():
            continue
        toks = _trim(text, _cap_run(text, m.end()), lead_ok=True)
        if toks:
            out.append(_det(toks[0][0], toks[-1][1], 0.85, "D2:person_honorific"))
    for m in _KIN.finditer(text):
        p = m.end()
        late = _LATE.match(text, p)
        if late:
            p = late.end()
        hon = _HON_CHAIN.match(text, p)
        if hon and hon.end() > p:
            p = hon.end()
        toks = _trim(text, _cap_run(text, p), lead_ok=bool(hon and hon.end() > hon.start()))
        if not toks and ("/" in m.group() or "." in m.group()[:4]):
            toks = _low_run(text, p)
        if toks:
            out.append(_det(toks[0][0], toks[-1][1], 0.85, "D2:person_kinship"))
        # the person whose relation is given, written right before the marker
        left = _left_cap_run(text, m.start())
        if left:
            out.append(_det(left[0][0], left[-1][1], 0.8, "D2:person_kinship"))
    for m in _LATE.finditer(text):
        p = m.end()
        hon = _HON_CHAIN.match(text, p)
        honoured = bool(hon and hon.end() > p)
        toks = _cap_run(text, hon.end() if honoured else p)
        if not toks or (not honoured and not _name_word(text[toks[0][0]:toks[0][1]])):
            continue
        toks = _trim(text, toks, lead_ok=honoured)
        if toks:
            out.append(_det(toks[0][0], toks[-1][1], 0.85, "D2:person_late"))
    return out


_LEFT_TOK = re.compile(rf"(?:{_INIT}|{_CAP_WORD}|{_UP_WORD})[ \t]*,?[ \t]*$")
_ONE_CAP = re.compile(rf"{_INIT}|{_CAP_WORD}|{_UP_WORD}")


def _left_cap_run(text: str, end: int, limit: int = 4) -> list[tuple[int, int]]:
    """Capitalised name tokens ending right before `end` (spaces and one comma allowed before `end`)."""
    toks: list[tuple[int, int]] = []
    e = end
    for _ in range(limit):
        m = _LEFT_TOK.search(text, max(0, e - 70), e)
        if not m:
            break
        s = m.start()
        if s > 0 and (text[s - 1].isalnum() or text[s - 1] in "'’-"):
            break
        tok_end = _ONE_CAP.match(text, s).end()
        if toks and "," in text[tok_end:e]:
            break
        toks.insert(0, (s, tok_end))
        e = s
    while toks and not _name_word(text[toks[0][0]:toks[0][1]]):
        toks = toks[1:]
    if not any(_name_word(text[a:b]) and not re.fullmatch(_INIT, text[a:b]) for a, b in toks):
        return []
    return toks


# --- lower-case names after a cue -----------------------------------------------------------------------------------
# A lower-case word that is not an English word, in a position where a name is expected. One word suffices after a
# strong cue (greeting, sign-off, honorific, a verb addressed to a person, "manager is"); a weak cue (to, with, from,
# by, for, about, and, of) needs two such words. A list continues with "and", "or", "&" or a comma.
_STRONG_CUE = re.compile(
    r"(?:(?:^|(?<=[\n.!?;]))[ \t]*(?i:hi+|hello|hey|dear|hlo|helo|good[ \t]+(?:morning|afternoon|evening))[ \t,]+"
    r"|(?i:regards|thanks|thank[ \t]+you|thx|cheers|sincerely|best|warmly)[ \t]*[,.!]?[ \t]*\n[ \t]*"
    r"|(?<![\w'’\-])(?i:mr|mrs|ms|dr|shri|smt|sri)(?:\.[ \t]*|[ \t]+)"
    r"|(?<![\w'’\-])(?i:ask|asked|ping|pinged|tell|told|inform|informed|remind|reminded|cc|call|called|message|"
    r"messaged|contact|contacted|met|meet|loop[ \t]+in|looped[ \t]+in|spoke[ \t]+(?:to|with)|speak[ \t]+(?:to|with)|"
    r"talked[ \t]+(?:to|with)|talk[ \t]+(?:to|with)|check[ \t]+with|checked[ \t]+with|connect[ \t]+with|"
    r"reach[ \t]+out[ \t]+to|escalate[ \t]+to|escalated[ \t]+to|assign[ \t]+to|assigned[ \t]+to|forward[ \t]+to|"
    r"forwarded[ \t]+to|approved[ \t]+by|raised[ \t]+by|submitted[ \t]+by|reported[ \t]+by)[ \t:]+"
    r"|(?<![\w'’\-])(?i:manager|reportee|colleague|candidate|employee|nominee|spouse|father|mother|husband|wife|"
    r"guardian|referee|referrer|approver|buddy|mentor)[ \t]+(?i:is|was|named|called|-|:)?[ \t]*)")
_WEAK_CUE = re.compile(r"(?<![\w'’\-])(?i:to|with|from|by|for|about|and|of)[ \t]+")
_LIST_SEP = re.compile(r"[ \t]*(?:,|&|\band\b|\bor\b)[ \t]*")
_POSSESSIVE_NOUN = re.compile(
    r"['’]s[ \t]+(?i:salary|ctc|pay|payslip|leave|leaves|reimbursement|claim|bonus|appraisal|rating|resignation|"
    r"offer|joining|exit|transfer|file|case|account|pf|uan|aadhaar|pan|dob|address|phone|mobile|number|email|"
    r"manager|team|form|documents?|request|ticket|appeal|loan|advance|increment|arrears|settlement|attendance)\b")


def lowercase_cues(text: str) -> list[Detection]:
    """Lower-case names in a name position (rules above). Text in any case; only lower-case tokens are taken."""
    out: list[Detection] = []
    if len(text) > 200_000 or not re.search(r"[a-z]", text):
        return out

    def take(p: int, need: int, score: float) -> None:
        toks = _low_run(text, p)
        if len(toks) < need:
            return
        out.append(_det(toks[0][0], toks[-1][1], score, "D2:person_lower"))
        q = toks[-1][1]
        for _ in range(5):                       # "anirudh or kshitij", "ravi, suresh and meena"
            sep = _LIST_SEP.match(text, q)
            if not sep or sep.end() == q:
                break
            more = _low_run(text, sep.end())
            if not more:
                break
            out.append(_det(more[0][0], more[-1][1], score, "D2:person_lower"))
            q = more[-1][1]

    for m in _STRONG_CUE.finditer(text):
        take(m.end(), 1, 0.75)
    for m in _WEAK_CUE.finditer(text):
        take(m.end(), 2, 0.7)
    for m in _POSSESSIVE_NOUN.finditer(text):    # "venkatesh raghavan's reimbursement", "rohit's payslip"
        e = m.start()
        toks: list[tuple[int, int]] = []
        while len(toks) < 3:
            w = re.search(r"(?<![\w'’@.\-])[a-z]{3,30}[ \t]*$", text[max(0, e - 40):e])
            if not w:
                break
            a = max(0, e - 40) + w.start()
            b = a + len(w.group().rstrip(" \t"))
            if not _low_name_word(text[a:b]):
                break
            toks.insert(0, (a, b))
            e = a
        if toks:
            out.append(_det(toks[0][0], toks[-1][1], 0.75, "D2:person_lower"))
    return out


# --- a word next to a known name joins it (class 1) -----------------------------------------------------------------
_RIGHT = re.compile(rf"[ \t]{{1,2}}({_CAP_WORD}|{_UP_WORD})(?=['’]s(?![\w])|(?![\w'’\-]|\.\w))")
_LEFT = re.compile(rf"(?<![\w'’.\-])({_INIT}|{_CAP_WORD}|{_UP_WORD})[ \t]{{1,2}}$")


def _same_case_ok(word: str, name: str) -> bool:
    """An all-caps word joins only an all-caps name ("PRIYA DESHMUKH"), a Title-case word any name."""
    if word.isupper() and len(word) > 1:
        return name.isupper()
    return True


def expand(text: str, dets: list[Detection]) -> list[Detection]:
    """Grow every PERSON span over the name words written right next to it ("Shalini" before an aliased "Phadnis",
    a middle name between two detected parts, a surname after a detected first name) and merge PERSON spans that
    the growth makes adjacent. A word joins only if it is capitalised like the name, is not an English word, an
    honorific or a place, and is not part of another detection. Spans of other types are never touched."""
    if not any(d.type is T.PERSON for d in dets):
        return dets
    dets = sorted(dets, key=lambda d: d.start)
    out: list[Detection] = []
    for i, d in enumerate(dets):
        if d.type is not T.PERSON:
            out.append(d)
            continue
        lo = dets[i - 1].end if i > 0 else 0                    # spans are disjoint: only neighbours can block
        hi = dets[i + 1].start if i + 1 < len(dets) else len(text)
        s, e = d.start, d.end
        name = text[s:e]
        for _ in range(3):
            m = _LEFT.search(text, max(lo, s - 48), s)
            if not m:
                break
            w = m.group(1)
            if not (_name_word(w) and _same_case_ok(w, name) and not _place(w)):
                break
            s = m.start(1)
        for _ in range(3):
            m = _RIGHT.match(text, e, hi)
            if not m:
                break
            w = m.group(1)
            if not (_name_word(w) and _same_case_ok(w, name) and not _place(w)):
                break
            e = m.end(1)
        if out and out[-1].type is T.PERSON and (out[-1].end >= s or _GAP.fullmatch(text[out[-1].end:s])):
            prev = out[-1]
            joined = text[prev.start:max(prev.end, e)].split()
            if len(joined) <= _MAX_NAME_WORDS and len({w.casefold() for w in joined}) == len(joined):
                out.pop()                        # adjacent PERSON spans form one name ("Abhay" + "Ramchandra Deshmukh")
                out.append(Detection(prev.start, max(prev.end, e), T.PERSON, max(prev.score, d.score), prev.source))
                continue
            s = max(s, prev.end)
        out.append(Detection(s, e, d.type, d.score, d.source) if (s, e) != (d.start, d.end) else d)
    return out


def _place(word: str) -> bool:
    return is_place(word)


# --- signature continuation ---------------------------------------------------------------------------------------
_LINE_NAME = re.compile(rf"\n[ \t]*({_CAP_WORD}(?:[ \t]{{1,2}}{_CAP_WORD})?)[ \t]*(?=\n|$)")


def signature_lines(text: str, dets: list[Detection]) -> list[Detection]:
    """A PERSON that fills its own line, followed by a line of one or two name words: the next line continues the
    name (a signature written "Gayatri\\nVenkataraman")."""
    out: list[Detection] = []
    for d in dets:
        if d.type is not T.PERSON:
            continue
        ls = text.rfind("\n", 0, d.start) + 1
        le = text.find("\n", d.end)
        le = len(text) if le == -1 else le
        if text[ls:d.start].strip() or text[d.end:le].strip(" \t,."):
            continue
        m = _LINE_NAME.match(text, le)
        if not m:
            continue
        toks = [t for t in m.group(1).split()]
        if all(_name_word(t) and not _place(t) for t in toks):
            out.append(_det(m.start(1), m.end(1), d.score, "D2:person_signature"))
    return out


# --- table columns of names (class 2) -------------------------------------------------------------------------------
_NAME_HEAD = frozenset("""name names employee employees emp staff worker associate person member manager managers
reportee reportees approver approvers nominee nominees spouse father mother guardian husband wife dependent dependant
candidate candidates applicant applicants reviewer interviewer interviewers recruiter owner assignee requester
requestor beneficiary payee supervisor lead hrbp buddy mentor referrer referee contact cardholder holder signatory
student trainee intern consultant""".split())
# a header with one of these words describes something else ("Employee ID", "Bank Name", "Manager Email")
_NOT_NAME_HEAD = frozenset("""id ids no number num code bank branch department dept project file company org
organisation organization team product client vendor city location place site designation title role grade band
level status email mail phone mobile date dob type count total amount salary ctc pay account ifsc pan aadhaar uan
address group unit division category relation relationship age gender share percentage rating score slot time
days leave request policy course batch shift desk office building floor country state""".split())
_PLACEHOLDER = frozenset("n/a na nil none null - -- tbd tba vacant unknown self same yes no nan".split())
_CELL_NAME = re.compile(r"[^\W\d_][\w'’.\- ]{0,58}[^\W\d_.]\.?|[^\W\d_]{2}", re.UNICODE)
_SEP_ROW = re.compile(r"[ \t|:+\-=]+")
_MAX_TABLE_LINES = 5000


def _cells(line: str, delim: str) -> list[tuple[int, int]]:
    """(start, end) of each cell, quotes stripped (a quoted CSV cell may hold the delimiter)."""
    out: list[tuple[int, int]] = []
    i, n = 0, len(line)
    while i <= n:
        if delim == "," and i < n and line[i] == '"':
            j = line.find('"', i + 1)
            while j != -1 and j + 1 < n and line[j + 1] == '"':
                j = line.find('"', j + 2)
            if j == -1:
                return []
            out.append((i + 1, j))
            k = line.find(delim, j + 1)
            if k == -1:
                break
            i = k + 1
            continue
        k = line.find(delim, i)
        if k == -1:
            out.append((i, n))
            break
        out.append((i, k))
        i = k + 1
    trimmed = []
    for a, b in out:
        while a < b and line[a] in ' \t"':
            a += 1
        while b > a and line[b - 1] in ' \t"':
            b -= 1
        trimmed.append((a, b))
    return trimmed


def _header_is_name(h: str) -> bool:
    words = re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", h).lower().replace("'s", ""))
    return bool(words) and any(w in _NAME_HEAD for w in words) and not any(w in _NOT_NAME_HEAD for w in words)


def _name_cell(cell: str) -> bool:
    c = cell.strip()
    if not c or c.lower() in _PLACEHOLDER or not _CELL_NAME.fullmatch(c) or len(c.split()) > 5:
        return False
    return not all(is_english(w) for w in re.findall(r"[^\W\d_]+", c))


def table_columns(text: str, dets: list[Detection]) -> list[Detection]:
    """Cells of a name column in a table (CSV, TSV, semicolon, pipe or Markdown). A column is a name column when its
    header names people ("Emp Name", "Employee", "Manager", "Father's Name"; not "Employee ID", "Bank Name") or when
    at least half of its non-empty cells, and at least two, are already detected as PERSON. Every name-shaped cell of
    such a column is a PERSON, except a cell of English words only ("Vacant", "Analyst") or a placeholder ("N/A").
    A header cell is short (at most four words, no digits)."""
    if "\n" not in text or not any(c in text for c in ",\t|;"):
        return []
    lines: list[tuple[int, int]] = []
    pos = 0
    for ln in text.split("\n", _MAX_TABLE_LINES)[:_MAX_TABLE_LINES]:
        lines.append((pos, pos + len(ln)))
        pos += len(ln) + 1
    persons = sorted((d.start, d.end) for d in dets if d.type is T.PERSON)
    pstarts = [p[0] for p in persons]

    def covered(a: int, b: int) -> bool:
        i = bisect_left(pstarts, b) - 1
        while i >= 0 and persons[i][1] > a:
            if persons[i][0] < b:
                return True
            i -= 1
        return False

    out: list[Detection] = []
    i = 0
    while i < len(lines):
        s, e = lines[i]
        line = text[s:e]
        delim = next((c for c in ("\t", "|", ";", ",") if line.count(c) >= 1), None)
        if delim is None:
            i += 1
            continue
        block: list[list[tuple[int, int]]] = []
        width = None
        j = i
        while j < len(lines):
            ls, le = lines[j]
            ln = text[ls:le]
            if not ln.strip():
                break
            if delim == "|" and _SEP_ROW.fullmatch(ln):
                j += 1
                continue                          # Markdown separator row
            cells = _cells(ln, delim)
            if delim == "|" and cells and cells[0][0] == cells[0][1]:
                cells = cells[1:]                 # "| a | b |": empty cells outside the outer pipes
            if delim == "|" and cells and cells[-1][0] == cells[-1][1]:
                cells = cells[:-1]
            if len(cells) < 2 or (width is not None and len(cells) != width):
                break
            width = len(cells)
            block.append([(ls + a, ls + b) for a, b in cells])
            j += 1
        if len(block) < 2:
            i = max(j, i + 1)
            continue
        head = [text[a:b] for a, b in block[0]]
        header_row = all(h and len(h) <= 40 and len(h.split()) <= 4 and not any(ch.isdigit() for ch in h)
                         for h in head) and \
            (not any(covered(a, b) for a, b in block[0]) or all(not _name_cell(h) for h in head))
        rows = block[1:] if header_row else block
        for k in range(width):
            col = [r[k] for r in rows if r[k][1] > r[k][0]]
            by_head = header_row and _header_is_name(head[k])
            if not by_head:
                hits = sum(1 for a, b in col if covered(a, b) and _name_cell(text[a:b]))
                if hits < 2 or hits * 2 < len(col):
                    continue
            for a, b in col:
                if not covered(a, b) and _name_cell(text[a:b]):
                    out.append(_det(a, b, 0.85, "D2:person_table"))
        i = j
    return out


# --- lower-case view for roster single names ------------------------------------------------------------------------
_LOWER_WORD = re.compile(r"(?<![\w@./\-])[a-z]{3,30}(?![\w@]|[.\-][a-z])")


def titled_unknown(text: str) -> str | None:
    """`text` with every lower-case word that is not an English word title-cased (same length, so offsets carry
    over), or None if nothing changed. Lets the roster's single-name rule, which needs a capital, see "ask devraj"."""
    chars = None
    for m in _LOWER_WORD.finditer(text):
        if _low_name_word(m.group()):
            if chars is None:
                chars = list(text)
            chars[m.start()] = chars[m.start()].upper()
    return None if chars is None else "".join(chars)
