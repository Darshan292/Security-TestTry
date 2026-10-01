"""PD-13 L1 record parsers: each parser on a sample, an ambiguity case, and a failure case."""
from __future__ import annotations

import pytest

from pgw.pd13 import records as R


def fields(text, fmt=None):
    g = R.parse(text)
    return [(f.key, text[f.start:f.end]) for f in g.fields if fmt is None or f.fmt == fmt], g


def has(fl, key, value):
    return (key, value) in fl


# --- json ---------------------------------------------------------------------------------------

def test_json_whole_document_with_offsets():
    t = '{"name": "Asha Rao", "pay": {"net": 52000, "ok": true}, "ids": ["A1", "B2"]}'
    fl, g = fields(t, "json")
    assert has(fl, "name", "Asha Rao") and has(fl, "net", "52000") and has(fl, "ids", "A1") and has(fl, "ids", "B2")
    assert has(fl, "ok", "true")
    assert any(r.kind == "object" for r in g.records)


def test_json_ndjson_lines_form_one_group():
    t = '{"a": "x1"}\n{"a": "x2"}\n{"a": "x3"}'
    fl, g = fields(t, "json")
    assert [v for k, v in fl if k == "a"] == ["x1", "x2", "x3"]
    assert any(r.kind == "array" and r.counts for r in g.records)


def test_json_embedded_block_in_prose():
    t = 'Result follows {"emp": "KAF12345", "amt": 900.50} thanks'
    fl, _ = fields(t, "json")
    assert has(fl, "emp", "KAF12345") and has(fl, "amt", "900.50")


def test_json_lenient_unquoted_keys_single_quotes_and_constructor_wrapper():
    t = "{ code: 'KAF22533', total: Decimal128('11000.00'), when: ISODate('2026-01-02'), trailing: 1, }"
    fl, _ = fields(t, "json")
    assert has(fl, "code", "KAF22533") and has(fl, "total", "11000.00") and has(fl, "when", "2026-01-02")


def test_json_multi_argument_call_is_an_array_of_scalars():
    fl, _ = fields('rows = Array("Asha Rao", "ABCDE1234F", 3)', "json")
    assert [v for _, v in fl] == ["Asha Rao", "ABCDE1234F", "3"]


def test_json_garbage_is_not_a_record():
    fl, _ = fields("[X] Laptop returned {not json} [ ] other", "json")
    assert fl == []


def test_json_top_level_object_does_not_count_as_a_person_scope_but_arrays_do():
    g = R.parse('{"hdr": {"d": "2026-01-01"}, "rows": [{"n": "A B"}, {"n": "C D"}]}')
    top = [r for r in g.records if r.kind == "object" and r.counts is False]
    assert len(top) == 1
    assert any(r.kind == "array" and r.counts for r in g.records)


# --- xml ----------------------------------------------------------------------------------------

def test_xml_whole_document_leaf_values_and_attributes():
    t = '<?xml version="1.0"?><Doc xmlns="urn:x"><P id="7"><Nm>Asha Rao</Nm><Amt Ccy="INR">500</Amt></P></Doc>'
    fl, g = fields(t, "xml")
    assert has(fl, "Nm", "Asha Rao") and has(fl, "Amt", "500") and has(fl, "Ccy", "INR") and has(fl, "id", "7")
    assert not any(k == "xmlns" for k, _ in fl)


def test_xml_fragments_inside_prose_and_partial_after_error():
    t = "log: <a><b>one</b></a> text <c><d>two</d> <e>three"
    fl, _ = fields(t, "xml")
    assert has(fl, "b", "one") and has(fl, "d", "two")


def test_xml_non_ascii_offsets():
    t = "<r><n>मृणाल जोशी</n><m>x</m></r>"
    fl, _ = fields(t, "xml")
    assert has(fl, "n", "मृणाल जोशी") and has(fl, "m", "x")


def test_xml_repeated_children_make_the_root_a_table_scope():
    g = R.parse("<L><E><n>a</n></E><E><n>b</n></E></L>")
    root = min(g.records, key=lambda r: r.start)
    assert root.counts


def test_xml_root_alone_does_not_count():
    g = R.parse("<L><H><d>2026-01-01</d></H><P><n>b</n></P></L>")
    root = min(g.records, key=lambda r: r.start)
    assert not root.counts


# --- tables -------------------------------------------------------------------------------------

def test_table_comma_with_header_and_quoting():
    t = 'Name,Note,Amt\nAsha,"hello, world",100\nRavi,plain,200\n'
    fl, _ = fields(t, "table")
    assert has(fl, "Note", "hello, world") and has(fl, "Amt", "200") and has(fl, "Name", "Asha")


def test_table_delimiter_is_inferred_not_listed():
    for d in ("|", ";", "\t", "#~#", "^^"):
        t = d.join(["id", "name", "amt"]) + "\n" + d.join(["1", "Asha Rao", "5000"]) + "\n" + d.join(["2", "Ravi Nair", "6000"])
        fl, _ = fields(t, "table")
        assert has(fl, "name", "Ravi Nair"), d


def test_table_framed_rows_and_ruler_lines():
    t = "+------+-------+\n| id   | name  |\n+------+-------+\n| 1    | Asha  |\n| 2    | Ravi  |\n+------+-------+\n"
    fl, _ = fields(t, "table")
    assert has(fl, "name", "Asha") and has(fl, "id", "2")


def test_table_rows_need_not_be_adjacent_and_trailing_delimiter_keeps_counts():
    t = "id,name,nom\n1,Asha Rao,X\nERROR here\n2,Ravi Nair,\n"
    fl, _ = fields(t, "table")
    assert has(fl, "name", "Ravi Nair") and has(fl, "nom", "X")


def test_table_headerless_block_has_no_keys():
    t = "100#~#A#~#Asha Rao#~#\n101#~#B#~#Ravi Nair#~#\n"
    fl, _ = fields(t, "table")
    assert has(fl, None, "Asha Rao")


def test_table_single_long_line_is_a_one_row_table_but_prose_commas_are_not():
    t = "Line 3 : 1001#~#Asha Rao#~#52,000#~#15,000#~#1,800#~#\n"
    fl, _ = fields(t, "table")
    assert has(fl, None, "Asha Rao")
    assert fields("Dear all, this is a note, with commas, in prose, only.", "table")[0] == []


def test_table_prose_lines_with_commas_are_not_tables():
    t = "We met on Monday, the team agreed, and Asha will follow up.\nThen on Tuesday, the plan changed, and Ravi replied.\n"
    assert fields(t, "table")[0] == []


# --- fixed width ------------------------------------------------------------------------------

def test_fixed_width_table_with_title_and_ruler():
    t = ("ACME PAYROLL CENSUS                          PAGE 1\n"
         "-------------------------------------------------\n"
         "CODE      NAME               GRADE   BASIC\n"
         "-------------------------------------------------\n"
         "KAF1001   ASHA RAO           A       52000\n"
         "KAF1002   RAVI NAIR          B       61000\n"
         "PL77      MEERA IYER         A       48500\n")
    fl, g = fields(t, "fixed")
    assert has(fl, "NAME", "ASHA RAO") and has(fl, "BASIC", "48500") and has(fl, "CODE", "PL77")
    assert not any(v.startswith("ACME") for _, v in fl)


def test_fixed_width_needs_three_aligned_lines():
    assert fields("a   b   c\nd   e   f\n", "fixed")[0] == []


# --- key / value ------------------------------------------------------------------------------

def test_kv_colon_equals_and_arrow_forms():
    fl, _ = fields("Name: Asha Rao\nid = KAF123\nlimit => 500\n", "kv")
    assert has(fl, "Name", "Asha Rao") and has(fl, "id", "KAF123") and has(fl, "limit", "500")


def test_kv_several_pairs_on_one_line_and_quoted_values():
    fl, _ = fields('Employee Name: Asha Rao      Employee ID: KAF 18514\nreq { ref: "PAY 1" amt: 90 }', "kv")
    assert has(fl, "Employee Name", "Asha Rao") and has(fl, "Employee ID", "KAF 18514")
    assert has(fl, "ref", "PAY 1") and has(fl, "amt", "90")


def test_kv_does_not_split_times_or_urls():
    fl, _ = fields("at 12:05:06 see http://host/x\n", "kv")
    assert fl == []


def test_kv_non_latin_keys_and_key_spans():
    t = "জন্ম তারিখ: 01/02/1990\n"
    g = R.parse(t)
    assert any(t[f.start:f.end] == "01/02/1990" for f in g.fields)
    t2 = "Tenzin Nongrum: technical 3/5\n"
    g2 = R.parse(t2)
    f = [x for x in g2.fields if x.fmt == "kv"][0]
    assert t2[f.kstart:f.kend] == f.key


def test_kv_semicolon_separated_pairs():
    fl, _ = fields("Provider=SQLX;User ID=svc;Password=abc123;", "kv")
    assert has(fl, "User ID", "svc") and has(fl, "Password", "abc123")


# --- indentation / braces ---------------------------------------------------------------------

def test_indent_and_brace_blocks_nest_records():
    t = "request {\n  payee {\n    name: \"Asha\"\n  }\n  amount {\n    units: 5\n  }\n}\n"
    g = R.parse(t)
    blocks = [r for r in g.records if r.fmt == "indent"]
    assert len(blocks) == 3
    outer = min(blocks, key=lambda r: r.start)
    assert all(outer.start <= b.start and b.end <= outer.end for b in blocks)


def test_yaml_like_block_by_indentation():
    t = "emp:\n  name: Asha\n  pay:\n    net: 5\nother: 1\n"
    g = R.parse(t)
    assert len([r for r in g.records if r.fmt == "indent"]) == 2


# --- ambiguity: every parse is kept --------------------------------------------------------------

def test_ambiguity_keeps_every_parse():
    t = 'name: Asha, {"name": "Asha"}\nx|y|z\n1|2|3\n'
    g = R.parse(t)
    assert {"json", "kv", "table"} <= {f.fmt for f in g.fields}


def test_placeholder_values():
    for v in ("", "  ", "NIL", "n/a", "____", "xxxxx", "-"):
        assert R.is_placeholder(v)
    assert not R.is_placeholder("Asha")
