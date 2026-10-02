"""Request protection: normalise -> detect (D1, D2, D3, D3b) -> propagate (D5) -> decide -> transform.

Works on the OpenAI Chat Completions message list. Anything in a content
position that we do not understand is rejected, never forwarded un-inspected.
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .alias import AliasAllocator, canon, scope_key
from .detect import recognizers
from .detect.edm import RestoreLedger, RosterIndex
from . import ablation as _abl
from . import normalize as _normalize_mod
from .normalize import normalize, to_original


def _views(text: str):
    """D0 views if available (R2), else the single base view."""
    if hasattr(_normalize_mod, "views") and _abl.get().D0:
        return _normalize_mod.views(text)
    return [("base", *normalize(text))]
from .policy import PolicyPack, generalize
from .types import Action, BlockedRequest, Context, Detection, Entity, EntityType


class UnsupportedContent(Exception):
    """400: a field in a content position the gateway cannot inspect."""


class ConversationIdRequired(Exception):
    """400: multi-turn restore without a conversation id (ADR-0010 rule 1)."""


ALLOWED_MESSAGE_KEYS = {"role", "content", "name", "tool_calls", "tool_call_id"}
ALLOWED_ROLES = {"system", "developer", "user", "assistant", "tool"}


# --- keys ---------------------------------------------------------------------

@dataclass(frozen=True)
class Keys:
    master: bytes

    @classmethod
    def from_env(cls) -> "Keys":
        hexkey = os.environ.get("PGW_MASTER_KEY")
        if not hexkey:
            raise RuntimeError("PGW_MASTER_KEY (64 hex chars) is required")
        return cls(bytes.fromhex(hexkey))

    def derive(self, tenant: str, purpose: str) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                    info=f"pgw/{purpose}/v1/{tenant}".encode()).derive(self.master)


# --- weak PERSON cues (Phase 1 has no NER) ---------------------------------------

_CAP = r"[A-Z][a-z'’\-]+(?:\s+[A-Z]\.)?"
_HONORIFIC = re.compile(rf"\b(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt|Sri|Prof)\.?\s+((?:{_CAP})(?:\s+{_CAP}){{0,2}})")
_GREETING = re.compile(rf"\b(?:Dear|Hi|Hello|Hey)\s+((?:{_CAP})(?:\s+{_CAP}){{0,2}})")
_SIGNOFF = re.compile(rf"(?:Regards|Thanks|Thank you|Best|Sincerely|Cheers),?\s*\n\s*((?:{_CAP})(?:\s+{_CAP}){{0,2}})")
# Preposition + >= 2 capitalised tokens ("email to Rahul Sharma"). Over-masks some org names;
# that costs utility, a miss costs a leak. Precision is measured in E-DET.
_PREP = re.compile(rf"\b(?:to|for|from|with|cc|about|regarding|by|and|between|of)\s+((?:{_CAP})(?:\s+{_CAP}){{1,2}})")
_NOT_NAMES = {"team", "all", "everyone", "sir", "madam", "there", "folks", "hr", "manager", "guys",
              "support", "admin", "the", "this", "that", "please", "thanks", "regards"}


_NAME_TOKEN = re.compile(r"(?:[A-Z][a-z]+(?:-[A-Z][a-z]+)*|[A-Z]['’][A-Z][a-z]+|[A-Z][a-z]+['’][a-z]+|[A-Z]\.)$")
_CAPS_TOKEN = re.compile(r"[A-Z]{2,}(?:['’-][A-Z]+)*$")


def _name_shaped(span: str) -> bool:
    """Shape filter for statistical NER hits (R2, dev2): spaCy tags code identifiers such as
    AWS_SECRET_ACCESS_KEY, BankRepo.save(BankRepo.java or payroll_rw as PERSON. Accept a span
    only if every token is a Title-case name word or an initial, or the span is >= 2 all-caps
    alphabetic words (ANINDITA ROUT). Digits, _ ( ) / = and camelCase never pass."""
    toks = span.split()
    if not toks or len(toks) > 5:
        return False
    if all(_NAME_TOKEN.match(t) for t in toks):
        return True
    return len(toks) >= 2 and all(_CAPS_TOKEN.match(t) for t in toks)


def detect_person_cues(text: str) -> list[Detection]:
    out = []
    if not _abl.get().CUES:
        return out
    for rx, score in ((_HONORIFIC, 0.85), (_GREETING, 0.7), (_SIGNOFF, 0.7), (_PREP, 0.6)):
        for m in rx.finditer(text):
            name = m.group(1)
            if name.split()[0].lower() in _NOT_NAMES:
                continue
            out.append(Detection(m.start(1), m.end(1), EntityType.PERSON, score, "D2:person_cue"))
    return out


# --- segments -----------------------------------------------------------------

@dataclass
class Segment:
    role: str
    text: str
    setter: Callable[[str], None]


def _segments(messages: list[dict]) -> list[Segment]:
    segs: list[Segment] = []
    for msg in messages:
        if not isinstance(msg, dict) or set(msg) - ALLOWED_MESSAGE_KEYS or msg.get("role") not in ALLOWED_ROLES:
            raise UnsupportedContent("unsupported message shape")
        role = msg["role"]
        content = msg.get("content")
        if isinstance(content, str):
            segs.append(Segment(role, content, lambda v, m=msg: m.__setitem__("content", v)))
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "text" or set(part) - {"type", "text"}:
                    raise UnsupportedContent("only text content parts are supported")
                segs.append(Segment(role, part["text"], lambda v, p=part: p.__setitem__("text", v)))
        elif content is not None:
            raise UnsupportedContent("content must be string, list or null")
        if isinstance(msg.get("name"), str):
            segs.append(Segment(role, msg["name"], lambda v, m=msg: m.__setitem__("name", v)))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc, dict) else None
            if not isinstance(fn, dict) or not isinstance(fn.get("arguments"), str):
                raise UnsupportedContent("unsupported tool_call shape")
            segs.append(Segment(role, fn["arguments"], lambda v, f=fn: f.__setitem__("arguments", v)))
    return segs


def _field_values(text: str) -> list[tuple[EntityType, str]]:
    """D1: values under sensitive field names in JSON segments."""
    s = text.lstrip()
    if not s[:1] in ("{", "["):
        return []
    try:
        obj = json.loads(s)
    except ValueError:
        return []
    out = []
    for path, t in recognizers.detect_fields(obj):
        v = obj
        for k in path:
            v = v[k]
        if isinstance(v, bool) or v is None:
            continue
        sv = str(v)
        if sv.strip():
            out.append((t, sv))
    return out


# --- propagation ----------------------------------------------------------------

_STOP_TOKENS = {"mr", "mrs", "ms", "dr", "shri", "smt", "kumar", "bin", "van", "von", "de", "da"}


def _occurrences(text: str, t: EntityType, surface: str) -> list[tuple[int, int]]:
    if len(surface) < 2:
        return []
    flags = re.IGNORECASE if t in (EntityType.PERSON, EntityType.EMAIL, EntityType.UPI_ID) else 0
    pre = r"(?<![\w@])" if surface[0].isalnum() else ""
    post = r"(?![\w@])" if surface[-1].isalnum() else ""
    return [(m.start(), m.end()) for m in re.finditer(pre + re.escape(surface) + post, text, flags)]


def _resolve(dets: list[Detection]) -> list[Detection]:
    """Non-overlapping set: higher score wins, then longer span."""
    kept: list[Detection] = []
    for d in sorted(dets, key=lambda d: (-d.score, -(d.end - d.start), d.start)):
        if all(d.end <= k.start or d.start >= k.end for k in kept):
            kept.append(d)
    return sorted(kept, key=lambda d: d.start)


# --- the pipeline ---------------------------------------------------------------

@dataclass
class Protected:
    messages: list[dict]
    scope_id: str
    entities: dict[tuple[EntityType, str], Entity]
    alias_map: dict[str, Entity] = field(default_factory=dict)   # "TYPE_SUFFIX" -> entity

    @property
    def sensitive_values(self) -> list[tuple[EntityType, str]]:
        """Everything the egress invariant must not see (ADR-0007)."""
        out = []
        for e in self.entities.values():
            if e.action is not Action.ALLOW:
                out.extend((e.type, s) for s in e.sources)
        return out

    def type_counts(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for e in self.entities.values():
            c[f"{e.type.value}:{e.action.value}"] = c.get(f"{e.type.value}:{e.action.value}", 0) + 1
        return c


class Protector:
    def __init__(self, policy: PolicyPack, keys: Keys, roster: RosterIndex | None = None,
                 ledger: RestoreLedger | None = None, ner=None):
        self.policy = policy
        self.keys = keys
        self.roster = roster
        self.ledger = ledger
        self.ner = ner          # D4 (pgw.detect.ner.NerDetector) or None; its errors propagate = fail closed

    def detect_text(self, text: str) -> list[Detection]:
        """D0 views + D2 + person cues + D3 (+ D4 NER) on one string; offsets in the ORIGINAL string.

        Every detector runs on every D0 view (base, de-spaced, de-obfuscated, digit-joined,
        case-restored); each view's offset map sends spans back to the original text, and
        overlaps are resolved later, so an evasion only has to be undone by one view.
        """
        out = []
        for name, view, omap in _views(text):
            dets = recognizers.detect_d2(view) + detect_person_cues(view)
            if self.roster is not None and _abl.get().D3:
                dets += self.roster.detect(view)
            if self.ner is not None and _abl.get().D4 and name in ("base", "casefold_titled", "despaced"):
                dets += [d for d in self.ner.detect(view) if _name_shaped(view[d.start:d.end])]
            for d in dets:
                s, e = to_original(omap, d.start, d.end)
                if e > s:
                    out.append(Detection(s, e, d.type, d.score, f"{d.source}@{name}" if name != "base" else d.source))
        return out

    def protect(self, messages: list[dict], ctx: Context,
                declared: list[tuple[EntityType, str]] | None = None) -> Protected:
        """declared: values a data contract states explicitly (e.g. names and IDs from a structured payload).
        They are protected wherever they occur, independent of detection; they can only add protection."""
        messages = copy.deepcopy(messages)
        segs = _segments(messages)

        if ctx.restore_output and not ctx.conversation_id and any(s.role == "assistant" for s in segs):
            raise ConversationIdRequired()

        per_seg = self._detect_segments(segs, ctx, declared) if declared else self._detect_segments(segs, ctx)
        return self._transform(messages, segs, per_seg, ctx)

    def analyze(self, messages: list[dict], ctx: Context) -> list[list[Detection]]:
        """Final resolved spans per segment (for evaluation); does not transform."""
        return self._detect_segments(_segments(copy.deepcopy(messages)), ctx)

    def _detect_segments(self, segs: list[Segment], ctx: Context,
                         declared: list[tuple[EntityType, str]] | None = None) -> list[list[Detection]]:
        # 1. Detect per segment (plus contract-declared values, which need no detection).
        per_seg: list[list[Detection]] = [self.detect_text(s.text) for s in segs]
        for t, v in declared or ():
            if v and v.strip():
                for i, seg in enumerate(segs):
                    per_seg[i] += [Detection(s, e, t, 1.0, "C:declared") for s, e in _occurrences(seg.text, t, v)]
        if self.ledger is not None and ctx.conversation_id:
            normed = [normalize(s.text) for s in segs]
            for i, dets in enumerate(self.ledger.detect(ctx, [n for n, _ in normed])):
                omap = normed[i][1]
                per_seg[i] += [Detection(*to_original(omap, d.start, d.end), d.type, d.score, d.source)
                               for d in dets]

        if not _abl.get().D5:   # ablation: no propagation step at all (still resolve overlaps)
            return [_resolve(d) for d in per_seg]

        # 2. Known values: everything detected anywhere, plus D1 field values.
        known: dict[tuple[EntityType, str], float] = {}
        for seg, dets in zip(segs, per_seg):
            for d in _resolve(dets):
                known[(d.type, seg.text[d.start:d.end])] = max(d.score, known.get((d.type, seg.text[d.start:d.end]), 0))
            for t, v in (_field_values(seg.text) if _abl.get().D1 else []):
                known[(t, v)] = max(0.95, known.get((t, v), 0))
        # PERSON partial mentions: "Rahul" after "Rahul Sharma" (D5).
        for (t, surface), sc in list(known.items()):
            if t is EntityType.PERSON:
                for tok in re.findall(r"[^\W\d_][\w'’\-]*", surface):
                    if len(tok) >= 3 and tok.lower() not in _STOP_TOKENS:
                        known.setdefault((t, tok), sc - 0.05)

        # 3. D5 propagation: every known value, everywhere.
        for i, seg in enumerate(segs):
            for (t, surface), sc in known.items():
                per_seg[i] += [Detection(s, e, t, sc, "D5:propagation") for s, e in _occurrences(seg.text, t, surface)]
            per_seg[i] = _resolve(per_seg[i])
        return per_seg

    def _transform(self, messages, segs, per_seg, ctx) -> Protected:
        # 4. Decide.
        entities: dict[tuple[EntityType, str], Entity] = {}
        for seg, dets in zip(segs, per_seg):
            for d in dets:
                surface = seg.text[d.start:d.end]
                key = (d.type, canon(d.type, surface))
                ent = entities.get(key)
                if ent is None:
                    ent = entities[key] = Entity(d.type, surface, self.policy.decide(ctx.purpose, d.type))
                ent.sources.add(surface)
        blocked = [e.type for e in entities.values() if e.action is Action.BLOCK_REQUEST]
        if blocked:
            raise BlockedRequest(blocked)

        # 5. Transform.
        scope_id = ctx.conversation_id or secrets.token_hex(16)
        alloc = AliasAllocator(scope_key(self.keys.derive(ctx.tenant, "alias"), scope_id))
        prot = Protected(messages, scope_id, entities)
        for key, ent in entities.items():
            if ent.action is Action.TOKENIZE:
                ent.alias = alloc.get(ent.type, ent.value)
        for key, ent in entities.items():  # second pass: collisions may have re-assigned aliases
            if ent.action is Action.TOKENIZE:
                ent.alias = alloc.get(ent.type, ent.value)
                ent.replacement = ent.alias
                prot.alias_map[ent.alias.strip("[]").upper()] = ent
            elif ent.action is Action.REDACT:
                ent.replacement = f"[{ent.type.value}]"
            elif ent.action is Action.GENERALIZE:
                ent.replacement = generalize(ent.type, ent.value)

        for seg, dets in zip(segs, per_seg):
            text = seg.text
            for d in sorted(dets, key=lambda d: d.start, reverse=True):
                ent = entities[(d.type, canon(d.type, text[d.start:d.end]))]
                if ent.replacement is not None:
                    text = text[:d.start] + ent.replacement + text[d.end:]
            if text != seg.text:
                seg.setter(text)
        return prot
