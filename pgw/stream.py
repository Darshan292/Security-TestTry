"""Streaming restore with a bounded hold-back, and the output gate (ADR-0008).

Order per chunk of released text:  output gate on ALIASED text  ->  restore  ->  emit.

Hold-back: we never release (a) the trailing whitespace-free token (it may be an
incomplete alias, email, id or secret) or (b) up to HOLD_MAX chars of preceding
tokens that contain digits or amount/date words (numbers span spaces: "9820 012 345",
"12 March 1984", "38 lakh"). Everything else is released immediately.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .alias import ALIAS_RE
from .detect import recognizers
from .types import EntityType

HOLD_MAX = 64
TOKEN_MAX = 256       # a whitespace-free run longer than this is released in pieces (still gated)
LOOKBACK = 48         # context the gate sees from already-released text

_RISKY_WORD = re.compile(
    r"(?:\d|₹|rs\.?$|inr|lakh|lac|crore|lpa|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\+)", re.IGNORECASE)
_GATE_TYPES = {EntityType.AADHAAR, EntityType.PAN, EntityType.CARD, EntityType.SECRET,
               EntityType.BANK_ACCOUNT, EntityType.UPI_ID, EntityType.EMAIL, EntityType.PHONE}


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
    events: list[GateEvent] = field(default_factory=list)
    restored: list = field(default_factory=list)   # (EntityType, value) actually emitted -> restore ledger
    _buf: str = ""
    _released_aliased: str = ""

    # -- public -------------------------------------------------------------
    def feed(self, delta: str) -> str:
        self._buf += delta
        cut = self._cut(self._buf)
        region, self._buf = self._buf[:cut], self._buf[cut:]
        return self._process(region)

    def finish(self) -> str:
        region, self._buf = self._buf, ""
        return self._process(region)

    # -- internals ----------------------------------------------------------
    def _cut(self, b: str) -> int:
        ws = max(b.rfind(" "), b.rfind("\n"), b.rfind("\t"))
        cut = ws + 1
        if len(b) - cut > TOKEN_MAX:          # pathological long token: release all but the tail
            return len(b) - TOKEN_MAX
        # step back over preceding risky tokens (numbers/dates/amounts spanning spaces)
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

    def _process(self, region: str) -> str:
        if not region:
            return ""
        ctx = self._released_aliased[-LOOKBACK:]
        gated = self._gate(ctx, region)
        self._released_aliased += gated
        self._released_aliased = self._released_aliased[-LOOKBACK:]
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
        # 2. deterministic recognisers on the aliased text
        for d in recognizers.detect_d2(text):
            if d.end <= off or d.type not in _GATE_TYPES:
                continue
            if any(s <= d.start < e for s, e in alias_spans):
                continue
            self.events.append(GateEvent("raw_pii", d.type.value))
            repl.append((max(d.start, off), d.end, f"[{d.type.value}]"))
        # 3. roster names the model produced on its own
        if self.roster is not None:
            for d in self.roster.detect(text):
                if d.end > off and not any(s <= d.start < e for s, e in alias_spans):
                    self.events.append(GateEvent("edm_name", d.type.value))
                    repl.append((max(d.start, off), d.end, f"[{d.type.value}]"))
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
