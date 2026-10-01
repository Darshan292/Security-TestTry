"""PD-13 L2 shape candidates: DATE, HIGH_ENTROPY, LONG_DIGITS, AMOUNT."""
from __future__ import annotations

import pytest

from pgw.pd13 import shapes as S
from pgw.types import EntityType as T


def kinds(text):
    return [(c.kind, text[c.start:c.end]) for c in S.shapes(text)]


@pytest.mark.parametrize("text,expected", [
    ("born 21-MAR-93", "21-MAR-93"),
    ("dob 05/03/1990", "05/03/1990"),
    ("dob 5.3.90", "5.3.90"),
    ("on 1990-05-12 ok", "1990-05-12"),
    ("on 1990-05-12T00:00:00 ok", "1990-05-12"),         # midnight is a date, not a timestamp
    ("on 19900512 ok", "19900512"),
    ("5 Sep 2026", "5 Sep 2026"),
    ("5th September, 2026", "5th September, 2026"),
    ("Sep 5, 2026", "Sep 5, 2026"),
    ("19-May-2026", "19-May-2026"),
])
def test_date_forms(text, expected):
    assert ("DATE", expected) in kinds(text)


@pytest.mark.parametrize("text", [
    "at 2026-03-09T12:26:07.137+05:30 done",              # event timestamp
    "Sat, 11 Jul 2026 12:05:06 +0530",
    "ref PAY-20260804-25435 x",                           # compact date inside an identifier
    "v1.2.3 and ip 10.41.44.21",
    "month May alone",
    "13/13/2020",
])
def test_not_a_date(text):
    assert not any(k == "DATE" for k, _ in kinds(text))


def test_date_carries_year_month_day_and_two_digit_flag():
    c = [x for x in S.shapes("21-MAR-93 and 5 Sep 2026") if x.kind == "DATE"]
    assert c[0].ymd == (93, 3, 21, True) and c[1].ymd == (2026, 9, 5, False)


@pytest.mark.parametrize("text,expected", [
    ("pay 1,23,456.00 now", "1,23,456.00"),
    ("pay 123,456 now", "123,456"),
    ("pay 52620.00 now", "52620.00"),
    ("pay 3.55 lpa now", "3.55 lpa"),
    ("pay Rs. 10000 now", "Rs. 10000"),
    ("pay INR 4,95,000 now", "INR 4,95,000"),
    ("a,52620.00,b", "52620.00"),                         # after a CSV comma
])
def test_amount_forms(text, expected):
    assert ("AMOUNT", expected) in kinds(text)


@pytest.mark.parametrize("text", ["rs0 here", "ratio 7.05 here", "version 1.20", "year 2026", "count 123", "0.00"])
def test_not_an_amount(text):
    assert not any(k == "AMOUNT" for k, _ in kinds(text))


def test_long_digits_bounds():
    assert ("LONG_DIGITS", "827098140303") in kinds("no 827098140303 ok")
    assert ("LONG_DIGITS", "123456789") in kinds("no 123456789 ok")
    assert not any(k == "LONG_DIGITS" for k, _ in kinds("no 12345678 ok"))                 # 8 digits
    assert not any(k == "LONG_DIGITS" for k, _ in kinds("no 1234567890123456789 ok"))      # 19 digits
    assert not any(k == "LONG_DIGITS" for k, _ in kinds("x-1234567890 y"))                  # hyphen-attached


def test_high_entropy_positive_cases():
    assert ("HIGH_ENTROPY", "f3A9x8Lq2ZpR7vT1mWk4") in kinds("token f3A9x8Lq2ZpR7vT1mWk4 end")
    assert any(k == "HIGH_ENTROPY" for k, _ in kinds("id 08da364a-6888-466d-bb98-a55db669cfe9"))
    assert any(k == "HIGH_ENTROPY" for k, _ in kinds("b64 PGh0bWw+PGJvZHk+PHA+UmVxdWVzdGluZyBhcHBy"))


@pytest.mark.parametrize("tok", [
    "FieldJustification", "Content-Transfer-Encoding", "X-MS-Exchange-Organization-AuthSource",
    "TLS_ECDHE_RSA_WITH_AES256_GCM_SHA384", "/sites/Payroll/_api/web/GetFolderBySe", "OrgnlGrpInfAndSts",
    "aaaaaaaaaaaaaaaaaaaa", "1234567890123456789", "short1A",
])
def test_high_entropy_negative_cases(tok):
    assert not any(k == "HIGH_ENTROPY" for k, _ in kinds(f"x {tok} y"))


def test_entropy_thresholds():
    assert S._entropy("aaaa") == 0
    assert S._entropy("abcdefghijklmnop") == pytest.approx(4.0)
    assert S._classes("abc") == 1 and S._classes("aB3_") == 4


def test_identifier_of_words():
    assert S.identifier_of_words("FieldJustification")
    assert S.identifier_of_words("SHA384-Encoding")
    assert not S.identifier_of_words("F04rPmjF6XF0")
    assert not S.identifier_of_words("xQzLpWbNmKjHgFdS")


def test_overlap_priority_date_over_amount_over_digits():
    # "19.05.2026" is a date although "19.05" alone could read as an amount
    assert kinds("on 19.05.2026 x") == [("DATE", "19.05.2026")]
    assert kinds("n 12345678901234 x") == [("LONG_DIGITS", "12345678901234")]


def test_provisional_types():
    assert S.Shape(0, 1, "DATE").type is T.DATE_OF_BIRTH
    assert S.Shape(0, 1, "HIGH_ENTROPY").type is T.SECRET
    assert S.Shape(0, 1, "LONG_DIGITS").type is T.BANK_ACCOUNT
    assert S.Shape(0, 1, "AMOUNT").type is T.COMPENSATION
