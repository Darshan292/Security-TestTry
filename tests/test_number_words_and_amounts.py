"""Numbers written in words and amounts without a currency cue (pgw.detect.numwords, recognizers._amount_shapes).

A fresh synthetic set designed for this test: identifiers of 12 schemes spelled digit by digit in varied formats,
amounts in Indian digit grouping and with lakh/crore in digits and in words, and benign text that must cross
unchanged. Every value is synthetic. The auditor's literal values appear only in one smoke test each.

Rule for counts (documented in recognizers._amount_shapes): an amount shape directly followed by a unit of count
("1,50,000 employees", "two lakh people") is a count, not money, and is left alone.
"""
import json
import os
import re
import time

import pytest
from starlette.testclient import TestClient

from pgw.alias import canon
from pgw.detect import numwords
from pgw.detect.recognizers import luhn_valid, verhoeff_check_digit
from pgw.gateway.app import create_app
from pgw.gateway.settings import Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.normalize import normalize
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack, generalize
from pgw.types import Context, EntityType
from tests.sealed import ROOT, attest, production, sha

POLICY = PolicyPack.load(ROOT / "policies/hris-default.yaml")
PROT = Protector(POLICY, Keys(bytes(32)), None)
CTX = Context("t", "t", "integration_debug")     # identifiers REDACTed (not blocked), amounts aliased
BAND = Context("t", "t", "comp_band_analysis")   # amounts generalised to a band

WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_DIGIT_WORD = re.compile(r"(?i)\b(?:zero|oh|nil|one|two|three|four|five|six|seven|eight|nine|double|triple)\b")
_MARKER = re.compile(r"\[[A-Z_]+(?:_[0-9A-Z]{6,8})?\]")


def _card(prefix15: str) -> str:
    for d in "0123456789":
        if luhn_valid(prefix15 + d):
            return prefix15 + d
    raise AssertionError


AADHAAR = "61937402518" + verhoeff_check_digit("61937402518")
CARD = _card("453914880343646")


def spell(digits: str, style: str = "space", groups: tuple[int, ...] = ()) -> str:
    """Digits as words. styles: space, hyphen, title, upper, oh (0 -> oh), repeat (double / triple)."""
    def word(d):
        w = "oh" if style == "oh" and d == "0" else WORDS[int(d)]
        return w.upper() if style == "upper" else w.title() if style == "title" else w

    def chunk(s):
        out, i = [], 0
        while i < len(s):
            n = len(s[i:]) - len(s[i:].lstrip(s[i]))
            if style == "repeat" and n >= 2:
                k = min(n, 3)
                out.append(("double " if k == 2 else "triple ") + word(s[i]))
                i += k
            else:
                out.append(word(s[i]))
                i += 1
        return ("-" if style == "hyphen" else " ").join(out)

    if not groups:
        return chunk(digits)
    parts, i = [], 0
    for g in groups:
        parts.append(chunk(digits[i:i + g]))
        i += g
    return ", ".join(parts)


# (scheme, prefix, spelled value, suffix): 12 schemes, cue words present for some and absent for others
IDS = [
    ("aadhaar_grouped", "my aadhaar is ", spell(AADHAAR, groups=(4, 4, 4)), ". please update"),
    ("aadhaar_no_cue_hyphen", "the number on the card is ", spell(AADHAAR, "hyphen"), ", kindly fix"),
    ("mobile_repeat", "reach me on ", spell("9731004885", "repeat"), " after six"),
    ("mobile_groups", "whatsapp ", spell("9731046285", groups=(4, 3, 3)), " anytime"),
    ("landline_oh", "office line ", spell("08041726593", "oh"), " ext 22"),
    ("bank_account_cue", "salary a/c no ", spell("39182047561203"), " at the main branch"),
    ("bank_account_no_cue", "credit it to ", spell("60214739582", "title"), " please"),
    ("uan", "UAN ", spell("100847362915", "upper"), " is not linked"),
    ("esic", "ESIC IP number ", spell("3104827756", groups=(2, 2, 6)), " for the claim"),
    ("card", "the corporate card ", spell(CARD, groups=(4, 4, 4, 4)), " was charged twice"),
    ("passport", "passport ", "P " + spell("4829173"), " expires soon"),
    ("driving_licence", "licence MH ", spell("1220160034871"), " renewed"),
    ("account_mixed_digits", "account number ", "50100 " + spell("234567891"), " needs update"),
]


def _protect(text: str, ctx: Context = CTX) -> str:
    return PROT.protect([{"role": "user", "content": text}], ctx).messages[0]["content"]


@pytest.mark.parametrize("scheme,prefix,value,suffix", IDS, ids=[i[0] for i in IDS])
def test_identifier_spoken_in_words_is_replaced_exactly(scheme, prefix, value, suffix):
    out = _protect(prefix + value + suffix)
    assert out.startswith(prefix) and out.endswith(suffix), out          # offsets map back exactly
    middle = out[len(prefix):len(out) - len(suffix)]
    assert _MARKER.fullmatch(middle), f"{scheme}: {out!r}"
    assert not _DIGIT_WORD.search(middle)


def test_view_maps_every_word_back_exactly():
    s, om = normalize("call nine eight double seven, six five four three two one now")
    view, vm = numwords.digit_view(s, om)
    assert view == "call 9877 654321 now"
    assert len(vm) == len(view) + 1
    assert [s[vm[i]:vm[i] + 6] for i in (5, 7, 8)] == ["nine e", "double", "seven,"]
    assert s[vm[10]:vm[10] + 3] == "six"


def test_spoken_and_written_identifier_share_one_alias():
    assert canon(EntityType.PHONE, spell("9731046285")) == "9731046285"
    assert canon(EntityType.AADHAAR, spell(AADHAAR, "repeat")) == AADHAAR
    a = PROT.protect([{"role": "user", "content": f"call {spell('9731046285')} or 97310 46285"}], CTX)
    phones = [e for e in a.entities.values() if e.type is EntityType.PHONE]
    assert len(phones) == 1 and len(phones[0].sources) == 2


# --- amounts -------------------------------------------------------------------------------------------------------

AMOUNTS = [
    ("the system shows ", "17,25,500", " for March"),
    ("in-hand came to ", "1,08,250", " pm only"),
    ("arrears of ", "12,40,000.50", " are pending"),
    ("the letter says ", "thirty one lakh twenty five thousand", " but HRMS differs"),
    ("joining bonus ", "nine lakh", " was promised"),
    ("the retention grant is ", "1.75 crore", " over four years"),
    ("the settlement agreed is ", "two crore forty lakh", " for her"),
    ("Offer: ", "Eighteen Lakh Seventy-Five Thousand", " fixed"),
    ("revised to ", "14 lakh 20 thousand", " from April"),
    ("my package is ", "6.5 lacs", ", please check"),
    ("she drew ", "3,05,75,000", " over the contract"),
    ("the US file shows ", "2,150,000", " gross"),
]


@pytest.mark.parametrize("prefix,amount,suffix", AMOUNTS, ids=[a[1] for a in AMOUNTS])
def test_amount_is_compensation(prefix, amount, suffix):
    out = _protect(prefix + amount + suffix)
    assert out.startswith(prefix) and out.endswith(suffix), out
    assert re.fullmatch(r"\[COMPENSATION_[0-9A-Z]{6,8}\]", out[len(prefix):len(out) - len(suffix)]), out


@pytest.mark.parametrize("amount,band", [("21,75,000", "₹20L–₹25L"), ("thirty one lakh twenty five thousand", "₹30L–₹35L"),
                                         ("1.75 crore", "₹175L–₹180L"), ("nine lakh", "₹5L–₹10L"),
                                         ("14 lakh 20 thousand", "₹10L–₹15L")])
def test_amount_is_generalised_where_the_policy_generalises(amount, band):
    assert _protect(f"the system shows {amount} now", BAND) == f"the system shows [COMPENSATION {band}] now"
    assert generalize(EntityType.COMPENSATION, amount) == f"[COMPENSATION {band}]"


BENIGN = [
    "Please allow one or two days for processing.",
    "Enable two-factor authentication on the portal.",
    "Declare your investments under Section 80C before Friday.",
    "Download Form 16 from the portal.",
    "The group has 1,50,000 employees across India.",
    "We processed 2,40,000 records last night.",
    "Over two lakh people registered for the drive.",
    "Lakhs of applicants apply every year.",
    "The meeting is on 12/03/2025 at 10:30 in room 1204.",
    "Release 3.14.2 shipped in 2024 for FY 2025-26.",
    "We hired five people in one week, two of them on day one.",
    "Section 80C allows a maximum deduction of 1,50,000 a year.",
    "The office has one two-seater sofa and three chairs.",
    "Rank them one, two, three and four.",
    "Payroll for 1,20,000 users runs at 9 pm.",
    "The wage ceiling is 21,000 per month.",
    "Invoice total 14,60,000 for the venue.",
    "Revenue grew to 12 crore this year.",
]


@pytest.mark.parametrize("text", BENIGN)
def test_benign_text_is_untouched(text):
    assert _protect(text) == text


def test_prose_does_not_make_a_view():
    for text in BENIGN:
        s, om = normalize(text)
        assert numwords.digit_view(s, om) is None, text


# --- auditor smoke tests (one each) --------------------------------------------------------------------------------

def test_auditor_smoke_values():
    out = _protect("my aadhar is two three four one, five six seven eight, nine zero one two. pls update.")
    assert out in ("my aadhar is [IDENTIFIER]. pls update.", "my aadhar is [AADHAAR]. pls update.",
                   "my aadhar is [AADHAAR_LIKE]. pls update.")
    assert "1,12,400" not in _protect("in-hand is only 1,12,400 pm.")
    out = _protect("Offer letter says twenty two lakh fifty thousand but system shows 21,00,000.")
    assert "lakh" not in out and "21,00,000" not in out


# --- end to end: production gateway, signed config, outbound bytes, restore -----------------------------------------

KEY = "numwords-key"


def _gateway(tmp_path):
    mock = MockLLMUpstream("echo")
    tasks = {"ticket_summary": str(ROOT / "contracts/ticket_summary.yaml")}
    apps = [{"app_id": "pilot", "tenant": "acme", "key_sha256": sha(KEY), "purposes": sorted(tasks),
             "restore_output": True, "models": ["mock-1"], "tasks": tasks}]
    s = Settings(**production(tmp_path, apps), ledger_db_path=str(tmp_path / "l.sqlite"),
                 egress_ledger_path=str(tmp_path / "e.jsonl"), master_key=os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s)), mock


def _send(client, text):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": text}],
            "pgw_subjects": {"requester": {"name": "Ritika Banerjee"}}}
    return client.post("/v1/chat/completions", json=body, headers={
        "Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary", **attest("ticket_summary", body)})


def _digits_leak(digits: str, text: str) -> bool:
    return digits in "".join(c for c in text if c.isdigit())


def test_gateway_sends_no_spoken_identifier_and_restores_amounts(tmp_path):
    client, mock = _gateway(tmp_path)
    ids = {scheme: v for scheme, _, v, _ in IDS}
    amounts = [a for _, a, _ in AMOUNTS]
    lines = [f"{p}{v}{s}." for _, p, v, s in IDS] + [f"Item: {p}{a}{s}." for p, a, s in AMOUNTS]
    r = _send(client, "Raised by Ritika Banerjee.\n" + "\n".join(lines) + "\n" + " ".join(BENIGN))
    assert r.status_code == 200, r.text
    wire = mock.received[-1].decode("utf-8")
    sent = "\n".join(m["content"] for m in json.loads(wire)["messages"] if m["role"] != "system")
    for scheme, v in ids.items():
        assert v.lower() not in wire.lower(), f"{scheme} crossed in words"
        assert not _digits_leak(numwords.to_digits(v), wire), f"{scheme} crossed as digits"
    for a in amounts:
        assert a.lower() not in wire.lower(), f"amount {a} crossed"
    for b in BENIGN:
        assert b in sent, f"benign text {b!r} was masked"
    ans = r.json()["choices"][0]["message"]["content"]
    for a in amounts:                     # C2 on EXT_PSEUDO: aliased out, restored exactly in the answer
        assert f"{a}" in ans, f"amount {a} not restored"
    restored = 0
    for scheme, p, v, s in IDS:
        line = f"{p}{v}{s}."
        types = {d.type for d in PROT.analyze([{"role": "user", "content": line}], CTX)[0]
                 if d.start < len(p) + len(v) and d.end > len(p)}
        if types & {EntityType.PHONE, EntityType.IDENTIFIER}:
            # a phone is C1; an identifier of no recognised scheme is REF: aliased out, the exact words come back
            assert v in ans, scheme
            restored += 1
        else:                                           # C3 identifiers (and AADHAAR_LIKE) are never restored
            assert v.lower() not in ans.lower(), (scheme, types)
    assert restored < len(IDS)


def test_gateway_auditor_smoke(tmp_path):
    client, mock = _gateway(tmp_path)
    text = ("my aadhar is two three four one, five six seven eight, nine zero one two. "
            "Offer letter says twenty two lakh fifty thousand but system shows 21,00,000; in-hand is 1,12,400 pm.")
    r = _send(client, text)
    assert r.status_code == 200
    wire = mock.received[-1].decode("utf-8")
    for v in ("two three four one", "twenty two lakh fifty thousand", "21,00,000", "1,12,400"):
        assert v not in wire
    assert not _digits_leak("234156789012", wire)
    ans = r.json()["choices"][0]["message"]["content"]
    assert "twenty two lakh fifty thousand" in ans and "21,00,000" in ans and "1,12,400" in ans


# --- adversarial floods: linear, under the test_dos_bounds bound ----------------------------------------------------

LIMIT = 1.5
FLOODS = {
    "digit_words": "one two three four five six seven eight nine zero ",
    "digit_word_sentences": "one two three four five six. ",
    "comma_words": "nine, ",
    "double_words": "double five triple six ",
    "lakh_words": "twenty two lakh fifty thousand ",
    "lakh_grouped": "1,00,000 ",
    "lakh_grouped_with_limit_words": "ceiling 1,00,000 ",
}


def _best(text: str) -> float:
    best = float("inf")
    for _ in range(3):
        t = time.perf_counter()
        PROT.detect_text(text)
        best = min(best, time.perf_counter() - t)
    return best


def _flood(unit: str, n: int) -> str:
    return (unit * (n // len(unit) + 1))[:n]


@pytest.mark.parametrize("name", FLOODS)
def test_number_word_flood_is_fast_and_linear(name):
    small = _best(_flood(FLOODS[name], 25_000))
    large = _best(_flood(FLOODS[name], 100_000))
    assert large < LIMIT
    assert large < 8 * max(small, 0.02)
