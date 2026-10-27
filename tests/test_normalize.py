"""D0 multi-view normalisation: examples, negatives and a seeded property test on the offset maps."""
from __future__ import annotations

import random
import re

import pytest

from pgw.normalize import normalize, to_original, views


def by_name(text):
    return {n: v for n, v, _ in views(text)}


# --- base view --------------------------------------------------------------------------------

def test_first_view_is_base_and_matches_normalize():
    for t in ("", "plain text", "r​ahul@acmе.in", "9 8 2 0"):
        v = views(t)
        assert v[0][0] == "base"
        assert (v[0][1], v[0][2]) == normalize(t)


def test_clean_text_has_only_base_when_nothing_to_deobfuscate():
    # already title-cased, no spaced runs, no email separators, no long digit groups
    assert [n for n, *_ in views("Please Review The Q3 Plan With Rahul On Friday.")] == ["base"]
    # ordinary prose only ever adds the (cheap, same-length) casefold_titled view, see _titled()
    assert {n for n, *_ in views("Please review the Q3 plan with Rahul on Friday.")} <= {"base", "casefold_titled"}


@pytest.mark.parametrize("raw,expected", [
    ("९८२००१२३४५", "9820012345"),                      # Devanagari
    ("٠١٢٣٤٥٦٧٨٩", "0123456789"),                      # Arabic-Indic
    ("１２３４５６７８９０", "1234567890"),               # fullwidth (NFKC)
    ("𝟗𝟖𝟐", "982"),                                     # mathematical digits (NFKC)
])
def test_non_ascii_digits_mapped_in_base(raw, expected):
    assert normalize(raw)[0] == expected


def test_math_and_fullwidth_letters_handled_by_nfkc():
    assert normalize("𝐑𝐚𝐡𝐮𝐥 Ｓｈａｒｍａ")[0] == "Rahul Sharma"
    assert normalize("𝓇𝒶𝒽𝓊𝓁")[0] == "rahul"


def test_confusables_cyrillic_greek_armenian():
    assert normalize("rаhսl")[0] == "rahul"              # Cyrillic а + Armenian ս
    assert normalize("ΑΒΕΗΙΚΜΝΟΡΤΧΥΖ")[0] == "ABEHIKMNOPTXYZ"
    assert normalize("ѕһаrmа")[0] == "sharma"
    assert normalize("ᴀᴄᴍᴇ")[0] == "acme"                  # small capitals


# --- despaced ---------------------------------------------------------------------------------

def test_despaced_examples():
    assert by_name("R a h u l")["despaced"] == "Rahul"
    assert by_name("R a h u l  S h a r m a")["despaced"] == "Rahul Sharma"
    assert by_name("R a h u l   S h a r m a joined")["despaced"] == "Rahul Sharma joined"
    assert by_name("R a h u l")["despaced"] == "Rahul"        # NBSP
    assert by_name("R.a.h.u.l")["despaced"] == "Rahul"
    assert by_name("call 9 8 2 0 0 1 2 3 4 5 now")["despaced"] == "call 9820012345 now"
    assert by_name("S r i v a  ,  G u n j a n")["despaced"] == "Sriva, Gunjan"
    assert by_name("D ' S i l v a")["despaced"] == "D'Silva"
    assert by_name("Ms. R .   D ' S o u z a")["despaced"] == "Ms. R. D'Souza"


def test_despaced_negatives():
    for t in ("I am a good fit", "a b", "I am a", "Vitamin A and B", "go to a b test", "x y"):
        assert "despaced" not in by_name(t), t
    # a newline is never a separator
    assert "despaced" not in by_name("a\nb\nc\nd")


def test_despaced_short_digit_lists_documented_behaviour():
    # DECISION: `version 1 2 3` has >= 3 single-char tokens, so the despaced VIEW is produced
    # ("version 123"). That is harmless (the base view is still scanned, nothing is lost) and it is
    # what makes "9 8 2 0 0 1 2 3 4 5" detectable; the cost of a false extra view is one detector pass.
    assert by_name("version 1 2 3")["despaced"] == "version 123"


# --- deobfuscated_email -----------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("mail rahul [at] acme [dot] in now", "mail rahul@acme.in now"),
    ("rahul(at)acme(dot)co(dot)in", "rahul@acme.co.in"),
    ("rahul {at} acme {dot} in", "rahul@acme.in"),
    ("rahul AT acme DOT in", "rahul@acme.in"),
    ("write to rahul at acme dot in please", "write to rahul@acme.in please"),
    ("rahul.sharma at acme.co.in", "rahul@acme.co.in".replace("rahul", "rahul.sharma")),
    ("rahul dot sharma at acme dot co dot in", "rahul.sharma@acme.co.in"),
    ("rahul [at] acme.in", "rahul@acme.in"),
    ("rahul @ acme.in", "rahul@acme.in"),
    ("reached at tejinder.cheema [at] kestrelsys [dot] in for", "reached at tejinder.cheema@kestrelsys.in for"),
])
def test_deobfuscated_email(raw, expected):
    assert by_name(raw)["deobfuscated_email"] == expected


def test_fullwidth_at_and_dot_are_base_normalised():
    assert normalize("rahul＠acme．in")[0] == "rahul@acme.in"
    assert [n for n, *_ in views("rahul＠acme．in")][:1] == ["base"]


def test_email_negatives():
    for t in ("meet at the office", "see me at noon", "at home dot", "rahul@acme.in",
              "call me at 5 pm dot",  "arrive at gate 4 dot 5"):
        assert "deobfuscated_email" not in by_name(t), t
    # spelled-out form needs an alphabetic TLD
    assert "deobfuscated_email" not in by_name("me at box dot 42")


def test_email_replacement_maps_to_original_span():
    raw = "mail rahul [at] acme [dot] in now"
    v = {n: (t, m) for n, t, m in views(raw)}["deobfuscated_email"]
    s = v[0].index("rahul@acme.in")
    os_, oe = to_original(v[1], s, s + len("rahul@acme.in"))
    assert raw[os_:oe] == "rahul [at] acme [dot] in"


# --- digits_joined ----------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("acct 1234 5678 9012", "acct 123456789012"),
    ("acct 1234 5678 9012", "acct 123456789012"),
    ("acct 1234 5678 9012 34", "acct 123456789012" + "34"),
    ("ph 98200.123-45", "ph 9820012345"),        # mixed separators join; pure hyphen chains are left to the base view
    ("ph 98200.12345", "ph 9820012345"),
    ("no 1234/5678/9012", "no 123456789012"),
    ("no 1234–5678–9012", "no 123456789012"),
    ("no 1234—5678—9012", "no 123456789012"),
    ("no 1234_5678_9012", "no 123456789012"),
    ("+91 98200 12345", "+919820012345"),
    ("card 4111 1111 1111 1111", "card 4111111111111111"),
    ("card 4111 1111 1111 1111 4111 1111 1111 1111", "card 4111111111111111 4111111111111111"),
    ("aadhaar ९८२० ००१२ ३४५६", "aadhaar 982000123456"),
])
def test_digits_joined(raw, expected):
    assert by_name(raw)["digits_joined"] == expected


def test_digits_joined_negatives():
    assert "digits_joined" not in by_name("date 12 03 2024")            # 8 digits
    assert "digits_joined" not in by_name("date 2024-03-12")
    assert "digits_joined" not in by_name("1234 5678\n9012")            # never across a newline
    assert "digits_joined" not in by_name("9820012345")                  # one group: nothing to join
    assert "digits_joined" not in by_name("1234 5678")                   # 8 digits
    assert "digits_joined" not in by_name("12345678901234567890")        # one 20-digit group


def test_digits_joined_keeps_newline_separated_lines_apart():
    t = by_name("1234 5678 9012\n3456 7890 1234")["digits_joined"]
    assert t == "123456789012\n345678901234"


# --- casefold_titled --------------------------------------------------------------------------

def test_casefold_titled():
    assert by_name("please email rahul sharma today")["casefold_titled"] == "Please Email Rahul Sharma Today"
    assert by_name("RAHUL SHARMA JOINED ACME")["casefold_titled"] == "Rahul Sharma Joined Acme"
    assert by_name("contact o'brien re abcde1234f")["casefold_titled"] == "Contact O'brien Re abcde1234f"
    assert by_name("PAN ABCDE1234F for RAHUL")["casefold_titled"] == "Pan ABCDE1234F For Rahul"   # ids kept


def test_casefold_titled_negative_and_length():
    assert "casefold_titled" not in by_name("Rahul Sharma Joined Acme")     # already titled (mixed)
    assert "casefold_titled" not in by_name("12 34")
    t = "straße RAHUL"
    v = by_name(t)
    assert all(len(x) == len(normalize(t)[0]) for n, x in v.items() if n == "casefold_titled")


# --- combined ---------------------------------------------------------------------------------

def test_despaced_email_combined_view():
    v = by_name("r a h u l  at  acme dot in")
    assert v["despaced_email"] == "rahul@acme.in"
    # every extra view is derived from the base; the plain email view alone cannot fix the spaced local part
    assert v["deobfuscated_email"] == "r a h u l@acme.in"


# --- property test: every view span maps back to the original --------------------------------

_SKIP = set(" .-/_‐‑‒–—−[](){}<>")
_TOKEN = re.compile(r"[ ]*[\[({<]?[ ]*(?:at|dot)[ ]*[\])}>]?[ ]*", re.I)


_SWAPPED = {"O": "0", "o": "0", "I": "1", "l": "1"}


def _explains(view_sub: str, orig_norm: str, fold: bool, swap: bool = False) -> bool:
    """view_sub must be derivable from the normalised original substring by only dropping separator
    characters and rewriting a bracketed/spelled at/dot token to '@' / '.' (digit_swap: also O/o -> 0, l/I -> 1)."""
    i = 0
    eq = (lambda a, b: a.casefold() == b.casefold()) if fold else \
        (lambda a, b: a == b or _SWAPPED.get(a) == b) if swap else (lambda a, b: a == b)
    for pos, vc in enumerate(view_sub):
        last = pos == len(view_sub) - 1
        while True:
            if i < len(orig_norm) and eq(orig_norm[i], vc):
                i += 1
                break
            if vc in "@." and _TOKEN.match(orig_norm, i) and _TOKEN.match(orig_norm, i).end() > i:
                i = _TOKEN.match(orig_norm, i).end()
                break
            if last and vc in "@." and all(c in _SKIP for c in orig_norm[i:]):
                # span ENDS on a replaced token: only the token's first char (space/bracket) is covered
                i = len(orig_norm)
                break
            if i < len(orig_norm) and orig_norm[i] in _SKIP:
                i += 1
                continue
            return False
    return all(c in _SKIP for c in orig_norm[i:])


_ALPHA = ["a", "b", "c", "d", "e", "h", "l", "m", "r", "s", "u", "R", "S", "A", "x", "z", "Q"]
_SEPS = [" ", " ", " ", ".", "-", "/", "_", "–", " ", " ", "  ", ",", "\n", "\t", ""]
_ODD = ["​", "‍", "﻿", "а", "е", "о", "Α", "ս", "９", "९", "٣", "＠", "．", "@", "'", ":", "ﬁ"[:0]]
_WORDS = ["at", "dot", "AT", "DOT", "[at]", "(at)", "{at}", "[dot]", "(dot)", "{dot}", " [at] ", " (dot) "]


def _rand_text(rng: random.Random) -> str:
    parts: list[str] = []
    for _ in range(rng.randint(1, 14)):
        k = rng.random()
        if k < 0.20:                                   # spaced letters / digits
            n = rng.randint(2, 9)
            sep = rng.choice([" ", " ", " ", "."])
            pool = _ALPHA if rng.random() < 0.6 else list("0123456789")
            parts.append(sep.join(rng.choice(pool) for _ in range(n)))
        elif k < 0.38:                                 # digit groups with odd separators
            sep = rng.choice(_SEPS)
            parts.append(sep.join("".join(rng.choice("0123456789९٣９") for _ in range(rng.randint(1, 6)))
                                  for _ in range(rng.randint(1, 5))))
        elif k < 0.56:                                 # obfuscated email
            at, dot = rng.choice(_WORDS), rng.choice(_WORDS)
            pad = rng.choice(["", " ", "  ", " "])
            lo = "".join(rng.choice("abcdrs") for _ in range(rng.randint(1, 5)))
            parts.append(f"{lo}{pad}{at}{pad}acme{pad}{dot}{pad}{rng.choice(['in', 'co', 'com', '42'])}")
        elif k < 0.66:                                 # lower / upper words
            w = " ".join("".join(rng.choice(_ALPHA) for _ in range(rng.randint(2, 7))) for _ in range(rng.randint(1, 4)))
            parts.append(w.lower() if rng.random() < 0.5 else w.upper())
        else:
            parts.append("".join(rng.choice(_ALPHA + _ODD + [" ", "\n", "-", "."]) for _ in range(rng.randint(0, 8))))
        parts.append(rng.choice(_SEPS))
    return "".join(parts)


def _check(text: str, rng: random.Random, seen: set):
    vs = views(text)
    assert vs[0][0] == "base" and (vs[0][1], vs[0][2]) == normalize(text)
    for name, vt, om in vs:
        seen.add(name)
        assert len(om) == len(vt) + 1, (name, text)
        assert om[-1] == len(text), (name, text)
        assert all(a <= b for a, b in zip(om, om[1:])), (name, text)
        assert all(0 <= x <= len(text) for x in om), (name, text)
        n = len(vt)
        if n <= 10:
            spans = [(s, e) for s in range(n) for e in range(s + 1, n + 1)]
        else:
            spans = [(0, n)] + [(s, s + 1) for s in range(n)]
            for _ in range(40):
                s = rng.randrange(n)
                spans.append((s, rng.randint(s + 1, n)))
        for s, e in spans:
            os_, oe = to_original(om, s, e)
            assert 0 <= os_ < oe <= len(text), (name, text, s, e, os_, oe)
            assert _explains(vt[s:e], normalize(text[os_:oe])[0], name == "casefold_titled", name == "digit_swap"), \
                (name, text, vt[s:e], text[os_:oe])


def test_property_spans_map_back_to_original():
    rng = random.Random(20240607)
    seen: set[str] = set()
    for _ in range(2500):
        _check(_rand_text(rng), rng, seen)
    # the generator must actually exercise every view
    assert seen >= {"base", "despaced", "deobfuscated_email", "digits_joined", "casefold_titled"}, seen


def test_property_handwritten_adversarial():
    rng = random.Random(7)
    seen: set[str] = set()
    for t in [
        "R a h u l  S h a r m a", "R a h u l   S h a r", "S r i v a ,   G u n",
        "W .   N i n g t h", "rahul [at] acme [dot] in", "a (at) b (dot) co (dot) in", "rahul＠acme．in",
        "1234 5678 9012\n3456 7890 1234", "​9​8 2 0 0 1 2 3 4 5", "९८२० ००१२ ३४५६",
        "r a h u l [at] a c m e [dot] i n", "RAHUL SHARMA", "rahul sharma", "",
    ]:
        _check(t, rng, seen)


def test_hyphen_only_chains_not_joined_but_phone_still_detected():
    """R2: joining GL codes like 7360-1933-03 created phone false positives; base view handles hyphen phones."""
    from pgw.detect.recognizers import detect_d2
    assert [n for n, _, _ in views("GL 7360-1933-03  CC-")] == ["base"] or \
        all(n != "digits_joined" for n, _, _ in views("GL 7360-1933-03  CC-"))
    assert any(d.type.value == "PHONE" for d in detect_d2("call 98200-12345 today"))
