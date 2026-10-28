"""Bounded, safe parsing of HRMS record payloads: JSON, CSV (header row) and XML.

Every parser here is bounded in size, depth and element count, and raises PayloadError with a content-free message
(never a value, never a parser message that quotes the input).

XML is parsed with the standard library's expat, configured so that nothing outside the document can be reached and
no entity can expand: any DOCTYPE (internal or external DTD), ENTITY, NOTATION or external-entity reference is refused
before or during parsing (billion laughs, quadratic blowup and XXE all need a DTD), parameter-entity parsing is off,
and depth and element count are capped. No third-party XML library is needed.
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.parsers.expat as expat

from .local_compute import PayloadError

MAX_DEPTH = 48
MAX_XML_ELEMENTS = 5_000_000
MAX_CSV_COLUMNS = 500
CSV_DELIMITERS = (",", ";", "\t", "|")


def depth_ok(obj, limit: int = MAX_DEPTH) -> bool:
    """True if `obj` (parsed JSON) nests at most `limit` levels. Iterative: a hostile document cannot overflow."""
    stack = [(obj, 1)]
    while stack:
        o, d = stack.pop()
        if d > limit:
            return False
        if isinstance(o, dict):
            stack.extend((v, d + 1) for v in o.values())
        elif isinstance(o, list):
            stack.extend((v, d + 1) for v in o)
    return True


def parse_json_text(text: str):
    try:
        obj = json.loads(text, parse_constant=_no_constant)
    except RecursionError:
        raise PayloadError("JSON nests too deeply") from None
    except ValueError:
        raise PayloadError("payload is not valid JSON") from None
    if not depth_ok(obj):
        raise PayloadError("JSON nests too deeply")
    return obj


def _no_constant(_name):
    raise ValueError("NaN/Infinity are not JSON")


def parse_csv(text: str, max_rows: int, delimiter: str = ","):
    """(header list, iterator of rows). Rows have exactly the header's length (else PayloadError with the row
    number). Blank lines are skipped. Quoting errors are refused (strict)."""
    if delimiter not in CSV_DELIMITERS:
        raise PayloadError("unsupported CSV delimiter")
    if "\x00" in text:
        raise PayloadError("CSV contains NUL characters")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        header = next(reader)
    except StopIteration:
        raise PayloadError("CSV has no header row") from None
    except csv.Error:
        raise PayloadError("CSV header row is malformed") from None
    if not header or len(header) > MAX_CSV_COLUMNS:
        raise PayloadError(f"CSV header must have 1-{MAX_CSV_COLUMNS} columns")

    def rows():
        n = 0
        while True:
            try:
                row = next(reader)
            except StopIteration:
                return
            except csv.Error:
                raise PayloadError(f"CSV line {reader.line_num} is malformed") from None
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            n += 1
            if n > max_rows:
                raise PayloadError("too many rows for this data contract")
            if len(row) != len(header):
                raise PayloadError(f"CSV record {n} has {len(row)} columns, the header has {len(header)}")
            yield row
    return header, rows()


_DTD = re.compile(r"<!\s*(?:DOCTYPE|ENTITY|NOTATION|ELEMENT|ATTLIST)", re.IGNORECASE)


def _local(name: str) -> str:
    return name.rsplit(":", 1)[-1]


class _XmlRefused(Exception):
    pass


def parse_xml(text: str, max_elements: int = MAX_XML_ELEMENTS, max_depth: int = MAX_DEPTH):
    """XML -> nested dicts: {root_name: element}. An element with neither children nor attributes is its stripped
    text; otherwise a dict of children (a repeated child name becomes a list), attributes as "@name" and
    non-blank text as "#text". Namespace prefixes are dropped; xmlns declarations are ignored."""
    if _DTD.search(text):
        raise PayloadError("XML with a DTD or entity declaration is refused")
    p = expat.ParserCreate()
    p.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def refuse(*_a):
        raise _XmlRefused()
    p.StartDoctypeDeclHandler = refuse
    p.EntityDeclHandler = refuse
    p.UnparsedEntityDeclHandler = refuse
    p.NotationDeclHandler = refuse
    p.ExternalEntityRefHandler = refuse
    p.ElementDeclHandler = refuse
    p.AttlistDeclHandler = refuse
    stack: list = []
    root: dict = {}
    count = [0]

    def start(name, attrs):
        count[0] += 1
        if count[0] > max_elements or len(stack) >= max_depth:
            raise _XmlRefused()
        node = {"__name": _local(name), "__children": [], "__text": [], "__attrs": {}}
        for k, v in attrs.items():
            if k == "xmlns" or k.startswith("xmlns:"):
                continue
            node["__attrs"]["@" + _local(k)] = v
        stack.append(node)

    def chars(data):
        if stack:
            stack[-1]["__text"].append(data)

    def end(_name):
        node = stack.pop()
        text = "".join(node["__text"]).strip()
        if not node["__children"] and not node["__attrs"]:
            value = text
        else:
            value = dict(node["__attrs"])
            for cname, cval in node["__children"]:
                if cname in value:
                    if not isinstance(value[cname], list) or cname not in node.get("__lists", ()):
                        value[cname] = [value[cname]]
                        node.setdefault("__lists", set()).add(cname)
                    value[cname].append(cval)
                else:
                    value[cname] = cval
            if text:
                value["#text"] = text
        if stack:
            stack[-1]["__children"].append((node["__name"], value))
        else:
            root[node["__name"]] = value

    p.StartElementHandler = start
    p.EndElementHandler = end
    p.CharacterDataHandler = chars
    try:
        p.Parse(text, True)
    except _XmlRefused:
        raise PayloadError("XML refused (DTD, entity, or size/depth limit)") from None
    except expat.ExpatError:
        raise PayloadError("payload is not well-formed XML") from None
    if not root:
        raise PayloadError("XML has no root element")
    return root
