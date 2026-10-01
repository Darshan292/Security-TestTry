"""PD-13 protector: L0+ views, L1 records, L2 shapes, L3 semantics (optional), L4 release policy.

Subclasses pgw.pipeline.Protector. ``analyze()`` and ``protect()`` are inherited, so aliasing, D5 propagation,
policy actions and the egress invariant are unchanged; only ``detect_text`` (and the D1 switch) differ. D1 is
turned off with the existing ablation switch for the duration of every detection call.
"""
from __future__ import annotations

import datetime as _dt
import re
from bisect import bisect_left
from pathlib import Path

from pgw import ablation as _abl
from pgw.detect import recognizers
from pgw.detect.edm import RosterIndex
from pgw.normalize import normalize, to_original
from pgw.normalize import views as d0_views
from pgw.pipeline import Keys, Protector, _name_shaped, detect_person_cues
from pgw.policy import PolicyPack
from pgw.types import Detection

from . import policy as l4
from .dictionary import Dictionary
from .fieldview import field_view, name_cells
from .records import parse
from .semantics import Semantics
from .shapes import Shape, shapes as l2_shapes
from .views import extra_views

_GROUPED_DIGITS = re.compile(r"^[0-9][0-9 \-]*[0-9]$")
_PLAIN_INT = re.compile(r"^\d{4,9}$")
_ROOT = Path(__file__).resolve().parents[2]


def _merge_shapes(cands: list[Shape]) -> list[Shape]:
    """Union overlapping candidates of one kind, then resolve kinds DATE > AMOUNT > LONG_DIGITS > HIGH_ENTROPY."""
    by_kind: dict[str, list[Shape]] = {}
    for c in cands:
        by_kind.setdefault(c.kind, []).append(c)
    merged: list[Shape] = []
    for kind, lst in by_kind.items():
        lst.sort(key=lambda c: (c.start, c.end))
        cur = None
        for c in lst:
            if cur and c.start < cur.end:
                cur = Shape(cur.start, max(cur.end, c.end), kind, cur.ymd or c.ymd)
            else:
                if cur:
                    merged.append(cur)
                cur = c
        if cur:
            merged.append(cur)
    prio = {"DATE": 0, "AMOUNT": 1, "LONG_DIGITS": 2, "HIGH_ENTROPY": 3}
    merged.sort(key=lambda c: (prio[c.kind], -(c.end - c.start), c.start))
    kept: list[Shape] = []
    for c in merged:
        if all(c.end <= k.start or c.start >= k.end for k in kept):
            kept.append(c)
    return sorted(kept, key=lambda c: c.start)


class PD13Protector(Protector):
    def __init__(self, *a, l3: bool = True, dictionary: Dictionary | None = None, today: _dt.date | None = None, **kw):
        super().__init__(*a, **kw)
        self.l3 = l3
        self._cell_cache: dict[str, list[Detection]] = {}
        self.semantics = Semantics(dictionary, today) if l3 else None

    # D1 (field-context detection) is removed in B and C through the existing ablation switch.
    def _detect_segments(self, segs, ctx):
        with _abl.use(_abl.get().without("D1")):
            return super()._detect_segments(segs, ctx)

    def detect_text(self, text: str) -> list[Detection]:
        with _abl.use(_abl.get().without("D1")):
            return self._detect(text)

    def _cell_ner(self, cell: str) -> list[Detection]:
        hit = self._cell_cache.get(cell)
        if hit is None:
            hit = [d for d in self.ner.detect(cell) if _name_shaped(cell[d.start:d.end])]
            if len(self._cell_cache) > 20000:
                self._cell_cache.clear()
            self._cell_cache[cell] = hit
        return hit

    def _detect(self, text: str) -> list[Detection]:
        if not text:
            return []
        base_dets = Protector.detect_text(self, text)             # D0 views + D2 + cues + D3 + D4, original coordinates
        base, om = normalize(text)

        def to_base(s: int, e: int) -> tuple[int, int]:
            bs = bisect_left(om, s, 0, len(base))
            be = bisect_left(om, e, 0, len(base))
            return bs, max(be, bs)

        dets: list[Detection] = []
        for d in base_dets:
            s, e = to_base(d.start, d.end)
            if e > s:
                dets.append(Detection(s, e, d.type, d.score, d.source))
        cands: list[Shape] = list(l2_shapes(base))
        # L0+ views: detectors and shape candidates, mapped back to the base view
        for v in extra_views(base):
            vd = recognizers.detect_d2(v.text) + detect_person_cues(v.text)
            if self.roster is not None and _abl.get().D3:
                vd += self.roster.detect(v.text)
            for d in vd:
                s, e = v.to_base(d.start, d.end)
                if e > s:
                    dets.append(Detection(s, e, d.type, d.score, f"{d.source}@{v.name}"))
            for c in l2_shapes(v.text):
                s, e = v.to_base(c.start, c.end)
                if e > s:
                    cands.append(Shape(s, e, c.kind, c.ymd))
        if _abl.get().D0:
            for name, vt, vom in d0_views(text):
                if name == "digits_joined":
                    for c in l2_shapes(vt):
                        s, e = to_base(*to_original(vom, c.start, c.end))
                        # only digit groups written with spaces or hyphens ("1234 5678 9012"); a dotted or slashed
                        # chain is an address, version or reference number
                        if e > s and _GROUPED_DIGITS.match(base[s:e]):
                            cands.append(Shape(s, e, c.kind, c.ymd))
        graph = parse(base)
        for f in graph.fields:                    # a numeric field value of >= 4 digits (not a calendar year) is an amount candidate
            v = base[f.start:f.end]
            if _PLAIN_INT.match(v) and not 1900 <= int(v) <= 2100:
                cands.append(Shape(f.start, f.end, "AMOUNT"))
        shp = _merge_shapes(cands)
        fv = field_view(base, graph)             # L1 field values one per line: the roster n-gram matcher sees names unglued
        if fv is not None and self.roster is not None and _abl.get().D3:
            for d in self.roster.detect(fv.text):
                s, e = fv.to_base(d.start, d.end)
                if d.type.value == "PERSON" and e > s and "\n" not in fv.text[d.start:d.end]:
                    dets.append(Detection(s, e, d.type, d.score, f"{d.source}@fields"))
        if self.ner is not None and _abl.get().D4:      # NER per name-shaped cell: no digits or delimiters in its context
            for a, b, t in name_cells(base, graph):
                for d in self._cell_ner(t):
                    dets.append(Detection(a + d.start, a + d.end, d.type, d.score, f"{d.source}@cell"))
        l3res = None
        if self.semantics is not None:
            persons = [(d.start, d.end) for d in dets if d.type.value == "PERSON"]
            l3res = self.semantics.analyse(base, graph, shp, persons)
        final = l4.apply(dets, shp, graph, l3res, base)
        out = []
        for d in final:
            s, e = to_original(om, d.start, d.end)
            if e > s:
                out.append(Detection(s, e, d.type, d.score, d.source))
        return out


def build_protector(data: Path, ner: str | None = None, l3: bool = True, today: _dt.date | None = None) -> PD13Protector:
    """Same construction as eval.run_det.build_protector (roster from data/roster.jsonl, optional spaCy NER)."""
    import json
    keys = Keys(bytes(32))
    roster = None
    rp = Path(data) / "roster.jsonl"
    if rp.exists():
        roster = RosterIndex(keys.derive("eval", "edm"))
        for line in rp.read_text().splitlines():
            if line.strip():
                roster.add_employee(json.loads(line))
    ner_det = None
    if ner:
        from pgw.detect.ner import NerDetector
        ner_det = NerDetector(ner)
    return PD13Protector(PolicyPack.load(_ROOT / "policies/hris-default.yaml"), keys, roster, ner=ner_det, l3=l3, today=today)
