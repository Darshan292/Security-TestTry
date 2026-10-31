"""Brute-force verification of the cross-query audit (pgw.product.setaudit; disclosure.HistoryStore).

Threat model: the external provider sees every outbound request of the tenant and keeps them. It knows every
predicate it was answered (and so, worst case, every set relation between the answers' record sets), every printed
number and every suppression marker, and reasons with linear algebra, integrality and the markers' bounds.

Two independent brute-force adversaries, both exhaustive on small populations, check what was ACCEPTED:

  linear  -- exact rational linear algebra over individual records: for EVERY set W of 1..k-1 records (all of
             them are enumerated), is its size a linear combination of the printed counts? (covers subtraction of
             printed totals from suppressed cells, pairs, triples, any number of answers)
  bounds  -- every integer assignment of record counts to the atoms of the answers' sets that satisfies every
             printed count exactly and every marker's bound (enumerated by depth-first search): does any
             suppressed cell of 1..k-1 records, or any atom of 1..k-1 records, take only one value?

The auditor-level test feeds random set families; the gateway-level test runs random roster query sequences
through records.run with a durable history and parses what would leave. SYNTHETIC data only.
"""
from __future__ import annotations

import hashlib
import itertools
import random
import re
from fractions import Fraction

import pytest

from pgw.product import disclosure, setaudit
from tests import hrms_synth as S
from tests.test_disclosure_control import leaving, parse_roster, person, plan_for, run

K = 5


# =============================================================================================================
# brute-force adversaries
# =============================================================================================================

class Span:
    """Exact rational row space over records 0..n-1."""

    def __init__(self, n: int):
        self.n = n
        self.rows: dict = {}          # pivot -> row (list of Fractions), reduced

    def _reduce(self, v):
        v = list(v)
        for p, r in self.rows.items():
            if v[p]:
                c = v[p]
                v = [a - c * b for a, b in zip(v, r, strict=True)]
        return v

    def add(self, members) -> None:
        v = self._reduce([Fraction(1 if i in members else 0) for i in range(self.n)])
        piv = next((i for i, x in enumerate(v) if x), None)
        if piv is None:
            return
        v = [x / v[piv] for x in v]
        for p, r in self.rows.items():
            if r[piv]:
                c = r[piv]
                self.rows[p] = [a - c * b for a, b in zip(r, v, strict=True)]
        self.rows[piv] = v

    def contains(self, members) -> bool:
        return not any(self._reduce([Fraction(1 if i in members else 0) for i in range(self.n)]))


def linear_leaks(n: int, exact_sets: list, k: int, wholes: list) -> list:
    """Every set of 1..k-1 records (out of n) whose size the exact counts determine, except a whole export."""
    sp = Span(n)
    for s in exact_sets:
        sp.add(s)
    out = []
    for r in range(1, k):
        for w in itertools.combinations(range(n), r):
            ws = frozenset(w)
            if ws not in wholes and sp.contains(ws):
                out.append(ws)
    return out


def atoms_of(n: int, cells: list) -> list:
    sig: dict = {}
    for i in range(n):
        key = tuple(i in c for c in cells)
        if any(key):
            sig.setdefault(key, set()).add(i)
    return [frozenset(a) for a in sig.values()]


def bound_pins(n: int, cells: list, k: int, cap: int, wholes: list) -> list:
    """cells: [(members, lo, hi or None)]. Enumerate every integer atom-count vector satisfying every cell; return the
    protected items (suppressed cells and atoms of 1..k-1 records, not a whole export) that take one value only."""
    sets = [c[0] for c in cells]
    atoms = atoms_of(n, sets)
    na = len(atoms)
    incl = [[j for j, c in enumerate(cells) if atoms[i] <= c[0]] for i in range(na)]
    last = {}
    for i in range(na):
        for j in incl[i]:
            last[j] = i
    items = [(f"cell{j}", [i for i in range(na) if atoms[i] <= c[0]]) for j, c in enumerate(cells)
             if not (c[2] is not None and c[1] == c[2]) and 1 <= len(c[0]) <= k - 1 and c[0] not in wholes]
    items += [(f"atom{i}", [i]) for i in range(na) if 1 <= len(atoms[i]) <= k - 1 and atoms[i] not in wholes]
    seen = {name: set() for name, _ in items}
    sums = [0] * len(cells)
    x = [0] * na
    nodes = [0]

    def ok_partial(i):
        for j in incl[i]:
            lo, hi = cells[j][1], cells[j][2]
            if hi is not None and sums[j] > hi:
                return False
            if last[j] == i and sums[j] < lo:
                return False
        return True

    def dfs(i):
        nodes[0] += 1
        assert nodes[0] < 3_000_000, "brute force too large"
        if i == na:
            for name, ats in items:
                seen[name].add(sum(x[a] for a in ats))
            return
        for v in range(cap + 1):
            x[i] = v
            for j in incl[i]:
                sums[j] += v
            good = ok_partial(i)
            over = any(cells[j][2] is not None and sums[j] > cells[j][2] for j in incl[i])
            if good:
                dfs(i + 1)
            for j in incl[i]:
                sums[j] -= v
            if over:
                break
        x[i] = 0
    dfs(0)
    return [name for name, vals in seen.items() if len(vals) < 2]


# =============================================================================================================
# auditor level: random set families
# =============================================================================================================

def _key(i: int) -> bytes:
    return hashlib.sha256(b"rec%d" % i).digest()[:16]


def _cell(members, lo, hi, whole=False):
    keys = frozenset(_key(i) for i in members)
    return (hashlib.sha256(b"".join(sorted(keys))).digest(), keys, lo, hi, whole)


def _marker_bounds(rng, size, k):
    if size < k:
        return rng.choice([(1, k - 1), (1, None)])
    return rng.choice([(1, None), (k, None)])


@pytest.mark.parametrize("seed", range(6))
def test_auditor_random_families_never_determine_a_small_group(seed):
    rng = random.Random(7000 + seed)
    accepted = refused = checked = 0
    for _trial in range(25):
        n = rng.randrange(6, 11)
        whole = frozenset(range(n))
        aud = setaudit.Auditor(K)
        assert aud.trial([_cell(whole, n, n, True)]) is None
        aud.commit()
        cells = [(whole, n, n)]
        wholes = [whole]
        for _step in range(rng.randrange(3, 8)):
            batch, plain = [], []
            if rng.random() < 0.25:          # a refreshed export: a joiner or leaver or two
                w = frozenset(i for i in range(n) if rng.random() < 0.85)
                if w:
                    batch.append(_cell(w, len(w), len(w), True))
                    plain.append((w, len(w), len(w)))
            for _ in range(rng.randrange(1, 4)):
                m = frozenset(i for i in range(n) if rng.random() < rng.choice([0.3, 0.5, 0.7]))
                if not m:
                    continue
                lo, hi = (len(m), len(m)) if rng.random() < 0.55 else _marker_bounds(rng, len(m), K)
                batch.append(_cell(m, lo, hi))
                plain.append((m, lo, hi))
            if not batch:
                continue
            if aud.trial(batch) is not None:
                aud.rollback()
                refused += 1
                continue
            aud.commit()
            accepted += 1
            cells += plain
            wholes += [c[0] for c, b in zip(plain, batch, strict=True) if b[4]]
            exact = [c[0] for c in cells if c[2] is not None and c[1] == c[2]]
            assert not linear_leaks(n, exact, K, wholes), (seed, cells)
            assert not bound_pins(n, cells, K, n + K, wholes), (seed, cells)
            checked += 1
    assert accepted > 15 and refused > 5 and checked == accepted


def test_auditor_brute_force_is_not_vacuous():
    """The adversaries find the tester's subtraction, a three-way combination and the five-singleton pin."""
    n = 22
    F, L, Sa = frozenset(range(12)), frozenset({12, 13}), frozenset(range(14, 22))
    T = F | L | Sa
    assert linear_leaks(n, [T, F, Sa], K, [T]) == [L]
    # three answers, no pair of which reveals anything: (A u B), (B u C), (A u C) printed -> each determined
    A, B, C = frozenset({0, 1}), frozenset({2, 3, 4}), frozenset({5, 6, 7})
    assert set(linear_leaks(8, [A | B, B | C, A | C], K, [])) >= {A, B, C}
    xs = [frozenset({i}) for i in range(5)]
    pins = bound_pins(15, [(frozenset(range(15)), 15, 15), *[(x, 1, None) for x in xs],
                           (frozenset(range(5)), 5, 5)], K, 20, [])
    assert len([p for p in pins if p.startswith("cell")]) == 5


def test_auditor_refuses_the_three_question_combination():
    """A u B (5), B u C (6), A u C (5): no pair differs by fewer than k records, the three together give A = 2."""
    aud = setaudit.Auditor(K)
    A, B, C = [0, 1], [2, 3, 4], [5, 6, 7]
    assert aud.trial([_cell(A + B, 5, 5)]) is None
    aud.commit()
    assert aud.trial([_cell(B + C, 6, 6)]) is None
    aud.commit()
    v = aud.trial([_cell(A + C, 5, 5)])
    assert v is not None and v.kind == "linear" and v.cells == {0}
    aud.rollback()
    assert aud.trial([_cell(B + C, 6, 6)]) is None             # a repeat is still fine after the rollback
    aud.commit()


# =============================================================================================================
# gateway level: random roster query sequences through records.run with a durable history
# =============================================================================================================

DEPTS = ["Finance", "Legal", "Sales"]


def _random_query(rng) -> dict:
    flt = []
    r = rng.random()
    if r < 0.3:
        flt.append({"field": "department", "op": "in", "value": rng.sample(DEPTS, rng.randrange(1, 3))})
    elif r < 0.5:
        flt.append({"field": "department", "op": rng.choice(["eq", "ne"]), "value": rng.choice(DEPTS)})
    elif r < 0.7:
        flt.append({"field": "enabled", "op": "eq", "value": rng.choice([True, False])})
    elif r < 0.8:
        flt += [{"field": "department", "op": "eq", "value": rng.choice(DEPTS)},
                {"field": "enabled", "op": "eq", "value": rng.choice([True, False])}]
    return {"filter": flt, "group_by": rng.choice([None, "department", "account_state"]), "list": False}


def _sets(people: list, q: dict) -> dict:
    """The record sets behind every count of a roster answer, computed independently of the gateway."""
    def val(p, f):
        if f == "department":
            return p["dept"]
        return p["enabled"]          # None: the attribute is absent
    n = len(people)
    matched, missing = set(), set()
    for i, p in enumerate(people):
        state = 1
        for c in q["filter"]:
            v = val(p, c["field"])
            if v is None:
                state = -1
                break
            ok = v == c["value"] if c["op"] == "eq" else v != c["value"] if c["op"] == "ne" else v in c["value"]
            if not ok:
                state = 0
                break
        if state == 1:
            matched.add(i)
        elif state == -1:
            missing.add(i)
    groups = {}
    if q["group_by"]:
        for i in matched:
            p = people[i]
            lab = p["dept"] if q["group_by"] == "department" else (
                "disabled" if p["enabled"] is False else "enabled")
            groups.setdefault(lab, set()).add(i)
    return {"total": frozenset(range(n)), "matched": frozenset(matched), "missing": frozenset(missing),
            "groups": {g: frozenset(s) for g, s in groups.items()}}


def _bounds(tok: str, k: int):
    if re.fullmatch(r"\d+", tok):
        return int(tok), int(tok)
    if tok == f"fewer than {k}":
        return 1, k - 1
    if tok == "withheld":
        return k, None
    assert tok == "suppressed", tok
    return 1, None


@pytest.mark.parametrize("seed", range(4))
def test_random_roster_sequences_never_determine_a_small_group(seed):
    """Random query sequences over random small populations, each through records.run with a durable history (one
    tenant, one dataset: every query in one scope). After every answered query, neither brute-force adversary finds
    a group of 1..k-1 records whose size is determined by everything printed so far."""
    plan = plan_for()
    rng = random.Random(9100 + seed)
    answered = refused = suppressed_seen = 0
    for _seq in range(12):
        people = []
        for d in DEPTS:
            people += [person(d, rng.choice([True, True, False, None])) for _ in range(rng.randrange(1, 5))]
        n = len(people)
        sy = S.ad_population(people, seed=rng.randrange(10 ** 6))
        store = disclosure.HistoryStore(":memory:")
        scope = disclosure.HistoryScope(store, "t", "app", "roster_question", lambda kid: (kid or "k1", b"k" * 32))
        known: list = []
        whole = frozenset(range(n))
        for _q in range(rng.randrange(3, 7)):
            q = _random_query(rng)
            try:
                text = leaving(run(plan, sy, q, scope))
            except disclosure.DisclosureRefused:
                refused += 1
                continue
            answered += 1
            p = parse_roster(text)
            sets = _sets(people, q)
            gb_sets = sets["groups"]
            assert set(p["groups"]) == set(gb_sets) or not q["group_by"], (p, gb_sets)
            for name in ("total", "matched", "missing"):
                if name == "missing" and not sets["missing"]:
                    continue
                if sets[name]:
                    known.append((sets[name], *_bounds(p[name], K)))
            for g, tok in p["groups"].items():
                known.append((gb_sets[g], *_bounds(tok, K)))
            suppressed_seen += sum(1 for c in known if not (c[2] is not None and c[1] == c[2]))
            exact = [c[0] for c in known if c[2] is not None and c[1] == c[2]]
            for c in known:      # what is printed is true
                assert c[1] <= len(c[0]) and (c[2] is None or len(c[0]) <= c[2]), (c, text)
            assert not linear_leaks(n, exact, K, [whole]), (seed, q, text)
            dedup = {}
            for c in known:
                lo, hi = dedup.get(c[0], (0, None))
                dedup[c[0]] = (max(lo, c[1]), c[2] if hi is None else (hi if c[2] is None else min(hi, c[2])))
            assert not bound_pins(n, [(s, lo, hi) for s, (lo, hi) in dedup.items()], K, n + K, [whole]), (seed, q)
        store.close()
    print("stats", answered, refused, suppressed_seen)
    assert answered > 30 and refused > 0 and suppressed_seen > 20, (answered, refused, suppressed_seen)


# =============================================================================================================
# gateway level with listings: every printed label group of a listing is a count the adversary reads
# =============================================================================================================

ROLLUP = {"Finance": "Corporate functions", "Legal": "Corporate functions", "Sales": "Customer-facing"}


def _listing_sets(text: str, people: list, sets: dict, p: dict, q: dict) -> list:
    """The record sets behind a listing's label groups, computed independently: rows printed 'department: D' are the
    matched people of D (or of the departments D rolls up, registry hierarchy); rows printed without a department are the matched people of a department whose group count
    is suppressed in the same answer (the value is withheld), or every unclaimed row when the column is dropped;
    'attributes withheld ...' rows are the rest. Every
    group's line count must equal its set's size (the model is checked, not assumed)."""
    block = text.split("Matching records:\n", 1)[1].split("\nAnswer the", 1)[0]
    by: dict = {}
    for line in block.splitlines():
        if line.startswith("- "):
            body = line[2:]
            assert "more not listed" not in body
            by.setdefault(body.split(": ", 1)[1] if ": " in body else "", []).append(body)
    if len(by) == 1 and "none" in by.get("", [""])[0]:
        return []
    matched = sets["matched"]
    out, claimed = [], set()
    for t in by:
        m = re.fullmatch(r"department: (.+)", t)
        if m:
            s = frozenset(i for i in matched if m.group(1) in (people[i]["dept"], ROLLUP[people[i]["dept"]]))
            out.append((t, s))
            claimed |= s
    hidden = {g for g, tok in p["groups"].items() if tok != "" and not re.fullmatch(r"\d+", tok)} \
        if q["group_by"] == "department" else set()
    if "" in by:
        s = frozenset(i for i in matched if i not in claimed and (people[i]["dept"] in hidden or not hidden))
        out.append(("", s))
        claimed |= s
    rest = [t for t in by if t not in {x for x, _ in out}]
    assert len(rest) <= 1, by.keys()
    if rest:
        out.append((rest[0], frozenset(matched - claimed)))
    for t, s in out:
        assert len(by[t]) == len(s), (t, len(by[t]), len(s), text)
    return [s for _t, s in out]


@pytest.mark.parametrize("seed", range(4))
def test_random_roster_sequences_with_listings_never_determine_a_small_group(seed):
    """As above, with listings (show: department): each label group of a listing, the 'attributes withheld' group
    included, enters the adversary's knowledge as an exact count. Neither brute-force adversary may find a group
    of 1..k-1 records determined after any answered query."""
    plan = plan_for({"show": ["department"]})
    rng = random.Random(9300 + seed)
    answered = refused = labelled = 0
    for _seq in range(14):
        people = []
        for d in DEPTS:
            people += [person(d, rng.choice([True, True, False, None])) for _ in range(rng.randrange(3, 10))]
        n = len(people)
        sy = S.ad_population(people, seed=rng.randrange(10 ** 6))
        store = disclosure.HistoryStore(":memory:")
        scope = disclosure.HistoryScope(store, "t", "app", "roster_question", lambda kid: (kid or "k1", b"k" * 32))
        known: list = []
        whole = frozenset(range(n))
        for _q in range(rng.randrange(3, 7)):
            q = _random_query(rng)
            q["list"] = rng.random() < 0.6
            try:
                text = leaving(run(plan, sy, q, scope))
            except disclosure.DisclosureRefused:
                refused += 1
                continue
            answered += 1
            p = parse_roster(text)
            sets = _sets(people, q)
            for name in ("total", "matched", "missing"):
                if name == "missing" and not sets["missing"]:
                    continue
                if sets[name]:
                    known.append((sets[name], *_bounds(p[name], K)))
            for g, tok in p["groups"].items():
                known.append((sets["groups"][g], *_bounds(tok, K)))
            if q["list"]:
                ls = _listing_sets(text, people, sets, p, q)
                labelled += len(ls) > 1
                known += [(s, len(s), len(s)) for s in ls if s]
            exact = [c[0] for c in known if c[2] is not None and c[1] == c[2]]
            assert not linear_leaks(n, exact, K, [whole]), (seed, q, text)
            dedup = {}
            for c in known:
                lo, hi = dedup.get(c[0], (0, None))
                dedup[c[0]] = (max(lo, c[1]), c[2] if hi is None else (hi if c[2] is None else min(hi, c[2])))
            assert not bound_pins(n, [(s, lo, hi) for s, (lo, hi) in dedup.items()], K, n + K, [whole]), (seed, q)
        store.close()
    print("stats", answered, refused, labelled)
    assert answered > 25 and refused > 0 and labelled >= 5, (answered, refused, labelled)


def test_incremental_audit_matches_a_full_audit():
    """The auditor searches R1 only where a trial changed rows (and nests tentative levels); after every step its R1
    verdict equals that of a fresh auditor that searches the same state in full. (R2 is a certificate search whose
    candidate family depends on the elimination order, so it is compared only for soundness, by the brute-force
    tests above.)"""
    rng = random.Random(4242)
    compared = disagreements = 0
    for _ in range(150):
        n = rng.randrange(8, 30)
        whole = _cell(range(n), n, n, True)
        aud = setaudit.Auditor(K)
        assert aud.trial([whole]) is None
        aud.commit()
        hist = [whole]
        for _step in range(rng.randrange(3, 9)):
            batch = []
            for _c in range(rng.randrange(1, 4)):
                m = [i for i in range(n) if rng.random() < rng.choice([0.15, 0.4, 0.7])]
                if m:
                    lo, hi = (len(m), len(m)) if rng.random() < 0.6 else _marker_bounds(rng, len(m), K)
                    batch.append(_cell(m, lo, hi))
            if not batch:
                continue
            aud.begin()                      # an enclosing level, as the history's suppression uses
            v = aud.trial(batch, r2=False)
            fresh = setaudit.Auditor(K)
            for c in hist:
                fresh.add(*c)
            fresh._normalise()
            fv = fresh.trial(batch, r2=False)
            compared += 1
            disagreements += (v is None) != (fv is None)
            if v is None and aud.check_r2() is not None:
                v = "bounds"
            if v is None:
                aud.commit()
                aud.commit()
                hist += batch
            else:
                aud.rollback()
                aud.rollback()
    assert compared > 500 and disagreements == 0
