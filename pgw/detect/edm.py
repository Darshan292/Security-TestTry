"""D3 exact-data matching against the tenant roster, keyed-hash only (ADR-0005),
and the D3b restore ledger for restored values re-sent by the client (ADR-0010).

Neither structure stores plaintext: only HMAC(key, TYPE || 0x1F || canon(value)).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
import time
from collections.abc import Iterable, Iterator

from ..alias import canon
from ..types import Context, Detection, EntityType

# Tokens split on whitespace AND structural punctuation, so values inside JSON/XML/log fields
# ("user":"Godavari Panthaki") form proper n-grams (R2: roster names missed in dev2 logs).
# . @ + - / _ ' stay inside tokens: emails, phones, "KS/13/8338", "D'Souza".
_TOKEN = re.compile(r"[^\s\"`,;:=|{}\[\]()<>]+")
_LEAD = "(\"'[<{"
_TRAIL = ".,;:!?)\"']>}"
_GREETING = re.compile(
    r"(?:\b(?:hi|hello|hey|dear|mr|mrs|ms|miss|dr|shri|smt|sri|thanks|regards|cc|to|from|with|and)\.?\s+)$",
    re.IGNORECASE,
)


# A candidate must LOOK like the type before its canonical form is compared. Without this,
# digit-canonicalised types match any window that merely contains the digits (e.g. an
# email plus the following phone field), producing wrong types and over-wide spans.
_DIGITISH = re.compile(r"^\+?[\d][\d \-().]*\d$")   # spaces only: never across a tab/newline (TSV cells)
_SHAPE = {
    EntityType.PHONE: _DIGITISH, EntityType.AADHAAR: _DIGITISH, EntityType.CARD: _DIGITISH,
    EntityType.BANK_ACCOUNT: _DIGITISH,
    EntityType.EMAIL: re.compile(r"^[^\s@]+@[^\s@]+$"), EntityType.UPI_ID: re.compile(r"^[^\s@]+@[^\s@]+$"),
    EntityType.EMPLOYEE_ID: re.compile(r"^[\w\-]+$"), EntityType.PAN: re.compile(r"^[\w\-]+$"),
    EntityType.IFSC: re.compile(r"^[\w\-]+$"),
    EntityType.PERSON: re.compile(r"^[^\W\d_][^\d\n\"{}:=<>\[\]@]*$"),
}


def shape_ok(t: EntityType, cand: str) -> bool:
    rx = _SHAPE.get(t)
    return rx is None or bool(rx.match(cand))


# Roster first/last names that are also common English words, months or titles:
# matched only after an explicit cue, never on capitalisation alone.
_COMMON_WORDS = frozenset("""
will may june july august april march mark bill rose grace joy hope faith victor summer rich guy pat dawn
sunny ray chase art frank page case long young white black brown green park hall king wood stone bell cook
price reed rice lane gay ash hunter mason miller baker carter taylor cole cash dean drew earl glen jack jean
lee max neil noble penny rob sage sky star van west north south east love lucky prince raja rani major
key moon gold silver angel april asha hari om ram shiv sri deep jay rich happy pal das singh
""".split())
_SPEAKER = re.compile(r"(?:^|\n)\s*(?:\[[\d:]+\]\s*)?$")


def _h(key: bytes, t: EntityType, c: str) -> bytes:
    return hmac.new(key, t.value.encode() + b"\x1f" + c.encode(), hashlib.sha256).digest()[:16]


def candidates(text: str, max_tokens: int = 4) -> Iterator[tuple[int, int, str]]:
    """Every run of 1..max_tokens tokens joined only by plain spaces, with edge punctuation trimmed.

    A multi-token candidate never crosses structural punctuation or a line break, so a JSON key
    and its value, or two TSV cells, are never glued into one candidate.
    """
    toks = [(m.start(), m.end()) for m in _TOKEN.finditer(text)]
    for i in range(len(toks)):
        for n in range(1, max_tokens + 1):
            if i + n > len(toks):
                break
            if n > 1 and text[toks[i + n - 2][1]:toks[i + n - 1][0]].strip(" ") != "":
                break
            s, e = toks[i][0], toks[i + n - 1][1]
            while s < e and text[s] in _LEAD:
                s += 1
            while e > s and text[e - 1] in _TRAIL:
                e -= 1
            if e > s:
                yield s, e, text[s:e]


class RosterIndex:
    """Keyed-hash index of known employees: full names, names, emails, ids, phones."""

    def __init__(self, key: bytes):
        self.key = key
        self._full: dict[bytes, EntityType] = {}
        self._single_names: set[bytes] = set()  # first/last names: only matched after a greeting cue
        self._shape_counts: dict[str, int] = {}
        self._shape_re: re.Pattern | None = None

    def add(self, t: EntityType, value: str) -> None:
        if value:
            self._full[_h(self.key, t, canon(t, value))] = t

    def _shape(self, emp_id: str) -> str:
        return re.sub(r"\d+", lambda m: "\\d{%d}" % len(m.group()), re.escape(emp_id.upper()))

    def id_shapes(self) -> re.Pattern | None:
        """Tenant employee-id formats learned from the roster: shapes seen for >= 1 % of employees
        (and >= 5 of them). Format metadata only; no identifier is stored in plaintext."""
        if self._shape_re is None and self._shape_counts:
            total = sum(self._shape_counts.values())
            keep = [sh for sh, c in self._shape_counts.items() if c >= max(5, 0.01 * total)]
            if keep:
                alt = "|".join(sorted(keep, key=len, reverse=True))
                self._shape_re = re.compile(rf"(?<![\w/-])(?:{alt})(?![\w/-])", re.IGNORECASE)
        return self._shape_re

    def add_employee(self, rec: dict) -> None:
        if rec.get("employee_id"):
            sh = self._shape(rec["employee_id"])
            self._shape_counts[sh] = self._shape_counts.get(sh, 0) + 1
            self._shape_re = None
        self.add(EntityType.PERSON, rec.get("full_name", ""))
        self.add(EntityType.EMAIL, rec.get("email", ""))
        self.add(EntityType.EMPLOYEE_ID, rec.get("employee_id", ""))
        self.add(EntityType.PHONE, rec.get("phone", ""))
        for part in (rec.get("first_name"), rec.get("last_name")):
            if part and len(part) >= 3:
                self._single_names.add(_h(self.key, EntityType.PERSON, canon(EntityType.PERSON, part)))

    def __len__(self) -> int:
        return len(self._full)

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        shapes = self.id_shapes()
        if shapes is not None:
            out += [Detection(m.start(), m.end(), EntityType.EMPLOYEE_ID, 0.9, "D3:roster_shape")
                    for m in shapes.finditer(text)]
        for s, e, cand in candidates(text, 3):
            n_tok = len(cand.split())
            for t in (EntityType.PERSON, EntityType.EMAIL, EntityType.EMPLOYEE_ID, EntityType.PHONE):
                if (n_tok > 1 and t in (EntityType.EMAIL, EntityType.EMPLOYEE_ID)) or not shape_ok(t, cand):
                    continue
                if _h(self.key, t, canon(t, cand)) in self._full:
                    out.append(Detection(s, e, t, 1.0, "D3:edm"))
                    break
            else:
                # A lone roster first/last name: capitalised and not a common word, or after a cue,
                # or as a chat speaker ("[12:24] brijesh: ...").
                if n_tok == 1 and cand[:1].isalpha() and shape_ok(EntityType.PERSON, cand) and \
                        _h(self.key, EntityType.PERSON, canon(EntityType.PERSON, cand)) in self._single_names:
                    cued = bool(_GREETING.search(text[max(0, s - 12):s]))
                    speaker = text[e:e + 1] == ":" and bool(_SPEAKER.search(text[max(0, s - 20):s]))
                    capital = cand[0].isupper() and cand.casefold() not in _COMMON_WORDS
                    if cued or speaker or capital:
                        out.append(Detection(s, e, EntityType.PERSON, 0.9 if (cued or speaker) else 0.8,
                                             "D3:edm_single"))
        return out


# Name-link candidates: initial + surname ("S. Kulshreshtha", "s kulshreshtha"), or one capitalised name word.
_LINK_CAND = re.compile(r"(?<![\w'’])(?:(?P<i>[^\W\d_])\.?[ \t]+(?P<sur>[^\W\d_]{3,})(?:['’]s)?(?![\w'’])"
                        r"|(?P<w>[^\W\d_a-z][^\W\d_]{2,})(?:['’]s)?(?![\w'’]))")


class RestoreLedger:
    """Per-conversation keyed hashes of every value the gateway restored (ADR-0010).

    SQLite here; the design's production target is the PostgreSQL ledger DB.
    Any exception from this class is a detector failure and must fail closed.

    Keying: with `keys` (pgw.pipeline.Keys or pgw.keys.TenantKeyring) each row is hashed with the
    TENANT's active ledger key and stores its key id, so lookups keep working for rows written under a
    previous key id until they expire (a row whose key id is gone raises: fail closed). Without `keys`
    (tests, rows from before key ids existed: key_id '') the single `key` is used.
    The same table family pins the alias key id per conversation (pinned_key / pin_key).
    """

    def __init__(self, key: bytes, path: str = ":memory:", ttl_s: int = 24 * 3600, keys=None):
        self.key = key
        self.keys = keys
        self.ttl_s = ttl_s
        self._kcache: dict[tuple[str, str], bytes] = {}
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS restore_ledger ("
            " tenant TEXT, app TEXT, conversation_id TEXT, h BLOB, type TEXT, expires_at REAL,"
            " key_id TEXT NOT NULL DEFAULT '',"
            " PRIMARY KEY (tenant, app, conversation_id, h))"
        )
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(restore_ledger)")}
        if "key_id" not in cols:   # ledger written before key ids: rows keep the legacy key ('')
            self.db.execute("ALTER TABLE restore_ledger ADD COLUMN key_id TEXT NOT NULL DEFAULT ''")
        # Name links (pgw.pipeline partial-name rule): keyed hash of a partial name form -> the alias of the one full
        # name it belongs to in this conversation, or '' once it was ambiguous. Aliases are pseudonyms the provider
        # already saw; the name form itself is only stored as a keyed hash.
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS name_links ("
            " tenant TEXT, app TEXT, conversation_id TEXT, h BLOB, alias TEXT, expires_at REAL,"
            " key_id TEXT NOT NULL DEFAULT '',"
            " PRIMARY KEY (tenant, app, conversation_id, h))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS conversation_keys ("
            " tenant TEXT, app TEXT, conversation_id TEXT, key_id TEXT, expires_at REAL,"
            " PRIMARY KEY (tenant, app, conversation_id))"
        )
        self.db.commit()

    def _key(self, tenant: str, key_id: str) -> bytes:
        if not key_id:
            return self.key
        k = self._kcache.get((tenant, key_id))
        if k is None:   # raises KeyUnavailable for a retired/unknown key id: fail closed
            k = self._kcache[(tenant, key_id)] = self.keys.derive(tenant, "ledger", key_id)
        return k

    def _k(self, t: str, c: str, key: bytes | None = None) -> bytes:
        return hmac.new(self.key if key is None else key, t.encode() + b"\x1f" + c.encode(),
                        hashlib.sha256).digest()[:16]

    def record(self, ctx: Context, items: Iterable[tuple[EntityType, str]]) -> None:
        if not ctx.conversation_id:
            return
        exp = time.time() + self.ttl_s
        kid = self.keys.active_id(ctx.tenant) if self.keys is not None else ""
        key = self._key(ctx.tenant, kid)
        rows = [(ctx.tenant, ctx.app, ctx.conversation_id, self._k(t.value, canon(t, v), key), t.value, exp, kid)
                for t, v in items]
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO restore_ledger"
                                " (tenant, app, conversation_id, h, type, expires_at, key_id)"
                                " VALUES (?,?,?,?,?,?,?)", rows)

    def _load(self, ctx: Context) -> dict[str, dict[bytes, str]]:
        cur = self.db.execute(
            "SELECT key_id, h, type FROM restore_ledger"
            " WHERE tenant=? AND app=? AND conversation_id=? AND expires_at>?",
            (ctx.tenant, ctx.app, ctx.conversation_id, time.time()),
        )
        out: dict[str, dict[bytes, str]] = {}
        for kid, h, t in cur.fetchall():
            out.setdefault(kid, {})[h] = t
        return out

    def detect(self, ctx: Context, texts: list[str]) -> list[list[Detection]]:
        if not ctx.conversation_id:
            return [[] for _ in texts]
        by_kid = self._load(ctx)
        if not by_kid:
            return [[] for _ in texts]
        keyed = [(self._key(ctx.tenant, kid), hashes) for kid, hashes in sorted(by_kid.items())]
        types = {EntityType(t) for hashes in by_kid.values() for t in hashes.values()}
        result = []
        for text in texts:
            dets = []
            for s, e, cand in candidates(text, 4):
                for t in types:
                    if not shape_ok(t, cand):
                        continue
                    c = canon(t, cand)
                    if any(self._k(t.value, c, key) in hashes for key, hashes in keyed):
                        dets.append(Detection(s, e, t, 1.0, "D3b:restore_ledger"))
                        break
            result.append(dets)
        return result

    def _link_h(self, ns: str, part: str, key: bytes) -> bytes:
        return self._k("NAME_LINK\x1f" + ns, part, key)

    def name_links(self, ctx: Context, ns: str, parts: list[str]) -> dict[str, str]:
        """part -> alias ('' = ambiguous) for the partial name forms this conversation has linked under `ns`."""
        if not ctx.conversation_id or not parts:
            return {}
        rows = self.db.execute(
            "SELECT key_id, h, alias FROM name_links WHERE tenant=? AND app=? AND conversation_id=? AND expires_at>?",
            (ctx.tenant, ctx.app, ctx.conversation_id, time.time())).fetchall()
        if not rows:
            return {}
        by_kid: dict[str, dict[bytes, str]] = {}
        for kid, h, alias in rows:
            by_kid.setdefault(kid, {})[h] = alias
        out: dict[str, str] = {}
        for kid, table in sorted(by_kid.items()):
            key = self._key(ctx.tenant, kid)
            for p in parts:
                a = table.get(self._link_h(ns, p, key))
                if a is not None:
                    out[p] = "" if (p in out and out[p] != a) else a
        return out

    def name_link_detect(self, ctx: Context, ns: str, texts: list[str]) -> list[list[Detection]]:
        """PERSON detections for the name parts this conversation linked earlier (pgw.pipeline): a capitalised name
        word, or an initial plus surname in any case. A trailing possessive stays outside the span."""
        if not ctx.conversation_id:
            return [[] for _ in texts]
        rows = self.db.execute(
            "SELECT key_id, h FROM name_links WHERE tenant=? AND app=? AND conversation_id=? AND expires_at>?",
            (ctx.tenant, ctx.app, ctx.conversation_id, time.time())).fetchall()
        if not rows:
            return [[] for _ in texts]
        by_kid: dict[str, set[bytes]] = {}
        for kid, h in rows:
            by_kid.setdefault(kid, set()).add(h)
        keyed = [(self._key(ctx.tenant, kid), hs) for kid, hs in sorted(by_kid.items())]
        out = []
        for text in texts:
            dets = []
            for m in _LINK_CAND.finditer(text):
                if m.group("w") is not None:
                    s, e = m.span("w")
                    part = canon(EntityType.PERSON, m.group("w"))
                else:
                    s, e = m.span("i")[0], m.span("sur")[1]
                    part = canon(EntityType.PERSON, m.group("i")) + " " + canon(EntityType.PERSON, m.group("sur"))
                if any(self._link_h(ns, part, key) in hs for key, hs in keyed):
                    dets.append(Detection(s, e, EntityType.PERSON, 0.9, "D3b:name_link"))
            out.append(dets)
        return out

    def record_name_links(self, ctx: Context, ns: str, links: dict[str, str]) -> None:
        """Store part -> alias. A part that now points somewhere else than before becomes ambiguous ('') for good."""
        if not ctx.conversation_id or not links:
            return
        old = self.name_links(ctx, ns, list(links))
        exp = time.time() + self.ttl_s
        kid = self.keys.active_id(ctx.tenant) if self.keys is not None else ""
        key = self._key(ctx.tenant, kid)
        rows = [(ctx.tenant, ctx.app, ctx.conversation_id, self._link_h(ns, p, key),
                 "" if (p in old and old[p] != a) else a, exp, kid) for p, a in links.items()]
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO name_links"
                                " (tenant, app, conversation_id, h, alias, expires_at, key_id)"
                                " VALUES (?,?,?,?,?,?,?)", rows)

    def pinned_key(self, ctx: Context) -> str | None:
        row = self.db.execute(
            "SELECT key_id FROM conversation_keys WHERE tenant=? AND app=? AND conversation_id=? AND expires_at>?",
            (ctx.tenant, ctx.app, ctx.conversation_id, time.time())).fetchone()
        return row[0] if row else None

    def pin_key(self, ctx: Context, key_id: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO conversation_keys VALUES (?,?,?,?,?)",
                            (ctx.tenant, ctx.app, ctx.conversation_id, key_id, time.time() + self.ttl_s))

    def erase(self, ctx: Context) -> None:
        with self.db:
            args = (ctx.tenant, ctx.app, ctx.conversation_id)
            self.db.execute("DELETE FROM restore_ledger WHERE tenant=? AND app=? AND conversation_id=?", args)
            self.db.execute("DELETE FROM name_links WHERE tenant=? AND app=? AND conversation_id=?", args)
            self.db.execute("DELETE FROM conversation_keys WHERE tenant=? AND app=? AND conversation_id=?", args)
