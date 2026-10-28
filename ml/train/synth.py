"""Template-and-slot synthesis: labels come from code, never from an LLM.

An LLM writes TEMPLATES: realistic document text in which every personal value is a typed slot such as
{{PERSON_1}}, {{AADHAAR_1}}, {{COMPENSATION_2}}. Python validates the template, fills each slot with a generated value
(values.py) and records the exact character offsets. The same slot index inside one document is the same value
(`{{PERSON_1}}` twice = the same person); `{{PERSON_1:first}}` renders that person's first name only.

The LLM may also write free text containing hard negatives and may use negative slots ({{NEG_TICKET_1}}, {{NEG_PLACE_1}},
...) that are filled with look-alike values and carry NO span.

Slot grammar:   {{TYPE_N}}  or  {{TYPE_N:modifier}}     TYPE in ENTITY_TYPES or NEG_<KIND>, N in 1..99.
"""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass

from . import structured, values as V
from .genres import BY_NAME, GENRES, LANG_MIX_NOTES, LENGTHS, Controls, GenreSpec
from .io import DocBuilder, make_record
from .labels import ENTITY_TYPES

SLOT_RE = re.compile(r"\{\{\s*([A-Z][A-Z_]*?)_(\d{1,2})(?::([a-z_]+))?\s*\}\}")
NEG_PREFIX = "NEG_"
NEG_KINDS = tuple(sorted(V.NEGATIVE_GENERATORS))
SLOT_BASES = frozenset(ENTITY_TYPES) | {NEG_PREFIX + k for k in NEG_KINDS}

_PERSON_MODS = {"first", "last", "initial", "caps", "lower", "full", "lastfirst", "initial_last", "first_initial"}
_MODIFIERS: dict[str, frozenset] = {
    "PERSON": frozenset(_PERSON_MODS),
    "PHONE": frozenset({"plain", "plus91", "plus91_space", "plus91_hyphen", "plus91_nosp", "zero", "split5", "paren", "landline"}),
    "AADHAAR": frozenset({"plain", "spaced", "hyphen", "dot"}),
    "CARD": frozenset({"plain", "spaced", "hyphen"}),
    "BANK_ACCOUNT": frozenset({"plain", "spaced"}),
    "DATE_OF_BIRTH": frozenset(V.DATE_STYLES),
    "COMPENSATION": frozenset({"inr_symbol", "rs_dot", "rs_plain", "inr_code", "slash", "lakh_L", "lakhs", "lpa", "words", "k",
                               "plain_group", "rs_slash", "western", "crore", "words_rupees", "decimal_paise", "monthly", "annual"}),
}


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Slot:
    base: str
    idx: int
    mod: str | None
    start: int
    end: int

    @property
    def key(self) -> tuple[str, int]:
        return (self.base, self.idx)


def parse_slots(template: str) -> list[Slot]:
    """All slots of a template. Raises TemplateError for unknown slot names, unknown modifiers, or unbalanced braces."""
    slots = []
    for m in SLOT_RE.finditer(template):
        base, idx, mod = m.group(1), int(m.group(2)), m.group(3)
        if base not in SLOT_BASES:
            raise TemplateError(f"unknown slot type {base!r}")
        if idx < 1:
            raise TemplateError("slot indices start at 1")
        if mod is not None and mod not in _MODIFIERS.get(base, frozenset()):
            raise TemplateError(f"unknown modifier {mod!r} for {base}")
        slots.append(Slot(base, idx, mod, m.start(), m.end()))
    rest = SLOT_RE.sub("\x00", template)
    if "{{" in rest or rest.count("}") > rest.count("{"):
        raise TemplateError("unbalanced or malformed slot braces")
    if re.search(r"\[[A-Z][A-Z_]*_\d+\]|<[A-Z][A-Z_]{3,}(?:_\d+)?>", rest):
        raise TemplateError("placeholder in the wrong syntax (use {{TYPE_N}})")
    return slots


# ----------------------------------------------------------------------------------- unslotted PII detection

_ROLE_LOCALS = set(V.ROLE_MAIL_LOCAL)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\d.])(?:(?:\+|00)?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_DIGITRUN = re.compile(r"(?<![\d.,])\d(?:[ -]?\d){8,18}(?![\d,])")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
_UPI = re.compile(r"\b[\w.-]{3,}@(?:ok\w+|ybl|ibl|axl|paytm|upi|apl|sbi|icici|hdfcbank|pnb|fbl|ikwik|airtel)\b")
_SECRET = re.compile(r"\bAKIA[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9]{16,}|\bghp_\w{20,}|\bxox[bp]-[\w-]{10,}|\beyJ[\w-]{10,}\.|\bAIza[\w-]{30,}"
                     r"|\b[0-9a-f]{32,}\b")
_AMOUNT = re.compile(r"(?:₹|\bRs\.?|\bINR)\s?\d[\d,]*(?:\.\d+)?(?:/-)?|\b\d[\d,]*(?:\.\d+)?\s?(?:LPA|lpa|lakhs?|crores?|L)\b|\b\d+/-")
_STATUTORY_CUE = re.compile(r"section|limit|ceiling|cap\b|exemption|deduction|slab|rebate|threshold|maximum|up to|upto|invoice|"
                            r"standard|80[cd]|bill|fee|fine|penalty|price|cost|budget|premium", re.I)
_DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]{2,8},?\s+\d{4}\b")
_BIRTH_CUE = re.compile(r"d\.?o\.?b|birth|born|janm|janmatithi", re.I)
_NAME_TOKEN = re.compile(r"\b[A-Z][a-zA-Z'’]+\b")

COMMON_WORD_ALLOW = frozenset({"Grace", "More", "Das", "Sen", "Roy", "Pal", "Ali", "Mark", "Thomas", "George", "Joseph", "Paul",
                               "Philip", "Simon", "Engineer", "Contractor", "Mistry", "Dey", "Bose", "Basu", "Shah", "Singh",
                               "Kaur", "Khan", "Rani", "Raja", "Mala", "Sagar", "Deepak", "Kamal", "Surya", "Ganga", "Jyoti",
                               "Madhu", "Usha", "Radha", "Rao", "Reddy", "Patel", "Menon", "Nair", "Iyer", "Bhat"})
_NAME_LEXICON: frozenset[str] = frozenset(
    [n for lst in V._FIRST.values() for n, _ in lst] + [s for lst in V._SURNAMES.values() for s in lst])


def unslotted_pii(template: str) -> list[str]:
    """Reasons why free text outside the slots contains a PII-shaped string, or a known person name. Known hard
    negatives (statutory limits, helplines, role mailboxes, names in AMBIGUOUS_WORDS) are not reported. Simple regex
    checks only; synth.validate_template can additionally run the repo's detector."""
    text = SLOT_RE.sub("\x00", template)
    why: list[str] = []

    def hit(kind: str, m: re.Match) -> None:
        why.append(f"{kind}: {m.group()[:40]!r}")

    for m in _EMAIL.finditer(text):
        if m.group().split("@")[0].lower() not in _ROLE_LOCALS and not _UPI.fullmatch(m.group()):
            hit("email", m)
    for m in _UPI.finditer(text):
        hit("upi", m)
    for m in _PHONE.finditer(text):
        hit("phone", m)
    for m in _DIGITRUN.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 9 <= len(digits) <= 19 and not _PHONE.fullmatch(m.group()) and not re.match(r"1800|1860", digits):
            hit("digit-run", m)
    for rx, kind in ((_PAN, "pan"), (_IFSC, "ifsc"), (_SECRET, "secret")):
        for m in rx.finditer(text):
            hit(kind, m)
    for m in _AMOUNT.finditer(text):
        if not _STATUTORY_CUE.search(text[max(0, m.start() - 60):m.end() + 40]):
            hit("amount", m)
    for m in _DATE.finditer(text):
        if _BIRTH_CUE.search(text[max(0, m.start() - 30):m.start()]):
            hit("dob", m)
    for m in _NAME_TOKEN.finditer(text):
        w = m.group()
        if w in _NAME_LEXICON and w not in V.AMBIGUOUS_WORDS and w not in COMMON_WORD_ALLOW:
            hit("name", m)
    return why


def validate_template(template: str, spec: GenreSpec | None = None, detector=None) -> tuple[bool, str]:
    """(ok, reason). `detector`, if given, is a callable text -> bool that returns True when the repo's detector finds
    a sensitive entity in the unslotted text (the local script wires pgw.detect here; Kaggle runs without it)."""
    try:
        slots = parse_slots(template)
    except TemplateError as e:
        return False, str(e)
    lo = (spec.min_chars if spec else 80) // 2
    hi = (spec.max_chars if spec else 6000) * 2
    if not lo <= len(template) <= hi:
        return False, f"length {len(template)} outside [{lo}, {hi}]"
    entity_slots = [s for s in slots if not s.base.startswith(NEG_PREFIX)]
    if not slots and not (spec and spec.allow_no_slots):
        return False, "no slots"
    if spec and spec.name == "policy_faq_negative" and entity_slots:
        return False, "negative genre must not contain entity slots"
    if spec and not spec.allow_no_slots and not entity_slots:
        return False, "no entity slots"
    why = unslotted_pii(template)
    if why:
        return False, "unslotted PII-shaped text: " + "; ".join(why[:3])
    if detector is not None and detector(SLOT_RE.sub(" ", template)):
        return False, "repo detector found an entity in unslotted text"
    return True, "ok"


# ----------------------------------------------------------------------------------------------- filling

def derive_rng(seed: int, *parts) -> random.Random:
    h = hashlib.sha256(("|".join(str(p) for p in (seed, *parts))).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))  # noqa: S311 (synthetic data, not cryptography)


class _Entities:
    """Per-document value store: one value per (slot type, index), coherent across types with the same index."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.people: dict[int, V.Person] = {}
        self.values: dict[tuple[str, int, str | None], str] = {}
        self.styles: dict[int, str] = {}

    def person(self, idx: int) -> V.Person:
        if idx not in self.people:
            self.people[idx] = V.person(self.rng)
        return self.people[idx]

    def value(self, base: str, idx: int, mod: str | None) -> str:
        if base == "PERSON":
            p = self.person(idx)
            if mod is None:
                if idx not in self.styles:
                    self.styles[idx] = self.rng.choice(("full", "full", "full", "last_first", "initial_last", "caps", "first_only",
                                                        "lower", "first_initial"))
                style = self.styles[idx]
            else:
                style = {"first": "first_only", "last": "last", "initial": "initial", "caps": "caps", "lower": "lower",
                         "full": "full", "lastfirst": "last_first", "initial_last": "initial_last",
                         "first_initial": "first_initial"}[mod]
            return V.name_surface(self.rng, p, style)
        if base.startswith(NEG_PREFIX):  # negatives re-roll per occurrence unless same index (kept stable)
            key = (base, idx, None)
            if key not in self.values:
                self.values[key] = V.NEGATIVE_GENERATORS[base[len(NEG_PREFIX):]](self.rng)
            return self.values[key]
        key = (base, idx, mod)
        if key in self.values:
            return self.values[key]
        v = self._make(base, idx, mod)
        self.values[key] = v
        return v

    def _make(self, base: str, idx: int, mod: str | None) -> str:
        r = self.rng
        p = self.person(idx) if (base in ("EMAIL", "PAN", "UPI_ID") and idx in self.people) else None
        if base == "EMAIL":
            return V.email(r, p)
        if base == "PHONE":
            return V.phone(r, mod)
        if base == "EMPLOYEE_ID":
            return V.employee_id(r)
        if base == "COMPENSATION":
            if mod in ("monthly", "annual"):
                return V.compensation(r, kind=mod)
            return V.compensation(r, style=mod)
        if base == "DATE_OF_BIRTH":
            return V.date_of_birth(r, mod)
        if base == "AADHAAR":
            return V.aadhaar(r, mod)
        if base == "PAN":
            return V.pan(r, p.last if p else None)
        if base == "BANK_ACCOUNT":
            return V.bank_account(r, fmt=mod)
        if base == "CARD":
            return V.card(r, mod)
        if base == "UPI_ID":
            ph = self.values.get(("PHONE", idx, None))
            return V.upi_id(r, p, ph)
        if base == "SECRET":
            return V.secret(r)
        if base == "IFSC":
            return V.ifsc(r)
        if base == "IDENTIFIER":
            return V.identifier(r)
        raise TemplateError(f"no generator for {base}")


def fill_template(template: str, rng: random.Random) -> tuple[str, list[dict]]:
    """Fill every slot; returns (text, spans) with exact offsets: text[start:end] is the slot's value."""
    slots = parse_slots(template)
    ents = _Entities(rng)
    for s in sorted(slots, key=lambda s: (s.base != "PERSON", s.base != "PHONE", s.start)):  # people and phones first (coherence)
        ents.value(s.base, s.idx, s.mod)
    b = DocBuilder()
    pos = 0
    for s in slots:
        b.lit(template[pos:s.start])
        b.val(ents.value(s.base, s.idx, s.mod), None if s.base.startswith(NEG_PREFIX) else s.base)
        pos = s.end
    b.lit(template[pos:])
    return b.text, b.spans


def fill_records(templates, per_template: int, seed: int, generator: str | None = None) -> list[dict]:
    """Template dicts ({"id","genre","lang","template", ...}) -> labelled records, `per_template` fills each."""
    out = []
    for t in templates:
        gen = generator or "llm:" + str(t.get("model", "unknown")) + "+slots"
        for k in range(per_template):
            text, spans = fill_template(t["template"], derive_rng(seed, t["id"], k))
            out.append(make_record(f"{t['id']}-{k}", t["genre"], t.get("lang", "en"), text, spans, gen, template_id=t["id"]))
    return out


def split_templates(template_ids, dev_fraction: float = 0.1, seed: int = 0) -> tuple[set[str], set[str]]:
    """Deterministic split BY TEMPLATE: every record of a template goes to the same side, so near-duplicate fills of one
    template can never straddle train and dev."""
    ids = sorted(set(template_ids))
    dev = {i for i in ids if int.from_bytes(hashlib.sha256(f"{seed}|{i}".encode()).digest()[:4], "big") / 2**32 < dev_fraction}
    if not dev and len(ids) >= 2:
        dev = {ids[0]}
    return set(ids) - dev, dev


# --------------------------------------------------------------------------------------- programmatic path

def programmatic_records(n: int, seed: int, genres: tuple[str, ...] | None = None) -> list[dict]:
    """No-LLM generator for the structured genres (those whose GenreSpec has `program`). Spans come from the builders."""
    names = [g.name for g in GENRES if g.program and (genres is None or g.name in genres)]
    if not names:
        raise ValueError("no programmatic genres selected")
    out = []
    for i in range(n):
        rng = derive_rng(seed, "prog", i)
        g = BY_NAME[rng.choice(names)]
        lang = rng.choice(g.langs)
        text, spans = structured.PROGRAMS[g.program](rng, lang)
        out.append(make_record(f"prog{seed}-{i:06d}", g.name, lang, text, spans, f"programmatic:{g.program}", program_id=f"{g.program}-{i}"))
    return out


# --------------------------------------------------------------------------------------------- prompting

SLOT_HELP = {
    "PERSON": "a person's name (employee, manager, family member, referee)",
    "EMAIL": "a personal or work email address of a person",
    "PHONE": "a person's phone number (any Indian format)",
    "EMPLOYEE_ID": "an employee id / code",
    "COMPENSATION": "a salary, CTC, bonus, deduction or any pay amount belonging to a person, with its currency marker",
    "DATE_OF_BIRTH": "a date of birth (ONLY births; other dates are written as ordinary text)",
    "AADHAAR": "a 12-digit Aadhaar number",
    "PAN": "a PAN card number",
    "BANK_ACCOUNT": "a bank account number",
    "CARD": "a credit/debit card number",
    "UPI_ID": "a UPI handle such as name@bank",
    "SECRET": "a password, API key or token (just the secret value)",
    "IFSC": "a bank IFSC code",
    "IDENTIFIER": "a passport / driving licence / voter id / UAN / insurance policy number",
}
NEG_HELP = {
    "TICKET": "a short helpdesk ticket/case number", "STATUTORY": "a statutory limit phrase (e.g. Section 80C limit)",
    "INVOICE": "an invoice total", "EXT": "a desk extension / floor code", "HELPLINE": "a public helpline number",
    "DATE": "a non-birth date (FY, joining date, deadline)", "PIN": "a postal PIN code", "COUNT": "a count/percentage/grade/version",
    "ROLE_EMAIL": "a role mailbox such as hr@company", "PLACE": "an Indian city or town", "NAMEDPLACE": "a road/colony named after a person",
    "ORG": "an organisation whose name looks like a person's", "NAMEWORD": "a common word or festival that is also a given name",
}


def slot_help() -> str:
    lines = ["Entity slots (use {{TYPE_N}}; the same N is the same value; {{PERSON_1:first}} is that person's first name only):"]
    lines += [f"  {{{{{t}_N}}}}  {SLOT_HELP[t]}" for t in ENTITY_TYPES]
    lines.append("Negative slots (look-alikes that are NOT personal data, filled automatically and never labelled):")
    lines += [f"  {{{{NEG_{k}_N}}}}  {NEG_HELP[k]}" for k in NEG_KINDS]
    return "\n".join(lines)


TEMPLATE_DELIM = "====="


def build_messages(spec: GenreSpec, ctl: Controls, n: int = 3) -> list[dict]:
    lo, hi = LENGTHS[ctl.length]
    sys = ("You write realistic but entirely fictional document templates for testing a privacy filter at an Indian company's "
           "HR helpdesk. You never write real personal data. Every personal value is replaced by a typed slot.")
    user = f"""Write {n} different templates of this document type: {spec.name.replace('_', ' ')}.
Description: {spec.description}
Typical slots: {', '.join(spec.typical_slots) or 'none'}. Layout: {spec.layout}. {spec.notes}
Voice: {ctl.persona}; tone: {ctl.tone}. {LANG_MIX_NOTES.get(ctl.lang, LANG_MIX_NOTES['en'])}
Length of each template: about {lo}-{hi} characters. Keep line breaks and alignment exactly as the real document would have them.

{slot_help()}

Rules:
1. Never type a real-looking name, phone, email, id, account, card, PAN, Aadhaar, password or salary yourself: ALWAYS use a slot.
2. Use several different slots per template, repeat an index when the same person/value appears again, and give kin their own PERSON index.
3. You may use these negative slots or write them as plain text: {', '.join(ctl.extra_negatives)}. Dates that are not births, statutory limits and helplines stay ordinary text or NEG slots.
4. Do not wrap slots in quotes or backticks, do not explain, do not number the templates.
Separate templates with a line containing only {TEMPLATE_DELIM}.
Example of slot use: "Dear {{{{PERSON_1:first}}}}, your PAN {{{{PAN_1}}}} is linked to account {{{{BANK_ACCOUNT_1}}}} (IFSC {{{{IFSC_1}}}}). Revised CTC: {{{{COMPENSATION_1}}}}. Helpline {{{{NEG_HELPLINE_1}}}}."
"""
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


_FENCE = re.compile(r"^```[a-z]*\s*$", re.M)
_LEAD = re.compile(r"^\s*(?:template|example)\s*\d*\s*[:.)-]\s*\n?", re.I)


def parse_llm_output(raw: str) -> list[str]:
    """Split a model reply into template strings (code fences and 'Template 1:' labels removed)."""
    raw = _FENCE.sub("", raw)
    parts = re.split(rf"^\s*{re.escape(TEMPLATE_DELIM)}+\s*$", raw, flags=re.M)
    out = []
    for p in parts:
        p = _LEAD.sub("", p.strip("\n")).rstrip()
        if p.strip():
            out.append(p)
    return out
