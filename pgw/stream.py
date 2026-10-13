"""Streaming restore and the output gate (ADR-0008, amended R2).

Order for every piece of text released to the client:
    output gate on ALIASED text  ->  restore  ->  emit

Modes (chosen per purpose by policy, see PolicyPack.stream_mode):
  * "buffered":    nothing is released until the upstream finishes; the gate sees the whole output.
                   Default for RESTRICTED purposes until streaming passes E-STREAM-LEAK.
  * "incremental": bounded hold-back. R2 changes, driven by the R1 TEST failures:
      - W4: the gate runs deterministic recognisers over the WHOLE aliased output released so far
            (capped at GATE_CONTEXT_MAX), not a 48-char lookback, so context-dependent types
            (a TSV header far above a value) are judged the same as in buffered mode. Past the cap
            the restorer stops releasing and degrades to buffered for the remainder.
      - W5: a whitespace-free run longer than TOKEN_MAX is cut only at a delimiter that is not
            inside an alias or an alias prefix; if no safe cut exists, it is held.
      - text is released in batches (>= MIN_RELEASE chars or at a newline) to bound gate cost.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .alias import ALIAS_RE
from .detect import recognizers
from .types import EntityType

HOLD_MAX = 64            # max chars of trailing risky tokens held back (numbers/dates/amounts span spaces)
TOKEN_MAX = 256          # longest whitespace-free run held back before a delimiter cut is attempted
LINE_MAX = 2048          # incremental mode releases whole lines; longer lines fall back to word cuts
GUARD_LOOKBACK = 4096    # released context the straddle guard re-reads
NAME_TOKENS = 2          # complete words always held (max roster n-gram is 3 words)
MIN_RELEASE = 48         # batch size for releases in incremental mode
GATE_CONTEXT_MAX = 32_768  # beyond this much output, incremental degrades to buffered

_RISKY_WORD = re.compile(
    r"(?:\d|₹|rs\.?$|inr|lakh|lac|crore|lpa|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\+|@)", re.IGNORECASE)
_SAFE_DELIMS = set(',;{}[]"\'<>=&|')
_GATE_TYPES = {EntityType.AADHAAR, EntityType.PAN, EntityType.CARD, EntityType.SECRET,
               EntityType.BANK_ACCOUNT, EntityType.UPI_ID, EntityType.EMAIL, EntityType.PHONE,
               EntityType.EMPLOYEE_ID, EntityType.COMPENSATION, EntityType.DATE_OF_BIRTH}
_DIGIT_TYPES = {EntityType.AADHAAR, EntityType.CARD, EntityType.BANK_ACCOUNT, EntityType.PHONE,
                EntityType.EMPLOYEE_ID, EntityType.COMPENSATION}


@dataclass
class GateEvent:
    kind: str          # "fabricated_alias" | "raw_pii" | "edm_name"
    type: str


@dataclass
class Restorer:
    alias_map: dict            # "TYPE_SUFFIX" -> Entity
    restore: bool = True
    never_restore: frozenset = frozenset()
    roster: object | None = None   # RosterIndex, for names the model "knew"
    mode: str = "incremental"      # "incremental" | "buffered"
    allowed: frozenset = frozenset()   # contract org_contacts (C0, lowercase) the model may write in its answer
    known_withheld: tuple[tuple[EntityType, str], ...] = ()
    _known_patterns: tuple = field(default=(), init=False, repr=False)

    def __post_init__(self):
        patterns=[]; seen=set()
        for typ,value in self.known_withheld:
            if not isinstance(value,str) or len(value.strip())<4: continue
            for variant in {value, value.strip(" \t\r\n.,;:!?)]}\"'([{<")}:
                if len(variant)<4 or (typ,variant) in seen: continue
                seen.add((typ,variant))
                if typ in _DIGIT_TYPES:
                    digits=re.sub(r"\D","",variant)
                    rx=re.compile(r"(?<!\d)" + r"[\s,._/\-]*".join(re.escape(c) for c in digits) + r"(?!\d)") if len(digits)>=4 else re.compile(re.escape(variant))
                else: rx=re.compile(re.escape(variant), re.I if typ in {EntityType.PERSON,EntityType.EMAIL,EntityType.UPI_ID} else 0)
                patterns.append((typ,rx))
        self._known_patterns=tuple(patterns)
    events: list[GateEvent] = field(default_factory=list)
    restored: list = field(default_factory=list)   # (EntityType, value) actually emitted -> restore ledger
    _buf: str = ""
    _released_aliased: str = ""
    _degraded: bool = False

    # -- public -------------------------------------------------------------
    def feed(self, delta: str) -> str:
        self._buf += delta
        if self.mode == "buffered" or self._degraded:
            return ""
        if len(self._released_aliased) + len(self._buf) > GATE_CONTEXT_MAX:
            self._degraded = True          # hold everything; judged at finish()
            return ""
        cut = self._line_cut(self._buf)
        cut = self._straddle_guard(cut)
        if cut <= 0:
            return ""
        region, self._buf = self._buf[:cut], self._buf[cut:]
        return self._process(region)

    def finish(self) -> str:
        region, self._buf = self._buf, ""
        return self._process(region)

    # -- internals ----------------------------------------------------------
    def _line_cut(self, b: str) -> int:
        """Release whole lines only: recognisers use same-line right context (an IFSC after an account
        number, "DOB X, not Y"). A line longer than LINE_MAX falls back to the word-level cut."""
        nl = b.rfind("\n")
        pending = len(b) - (nl + 1)
        if pending <= LINE_MAX:
            return nl + 1 if nl >= 0 else 0
        return self._cut(b)

    def _straddle_guard(self, cut: int) -> int:
        """Never release part of something the gate would mask once more text is seen: detect on
        (released tail + buffer) and pull the cut back to the start of any detection crossing it."""
        if cut <= 0:
            return 0
        tail = self._released_aliased[-GUARD_LOOKBACK:]
        text = tail + self._buf
        off = len(tail)
        spans = [(d.start, d.end) for d in recognizers.detect_d2(text) if d.type in _GATE_TYPES]
        spans.extend((m.start(),m.end()) for _,rx in self._known_patterns for m in rx.finditer(text))
        if self.roster is not None:
            spans += [(d.start, d.end) for d in self.roster.detect(text)]
        c = off + cut
        moved = True
        while moved:
            moved = False
            for s, e in spans:
                if s < c < e:
                    c, moved = s, True
        return max(0, c - off)

    def _cut(self, b: str) -> int:
        ws = max(b.rfind(" "), b.rfind("\n"), b.rfind("\t"))
        cut = ws + 1
        if len(b) - cut > TOKEN_MAX:
            cut = self._delimiter_cut(b, cut)
            if cut is None:
                return 0
        # hold the last NAME_TOKENS complete words: roster names match as <= 3-word n-grams, so a word
        # that could still become the start of a match must not be released yet (R2 dev2: "bhavesh"
        # leaked when "bhavesh chauhan" arrived across two releases)
        for _ in range(NAME_TOKENS):
            j = cut - 1
            while j >= 0 and b[j].isspace():
                j -= 1
            k = j
            while k >= 0 and not b[k].isspace():
                k -= 1
            if j < 0 or len(b) - (k + 1) > HOLD_MAX:
                break
            cut = k + 1
        # step back over preceding risky tokens (numbers/dates/amounts/emails spanning spaces)
        while cut > 0:
            j = cut - 1
            while j >= 0 and b[j].isspace():
                j -= 1
            if j < 0:
                break
            k = j
            while k >= 0 and not b[k].isspace():
                k -= 1
            tok = b[k + 1:j + 1]
            if _RISKY_WORD.search(tok) and len(b) - (k + 1) <= HOLD_MAX:
                cut = k + 1
            else:
                break
        return cut

    @staticmethod
    def _delimiter_cut(b: str, run_start: int) -> int | None:
        """Cut inside a long whitespace-free run: at a delimiter, outside any alias or alias prefix,
        leaving at least TOKEN_MAX chars held. None if no safe position exists."""
        limit = len(b) - TOKEN_MAX
        busy = [(m.start(), m.end()) for m in ALIAS_RE.finditer(b, run_start)]
        for p in range(limit, run_start, -1):
            if b[p - 1] not in _SAFE_DELIMS:
                continue
            if any(s < p < e for s, e in busy):   # never inside a complete alias
                continue
            return p                               # an incomplete alias can only sit in the held tail (<= 31 chars)
        return None

    def _process(self, region: str) -> str:
        if not region:
            return ""
        gated = self._gate(self._released_aliased, region)
        self._released_aliased += gated
        return self._restore(gated)

    def _gate(self, ctx: str, region: str) -> str:
        text = ctx + region
        off = len(ctx)
        repl: list[tuple[int, int, str]] = []
        # 1. alias-shaped strings that are not in scope were invented by the model
        alias_spans = []
        for m in ALIAS_RE.finditer(text):
            alias_spans.append((m.start(), m.end()))
            if m.end() <= off:
                continue
            key = f"{m.group(1).upper()}_{m.group(2).upper()}"
            if key not in self.alias_map:
                self.events.append(GateEvent("fabricated_alias", m.group(1).upper()))
                repl.append((max(m.start(), off), m.end(), f"[unknown {m.group(1).upper()}]"))
        # Exact per-request values withheld from the provider are gated even if generic recognisers miss context.
        for typ,rx in self._known_patterns:
            for m in rx.finditer(text):
                if m.end() <= off or any(a <= m.start() and m.end() <= b for a,b in alias_spans): continue
                self.events.append(GateEvent("known_withheld", typ.value))
                repl.append((max(m.start(),off),m.end(),f"[{typ.value}]"))
        # 2. deterministic recognisers over the whole output so far (context-dependent types need it)
        for d in recognizers.detect_d2(text):
            if d.end <= off or d.type not in _GATE_TYPES:
                continue
            if text[d.start:d.end].lower() in self.allowed:   # declared organisational contact, not personal
                continue
            if any(s <= d.start < e for s, e in alias_spans):
                continue
            self.events.append(GateEvent("raw_pii", d.type.value))
            repl.append((max(d.start, off), d.end, f"[{d.type.value}]"))
        # 3. roster names the model produced on its own (context-free: region plus a short lookback)
        if self.roster is not None:
            lb = max(0, off - 64)
            for d in self.roster.detect(text[lb:]):
                s, e = d.start + lb, d.end + lb
                if e > off and not any(a <= s < b for a, b in alias_spans):
                    self.events.append(GateEvent("edm_name", d.type.value))
                    repl.append((max(s, off), e, f"[{d.type.value}]"))
        out = text
        taken: list[tuple[int, int]] = []
        for s, e, r in sorted(repl, key=lambda x: (-x[0], x[0] - x[1])):
            if any(not (e <= ts or s >= te) for ts, te in taken):
                continue
            taken.append((s, e))
            out = out[:s] + r + out[e:]
        return out[off:] if len(out) >= off else ""

    def _restore(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            ent = self.alias_map.get(f"{m.group(1).upper()}_{m.group(2).upper()}")
            if ent is None or not self.restore or ent.type in self.never_restore:
                return m.group(0)
            self.restored.append((ent.type, ent.value))
            return ent.value
        return ALIAS_RE.sub(sub, text)
