import random

import pytest

from pgw.detect.recognizers import (
    detect_d2, detect_fields, luhn_valid, verhoeff_check_digit, verhoeff_valid,
)
from pgw.types import EntityType as T


def spans(text, typ=None):
    return [(text[d.start:d.end], d.type) for d in detect_d2(text) if typ is None or d.type == typ]


def vals(text, typ):
    return [v for v, _ in spans(text, typ)]


def aadhaar_num(prefix11="23456789012"):
    return prefix11 + verhoeff_check_digit(prefix11)


def bad_aadhaar_num(prefix11="23456789012"):
    good = aadhaar_num(prefix11)
    return prefix11 + str((int(good[-1]) + 1) % 10)


def luhn_complete(prefix):
    for c in range(10):
        if luhn_valid(prefix + str(c)):
            return prefix + str(c)


def group4(n):
    return " ".join(n[i:i + 4] for i in range(0, len(n), 4))


# ---------------------------------------------------------------- contract
def test_empty_and_plain():
    assert detect_d2("") == []
    assert detect_d2("Hello world, nothing to see here.") == []


def test_output_sorted_nonoverlapping_and_source():
    text = "x.y@corp.com 98200 12345 ABCPE1234F sk-" + "a" * 30
    ds = detect_d2(text)
    assert [d.start for d in ds] == sorted(d.start for d in ds)
    for a, b in zip(ds, ds[1:]):
        assert a.end <= b.start
    assert all(d.source.startswith("D2:") for d in ds)
    assert all(0 < d.score <= 1 for d in ds)


def test_offsets_are_codepoints():
    text = "नमस्ते ₹ ok a@b.com"
    d = detect_d2(text)[0]
    assert text[d.start:d.end] == "a@b.com"


# ---------------------------------------------------------------- checksums
def test_verhoeff_known_vectors():
    assert verhoeff_check_digit("236") == "3"
    assert verhoeff_valid("2363")
    assert not verhoeff_valid("2364")
    assert verhoeff_check_digit("142857") == "0"  # canonical test vector
    assert verhoeff_valid("1428570")


def test_verhoeff_roundtrip_and_transposition():
    rnd = random.Random(1)
    for _ in range(200):
        p = "".join(rnd.choice("0123456789") for _ in range(11))
        full = p + verhoeff_check_digit(p)
        assert verhoeff_valid(full)
        # any single-digit substitution is caught
        i = rnd.randrange(12)
        alt = str((int(full[i]) + 1) % 10)
        assert not verhoeff_valid(full[:i] + alt + full[i + 1:])


def test_luhn_known():
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")


# ---------------------------------------------------------------- EMAIL
@pytest.mark.parametrize("addr", [
    "rahul.sharma@esu.com", "a_b+tag@sub.corp.co.in", "HR-Team@Example.ORG", "x9@a-b.io",
])
def test_email_positive(addr):
    ds = detect_d2(f"Contact {addr} today")
    assert [(d.type, d.score) for d in ds] == [(T.EMAIL, 0.99)]
    assert f"Contact {addr} today"[ds[0].start:ds[0].end] == addr


def test_email_trailing_punct_and_angle():
    assert vals("Mail <ab@corp.com>.", T.EMAIL) == ["ab@corp.com"]
    assert vals("Mail ab@corp.com.", T.EMAIL) == ["ab@corp.com"]
    assert vals("mailto:ab@corp.com,cd@corp.com", T.EMAIL) == ["ab@corp.com", "cd@corp.com"]


@pytest.mark.parametrize("s", ["user@localhost", "user@", "@corp.com", "user@corp", "a @ b.com", "user@corp.c"])
def test_email_negative(s):
    assert vals(s, T.EMAIL) == []


# ---------------------------------------------------------------- UPI
@pytest.mark.parametrize("h", [
    "rahul.sharma@ybl", "9820012345@paytm", "priya-k@okaxis", "amit_99@oksbi", "some.one@okhdfcbank",
    "abc123@upi", "name.x@jupiteraxis", "nn@axl", "nn@ibl", "nn@ptyes", "nn@ptsbi", "nn@freecharge",
])
def test_upi_positive(h):
    ds = detect_d2(f"pay to {h} now")
    assert [d.type for d in ds] == [T.UPI_ID]
    assert ds[0].score == 0.95
    assert f"pay to {h} now"[ds[0].start:ds[0].end] == h


@pytest.mark.parametrize("s", [
    "rahul@ybl.com", "rahul@okaxis.co.in", "rahul@sbi.co.in", "rahul@icici.com", "rahul@unknownpsp", "rahul@yblx",
])
def test_upi_not_on_emails_or_unknown(s):
    assert vals(s, T.UPI_ID) == []


def test_upi_sentence_end_and_email_preferred():
    assert vals("Send to rahul@ybl.", T.UPI_ID) == ["rahul@ybl"]
    ds = detect_d2("rahul@icici.com")
    assert [d.type for d in ds] == [T.EMAIL]


# ---------------------------------------------------------------- PHONE
@pytest.mark.parametrize("p", [
    "9820012345", "98200 12345", "98200-12345", "+91 9820012345", "+91-98200-12345", "+91 98200 12345",
    "(+91) 98200 12345", "919820012345", "09820012345", "0 98200 12345".replace("0 ", "0"), "6000000000",
    "7123456789", "8123456789", "982 001 2345", "9820 012 345",
])
def test_phone_india_positive(p):
    t = f"Call {p} please"
    ds = [d for d in detect_d2(t) if d.type == T.PHONE]
    assert len(ds) == 1 and t[ds[0].start:ds[0].end] == p and ds[0].score == 0.9


@pytest.mark.parametrize("s", [
    "1234567890",            # starts with 1
    "5820012345",            # starts with 5
    "98200123456",           # 11 digits, not a valid prefix form
    "98200 1234",            # 9 digits
    "A9820012345",           # glued to letters
    "9820012345X",
    "98200123451234",        # longer digit run
    "INV-2024-000123",
])
def test_phone_negative(s):
    assert vals(f"ref {s} end", T.PHONE) == []


def test_phone_two_numbers_space_separated():
    assert vals("9820012345 9820012346", T.PHONE) == ["9820012345", "9820012346"]


def test_phone_international():
    ds = [d for d in detect_d2("Reach +44 20 7946 0958 or +1 (415) 555-2671 or +1-415-555-2671") if d.type == T.PHONE]
    assert len(ds) == 3 and all(d.score == 0.8 for d in ds)
    assert vals("+44 20 7946 0958", T.PHONE) == ["+44 20 7946 0958"]


def test_phone_intl_negative_and_india_preferred():
    assert vals("+12 34", T.PHONE) == []
    ds = detect_d2("+91 98200 12345")
    assert [(d.type, d.score) for d in ds] == [(T.PHONE, 0.9)]


# ---------------------------------------------------------------- AADHAAR
def test_aadhaar_grouped_no_context():
    n = aadhaar_num()
    for fmt in (group4(n), group4(n).replace(" ", "-")):
        ds = detect_d2(f"number {fmt} here")
        assert [(d.type, d.score) for d in ds] == [(T.AADHAAR, 0.85)]


def test_aadhaar_grouped_with_context_scores_higher():
    n = aadhaar_num()
    ds = detect_d2(f"Aadhaar: {group4(n)}")
    assert [(d.type, d.score) for d in ds] == [(T.AADHAAR, 0.95)]


@pytest.mark.parametrize("kw", ["Aadhaar no", "AADHAR", "UIDAI", "uid", "आधार", "Aadhaar number is"])
def test_aadhaar_contiguous_needs_context(kw):
    n = aadhaar_num()
    ds = detect_d2(f"{kw} {n}")
    assert [(d.type, d.score) for d in ds] == [(T.AADHAAR, 0.85)]


def test_aadhaar_contiguous_without_context_not_detected():
    assert detect_d2(f"order {aadhaar_num()} shipped") == []


def test_aadhaar_context_window_is_40_chars():
    n = aadhaar_num()
    assert detect_d2("aadhaar" + " " * 30 + n)  # 31 chars away
    assert not detect_d2("aadhaar" + " " * 45 + n)


def test_aadhaar_bad_checksum_rejected_even_with_context():
    assert detect_d2(f"Aadhaar {bad_aadhaar_num()}") == []
    assert detect_d2(f"Aadhaar {group4(bad_aadhaar_num())}") == []


@pytest.mark.parametrize("first", ["0", "1"])
def test_aadhaar_first_digit_2_to_9(first):
    p = first + "3456789012"
    n = p + verhoeff_check_digit(p)
    assert len(n) == 12 and verhoeff_valid(n)
    assert detect_d2(f"Aadhaar {n}") == []
    assert detect_d2(f"{group4(n)}") == []


def test_aadhaar_not_inside_longer_token():
    n = aadhaar_num()
    assert all(d.type != T.AADHAAR for d in detect_d2(f"Aadhaar 9{n}"))
    assert all(d.type != T.AADHAAR for d in detect_d2(f"Aadhaar {n}9"))
    assert all(d.type != T.AADHAAR for d in detect_d2(f"Aadhaar A{n}"))


def test_aadhaar_random_verhoeff_passers_without_context_are_ignored():
    # Ten-percent-of-random property: contiguous 12-digit numbers w/o context never fire.
    rnd = random.Random(7)
    hits = 0
    for _ in range(300):
        p = str(rnd.randint(2, 9)) + "".join(rnd.choice("0123456789") for _ in range(10))
        n = p + verhoeff_check_digit(p)
        t = f"order id {n}"
        hits += any(d.type == T.AADHAAR for d in detect_d2(t))
    assert hits == 0


def test_order_id_bad_verhoeff_not_flagged():
    assert detect_d2(f"Order id {bad_aadhaar_num()} shipped") == []


def test_aadhaar_inside_16_digit_card_group_not_aadhaar():
    card = luhn_complete("411111111111111")
    assert [d.type for d in detect_d2(f"card {group4(card)}")] == [T.CARD]


# ---------------------------------------------------------------- PAN
@pytest.mark.parametrize("pan", ["ABCPE1234F", "AAAAA0000A".replace("AAAAA", "AAAHA"), "BNZAA2318J", "ZZZTZ9999Z"])
def test_pan_positive(pan):
    ds = detect_d2(f"PAN {pan}.")
    assert [(d.type, d.score) for d in ds] == [(T.PAN, 0.95)]


@pytest.mark.parametrize("s", [
    "abcpe1234f",       # lowercase
    "ABCDE1234F",       # 4th char D invalid
    "ABCPE12345",       # last must be letter
    "ABCPE123F",        # too short
    "XABCPE1234F",      # glued
    "ABCPE1234FX",
    "ABCPE1234F9",
])
def test_pan_negative(s):
    assert vals(f"pan {s} ok", T.PAN) == []


# ---------------------------------------------------------------- IFSC
@pytest.mark.parametrize("c", ["HDFC0001234", "SBIN0RRCBGB", "ICIC0000047", "UTIB0000001"])
def test_ifsc_positive(c):
    assert vals(f"IFSC: {c}", T.IFSC) == [c]


@pytest.mark.parametrize("s", [
    "HDFC1001234",        # fifth char must be 0
    "HDF0001234",         # 3 letters
    "hdfc0001234",        # lowercase
    "HDFC000123",         # short
    "HDFC00012345",       # long
    "ACCOUNTING", "DEPARTMENT", "INVOICE",
    "AHDFC0001234",
])
def test_ifsc_negative(s):
    assert vals(f"x {s} y", T.IFSC) == []


# ---------------------------------------------------------------- CARD
CARDS = {
    "visa": luhn_complete("411111111111111"),
    "visa13": luhn_complete("422222222222"),
    "visa19": luhn_complete("401288888888188888"),
    "mc51": luhn_complete("510510510510510"),
    "mc55": luhn_complete("555555555555444"),
    "mc2221": luhn_complete("222100000000000"),
    "mc2720": luhn_complete("272099999999999"),
    "amex34": luhn_complete("34000000000000"),
    "amex37": luhn_complete("37828224631000"),
    "rupay60": luhn_complete("608000000000000"),
    "rupay65": luhn_complete("652100000000000"),
    "rupay81": luhn_complete("817200000000000"),
    "rupay82": luhn_complete("827200000000000"),
    "rupay508": luhn_complete("508500000000000"),
    "diners36": luhn_complete("30569309025904".replace("30", "36", 1)),
    "diners38": luhn_complete("38520000023237"[:-1]),
}


@pytest.mark.parametrize("name", sorted(CARDS))
def test_card_contiguous_positive(name):
    n = CARDS[name]
    assert luhn_valid(n)
    ds = detect_d2(f"card {n}.")
    assert [(d.type, d.score) for d in ds] == [(T.CARD, 0.95)], name
    assert f"card {n}."[ds[0].start:ds[0].end] == n


def test_card_separators():
    n = CARDS["visa"]
    assert vals(f"c {group4(n)} d", T.CARD) == [group4(n)]
    assert vals(f"c {group4(n).replace(' ', '-')} d", T.CARD) == [group4(n).replace(" ", "-")]
    amex = CARDS["amex37"]
    spaced = f"{amex[:4]} {amex[4:10]} {amex[10:]}"
    assert vals(f"c {spaced} d", T.CARD) == [spaced]


def test_card_bad_luhn_or_iin_or_length():
    n = CARDS["visa"]
    bad = n[:-1] + str((int(n[-1]) + 1) % 10)
    assert vals(f"c {bad}", T.CARD) == []
    assert vals(f"c {luhn_complete('711111111111111')}", T.CARD) == []       # IIN 7 not accepted
    assert vals(f"c {luhn_complete('41111111111')}", T.CARD) == []           # 12 digits
    assert vals(f"c {luhn_complete('4' * 19)}", T.CARD) == []                # 20 digits
    assert vals(f"c {luhn_complete('222000000000000')}", T.CARD) == []       # 2220 outside range
    assert vals(f"c {luhn_complete('272100000000000')}", T.CARD) == []       # 2721 outside range


def test_card_mixed_separators_and_glued_rejected():
    n = CARDS["visa"]
    assert vals(f"c {n[:4]}-{n[4:8]} {n[8:12]}-{n[12:]}", T.CARD) == []
    assert vals(f"c X{n}", T.CARD) == []
    assert vals(f"c {n}X", T.CARD) == []


def test_card_followed_by_other_number_after_space():
    n = CARDS["visa"]
    assert vals(f"card {n} 5 items", T.CARD) == [n]


# ---------------------------------------------------------------- BANK_ACCOUNT
@pytest.mark.parametrize("kw", [
    "account no", "Account Number", "A/C", "a/c no.", "acct", "Bank Account", "beneficiary account", "account no:",
    "Acct No.", "account #",
])
def test_bank_account_positive(kw):
    t = f"{kw} 50100234567890 done"
    ds = [d for d in detect_d2(t) if d.type == T.BANK_ACCOUNT]
    assert len(ds) == 1 and t[ds[0].start:ds[0].end] == "50100234567890" and ds[0].score == 0.85


def test_bank_account_lengths_and_window():
    assert vals("a/c 123456789", T.BANK_ACCOUNT) == ["123456789"]
    assert vals("a/c 123456789012345678", T.BANK_ACCOUNT) == ["123456789012345678"]
    assert vals("a/c 12345678", T.BANK_ACCOUNT) == []
    assert vals("a/c 1234567890123456789", T.BANK_ACCOUNT) == []
    assert vals("a/c " + " " * 20 + "123456789", T.BANK_ACCOUNT) == ["123456789"]
    assert vals("a/c " + " " * 40 + "123456789", T.BANK_ACCOUNT) == []


def test_bank_account_needs_context_and_is_after_only():
    assert vals("value 50100234567890", T.BANK_ACCOUNT) == []
    assert vals("50100234567890 is the a/c", T.BANK_ACCOUNT) == []


def test_bank_account_spaced_groups():
    assert vals("account number 5010 0234 5678", T.BANK_ACCOUNT) == ["5010 0234 5678"]


def test_bank_account_excludes_card_and_aadhaar():
    card = CARDS["visa"]
    assert [d.type for d in detect_d2(f"account no {card}")] == [T.CARD]
    n = aadhaar_num()
    assert [d.type for d in detect_d2(f"a/c {group4(n)}")] == [T.AADHAAR]
    ds = detect_d2(f"Aadhaar {n} account no {n}")
    assert all(d.type == T.AADHAAR for d in ds)


def test_bank_account_not_in_longer_token():
    assert vals("a/c X123456789", T.BANK_ACCOUNT) == []
    assert vals("a/c 123456789X", T.BANK_ACCOUNT) == []


# ---------------------------------------------------------------- SECRET
@pytest.mark.parametrize("tok", [
    "sk-" + "a" * 20,
    "sk-proj-" + "Ab3_" * 10,
    "sk-ant-api03-" + "Zx-9" * 8,
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_" + "A1" * 18,
    "gho_" + "b" * 36,
    "ghs_" + "c" * 40,
    "xoxb-1234567890-abcdefghij",
    "xoxp-123456789012-123456789012-abcdef",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
])
def test_secret_tokens(tok):
    t = f"token={tok}!"
    ds = [d for d in detect_d2(t) if d.type == T.SECRET]
    assert ds and all(d.score == 0.99 for d in ds)
    assert any(t[d.start:d.end] == tok for d in ds)


def test_secret_exact_span_openai():
    t = "key is sk-proj-abcdefghijklmnopqrstuv, thanks"
    assert vals(t, T.SECRET) == ["sk-proj-abcdefghijklmnopqrstuv"]


@pytest.mark.parametrize("s", [
    "sk-short", "task-" + "a" * 30, "AKIA" + "A" * 15, "AKIAIOSFODNN7EXAMPLe", "ghp_" + "a" * 35, "xoxb-short",
    "eyJhbGciOiJIUzI1NiJ9.notjwt.sig", "risk-" + "a" * 30,
])
def test_secret_negative(s):
    assert vals(f"see {s} end", T.SECRET) == []


def test_secret_authorization_headers():
    assert vals("Authorization: Bearer abc123.def-456_x", T.SECRET) == ["abc123.def-456_x"]
    assert vals("authorization: bearer Zm9vYmFy", T.SECRET) == ["Zm9vYmFy"]
    assert vals("Authorization: Basic dXNlcjpwYXNz", T.SECRET) == ["dXNlcjpwYXNz"]
    assert vals('{"Authorization": "Bearer tok.en/+="}', T.SECRET) == ["tok.en/+="]
    assert vals("Authorization: Digest abc", T.SECRET) == []
    assert vals("Authorization header is required", T.SECRET) == []


def test_secret_standalone_bearer():
    assert vals("use Bearer abcdEFGH1234567890xyz here", T.SECRET) == ["abcdEFGH1234567890xyz"]
    assert vals("Bearer authentication is used", T.SECRET) == []


@pytest.mark.parametrize("text,val", [
    ("password=hunter2", "hunter2"),
    ("password: hunter2", "hunter2"),
    ('password = "hun ter2"', "hun ter2"),
    ("passwd: 'p@ss w0rd!'", "p@ss w0rd!"),
    ("pwd=abc123;", "abc123"),
    ("secret: s3cr3t,", "s3cr3t"),
    ("API_KEY=abcd1234", "abcd1234"),
    ("apikey: abcd1234", "abcd1234"),
    ("api-key = abcd1234", "abcd1234"),
    ("client_secret=Zz9Yy8", "Zz9Yy8"),
    ('{"password": "hunter2"}', "hunter2"),
    ('{"clientSecret": "hunter2"}', "hunter2"),
    ("DB_PASSWORD=hunter2", "hunter2"),
    ("dbPassword: hunter2", "hunter2"),
    ("Password: Tr0ub4dor&3", "Tr0ub4dor&3"),
    ('{"pwd": 12345}', "12345"),
])
def test_secret_key_value(text, val):
    assert vals(text, T.SECRET) == [val]


@pytest.mark.parametrize("text", [
    "password:", "password: \nnext line", "password is required", "secretary: Jane Doe", "the password policy",
    "password=", 'password=""', "secret: true", "password: null", "mypassword=abc",
])
def test_secret_key_value_negative(text):
    assert vals(text, T.SECRET) == []


def test_secret_pem_block():
    pem = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234567890\nabcdef98765432/+=\n"
           "-----END RSA PRIVATE KEY-----")
    t = f"here:\n{pem}\nafter"
    assert vals(t, T.SECRET) == [pem]
    pem2 = "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"
    assert vals(pem2, T.SECRET) == [pem2]
    pem3 = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----"
    assert vals(pem3, T.SECRET) == [pem3]
    assert vals("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----", T.SECRET) == []


def test_secret_pem_truncated_fails_closed():
    t = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\nkqhkiG9w0BAQEFAA"
    assert vals(t, T.SECRET) == [t]


def test_secret_pem_swallows_inner_digit_runs():
    pem = "-----BEGIN PRIVATE KEY-----\n9820012345\n-----END PRIVATE KEY-----"
    ds = detect_d2(pem)
    assert [d.type for d in ds] == [T.SECRET]


# ---------------------------------------------------------------- EMPLOYEE_ID
@pytest.mark.parametrize("v", ["EMP1234", "EMP-1234", "EMP_12345678", "emp-0042", "E123456", "WD-123456", "WD-12345678"])
def test_employee_id_formats(v):
    ds = detect_d2(f"staff {v} joined")
    assert [(d.type, d.score) for d in ds] == [(T.EMPLOYEE_ID, 0.9)]
    assert f"staff {v} joined"[ds[0].start:ds[0].end] == v


@pytest.mark.parametrize("v", ["EMP123", "EMP123456789", "E12345", "E1234567", "WD-12345", "WD-123456789", "XEMP1234", "EMP-1234X"])
def test_employee_id_format_negative(v):
    assert vals(f"see {v} here", T.EMPLOYEE_ID) == []


@pytest.mark.parametrize("text,val", [
    ("Employee ID: 4521", "4521"),
    ("employee id 88AB12", "88AB12"),
    ("Emp ID - X77Z", "X77Z"),
    ("Employee No. 10023456", "10023456"),
    ("Worker ID: W0001234", "W0001234"),
    ("staff id=SF99887", "SF99887"),
    ("emp# 5567", "5567"),
    ("Employee ID is 7788", "7788"),
    ("employee_id: JSMITH", "JSMITH"),
    ('"employee_id": "ab12cd"', "ab12cd"),
    ("worker id number A1B2C3D4", "A1B2C3D4"),
])
def test_employee_id_context(text, val):
    assert vals(text, T.EMPLOYEE_ID) == [val]


@pytest.mark.parametrize("text", [
    "Employee ID: 123",                    # too short
    "Employee ID: 12345678901",            # too long
    "employee id field is missing",        # no digits, lowercase word
    "Employee ID:" + " " * 30 + "A1234",   # beyond 25 chars
    "employees 4521",
])
def test_employee_id_context_negative(text):
    assert vals(text, T.EMPLOYEE_ID) == []


# ---------------------------------------------------------------- COMPENSATION
@pytest.mark.parametrize("amt", [
    "₹18,40,000", "₹ 18,40,000", "Rs. 18,40,000", "Rs 18,40,000", "INR 18,40,000", "₹1,20,00,000", "₹1,200,000",
    "₹38L", "₹38 L", "38 lakh", "38 lakhs", "38 Lakhs", "12.5 LPA", "12.5LPA", "38 lac", "₹2 crore", "2 Cr", "2 crores",
    "50k", "₹50K", "18,40,000 INR", "1840000 INR", "Rs.5000", "Rs. 5,000/-", "₹1,234.50", "3.5 lakh", "₹ 99",
    "rs 40000", "INR40000",
])
def test_compensation_marked(amt):
    t = f"They will get {amt} per year."
    ds = [d for d in detect_d2(t) if d.type == T.COMPENSATION]
    assert len(ds) == 1, (amt, ds)
    assert t[ds[0].start:ds[0].end] == amt and ds[0].score == 0.9


@pytest.mark.parametrize("text,val", [
    ("Salary: 1840000", "1840000"),
    ("CTC is 18,40,000", "18,40,000"),
    ('"annual_ctc": 2010000,', "2010000"),
    ("basic 45000", "45000"),
    ("net pay 52,300.75", "52,300.75"),
    ("bonus of 100000", "100000"),
    ("Stipend 12000/-", "12000"),
    ("hike 25,000", "25,000"),
    ("salary,50000,60000", "50000"),
    ("Compensation revision 2024 to 2200000", "2200000"),
])
def test_compensation_context_only(text, val):
    ds = [d for d in detect_d2(text) if d.type == T.COMPENSATION]
    assert ds and text[ds[-1].start:ds[-1].end] == val and ds[-1].score == 0.8


def test_compensation_marked_beats_context():
    t = "Salary: ₹18,40,000"
    ds = detect_d2(t)
    assert [(t[d.start:d.end], d.score) for d in ds] == [("₹18,40,000", 0.9)]


@pytest.mark.parametrize("text", [
    "We shipped 5000 units",
    "Ticket 1840000 closed",
    "The 12 L tank",                      # no currency marker
    "Invoice total 18,40,000",            # non-comp context, no marker
    "salary 500",                         # < 4 digits and no grouping
    "bonus in 2024",                      # year, not amount
    "salary revision effective 2024",
    "pay 12/03/2024",
    "hrs 5000",
    "10kg", "5 km", "x5k", "Rs5000abc", "payment 9999",
    "basic",
])
def test_compensation_negative(text):
    assert vals(text, T.COMPENSATION) == []


def test_compensation_marker_not_in_token():
    assert vals("ABC₹".replace("₹", "") + " 5", T.COMPENSATION) == []
    assert vals("PARIRS 5000", T.COMPENSATION) == []


# ---------------------------------------------------------------- DOB
@pytest.mark.parametrize("d", [
    "12/03/1984", "12-03-1984", "1984-03-12", "12 March 1984", "March 12, 1984", "12-Mar-1984", "12th March, 1984",
    "12.03.1984", "1/3/1984", "Mar 12 1984", "12 Sept 1984", "1984/03/12", "1984-03-12T00:00:00",
])
@pytest.mark.parametrize("kw", ["DOB:", "dob", "D.O.B.", "Date of Birth -", "born on", "born", "birth date:", "Birthdate"])
def test_dob_positive(kw, d):
    t = f"{kw} {d} ok"
    ds = [x for x in detect_d2(t) if x.type == T.DATE_OF_BIRTH]
    assert len(ds) == 1 and ds[0].score == 0.9, (t, ds)
    core = d.split("T")[0]
    assert t[ds[0].start:ds[0].end] == core


@pytest.mark.parametrize("text", [
    "Joined on 12/03/2015", "Meeting 12 March 1984", "Invoice dated 2024-03-12", "Date: 12-Mar-1984",
    "DOB: unknown", "DOB: 32/13/1984", "DOB: 12/03/84", "DOB: 12/03/19845",
    "DOB: 112/03/1984", "newborn 12/03/1984", "DOB" + " " * 40 + "12/03/1984", "DOB: 99/99/1984", "DOB: 12/03/1700",
    "dobby 12/03/1984",
])
def test_dob_negative(text):
    assert vals(text, T.DATE_OF_BIRTH) == []


def test_dob_only_first_date_after_keyword():
    t = "DOB 12/03/1984, joined 01/04/2015"
    assert vals(t, T.DATE_OF_BIRTH) == ["12/03/1984"]


def test_dob_multiple_keywords():
    t = "DOB: 12/03/1984. Spouse born 5 June 1986."
    assert vals(t, T.DATE_OF_BIRTH) == ["12/03/1984", "5 June 1986"]


# ---------------------------------------------------------------- look-alikes and fusion
def test_invoice_and_ids_produce_nothing():
    for t in ["INV-2024-000123", "Invoice INV-2024-000123 for 3 items", "PO-77821-A", "ORD 2024/03/000123",
              "Version 3.11.4 released", "ticket #482913 opened"]:
        assert detect_d2(t) == [], t


def test_twelve_digit_order_ids_not_aadhaar():
    rnd = random.Random(11)
    for _ in range(100):
        p = str(rnd.randint(2, 9)) + "".join(rnd.choice("0123456789") for _ in range(10))
        n = p + str((int(verhoeff_check_digit(p)) + 1) % 10)
        assert all(d.type != T.AADHAAR for d in detect_d2(f"Order id: {n}")), n


def test_overlap_higher_score_wins():
    # UPI (0.95) over phone (0.9) on the same digits
    t = "pay 9820012345@ybl"
    assert [(x.type, t[x.start:x.end]) for x in detect_d2(t)] == [(T.UPI_ID, "9820012345@ybl")]


def test_overlap_tie_longer_wins():
    n = aadhaar_num()
    t = f"a/c no {group4(n)}"
    assert [d.type for d in detect_d2(t)] == [T.AADHAAR]


def test_secret_over_email_in_url():
    t = "password=user@corp.com"
    ds = detect_d2(t)
    assert len(ds) == 1 and ds[0].score == 0.99


def test_mixed_hr_paragraph():
    t = ("Employee ID: EMP-004521, Rahul Sharma <rahul.sharma@esu.com>, mobile +91 98200 12345, "
         "PAN ABCPE1234F, DOB 12/03/1984, CTC ₹38L, a/c no 50100234567890 IFSC HDFC0001234, UPI rahul@okaxis.")
    got = {(d.type, t[d.start:d.end]) for d in detect_d2(t)}
    assert got == {
        (T.EMPLOYEE_ID, "EMP-004521"), (T.EMAIL, "rahul.sharma@esu.com"), (T.PHONE, "+91 98200 12345"),
        (T.PAN, "ABCPE1234F"), (T.DATE_OF_BIRTH, "12/03/1984"), (T.COMPENSATION, "₹38L"),
        (T.BANK_ACCOUNT, "50100234567890"), (T.IFSC, "HDFC0001234"), (T.UPI_ID, "rahul@okaxis"),
    }


def test_pure_and_deterministic():
    t = "Aadhaar " + group4(aadhaar_num()) + " x@ybl.com 98200 12345"
    assert detect_d2(t) == detect_d2(t)


def test_no_catastrophic_backtracking():
    import time
    t0 = time.time()
    detect_d2("1 " * 4000 + "a")
    detect_d2("9" * 8000 + "a")
    detect_d2("a." * 4000 + "@")
    detect_d2("salary " + "1," * 3000)
    assert time.time() - t0 < 5


# ---------------------------------------------------------------- D1
def test_d1_basic_table():
    obj = {
        "name": "Rahul", "full_name": "R S", "legal_name": "x", "first_name": "a", "last_name": "b",
        "preferred_name": "c", "worker_name": "d", "employee_name": "e", "manager_name": "f",
        "email": "a@b.com", "work_email": "a@b.com", "personal_email": "a@b.com",
        "phone": "1", "mobile": "2", "phone_number": "3",
        "employee_id": "E1", "worker_id": "W1", "emp_id": "1", "employee_number": 7,
        "salary": 100, "base_pay": 1, "ctc": "2", "annual_salary": 3, "compensation": 4, "gross_pay": 5,
        "net_pay": 6, "bonus": 7,
        "dob": "1984-01-01", "date_of_birth": "x", "birth_date": "y",
        "aadhaar": "1", "aadhaar_number": "2", "pan": "P", "pan_number": "Q",
        "bank_account": "1", "account_number": "2", "ifsc": "I", "ifsc_code": "J",
    }
    got = dict(detect_fields(obj))
    exp = {
        T.PERSON: ["name", "full_name", "legal_name", "first_name", "last_name", "preferred_name", "worker_name",
                   "employee_name", "manager_name"],
        T.EMAIL: ["email", "work_email", "personal_email"],
        T.PHONE: ["phone", "mobile", "phone_number"],
        T.EMPLOYEE_ID: ["employee_id", "worker_id", "emp_id", "employee_number"],
        T.COMPENSATION: ["salary", "base_pay", "ctc", "annual_salary", "compensation", "gross_pay", "net_pay", "bonus"],
        T.DATE_OF_BIRTH: ["dob", "date_of_birth", "birth_date"],
        T.AADHAAR: ["aadhaar", "aadhaar_number"], T.PAN: ["pan", "pan_number"],
        T.BANK_ACCOUNT: ["bank_account", "account_number"], T.IFSC: ["ifsc", "ifsc_code"],
    }
    assert len(got) == len(obj)
    for typ, keys in exp.items():
        for k in keys:
            assert got[(k,)] == typ, k


@pytest.mark.parametrize("key", ["workEmail", "Work-Email", "WORK_EMAIL", "work email", "WorkEmail", " work_email "])
def test_d1_key_normalisation(key):
    assert detect_fields({key: "a@b.com"}) == [((key,), T.EMAIL)]


@pytest.mark.parametrize("key,typ", [
    ("employeeID", T.EMPLOYEE_ID), ("EmployeeId", T.EMPLOYEE_ID), ("DOB", T.DATE_OF_BIRTH), ("dateOfBirth", T.DATE_OF_BIRTH),
    ("PANNumber", T.PAN), ("IFSCCode", T.IFSC), ("annualCTC", T.COMPENSATION), ("Annual-Salary", T.COMPENSATION),
    ("firstName", T.PERSON), ("mobileNumber", T.PHONE), ("AadhaarNumber", T.AADHAAR),
])
def test_d1_camel_and_acronyms(key, typ):
    assert detect_fields({key: "v"}) == [((key,), typ)]


def test_d1_nested_paths_and_lists():
    obj = {"workers": [{"name": "A", "contact": {"phone": "98200 12345"}}, {"name": "B"}], "meta": {"n": 1}}
    assert detect_fields(obj) == [
        (("workers", 0, "name"), T.PERSON),
        (("workers", 0, "contact", "phone"), T.PHONE),
        (("workers", 1, "name"), T.PERSON),
    ]


def test_d1_scalars_in_list_inherit_key():
    assert detect_fields({"phone": ["1", "2"], "notes": ["x"]}) == [(("phone", 0), T.PHONE), (("phone", 1), T.PHONE)]


def test_d1_top_level_list():
    assert detect_fields([{"email": "a@b.com"}]) == [((0, "email"), T.EMAIL)]


def test_d1_numbers_ok_bool_none_empty_skipped():
    obj = {"salary": 1840000, "bonus": 1.5, "phone": 9820012345, "ctc": True, "email": None, "dob": "", "pan": "  "}
    got = dict(detect_fields(obj))
    assert set(got) == {("salary",), ("bonus",), ("phone",)}


def test_d1_container_values_are_not_flagged_as_leaves():
    assert detect_fields({"salary": {"currency": "INR"}}) == []
    assert detect_fields({"email": ["x@y.com"], "name": {"first": "a"}}) == [(("email", 0), T.EMAIL)]


@pytest.mark.parametrize("parent", ["company", "organization", "org", "department", "cost_center", "location", "job_profile",
                                    "Company", "costCenter", "jobProfile"])
def test_d1_org_parent_skips_name(parent):
    assert detect_fields({parent: {"name": "Engineering"}}) == []
    assert detect_fields({parent: [{"name": "Engineering"}]}) == []


def test_d1_name_elsewhere_still_person_and_siblings_kept():
    obj = {"department": {"name": "Eng", "manager_name": "M"}, "employee": {"name": "E"}}
    assert detect_fields(obj) == [
        (("department", "manager_name"), T.PERSON),
        (("employee", "name"), T.PERSON),
    ]


def test_d1_non_matching_keys():
    assert detect_fields({"nickname": "x", "username": "y", "filename": "z", "panel": "p", "company_name": "ACME",
                          "department_name": "Eng", "description": "d"}) == []


def test_d1_scalar_root_and_empty():
    assert detect_fields("hello") == []
    assert detect_fields({}) == []
    assert detect_fields([]) == []
    assert detect_fields(None) == []
