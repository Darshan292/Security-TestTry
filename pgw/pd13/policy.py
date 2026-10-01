"""L4: release policy (RESTRICTED). Pure functions over detections, shapes, the record graph and L3's result.

Person-bearing: a record (or, for prose, the document) that contains a roster / NER / cue PERSON, an EMPLOYEE_ID,
or a personal EMAIL / PHONE (or, in arm B, an L3-typed one of those).

Rules:
 1. D2 / roster validator hits are kept exactly as detected. Nothing here removes or retypes them.
 2. A shape candidate in a person-bearing record (person-bearing document for prose) is MASKED unless L3
    types it (then the typed span is emitted) or marks it with a compatible RELEASE concept.
 3. HIGH_ENTROPY is masked in every document unless L3 releases it.
 4. L3 may type a value but can never release a D2 validator hit: shapes overlapping a validator hit are
    dropped (the validator hit stands), and typed spans overlapping one are dropped.
 5. Without L3 (arm C) there is no typing and no release: rules 1-3 apply without exceptions.
"""
from __future__ import annotations

import re
from bisect import bisect_right

from pgw.types import Detection, EntityType as T

from .records import Graph
from .semantics import L3Result
from .shapes import Shape

SHAPE_SCORE = 0.8
L3_SCORE = 0.88
_ROLE_LOCAL = {"hr", "hrd", "hrms", "payroll", "support", "info", "admin", "noreply", "no-reply", "donotreply",
               "helpdesk", "careers", "jobs", "accounts", "finance", "it", "contact", "sales", "team", "office",
               "hello", "mail", "recruitment", "recruiter", "benefits", "training", "security", "compliance", "legal",
               "service", "services", "notifications", "alerts", "billing", "feedback", "enquiry", "inquiry", "ops"}
_COMPAT = {"DATE": {"RELEASE_DATE"}, "AMOUNT": {"RELEASE_AMOUNT"},
           "LONG_DIGITS": {"RELEASE_ID", "RELEASE_AMOUNT"}, "HIGH_ENTROPY": {"RELEASE_ID", "RELEASE_TEXT"}}


def is_validator(d: Detection) -> bool:
    """D2 pattern recognisers and roster EDM hits other than PERSON mentions."""
    return d.type is not T.PERSON and d.source.startswith(("D2:", "D3:"))


def personal_phone(v: str) -> bool:
    d = re.sub(r"\D", "", v)
    if v.strip().startswith("+") and not v.strip().startswith("+91"):
        return True
    for pre in ("0091", "91", "0"):
        if d.startswith(pre) and len(d) - len(pre) == 10:
            d = d[len(pre):]
            break
    return len(d) == 10 and d[0] in "6789"


def is_anchor(t: T, surface: str) -> bool:
    if t in (T.PERSON, T.EMPLOYEE_ID):
        return True
    if t is T.EMAIL:
        return surface.split("@")[0].strip().lower() not in _ROLE_LOCAL
    if t is T.PHONE:
        return personal_phone(surface)
    return False


class Scope:
    """Answers 'is this span person-bearing?' from anchors and the record graph."""

    def __init__(self, graph: Graph, anchors: list[tuple[int, int]]):
        self.anchors = sorted(anchors)
        self._starts = [a for a, _ in self.anchors]
        self.doc = bool(self.anchors)
        self.recs = [r for r in graph.records if r.strong and r.counts]

    def _has_anchor(self, a: int, b: int) -> bool:
        i = bisect_right(self._starts, a - 1)
        # first anchor with start >= a
        while i < len(self.anchors) and self.anchors[i][0] < b:
            if self.anchors[i][0] >= a:
                return True
            i += 1
        return False

    def person_bearing(self, a: int, b: int) -> bool:
        inside = [r for r in self.recs if r.start <= a and b <= r.end]
        if not inside:
            return self.doc
        return any(self._has_anchor(r.start, r.end) for r in inside)


def _overlap(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


def apply(dets: list[Detection], shapes: list[Shape], graph: Graph, l3: L3Result | None, text: str,
          typed_anchor: bool = True) -> list[Detection]:
    """dets: base-coordinate detections from D2 / cues / roster / NER. Returns the L4 detection list."""
    out = list(dets)
    validators = [d for d in dets if is_validator(d)]
    v_sorted = sorted((d.start, d.end) for d in validators)
    vs = [a for a, _ in v_sorted]

    def hits_validator(a, b) -> bool:
        i = bisect_right(vs, a) - 1
        for j in range(max(0, i), len(v_sorted)):
            if v_sorted[j][0] >= b:
                break
            if _overlap(a, b, *v_sorted[j]):
                return True
        return False

    anchors = [(d.start, d.end) for d in dets if is_anchor(d.type, text[d.start:d.end])]
    typed: list[tuple[int, int, T, str]] = []
    if l3 is not None:
        typed = [t for t in l3.typed if not hits_validator(t[0], t[1])]
        if typed_anchor:
            anchors += [(a, b) for a, b, t, _ in typed if is_anchor(t, text[a:b])]
    scope = Scope(graph, anchors)

    for a, b, t, src in typed:
        out.append(Detection(a, b, t, L3_SCORE, src))
    typed_spans = [(a, b) for a, b, *_ in typed]

    rel = l3.released if l3 is not None else []
    for sh in shapes:
        if hits_validator(sh.start, sh.end):
            continue
        if any(_overlap(sh.start, sh.end, a, b) for a, b in typed_spans):
            continue                                               # L3 typed it
        released = any(c in _COMPAT[sh.kind] and sh.start >= a - 1 and sh.end <= b + 1 for a, b, c in rel)
        if released:
            continue
        if sh.kind == "HIGH_ENTROPY" or scope.person_bearing(sh.start, sh.end):
            out.append(Detection(sh.start, sh.end, sh.type, SHAPE_SCORE, f"L2:shape:{sh.kind}"))
    return out
