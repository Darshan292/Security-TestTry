"""Adversarial transforms whose mechanism matches an earlier (v1/v2) kind."""
import re

try:
    from ._util import Drop, assemble, make_entry, rewrite_chars, span_texts, choose_positions
except ImportError:  # run as a script from this directory
    from _util import Drop, assemble, make_entry, rewrite_chars, span_texts, choose_positions

LETTER_TYPES = {"PERSON", "EMAIL", "PAN", "IFSC", "UPI_ID", "EMPLOYEE_ID", "SECRET"}
DIGIT_TYPES = {"AADHAAR", "PHONE", "BANK_ACCOUNT", "CARD", "COMPENSATION", "DATE_OF_BIRTH", "EMPLOYEE_ID", "OOT"}

# ------------------------------------------------------------------ homoglyph (deliberate)
_HOMO = {"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х",
         "y": "у", "i": "і", "s": "ѕ", "j": "ј",
         "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К",
         "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "Ү"}


def _homoglyph(item, rng):
    new = []
    for (s, t) in zip(item["spans"], span_texts(item)):
        if s["type"] in LETTER_TYPES:
            idx = [i for i, c in enumerate(t) if c in _HOMO]
            if idx:
                chosen = {i for i in idx if rng.random() < 0.5} or {rng.choice(idx)}
                t = "".join(_HOMO[c] if i in chosen else c for i, c in enumerate(t))
        new.append(t)
    return assemble(item, "homoglyph_swap", new)


# ------------------------------------------------------------------ zero-width insertion (deliberate)
_ZW = ["​", "‌", "‍", "⁠", "﻿"]


def _zero_width(item, rng):
    ins = {}
    for s in item["spans"]:
        n = s["end"] - s["start"]
        for p in choose_positions(rng, n, rng.randint(1, 3)):
            ins[s["start"] + p] = rng.choice(_ZW)
    return rewrite_chars(item, "zw_insert", [c for c in item["text"]], ins)


# ------------------------------------------------------------------ PDF per-glyph gaps
def _pdf_kern_spaces(item, rng):
    new = []
    for t in span_texts(item):
        out = []
        p = rng.choice([0.35, 0.6, 1.0])
        for i, c in enumerate(t):
            out.append(c)
            if i < len(t) - 1 and c != " " and t[i + 1] != " " and rng.random() < p:
                out.append(" ")
        s = "".join(out)
        if s == t and len(t) > 1:
            j = rng.randrange(1, len(t))
            s = t[:j] + " " + t[j:] if t[j - 1] != " " and t[j] != " " else t
        new.append(s)
    return assemble(item, "pdf_glyph_gaps", new)


# ------------------------------------------------------------------ OCR confusion (+ dropout)
_OCR1 = {"0": "O", "1": "l", "5": "S", "8": "B", "2": "Z", "6": "b", "4": "A",
         "O": "0", "I": "1", "l": "1", "S": "5", "B": "8", "Z": "2", "G": "6", "q": "9", "o": "0"}
_OCRN = {"m": "rn", "w": "vv", "d": "cl", "h": "li", "@": "a", "H": "11"}


def _ocr(item, rng):
    new = []
    for t in span_texts(item):
        out, hit = [], False
        for c in t:
            r = rng.random()
            if c in _OCR1 and r < 0.2:
                out.append(_OCR1[c]); hit = True
            elif c in _OCRN and r < 0.15:
                out.append(_OCRN[c]); hit = True
            else:
                out.append(c)
        s = "".join(out)
        if not hit:
            cand = [i for i, c in enumerate(t) if c in _OCR1]
            if cand:
                i = rng.choice(cand)
                s = t[:i] + _OCR1[t[i]] + t[i + 1:]
        if len(t) >= 6 and rng.random() < 0.07:  # glyph lost to noise / crease: unrecoverable
            i = rng.randrange(1, len(s) - 1)
            new.append(Drop(s[:i] + s[i + 1:]))
        else:
            new.append(s)
    return assemble(item, "scan_glyph_errors", new)


# ------------------------------------------------------------------ HTML numeric entities
def _html_entity(item, rng):
    new = []
    for t in span_texts(item):
        mode = rng.choice(["dec", "hex", "mixed"])
        p = rng.choice([0.3, 0.6, 1.0])
        out = []
        for c in t:
            if (c in "@.-_+" and rng.random() < 0.8) or (rng.random() < p and c != " "):
                m = mode if mode != "mixed" else rng.choice(["dec", "hex"])
                out.append("&#%d;" % ord(c) if m == "dec" else "&#x%X;" % ord(c))
            else:
                out.append(c)
        new.append("".join(out))
    return assemble(item, "markup_entities", new)


# ------------------------------------------------------------------ hard wrap at terminal width
def _line_wrap(item, rng):
    text = item["text"]
    width = rng.choice([40, 48, 60, 72, 80])
    nl = rng.choice(["\n", "\n", "\r\n"])
    ins, col = {}, 0
    for i, c in enumerate(text):
        if c == "\n":
            col = 0
            continue
        if c == "\r":
            continue
        if col >= width:
            ins[i] = nl
            col = 0
        col += 1
    if not ins:
        return None
    return rewrite_chars(item, "terminal_wrap", [c for c in text], ins)


# ------------------------------------------------------------------ case games
def _alt(t):
    return "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(t))


def _case(item, rng):
    new = []
    for s, t in zip(item["spans"], span_texts(item)):
        ty = s["type"]
        if ty == "PERSON":
            t = rng.choice([t.upper(), t.lower(), _alt(t)])
        elif ty in ("EMAIL", "UPI_ID"):
            t = rng.choice([t.upper(), _alt(t)])
        elif ty in ("PAN", "IFSC", "EMPLOYEE_ID"):
            t = t.lower()
        new.append(t)
    return assemble(item, "case_games", new)


# ------------------------------------------------------------------ non-ASCII digit scripts
_DIGIT_BLOCKS = [0x0966, 0x0660, 0x06F0, 0x09E6, 0x0BE6, 0x0AE6, 0x0A66]


def _native_digits(item, rng):
    base = rng.choice(_DIGIT_BLOCKS)
    new = []
    for s, t in zip(item["spans"], span_texts(item)):
        if s["type"] in DIGIT_TYPES:
            t = "".join(chr(base + int(c)) if c in "0123456789" else c for c in t)
        new.append(t)
    return assemble(item, "digit_script", new)


# ------------------------------------------------------------------ unicode space variants
_SP = [" ", " ", " ", " ", " ", "　"]


def _nbsp(item, rng):
    sp = rng.choice(_SP)
    new = [t.replace(" ", sp) if rng.random() < 0.9 else t.replace(" ", rng.choice(_SP)) for t in span_texts(item)]
    return assemble(item, "unicode_spaces", new)


# ------------------------------------------------------------------ speech-to-text of spoken values
_DW = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six",
       "7": "seven", "8": "eight", "9": "nine"}
_MULT = {2: "double", 3: "triple"}


def _say_digits(d, rng, group=True):
    out, i = [], 0
    zero = rng.choice(["zero", "oh"])
    while i < len(d):
        j = i
        while j < len(d) and d[j] == d[i]:
            j += 1
        run = j - i
        w = zero if d[i] == "0" else _DW[d[i]]
        if group and run in _MULT and rng.random() < 0.7:
            out.append(_MULT[run] + " " + w)
        else:
            out.extend([w] * run)
        i = j
    return out


def _say_alnum(v, rng):
    out = []
    for c in v:
        if c.isdigit():
            out.extend(_say_digits(c, rng, False))
        elif c.isalpha():
            out.append(c.lower() if rng.random() < 0.5 else c.upper())
        else:
            out.append({"@": "at", ".": "dot", "_": "underscore", "-": "dash", "+": "plus", "/": "slash"}.get(c, c))
    return out


def _speech(item, rng):
    new = []
    for s, t in zip(item["spans"], span_texts(item)):
        ty = s["type"]
        if ty == "EMAIL":
            t = " ".join(_say_alnum(t, rng))
        elif ty in ("PHONE", "AADHAAR", "BANK_ACCOUNT", "CARD"):
            parts = []
            for ch in re.findall(r"\+|\d+", t):
                parts.extend(["plus"] if ch == "+" else _say_digits(ch, rng))
            t = " ".join(parts)
        elif ty in ("PAN", "IFSC"):
            t = " ".join(_say_alnum(t, rng))
        new.append(t)
    return assemble(item, "stt_dictation", new)


KINDS = {
    "homoglyph_swap": make_entry("homoglyph_swap", _homoglyph, "known",
                                 "Deliberate: insider swaps Latin letters for look-alike Cyrillic letters.", "value"),
    "zw_insert": make_entry("zw_insert", _zero_width, "known",
                            "Deliberate: invisible zero-width characters spliced into values.", "value"),
    "pdf_glyph_gaps": make_entry("pdf_glyph_gaps", _pdf_kern_spaces, "known",
                                 "PDF extraction: per-glyph positioning yields spaces inside words and numbers.", "value"),
    "scan_glyph_errors": make_entry("scan_glyph_errors", _ocr, "known",
                                    "OCR of a scan: look-alike character confusions, occasional lost glyph.", "value"),
    "markup_entities": make_entry("markup_entities", _html_entity, "known",
                                  "HTML mail/web export: characters written as numeric character references.", "value"),
    "terminal_wrap": make_entry("terminal_wrap", _line_wrap, "known",
                                "Terminal/console capture hard-wraps lines at a fixed column.", "both"),
    "case_games": make_entry("case_games", _case, "known",
                             "Case changes: upper-cased name, lower-cased ID, alternating case.", "value"),
    "digit_script": make_entry("digit_script", _native_digits, "known",
                               "Locale/IME output: ASCII digits rendered as Indic or Arabic-Indic digits.", "value"),
    "unicode_spaces": make_entry("unicode_spaces", _nbsp, "known",
                                 "Word processor / web copy: ordinary spaces become non-breaking or exotic spaces.", "value"),
    "stt_dictation": make_entry("stt_dictation", _speech, "known",
                                "Speech-to-text transcript of a value read aloud (words for digits, 'at', 'dot').", "value"),
}
