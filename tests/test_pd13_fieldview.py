"""PD-13: L1-derived field view and name cells; coordinate round-trip of detections through D0 + L0+ maps."""
from __future__ import annotations

import random

import pytest

from pgw.normalize import normalize
from pgw.pd13 import records as R
from pgw.pd13.fieldview import field_view, name_cells, name_shaped_cell
from tests.test_pd13_policy import make, spans


@pytest.mark.parametrize("v,ok", [
    ("Asha Rao", True), ("ASHA RAO", True), ("V. NAGPAL", True), ("সোহিনী সেন", True), ("मृणाल जोशी", True),
    ("Anne-Marie O'Neil", True),
    ("asha rao", False), ("Asha", False), ("Asha Rao 2", False), ("Kesarivan Agro Foods Private Limited", False),
    ("Polytechnic Institute", False), ("Plant Operations Department", False), ("a@b.in x", False),
])
def test_name_shaped_cell(v, ok):
    assert name_shaped_cell(v) is ok


def test_name_cells_cover_fields_keys_and_own_line_names():
    t = ("Presented by: Yangchen Menon\n"
         "Tenzin Nongrum: technical 3/5\n"
         "Hina Hussain\n"
         "B.Com (Hons)\n"
         "// HENRIK SCHMIDT\n"
         "Name | Branch\nKiran Kumar | Civil\nMeera Iyer | Civil\n")
    g = R.parse(t)
    cells = {t[a:b] for a, b, _ in name_cells(t, g)}
    assert {"Yangchen Menon", "Tenzin Nongrum", "Hina Hussain", "HENRIK SCHMIDT", "Kiran Kumar", "Meera Iyer"} <= cells
    assert "B.Com (Hons)" not in cells


def test_name_cells_titlecase_all_caps_for_the_ner_text_only():
    t = "Name: ASHA RAO\n"
    g = R.parse(t)
    (a, b, nt), = [c for c in name_cells(t, g) if t[c[0]:c[1]] == "ASHA RAO"]
    assert nt == "Asha Rao" and t[a:b] == "ASHA RAO" and len(nt) == b - a


def test_name_cells_respect_the_limit():
    t = "\n".join(f"Person{chr(65 + i % 26)} Name{chr(65 + i // 26)}" for i in range(200))
    assert len(name_cells(t, R.parse(t), limit=7)) <= 7


def test_field_view_one_value_per_line_maps_back():
    t = "id#~#name#~#amt\n1#~#Asha Rao#~#500\n2#~#Ravi Nair#~#600\n"
    g = R.parse(t)
    v = field_view(t, g)
    assert v is not None and "Asha Rao\n" in v.text
    i = v.text.index("Ravi Nair")
    a, b = v.to_base(i, i + len("Ravi Nair"))
    assert t[a:b] == "Ravi Nair"
    assert all(0 <= x <= y <= len(t) for x, y in zip(v.smap, v.emap))


def test_field_view_none_without_fields():
    assert field_view("plain prose", R.parse("plain prose")) is None


# --- detection coordinates are original-string offsets, whatever view found them -----------------------

@pytest.mark.parametrize("text", [
    "Dear Asha Rao, the id is KAF 10001 and PAN ABCPE1234F.",
    "Zero​width: Dear A​sha Rao, dob 05/03/1990.",
    "Fullwidth ２０２６ Dear Asha Rao, ref 98765432101234 tail",
    "Dear Asha Rao,\n account 1234 5678\n9012 3456 on file.",
    "Dear Asha Rao, pan &#65;BCPE1234F entity",
])
def test_detections_lie_inside_the_original_text_and_slice_back(text):
    p = make(True)
    dets = p.detect_text(text)
    assert dets
    for d in dets:
        assert 0 <= d.start < d.end <= len(text)
        assert text[d.start:d.end].strip()


@pytest.mark.parametrize("seed", range(25))
def test_random_documents_never_produce_out_of_range_spans(seed):
    rng = random.Random(seed)
    pieces = ["Dear Asha Rao", "KAF 10001", "05/03/1990", "52,620.00", "a|b|c", "x,y,z", "{\"k\": \"v\"}", "<a><b>t</b></a>",
              "\n", "  ", "name: Meera Iyer", "98765432101234", "&amp;", "​", "f3A9x8Lq2ZpR7vT1mWk4", "k=v;j=w"]
    text = "".join(rng.choice(pieces) + rng.choice([" ", "\n", ""]) for _ in range(rng.randint(1, 30)))
    p = make(rng.random() < 0.5)
    for d in p.detect_text(text):
        assert 0 <= d.start < d.end <= len(text)
    base, om = normalize(text)
    assert om[-1] == len(text)
