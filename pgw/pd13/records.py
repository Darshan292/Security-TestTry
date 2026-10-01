"""L1: record graph from standard parsers. Output is a set of fields ``(record, key, value span)`` plus the
record tree; everything that is not inside a field is prose. All spans are offsets into the string given to
``parse`` (the D0 base view).

Parsers (every parse that succeeds is kept; nothing is dropped on ambiguity):
  json     lenient JSON scanner with offsets (whole text, NDJSON lines, embedded blocks); unquoted keys,
           single quotes, trailing commas and ``Name('x')`` constructor wrappers are accepted
  xml      ``xml.parsers.expat`` (the parser under xml.etree) over a synthetic root, so fragments and prose
           around them parse; partial results survive a syntax error
  table    delimited blocks of >= 2 lines with a consistent cell count; the delimiter is *inferred* as a
           punctuation run (or a tab) that splits every line of the block into the same number of cells;
           RFC 4180 double-quote quoting; ruler lines are skipped
  fixed    whitespace-aligned fixed-width tables (>= 3 lines sharing column gaps)
  kv       ``k: v``, ``k = v``, ``k => v`` pairs inside a line
  indent   indentation / brace blocks (``key:`` or ``key {`` opens a block); used for record nesting
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from xml.parsers import expat


@dataclass
class Field:
    rid: int
    key: str | None
    path: tuple
    start: int
    end: int
    col: tuple | None
    fmt: str
    kstart: int = 0       # key span (kv parser only); kstart == kend when the key position is unknown
    kend: int = 0


@dataclass
class Rec:
    rid: int
    start: int
    end: int
    parent: int | None
    kind: str
    fmt: str
    strong: bool          # structured record (json/xml/table/fixed/indent) vs weak (kv lines)
    counts: bool = True   # False for a hierarchical document root (header-ish content is not a person scope)


@dataclass
class Graph:
    records: list[Rec] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)

    def new_rec(self, start, end, parent, kind, fmt, strong=True, counts=True) -> Rec:
        r = Rec(len(self.records), start, end, parent, kind, fmt, strong, counts)
        self.records.append(r)
        return r

    def add_field(self, rid, key, path, start, end, col, fmt, kstart=0, kend=0):
        if end > start:
            self.fields.append(Field(rid, key, tuple(path), start, end, col, fmt, kstart, kend))

    def chain(self, rid: int) -> list[Rec]:
        out = []
        while rid is not None:
            r = self.records[rid]
            out.append(r)
            rid = r.parent
        return out


_PLACEHOLDER = re.compile(r"^[\s_\-.*xX#?/\\|~]*$|^(?:nil|na|n/a|none|null|nan|tbd|nill?)$", re.I)


def is_placeholder(v: str) -> bool:
    return bool(_PLACEHOLDER.match(v.strip()))


# ============================================================================================ JSON
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_LIT = {"true", "false", "null", "none", "undefined", "nan", "True", "False", "None"}
_MAX_DEPTH = 40
_CALL = re.compile(r"(?<![\w$.])[A-Za-z_$][\w$]*[ ]*\(")


class _Fail(Exception):
    pass


class _Scan:
    def __init__(self, s: str):
        self.s, self.n = s, len(s)
        self.steps = 0

    def ws(self, i):
        s, n = self.s, self.n
        while i < n and s[i] in " \t\r\n":
            i += 1
        return i

    def string(self, i):
        s, n = self.s, self.n
        q = s[i]
        j = i + 1
        while j < n:
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == q:
                return (i + 1, j), j + 1
            if c == "\n":
                raise _Fail
            j += 1
        raise _Fail

    def value(self, i, depth):
        self.steps += 1
        if self.steps > 200000 or depth > _MAX_DEPTH:
            raise _Fail
        s = self.s
        i = self.ws(i)
        if i >= self.n:
            raise _Fail
        c = s[i]
        if c == "{":
            return self.obj(i, depth)
        if c == "[":
            return self.arr(i, depth)
        if c in "\"'":
            sp, j = self.string(i)
            return ("str", sp[0], sp[1]), j
        m = _NUM.match(s, i)
        if m and not (m.end() < self.n and (s[m.end()].isalnum() or s[m.end()] == "_")):
            return ("num", m.start(), m.end()), m.end()
        m = _IDENT.match(s, i)
        if m:
            j = self.ws(m.end())
            if j < self.n and s[j] == "(":                       # Name( scalar ) or Name( scalar, scalar, ... )
                k = self.ws(j + 1)
                args = []
                while True:
                    v, k = self.value(k, depth + 1)
                    if v[0] not in ("str", "num", "lit"):
                        raise _Fail
                    args.append(v)
                    k = self.ws(k)
                    if k < self.n and s[k] == ",":
                        k = self.ws(k + 1)
                        continue
                    break
                if k < self.n and s[k] == ")":
                    if len(args) == 1:
                        return args[0], k + 1
                    return ("arr", j, k + 1, args), k + 1
                raise _Fail
            if m.group() in _LIT:
                return ("lit", m.start(), m.end()), m.end()
        raise _Fail

    def key(self, i):
        s = self.s
        if s[i] in "\"'":
            sp, j = self.string(i)
            return s[sp[0]:sp[1]], j
        m = _IDENT.match(s, i)
        if m:
            return m.group(), m.end()
        raise _Fail

    def obj(self, i, depth):
        s, n = self.s, self.n
        items = []
        j = self.ws(i + 1)
        while True:
            if j >= n:
                raise _Fail
            if s[j] == "}":
                break
            k, j = self.key(j)
            j = self.ws(j)
            if j >= n or s[j] != ":":
                raise _Fail
            v, j = self.value(j + 1, depth + 1)
            items.append((k, v))
            j = self.ws(j)
            if j < n and s[j] == ",":
                j = self.ws(j + 1)
            elif j < n and s[j] == "}":
                break
            else:
                raise _Fail
        return ("obj", i, j + 1, items), j + 1

    def arr(self, i, depth):
        s, n = self.s, self.n
        items = []
        j = self.ws(i + 1)
        while True:
            if j >= n:
                raise _Fail
            if s[j] == "]":
                break
            v, j = self.value(j, depth + 1)
            items.append(v)
            j = self.ws(j)
            if j < n and s[j] == ",":
                j = self.ws(j + 1)
            elif j < n and s[j] == "]":
                break
            else:
                raise _Fail
        return ("arr", i, j + 1, items), j + 1


def _json_emit(g: Graph, node, parent: int | None, path: tuple, key: str | None, top: bool):
    kind = node[0]
    if kind == "obj":
        r = g.new_rec(node[1], node[2], parent, "object", "json", counts=not top)
        for k, v in node[3]:
            p = path + (k,)
            if v[0] in ("obj", "arr"):
                _json_emit(g, v, r.rid, p, k, False)
            else:
                g.add_field(r.rid, k, p, v[1], v[2], ("json", p), "json")
        return r
    if kind == "arr":
        objs = sum(1 for x in node[3] if x[0] in ("obj", "arr"))
        r = g.new_rec(node[1], node[2], parent, "array", "json", counts=True)
        for x in node[3]:
            if x[0] in ("obj", "arr"):
                _json_emit(g, x, r.rid, path, key, False)
            else:
                g.add_field(r.rid, key, path, x[1], x[2], ("json", path), "json")
        return r


def parse_json(s: str, g: Graph) -> None:
    n = len(s)
    i = 0
    tops = []
    sc = _Scan(s)
    attempts = 0
    calls = {m.start() for m in _CALL.finditer(s)}
    while i < n and attempts < 4000:
        c = s[i]
        if c in "{[" or i in calls:
            attempts += 1
            sc.steps = 0
            try:
                node, j = sc.value(i, 0)
            except (_Fail, RecursionError):
                i += 1
                continue
            ok = (node[0] == "obj" and node[3]) or (node[0] == "arr" and
                  (any(x[0] in ("obj", "arr") for x in node[3]) or len(node[3]) >= 2))
            if ok:
                tops.append(node)
                i = j
                continue
        i += 1
    prev = None
    group_nodes: list = []

    def flush():
        nonlocal group_nodes
        if len(group_nodes) >= 2 and all(x[0] == "obj" for x in group_nodes):
            gr = g.new_rec(group_nodes[0][1], group_nodes[-1][2], None, "array", "json")
            for x in group_nodes:
                _json_emit(g, x, gr.rid, (), None, False)
        else:
            for x in group_nodes:
                _json_emit(g, x, None, (), None, True)
        group_nodes = []

    for node in tops:                       # consecutive top-level objects separated by whitespace/commas: NDJSON
        if prev is not None and group_nodes and not s[prev[2]:node[1]].strip(" \t\r\n,"):
            group_nodes.append(node)
        else:
            flush()
            group_nodes = [node]
        prev = node
    flush()


# ============================================================================================ XML
_TAG_END = re.compile(r"""(?:[^>"']|"[^"]*"|'[^']*')*>""")
_ATTR = re.compile(r"""([^\s=/<>"']+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _local(name: str) -> str:
    return name.rsplit(":", 1)[-1]


def parse_xml(s: str, g: Graph) -> None:
    if "<" not in s or not re.search(r"<[A-Za-z_]", s):
        return
    # strip declaration / doctype by blanking them (same length, so offsets are unchanged)
    t = re.sub(r"<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>|<!\[CDATA\[|\]\]>",
               lambda m: " " * len(m.group()) if not m.group().startswith(("<![CDATA", "]]")) else m.group(), s, flags=re.S)
    pos = 0
    guard = 0
    while pos < len(t) and guard < 50:
        guard += 1
        m = re.compile(r"<[A-Za-z_]").search(t, pos)
        if not m:
            return
        err = _xml_region(t, m.start(), g)
        if err is None or err <= m.start():
            pos = m.start() + 1 if err is None else m.start() + 1
            if err is None:
                return
        else:
            pos = err


def _xml_region(t: str, start: int, g: Graph):
    region = t[start:]
    wrapped = "<_r>" + region + "</_r>"
    data = wrapped.encode("utf-8")
    # byte index -> char index (in wrapped)
    if len(data) == len(wrapped):
        b2c = None
    else:
        b2c = {}
        b = 0
        for ci, ch in enumerate(wrapped):
            b2c[b] = ci
            b += len(ch.encode("utf-8"))
        b2c[b] = len(wrapped)

    def cidx(b):
        return b if b2c is None else b2c.get(b, max(k for k in b2c if k <= b) if b not in b2c else b2c[b])

    off = start - 4
    p = expat.ParserCreate()
    p.buffer_text = False
    stack: list[dict] = []
    root_rec: list = []
    last_pos = [start]

    def tag_span(ci):
        m = _TAG_END.match(wrapped, ci + 1)
        return (ci, m.end()) if m else (ci, len(wrapped))

    def sh(i):  # wrapped index -> t index
        return i + off

    def on_start(name, attrs):
        ci = cidx(p.CurrentByteIndex)
        if name == "_r" and not stack:
            return
        a, b = tag_span(ci)
        node = {"name": _local(name), "tag_start": sh(a), "tag_end": sh(b), "children": [], "rec": None}
        if stack:
            stack[-1]["children"].append(node)
        else:
            root_rec.append(node)
        stack.append(node)
        # attributes
        node["attrs"] = [(m.group(1), sh(m.start(2) if m.group(2) is not None else m.start(3)),
                          sh(m.end(2) if m.group(2) is not None else m.end(3)))
                         for m in _ATTR.finditer(wrapped, a, b)]

    def on_end(name):
        ci = cidx(p.CurrentByteIndex)
        if name == "_r" and not stack:
            return
        if stack:
            node = stack.pop()
            node["close_start"] = sh(ci)
            node["closed"] = True
            last_pos[0] = sh(ci)

    p.StartElementHandler = on_start
    p.EndElementHandler = on_end
    err_at = None
    try:
        p.Parse(data, True)
    except expat.ExpatError:
        try:
            err_at = sh(cidx(p.ErrorByteIndex))
        except Exception:
            err_at = len(t)
    # emit what was parsed
    for top in root_rec:
        _xml_emit(t, g, top, None, (), True)
    if err_at is None:
        return None
    return max(err_at, last_pos[0] + 1)


def _xml_emit(t: str, g: Graph, node, parent, path, top):
    name = node["name"]
    p = path + (name,)
    kids = node["children"]
    closed = node.get("closed")
    end = node.get("close_start", node["tag_end"])
    if not kids:
        if closed and parent is not None:
            g.add_field(parent, name, p, node["tag_end"], node["close_start"], ("xml", p), "xml")
        for an, s0, e0 in node.get("attrs", []):
            if an.startswith("xmlns"):
                continue
            g.add_field(parent if parent is not None else g.new_rec(node["tag_start"], end, None, "element", "xml",
                                                                    counts=False).rid, an, p + ("@" + _local(an),),
                        s0, e0, ("xml", p + ("@" + _local(an),)), "xml")
        return
    r = g.new_rec(node["tag_start"], max(end, node["tag_end"]), parent, "element", "xml", counts=not top)
    for an, s0, e0 in node.get("attrs", []):
        if an.startswith("xmlns"):
            continue
        g.add_field(r.rid, an, p + ("@" + _local(an),), s0, e0, ("xml", p + ("@" + _local(an),)), "xml")
    tags = Counter(k["name"] for k in kids if k["children"])
    if any(c >= 2 for c in tags.values()):
        r.counts = True                              # a container of repeated records is a table
    for k in kids:
        _xml_emit(t, g, k, r.rid, p, False)


# ============================================================================================ tables
_RULER = re.compile(r"^[\s\-+=|_*~#:.]*$")
_DELIM_RUN = re.compile(r"[^\w\s]+")
_NOT_DELIM = re.compile(r"^[.:=\-/_'\"()\[\]{}@+%&*!?<>\\]+$")      # punctuation that never separates columns
_PROSE_PUNCT = re.compile(r"^[,;\s]+$")


def _lines(s: str):
    pos = 0
    out = []
    for ln in s.split("\n"):
        e = pos + len(ln)
        out.append((pos, e - 1 if ln.endswith("\r") else e))
        pos = e + 1
    return out


def split_cells(s: str, a: int, b: int, delim: str):
    """Quote-aware split of s[a:b] on `delim`; returns [(start, end)] of trimmed cell contents."""
    cells = []
    i = a
    dl = len(delim)
    while True:
        j = i
        while j < b and s[j] in " \t" and s[j:j + dl] != delim:
            j += 1
        if j < b and s[j] == '"':
            k = j + 1
            while k < b:
                if s[k] == '"':
                    if k + 1 < b and s[k + 1] == '"':
                        k += 2
                        continue
                    break
                k += 1
            cs, ce = j + 1, min(k, b)
            nxt = s.find(delim, k, b)
            nxt = b if nxt < 0 else nxt
        else:
            nxt = s.find(delim, i, b)
            nxt = b if nxt < 0 else nxt
            cs, ce = i, nxt
            while cs < ce and s[cs] in " \t":
                cs += 1
            while ce > cs and s[ce - 1] in " \t":
                ce -= 1
        cells.append((cs, ce))
        if nxt >= b:
            break
        i = nxt + dl
    # a line framed by delimiters (| a | b |) has an empty first and last cell; a lone trailing delimiter does not
    if len(cells) > 3 and cells[0][0] == cells[0][1] and cells[-1][0] == cells[-1][1]:
        cells = cells[1:-1]
    return cells


_HDR_CELL = re.compile(r"^[^\W\d_][^\n]{0,40}$")


def _is_header(cells_text: list[str]) -> bool:
    ne = [c for c in cells_text if c.strip()]
    if len(ne) < 2 or len(ne) < 0.6 * len(cells_text):
        return False
    if sum(any(ch.isdigit() for ch in c) for c in ne) > 1:
        return False
    return all(_HDR_CELL.match(c.strip()) for c in ne)


def _emit_table(g: Graph, s: str, lines, rows, fmt: str):
    """rows: list of (line_index, cells[(a,b)]); the first row may be a header."""
    a0, b0 = lines[rows[0][0]][0], lines[rows[-1][0]][1]
    tbl = g.new_rec(a0, b0, None, "table", fmt)
    cells0 = [re.sub(r"^[\W_]+", "", s[a:b]) for a, b in rows[0][1]]   # a leading comment marker is not part of the name
    header = _is_header(cells0) and len(rows) >= 2
    keys = [c.strip() for c in cells0] if header else None
    tid = tbl.rid
    for ri, (li, cells) in enumerate(rows):
        if header and ri == 0:
            continue
        r = g.new_rec(lines[li][0], lines[li][1], tbl.rid, "row", fmt)
        for ci, (a, b) in enumerate(cells):
            key = keys[ci] if keys and ci < len(keys) and keys[ci] else None
            g.add_field(r.rid, key, (key,) if key else (), a, b, (fmt, tid, ci), fmt)


def parse_tables(s: str, g: Graph) -> None:
    lines = _lines(s)
    n = len(lines)
    i = 0
    while i < n:
        a, b = lines[i]
        if not s[a:b].strip():
            i += 1
            continue
        j = i
        while j < n and s[lines[j][0]:lines[j][1]].strip():
            j += 1
        _tables_in_run(g, s, lines, i, j)
        _fixed_in_run(g, s, lines, i, j)
        i = j


def _tables_in_run(g, s, lines, i, j):
    content = [k for k in range(i, j) if not _RULER.match(s[lines[k][0]:lines[k][1]])]
    if len(content) < 1:
        return
    cands = Counter()
    for k in content:
        txt = s[lines[k][0]:lines[k][1]]
        for d in set(_DELIM_RUN.findall(txt)):
            if not _NOT_DELIM.match(d) and all(unicodedata.category(ch)[0] in "PS" for ch in d):
                cands[d] += 1
        if "\t" in txt:
            cands["\t"] += 1
    scored = []
    for d, c in cands.items():
        if c < 1:
            continue
        counts = {k: len(split_cells(s, lines[k][0], lines[k][1], d)) for k in content}
        by_n: dict[int, list[int]] = {}
        for k in content:                       # rows of one table need not be adjacent (an error line may sit between)
            if counts[k] >= 2:
                by_n.setdefault(counts[k], []).append(k)
        # a lone line is a one-row table only when it has many cells and the delimiter is not prose punctuation
        best = [blk for n, blk in by_n.items() if len(blk) >= 2 or (n >= 5 and not _PROSE_PUNCT.match(d))]
        for blk in best:
            ncell = counts[blk[0]]
            prose = bool(_PROSE_PUNCT.match(d))
            lens = [b - a for k in blk for a, b in split_cells(s, lines[k][0], lines[k][1], d)]
            if statistics.median(lens) > 40:        # cells of a table are short; prose lines split on commas are not
                continue
            if prose:       # commas / semicolons also occur in sentences: need more rows, or a short-label header
                head = [s[a:b] for a, b in split_cells(s, lines[blk[0]][0], lines[blk[0]][1], d)]
                strong_header = ncell >= 3 and _is_header(head) and all(len(c.split()) <= 3 for c in head)
                credible = ((ncell >= 3 and len(blk) >= 3) or len(blk) >= 4 or (strong_header and len(blk) >= 2)
                            or (len(blk) == 1 and ncell >= 5))
            else:
                credible = ncell >= 3 or len(blk) >= 3 or (len(blk) == 1 and ncell >= 5)
            if credible:
                scored.append((len(blk) * ncell, d, blk, counts))
    scored.sort(key=lambda x: -x[0])
    used = 0
    for _, d, blk, counts in scored:
        if used >= 4:
            break
        used += 1
        rows = [(k, split_cells(s, lines[k][0], lines[k][1], d)) for k in blk]
        _emit_table(g, s, lines, rows, "table")


def _fixed_in_run(g, s, lines, i, j):
    ls = [k for k in range(i, j) if not _RULER.match(s[lines[k][0]:lines[k][1]])]
    if len(ls) < 3:
        return
    width = max(lines[k][1] - lines[k][0] for k in ls)
    if width > 400:
        return
    blank = [0] * width
    for k in ls:
        a, b = lines[k]
        for p in range(width):
            if a + p >= b or s[a + p] in " \t":
                blank[p] += 1
    thr = 0.75 * len(ls)                    # a title or footer line may violate the gaps
    gap = [blank[p] >= thr for p in range(width)]
    bounds = []
    p = 0
    while p < width:
        if gap[p]:
            q = p
            while q < width and gap[q]:
                q += 1
            if q - p >= 2 and p > 0 and q < width:
                bounds.append((p, q))
            p = q
        else:
            p += 1

    def filled(c0: int, c1: int) -> int:
        return sum(1 for k in ls if any(lines[k][0] + c < lines[k][1] and s[lines[k][0] + c] not in " \t"
                                        for c in range(c0, c1)))
    changed = True
    while changed and bounds:                # a gap must separate two segments that both carry content
        changed = False
        edges = [0] + [x for bd in bounds for x in bd] + [width]
        segs = [(edges[2 * i], edges[2 * i + 1]) for i in range(len(bounds) + 1)]
        for gi in range(len(bounds)):
            if filled(*segs[gi]) < 2 / 3 * len(ls) or filled(*segs[gi + 1]) < 2 / 3 * len(ls):
                del bounds[gi]
                changed = True
                break
    if len(bounds) < 2:
        return
    edges = [0] + [x for bd in bounds for x in bd] + [width]
    segs = [(edges[2 * i], edges[2 * i + 1]) for i in range(len(bounds) + 1)]
    starts = [c0 for c0, _ in segs]
    rows = []
    for k in ls:
        a, b = lines[k]
        chunks = [(m.start(), m.end()) for m in _CHUNK.finditer(s, a, b)]
        if not chunks:
            continue
        cells: list[list[int]] = [[0, 0] for _ in segs]
        aligned = 0
        for x, y in chunks:
            col = max(i for i, c0 in enumerate(starts) if c0 <= x - a or i == 0)
            if min(abs((x - a) - c0) for c0 in starts) <= 2:
                aligned += 1
            if cells[col][1] == 0:
                cells[col] = [x, y]
            else:
                cells[col][1] = y
        filled_cols = sum(1 for x, y in cells if y > x)
        if aligned >= 0.8 * len(chunks) and filled_cols >= 0.5 * len(segs) and filled_cols >= 2:
            rows.append((k, [(x, y) for x, y in cells]))
    if len(rows) >= 3:
        _emit_table(g, s, lines, rows, "fixed")


_CHUNK = re.compile(r"\S+(?: \S+)*")


# ============================================================================================ kv + indent
_SEG_SPLIT = re.compile(r"\t+|[ ]{2,}")
_MK = "\u0300-\u036f\u0900-\u0eff"      # combining marks and the Indic / South-East Asian blocks (vowel signs are not \w)
_KV = re.compile(rf"(?P<k>[^\W\d_][\w{_MK} .\-/#()&']{{0,60}}?)[ ]*(?P<sep>=>|:|=)(?:[ ]+|(?=[\"'\[{{])|(?<==)(?=\S))")
_KV1 = re.compile(rf"(?<![\w{_MK}.\-/$])(?P<k>[^\W\d_][\w{_MK}.\-]{{0,40}})(?P<sep>=>|:|=)(?=(?<=[:])[ \t\"'\[{{]|(?<=[=>])\S)")
_NEXT_KEY = re.compile(
    rf"(?:[ ]+(?=[^\W\d_][\w{_MK}.\-]{{0,40}}[ ]*(?:(?:=>|:|=)(?:[ \"'\[{{]|[^\s=:]|$)|[{{])))"
    rf"|(?:;[ ]*(?=[^\W\d_][\w{_MK} .\-]{{0,40}}?[ ]*(?:=>|=)))")
_OPENER = {"}": "{", "]": "[", ")": "("}


def _kv_in_segment(s: str, a: int, b: int):
    out = []
    pos = a
    while pos < b:
        m = _KV.match(s, pos, b)
        k = m.group("k").strip() if m else ""
        if not m or not k or len(k.split()) > 7 or k.lower() in ("http", "https", "ftp"):
            m = _KV1.search(s, pos, b)
            if not m:
                break
            k = m.group("k")
            ks, ke = m.start("k"), m.end("k")
            va = m.end()
            while va < b and s[va] in " \t":
                va += 1
        else:
            ks, ke = m.start("k"), m.start("k") + len(m.group("k").rstrip())
            k = k
            va = m.end()
        if va >= b:
            break
        if _KV1.match(s, va, b):            # "request: payout_id: ..." - the outer key has no scalar value
            pos = va
            continue
        if s[va] in "\"'":
            q = s[va]
            vz = s.find(q, va + 1, b)
            if vz < 0:
                vs, ve, nxt = va + 1, b, b
            else:
                vs, ve, nxt = va + 1, vz, vz + 1
        else:
            nm = _NEXT_KEY.search(s, va, b)
            ve = nm.start() if nm else b
            vs = va
            nxt = nm.end() if nm else b
            while ve > vs and s[ve - 1] in " \t,;":
                ve -= 1
            while ve > vs and s[ve - 1] in "}])" and s.count(_OPENER[s[ve - 1]], vs, ve) < s.count(s[ve - 1], vs, ve):
                ve -= 1                                       # a closing bracket with no opener inside the value
                while ve > vs and s[ve - 1] in " \t,;":
                    ve -= 1
        if ve > vs and not (s[vs] in "{[" and ve - vs <= 1):
            out.append((k, vs, ve, ks, ke))
        pos = max(nxt, m.end())
        while pos < b and s[pos] in " \t,;":
            pos += 1
    return out


def parse_kv(s: str, g: Graph) -> None:
    lines = _lines(s)
    cur: Rec | None = None
    blk_start = None
    prev_idx = -2
    for li, (a, b) in enumerate(lines):
        txt = s[a:b]
        pairs = []
        if txt.strip():
            pos = a
            segs = []
            for m in _SEG_SPLIT.finditer(txt):
                segs.append((pos, a + m.start()))
                pos = a + m.end()
            segs.append((pos, b))
            for sa, sb in segs:
                if sb > sa:
                    pairs += _kv_in_segment(s, sa, sb)
        if pairs:
            if cur is None or li != prev_idx + 1:
                cur = g.new_rec(a, b, None, "kv", "kv", strong=False)
            cur.end = b
            for k, vs, ve, ks, ke in pairs:
                g.add_field(cur.rid, k, (k,), vs, ve, ("kv", re.sub(r"[^a-z0-9]", "", k.lower())), "kv", ks, ke)
            prev_idx = li
        elif not txt.strip():
            cur = None


def parse_indent(s: str, g: Graph) -> None:
    """Brace / indentation blocks: a line that is only ``key:`` / ``key {`` opens a record that extends over the
    following more-indented (or brace-nested) lines. Produces strong records used for nesting."""
    lines = _lines(s)
    n = len(lines)
    stack: list[tuple[int, Rec, str]] = []     # (indent, rec, mode)
    li = 0
    for li in range(n):
        a, b = lines[li]
        txt = s[a:b]
        if not txt.strip():
            continue
        ind = len(txt) - len(txt.lstrip(" \t"))
        stripped = txt.strip()
        # close brace blocks
        while stack and stack[-1][2] == "brace" and stripped.startswith(("}", "]", ")")):
            stack[-1][1].end = b
            stack.pop()
            stripped = stripped[1:].lstrip(" ,;")
            if not stripped:
                break
        if not stripped:
            continue
        while stack and stack[-1][2] == "indent" and ind <= stack[-1][0]:
            stack.pop()
        opens = re.match(r"^(?:-[ \t]+)?([A-Za-z_][\w .\-/]{0,40}?)[ \t]*(?::[ \t]*)?([{\[])[ \t]*$", stripped)
        colon = re.match(r"^(?:-[ \t]+)?([A-Za-z_][\w .\-/]{0,40}?):[ \t]*$", stripped)
        if opens or colon:
            parent = stack[-1][1].rid if stack else None
            r = g.new_rec(a, b, parent, "block", "indent", counts=True)
            stack.append((ind, r, "brace" if opens else "indent"))
        else:
            for _, r, _m in stack:
                r.end = max(r.end, b)


def parse(s: str) -> Graph:
    """All parsers over `s`; every successful parse is kept."""
    g = Graph()
    for fn in (parse_json, parse_xml, parse_tables, parse_kv, parse_indent):
        try:
            fn(s, g)
        except RecursionError:      # pathological nesting: keep what the other parsers found
            continue
    return g
