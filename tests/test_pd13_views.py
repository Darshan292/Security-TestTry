"""PD-13 L0+ views: examples, negatives and seeded property tests on the offset maps."""
from __future__ import annotations

import random

import pytest

from pgw.pd13.views import apply_edits, extra_views, identity_view


def by_name(text):
    return {v.name: v for v in extra_views(text)}


# --- unwrap -------------------------------------------------------------------------------------

def test_unwrap_joins_digit_group_split_by_a_line_break():
    v = by_name("Account 1234 5678\n9012 3456 on file")["unwrapped"]
    assert "1234 56789012 3456" in v.text


def test_unwrap_joins_soft_break_marker():
    v = by_name("mail rahul.sharma=\n@acme.example now")["unwrapped"]
    assert "rahul.sharma@acme.example" in v.text


def test_unwrap_joins_address_broken_at_the_margin():
    line = "x" * 60 + " rahul.sharma@acme"
    v = by_name(line + "\n.example.com ok")["unwrapped"]
    assert "rahul.sharma@acme.example.com" in v.text


def test_unwrap_leaves_table_rows_and_short_lines_alone():
    assert "unwrapped" not in by_name("Name,ID\nAsha,1234\nRavi,5678\n")
    assert "unwrapped" not in by_name("Courier AWB: AWB0621733843\nAsset tag: 7")   # next line is a new field


def test_unwrap_does_not_join_a_key_line():
    long_line = "Received: " + "a" * 60 + "1"
    assert "unwrapped" not in by_name(long_line + "\nX-Header: value")


# --- unescape / OCR / id gap -------------------------------------------------------------------

def test_unescape_numeric_and_named_references():
    v = by_name("pan &#65;BCPE1234F &amp; x")["unescaped"]
    assert v.text == "pan ABCPE1234F & x"


def test_unescape_unchanged_text_has_no_view():
    assert "unescaped" not in by_name("plain & text")


def test_unescape_replacement_covers_the_whole_reference():
    t = "id &#49;&#50;&#51;&#52; end"
    v = by_name(t)["unescaped"]
    i = v.text.index("1234")
    assert v.to_base(i, i + 4) == (t.index("&#49;"), t.index(" end"))


def test_ocr_digit_confusion_only_inside_digit_dominant_tokens():
    v = by_name("acct 5O4l729B3 end")["ocr_digits"]
    assert "504172983" in v.text
    assert "ocr_digits" not in by_name("word BOSS and SLIB")          # letters only
    assert "ocr_digits" not in by_name("ABCDE1234F")                  # PAN: other letters break the run


def test_id_gap_removes_the_space_in_a_letters_then_digits_code():
    v = by_name("employee code is KAF 18514.")["id_gap"]
    assert "KAF18514" in v.text
    assert "id_gap" not in by_name("version 3 of the plan")
    assert "id_gap" not in by_name("Rs 12345")                         # not capitals + >= 4 digits? "Rs" has a lowercase


# --- offset-map properties ---------------------------------------------------------------------

_ALPHABET = list("abcXYZ0123456789O lIS\n =&#;") + ["&amp;", "&#49;", " KAF ", "=\n", "1234 ", "\n"]


def _rand_text(rng: random.Random) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 80)))


@pytest.mark.parametrize("seed", range(40))
def test_view_maps_are_monotone_and_unchanged_chars_round_trip(seed):
    rng = random.Random(seed)
    text = _rand_text(rng)
    for v in extra_views(text):
        assert len(v.smap) == len(v.emap) == len(v.text)
        assert all(0 <= a <= b <= len(text) for a, b in zip(v.smap, v.emap))
        assert v.smap == sorted(v.smap) and v.emap == sorted(v.emap)
        for i, ch in enumerate(v.text):
            if v.emap[i] - v.smap[i] == 1 and text[v.smap[i]] == ch:
                assert v.to_base(i, i + 1) == (v.smap[i], v.smap[i] + 1)
        if v.text:
            lo, hi = v.to_base(0, len(v.text))
            assert 0 <= lo <= hi <= len(text)


@pytest.mark.parametrize("seed", range(40))
def test_span_in_a_view_maps_to_a_base_span_that_contains_its_unedited_characters(seed):
    rng = random.Random(1000 + seed)
    text = _rand_text(rng)
    for v in extra_views(text):
        if len(v.text) < 2:
            continue
        a = rng.randrange(0, len(v.text) - 1)
        b = rng.randrange(a + 1, len(v.text) + 1)
        lo, hi = v.to_base(a, b)
        assert lo <= hi
        kept = [v.text[i] for i in range(a, b) if v.emap[i] - v.smap[i] == 1 and text[v.smap[i]] == v.text[i]]
        sub = text[lo:hi]
        pos = 0
        for ch in kept:                                   # unedited chars appear in order inside the mapped span
            pos = sub.find(ch, pos)
            assert pos >= 0
            pos += 1


def test_apply_edits_composes_maps():
    base = identity_view("base", "ab--cd--ef")
    v1 = apply_edits(base, "v1", [(2, 4, ""), (6, 8, "")])           # abcdef
    v2 = apply_edits(v1, "v2", [(2, 4, "X")])                         # abXef: X covers c,d plus the removed dashes between
    assert v2.text == "abXef"
    assert v2.to_base(2, 3) == (4, 6)
    assert v2.to_base(0, 5) == (0, 10)
