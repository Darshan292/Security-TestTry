"""Postal addresses (house/flat number with street and locality words) and Indian PIN codes in an address context.

Type: ADDRESS (class C1: an address identifies where a person lives, so it is aliased and restored like a name; one
alias per detected span, however many lines the original had). Score 0.8: above the shape
backstop (0.4) and statistical NER (which tags building and locality names as PERSON and would otherwise split the
address), below the scheme recognizers.

What is detected
- The street part of an address line: from a house marker ("Flat 302", "House No. 17", "Door No 5-9-112", "#45",
  "Plot 44") or a bare house number right after an address cue ("lives at 23/B, ...", "Address: 12, ..."), through
  the comma-separated segments that follow while they name a building, street or locality ("Sai Krupa Apartments",
  "4th Cross", "Malleswaram"); it stops at a city or state name, a PIN, or ordinary prose. A house number alone
  ("Room 204 on the 3rd floor") is not an address: a street or locality word must follow.
- A PIN code: six digits, first digit 1-9, optionally written "560 003", when labelled (PIN, pincode, postal code,
  zip) or written right after a city or state name ("Bengaluru 560003", "Nagpur - 440013") or at the end of a
  comma-separated address line.
City and state names are general geographic knowledge (the states and union territories, and large cities with
their common older spellings); the street vocabulary is the ordinary vocabulary of Indian addresses.

Multi-line: after a street part that ends its line, up to three following lines join it while each is itself a run of
address segments (a street or locality word, no prose); spans that touch or are separated only by commas, dashes and
line breaks merge into one ADDRESS. The city or state name before a PIN stays outside the span (a city is not what
identifies a residence); the PIN is its own span. Nothing else is inferred: a locality line with no house marker, street word or PIN is not recognised here; the ML detector's address label (profile
`labels`) covers free-form addresses.

PIN block (opt-in, `pin_block`): without an ML address label, a PIN that is accepted as above also masks the contiguous
address block it ends, found by STRUCTURE only (no street-name lists): the comma-separated segments to its left on the
same line while each is a short run of capitalised/numeric words, cut at a sentence boundary or a label colon (the
last, partly prose segment contributes its trailing capitalised run), and up to three preceding lines that end in a
comma/dash or are themselves short capitalised-or-numeric lines. See docs/ML_ARCHITECTURE.md, "Address coverage".

Linear: one pass of bounded regexes; each candidate looks at most one line (capped at 300 characters) around it.
"""
from __future__ import annotations

import bisect
import re
from contextlib import contextmanager
from contextvars import ContextVar

from pgw.types import EntityType as T

SOURCE = "D2:address"
SCORE = 0.8

_STATES = """andhra pradesh|arunachal pradesh|assam|bihar|chhattisgarh|goa|gujarat|haryana|himachal pradesh|jharkhand|
karnataka|kerala|madhya pradesh|maharashtra|manipur|meghalaya|mizoram|nagaland|odisha|orissa|punjab|rajasthan|
sikkim|tamil nadu|telangana|tripura|uttar pradesh|uttarakhand|uttaranchal|west bengal|andaman and nicobar islands|
chandigarh|dadra and nagar haveli|daman and diu|delhi|new delhi|jammu and kashmir|jammu|kashmir|ladakh|lakshadweep|
puducherry|pondicherry|ncr"""
_CITIES = """mumbai|bombay|navi mumbai|thane|pune|nagpur|nashik|aurangabad|solapur|kolhapur|satara|sangli|amravati|
bengaluru|bangalore|mysuru|mysore|mangaluru|mangalore|hubli|dharwad|belagavi|belgaum|chennai|madras|coimbatore|
madurai|tiruchirappalli|trichy|salem|tirunelveli|vellore|hyderabad|secunderabad|warangal|vijayawada|visakhapatnam|
vizag|guntur|tirupati|nellore|kochi|cochin|ernakulam|thiruvananthapuram|trivandrum|kozhikode|calicut|thrissur|
kolkata|calcutta|howrah|durgapur|siliguri|asansol|ahmedabad|surat|vadodara|baroda|rajkot|gandhinagar|bhavnagar|
jamnagar|jaipur|jodhpur|udaipur|kota|ajmer|bikaner|lucknow|kanpur|noida|greater noida|ghaziabad|agra|varanasi|
prayagraj|allahabad|meerut|bareilly|aligarh|gorakhpur|gurugram|gurgaon|faridabad|panipat|ambala|chandigarh|mohali|
ludhiana|amritsar|jalandhar|patiala|bhopal|indore|gwalior|jabalpur|ujjain|raipur|bhilai|bilaspur|ranchi|jamshedpur|
dhanbad|bokaro|patna|gaya|muzaffarpur|bhubaneswar|cuttack|rourkela|guwahati|dibrugarh|shillong|imphal|agartala|
aizawl|kohima|itanagar|gangtok|dehradun|haridwar|rishikesh|shimla|srinagar|leh|panaji|margao|vasco|daman|silvassa|
port blair|kavaratti|puducherry"""
_PLACE_SET = frozenset(w.strip() for w in (_STATES + "|" + _CITIES).replace("\n", "").split("|"))
_TAIL_WORDS = re.compile(r"[A-Za-z]+")


def is_place(word: str) -> bool:
    return word.lower() in _PLACE_SET


def _ends_with_place(s: str) -> bool:
    """`s` ends with a city or state name (one to three words), ignoring trailing spaces, commas and dashes."""
    s = s.rstrip(" \t,-–")
    if not s or not s[-1].isalpha():
        return False
    words = _TAIL_WORDS.findall(s[-40:])
    return any(" ".join(words[-k:]).lower() in _PLACE_SET for k in (1, 2, 3) if len(words) >= k)


def _is_place_seg(seg: str) -> bool:
    """A segment that is a city or state, optionally followed by a PIN ("Delhi 110085", "Bangalore-560038")."""
    seg = _PIN_TAIL.sub("", seg).strip(" \t.,-–")
    return bool(seg) and " ".join(_TAIL_WORDS.findall(seg)).lower() in _PLACE_SET


# Street, building and locality words of Indian addresses
_LOCALITY = r"""road|rd|street|st|lane|ln|marg|path|nagar|nagara|colony|layout|cross|main|sector|phase|block|
apartments?|apts?|society|soc|residency|residences?|enclave|vihar|puram|pet|peth|halli|wadi|wada|extension|extn|
stage|towers?|heights|complex|villas?|chawl|mohalla|village|vill|post|taluka?|tehsil|district|dist|circle|chowk|
bazaa?r|market|park|gardens?|avenue|ave|bagh|ganj|pura|kunj|nivas|niwas|bhavan|bhawan|sadan|building|bldg|mansion|
gali|galli|highway|hwy|bypass|junction|township|estate|plaza|arcade|chambers|nilayam|illam|mahal|kutir|dham|niketan|kendra|cottage|bungalow|quarters|qtrs|lines|cantonment|cantt"""
_LOCALITY_RE = re.compile(r"(?<![A-Za-z])(?i:" + _LOCALITY.replace("\n", "") + r")(?![A-Za-z])")

_HNUM = r"(?:[A-Za-z]{1,2}[ \t]?[-/][ \t]?)?\d{1,5}[A-Za-z]?(?:[ \t]?[-/][ \t]?[A-Za-z0-9]{1,5}){0,3}"
_HOUSE = re.compile(
    r"(?<![\w#])(?:(?i:flat|house|h[ \t]?\.?[ \t]?no|door|plot|apt|apartment|unit|bungalow|villa|quarter|qtr|"
    r"shop|block|bldg|building|wing|survey|s[ \t]?\.?[ \t]?no|ward|khasra)\.?[ \t]*(?i:no|number|num)?\.?[ \t]*"
    r"[:#\-]?[ \t]*|#[ \t]?)(?P<n>" + _HNUM + r")(?![\w])")
_ADDR_CUE = re.compile(
    r"(?i:address|residence|residing[ \t]+at|resides[ \t]+at|resident[ \t]+of|lives[ \t]+at|living[ \t]+at|"
    r"staying[ \t]+at|stays[ \t]+at|located[ \t]+at|ship(?:ped)?(?:[ \t]+it)?[ \t]+to|courier(?:ed)?(?:[ \t]+\w+){0,2}"
    r"[ \t]+to|deliver(?:ed)?(?:[ \t]+\w+){0,2}[ \t]+to|send(?:[ \t]+\w+){0,2}[ \t]+to|moved[ \t]+to|c/o)"
    r"[ \t]*(?::|-|is)?[ \t]*(?:is[ \t]+|at[ \t]+)?")
_BARE_HOUSE = re.compile(r"(?P<n>" + _HNUM + r")(?=[ \t]*,)")

_PIN = re.compile(r"(?<![\w.,/])(?<![\d\-]-)[1-9]\d{2}[ ]?\d{3}(?![\w]|[.,/\-]\d)")
_PIN_LABEL = re.compile(r"(?i:pin|pincode|pin[ \t]+code|postal[ \t]+code|post[ \t]+code|zip|zip[ \t]+code|p\.?[ \t]?o\.?)"
                        r"[ \t]*(?i:no\.?|number|code)?[ \t]*[:#.\-]?[ \t]*$")
_CURRENCY_BEFORE = re.compile(r"(?:₹|(?<![A-Za-z])(?i:rs\.?|inr|usd))[ \t]*$")
_PIN_TAIL = re.compile(r"[ \t]*[,\-–]?[ \t]*[1-9]\d{2}[ ]?\d{3}[ \t.]*$")


def _line(text: str, pos: int) -> tuple[int, int]:
    """The line around `pos`, at most 300 characters on each side."""
    ls = max(text.rfind("\n", max(0, pos - 300), pos) + 1, pos - 300, 0)
    le = text.find("\n", pos, pos + 300)
    return ls, (min(len(text), pos + 300) if le == -1 else le)


def _street_end(text: str, start: int, num_end: int, le: int) -> int | None:
    """End of the street part that starts with a house number ending at `num_end`, or None if no street or
    locality word follows (then it is not an address)."""
    segs: list[tuple[int, int]] = []
    p = num_end
    while p < le and len(segs) < 8:
        c = text.find(",", p, le)
        e = le if c == -1 else c
        segs.append((p, e))
        if c == -1:
            break
        p = c + 1
    end, seen_locality, stop_place = None, False, False
    for k, (a, b) in enumerate(segs):
        seg = text[a:b].strip(" \t.")
        if k == 0 and not seg:
            continue                                   # "Flat 302," : the number ends the first segment
        if not seg or _is_place_seg(seg) or _PIN.fullmatch(seg):
            stop_place = bool(seg)
            break
        words = seg.split()
        loc = bool(_LOCALITY_RE.search(seg))
        capitalised = all(w[0].isupper() or w[0].isdigit() or w.lower() in ("of", "and", "&") for w in words)
        if not capitalised or len(words) > 6:
            break                                      # prose, not an address segment
        seen_locality |= loc
        end = b if text[b - 1:b] != " " else a + len(text[a:b].rstrip())
        # trailing city name glued to the segment ("Indiranagar, Bangalore-560038") is handled by the PIN rule
    if not (seen_locality or stop_place):
        return None
    if end is None:
        end = num_end
    while end > start and text[end - 1] in " \t.":
        end -= 1
    return end


_LINE_BREAK = re.compile(r"[ \t]*,?[ \t]*\r?\n[ \t]*")


def _next_lines(text: str, end: int) -> int:
    """An address written over several lines: while the street part ends its line, the next line (at most three more)
    joins it if it is itself a run of address segments (a street or locality word, no prose)."""
    for _ in range(3):
        m = _LINE_BREAK.match(text, end)
        if not m:
            break
        _, le = _line(text, m.end())
        e = _street_end(text, m.end(), m.end(), le)
        if e is None or e <= m.end():
            break
        end = e
    return end


_JOIN = re.compile(r"[ \t,\-–\r\n]*")


def _merge(text: str, spans: list) -> list:
    """Sorted spans; overlapping ones, and ones separated only by commas, dashes and line breaks, become one."""
    out: list = []
    for s, e, t, sc, src in sorted(spans):
        if out and s - out[-1][1] <= 12 and (s <= out[-1][1] or _JOIN.fullmatch(text[out[-1][1]:s])):
            out[-1] = (out[-1][0], max(e, out[-1][1]), t, sc, src)
        else:
            out.append((s, e, t, sc, src))
    return out


# --- PIN block ------------------------------------------------------------------------------------------------------
_PIN_BLOCK: ContextVar[bool] = ContextVar("pgw_pin_block", default=False)


@contextmanager
def pin_block(enabled: bool):
    """Scope in which an accepted PIN also masks the address block it ends (the Protector sets it in pin_only mode)."""
    tok = _PIN_BLOCK.set(bool(enabled))
    try:
        yield
    finally:
        _PIN_BLOCK.reset(tok)


_BLOCK_LINES = 3                 # preceding lines that may join the block
_BLOCK_SEGMENTS = 8              # comma segments examined per line
_BLOCK_WORDS = 7                 # words in a segment that is taken whole
_CONNECT = frozenset({"of", "and", "&", "near", "nr", "nr.", "opp", "opp.", "opposite", "behind", "beside", "off",
                      "adjacent", "adj", "adj."})
_SENT_END = re.compile(r"(?:(?<=[a-z]{3})[.!?;]|[:])[ \t]+|[:][ \t]*$|(?<=[a-z]{3})[.!?;]$")
_NOT_ADDRESS_WORD = re.compile(r"[\"'(){}\[\]<>@=|]|https?:|www\.")


def _addr_word(w: str) -> bool:
    if _NOT_ADDRESS_WORD.search(w):
        return False
    return w[0].isupper() or w[0].isdigit() or w[0] == "#" or w.lower() in _CONNECT


def _trailing_run(words: list[str]) -> int:
    """How many words at the end of `words` are address words."""
    n = 0
    for w in reversed(words):
        if not _addr_word(w):
            break
        n += 1
    return n


def _scan_back(text: str, ls: int, end: int) -> tuple[int, bool]:
    """Start of the address block inside text[ls:end] (one line, ending where the PIN or the next block piece starts):
    (offset, reached) with reached True if the whole line belongs to it (so the line above may join)."""
    seg_end, start, n = end, end, 0
    while seg_end > ls and n < _BLOCK_SEGMENTS:
        c = text.rfind(",", ls, seg_end)
        seg_start = c + 1 if c != -1 else ls
        seg = text[seg_start:seg_end]
        stripped = seg.strip(" \t-–")
        if stripped:
            lead = len(seg) - len(seg.lstrip(" \t-–"))
            cut = 0
            for m in _SENT_END.finditer(stripped):
                cut = m.end()
            tail = stripped[cut:]
            words = tail.split()
            k = _trailing_run(words) if words else 0
            if cut == 0 and words and k == len(words) <= _BLOCK_WORDS:
                start = seg_start + lead                  # the whole segment is address words
            else:
                if k == 0:
                    return start, False
                # start of the k-th word from the end of the segment
                pos = seg_start + lead + cut
                idx = [m.start() for m in re.finditer(r"\S+", tail)]
                start = pos + idx[len(words) - k]
                return start, False
        seg_end = seg_start - 1 if c != -1 else ls
        n += 1
        if c == -1:
            return start, True
    return start, seg_end <= ls


_HOUSE_START = re.compile(r"(?:#[ \t]?)?(?:[A-Za-z]{1,2}-)?\d{1,5}[A-Za-z]?(?:[/-]\w+)*(?=[ \t]*,|[ \t]+[A-Z])|(?:" +
                          _HOUSE.pattern + r")")


def _block_start(text: str, pin_at: int) -> int:
    ls, _ = _line(text, pin_at)
    before = _PIN_LABEL.sub("", text[ls:pin_at])
    end = ls + len(before)
    start, reached = _scan_back(text, ls, end)
    if start >= end:
        start = pin_at                                # nothing before the PIN (its label stays outside, as before)
    for _ in range(_BLOCK_LINES):
        if not reached or ls == 0 or _HOUSE_START.match(text, start):
            break                                     # a block that starts with a house number starts there
        pend = ls - 1 - (1 if text[ls - 2:ls - 1] == "\r" else 0)
        pls = text.rfind("\n", max(0, pend - 300), pend) + 1
        pls = max(pls, pend - 300, 0)
        body = text[pls:pend].rstrip(" \t")
        if not body.strip() or body.endswith((":", ".", "!", "?")):
            break
        words = body.strip(" \t,-–").split()
        if not body.endswith((",", "-", "–")) and not (
                words and len(words) <= 8 and all(_addr_word(w) for w in words) and ":" not in body):
            break
        s2, reached = _scan_back(text, pls, pls + len(body))
        if s2 >= pls + len(body):        # nothing of the line belongs to the block
            break
        start, ls = s2, pls
    return start


_WORD = re.compile(r"[^\s,\-–]+")


def _block_spans(text: str, pin_at: int, pin_end: int) -> list[tuple[int, int]]:
    """The PIN plus the block it ends, without the city or state written just before the PIN (a city is not what
    identifies a residence, as in the rule-based spans); a block that is only that city is dropped."""
    start = _block_start(text, pin_at)
    words = [m for m in _WORD.finditer(text, start, pin_at)]
    cut = pin_at
    for k in (3, 2, 1):
        if len(words) >= k and " ".join(w.group() for w in words[-k:]).lower() in _PLACE_SET:
            cut = words[-k].start()
            break
    left = text[start:cut].rstrip(" \t\r\n,-–")
    return ([(start, start + len(left))] if left else []) + [(pin_at, pin_end)]


def clip_blocks(cands: list) -> list:
    """Candidates with every extended ADDRESS span cut back from the left until it no longer overlaps a stronger
    candidate (a field-value or scheme span). Without this the stronger span wins the overlap and the whole address,
    PIN included, would be dropped by the greedy resolver. Only used in `pin_block` scope."""
    if not _PIN_BLOCK.get():
        return cands
    strong = sorted((c[0], c[1]) for c in cands if c[3] > SCORE)
    if not strong:
        return cands
    starts = [a for a, _ in strong]
    out = []
    for c in cands:
        if c[4] == SOURCE and c[3] == SCORE and c[1] - c[0] > 6:
            lo = c[0]
            # strong spans that start before this one ends and end after this one starts
            i = bisect.bisect_left(starts, c[1]) - 1
            while i >= 0 and starts[i] >= c[0] - 400:
                a, b = strong[i]
                if b > lo and b < c[1]:
                    lo = b
                i -= 1
            if lo != c[0]:
                c = (lo, c[1], c[2], c[3], c[4])
        out.append(c)
    return out


def addresses(text: str) -> list[tuple[int, int, T, float, str]]:
    out: list[tuple[int, int, T, float, str]] = []
    if len(text) > 200_000 or not re.search(r"\d", text):
        return out
    starts: set[int] = set()
    for m in _HOUSE.finditer(text):
        ls, le = _line(text, m.start())
        e = _street_end(text, m.start(), m.end("n"), le)
        if e is not None:
            out.append((m.start(), _next_lines(text, e), T.ADDRESS, SCORE, SOURCE))
            starts.add(m.start())
    for m in _ADDR_CUE.finditer(text):
        b = _BARE_HOUSE.match(text, m.end())
        if not b or b.start() in starts:
            continue
        ls, le = _line(text, b.start())
        e = _street_end(text, b.start(), b.end("n"), le)
        if e is not None:
            out.append((b.start(), _next_lines(text, e), T.ADDRESS, SCORE, SOURCE))
    for m in _PIN.finditer(text):
        a = m.start()
        before = text[max(0, a - 60):a]
        if _CURRENCY_BEFORE.search(before):
            continue
        ok = bool(_PIN_LABEL.search(before) or _ends_with_place(before))
        if not ok:
            ls, _ = _line(text, a)
            line = text[ls:a]
            ok = line.count(",") >= 2 and bool(re.search(r",[ \t]*[A-Z][\w .\-]*[ \t,\-–]*$", line)) and \
                bool(_LOCALITY_RE.search(line) or _ends_with_place(line))
        if ok:
            for s0, e0 in _block_spans(text, a, m.end()) if _PIN_BLOCK.get() else [(a, m.end())]:
                out.append((s0, e0, T.ADDRESS, SCORE, SOURCE))
    return _merge(text, out)
