"""Adversarial transformations for E-ADV (EVALUATION_PLAN.md).

``transform(item, kind, rng) -> item`` rewrites the entity text *inside* the gold spans
and rebuilds the string from (untouched gap text + new entity text).  Because the new
string is assembled piecewise, every span is remapped by construction and never located
by searching.  Text between spans is never modified.

Kinds: homoglyph, zero_width, spaced, obfuscated_email, case, nbsp.

``check_item`` / ``self_check`` assert that after a transform every span still covers the
transformed entity (its canonical form equals the canonical form of the original entity).
"""
from __future__ import annotations

import copy
import random
import sys

KINDS = ("homoglyph", "zero_width", "spaced", "obfuscated_email", "case", "nbsp")

ZW = ("​", "‍")
NBSP = " "

_CYR = {"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у",
        "i": "і", "s": "ѕ", "j": "ј",
        "A": "А", "B": "В", "E": "Е", "H": "Н", "K": "К", "M": "М", "O": "О",
        "P": "Р", "C": "С", "T": "Т", "X": "Х"}
_GRK = {"o": "ο", "v": "ν", "i": "ι", "u": "υ", "a": "α",
        "A": "Α", "B": "Β", "E": "Ε", "H": "Η", "I": "Ι", "K": "Κ", "M": "Μ",
        "N": "Ν", "O": "Ο", "P": "Ρ", "T": "Τ", "X": "Χ", "Y": "Υ", "Z": "Ζ"}
_FOLD = {v: k for k, v in list(_CYR.items()) + list(_GRK.items())}

_AT = ("[at]", "(at)", "at")
_DOT = ("[dot]", "(dot)", "dot")
_NUM_TYPES = ("PHONE", "AADHAAR", "CARD", "BANK_ACCOUNT", "COMPENSATION")


# ---- per-entity rewriters: return new entity text (may equal the old one)
def _homoglyph(v, t, rng):
    if t not in ("PERSON", "EMAIL"):
        return v
    table = _CYR if rng.random() < 0.7 else _GRK
    idx = [i for i, c in enumerate(v) if c in table]
    if not idx:
        return v
    chosen = {i for i in idx if rng.random() < 0.5} or {rng.choice(idx)}
    return "".join(table[c] if i in chosen else c for i, c in enumerate(v))


def _zero_width(v, t, rng):
    if len(v) < 2:
        return v
    n = 1 if len(v) < 6 else rng.randint(1, 2)
    pos = sorted(rng.sample(range(1, len(v)), min(n, len(v) - 1)), reverse=True)
    for p in pos:
        v = v[:p] + rng.choice(ZW) + v[p:]
    return v


def _spaced(v, t, rng):
    if t != "PERSON":
        return v
    return "   ".join(" ".join(w) for w in v.split(" "))


def _obfuscated_email(v, t, rng):
    if t != "EMAIL" or "@" not in v:
        return v
    local, domain = v.split("@", 1)
    at, dot = rng.choice(_AT), rng.choice(_DOT)
    return f"{local} {at} " + f" {dot} ".join(domain.split("."))


def _case(v, t, rng):
    if t != "PERSON":
        return v
    return v.lower() if rng.random() < 0.6 else v.upper()


def _group(digits, sizes):
    out, i = [], 0
    for n in sizes:
        out.append(digits[i:i + n])
        i += n
    if i < len(digits):
        out.append(digits[i:])
    return NBSP.join(x for x in out if x)


def _nbsp(v, t, rng):
    if t not in _NUM_TYPES:
        return v
    if t == "COMPENSATION":
        if " " in v:
            return v.replace(" ", NBSP)
        if "," in v:
            return v.replace(",", NBSP)
        return v
    if " " in v or "-" in v:
        return v.replace(" ", NBSP).replace("-", NBSP)
    if not v.isdigit():
        return v
    if t == "PHONE":
        return _group(v, [5, 5] if len(v) == 10 else [4] * (len(v) // 4))
    if t == "AADHAAR":
        return _group(v, [4, 4, 4])
    return _group(v, [4] * (len(v) // 4))


_REWRITERS = {"homoglyph": _homoglyph, "zero_width": _zero_width, "spaced": _spaced,
              "obfuscated_email": _obfuscated_email, "case": _case, "nbsp": _nbsp}


# ---- public API
def transform(item, kind, rng):
    """Return a new item with `kind` applied to every eligible entity; spans remapped."""
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
    out["adv"] = {"kind": kind, "edits": edits}
    check_item(item, out, kind)
    return out


def applicable(item, kind):
    """True if `kind` would change at least one entity of `item` (uses a throw-away RNG)."""
    fn = _REWRITERS[kind]
    r = random.Random(0)
    return any(fn(item["text"][s["start"]:s["end"]], s["type"], r) != item["text"][s["start"]:s["end"]] for s in item["spans"])


def _canon(v, kind):
    v = "".join(_FOLD.get(c, c) for c in v)
    v = v.replace("​", "").replace("‍", "").replace(NBSP, " ")
    if kind == "obfuscated_email":
        for tok in ("[at]", "(at)", " at "):
            v = v.replace(tok, "@")
        for tok in ("[dot]", "(dot)", " dot "):
            v = v.replace(tok, ".")
    if kind == "nbsp":          # digit-group separators (space, hyphen, comma) are interchangeable
        v = v.replace(",", "").replace("-", "")
    return "".join(v.split()).casefold()


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
        ent_new = nt[b["start"]:b["end"]]
        assert ent_new.strip(), "empty entity after transform"
        assert _canon(ent_new, kind) == _canon(ot[a["start"]:a["end"]], kind), \
            f"span no longer covers entity: {ot[a['start']:a['end']]!r} -> {ent_new!r}"
        po, pn = a["end"], b["end"]
    assert ot[po:] == nt[pn:], "trailing text modified"


def self_check(verbose=False):
    """Exercise every kind on a hand-built item covering all types; raises AssertionError on failure."""
    text = ("Dear Rahul Sharma, mail rahul.sharma@acme.in or call +91 98765 43210. Aadhaar 2345 6789 0123, "
            "card 4111111111111111, a/c 123456789012, CTC INR 38 lakh (₹18,40,000). PAN ABCPS1234K; "
            "reach R. Sharma on 9876543210. Token Bearer abc123")
    spec = [("Rahul Sharma", "PERSON"), ("rahul.sharma@acme.in", "EMAIL"), ("+91 98765 43210", "PHONE"),
            ("2345 6789 0123", "AADHAAR"), ("4111111111111111", "CARD"), ("123456789012", "BANK_ACCOUNT"),
            ("INR 38 lakh", "COMPENSATION"), ("₹18,40,000", "COMPENSATION"), ("ABCPS1234K", "PAN"),
            ("R. Sharma", "PERSON"), ("9876543210", "PHONE"), ("abc123", "SECRET")]
    spans, pos = [], 0
    for v, t in spec:
        s = text.index(v, pos)          # test fixture only: hand-written sentence above
        spans.append({"start": s, "end": s + len(v), "type": t})
        pos = s + len(v)
    item = {"id": "selfcheck-1", "slice": "hr_free_text", "template_id": "selfcheck", "name_source": "roster",
            "text": text, "spans": spans}
    for kind in KINDS:
        for seed in range(25):
            out = transform(item, kind, random.Random(seed))
            assert out["adv"]["edits"] > 0, f"{kind} made no edit"
    if verbose:
        print("adversarial self-check ok:", ", ".join(KINDS))
    return True


if __name__ == "__main__":
    self_check(verbose=True)
    sys.exit(0)
