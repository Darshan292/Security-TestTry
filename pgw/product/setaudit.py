"""Cross-query audit of released counts over keyed record sets (the disclosure-control history,
pgw.product.disclosure.HistoryStore; docs/HRMS_CONTRACTS.md "Small groups", SECURITY_BOUNDARY.md §12).

Model. Every count a history scope has ever revealed is a CELL: a set S of 16-byte keyed record hashes and what the
reader learned about |S| -- an exact number (lo = hi = |S|), or an interval [lo, hi] (a suppression marker: zeros are
printed, so "suppressed" is [1, inf), "fewer than k" is [1, k-1], "withheld" is [k, inf); a rounded count: the
integers that round to the printed value). The auditor keeps

  * the ATOMS: the partition of every key seen into classes of keys with the same membership in every cell. Every
    cell is a union of atoms;
  * L, the linear span of the exact cells' indicator vectors over the atoms, in reduced row echelon form modulo the
    prime 2^61-1 (sparse rows; pivots preferably on atoms with >= k records).

The adversary is assumed to know every set relation between the cells (which atoms exist: empty regions are assumed
known, the conservative choice), every exact count and every interval, and to reason with linear algebra,
integrality, non-negativity and the intervals. After every accepted batch of cells two properties hold:

  R1 (linear; exhaustive): no union W of atoms with 1 <= |W| <= k-1 has its size determined by the exact counts
     (1_W in L), unless W is exactly a whole export printed as such (an export's own record total). Note what this
     covers: every pairwise or three-way union / difference of printed counts across any number of answers, a
     suppressed cell recovered by subtraction ("22 - 12 - 8 = 2"), a joiner between two exports, the never-printed
     "did not match" cell. W lies in L n span(small atoms), whose basis is the set of rows with a small pivot once
     no small-pivot row has an entry on a large atom; W is then a 0/1 sum of at most k-1 of those rows (its
     coefficients are its values on the pivots), searched exhaustively per connected component of rows.
  R2 (bounds; certified): every interval cell whose true size is 1..k-1 and every atom of 1..k-1 records has a
     verified alternative: an integer atom-count vector, different on that cell / atom, that satisfies every exact
     count exactly, every interval and non-negativity. Candidates are kernel vectors of L (one per free atom, and
     sums / differences of two) with small integer entries; each candidate is verified in exact integer arithmetic.
     A protected item without a certificate is treated as pinned (the five suppressed singletons whose union is
     printed as 5). Sound; possibly conservative (a certificate may exist outside the candidate family).

Both checks fail closed: an exhausted search budget is a violation. Modular arithmetic can only add dependencies that
do not hold over the rationals (extra refusals); every dependency over the rationals holds modulo p unless p divides
a denominator of the elimination, which for 0/1 systems of this size is not a practical concern (stated in
SECURITY_BOUNDARY.md §12). Not covered: a union of two or more atoms that is neither a cell nor linearly determined
and is pinned only through interval bounds; inference from the suppression algorithm itself; background knowledge.

Cost: one exact cell is one sparse row reduction, O(rank x row length); refinement is O(|S|) dictionary operations.
Bounded by the sealed per-scope caps on cells, stored keys and atoms (HistoryStore)."""
from __future__ import annotations

from dataclasses import dataclass, field

P = (1 << 61) - 1
_HALF = P // 2
_MISSING = object()
MAX_ENTRY = 8                 # a candidate move with a larger entry is not used as a certificate
DEFAULT_BUDGET = 400_000      # search steps per check; exhausted -> violation (fail closed)


def _signed(r: int) -> int:
    return r if r <= _HALF else r - P


@dataclass
class _Cell:
    atoms: set
    n: int                    # true size
    lo: int
    hi: int | None            # None: unbounded
    whole: bool

    @property
    def exact(self) -> bool:
        return self.hi is not None and self.lo == self.hi


@dataclass
class Violation:
    """Why a batch was not accepted: `kind` linear | bounds | budget; `cells` the indexes of the batch cells whose
    record sets touch the problem (the caller may suppress one of them and retry)."""
    kind: str
    cells: set = field(default_factory=set)


class Auditor:
    """In-memory audit state of one history scope. `trial(batch)` applies a batch tentatively and returns None (then
    `commit()`) or a Violation (then `rollback()`). A batch is a list of (digest, keys, lo, hi, whole)."""

    def __init__(self, k: int, budget: int = DEFAULT_BUDGET):
        self.k = k
        self.budget = budget
        self.atom_of: dict = {}     # key -> atom id
        self.size: dict = {}        # atom id -> number of keys
        self.acells: dict = {}      # atom id -> set of cell ids
        self.cells: dict = {}       # cell id -> _Cell
        self.by_digest: dict = {}   # digest -> cell id
        self.rows: dict = {}        # pivot atom -> {atom: coefficient mod P}
        self.col: dict = {}         # atom -> set of pivots whose row is non-zero there
        self._next = 0
        self._j: dict | None = None
        self._j_next = 0

    # --- journal (tentative batches) --------------------------------------------------------------------------------

    def _save(self, name: str, key) -> None:
        j = self._j
        if j is None or (name, key) in j:
            return
        d = getattr(self, name)
        v = d.get(key, _MISSING)
        if isinstance(v, set):
            v = set(v)
        elif isinstance(v, dict):
            v = dict(v)
        elif isinstance(v, _Cell):
            v = _Cell(set(v.atoms), v.n, v.lo, v.hi, v.whole)
        j[(name, key)] = v

    def begin(self) -> None:
        self._j = {}
        self._j_next = self._next

    def commit(self) -> None:
        self._j = None

    def rollback(self) -> None:
        j, self._j = self._j, None
        if j is None:
            return
        for (name, key), v in j.items():
            d = getattr(self, name)
            if v is _MISSING:
                d.pop(key, None)
            else:
                d[key] = v
        self._next = self._j_next

    def _new_id(self) -> int:
        self._next += 1
        return self._next

    # --- state ------------------------------------------------------------------------------------------------------

    @property
    def n_atoms(self) -> int:
        return len(self.size)

    def known(self, digest: bytes, lo: int, hi: int | None) -> bool:
        """True if a cell with this digest is stored with bounds at least as tight (the batch adds nothing)."""
        cid = self.by_digest.get(digest)
        if cid is None:
            return False
        c = self.cells[cid]
        return c.lo >= lo and (hi is None or (c.hi is not None and c.hi <= hi))

    def add(self, digest: bytes, keys, lo: int, hi: int | None, whole: bool) -> int:
        cid = self.by_digest.get(digest)
        if cid is not None:
            c = self.cells[cid]
            was_exact = c.exact
            self._save("cells", cid)
            c.lo = max(c.lo, lo)
            c.hi = hi if c.hi is None else (c.hi if hi is None else min(c.hi, hi))
            c.whole = c.whole or whole
            if c.exact and not was_exact:
                self._add_row(c.atoms)
            return cid
        atoms = self._refine(keys)
        cid = self._new_id()
        self._save("cells", cid)
        self._save("by_digest", digest)
        self.cells[cid] = c = _Cell(atoms, len(keys), lo, hi, whole)
        self.by_digest[digest] = cid
        for a in atoms:
            self._save("acells", a)
            self.acells[a].add(cid)
        if c.exact:
            self._add_row(atoms)
        return cid

    def _refine(self, keys) -> set:
        touched: dict = {}
        fresh = []
        for key in keys:
            a = self.atom_of.get(key)
            if a is None:
                fresh.append(key)
            else:
                touched.setdefault(a, []).append(key)
        out = set()
        for a, ks in touched.items():
            if len(ks) == self.size[a]:
                out.add(a)
                continue
            b = self._new_id()
            for name, key in (("size", a), ("size", b), ("acells", b), ("col", b)):
                self._save(name, key)
            self.size[b] = len(ks)
            self.size[a] -= len(ks)
            for key in ks:
                self._save("atom_of", key)
                self.atom_of[key] = b
            self.acells[b] = set(self.acells[a])
            for c in self.acells[a]:
                self._save("cells", c)
                self.cells[c].atoms.add(b)
            piv = self.col.get(a)
            if piv:
                for p in piv:
                    self._save("rows", p)
                    self.rows[p][b] = self.rows[p][a]
                self.col[b] = set(piv)
            out.add(b)
        if fresh:
            b = self._new_id()
            for name in ("size", "acells"):
                self._save(name, b)
            self.size[b] = len(fresh)
            self.acells[b] = set()
            for key in fresh:
                self._save("atom_of", key)
                self.atom_of[key] = b
            out.add(b)
        return out

    # --- linear algebra ---------------------------------------------------------------------------------------------

    def _col_add(self, a, p) -> None:
        self._save("col", a)
        self.col.setdefault(a, set()).add(p)

    def _col_del(self, a, p) -> None:
        self._save("col", a)
        s = self.col.get(a)
        if s is not None:
            s.discard(p)
            if not s:
                del self.col[a]

    def _eliminate(self, q, v: dict) -> None:
        """Remove column q from every other row using the normalised row v (v[q] == 1)."""
        for p in list(self.col.get(q, ())):
            if p == q:
                continue
            self._save("rows", p)
            row = self.rows[p]
            c = row.get(q)
            if not c:
                continue
            for a, x in v.items():
                y = (row.get(a, 0) - c * x) % P
                if y:
                    if a not in row:
                        self._col_add(a, p)
                    row[a] = y
                elif a in row:
                    del row[a]
                    self._col_del(a, p)

    def _add_row(self, atoms) -> None:
        v = dict.fromkeys(atoms, 1)
        for p in [a for a in v if a in self.rows]:
            c = v.get(p)
            if not c:
                continue
            for a, x in self.rows[p].items():
                y = (v.get(a, 0) - c * x) % P
                if y:
                    v[a] = y
                else:
                    v.pop(a, None)
        if not v:
            return
        size = self.size
        q = max(v, key=lambda a: (size[a], -a))
        inv = pow(v[q], P - 2, P)
        v = {a: x * inv % P for a, x in v.items()}
        self._eliminate(q, v)
        self._save("rows", q)
        self.rows[q] = v
        for a in v:
            self._col_add(a, q)

    def _repivot(self, p, q) -> None:
        self._save("rows", p)
        row = self.rows.pop(p)
        for a in row:
            self._col_del(a, p)
        inv = pow(row[q], P - 2, P)
        v = {a: x * inv % P for a, x in row.items()}
        self._eliminate(q, v)
        self._save("rows", q)
        self.rows[q] = v
        for a in v:
            self._col_add(a, q)

    def _normalise(self) -> None:
        """No row with a small pivot keeps an entry on a large atom (then the small-pivot rows span L n span(small))."""
        k, size = self.k, self.size
        changed = True
        while changed:
            changed = False
            for p in [p for p in self.rows if size[p] < k]:
                row = self.rows.get(p)
                if row is None:
                    continue
                big = [a for a in row if size[a] >= k]
                if big:
                    self._repivot(p, max(big, key=lambda a: (size[a], -a)))
                    changed = True

    # --- checks -----------------------------------------------------------------------------------------------------

    def trial(self, batch: list):
        """Apply `batch` tentatively (begin() is called here) and audit. Returns None or a Violation."""
        self.begin()
        ids = [self.add(*cell) for cell in batch]
        self._normalise()
        steps = [0]
        v = self._r1(steps) or self._r2(steps)
        if v is not None:
            atoms = v.cells
            v.cells = {i for i, cid in enumerate(ids) if self.cells[cid].atoms & atoms}
        return v

    def _tick(self, steps, n=1) -> bool:
        steps[0] += n
        return steps[0] > self.budget

    def _r1(self, steps):
        k, size = self.k, self.size
        krows = [p for p in self.rows if size[p] < k]
        if not krows:
            return None
        whole = {frozenset(c.atoms) for c in self.cells.values() if c.whole and c.exact}
        # connected components of rows sharing a column
        parent = {p: p for p in krows}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        owner: dict = {}
        for p in krows:
            for a in self.rows[p]:
                o = owner.setdefault(a, p)
                if o != p:
                    ra, rb = find(o), find(p)
                    if ra != rb:
                        parent[ra] = rb
        comps: dict = {}
        for p in krows:
            comps.setdefault(find(p), []).append(p)
        for comp in comps.values():
            hit = self._r1_component(comp, whole, steps)
            if hit is not None:
                return hit
        return None

    def _r1_component(self, comp, whole, steps):
        """Every 0/1 sum of at most k-1 rows of one component with fewer than k records. Rows are visited in
        breadth-first order over shared columns; a column is final once the last row touching it has been decided,
        so a partial sum with a final column outside {0, 1} (or final weight >= k) is abandoned at once."""
        k, size, rows = self.k, self.size, self.rows
        by_col: dict = {}
        for p in comp:
            for a in rows[p]:
                by_col.setdefault(a, []).append(p)
        order, seen = [], set()
        for root in sorted(comp, key=lambda p: (size[p], p)):
            if root in seen:
                continue
            seen.add(root)
            queue = [root]
            while queue:
                p = queue.pop(0)
                order.append(p)
                for a in rows[p]:
                    for q in by_col[a]:
                        if q not in seen:
                            seen.add(q)
                            queue.append(q)
        last: dict = {}
        for i, p in enumerate(order):
            for a in rows[p]:
                last[a] = i
        fin = [[] for _ in order]
        for a, i in last.items():
            fin[i].append(a)
        r = len(order)
        found = []

        def explore(j, s, w, ones, cnt):
            """Include row j in the partial sum s (unfinalised columns), w / ones: finalised columns equal to 1."""
            if found or self._tick(steps, len(rows[order[j]])):
                return
            s = dict(s)
            for a, x in rows[order[j]].items():
                y = (s.get(a, 0) + x) % P
                if y:
                    s[a] = y
                else:
                    s.pop(a, None)
            ones = list(ones)
            for a in fin[j]:
                v = s.pop(a, 0)
                if v == 1:
                    w += size[a]
                    ones.append(a)
                elif v:
                    return
            if w > k - 1:
                return
            if all(v == 1 for v in s.values()):            # stop here: exclude every later row
                tot = w + sum(size[a] for a in s)
                wset = frozenset(ones) | frozenset(s)
                if wset and 1 <= tot <= k - 1 and wset not in whole:
                    found.append(set(wset))
                    return
            if cnt + 1 >= k - 1:
                return
            for jj in range(j + 1, r):
                explore(jj, s, w, ones, cnt + 1)
                if found:
                    return
                # row jj excluded: its final columns take the current values
                for a in fin[jj]:
                    v = s.get(a, 0)
                    if v == 1:
                        s = dict(s)
                        del s[a]
                        w += size[a]
                        ones = [*ones, a]
                    elif v:
                        return
                if w > k - 1:
                    return

        for j in range(r):
            explore(j, {}, 0, [], 0)
            if found:
                return Violation("linear", found[0])
            if steps[0] > self.budget:
                return Violation("budget", set().union(*(rows[q].keys() for q in comp)))
        if steps[0] > self.budget:
            return Violation("budget", set().union(*(rows[q].keys() for q in comp)))
        return None

    def _kernel(self, f, cache):
        """The kernel vector of free atom f as signed small integers, with its effect on every cell; None if an entry
        is not a small integer or the vector fails exact verification."""
        if f in cache:
            return cache[f]
        g = {f: 1}
        for p in self.col.get(f, ()):
            if p != f:
                x = _signed((-self.rows[p][f]) % P)
                if abs(x) > MAX_ENTRY:
                    cache[f] = None
                    return None
                g[p] = x
        out = self._effect(g)
        cache[f] = out
        return out

    def _effect(self, g):
        delta: dict = {}
        for a, x in g.items():
            for c in self.acells.get(a, ()):
                delta[c] = delta.get(c, 0) + x
        for c, d in delta.items():
            if d and self.cells[c].exact:
                return None          # not a kernel vector over the integers (modular artefact): never a certificate
        return g, {c: d for c, d in delta.items() if d}

    def _feasible(self, g, delta, t) -> bool:
        size, cells = self.size, self.cells
        for a, x in g.items():
            if size[a] + t * x < 0:
                return False
        for c, d in delta.items():
            cell = cells[c]
            v = cell.n + t * d
            if v < cell.lo or (cell.hi is not None and v > cell.hi):
                return False
        return True

    def _r2(self, steps):
        k, size, cells, rows = self.k, self.size, self.cells, self.rows
        whole = {frozenset(c.atoms) for c in cells.values() if c.whole and c.exact}
        items = []
        for cid, c in cells.items():
            if not c.exact and 1 <= c.n <= k - 1 and frozenset(c.atoms) not in whole:
                items.append((c.atoms, cid, None))
        for a, n in size.items():
            if 1 <= n <= k - 1 and frozenset((a,)) not in whole:
                items.append(({a}, None, a))
        cache: dict = {}
        for atoms, cid, atom in items:
            cands = []
            for a in atoms:
                if a in rows:
                    cands.extend(b for b in rows[a] if b not in rows)
                else:
                    cands.append(a)
            cands = list(dict.fromkeys(cands))
            if self._certify(cands, cid, atom, cache, steps):
                continue
            if steps[0] > self.budget:
                return Violation("budget", set(atoms))
            return Violation("bounds", set(atoms))
        return None

    def _changes(self, g, delta, cid, atom) -> bool:
        return bool(delta.get(cid)) if cid is not None else bool(g.get(atom))

    def _certify(self, cands, cid, atom, cache, steps) -> bool:
        vecs = []
        for f in cands:
            if self._tick(steps):
                return False
            e = self._kernel(f, cache)
            if e is None:
                continue
            g, delta = e
            vecs.append(e)
            if self._changes(g, delta, cid, atom) and (self._feasible(g, delta, 1) or self._feasible(g, delta, -1)):
                return True
        # sums and differences of two candidate moves
        vecs = vecs[:40]
        for i, (g1, d1) in enumerate(vecs):
            for g2, d2 in vecs[i + 1:]:
                for s in (1, -1):
                    if self._tick(steps, len(g1) + len(g2)):
                        return False
                    g = dict(g1)
                    for a, x in g2.items():
                        y = g.get(a, 0) + s * x
                        if y:
                            g[a] = y
                        else:
                            g.pop(a, None)
                    delta = dict(d1)
                    for c, x in d2.items():
                        y = delta.get(c, 0) + s * x
                        if y:
                            delta[c] = y
                        else:
                            delta.pop(c, None)
                    if g and self._changes(g, delta, cid, atom) and (
                            self._feasible(g, delta, 1) or self._feasible(g, delta, -1)):
                        return True
        return False
