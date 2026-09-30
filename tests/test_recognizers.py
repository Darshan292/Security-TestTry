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


def test_aadhaar_context_window_is_120_chars():
    n = aadhaar_num()
    assert detect_d2("aadhaar" + " " * 100 + n)  # 100 chars away
    assert not detect_d2("aadhaar" + " " * 130 + n)


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
    "abcpe1234f",       # lowercase without a PAN label
    "ABCDE1234F",       # 4th char D invalid
    "ABCPE12345",       # last must be letter
    "ABCPE123F",        # too short
    "XABCPE1234F",      # glued
    "ABCPE1234FX",
    "ABCPE1234F9",
])
def test_pan_negative(s):
    assert vals(f"code {s} ok", T.PAN) == []


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
    assert len(ds) == 1 and t[ds[0].start:ds[0].end] == "50100234567890" and ds[0].score in (0.85, 0.95)


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
    assert [d.type for d in detect_d2(f"account no {group4(card)}")] == [T.CARD]   # grouped 4-4-4-4 stays a card
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
    assert ds and text[ds[-1].start:ds[-1].end] == val and ds[-1].score in (0.8, 0.95)


def test_compensation_marked_beats_context():
    t = "Salary: ₹18,40,000"
    ds = detect_d2(t)
    assert [(t[d.start:d.end], d.type) for d in ds] == [("₹18,40,000", T.COMPENSATION)]


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
    assert len(ds) == 1 and ds[0].score in (0.9, 0.95), (t, ds)
    core = d.split("T")[0]
    assert t[ds[0].start:ds[0].end] == core


@pytest.mark.parametrize("text", [
    "Joined on 12/03/2015", "Meeting 12 March 1984", "Invoice dated 2024-03-12", "Date: 12-Mar-1984",
    "DOB: unknown", "DOB: 32/13/1984", "DOB: 12/03/84", "DOB: 12/03/19845",
    "DOB: 112/03/1984", "newborn 12/03/1984", "DOB" + " " * 70 + "12/03/1984", "DOB: 99/99/1984", "DOB: 12/03/1700",
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


# =====================================================================================
# Round 2: field-context detector (D1 in free text), stricter/wider D2 rules
# =====================================================================================
from pgw.detect.recognizers import detect_field_context  # noqa: E402
from pgw.normalize import normalize  # noqa: E402


def flat(text):
    """Single-line CSV (e.g. pasted into chat): rows glued by spaces. D0 itself keeps \\n and \\t."""
    return " ".join(normalize(text)[0].split())


def fc(text):
    return [(text[d.start:d.end], d.type) for d in detect_field_context(text)]


def fcv(text, typ):
    return [v for v, t in fc(text) if t == typ]


AAD = aadhaar_num("84068171012")           # valid Verhoeff, first digit 8
AAD_BAD = bad_aadhaar_num("84068171012")
VISA = CARDS["visa"]


def test_field_context_metadata():
    t = '{"email": "a.b@corp.com"}'
    ds = detect_field_context(t)
    assert [(d.type, d.score, d.source) for d in ds] == [(T.EMAIL, 0.95, "D1:field_ctx")]
    assert detect_field_context("") == []
    # also picked up by detect_d2 (a longer/higher-score D2 hit may win the overlap)
    assert any(d.source == "D1:field_ctx" for d in detect_d2('{"employee_id": "ZK-4471"}'))


# ---------------------------------------------------------------- JSON-ish
@pytest.mark.parametrize("text,typ,val", [
    ('{"primaryWorkEmail": "nayana@corp.in"}', T.EMAIL, "nayana@corp.in"),
    ('{"mobilePhone": "+91-9608787041"}', T.PHONE, "+91-9608787041"),
    ('{"mobilePhone": 9608787041}', T.PHONE, "9608787041"),
    ('{"Birth_Date": "1977-09-23"}', T.DATE_OF_BIRTH, "1977-09-23"),
    ('{"dateOfBirth": "23 Sep 1977"}', T.DATE_OF_BIRTH, "23 Sep 1977"),
    ('{"bankAccount": "61407831657519755"}', T.BANK_ACCOUNT, "61407831657519755"),
    ('{"beneficiaryAccount": "1619407436"}', T.BANK_ACCOUNT, "1619407436"),
    ('{"from_account": "50100234567890", "to_account": "4011223344556"}', T.BANK_ACCOUNT, "50100234567890"),
    ('{"account": 2811478136133}', T.BANK_ACCOUNT, "2811478136133"),
    ('{"Emp No": "EMP-1234"}', T.EMPLOYEE_ID, "EMP-1234"),
    ('{"empCode": "XJ8821"}', T.EMPLOYEE_ID, "XJ8821"),
    ('{"Legal Name": "Nayana Gaikwad"}', T.PERSON, "Nayana Gaikwad"),
    ('{"legalName": "D\'Souza, R. K."}', T.PERSON, "D'Souza, R. K."),
    ('{"PAN No": "ABCPE1234F"}', T.PAN, "ABCPE1234F"),
    ('{"Net Pay": "52,300.75"}', T.COMPENSATION, "52,300.75"),
    ('{"annualBasePay": 7290000}', T.COMPENSATION, "7290000"),
    ('{"ctc": "₹38L"}', T.COMPENSATION, "₹38L"),
    ('{"awsSecretAccessKey": "I8rZ60RlrxltfvjHWqRqKLVJD5u2kr0/iYcIL"}', T.SECRET, "I8rZ60RlrxltfvjHWqRqKLVJD5u2kr0/iYcIL"),
    ('{"adminPassword": "S3cr3tPw!"}', T.SECRET, "S3cr3tPw!"),
    ('{"token": "OULkJETD3gZbOGIgBKRg"}', T.SECRET, "OULkJETD3gZbOGIgBKRg"),
    ('{"client-secret": "abcd1234"}', T.SECRET, "abcd1234"),
    ('{"upi": "amit99@oksbi"}', T.UPI_ID, "amit99@oksbi"),
])
def test_fc_json_positive(text, typ, val):
    assert val in fcv(text, typ), fc(text)


def test_fc_json_all_key_styles():
    assert fcv("{'email': 'a@b.com'}", T.EMAIL) == ["a@b.com"]                        # python repr
    assert fcv('{\\"phone\\":\\"98200 12345\\",\\"bankAccount\\":\\"3276123784556688\\"}', T.PHONE) == ["98200 12345"]
    assert fcv('payload="{\\"bankAccount\\":\\"3276123784556688\\"}" cause=x', T.BANK_ACCOUNT) == ["3276123784556688"]
    assert fcv('2026-05-24 ERROR log {"dob":"1985-04-15"} tail', T.DATE_OF_BIRTH) == ["1985-04-15"]
    assert fcv('{"message":"token rejected","token":"OULkJETD3gZbOGIgBKRgnZ5gDQ64r2V7"}', T.SECRET) == \
        ["OULkJETD3gZbOGIgBKRgnZ5gDQ64r2V7"]


@pytest.mark.parametrize("text", [
    f'{{"aadhaar": "{AAD_BAD}"}}',                       # bad Verhoeff
    '{"aadhaar": "84068171012"}',                        # 11 digits
    '{"bankAccount": "12345678"}',                       # 8 digits
    '{"bankAccount": "1234567890123456789"}',            # 19 digits
    '{"bankAccount": "not provided"}',
    '{"phone": "12345"}', '{"phone": "n/a"}',
    '{"email": "not-an-email"}',
    '{"dob": "someday"}', '{"dob": "32/13/1984"}', '{"dob": "1984"}',
    '{"salary": "confidential"}', '{"salary": 5}', '{"bonus": true}', '{"ctc": null}',
    '{"name": "Agent007"}', '{"name": "a b c d e f g"}', '{"name": ""}',
    '{"pan": "12345"}', '{"ifsc": "HDFC1001234"}', '{"employeeId": "12"}', '{"employeeId": "no value here"}',
    '{"password": "***"}', '{"password": "<redacted>"}', '{"password": "{{DB_PASSWORD}}"}', '{"password": "${DB_PASS}"}',
    '{"password": "required"}', '{"password": "changeme"}', '{"password": null}', '{"password": "abc"}',
    '{"token_type": "Bearer"}', '{"tokenExpiry": "3600"}', '{"password_policy": "min 8 chars"}',
    '{"cardNumber": "4111111111111112"}',               # bad Luhn
])
def test_fc_json_negative(text):
    assert fc(text) == [], fc(text)


def test_fc_amount_needs_pay_context():
    assert fcv('{"compensation": {"annualBasePay": {"amount": 7290000, "currency": "INR"}}}', T.COMPENSATION) == ["7290000"]
    assert fcv('{"payroll": {"amount": "1,48,500"}}', T.COMPENSATION) == ["1,48,500"]
    assert fc('{"invoice": {"amount": 7290000, "currency": "INR"}}') == []
    assert fc('{"amount": 7290000}') == []
    assert fc('{"order": "A1", "item": "salary slip template", "amount": 12500}') == []   # order context wins
    assert fc("amt=Rs 2,00,500 row rejected") == []


def test_fc_bare_name_skips_org_objects():
    assert fcv('{"person": {"name": "Rahul Sharma"}}', T.PERSON) == ["Rahul Sharma"]
    for parent in ("department", "company", "organization", "org", "cost_center", "location", "job_profile"):
        assert fc('{"%s": {"name": "Engineering"}}' % parent) == []
    assert fc('{"department": [{"name": "Engineering"}]}') == []
    assert fcv('{"department": {"name": "Eng", "manager_name": "Meera Iyer"}}', T.PERSON) == ["Meera Iyer"]
    assert fc('{"name": "engineering"}') == []          # bare name must be capitalised


# ---------------------------------------------------------------- sibling hints
@pytest.mark.parametrize("text,typ,val", [
    (f'{{"type": "AADHAAR", "id": "{AAD}"}}', T.AADHAAR, AAD),
    (f'{{"idType": "aadhaar", "value": "{AAD}"}}', T.AADHAAR, AAD),
    (f'{{"id": "{AAD}", "type": "India Aadhaar"}}', T.AADHAAR, AAD),
    ('{"type": "India PAN", "id": "WVSPG4547S"}', T.PAN, "WVSPG4547S"),
    ('{"type": "email", "value": "a@b.co.in"}', T.EMAIL, "a@b.co.in"),
    ('{"field": "Birth_Date", "new_value": "1968-02-15"}', T.DATE_OF_BIRTH, "1968-02-15"),
    ('{"field": "Salary", "old_value": "18,40,000", "new_value": "20,10,000"}', T.COMPENSATION, "20,10,000"),
    ('{"name": "DB_PASSWORD", "value": "s3cr3t-Pw"}', T.SECRET, "s3cr3t-Pw"),
    ('{"from": "1336420150675096", "to": "679609455057"}', T.BANK_ACCOUNT, "679609455057"),
    ('{"from": "Rs. 52,90,000", "to": "₹62 lakhs"}', T.COMPENSATION, "₹62 lakhs"),
    ('{"from": "3.5L", "to": "₹3,70,000"}', T.COMPENSATION, "3.5L"),
])
def test_fc_sibling_hints(text, typ, val):
    assert val in fcv(text, typ), fc(text)


@pytest.mark.parametrize("text", [
    f'{{"type": "AADHAAR", "id": "{AAD_BAD}"}}',           # hint but bad checksum
    '{"type": "widget", "id": "840681710124"}',            # unknown hint
    '{"from": "Mumbai", "to": "Pune"}',
    '{"from": "12345", "to": "67890"}',                    # too short for an account
    '{"from": "1336420150675096"}',                        # lone `from`: not a diff pair
    '{"kind": "AADHAAR"} {"id": "%s"}' % AAD,              # different objects
])
def test_fc_sibling_hint_negative(text):
    assert not [1 for v, t in fc(text) if t in (T.AADHAAR, T.BANK_ACCOUNT, T.COMPENSATION)], fc(text)


# ---------------------------------------------------------------- XML
@pytest.mark.parametrize("text,typ,val", [
    ("<wd:Birth_Date>1989-07-23</wd:Birth_Date>", T.DATE_OF_BIRTH, "1989-07-23"),
    ("<BirthDate>1989-07-23</BirthDate>", T.DATE_OF_BIRTH, "1989-07-23"),
    ("<ns:Email_Address>a@corp.in</ns:Email_Address>", T.EMAIL, "a@corp.in"),
    ("<wd:Phone_Number>022-6842 3149</wd:Phone_Number>", T.PHONE, "022-6842 3149"),
    ("<Contact_Phone>+91 64769 85841</Contact_Phone>", T.PHONE, "+91 64769 85841"),
    ("<wd:Worker_ID>EMP-18864</wd:Worker_ID>", T.EMPLOYEE_ID, "EMP-18864"),
    ("<wd:Total_Base_Pay wd:Currency=\"INR\" wd:Frequency=\"Annual\">2880000</wd:Total_Base_Pay>", T.COMPENSATION, "2880000"),
    ("<wd:First_Name>Yuvraj</wd:First_Name>", T.PERSON, "Yuvraj"),
    ("<wd:Manager>Chitra Kamath</wd:Manager>", T.PERSON, "Chitra Kamath"),
    ("<wsse:Password Type=\"http://x/oasis#PasswordText\">Hrms42#hrm</wsse:Password>", T.SECRET, "Hrms42#hrm"),
    (f"<wd:National_ID wd:Type=\"IN-AADHAAR\">{AAD}</wd:National_ID>", T.AADHAAR, AAD),
    ("<wd:National_ID wd:Type=\"IN-PAN\">RGBPB0055A</wd:National_ID>", T.PAN, "RGBPB0055A"),
    ("<BankAccount>50100234567890</BankAccount>", T.BANK_ACCOUNT, "50100234567890"),
])
def test_fc_xml_positive(text, typ, val):
    assert val in fcv(text, typ), fc(text)


@pytest.mark.parametrize("text", [
    "<wd:Birth_Date>not known</wd:Birth_Date>", "<wd:Email_Address>none</wd:Email_Address>",
    "<wd:Department><wd:Name>Engineering</wd:Name></wd:Department>",
    "<wd:Department>\n  <wd:Name>Engineering</wd:Name>\n</wd:Department>",
    "<wsse:Password Type=\"x\">***</wsse:Password>", "<Password>${SECRET}</Password>",
    "<wd:Phone_Number>12345</wd:Phone_Number>", "<wd:National_ID wd:Type=\"IN-PASSPORT\">K1234567</wd:National_ID>",
    "<wd:Salary_Grade>G8</wd:Salary_Grade>", f"<wd:National_ID wd:Type=\"IN-AADHAAR\">{AAD_BAD}</wd:National_ID>",
])
def test_fc_xml_negative(text):
    assert fc(text) == [], fc(text)


# ---------------------------------------------------------------- key: value / env / ini
@pytest.mark.parametrize("text,typ,val", [
    ("Email: hormuz.cama@yahoo.co.in | Mobile: +917520803226", T.EMAIL, "hormuz.cama@yahoo.co.in"),
    ("Email: hormuz.cama@yahoo.co.in | Mobile: +917520803226", T.PHONE, "+917520803226"),
    ("Employee ID: E420002\nSubject: x", T.EMPLOYEE_ID, "E420002"),
    ("emp_no = WD-1404680", T.EMPLOYEE_ID, "WD-1404680"),
    ("Date of Birth: 1981-07-10\nPAN: FSWPA7759V", T.DATE_OF_BIRTH, "1981-07-10"),
    ("Date of Birth: 1981-07-10\nPAN: FSWPA7759V", T.PAN, "FSWPA7759V"),
    ("Aadhaar: 7382 3266 3762".replace("7382 3266 3762", group4(aadhaar_num("73823266376"))), T.AADHAAR, group4(aadhaar_num("73823266376"))),
    ("A/c No: 3661451554394\nIFSC: IDIB00W671D", T.BANK_ACCOUNT, "3661451554394"),
    ("Account holder: Pallavi Mehta\nA/c No: 3661451554394", T.PERSON, "Pallavi Mehta"),
    ("acct=431361808069897 ifsc=YESB055R12N", T.BANK_ACCOUNT, "431361808069897"),
    ("name=Pratap Sanyal acct=266825836484 ifsc=IDIB0246660", T.PERSON, "Pratap Sanyal"),
    ("Contact: 080-6864 6172", T.PHONE, "080-6864 6172"),
    ("Current CTC: ₹59,70,000 | Expected: 69 LPA", T.COMPENSATION, "₹59,70,000"),
    ("Net Pay = 52,300.75", T.COMPENSATION, "52,300.75"),
    ("+  awsSecretAccessKey: HqxJuhtwxsi0kNEKevx4ZANLsw00UYxOubste0Uv", T.SECRET, "HqxJuhtwxsi0kNEKevx4ZANLsw00UYxOubste0Uv"),
    ("+  adminPassword: 09TSoayHo4T", T.SECRET, "09TSoayHo4T"),
    ("DB_PASSWORD=login_4569#", T.SECRET, "login_4569#"),
    ("https://api.example.in/v1/lookup?phone=9820012345&x=1", T.PHONE, "9820012345"),
    ("Row 121: Date_of_Birth 11/12/1975 is not a valid date", T.DATE_OF_BIRTH, "11/12/1975"),
    ("Row 350: Invalid Bank_Account_Number '4735410777404554' for routing", T.BANK_ACCOUNT, "4735410777404554"),
    ("Row 125: Base_Pay 67.9 LPA exceeds the maximum", T.COMPENSATION, "67.9 LPA"),
])
def test_fc_kv_positive(text, typ, val):
    assert val in fcv(text, typ), fc(text)


def test_fc_kv_value_stops_at_next_key():
    t = "name=Pratap Sanyal acct=266825836484 ifsc=IDIB0246660 amt=Rs 5"
    assert fcv(t, T.PERSON) == ["Pratap Sanyal"]
    assert fcv(flat("Candidate: Hormuz Cama\nEmail: h@x.co"), T.PERSON) == ["Hormuz Cama"]
    assert fcv("Manager: Rahul Sharma is on leave", T.PERSON) == ["Rahul Sharma"]


@pytest.mark.parametrize("text", [
    "Passcode: (see calendar)", "password: required", "Password: ***", "secret: true", "Meeting ID: 094 2430 7429",
    "Reason for leaving: better compensation", "Status: Active", "Department: Engineering", "Name: engineering-svc",
    "Employee ID: 123", "Phone: 12345", "Email: not provided", "Salary: to be discussed", "Bonus: 5",
    "Account type: 50100234567890", "Bank Name: HDFC Bank", "File Name: hr_export.csv",
    "Date of Joining: 12/03/2015", "Birth place: Pune", "https://host/path", "time: 12:24",
])
def test_fc_kv_negative(text):
    assert fc(text) == [], fc(text)


# ---------------------------------------------------------------- CSV / TSV (real line breaks)
def test_fc_csv_columns_quoted_cells():
    t = ("Legal Name,Bank IFSC,Bank A/c No,PAN No,Amount,Narration\n"
         'Aamir Ghani,BKID08S84H6,723772573973598,NNUPG3897N,"50,000",SALARY-SEP26\n'
         'Dorjee Neog,UBIN08J3214,469489825369088,PCGPN6322Y,"3,83,300",SALARY-SEP26\n'
         "Sandeep Chaturvedi,IDIB0Z0U2YP,4650271428160,NMLPC4486O,199700.00,SALARY-SEP26\n")
    got = fc(t)
    assert ("Aamir Ghani", T.PERSON) in got and ("Sandeep Chaturvedi", T.PERSON) in got
    assert ("723772573973598", T.BANK_ACCOUNT) in got and ("4650271428160", T.BANK_ACCOUNT) in got
    assert ("50,000", T.COMPENSATION) in got and ("3,83,300", T.COMPENSATION) in got     # quotes excluded from the span
    assert ("199700.00", T.COMPENSATION) in got and ("NNUPG3897N", T.PAN) in got
    assert not [v for v, ty in got if v in ("Narration", "SALARY-SEP26")]


def test_fc_csv_quoted_name_with_comma_and_dates():
    t = ('E-mail,Employee Name,Worker ID,Status,Date of Joining,Phone,D.O.B\n'
         'e.v@kestrelsys.in,"Vadakkan, Elijah",EMP-06050,Notice Period,2024-09-30,(+91) 97618-11018,"November 11, 1999"\n'
         "sundar@gmail.com,Sundar,E890741,Active,2025-12-30,91 7120233029,\"June 02, 1971\"\n")
    got = fc(t)
    assert ("Vadakkan, Elijah", T.PERSON) in got and ("Sundar", T.PERSON) in got
    assert ("November 11, 1999", T.DATE_OF_BIRTH) in got and ("June 02, 1971", T.DATE_OF_BIRTH) in got
    assert ("2024-09-30", T.DATE_OF_BIRTH) not in got and ("2025-12-30", T.DATE_OF_BIRTH) not in got   # joining dates
    assert ("(+91) 97618-11018", T.PHONE) in got and ("EMP-06050", T.EMPLOYEE_ID) in got


def test_fc_tsv_columns():
    t = "Emp No\tFull Name\tDate of Birth\tAnnual CTC\nEMP-1001\tMeera Iyer\t14/03/1988\t18,40,000\nEMP-1002\tRohit Das\t02/11/1991\t1200000\n"
    got = fc(t)
    for v, ty in [("EMP-1001", T.EMPLOYEE_ID), ("Meera Iyer", T.PERSON), ("14/03/1988", T.DATE_OF_BIRTH),
                  ("18,40,000", T.COMPENSATION), ("Rohit Das", T.PERSON), ("02/11/1991", T.DATE_OF_BIRTH),
                  ("1200000", T.COMPENSATION)]:
        assert (v, ty) in got, (v, got)


def test_fc_csv_negative_no_header_or_untyped_columns():
    assert fc("Status,Location,Grade\nActive,Pune,G7\nActive,Mumbai,G8\n") == []
    assert fc("Hello team, please review the salary revision, thanks\n") == []
    assert fc("1,2,3\n4,5,6\n7,8,9\n") == []
    # a value that does not validate for its column is left alone
    t = "Name,Email,Bank Account\nRahul Sharma,not-an-email,12345\n"
    assert fc(t) == [("Rahul Sharma", T.PERSON)]


def test_fc_csv_amount_column_needs_pay_context():
    assert fcv("Name,Amount\nRahul Sharma,50000\nMeera Iyer,60000\n", T.COMPENSATION) == []
    t = "Name,Amount,Narration\nRahul Sharma,50000,SALARY-SEP26\nMeera Iyer,60000,SALARY-SEP26\n"
    assert fcv(t, T.COMPENSATION) == ["50000", "60000"]
    inv = "Item,Amount,Narration\nCourier,50000,invoice INV-1\nHosting,60000,invoice INV-2\n"
    assert fcv(inv, T.COMPENSATION) == []


# ---------------------------------------------------------------- CSV, flattened by D0 (rows separated by spaces)
FLAT_ADVICE = flat(
    "Legal Name,Bank IFSC,Bank A/c No,PAN No,Amount,Narration\n"
    'Aamir Ghani,BKID08S84H6,723772573973598,NNUPG3897N,"50,000",SALARY-SEP26\n'
    'Dorjee Neog,UBIN08J3214,469489825369088,PCGPN6322Y,"3,83,300",SALARY-SEP26\n'
    "Sandeep Chaturvedi,IDIB0Z0U2YP,4650271428160,NMLPC4486O,199700.00,SALARY-SEP26\n"
    "Divya Lal,ICIC0987517,1066740337987,QWFPL1614V,251700.00,SALARY-SEP26\n")


def test_fc_flat_csv_bank_advice():
    assert "\n" not in FLAT_ADVICE
    got = fc(FLAT_ADVICE)
    for v, ty in [("Aamir Ghani", T.PERSON), ("Dorjee Neog", T.PERSON), ("Sandeep Chaturvedi", T.PERSON), ("Divya Lal", T.PERSON),
                  ("723772573973598", T.BANK_ACCOUNT), ("1066740337987", T.BANK_ACCOUNT), ("50,000", T.COMPENSATION),
                  ("3,83,300", T.COMPENSATION), ("199700.00", T.COMPENSATION), ("251700.00", T.COMPENSATION)]:
        assert (v, ty) in got, (v, got)
    assert not [v for v, _ in got if "SALARY" in v or v.startswith("Narration")]     # no over-capture of the glue words


def test_fc_flat_csv_shuffled_columns_and_dates():
    t = flat("Status,Location,Date of Birth,Manager,Mobile,Full Name,EmpCode,Department\n"
             'Active,Pune,"August 03, 1980",Firuza Tarapore,+91 931 713 8256,Pankaj Khanna,E392879,Sales\n'
             "On Leave,Mumbai,14-06-1995,Mehnaz Mirza,011-9450 7307,Daljeet Sidhu,EMP-00776,Sales\n"
             "Active,Kolkata,02/02/1986,Nisha Negi,98200 12345,Irfan Khan,WD-7701234,Legal\n")
    got = fc(t)
    for v, ty in [("August 03, 1980", T.DATE_OF_BIRTH), ("14-06-1995", T.DATE_OF_BIRTH), ("02/02/1986", T.DATE_OF_BIRTH),
                  ("Firuza Tarapore", T.PERSON), ("Pankaj Khanna", T.PERSON), ("Irfan Khan", T.PERSON),
                  ("011-9450 7307", T.PHONE), ("E392879", T.EMPLOYEE_ID), ("WD-7701234", T.EMPLOYEE_ID)]:
        assert (v, ty) in got, (v, got)
    assert not [v for v, ty in got if v in ("Sales", "Legal", "Pune", "Active", "Kolkata", "Mumbai") or "Sales " in v]


def test_fc_flat_csv_name_first_and_comp_last():
    t = flat("Full Name,Department,Date of Joining,Emp ID,Reporting Manager,CTC\n"
             "YASH AWASTHI,Legal,11 Mar 2026,EMP-76312,Sudip Halder,6100000\n"
             "KRISHNAN KANNAN,Finance,07-12-2023,EMP-50664,Swagata Ghosh,6970000\n"
             "QASIM SAYYED,Engineering,2026-07-27,EMP-01303,Smruti Patil,1900000\n")
    got = fc(t)
    for v, ty in [("YASH AWASTHI", T.PERSON), ("Sudip Halder", T.PERSON), ("EMP-76312", T.EMPLOYEE_ID), ("6100000", T.COMPENSATION),
                  ("KRISHNAN KANNAN", T.PERSON), ("6970000", T.COMPENSATION), ("QASIM SAYYED", T.PERSON), ("1900000", T.COMPENSATION)]:
        assert (v, ty) in got, (v, got)
    assert ("Legal", T.PERSON) not in got and ("Engineering", T.PERSON) not in got


def test_fc_flat_csv_glued_names_are_split():
    t = flat("Reporting Manager,DOB,CTC,Emp No,Email Address,Name\n"
             'Sreenivas Thevar,09 January 1999,"56,30,000",EMP-18389,darab@kestrelsys.in,Darab Batliwala\r\n'
             'Gulnaz Zaidi,15 June 1983,"68,60,000",WD-3892750,jani@gmail.com,Vatsal Jani\r\n'
             'Noor Abbasi,05 June 1973,"73,10,000",E747117,nag@hotmail.com,Nagaraja Unnithan\r\n')
    got = fc(t)
    for name in ("Sreenivas Thevar", "Gulnaz Zaidi", "Noor Abbasi", "Vatsal Jani"):
        assert (name, T.PERSON) in got, (name, got)
    assert ("09 January 1999", T.DATE_OF_BIRTH) in got and ("56,30,000", T.COMPENSATION) in got


def test_fc_flat_csv_prose_before_header_and_after_rows():
    t = flat("Please see the extract below.\nEmp ID,PAN Number,Status,Name,Email\n"
             "EMP-00539,YGKPC1957W,On Leave,Chandra Dinesh,d@k.in\nE692437,YZXPS1956U,Active,Sharma Gunjan,g@y.in\n"
             "EMP-03726,NYXPP4196P,Active,Puthen Joshua,j@k.in\nThanks, HR\n")
    got = fc(t)
    assert ("EMP-00539", T.EMPLOYEE_ID) in got and ("YZXPS1956U", T.PAN) in got and ("Sharma Gunjan", T.PERSON) in got
    assert not [v for v, _ in got if "Thanks" in v or "HR" == v]


def test_fc_flat_not_a_table():
    assert fc(flat("Hi team, please pay the vendor, thanks, Ravi")) == []
    assert fc(flat("Status,Location,Grade\nActive,Pune,G7\nActive,Mumbai,G8\nActive,Delhi,G9\n")) == []


# ---------------------------------------------------------------- normalise-independence
def test_fc_same_hits_with_and_without_line_breaks():
    raw = ("Emp ID,Full Name,Date of Birth,Annual CTC\n"
           "EMP-1001,Meera Iyer,14/03/1988,\"18,40,000\"\n"
           "EMP-1002,Rohit Das,02/11/1991,\"12,00,000\"\n"
           "EMP-1003,Anita Rao,05/05/1990,\"9,50,000\"\n")
    a, b = set(fc(raw)), set(fc(flat(raw)))
    assert a == b and len(a) == 12


# =====================================================================================
# Round 2 D2 rules
# =====================================================================================
# ---- item 2: Aadhaar keyword window 120
def test_aadhaar_keyword_within_120_chars():
    n = AAD
    t = '"type": "India Aadhaar",\n                        "id": "' + n + '"'
    assert vals(t, T.AADHAAR) == [n]
    assert vals("uid " + "x" * 100 + " " + n, T.AADHAAR) == [n]
    assert vals("uid " + "x" * 150 + " " + n, T.AADHAAR) == []
    assert vals(f"order id {AAD_BAD}", T.AADHAAR) == [] and vals(f"aadhaar {AAD_BAD}", T.AADHAAR) == []


# ---- item 3: phones
@pytest.mark.parametrize("p", [
    "011-1241 0700", "022-6842 3149", "080-2664 6184", "0120-456 7890", "04712-40 9319", "(022) 2670 8915", "(080)2664 6184",
    "0484-2670915".replace("2670915", "267 0915"), "+91 909 353 5480", "+91-909-353-5480", "909 353 5480",
])
def test_phone_landlines_and_3_3_4(p):
    t = f"call {p} today"
    assert vals(t, T.PHONE) == [p], detect_d2(t)


@pytest.mark.parametrize("t", [
    "Join Zoom Meeting: https://kestrelsys.zoom.us/j/07605122133",
    "https://zoom.us/j/9820012345?pwd=abc", "see https://x.example/orders/09820012345/items",
    "Meeting ID: 094 2430 7429", "Webinar ID 022-6842 3149", "order_id=919328261357 sku=1", "awb=9198200123451",
    "tracking 09820012345", "Invoice no 022-6842 3149",
    "amt=₹2,35,000\n2026-02-25 11:55:02", "amt=₹2,35,000 2026-02-25",
    "0000 000 0000", "0011 1241 0700",                    # STD codes start 01-09 then a non-zero digit
    "011-1241 070", "011-1241 07001",                     # not 11 digits
])
def test_phone_negatives_urls_ids_dates(t):
    assert vals(t, T.PHONE) == [], (t, detect_d2(t))


def test_phone_in_whatsapp_link_and_query_still_detected():
    assert vals("https://wa.me/919820012345", T.PHONE) == ["919820012345"]
    assert vals("https://x.in/lookup?phone=9820012345", T.PHONE) == ["9820012345"]
    assert vals("order 5 call me on 9820012345", T.PHONE) == ["9820012345"]


# ---- item 4: DOB
@pytest.mark.parametrize("t,val", [
    ("DOB: 08.05.1972", "08.05.1972"),
    ("mera janm tithi 1993-11-30 hai", "1993-11-30"),
    ("janam 18/06/1969", "18/06/1969"),
    ("Janm Tithi: 30 Nov 1993", "30 Nov 1993"),
    ("her birthday is 12 March 1984", "12 March 1984"),
    ("birth 12-Mar-1984", "12-Mar-1984"),
    ("born on 5th June 1986", "5th June 1986"),
    ("D.O.B 21st November 1969", "21st November 1969"),
    ("Please note DOB of Sabyasachi Saha is 05-03-1973 in HRMS", "05-03-1973"),
    ("date of birth" + " " * 50 + "12/03/1984", "12/03/1984"),
    ("Row 121: Date_of_Birth 11/12/1975 is not a valid date", "11/12/1975"),
])
def test_dob_extended_context(t, val):
    assert val in vals(t, T.DATE_OF_BIRTH), detect_d2(t)


def test_dob_correction_pair_and_lookalikes():
    t = "Please note DOB of Abhinav Bisht is 2001-05-10, not 08 Jul 2003. Documents attached"
    assert vals(t, T.DATE_OF_BIRTH) == ["2001-05-10", "08 Jul 2003"]
    assert vals("it should be fine, DOB 12/03/1984 and joined 01/04/2015", T.DATE_OF_BIRTH) == ["12/03/1984"]
    assert vals("DOB: 12/03/1984, joined 01/04/2015", T.DATE_OF_BIRTH) == ["12/03/1984"]


@pytest.mark.parametrize("t", [
    "Joined on 08.05.1972", "the janmashtami festival is on 08.05.1972", "birthday party moved to 12/03/2024".replace("2024", "0001"),
    "Employee ID,Date of Birth\n02 June 2024,Zarna",                    # header row then next row's first cell
    "Manager,Department,Date of Birth 02 June 2024,Zarna Bhosale".replace(" 02 June 2024", "") + "\n02 June 2024,x",
    "DOB" + " " * 70 + "12/03/1984",
    "dob 99.99.1972", "born 08.05.1700",
])
def test_dob_negatives_extended(t):
    assert vals(t, T.DATE_OF_BIRTH) == [], (t, detect_d2(t))


def test_dob_csv_header_cell_not_a_context():
    hdr = "Legal Name,PAN,Location,Date of Birth\n02 June 2024,Zarna Bhosale,Custo"
    assert vals(hdr, T.DATE_OF_BIRTH) == []
    assert vals(flat(hdr), T.DATE_OF_BIRTH) == []


# ---- item 5: SECRET casual / XML
@pytest.mark.parametrize("t,val", [
    ("portal login is user a@b.co.in pass GWIV4ubVRNcobYgKf pls dont forward", "GWIV4ubVRNcobYgKf"),
    ("user x pass Monsoon@8765 pls", "Monsoon@8765"),
    ("user x pass Login%7120 thanks", "Login%7120"),
    ("user x pass frlnFzkfnN pls", "frlnFzkfnN"),
    ("the password is xY7#kq9m ok", "xY7#kq9m"),
    ("password is Hunter22", "Hunter22"),
    ("pwd: abc12345", "abc12345"),
    ("PWD: Zz9!yy88.", "Zz9!yy88"),
    ("wifi pass is 8Kx2mP9q, dont share", "8Kx2mP9q"),
])
def test_secret_casual_chat(t, val):
    assert vals(t, T.SECRET) == [val], detect_d2(t)


@pytest.mark.parametrize("t", [
    "pass the salt", "you pass 2024", "pass through-put", "boarding pass 123456AB", "bus pass ABC12345", "pass ok",
    "password is required", "password is optional", "the password is not shared", "pwd: {{DB_PASSWORD}}", "pass ${DB_PASS}",
    "pass <redacted>", "pass *******", "pass abcde", "pass Through", "password: changeme",
])
def test_secret_casual_negatives(t):
    assert vals(t, T.SECRET) == [], (t, detect_d2(t))


def test_secret_xml_password_element():
    t = '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText">Kestrel36!kes</wsse:Password>'
    assert "Kestrel36!kes" in [x for x, ty in [(t[d.start:d.end], d.type) for d in detect_d2(t)] if ty == T.SECRET]
    assert [d for d in detect_d2('<wsse:Password Type="x">{{x}}</wsse:Password>')] == []


def test_secret_curl_basic_auth():
    assert vals("curl -u isu_payroll:workday_4028* -X GET https://x", T.SECRET) == ["workday_4028*"]
    assert vals("curl --user svc_hr:chennai_8295# https://x", T.SECRET) == ["chennai_8295#"]
    assert vals("curl -u 'svc:p@ss w0rd' https://x", T.SECRET) != []
    assert vals("curl -u svc https://x", T.SECRET) == [] and vals("curl -u svc:${PW} https://x", T.SECRET) == []
    assert vals("curl -u user:*** https://x", T.SECRET) == []


def test_secret_authorization_token_scheme():
    assert vals("Authorization: token ghp_" + "a1" * 18, T.SECRET) != []
    assert vals('curl -H "Authorization: token abcDEF123456" https://x', T.SECRET) == ["abcDEF123456"]


# ---- item 6: COMPENSATION precision
@pytest.mark.parametrize("t,val", [
    ("Your revised fixed compensation will be INR 73.3 lakh per annum", "INR 73.3 lakh"),
    ("Your monthly gross will be ₹ 3,30,000 per month", "₹ 3,30,000"),
    ("Current CTC: ₹59,70,000 | Expected: 69 LPA", "₹59,70,000"),
    ("Expected 69 LPA", "69 LPA"),
    ("offer of ₹83,80,000 against current 69.2 lpa", "₹83,80,000"),
    ("Notice period buyout INR 3,45,500 maang rahe hain", "INR 3,45,500"),
    ("variable pay target is 65.3L", "65.3L"),
    ("bonus Rs. 1,50,000 payable in April", "Rs. 1,50,000"),
    ("Total compensation: ₹18,40,000", "₹18,40,000"),
    ("stipend of 25k per month", "25k"),
    ("hike to 12,00,000/-", "12,00,000"),
    ("arrears Rs 40,000 will be paid", "Rs 40,000"),
    ("₹18,40,000 per annum", "₹18,40,000"),
    ("18 lakh p.a.", "18 lakh"),
    ("18,40,000 INR per year", "18,40,000 INR"),
])
def test_compensation_marked_with_pay_context(t, val):
    assert val in vals(t, T.COMPENSATION), detect_d2(t)


@pytest.mark.parametrize("t", [
    "4. courier charges   qty 36   Rs. 4,52,88,000\nTotal (incl. GST 18%): ₹7,399\nPayment terms: net 30",
    "Shares of Workday closed at Rs. 873.76 on the NSE",
    "Office supplies: budget Rs. 4,34,500, actual INR 6,13,500, variance 38%",
    "order_id=286198933651 sku=SKU-JGL-7007 status=PENDING awb=1 value=Rs. 7,59,500",
    "order_id=286198933651 value=₹5,099\norder_id=99917",
    "Invoice INV-2024-000123: INR 7,15,000",
    "Price: ₹2,999 (incl. GST)", "Total: Rs 1,20,000", "share price ₹1,916.87", "MRP ₹499", "purchase of laptops for ₹2,50,000",
    "The trip cost ₹5,000 for the team", "Deposit of ₹50,000 was made", "fee is Rs. 500",
    "revenue grew to ₹12 crore", "net profit Rs. 4 crore",
])
def test_compensation_marked_without_pay_context_or_with_invoice_context(t):
    assert vals(t, T.COMPENSATION) == [], (t, detect_d2(t))


def test_compensation_lpa_and_per_annum_need_no_context():
    assert vals("worth 69 LPA", T.COMPENSATION) == ["69 LPA"]
    assert vals("₹18,40,000 per annum was quoted", T.COMPENSATION) == ["₹18,40,000"]
    assert vals("cost is ₹1,916 p.a.", T.COMPENSATION) == ["₹1,916"]


def test_compensation_pay_context_window_is_80_chars():
    near = "salary " + "." * 60 + " ₹18,40,000"
    far = "salary " + "." * 120 + " ₹18,40,000"
    assert vals(near, T.COMPENSATION) == ["₹18,40,000"]
    assert vals(far, T.COMPENSATION) == []
    assert vals("₹18,40,000 " + "." * 60 + " salary", T.COMPENSATION) == ["₹18,40,000"]       # context on either side


def test_compensation_closer_noncomp_context_wins():
    assert vals("salary revision letter attached. invoice for hosting: ₹18,40,000", T.COMPENSATION) == []
    assert vals("invoice attached. salary revision to ₹18,40,000", T.COMPENSATION) == ["₹18,40,000"]


def test_compensation_bare_number_not_a_year_or_phone_tail():
    for t in ["Salary credit: April 17, 2026", "Salary credit: 30-Apr-2026", "Salary credit: 2023-03-13", "Salary credit: 02 October 2025",
              "Payroll freeze: 13.07.2023", "pay slip for 2024", "revision effective 07-Nov-2025", "bonus for FY 2024"]:
        assert vals(t, T.COMPENSATION) == [], (t, detect_d2(t))
    assert vals("salary 2500", T.COMPENSATION) == ["2500"]        # a 4-digit amount that is not a year context
    assert vals("stipend 12000", T.COMPENSATION) == ["12000"]


def test_compensation_csv_header_cell_keyword_is_not_context():
    t = "Emp,Gross Annual,EmpCode\n27th December 2025,Urvashi,E1"
    assert vals(t, T.COMPENSATION) == []
    assert vals(flat(t), T.COMPENSATION) == []


# ---- item 7: CARD precision
@pytest.mark.parametrize("kw", ["IMEI", "imei:", "tracking", "AWB", "awb=", "pallet", "serial", "Serial No.", "S/N", "order", "order_id=",
                                "PO", "PO#", "shipment", "consignment", "Consignment No"])
def test_card_dropped_after_lookalike_keywords(kw):
    t = f"{kw} {VISA} received"
    assert vals(t, T.CARD) == [], (t, detect_d2(t))


def test_card_keyword_window_is_25_chars_and_word_bounded():
    assert vals("tracking" + " " * 40 + VISA, T.CARD) == [VISA]
    assert vals("tracking" + " " * 10 + VISA, T.CARD) == []
    assert vals(f"Card {VISA}", T.CARD) == [VISA] and vals(f"lost card number {VISA}", T.CARD) == [VISA]
    assert vals(f"Sport {VISA}", T.CARD) == [VISA]                # 'po' inside a word is not the PO keyword
    assert vals(f"Reorder {VISA}", T.CARD) == [VISA]
    assert vals(f"card {group4(VISA)}", T.CARD) == [group4(VISA)]


def test_card_imei_15_digits_from_negatives_shape():
    imei = luhn_complete("35258743023427")
    assert vals(f"IMEI {imei}  tracking 82077541", T.CARD) == []
    assert vals(f"pallet {luhn_complete('34165035297395154')}", T.CARD) == []


# ---- item 8: BANK bare labels
@pytest.mark.parametrize("t,val", [
    ("Account: 50100234567890", "50100234567890"),
    ("account 50100234567890", "50100234567890"),
    ("A/C: 50100234567890", "50100234567890"),
    ("acc no 50100234567890", "50100234567890"),
    ("Acc. No. 50100234567890", "50100234567890"),
    ("mera naya account hai 692891108624529442, ifsc ICIC048I063", "692891108624529442"),
    ("Bank verification failed for account 6789934292745113 (IFSC X)", "6789934292745113"),
    ("credit to account number 36332339050 (IFSC ICIC05I3896)", "36332339050"),
    ("acct=431361808069897 ifsc", "431361808069897"),
    ("salary account: 1066740337987", "1066740337987"),
    ("Savings A/c No: 4650271428160", "4650271428160"),
])
def test_bank_bare_labels(t, val):
    assert vals(t, T.BANK_ACCOUNT) == [val], detect_d2(t)


@pytest.mark.parametrize("t", [
    "account manager 50100234567890",              # different meaning; digits not right after the label
    "account 12345678", "account 1234567890123456789",
    "Accounts payable 50100234567890",
    "the account was created on 2024 and the user 9820012345 called",
    "accounting 50100234567890",
    "50100234567890 account",                      # digits before the label
])
def test_bank_bare_label_negatives(t):
    assert vals(t, T.BANK_ACCOUNT) == [], (t, detect_d2(t))


def test_bank_label_beats_luhn_valid_contiguous_digits_but_not_grouped_card():
    assert [d.type for d in detect_d2(f"acct={VISA}")] == [T.BANK_ACCOUNT]
    assert [d.type for d in detect_d2(f"Account: {VISA}")] == [T.BANK_ACCOUNT]
    assert [d.type for d in detect_d2(f"A/c No: {group4(VISA)}")] == [T.CARD]
    assert [d.type for d in detect_d2(f"card number {VISA}")] == [T.CARD]
    assert [d.type for d in detect_d2(f'{{"cardNumber": "{VISA}"}}')] == [T.CARD]


def test_bank_label_beats_phone_shaped_account():
    ds = detect_d2("A/c No: 8520820132\nIFSC: IDFB07477L9")
    assert [(d.type, d.score >= 0.85) for d in ds if d.type != T.IFSC] == [(T.BANK_ACCOUNT, True)]
    assert [d.type for d in detect_d2("Call 8520820132")] == [T.PHONE]


# ---- misc precision found on tune/dev negatives
@pytest.mark.parametrize("t", ["Assets: logo@2x.png, hero@3x.webp, icon@2x.svg", "react@18.2.0, lodash@4.17.21, @types/node@20.11.5",
                               "SENTRY_DSN=https://88128e5193a2fb6318997d3d6375ebfb@o723579.ingest.sentry.io/8406734",
                               "@app.route('/health'), @retry(times=2)", "git@github.com:org/repo.git".replace("git@", "git @")])
def test_email_lookalikes(t):
    assert vals(t, T.EMAIL) == []


def test_email_real_addresses_still_found_next_to_lookalikes():
    assert vals("logo@2x.png and rahul@corp.co.in", T.EMAIL) == ["rahul@corp.co.in"]
    assert vals("user@example.md", T.EMAIL) == ["user@example.md"]


def test_pan_lowercase_needs_label():
    assert vals("for the 12bb form my pan is rycpr4442j, uan pending", T.PAN) == ["rycpr4442j"]
    assert vals("PAN: abcpe1234f", T.PAN) == ["abcpe1234f"]
    assert vals("permanent account number abcpe1234f", T.PAN) == ["abcpe1234f"]
    assert vals("ref rycpr4442j", T.PAN) == []
    assert vals("company pantry rycpr4442j", T.PAN) == []


def test_employee_id_keyword_never_crosses_lines_or_header_cells():
    assert vals("Emp,Name,Worker ID\n04 Feb 2024,lokesh", T.EMPLOYEE_ID) == []
    assert vals(flat("Emp,Name,Worker ID\n04 Feb 2024,lokesh"), T.EMPLOYEE_ID) == []
    assert vals("Employee ID\nSWAGATA GHOSH", T.EMPLOYEE_ID) == []
    assert vals("Employee ID: E123456", T.EMPLOYEE_ID) == ["E123456"]


def test_detect_d2_stays_sorted_and_non_overlapping_with_field_context():
    t = flat(FLAT_ADVICE + " " + 'Contact: 080-6864 6172 email: a.b@corp.com {"password": "hunter22x"}')
    ds = detect_d2(t)
    assert [d.start for d in ds] == sorted(d.start for d in ds)
    assert all(a.end <= b.start for a, b in zip(ds, ds[1:]))
    assert {d.type for d in ds} >= {T.PERSON, T.BANK_ACCOUNT, T.COMPENSATION, T.PHONE, T.EMAIL, T.SECRET}


def test_field_context_performance_on_large_inputs():
    import time
    big = ("Emp ID,Full Name,Date of Birth,Annual CTC\n" + "EMP-1001,Meera Iyer,14/03/1988,\"18,40,000\"\n" * 400)
    t0 = time.time()
    detect_d2(big)
    detect_d2(flat(big))
    detect_d2('{"a":' * 500)
    detect_d2("x: " * 3000)
    detect_d2("," * 5000 + "Name")
    detect_d2("<a>" * 1000)
    assert time.time() - t0 < 8


def test_fuzz_no_crash_and_valid_offsets():
    rnd = random.Random(2024)
    alphabet = list('abcXYZ019 ,:=;"\'{}[]<>/\\-_.@#₹\n\t|') + ["password", "Name", "Salary", "DOB", "a/c", "Emp ID", "12,00,000",
                                                                   "98200 12345", "</x>", "<a>", "EMP-1234", "Rs.", "lakh"]
    for _ in range(400):
        t = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 120)))
        for text in (t, flat(t)):
            ds = detect_d2(text)
            assert all(0 <= d.start < d.end <= len(text) for d in ds)
            assert [d.start for d in ds] == sorted(d.start for d in ds)
            assert all(a.end <= b.start for a, b in zip(ds, ds[1:]))


def test_fc_pass_style_keys():
    assert fcv('db_pass = "x1y2z3q9"', T.SECRET) == ["x1y2z3q9"]
    assert fcv('{"smtp_pass": "Zx81!kk"}', T.SECRET) == ["Zx81!kk"]
    assert fcv('{"pw": "Zx81!kk"}', T.SECRET) == ["Zx81!kk"]
    assert fc('{"pass": "abcdef12"}') == []             # bare "pass" is too ambiguous as a key
    assert fc('Result: pass') == []


def test_fc_alias_placeholders_from_the_gateway_are_not_values():
    for t in ['{"name": "[PERSON_7QX2MA]"}', '{"email": "[EMAIL_AB12CD]"}', '{"password": "[SECRET_AB12CD]"}',
              '{"salary": "[COMPENSATION ₹15L–₹20L]"}', '{"employee_id": "[EMPLOYEE_ID_AB12CD]"}', 'phone: [PHONE_AB12CD]']:
        assert fc(t) == [], t
