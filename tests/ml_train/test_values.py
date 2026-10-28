import random
import re

import pytest
from ml.train import values as V


def R(seed=0):
    return random.Random(seed)


def test_aadhaar_verhoeff_valid_all_formats():
    r = R()
    for fmt in ("plain", "spaced", "hyphen", "dot", None):
        for _ in range(200):
            a = V.aadhaar(r, fmt)
            d = re.sub(r"\D", "", a)
            assert len(d) == 12 and d[0] in "23456789" and V.verhoeff_valid(d)
    assert V.verhoeff_valid("234123412346")  # known-good published test number
    assert not V.verhoeff_valid("234123412347")


def test_aadhaar_agrees_with_gateway_checksum():
    from pgw.detect.recognizers import verhoeff_valid as gw
    r = R(3)
    for _ in range(100):
        assert gw(re.sub(r"\D", "", V.aadhaar(r)))


def test_pan_structure_and_holder_type():
    r = R()
    for _ in range(300):
        p = V.pan(r, "Sharma")
        assert re.fullmatch(r"[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]", p)
    assert V.pan(r, "Sharma", holder="P")[3:5] == "PS"
    assert V.pan(r, "iyer", holder="C")[3:5] == "CI"
    assert V.pan(r, None, holder="F")[3] == "F"
    share = sum(V.pan(r)[3] == "P" for _ in range(500)) / 500
    assert share > 0.8


def test_ifsc_shape():
    r = R()
    for _ in range(200):
        assert re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", V.ifsc(r))


def test_cards_luhn_and_length():
    r = R()
    for net in ("visa", "mastercard", "rupay", "amex"):
        for fmt in ("plain", "spaced", "hyphen"):
            for _ in range(100):
                c = V.card(r, fmt, net)
                d = re.sub(r"\D", "", c)
                assert V.luhn_valid(d) and len(d) in (15, 16)


def test_luhn_agrees_with_gateway():
    from pgw.detect.recognizers import luhn_valid as gw
    r = R(5)
    for _ in range(100):
        assert gw(re.sub(r"\D", "", V.card(r)))


def test_bank_account_lengths():
    r = R()
    lens = {len(re.sub(r"\D", "", V.bank_account(r))) for _ in range(500)}
    assert min(lens) >= 9 and max(lens) <= 18 and len(lens) > 6
    with pytest.raises(ValueError):
        V.bank_account(r, 8)


def test_upi_phone_email_shapes():
    r = R()
    p = V.person(r)
    for _ in range(100):
        assert re.fullmatch(r"[\w.-]+@[a-z]+", V.upi_id(r, p))
        assert re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", V.email(r, p))
    fmts = {}
    for f in ("plain", "plus91", "plus91_space", "plus91_hyphen", "plus91_nosp", "zero", "split5", "paren", "landline"):
        fmts[f] = V.phone(r, f)
    assert fmts["plus91"].startswith("+91 ") and fmts["zero"].startswith("0") and fmts["plus91_nosp"].startswith("+91")
    assert re.fullmatch(r"[6-9]\d{9}", fmts["plain"])
    with pytest.raises(ValueError):
        V.phone(r, "bogus")


def test_employee_id_styles_vary():
    r = R()
    ids = {re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", V.employee_id(r))) for _ in range(300)}
    assert len(ids) >= 8


def test_indian_grouping_and_words():
    assert V.indian_group(1234567) == "12,34,567"
    assert V.indian_group(999) == "999" and V.indian_group(1000) == "1,000" and V.indian_group(100000) == "1,00,000"
    assert V.indian_group(123456789) == "12,34,56,789"
    assert V.number_words(1200000) == "twelve lakh"
    assert V.number_words(1250000) == "twelve lakh fifty thousand"
    assert V.number_words(45000) == "forty-five thousand"


def test_compensation_covers_every_format():
    r = R()
    seen = {s: V.compensation(r, style=s) for s in ("inr_symbol", "rs_dot", "rs_plain", "inr_code", "slash", "lakh_L", "lakhs",
                                                    "lpa", "words", "k", "plain_group", "rs_slash", "western", "crore",
                                                    "words_rupees", "decimal_paise")}
    assert seen["inr_symbol"].startswith("₹") and seen["rs_dot"].startswith("Rs.") and seen["inr_code"].startswith("INR ")
    assert seen["slash"].endswith("/-") and seen["rs_slash"].endswith("/-")
    assert seen["lpa"].lower().endswith("lpa") and re.search(r"\d ?L$", seen["lakh_L"])
    assert "lakh" in seen["lakhs"] and re.fullmatch(r"[a-z\- ]+", seen["words"])
    assert any(re.search(r"\d{1,2},\d{2},\d{3}", V.compensation(r, style="inr_symbol", kind="annual")) for _ in range(50))


def test_dob_formats_and_other_dates():
    r = R()
    out = {s: V.date_of_birth(r, s) for s in V.DATE_STYLES}
    assert len(set(out.values())) > 8
    for s in out.values():
        yr = re.findall(r"(?<!\d)(19[6-9]\d|200[0-4])(?!\d)|[./-](\d\d)$", s)
        assert yr or re.search(r"\d\d$", s)
    minor = V.date_of_birth(r, "iso", minor=True)
    assert 2009 <= int(minor[:4]) <= 2020


def test_secrets_shapes():
    r = R()
    assert V.secret(r, "aws").startswith("AKIA") and len(V.secret(r, "aws")) == 20
    assert V.secret(r, "sk").startswith("sk-") and V.secret(r, "ghp").startswith("ghp_")
    assert V.secret(r, "jwt").count(".") == 2
    assert len(V.secret(r, "password")) >= 6


def test_names_are_diverse_and_never_contain_newlines():
    r = R()
    regions = set()
    for _ in range(500):
        p = V.person(r)
        regions.add(p.region)
        for st in (None, "full", "last_first", "initial_last", "caps", "first_only", "lower", "first_initial", "last", "initial"):
            s = V.name_surface(r, p, st)
            assert s.strip() and "\n" not in s
    assert len(regions) == len(V.REGIONS) == 9
    with pytest.raises(ValueError):
        V.name_surface(r, p, "nope")


def test_hard_negative_generators_produce_text():
    r = R()
    for fn in V.NEGATIVE_GENERATORS.values():
        for _ in range(30):
            s = fn(r)
            assert isinstance(s, str) and s
    assert "Vijayawada" in V.PLACES and "Asha" not in V.PLACES


def test_deterministic_with_seed():
    a = [V.aadhaar(R(9)), V.pan(R(9), "Rao"), V.compensation(R(9)), V.person(R(9)).full(), V.secret(R(9))]
    b = [V.aadhaar(R(9)), V.pan(R(9), "Rao"), V.compensation(R(9)), V.person(R(9)).full(), V.secret(R(9))]
    assert a == b
