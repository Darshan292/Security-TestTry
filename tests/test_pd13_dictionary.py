"""PD-13 L3 dictionary: schema, provenance, matching."""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest

from pgw.pd13.dictionary import CONCEPTS, PATH, Dictionary, key_words, norm_key

ENTRIES = json.loads(PATH.read_text())
# the only standards PD13_DESIGN.md allows
_STANDARDS = ("schema.org", "vCard RFC 6350", "SAP HCM PA infotype", "Workday Get_Workers", "SAP SuccessFactors OData",
              "HR Open Standards", "ISO 20022", "EPFO", "ESIC", "Income Tax Form 16")
DEV3 = Path(__file__).resolve().parents[1] / "eval" / "data3" / "dev3build.jsonl"


def dev3_lines() -> list[str]:
    if DEV3.exists(): return DEV3.read_text(encoding="utf-8").splitlines()
    compressed = DEV3.with_suffix(DEV3.suffix + ".gz")
    if compressed.exists():
        with gzip.open(compressed, "rt", encoding="utf-8") as f: return f.read().splitlines()
    pytest.skip("sealed dev3build synthetic split is not present in this source archive")


def test_every_entry_has_key_concept_and_provenance():
    assert ENTRIES
    for e in ENTRIES:
        assert isinstance(e.get("key"), str) and e["key"].strip(), e
        assert e.get("concept") in CONCEPTS, e
        assert isinstance(e.get("provenance"), str) and e["provenance"].strip(), e


def test_provenance_is_standard_or_dev3_item():
    for e in ENTRIES:
        kind, _, rest = e["provenance"].partition(":")
        assert kind in ("standard", "dev3"), e
        assert rest.strip(), e
        if kind == "standard":
            assert any(src in rest for src in _STANDARDS), e
        else:
            assert re.fullmatch(r"dev3build-[\w]+-\d{3}", rest), e


def test_dev3_provenance_ids_exist_in_dev3build_only():
    ids = {json.loads(l)["id"] for l in dev3_lines() if l.strip()}
    for e in ENTRIES:
        if e["provenance"].startswith("dev3:"):
            assert e["provenance"][5:] in ids, e


def test_optional_flags_are_booleans_and_unknown_fields_are_rejected():
    allowed = {"key", "concept", "provenance", "needs_evidence", "suffix", "anyword"}
    for e in ENTRIES:
        assert set(e) <= allowed, e
        for f in ("needs_evidence", "suffix", "anyword"):
            assert isinstance(e.get(f, False), bool)


def test_entry_counts_by_provenance_type():
    std = sum(e["provenance"].startswith("standard:") for e in ENTRIES)
    dev = sum(e["provenance"].startswith("dev3:") for e in ENTRIES)
    assert std + dev == len(ENTRIES) and std > 0 and dev > 0


def test_key_normalisation():
    assert norm_key("Date_of_Birth") == norm_key("dateOfBirth") == norm_key("date of birth") == "dateofbirth"
    assert key_words("EPF_CONTRI_REMITTED") == ["epf", "contri", "remitted"]
    assert key_words("annualBasePay") == ["annual", "base", "pay"]


@pytest.fixture(scope="module")
def d():
    return Dictionary.load()


def test_exact_lookup_ignores_case_and_separators(d):
    assert d.lookup((), "GBDAT").concept == "DATE_OF_BIRTH"
    assert d.lookup((), "birth_date").concept == "DATE_OF_BIRTH"
    assert d.lookup((), "Date of Birth").concept == "DATE_OF_BIRTH"
    assert d.lookup((), "no such key at all") is None


def test_path_suffix_lookup(d):
    assert d.lookup(("Doc", "Pmt", "Cdtr", "Nm"), "Nm").concept == "PERSON"
    assert d.lookup(("Doc", "InitgPty", "Nm"), "Nm").needs_evidence
    assert d.lookup(("Doc", "Pmt", "Cdtr", "Nm"), "Nm").needs_evidence is False
    assert d.lookup(("Doc", "CdtrAcct", "Id", "Othr", "Id"), "Id").concept == "BANK_ACCOUNT"


def test_bilingual_key_is_matched_segment_by_segment(d):
    assert d.lookup(("जन्म तिथि / DOB",), "जन्म तिथि / DOB").concept == "DATE_OF_BIRTH"


def test_suffix_and_anyword_entries(d):
    assert d.lookup((), "Last Drawn Salary").concept == "COMPENSATION"
    assert d.lookup((), "Account Holder Name").concept == "PERSON"
    assert d.lookup((), "Nominee for pending dues").concept == "PERSON"


def test_single_word_suffix_needs_two_words(d):
    # a bare "salary" must match only through its own entry, never as a suffix of nothing
    assert d.lookup((), "Totally Unrelated Thing") is None


def test_release_entries_exist_for_dates_and_ids(d):
    assert d.lookup((), "CreDtTm").concept == "RELEASE_DATE"
    assert d.lookup((), "MsgId").concept == "RELEASE_ID"
    assert d.lookup((), "NbOfTxs").concept == "RELEASE_AMOUNT"
