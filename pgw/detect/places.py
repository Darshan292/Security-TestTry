"""Postal addresses (house/flat number with street and locality words) and Indian PIN codes in an address context.

Type: IDENTIFIER (pgw.types has no address type; an address identifies a person's residence and is withheld like
the shape backstop's identifiers: REDACTed on the shipped contracts, never restored). Score 0.8: above the shape
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

Linear: one pass of bounded regexes; each candidate looks at most one line (capped at 300 characters) around it.
"""
from __future__ import annotations

import re

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


def addresses(text: str) -> list[tuple[int, int, T, float, str]]:
    out: list[tuple[int, int, T, float, str]] = []
    if len(text) > 200_000 or not re.search(r"\d", text):
        return out
    starts: set[int] = set()
    for m in _HOUSE.finditer(text):
        ls, le = _line(text, m.start())
        e = _street_end(text, m.start(), m.end("n"), le)
        if e is not None:
            out.append((m.start(), e, T.IDENTIFIER, SCORE, SOURCE))
            starts.add(m.start())
    for m in _ADDR_CUE.finditer(text):
        b = _BARE_HOUSE.match(text, m.end())
        if not b or b.start() in starts:
            continue
        ls, le = _line(text, b.start())
        e = _street_end(text, b.start(), b.end("n"), le)
        if e is not None:
            out.append((b.start(), e, T.IDENTIFIER, SCORE, SOURCE))
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
            out.append((m.start(), m.end(), T.IDENTIFIER, SCORE, SOURCE))
    return out
