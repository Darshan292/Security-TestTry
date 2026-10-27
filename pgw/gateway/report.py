"""Transparency report (`pgw_report` v1, docs/TRANSPARENCY_REPORT.md): what the gateway did with one request.

Returned ONLY to the authenticated app that sent the request, and only when that app's entry in the signed registry
has `transparency: true`. It is never logged, never written to a ledger and never sent upstream.

Everything in it comes from the request's real processing: stage timings are measured around the code that ran, the
spans are the pipeline's final detections mapped back to the caller's original strings, the placeholders are the
replacement texts that went upstream, and `outbound` is the exact body that was sent (its sha256 is over the exact
wire bytes). It holds no value the caller did not send in this request: offsets, types, classes, placeholders, and
the protected text that left. Restore is reported as counts only, so no restore-ledger value (from an earlier turn
or another conversation) can appear in it.

When an app has transparency off, `NULL` stands in: every method is a no-op and nothing is built.
"""
from __future__ import annotations

import re
import time

from ..alias import canon
from ..product.contracts import CLASS_OF, DataClass, Output
from ..types import Action, EntityType

VERSION = 1
MAX_DETECTED = 2000      # entries in `detected`; past that the list is cut and `truncated` is true
STAGES = ("received", "verify_source", "compute", "protect", "egress_check", "provider", "restore")
CHECKS = {
    "egress_clean": "No value detected or declared in THIS request appears in what left",
    "declared_absent": "No declared subject appears in what left",
    "source_verified": "The input carries a valid signature from a source this contract allowlists",
    "c3_not_returned": "No government or financial identifier (C3) appears in the answer",
}
ACTION = {Action.TOKENIZE: "alias", Action.REDACT: "remove", Action.GENERALIZE: "generalize", Action.ALLOW: "allow",
          Action.BLOCK_REQUEST: "block"}
_REPLACED = ("alias", "generalize")
_REMOVED = ("remove", "withhold")


def _class(t: EntityType) -> str:
    return CLASS_OF.get(t, DataClass.C0).value


class _Null:
    """Transparency off: nothing is measured, recorded or built."""
    enabled = False

    def begin(self, sid):
        pass

    def end(self, sid, status="done"):
        pass

    def skip(self, *sids):
        pass

    def carry(self, sid, ms):
        pass

    def check(self, cid, status):
        pass

    def fail_open(self):
        pass


NULL = _Null()


class Report:
    enabled = True

    def __init__(self, rid: str, t_start: float):
        self.rid = rid
        self.purpose: str | None = None
        self.contract = None
        self.route: str | None = None
        self.input_source: str | None = None
        self.verified = False
        self.stages: dict[str, dict] = {}
        self._open: tuple[str, float] | None = ("received", t_start)
        self._carry: dict[str, float] = {}
        self.checks = dict.fromkeys(CHECKS, "n/a")
        self.prot = None              # pgw.pipeline.Protected (references, read at build time)
        self.held_maps: dict = {}     # (message, part) -> (starts, ends): held-out text -> original offsets
        self.compute = None           # pgw.product.local_compute.ComputeResult (LOCAL_COMPUTE)
        self.declared = ()            # declared-subject matchers (pgw.product.subjects) of this request
        self.outbound: dict | None = None
        self.restored = 0
        self.kept_withheld = 0
        self.restore_done = False

    # --- stages (real measured time) ---
    def begin(self, sid: str) -> None:
        self._open = (sid, time.perf_counter())

    def end(self, sid: str, status: str = "done") -> None:
        ms = self._carry.pop(sid, 0.0)
        if self._open is not None and self._open[0] == sid:
            ms += (time.perf_counter() - self._open[1]) * 1000
            self._open = None
        self.record(sid, status, ms)

    def record(self, sid: str, status: str, ms: float) -> None:
        self.stages[sid] = {"id": sid, "status": status, "ms": round(ms, 3)}

    def skip(self, *sids: str) -> None:
        for sid in sids:
            self.stages.setdefault(sid, {"id": sid, "status": "skipped", "ms": 0.0})

    def carry(self, sid: str, ms: float) -> None:
        """Time measured for `sid` before the stage itself begins (added to it when it ends)."""
        self._carry[sid] = self._carry.get(sid, 0.0) + ms

    def fail_open(self) -> None:
        """The request stopped: the stage running now failed."""
        if self._open is not None:
            self.end(self._open[0], "failed")

    def check(self, cid: str, status: str) -> None:
        self.checks[cid] = status

    # --- building ---
    def _entries(self):
        """Yield every `detected` entry (unbounded; the caller caps the list and counts all)."""
        if self.compute is not None:
            yield from self._compute_entries()
            return
        prot = self.prot
        if prot is None:
            return
        ents = prot.entities
        for seg, dets in zip(prot.segments, prot.detections, strict=True):
            mi, pi, fld = seg.loc
            maps = self.held_maps.get((mi, pi)) if fld == "content" else None
            text = seg.text
            # A span that a declared subject's matcher also found is reported as declared even when another detector
            # (roster, propagation) won the overlap resolution: the app declared that value.
            declared = ({(a, b, m.t) for m in self.declared for a, b in m.spans(text)}
                        if dets and self.declared else ())
            for d in dets:
                ent = ents.get((d.type, canon(d.type, text[d.start:d.end])))
                if ent is None:
                    continue
                s, e = (d.start, d.end) if maps is None else (maps[0][d.start], maps[1][d.end - 1])
                is_declared = d.source.startswith("C:") or any(t is d.type and a < d.end and d.start < b
                                                               for a, b, t in declared)
                entry = {"message": mi, "part": pi, "start": s, "end": e, "type": d.type.value,
                         "class": _class(d.type), "action": ACTION[ent.action], "placeholder": ent.replacement,
                         "source": "declared" if is_declared else "detected"}
                if fld != "content":
                    entry["field"] = fld
                yield entry

    def _compute_entries(self):
        cr, contract, prot = self.compute, self.contract, self.prot
        ents = prot.entities if prot is not None else {}

        def lookup(t, value):
            return ents.get((t, canon(t, str(value))))

        declared_keys = set()
        for path, cls, t, value in cr.fields or ():
            action, placeholder = "withhold", None
            if cls is DataClass.C1:
                declared_keys.add((t, canon(t, str(value))))
                ent = lookup(t, value)
                action = ACTION[ent.action] if ent is not None else "alias"
                placeholder = ent.replacement if ent is not None else None
            yield {"message": 0, "part": None, "field": path, "type": t.value, "class": cls.value, "action": action,
                   "placeholder": placeholder, "source": "declared"}
        for name, value in cr.outputs.items():
            spec = contract.outputs.get(name) or Output(name, DataClass.D, "INTERNAL")
            action, placeholder = {"INTERNAL": "withhold", "RAW": "allow"}.get(spec.egress), None
            if spec.egress == "TRANSFORMED":
                declared_keys.add((spec.entity, canon(spec.entity, str(value))))
                ent = lookup(spec.entity, value)
                action = ACTION[ent.action] if ent is not None else "alias"
                placeholder = ent.replacement if ent is not None else None
            yield {"message": 0, "part": None, "field": f"outputs.{name}", "type": spec.entity.value,
                   "class": spec.derived_from.value, "action": action, "placeholder": placeholder,
                   "source": "computed"}
        for t in cr.masked or ():
            yield {"message": 0, "part": None, "field": "instruction", "type": t.value, "class": _class(t),
                   "action": "withhold", "placeholder": f"[{t.value}]", "source": "detected"}
        if prot is None:
            return
        at = cr.instruction_at
        for seg, dets in zip(prot.segments, prot.detections, strict=True):
            maps = self.held_maps.get(seg.loc[:2]) if seg.loc[2] == "content" else None
            for d in dets:
                key = (d.type, canon(d.type, seg.text[d.start:d.end]))
                ent = ents.get(key)
                if ent is None or key in declared_keys:
                    continue
                s, e = (d.start, d.end) if maps is None else (maps[0][d.start], maps[1][d.end - 1])
                yield {"message": 0, "part": None,
                       "field": "instruction" if at is not None and at[0] <= s and e <= at[1] else "prompt",
                       "type": d.type.value, "class": _class(d.type), "action": ACTION[ent.action],
                       "placeholder": ent.replacement,
                       "source": "declared" if d.source.startswith("C:") else "detected"}

    def build(self, outcome: str, error: dict | None = None) -> dict:
        detected: list[dict] = []
        replaced = removed = total = 0
        by_type: dict[str, int] = {}
        for entry in self._entries():
            total += 1
            if total <= MAX_DETECTED:
                detected.append(entry)
            a = entry["action"]
            replaced += a in _REPLACED
            removed += a in _REMOVED
            by_type[entry["type"]] = by_type.get(entry["type"], 0) + 1
        outbound = self.outbound
        if outbound is not None and (error is not None or outbound["destination"] == "in_house"):
            # An error response never carries request text. Neither does the in-house route: what went there is the
            # caller's UNPROTECTED input (C3 included), and the gateway never returns C3 to the app.
            outbound = {**outbound, "messages": None}
        stages = [self.stages[s] for s in STAGES if s in self.stages]
        c = self.contract
        return {
            "version": VERSION,
            "request_id": self.rid,
            "purpose": self.purpose,
            "contract": c.ref if c is not None else None,
            "route": self.route,
            "outcome": outcome,
            "input_source": {"id": self.input_source, "verified": self.verified},
            "stages": stages,
            "detected": detected,
            "truncated": total > MAX_DETECTED,
            "counts": {"replaced": replaced, "removed": removed, "by_type": by_type},
            "outbound": outbound,
            "restore": ({"restored": self.restored, "kept_withheld": self.kept_withheld}
                        if self.restore_done else None),
            "checks": [{"id": k, "status": v, "label": CHECKS[k]} for k, v in self.checks.items()],
            "error": error,
        }


def held_out(text: str, held: tuple, n_org: int, sentinel: int, track: bool):
    """Replace organisational contacts and public constants by one private-use sentinel character each (contacts in
    any case, constants exactly as whole numbers), after removing sentinel characters already in the text.

    Returns (new text, maps). With track=False maps is None and the work is exactly the original substitution. With
    track=True maps = (starts, ends): for each character of the new text, the original offsets it covers, so a span
    [s, e) of the new text is [starts[s], ends[e - 1]) of the original. Both modes produce the same text."""
    def rx(i, oc):
        if i < n_org:
            return re.compile(re.escape(oc), re.IGNORECASE)
        return re.compile(r"(?<![\w.,])" + re.escape(oc) + r"(?![\w]|[.,]\d)")

    def ours(c):
        return sentinel <= ord(c) < sentinel + 0x100
    if not track or any(not oc for oc in held):
        t = "".join(c for c in text if not ours(c))
        for i, oc in enumerate(held):
            t = rx(i, oc).sub(chr(sentinel + i), t)
        return t, None
    keep = [i for i, c in enumerate(text) if not ours(c)]
    t = "".join(text[i] for i in keep)
    starts, ends = keep, [i + 1 for i in keep]
    for i, oc in enumerate(held):
        out, ns, ne, pos = [], [], [], 0
        for m in rx(i, oc).finditer(t):
            out.append(t[pos:m.start()])
            ns += starts[pos:m.start()]
            ne += ends[pos:m.start()]
            out.append(chr(sentinel + i))
            ns.append(starts[m.start()])
            ne.append(ends[m.end() - 1])
            pos = m.end()
        if not out:
            continue
        out.append(t[pos:])
        t = "".join(out)
        starts, ends = ns + starts[pos:], ne + ends[pos:]
    return t, (starts, ends)
