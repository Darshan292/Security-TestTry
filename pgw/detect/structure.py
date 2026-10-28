"""Record-structured regions in free text: tables, exports and record payloads pasted into a ticket.

Free text crosses to an external model only after detection has replaced what it recognised. Detection is built for
prose; a record export (a payroll register, a CSV, an HRIS API payload, a directory dump) is different in kind: its
values sit in columns or under keys with no sentence context, names are often ALL-CAPS cells, and one paste carries
many people. Such content does not belong on the free-text external path at all (SECURITY_BOUNDARY.md §11). This
module finds it, format-agnostically: it reads the SHAPE of the text, never vendor names, field names or values.

Detectors (each linear in the input; nesting and look-back are capped):
  delimited     consecutive lines with the same number (>= 3) of comma / tab / pipe / semicolon cells (quoted CSV
                aware); markdown and ASCII tables (ruler lines are skipped inside a run)
  aligned       consecutive lines split by runs of >= 2 spaces into the same number (>= 3) of cells, or aligned
                cells (fixed-width exports, with or without a header)
  record_lines  consecutive lines with the same token-shape signature carrying >= 2 data tokens (numbers, amounts,
                dates, ids, emails): "48211 SHANTANU DESHMUKH 41250.00 16500.00" is ID WORD AMOUNT AMOUNT
  key_value     runs of "key: value" / "key = value" lines (LDIF, YAML lists of mappings, INI, forms); a repeated key
                or a list item starts a new record. Also logfmt lines with >= 3 key=value pairs.
  json          objects/arrays (also NDJSON, escaped JSON in logs, unquoted keys, truncated or pretty-printed):
                objects inside one array are records, their keys are fields
  xml           repeated sibling elements with >= 3 leaf values each (SOAP, HTML table rows, attribute rosters)

A region is STRUCTURED (the gateway refuses it on the external path) when it holds
  * >= 2 rows/records of >= 3 fields with rows x fields >= MIN_CELLS (a table's header row counts as a row), or
  * one record with >= SINGLE_SYNTAX_FIELDS leaf fields in record syntax (a worker object in JSON/XML, an LDIF entry,
    an INI/YAML block with identifier keys), or >= SINGLE_RECORD_FIELDS fields in a human-written form.
The thresholds were chosen on dev and synthetic data only (docs in SECURITY_BOUNDARY.md §11).

`header_rows` returns the header lines of recognised tables: their cells are field labels, never entities.
"""
from __future__ import annotations

import collections
import re
import statistics
from dataclasses import dataclass, field

__all__ = ["Region", "scan", "find", "find_in", "is_structured", "header_rows", "MIN_FIELDS", "MIN_CELLS", "SINGLE_RECORD_FIELDS",
           "SINGLE_SYNTAX_FIELDS"]

MIN_FIELDS = 3               # a record has at least this many fields
MIN_CELLS = 8                # rows x fields of a multi-record region (2 x 4, 3 x 3, ...)
SINGLE_RECORD_FIELDS = 12    # one human-written record (a form, a table row) with this many fields is an export
SINGLE_SYNTAX_FIELDS = 6     # one record in record syntax (JSON, XML, LDIF/INI/YAML keys) with this many leaf fields
_EXPLICIT = frozenset({"json", "xml", "key_value", "logfmt"})
MAX_LINE = 2000              # a longer line is not a table row (JSON/XML are scanned separately)
_MAX_DEPTH = 200             # JSON/XML frames kept; deeper nesting is counted, not tracked
_MAX_UNWIND = 32             # frames searched for the opener of a mismatched close


@dataclass(frozen=True)
class Region:
    start: int
    end: int
    format: str                  # delimited:<d> | aligned | record_lines | key_value | logfmt | json | xml
    records: int                 # data records (a table's header row excluded)
    fields: int                  # fields per record (median)
    rows: int = 0                # lines of a table including its header (records for other formats)
    keys: tuple = field(default=(), compare=False)    # header cells / keys when present (internal use only)
    header: tuple | None = None  # (start, end) of the header line of a table

    @property
    def structured(self) -> bool:
        rows = max(self.rows, self.records)
        if self.format in _EXPLICIT and self.records >= 2 and self.fields >= MIN_FIELDS:
            return True     # record syntax (JSON, XML, key/value blocks) is unambiguous: no cell minimum
        if self.records >= 1 and rows >= 2 and self.fields >= MIN_FIELDS and rows * self.fields >= MIN_CELLS:
            return True
        single = SINGLE_SYNTAX_FIELDS if self.format in _EXPLICIT else SINGLE_RECORD_FIELDS
        return self.records >= 1 and self.fields >= single

    def summary(self) -> dict:
        """Content-free description (format and counts only): safe to return to the caller and to log."""
        return {"format": self.format.split(":")[0], "records": self.records, "fields": self.fields}


# ------------------------------------------------------------------------------------------------- lines and tokens
_TOKEN = re.compile(r"\S+")
_EDGE = "\"'()[]{}<>,;:|*•"
_DATE = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}[-/ ]?[A-Za-z]{3,9}[-/ ]?\d{2,4}")
_TIME = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?(?:[aApP][mM])?")
_AMOUNT = re.compile(r"[₹$€£]?-?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|[₹$€£]?-?\d+\.\d+|[₹$€£]\d+")
_INT = re.compile(r"[+-]?\d+%?")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_LIST_MARK = re.compile(r"(?:\d{1,3}[.)]|[a-zA-Z][.)]|[-*•–>]+|#+)$")
_RULER = re.compile(r"[\s\-=_+|:.~*#]*")


class _Lines(list):
    """(start, end) of every line (a trailing \r excluded), plus `raw`: the line texts ("" for a line too long to be a
    table row)."""
    raw: list


def _lines(text: str) -> _Lines:
    parts = text.split("\n")
    out, raw, pos = _Lines(), [], 0
    for ln in parts:
        n = len(ln)
        if ln.endswith("\r"):
            ln = ln[:-1]
        out.append((pos, pos + len(ln)))
        raw.append(ln if len(ln) <= MAX_LINE else "")
        pos += n + 1
    out.raw = raw
    return out


def _kind(tok: str) -> str:
    """Token class: W word, H time (not data), T date, D amount, N integer, E email, A alphanumeric id, P punctuation."""
    core = tok.strip(_EDGE)
    if not core:
        return "P"
    if core.isalpha():
        return "W"
    if _TIME.fullmatch(core):
        return "H"
    if _DATE.fullmatch(core):
        return "T"
    if _AMOUNT.fullmatch(core):
        return "D"
    if _INT.fullmatch(core):
        return "N"
    if "@" in core and _EMAIL.fullmatch(core):
        return "E"
    if any(c.isdigit() for c in core) and any(c.isalpha() for c in core):
        return "A"
    if not any(c.isalnum() for c in core):
        return "P"
    return "W"


_DATA = frozenset("TDNEA")
_DIGIT = re.compile(r"\d")
_STAMP = re.compile(r"[\s\[(]*(?:\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}[,\sT]*)?\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?"
                    r"\s*(?:[aApP]\.?[mM]\.?)?[\s\])\-–:]*")
_LABEL_IN_CELL = re.compile(r"(?:^|\s)[A-Za-z][\w .'/()\-]{0,40}: +\S")
_SPACED_DATE = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)? [A-Za-z]{3,9},? \d{4}\b|\b[A-Za-z]{3,9} \d{1,2}(?:st|nd|rd|th)?,? \d{4}\b")


def _signature(line: str) -> tuple[tuple, int, int]:
    """(shape signature with word runs collapsed and punctuation dropped, data tokens, word tokens). A leading list
    marker ("1.", "-", "a)") is not part of the record; short digit groups written apart ("7522 7979 9411",
    "+91 620 581 3630") are one value."""
    if any(c.isdigit() for c in line):
        line = _SPACED_DATE.sub(lambda m: m.group().replace(",", "").replace(" ", "-"), line)
    stamp = _STAMP.match(line)
    rest = line[stamp.end():] if stamp else line
    kv = _KV.match(rest)
    if kv is not None and len(kv.group("key").split()) <= 3 and kv.group("val"):
        # "Referee 1: <values>", "12/03/24, 10:41 pm - Ravi: <message>": the label (and the time stamp of a chat
        # line) is not a field of a record
        line = kv.group("val")
    toks = _TOKEN.findall(line)
    if toks and _LIST_MARK.fullmatch(toks[0]) and len(toks) > 1:
        toks = toks[1:]
    sig: list[str] = []
    data = words = 0
    grp = 0
    for t in toks[:80]:
        k = _kind(t)
        if k == "P":
            grp = 0
            continue
        digits = len(t.strip(_EDGE).lstrip("+"))
        if k == "N" and grp and 2 <= digits <= grp:
            continue                 # the next group of a number written apart
        # a digit group starts a number written apart: after a dialling prefix ("+91", "(+91)") or a trunk zero
        # ("077627 93114") the next group is part of the phone number
        core = t.strip(_EDGE)
        grp = (10 if core[:1] == "+" else 5 if core[:1] == "0" else 4) if k == "N" and 2 <= digits <= 6 else 0
        if k == "W":
            words += 1
            if sig and sig[-1] == "W":
                continue
        data += k in _DATA
        k = "V" if k in "NDA" else k      # a number, an amount, an id: one column may hold any of them
        sig.append(k)
    return tuple(sig), data, words


def _has_data(cell: str) -> bool:
    return any(_kind(t) in _DATA for t in _TOKEN.findall(cell))


def _label_like(cell: str) -> bool:
    """A header cell: a short label with no data token in it."""
    c = cell.strip().strip("\"'")
    return bool(c) and len(c) <= 48 and any(ch.isalpha() for ch in c) and not _has_data(c) \
        and len(c.split()) <= 5 and c[-1:] not in ".?!"


# ------------------------------------------------------------------------------------------------- tables
_DELIMS = (",", "\t", "|", ";")


def _split_quoted(line: str, d: str) -> list[str]:
    """Cells of a delimited line; double-quoted cells may contain the delimiter (CSV)."""
    if '"' not in line:
        return line.split(d)
    cells, cur, q = [], [], False
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c == '"':
            if q and i + 1 < n and line[i + 1] == '"':
                cur.append('"')
                i += 2
                continue
            q = not q
        elif c == d and not q:
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    cells.append("".join(cur))
    return cells


_LIST_ITEM = re.compile(r"\s*(?:\d{1,3}[.)]|[a-zA-Z][.)]|[-*•–])\s+\S")
# a grouped amount written by a person ("Rs 3,86,000", " 1,250.00"): its commas are not cell separators
_GROUPED = re.compile(r"(?<=[\s₹$])\d{1,3}(?:,\d{2,3})+(?:\.\d+)?(?![\d,])")


def _cells(line: str, d: str) -> list[str] | None:
    if d == "|":
        s = line.strip()
        if s.count("|") < 2:
            return None
        s = s[1:] if s.startswith("|") else s
        s = s[:-1] if s.endswith("|") else s
        cells = s.split("|")
    elif d == "\t":
        if line.count("\t") < 2:
            return None
        cells = line.split("\t")
    else:
        if line.count(d) < 2 or _LIST_ITEM.match(line):
            return None      # a bullet or numbered list item is prose formatting, not a delimited row
        if d == "," and "," in line:
            line = _GROUPED.sub(lambda m: m.group().replace(",", "\x00"), line)
        cells = [c.replace("\x00", ",") for c in _split_quoted(line, d)]
        if any(_LABEL_IN_CELL.search(c) for c in cells):
            return None      # "label: text" inside a cell: a message or a form line ("Ravi: ok, will do"), not a row
    return cells if len(cells) >= MIN_FIELDS else None


_GAP = re.compile(r"\S+(?: \S+)*")


def _gap_cells(line: str) -> list[tuple[int, int]] | None:
    """Cells of a line split at runs of two or more spaces (or tabs), as (start, end) columns."""
    if "  " not in line and "\t" not in line:
        return None
    cells = [m.span() for m in _GAP.finditer(line.replace("\t", "  "))]
    return cells if len(cells) >= MIN_FIELDS else None


def _aligned(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    if len(a) == len(b):
        return True
    starts = {s for s, _ in a} | {s + 1 for s, _ in a} | {s - 1 for s, _ in a}
    ends = {e for _, e in a} | {e + 1 for _, e in a} | {e - 1 for _, e in a}
    hit = sum(1 for s, e in b[1:] if s in starts or e in ends)
    return hit >= max(2, (min(len(a), len(b)) - 1) * 0.6)


_SENTENCE = re.compile(r"[a-z][.?!] +[A-Za-z]")


def _proseish(rows: list[list[str]]) -> bool:
    """Comma/semicolon runs of prose (sentences that happen to have the same number of commas)."""
    cells = [c for r in rows for c in r]
    words = [len(c.split()) for c in cells]
    long_ = sum(w > 6 or bool(_SENTENCE.search(c)) for w, c in zip(words, cells, strict=True))
    return statistics.median(words) > 4 or long_ > 0.3 * len(cells)


def _header_index(cell_rows: list[list[str]]) -> bool:
    """True when the first row is a header: every cell a label, and some column holds data in most other rows."""
    if len(cell_rows) < 2 or not all(_label_like(c) for c in cell_rows[0]):
        return False
    k = len(cell_rows[0])
    body = cell_rows[1:]
    for j in range(k):
        col = [r[j] for r in body if j < len(r)]
        if col and sum(_has_data(c) for c in col) * 2 >= len(col):
            return True
    return False


def _table_region(text, lines, idx: list[int], cell_rows: list[list[str]], fmt: str) -> Region:
    hdr = _header_index(cell_rows)
    a, b = lines[idx[0]][0], lines[idx[-1]][1]
    fields = int(statistics.median(len(r) for r in cell_rows))
    keys = tuple(c.strip()[:64] for c in cell_rows[0]) if hdr else ()
    return Region(a, b, fmt, len(idx) - hdr, fields, len(idx), keys,
                  lines[idx[0]] if hdr else None)


def _tables(text: str, lines, out: list[Region]) -> None:
    n = len(lines)
    raw = lines.raw
    ruler = [bool(r) and _RULER.fullmatch(r) is not None and len(r.strip()) >= 3 for r in raw]
    for d in _DELIMS:
        if d not in text:
            continue
        i = 0
        while i < n:
            # a block: consecutive lines that use the delimiter at all (ruler lines inside do not break it)
            if ruler[i] or d not in raw[i]:
                i += 1
                continue
            j = i
            while j < n and (d in raw[j] or ruler[j]):
                j += 1
            members = [k for k in range(i, j) if not ruler[k] and not (d == "|" and _RULER.fullmatch(raw[k]))]
            cells = {k: c for k in members if (c := _cells(raw[k], d)) is not None}
            if len(cells) >= 2:
                k_mode = collections.Counter(len(c) for c in cells.values()).most_common(1)[0][0]
                idx = [k for k in members if k in cells and len(cells[k]) == k_mode]
                # a table's rows agree on their cell count; a chat or a paragraph whose lines happen to contain the
                # delimiter is ragged (the date comma of every chat line, a comma inside some messages)
                rows = [cells[k] for k in idx]
                if len(idx) >= 2 and len(idx) * 5 >= 3 * len(members) and not (d in ",;" and _proseish(rows)):
                    out.append(_table_region(text, lines, idx, [[x.strip() for x in r] for r in rows],
                                             f"delimited:{d}"))
            i = j
    # space-aligned / fixed-width
    i = 0
    while i < n:
        g = _gap_cells(raw[i]) if not ruler[i] else None
        if g is None:
            i += 1
            continue
        idx, spans, j = [i], [g], i + 1
        while j < n:
            if ruler[j]:
                j += 1
                continue
            h = _gap_cells(raw[j])
            if h is None or not _aligned(spans[-1], h):
                break
            idx.append(j)
            spans.append(h)
            j += 1
        if len(idx) >= 2:
            rows = [[raw[k][s:e] for s, e in sp] for k, sp in zip(idx, spans, strict=True)]
            if not _proseish(rows):
                out.append(_table_region(text, lines, idx, rows, "aligned"))
        i = max(j, i + 1)


def _record_lines(text: str, lines, out: list[Region]) -> None:
    """Runs of lines with one token-shape signature (>= 3 fields, >= 2 data tokens): rows of an export whatever the
    separator, including single spaces. A single such row counts under a recognised header line."""
    sigs = []
    for r in lines.raw:
        if not r or _DIGIT.search(r) is None:
            sigs.append(None)
            continue
        sig, data, words = _signature(r)
        # a record line is mostly values: prose lines that happen to share a shape carry many more words than data
        sigs.append(sig if len(sig) >= MIN_FIELDS and data >= 2 and 2 * data > words else None)
    n, i = len(sigs), 0
    while i < n:
        if sigs[i] is None:
            i += 1
            continue
        j = i + 1
        while j < n and sigs[j] == sigs[i]:
            j += 1
        if j - i >= 2 or len(sigs[i]) > MIN_FIELDS:
            hdr = _shape_header(text, lines, i, len(sigs[i]))
            if j - i == 1 and hdr is not None:
                # one row: only under a header whose columns are visibly separated (gaps or a delimiter)
                h = lines.raw[hdr]
                cols = _gap_cells(h) or next((c for d in _DELIMS if (c := _cells(h, d)) is not None), None)
                if cols is None or len(cols) < len(sigs[i]) - 1:
                    hdr = None
            if j - i >= 2 or hdr is not None:
                a = lines[hdr][0] if hdr is not None else lines[i][0]
                out.append(Region(a, lines[j - 1][1], "record_lines", j - i, len(sigs[i]),
                                  j - i + (hdr is not None), (), lines[hdr] if hdr is not None else None))
        i = j


def _shape_header(text: str, lines, first: int, fields: int) -> int | None:
    """The header line above a run of record lines (a ruler line may sit between): no data token, every word a label
    word (capitalised or with _ / #), no sentence punctuation, at least `fields` - 1 words."""
    k = first - 1
    if k >= 0:
        s = text[lines[k][0]:lines[k][1]].strip()
        if len(s) >= 3 and _RULER.fullmatch(s):
            k -= 1
    if k < 0:
        return None
    a, b = lines[k]
    s = text[a:b].strip()
    toks = _TOKEN.findall(s)
    if not toks or len(s) > 300 or len(toks) < max(MIN_FIELDS, fields - 1) or s[-1] in ".?!:,":
        return None
    for t in toks:
        core = t.strip(_EDGE)
        if t[-1:] in ",:;" or not core or _kind(t) in _DATA or not (core[0].isupper() or "_" in core or "#" in core or "/" in core):
            return None
    return k


# ------------------------------------------------------------------------------------------------- key/value records
_KV = re.compile(r"[ \t]*(?P<item>[-*][ \t]+)?(?P<key>[A-Za-z_][\w.()/#&'\- ]{0,40}?)[ \t]*(?:::?|=)(?:[ \t]+|$|(?=[^/=:]))"
                 r"(?P<val>.*)")
_MAX_VALUE_WORDS = 7
_LOGFMT = re.compile(r"(?<![\w.\-])[A-Za-z_][\w.\-]{0,40}=(?:\"[^\"\n]{0,300}\"|[^\s\"]+)")


def _kv_key(line: str) -> tuple[str, bool, bool] | None:
    """(key, starts a list item, written with '=') of a key/value line."""
    m = _KV.match(line)
    if m is None:
        return None
    key = m.group("key").strip()
    if not key or len(key.split()) > 5 or key.lower() in ("http", "https", "ftp", "mailto"):
        return None
    if len(m.group("val").split()) > _MAX_VALUE_WORDS:
        return None          # a label followed by a sentence ("Ticket PAY-12: for PAN the value is ...") is prose
    return key, bool(m.group("item")), line[m.end("key"):m.start("val")].strip()[:1] == "="


# identifier-styled keys (employee_id, telephoneNumber, display.name, bank-ac, addr1): record syntax, not a label a
# person types into a ticket ("Date of Birth", "Room")
_STYLED = re.compile(r"[^ ]*(?:[_.]|[a-z][A-Z]|[A-Za-z]\d|\w-\w)[^ ]*")


_SECTION = re.compile(r"\s*\[[^\[\]\n]{1,64}\]\s*")


def _key_values(text: str, lines, out: list[Region]) -> None:
    n, i = len(lines), 0
    raw = lines.raw
    while i < n:
        if _kv_key(raw[i]) is None:
            i += 1
            continue
        records: list[list[str]] = [[]]
        raw_keys: list[str] = []
        n_eq = n_item = 0
        j, last = i, i
        blank = False
        while j < n:
            s = raw[j]
            if not s.strip() or (_RULER.fullmatch(s) and len(s.strip()) >= 3) or _SECTION.fullmatch(s):
                blank = True       # blank, ruler and [section] lines separate blocks of one region
                j += 1
                continue
            kv = _kv_key(s)
            if kv is None:
                if s[:1] in " \t" and not blank:     # continuation line (LDIF folding, wrapped value)
                    j += 1
                    continue
                break
            key, item, eq = kv
            cur = records[-1]
            raw_keys.append(key)
            n_eq += eq
            n_item += item
            key = key.lower()
            if cur and (item or key in cur or (blank and len(cur) >= MIN_FIELDS)):
                records.append([])
            records[-1].append(key)
            blank = False
            last = j
            j += 1
        recs = [r for r in records if r]
        a, b = lines[i][0], lines[last][1]
        keys = [k for r in recs for k in r]
        # identifier keys (mail, employeeNumber, staff_code) are record syntax; spaced labels are a human form
        styled = sum(bool(_STYLED.fullmatch(k)) for k in raw_keys)
        spaced = sum(" " in k for k in raw_keys)
        # record syntax: identifier-styled keys, "key = value" (INI, .env, properties) or YAML list items; a person
        # filling in a form writes "Label: value"
        syntax = styled * 10 >= 3 * len(raw_keys) or n_eq * 10 >= 7 * len(raw_keys) or n_item >= 2
        fmt = "key_value" if syntax and spaced * 5 <= len(raw_keys) else "form"
        big = [r for r in recs if len(r) >= MIN_FIELDS]
        if len(big) >= 2 and _shared(big):
            out.append(Region(a, b, fmt, len(big), int(statistics.median(len(r) for r in big)), len(big),
                              tuple(dict.fromkeys(keys))[:64]))
        elif len(keys) >= MIN_FIELDS:
            out.append(Region(a, b, fmt, 1, len(set(keys)), 1, tuple(dict.fromkeys(keys))[:64]))
        i = max(j, i + 1)
    # logfmt / key=value log lines
    i = 0
    counts = [len(_LOGFMT.findall(r)) if "=" in r else 0 for r in raw]
    while i < n:
        if counts[i] < MIN_FIELDS:
            i += 1
            continue
        j = i + 1
        while j < n and counts[j] >= MIN_FIELDS:
            j += 1
        out.append(Region(lines[i][0], lines[j - 1][1], "logfmt", j - i,
                          int(statistics.median(counts[i:j])), j - i))
        i = j


def _shared(recs: list[list[str]]) -> bool:
    """Records of one kind share most keys (sections of a single form do not)."""
    for x, y in zip(recs, recs[1:], strict=False):
        sx, sy = set(x), set(y)
        if len(sx & sy) * 2 >= min(len(sx), len(sy)):
            return True
    return False


# ------------------------------------------------------------------------------------------------- JSON
_JTOK = re.compile(r"\"(?:[^\"\\\n]|\\.){0,2000}\"|'[^'\n]{1,64}'(?=\s*:)|[{}\[\],]|:"
                   r"|(?<![\w\"'$.])[A-Za-z_$][\w$\-]{0,40}(?=\s*:)")


class _F:
    __slots__ = ("kind", "start", "keys", "recs", "prev")

    def __init__(self, kind, start):
        self.kind, self.start, self.keys, self.recs, self.prev = kind, start, 0, [], kind


def _json(text: str, out: list[Region]) -> None:
    if "{" not in text and "[" not in text:
        return
    views = [text]
    if '\\"' in text:
        views.append(text.replace('\\"', ' "'))     # escaped JSON inside a log line, same offsets
    for v in views:
        _json_view(v, out)


def _json_view(text: str, out: list[Region]) -> None:
    root = _F("[", 0)
    stack = [root]
    over = 0
    last_end = 0

    def close(f: _F, end: int) -> None:
        parent = stack[-1] if f is not root else _F("[", 0)
        if f.kind == "{":
            parent.keys += f.keys
            if parent.kind == "[":
                parent.recs.append((f.keys, f.start, end))
            if f.keys >= SINGLE_SYNTAX_FIELDS and parent is root:
                out.append(Region(f.start, end, "json", 1, f.keys, 1))
        big = [r for r in f.recs if r[0] >= MIN_FIELDS]
        if len(big) >= 2:
            a, b = (f.start, end) if f is not root else (big[0][1], big[-1][2])
            out.append(Region(a, b, "json", len(big), int(statistics.median(r[0] for r in big)), len(big)))
        if f.kind == "[" and f is not root:
            parent.keys += f.keys

    for m in _JTOK.finditer(text):
        t = m.group()
        c = t[0]
        top = stack[-1]
        if c in "{[":
            if len(stack) >= _MAX_DEPTH:
                over += 1
            else:
                stack.append(_F(c, m.start()))
                continue
        elif c in "}]":
            if over:
                over -= 1
                continue
            want = "{" if c == "}" else "["
            k = len(stack) - 1
            while k > 0 and len(stack) - k <= _MAX_UNWIND and stack[k].kind != want:
                k -= 1
            if k <= 0 or stack[k].kind != want:
                continue
            while len(stack) > k:
                f = stack.pop()
                close(f, m.end())
            last_end = m.end()
            top = stack[-1]
            top.prev = c
            continue
        elif c == "," or c == ":":
            top.prev = c
            continue
        else:   # a string or a bare/single-quoted key
            if top.kind == "{" and top.prev in "{," and text[m.end():m.end() + 64].lstrip()[:1] == ":":
                top.keys += 1
            top.prev = "s"
    while len(stack) > 1:      # truncated payload: close what is open
        close(stack.pop(), len(text))
    close(root, last_end or len(text))


# ------------------------------------------------------------------------------------------------- XML
_XTAG = re.compile(r"<(/?)([A-Za-z_][\w:.\-]{0,80})((?:\s[^<>]{0,2000}?)?)(/?)>")
_XATTR = re.compile(r"[\w:.\-]+\s*=\s*(?:\"[^\"]*\"|'[^']*')")


class _X:
    __slots__ = ("name", "start", "tend", "leaves", "kids", "attrs")

    def __init__(self, name, start, tend, attrs):
        self.name, self.start, self.tend, self.leaves, self.kids, self.attrs = name, start, tend, 0, {}, attrs


def _xml(text: str, out: list[Region]) -> None:
    if "<" not in text or ">" not in text:
        return
    root = _X("", 0, 0, 0)
    stack = [root]
    over = 0

    def close(f: _X, end: int, inner_end: int) -> None:
        if f.kids:
            leaves = f.leaves + f.attrs
        else:   # a leaf counts when it holds a value (short text, no sentence), not prose (an HTML paragraph)
            body = text[f.tend:inner_end].strip()
            leaves = f.attrs + (0 < len(body) <= 80 and not _SENTENCE.search(body))
        parent = stack[-1]
        parent.leaves += leaves
        parent.kids.setdefault(f.name, []).append((leaves, f.start, end))
        for recs in f.kids.values():
            big = [r for r in recs if r[0] >= MIN_FIELDS]
            if len(big) >= 2:
                out.append(Region(big[0][1], big[-1][2], "xml", len(big), int(statistics.median(r[0] for r in big)),
                                  len(big)))
        if leaves >= SINGLE_SYNTAX_FIELDS and parent is root:
            out.append(Region(f.start, end, "xml", 1, leaves, 1))

    for m in _XTAG.finditer(text):
        closing, name, attrs, selfc = m.group(1), m.group(2), m.group(3), m.group(4)
        if closing:
            if over:
                over -= 1
                continue
            k = len(stack) - 1
            while k > 0 and len(stack) - k <= _MAX_UNWIND and stack[k].name != name:
                k -= 1
            if k <= 0 or stack[k].name != name:
                continue
            while len(stack) > k:
                close(stack.pop(), m.end(), m.start())
            continue
        na = len(_XATTR.findall(attrs)) if attrs else 0
        if selfc:
            stack[-1].leaves += max(1, na)
            stack[-1].kids.setdefault(name, []).append((max(1, na), m.start(), m.end()))
            continue
        if len(stack) >= _MAX_DEPTH:
            over += 1
            continue
        stack.append(_X(name, m.start(), m.end(), na))
    while len(stack) > 1:
        f = stack.pop()
        close(f, len(text), f.tend)
    for recs in root.kids.values():
        big = [r for r in recs if r[0] >= MIN_FIELDS]
        if len(big) >= 2:
            out.append(Region(big[0][1], big[-1][2], "xml", len(big), int(statistics.median(r[0] for r in big)),
                              len(big)))


# ------------------------------------------------------------------------------------------------- API
def scan(text: str) -> list[Region]:
    """Every record-like region found (also those under the thresholds), sorted by start."""
    if not text:
        return []
    out: list[Region] = []
    lines = _lines(text)
    if len(lines) >= 2:
        _tables(text, lines, out)
        _record_lines(text, lines, out)
    _key_values(text, lines, out)
    _json(text, out)
    _xml(text, out)
    out.sort(key=lambda r: (r.start, -r.end))
    return out


def find(text: str) -> list[Region]:
    """The structured regions of `text` (those the free-text external path refuses)."""
    return [r for r in scan(text) if r.structured]


def is_structured(text: str) -> bool:
    return bool(find(text))


def find_in(texts) -> list[tuple[int, Region]]:
    """(index of the text, region) for every structured region of every text (the gateway passes each string of a
    request: message contents, content parts, tool-call arguments)."""
    return [(i, r) for i, t in enumerate(texts) if isinstance(t, str) for r in find(t)]


def header_rows(text: str) -> list[tuple[int, int]]:
    """(start, end) of the header lines of recognised tables (delimited, aligned, record lines): a line of >= 3 label
    cells, separated by gaps or a delimiter, above data rows that hold data tokens. A row of values (a name, an
    amount) is never taken for one, and no list of header words is involved."""
    if "\n" not in text:
        return []
    lines = _lines(text)
    out: list[Region] = []
    _tables(text, lines, out)
    _record_lines(text, lines, out)
    spans = set()
    for r in out:
        if r.header is None:
            continue
        h = text[r.header[0]:r.header[1]]
        cols = _gap_cells(h)
        cells = [h[a:b] for a, b in cols] if cols else next(
            (c for d in _DELIMS if (c := _cells(h, d)) is not None), None)
        # only a header with visible columns, every cell a label: a line of prose above a table is never one
        if cells and len(cells) >= MIN_FIELDS and all(_label_like(c) for c in cells if c.strip()):
            spans.add(r.header)
    return sorted(spans)
