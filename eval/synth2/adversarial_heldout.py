"""HELD-OUT adversarial transforms for E-ADV (Release 2, Amendment 2026-09-30b item 2).

This module is HELD OUT: it must not be shown to, or read by, anyone fixing the detector until after the one-shot
``test2`` run.  Its kinds are disjoint from the six R1 kinds (homoglyph, zero_width, spaced, obfuscated_email, case, nbsp).

Held-out kinds
--------------
mixed_separators   digit groups of numeric values glued with a random mix of different separators
native_digits      ASCII digits replaced by another script's decimal digits (Devanagari / fullwidth / Arabic-Indic)
spoken_email       e-mail addresses written the way people dictate or paste them (Indian "at the rate", fullwidth, braces)
leetspeak_name     person names with look-alike digits/symbols for some letters
line_wrap          a value broken across a physical line (as in fixed-width mail clients and PDF copy-paste)
ocr_confusion      scanner/OCR letter<->digit confusions inside identifiers and numbers
html_entity        characters of values written as numeric HTML character references (scraped web mail / CMS exports)

``transform(item, kind, rng) -> item`` rebuilds the text piecewise from untouched gap text plus rewritten entity text,
so every span is remapped by construction (never found by search).  ``check_item`` / ``self_check`` assert that every span
still covers its entity (canonical forms equal) and that nothing outside the spans changed.
"""
from __future__ import annotations

import copy
import html
import random
import re
import sys
import unicodedata

KINDS = ["mixed_separators", "native_digits", "spoken_email", "leetspeak_name", "line_wrap", "ocr_confusion", "html_entity"]

_R1 = {"homoglyph", "zero_width", "spaced", "obfuscated_email", "case", "nbsp"}
assert not (_R1 & set(KINDS)), "held-out kinds must be new"

_NUMERIC = ("PHONE", "AADHAAR", "CARD", "BANK_ACCOUNT")
_SEPS = ["-", ".", " ", "/", "-", "."]
_DIGITS = {"deva": "०१२३४५६७८९", "full": "０１２３４５６７８９", "arab": "٠١٢٣٤٥٦٧٨٩"}
_LEET = {"a": ["4", "@"], "e": ["3"], "i": ["1", "!"], "o": ["0"], "s": ["5", "$"], "t": ["7"], "l": ["1"], "b": ["8"]}
_LEET_BACK = {"4": "a", "@": "a", "3": "e", "1": "i", "!": "i", "0": "o", "5": "s", "$": "s", "7": "t", "8": "b"}
_OCR = {"0": ["O"], "1": ["l", "I"], "5": ["S"], "8": ["B"], "2": ["Z"], "O": ["0"], "I": ["1"], "S": ["5"], "B": ["8"], "Z": ["2"], "l": ["1"]}
_OCR_FOLD = {"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "s": "5", "B": "8", "Z": "2"}


def _digits_only(v):
    return "".join(c for c in v if c.isdigit())


def _split_sizes(n, rng):
    sizes, left = [], n
    while left > 0:
        k = min(left, rng.choice([2, 3, 3, 4, 4, 5]))
        if left - k == 1:
            k += 1
        sizes.append(k)
        left -= k
    return sizes


def _mixed_separators(v, t, rng):
    if t in _NUMERIC:
        plus = "+" if v.startswith("+") else ""
        d = _digits_only(v)
        if len(d) < 8:
            return v
        sizes = _split_sizes(len(d), rng)
        seps = [rng.choice(_SEPS) for _ in sizes[:-1]]
        if len(sizes) > 2 and len(set(seps)) == 1:
            seps[rng.randrange(len(seps))] = rng.choice([s for s in _SEPS if s != seps[0]])
        out, i = plus, 0
        for k, size in enumerate(sizes):
            out += d[i:i + size]
            i += size
            if k < len(seps):
                out += seps[k]
        return out
    if t == "DATE_OF_BIRTH":
        m = re.fullmatch(r"(\d{1,4})([/.-])(\d{1,2})([/.-])(\d{1,4})", v)
        if not m:
            return v
        a, s1, b, s2, c = m.groups()
        pool = [x for x in "/.-" if x != s1]
        return f"{a}{s1}{b}{rng.choice(pool)}{c}"
    return v


def _native_digits(v, t, rng):
    if t not in ("PHONE", "AADHAAR", "CARD", "BANK_ACCOUNT", "DATE_OF_BIRTH", "COMPENSATION", "EMPLOYEE_ID", "PAN"):
        return v
    table = _DIGITS[rng.choice(sorted(_DIGITS))]
    return "".join(table[int(c)] if c in "0123456789" else c for c in v)


_AT = [" at the rate ", " at the rate of ", "＠", "{at}", " <at> "]
_DOT = [" point ", "．", "{dot}", " <dot> "]


def _spoken_email(v, t, rng):
    if t != "EMAIL" or "@" not in v:
        return v
    local, domain = v.split("@", 1)
    k = rng.randrange(4)
    if k == 0:
        return f"{local} at the rate {' point '.join(domain.split('.'))}"
    if k == 1:
        return f"{local}＠{'．'.join(domain.split('.'))}"
    if k == 2:
        return f"{local}{{at}}{'{dot}'.join(domain.split('.'))}"
    return f"{local} <at> {' <dot> '.join(domain.split('.'))}"


def _leetspeak_name(v, t, rng):
    if t != "PERSON":
        return v
    idx = [i for i, c in enumerate(v) if c.lower() in _LEET]
    if not idx:
        return v
    chosen = {i for i in idx if rng.random() < 0.5} or {rng.choice(idx)}
    return "".join(rng.choice(_LEET[c.lower()]) if i in chosen else c for i, c in enumerate(v))


_WRAP_TYPES = ("PHONE", "AADHAAR", "CARD", "BANK_ACCOUNT", "PAN", "IFSC", "EMPLOYEE_ID", "SECRET", "EMAIL", "PERSON", "UPI_ID", "DATE_OF_BIRTH")


def _line_wrap(v, t, rng):
    if t not in _WRAP_TYPES or len(v) < 6:
        return v
    nl = rng.choice(["\n", "\n", "\r\n"]) + " " * rng.choice([0, 0, 2, 4])
    if t == "PERSON" and " " in v:
        pos = [m.start() for m in re.finditer(" ", v)]
        i = rng.choice(pos)
        return v[:i] + nl + v[i + 1:]
    if t == "PERSON":
        return v
    i = rng.randint(3, len(v) - 3)
    return v[:i] + nl + v[i:]


def _ocr_confusion(v, t, rng):
    if t not in ("PHONE", "AADHAAR", "CARD", "BANK_ACCOUNT", "PAN", "IFSC", "EMPLOYEE_ID"):
        return v
    idx = [i for i, c in enumerate(v) if c in _OCR and not (t in ("PHONE", "AADHAAR", "CARD", "BANK_ACCOUNT") and c not in "01258")]
    if not idx:
        return v
    chosen = {i for i in idx if rng.random() < 0.4} or {rng.choice(idx)}
    return "".join(rng.choice(_OCR[c]) if i in chosen else c for i, c in enumerate(v))


def _html_entity(v, t, rng):
    if t == "EMAIL":
        return v.replace("@", "&#64;").replace(".", "&#46;" if rng.random() < 0.7 else ".")
    if t == "PERSON":
        idx = [i for i, c in enumerate(v) if c.isalpha() and c.isascii()]
        if not idx:
            return v
        chosen = {i for i in idx if rng.random() < 0.25} or {rng.choice(idx)}
        return "".join((f"&#{ord(c)};" if rng.random() < 0.5 else f"&#x{ord(c):x};") if i in chosen else c for i, c in enumerate(v))
    if t == "COMPENSATION" and "₹" in v:
        return v.replace("₹", "&#8377;")
    if t == "PHONE" and v.startswith("+"):
        return "&#43;" + v[1:]
    return v


_REWRITERS = {"mixed_separators": _mixed_separators, "native_digits": _native_digits, "spoken_email": _spoken_email,
              "leetspeak_name": _leetspeak_name, "line_wrap": _line_wrap, "ocr_confusion": _ocr_confusion, "html_entity": _html_entity}
assert set(_REWRITERS) == set(KINDS)


def transform(item, kind, rng):
    """Return a new item with `kind` applied to every eligible entity; spans remapped by construction."""
    if kind not in _REWRITERS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
    fn = _REWRITERS[kind]
    text = item["text"]
    parts, spans, pos, edits, cur = [], [], 0, 0, 0
    for sp in sorted(item["spans"], key=lambda s: (s["start"], s["end"])):
        gap = text[pos:sp["start"]]
        parts.append(gap)
        cur += len(gap)
        old = text[sp["start"]:sp["end"]]
        new = fn(old, sp["type"], rng)
        edits += new != old
        parts.append(new)
        spans.append({"start": cur, "end": cur + len(new), "type": sp["type"]})
        cur += len(new)
        pos = sp["end"]
    parts.append(text[pos:])
    out = copy.deepcopy(item)
    out["id"] = f"{item['id']}~{kind}"
    out["text"] = "".join(parts)
    out["spans"] = spans
    out["adv"] = {"kind": kind, "edits": edits, "heldout": True}
    check_item(item, out, kind)
    return out


def applicable(item, kind):
    fn = _REWRITERS[kind]
    r = random.Random(0)
    return any(fn(item["text"][s["start"]:s["end"]], s["type"], r) != item["text"][s["start"]:s["end"]] for s in item["spans"])


def _canon(v, kind):
    if kind == "mixed_separators":
        return re.sub(r"\D", "", v)
    if kind == "native_digits":
        return "".join(str(unicodedata.decimal(c)) if c.isdigit() else c for c in v)
    if kind == "spoken_email":
        for tok in (" at the rate of ", " at the rate ", "＠", "{at}", " <at> "):
            v = v.replace(tok, "@")
        for tok in (" point ", "．", "{dot}", " <dot> "):
            v = v.replace(tok, ".")
        return v
    if kind == "leetspeak_name":
        return "".join(_LEET_BACK.get(c, c) for c in v).casefold().replace("l", "i")
    if kind == "line_wrap":
        return "".join(v.split())
    if kind == "ocr_confusion":
        return "".join(_OCR_FOLD.get(c, c) for c in v)
    if kind == "html_entity":
        return html.unescape(v)
    raise ValueError(kind)


def check_item(orig, new, kind):
    """Assert every span still covers the transformed entity and nothing outside spans changed."""
    ot, nt = orig["text"], new["text"]
    os_ = sorted(orig["spans"], key=lambda s: (s["start"], s["end"]))
    ns = new["spans"]
    assert len(os_) == len(ns), "span count changed"
    po = pn = 0
    for a, b in zip(os_, ns):
        assert a["type"] == b["type"], "span type changed"
        assert 0 <= b["start"] < b["end"] <= len(nt), "span out of bounds"
        assert b["start"] >= pn, "spans overlap / unordered"
        assert ot[po:a["start"]] == nt[pn:b["start"]], "text outside spans was modified"
        ent_new, ent_old = nt[b["start"]:b["end"]], ot[a["start"]:a["end"]]
        assert ent_new.strip() == ent_new and ent_new, "entity empty or has edge whitespace after transform"
        c_new = _canon(ent_new, kind)
        c_old = _canon(ent_old, kind)
        assert c_new == c_old, f"span no longer covers entity: {ent_old!r} -> {ent_new!r}"
        po, pn = a["end"], b["end"]
    assert ot[po:] == nt[pn:], "trailing text modified"


def self_check(verbose=False):
    text = ("Reach Rahul Sharma at rahul.sharma@acme.in or +91 98765 43210. Aadhaar 2345 6789 0123, card 4111 1111 1111 1111, "
            "a/c 123456789012, PAN ABCPS1234K, IFSC HDFC0001234, emp EMP-20418, DOB 07/03/1991, pay INR 38 lakh (₹18,40,000). "
            "Token ghp_abcdefghijklmnopqrstuvwxyz0123456789 and UPI rahul@okhdfcbank.")
    spec = [("Rahul Sharma", "PERSON"), ("rahul.sharma@acme.in", "EMAIL"), ("+91 98765 43210", "PHONE"), ("2345 6789 0123", "AADHAAR"),
            ("4111 1111 1111 1111", "CARD"), ("123456789012", "BANK_ACCOUNT"), ("ABCPS1234K", "PAN"), ("HDFC0001234", "IFSC"),
            ("EMP-20418", "EMPLOYEE_ID"), ("07/03/1991", "DATE_OF_BIRTH"), ("INR 38 lakh", "COMPENSATION"), ("₹18,40,000", "COMPENSATION"),
            ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "SECRET"), ("rahul@okhdfcbank", "UPI_ID")]
    spans, pos = [], 0
    for v, t in spec:
        s = text.index(v, pos)          # fixture only: hand-written sentence above
        spans.append({"start": s, "end": s + len(v), "type": t})
        pos = s + len(v)
    item = {"id": "selfcheck-1", "slice": "hr_free_text", "template_id": "selfcheck", "name_source": "roster", "text": text, "spans": spans}
    for kind in KINDS:
        for seed in range(25):
            out = transform(item, kind, random.Random(seed))
            assert out["adv"]["edits"] > 0, f"{kind} made no edit"
    if verbose:
        print("held-out adversarial self-check ok:", ", ".join(KINDS))
    return True


if __name__ == "__main__":
    self_check(verbose=True)
    sys.exit(0)
