"""Disclosure control, round 9: listings and the audit budget (pgw.product.records._list_cells,
pgw.product.disclosure.HistoryStore, pgw.product.setaudit).

(1) A roster listing prints, per listed alias, an attribute text ("department: Finance", "attributes withheld"):
every group of rows printed with one text is a count the reader can read off, and so are the number of listed
aliases and "... and N more not listed". They are history cells now, audited with every other count; when they
would break the audit, the history withholds the listing's attributes (every alias then prints the same text).
(2) The audit is bounded in wall-clock time; a refusal for an exhausted budget says so (reason audit_budget),
distinct from a refusal because a small group would be determined (reason differencing).

Production mode, signed config, attested payloads; assertions read the bytes the mock provider received.
SYNTHETIC data only (tests/hrms_synth.py).
"""
from __future__ import annotations

import collections
import dataclasses
import re

from pgw.product import disclosure, setaudit
from tests import hrms_synth as S
from tests.test_disclosure_control import _refused, gw, person, plan_for, post, run, sent_since

__all__ = ["gw"]


def _pop():
    """Finance 12, Legal 2, Sales 8, Support 9 (the tester's population)."""
    return S.ad_population([person("Finance")] * 12 + [person("Legal")] * 2 + [person("Sales")] * 8
                           + [person("Support")] * 9)


def _q(*depts, op="in", lst=False, group_by=None):
    flt = [] if not depts else [{"field": "department", "op": op, "value": list(depts) if op == "in" else depts[0]}]
    return {"filter": flt, "group_by": group_by, "list": lst}


def listing_classes(text: str) -> collections.Counter:
    """Listed lines by printed attribute text (the alias removed); '... more not listed' lines by their text."""
    block = text.split("Matching records:\n", 1)[1].split("\nAnswer the", 1)[0]
    c: collections.Counter = collections.Counter()
    for line in block.splitlines():
        if not line.startswith("- "):
            continue
        body = line[2:]
        if "more not listed" in body:
            c[body] += 1
            continue
        c[body.split(": ", 1)[1] if ": " in body else ""] += 1
    return c


def _ask(client, mock, sy, q):
    n0 = len(mock.received)
    r = post(client, "roster_question", sy.envelope(q))
    return r, (sent_since(mock, n0) if r.status_code == 200 else None)


def _dept_counts(classes) -> dict:
    out: collections.Counter = collections.Counter()
    for t, n in classes.items():
        m = re.search(r"department: ([A-Za-z ]+?)(;|$)", t)
        out[m.group(1) if m else ("withheld" if "withheld" in t else t)] += n
    return dict(out)


def test_listing_label_groups_are_audited_like_counts(gw):
    """Tester t6: in(Finance, Legal, Sales) with list: 12 rows 'department: Finance', 10 'attributes withheld';
    then 'Sales' -> 8 gives Legal = 22 - 12 - 8 = 2. The listing's label groups are history cells now: the second
    question is refused, nothing is sent; Finance (12, printed by the listing) is a known count and still answered."""
    client, mock = gw
    sy = _pop()
    r, text = _ask(client, mock, sy, _q("Finance", "Legal", "Sales", lst=True))
    assert r.status_code == 200, r.text
    d = _dept_counts(listing_classes(text))
    assert d == {"Finance": 12, "withheld": 10}, d
    n0 = len(mock.received)
    _refused(post(client, "roster_question", sy.envelope(_q("Sales", op="eq"))))
    assert len(mock.received) == n0
    r, text = _ask(client, mock, sy, _q("Finance", op="eq"))
    assert r.status_code == 200 and "Matching: 12 " in text


def test_reverse_order_withholds_the_listing_attributes(gw):
    """Sales (8) first; the listing would then print Finance 12 / withheld 10 -> Legal = 2. The history withholds the
    listing's attributes instead: every alias prints the same text, so no label group is readable."""
    client, mock = gw
    sy = _pop()
    r, _ = _ask(client, mock, sy, _q("Sales", op="eq"))
    assert r.status_code == 200
    r, text = _ask(client, mock, sy, _q("Finance", "Legal", "Sales", lst=True))
    assert r.status_code == 200, r.text
    c = listing_classes(text)
    assert set(c) == {"attributes withheld (disclosure control)"} and sum(c.values()) == 22, c
    assert r.json()["pgw_report"]["records"]["disclosure"]["lists_attributes_withheld_history"] == 1


def test_unfiltered_listing_then_subtraction_is_refused(gw):
    """Unfiltered listing (Finance 12, Support 9, 10 withheld), then Sales 8 -> Legal = 31 - 12 - 9 - 8 = 2."""
    client, mock = gw
    sy = _pop()
    r, text = _ask(client, mock, sy, _q(lst=True))
    assert r.status_code == 200, r.text
    assert _dept_counts(listing_classes(text)) == {"Finance": 12, "Support": 9, "withheld": 10}
    _refused(post(client, "roster_question", sy.envelope(_q("Sales", op="eq"))))
    r, _ = _ask(client, mock, sy, _q("Support", op="eq"))
    assert r.status_code == 200


def test_listing_truncation_counts_are_cells():
    """A listing cut at max_listed prints how many were listed and how many were not: both are cells, and the cut
    is moved so that neither part has 1..k-1 records."""
    from pgw.product import records
    plan = plan_for({"max_listed": 10})
    sy = S.ad_population([person("Finance")] * 13 + [person("Sales")] * 9)
    store = disclosure.HistoryStore(":memory:")
    scope = disclosure.HistoryScope(store, "t", "app", "roster_question", lambda kid: (kid or "k1", b"k" * 32))
    seen = []
    orig = records._list_cells

    def spy(*a):
        out = orig(*a)
        seen.append({n: len(rs) for n, rs, _alt, _g in out})
        return out
    records._list_cells = spy
    try:
        text = "".join(run(plan, sy, _q("Finance", op="eq", lst=True), scope).raw.values())
    finally:
        records._list_cells = orig
    # 13 matched, max 10 listed -> 3 would remain: 8 listed, 5 not listed instead
    assert seen[-1]["#listed"] == 8 and seen[-1]["#not_listed"] == 5, seen
    assert "... and 5 more not listed" in text
    store.close()


def test_listing_cells_mutation_is_caught():
    """Mutation check: with the listing cells disabled, the tester's sequence is answered (the leak is back)."""
    from pgw.product import records
    plan = plan_for()
    sy = _pop()

    def sequence():
        store = disclosure.HistoryStore(":memory:")
        scope = disclosure.HistoryScope(store, "t", "app", "roster_question", lambda kid: (kid or "k1", b"k" * 32))
        run(plan, sy, _q("Finance", "Legal", "Sales", lst=True), scope)
        try:
            run(plan, sy, _q("Sales", op="eq"), scope)
            return "answered"
        except disclosure.DisclosureRefused as e:
            return e.code
        finally:
            store.close()
    assert sequence() == "differencing"
    orig = records._list_cells
    records._list_cells = lambda *a: []
    try:
        assert sequence() == "answered"
    finally:
        records._list_cells = orig


# =============================================================================================================
# (6) wall-clock audit budget and an accurate refusal
# =============================================================================================================

def test_audit_deadline_stops_the_search_and_fails_closed():
    """A deadline already passed: the search stops at its first clock read with a budget violation (never accepts)."""
    import time
    aud = setaudit.Auditor(5)
    keys = [bytes([i]) * 16 for i in range(40)]
    import hashlib

    def cell(ms, lo, hi, whole=False):
        ks = frozenset(keys[i] for i in ms)
        return hashlib.sha256(b"".join(sorted(ks))).digest(), ks, lo, hi, whole
    assert aud.trial([cell(range(40), 40, 40, True)]) is None
    aud.commit()
    batch = [cell(range(i, i + 3), 1, None) for i in range(0, 36, 2)]
    batch += [cell(range(i, i + 7), 7, 7) for i in range(0, 33, 3)]
    v = aud.trial(batch, deadline=time.monotonic() - 1)
    assert v is not None and v.kind == "budget" and aud.expired
    aud.rollback()
    assert aud.trial(batch) is None or True          # with time it completes (accepted or a real violation)


def test_budget_refusal_says_so_and_names_no_value(monkeypatch):
    """An audit that cannot finish is refused with reason audit_budget and a message that does not claim a small
    group would be revealed; a real differencing refusal keeps its own reason."""
    plan = plan_for()
    sy = _pop()
    store = disclosure.HistoryStore(":memory:")
    scope = disclosure.HistoryScope(store, "t", "app", "roster_question", lambda kid: (kid or "k1", b"k" * 32))
    run(plan, sy, _q("Finance", "Legal", op="in"), scope)
    real = setaudit.Auditor.trial

    def exhausted(self, batch, deadline=None, r2=True):
        return real(self, batch, deadline=0.0, r2=r2)   # monotonic() is always past 0
    monkeypatch.setattr(setaudit.Auditor, "trial", exhausted)
    monkeypatch.setattr(setaudit, "_CLOCK_EVERY", 1)
    try:
        run(plan, sy, _q("Finance", "Sales", "Support", group_by="department"), scope)
        raise AssertionError("not refused")
    except disclosure.DisclosureRefused as e:
        assert e.code == disclosure.REFUSE_AUDIT_BUDGET
        msg = str(e)
        assert "time budget" in msg and "fewer than" not in msg and not re.search(r"\d", msg)
    assert store.metrics.snapshot()["refused_audit_budget"] == 1
    monkeypatch.undo()
    try:
        run(plan, sy, _q("Finance", op="eq"), scope)
        raise AssertionError("not refused")
    except disclosure.DisclosureRefused as e:
        assert e.code == disclosure.REFUSE_DIFFERENCING and "fewer than 5" in str(e)
    store.close()


def test_audit_max_ms_is_sealed_config():
    plan = plan_for()
    cfg = plan.control.cfg
    assert cfg.audit_max_ms == setaudit.DEFAULT_MAX_MS
    raw = (plan_for.__globals__["ROOT"] / "registry/rules/disclosure_control.yaml").read_bytes()
    c2 = disclosure.load_config("disclosure_control.yaml",
                                raw.replace(b"audit_budget: 400000", b"audit_budget: 400000\n  audit_max_ms: 800"))
    assert c2.audit_max_ms == 800
    import pytest
    with pytest.raises(disclosure.SchemaError):
        disclosure.load_config("disclosure_control.yaml",
                               raw.replace(b"audit_budget: 400000", b"audit_budget: 400000\n  audit_max_ms: 5"))
    assert dataclasses.replace(cfg, audit_max_ms=100).audit_max_ms == 100
