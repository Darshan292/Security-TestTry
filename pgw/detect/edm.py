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

_TOKEN = re.compile(r"\S+")
_LEAD = "(\"'[<{"
_TRAIL = ".,;:!?)\"']>}"
_GREETING = re.compile(
    r"(?:\b(?:hi|hello|hey|dear|mr|mrs|ms|miss|dr|shri|smt|sri|thanks|regards|cc|to|from|with|and)\.?\s+)$",
    re.IGNORECASE,
)


def _h(key: bytes, t: EntityType, c: str) -> bytes:
    return hmac.new(key, t.value.encode() + b"\x1f" + c.encode(), hashlib.sha256).digest()[:16]


def candidates(text: str, max_tokens: int = 4) -> Iterator[tuple[int, int, str]]:
    """Every run of 1..max_tokens whitespace-separated tokens, with edge punctuation trimmed."""
    toks = [(m.start(), m.end()) for m in _TOKEN.finditer(text)]
    for i in range(len(toks)):
        for n in range(1, max_tokens + 1):
            if i + n > len(toks):
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

    def add(self, t: EntityType, value: str) -> None:
        if value:
            self._full[_h(self.key, t, canon(t, value))] = t

    def add_employee(self, rec: dict) -> None:
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
        for s, e, cand in candidates(text, 3):
            n_tok = len(cand.split())
            for t in (EntityType.PERSON, EntityType.EMAIL, EntityType.EMPLOYEE_ID, EntityType.PHONE):
                if n_tok > 1 and t in (EntityType.EMAIL, EntityType.EMPLOYEE_ID):
                    continue
                if _h(self.key, t, canon(t, cand)) in self._full:
                    out.append(Detection(s, e, t, 1.0, "D3:edm"))
                    break
            else:
                # A lone first/last name is only a PERSON when a greeting/honorific precedes it.
                if n_tok == 1 and cand[:1].isalpha() and \
                        _h(self.key, EntityType.PERSON, canon(EntityType.PERSON, cand)) in self._single_names and \
                        _GREETING.search(text[max(0, s - 12):s]):
                    out.append(Detection(s, e, EntityType.PERSON, 0.9, "D3:edm_single"))
        return out


class RestoreLedger:
    """Per-conversation keyed hashes of every value the gateway restored (ADR-0010).

    SQLite here; the design's production target is the PostgreSQL ledger DB.
    Any exception from this class is a detector failure and must fail closed.
    """

    def __init__(self, key: bytes, path: str = ":memory:", ttl_s: int = 24 * 3600):
        self.key = key
        self.ttl_s = ttl_s
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS restore_ledger ("
            " tenant TEXT, app TEXT, conversation_id TEXT, h BLOB, type TEXT, expires_at REAL,"
            " PRIMARY KEY (tenant, app, conversation_id, h))"
        )

    def _k(self, t: str, c: str) -> bytes:
        return hmac.new(self.key, t.encode() + b"\x1f" + c.encode(), hashlib.sha256).digest()[:16]

    def record(self, ctx: Context, items: Iterable[tuple[EntityType, str]]) -> None:
        if not ctx.conversation_id:
            return
        exp = time.time() + self.ttl_s
        rows = [(ctx.tenant, ctx.app, ctx.conversation_id, self._k(t.value, canon(t, v)), t.value, exp)
                for t, v in items]
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO restore_ledger VALUES (?,?,?,?,?,?)", rows)

    def _load(self, ctx: Context) -> dict[bytes, str]:
        cur = self.db.execute(
            "SELECT h, type FROM restore_ledger WHERE tenant=? AND app=? AND conversation_id=? AND expires_at>?",
            (ctx.tenant, ctx.app, ctx.conversation_id, time.time()),
        )
        return dict(cur.fetchall())

    def detect(self, ctx: Context, texts: list[str]) -> list[list[Detection]]:
        if not ctx.conversation_id:
            return [[] for _ in texts]
        hashes = self._load(ctx)
        if not hashes:
            return [[] for _ in texts]
        types = {EntityType(t) for t in hashes.values()}
        result = []
        for text in texts:
            dets = []
            for s, e, cand in candidates(text, 4):
                for t in types:
                    if self._k(t.value, canon(t, cand)) in hashes:
                        dets.append(Detection(s, e, t, 1.0, "D3b:restore_ledger"))
                        break
            result.append(dets)
        return result

    def erase(self, ctx: Context) -> None:
        with self.db:
            self.db.execute("DELETE FROM restore_ledger WHERE tenant=? AND app=? AND conversation_id=?",
                            (ctx.tenant, ctx.app, ctx.conversation_id))
