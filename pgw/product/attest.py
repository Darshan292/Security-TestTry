"""Cryptographic source attestation for free text (SECURITY_BOUNDARY.md §4, audit finding S3).

A contract allowlists source-side sanitizers/exports and pins one Ed25519 public key for each (sealed, signed config).
The source system holds the private key; the gateway and the calling app never do. For each request the source signs

    DOMAIN "\\n" json([app_id, purpose, source_id, timestamp, nonce,
                        sha256(canonical({"messages": ..., "pgw_subjects": ...}))])

and the app forwards the request with X-PGW-Input-Source / -Timestamp / -Nonce / -Signature.
The gateway recomputes the digest over the parsed body it is about to process, so any change to any message (any
role, any list-form content part, tool arguments, order, count) or to pgw_subjects invalidates the signature. The
calling app's id is bound in, so one app cannot use another app's attestation, and each signed message is accepted
once (ReplayCache: per process; replicas need a shared store).

What this proves: the holder of the source key signed exactly this content for this purpose, recently.
What it does not prove: that the source's sanitizer actually removed NOT_EXPECTED content, or that the key is held
only by the source system.

    python -m pgw.product.attest keygen KEYFILE    # new source key; prints the public key for the contract
"""
from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

DOMAIN = b"pgw-input-attestation/v2"
MAX_SKEW_S = 300            # accepted clock difference between the source's timestamp and the gateway, each way
REPLAY_CACHE_MAX = 100_000  # signed messages remembered per process; when full, requests fail closed (503)
H_SOURCE, H_TIMESTAMP, H_NONCE, H_SIGNATURE = ("X-PGW-Input-Source", "X-PGW-Input-Timestamp", "X-PGW-Input-Nonce",
                                               "X-PGW-Input-Signature")

# Public keys of the demo/test source keys whose PRIVATE halves are committed to the repository
# (DEMO_KEYS_FILE). The shipped contracts carry these so synthetic demos and tests can sign; a production gateway
# refuses to start with any of them. Operators replace them with their own sources' keys and re-sign the config.
DEMO_PUBKEYS = frozenset({
    "853c6ae50fb259f388e8b8222988bc24ff2ddcf37f84322cdbf3387eeb1fb48f",   # band-review-form@1
    "fbcf8d3fd275bc11b9770d7a7ea4e43080346f1779ce9502e0c1e0c98ea63e1d",   # comp-review-form@1
    "df95e36301ef3eebec44a842d8c3e2bf094ed03ef2ef8a4b510524a5c49b6428",   # esi-coverage-form@1
    "2d8e205442997cadefc51d732902532d7a81d3edaa1bada9d5c383cfd4f30cec",   # helpdesk-ticket-export@1
    "836eb89434c1ae57216a94529c1501bd9217fe24cbfc912a1c1f2d2bd5798d43",   # hr-email-composer@1
    "e6ed0bedd45b9b37ae920bdc53e9970c83924ae7c2bd175fb0a01a267d5a821d",   # hris-case-export@1
    "b5891a0dd6aa39298a8929885b9b28df72e4932a27b0e2d24133a3d21a8510b2",   # hr-workspace-forms@1
})
DEMO_KEYS_FILE = Path(__file__).resolve().parents[2] / "tests/fixtures/DEMO_ONLY_source_signing_keys.json"

_HEX_KEY = re.compile(r"[0-9a-f]{64}")
_HEX_SIG = re.compile(r"[0-9a-fA-F]{128}")
_TS = re.compile(r"[0-9]{1,12}")
_NONCE = re.compile(r"[0-9a-f]{32,64}")

# Small-order Ed25519 points (orders 1, 2, 4, 8) by y coordinate: a public key whose y is one of these (in any
# encoding, either sign bit) lets signatures verify for many messages, so it is never accepted as a source key.
_P = 2 ** 255 - 19
_Y8 = int.from_bytes(bytes.fromhex("26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05"), "little")
_SMALL_ORDER_Y = frozenset({0, 1, _P - 1, _Y8 % _P, (-_Y8) % _P})


class AttestationError(ValueError):
    """Verification failed. `reason` is a short, content-free code (safe for responses, logs and the ledger)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def parse_public_key(hex_key) -> bytes:
    """A pinned source public key: 64 lowercase hex characters encoding a valid Ed25519 key. Raises ValueError."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(hex_key, str) or not _HEX_KEY.fullmatch(hex_key):
        raise ValueError("source public key must be 64 lowercase hex characters (Ed25519)")
    raw = bytes.fromhex(hex_key)
    y = int.from_bytes(raw, "little") & ((1 << 255) - 1)
    if y >= _P:
        raise ValueError("source public key is not canonically encoded")
    if y in _SMALL_ORDER_Y:
        raise ValueError("source public key is a small-order point")
    Ed25519PublicKey.from_public_bytes(raw)
    return raw


def content_digest(messages, pgw_subjects=None) -> str:
    """sha256 hex of the canonical JSON of {"messages": ..., "pgw_subjects": ... or null}: sorted keys, no
    whitespace, ASCII escapes. NaN/Infinity are not JSON and are refused (ValueError)."""
    doc = {"messages": messages, "pgw_subjects": pgw_subjects}
    data = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def canonical_message(app_id: str, purpose: str, source_id: str, timestamp: int, nonce: str, digest: str) -> bytes:
    """The exact bytes signed: a domain tag, then an unambiguous JSON array (no delimiter collisions)."""
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        raise TypeError("timestamp must be an int (unix seconds)")
    fields = json.dumps([app_id, purpose, source_id, timestamp, nonce, digest], separators=(",", ":"),
                        ensure_ascii=True)
    return DOMAIN + b"\n" + fields.encode("ascii")


def sign(private_key_hex: str, purpose: str, source_id: str, messages, pgw_subjects=None,
         timestamp: int | None = None, *, app_id: str, nonce: str | None = None) -> dict:
    """Headers a source system attaches to a request for the calling app `app_id` (sources, demos and tests).
    A fresh random nonce makes every signature single-use, even for identical content in the same second."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    ts = int(time.time()) if timestamp is None else timestamp
    nonce = nonce or os.urandom(16).hex()
    msg = canonical_message(app_id, purpose, source_id, ts, nonce, content_digest(messages, pgw_subjects))
    sig = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex.strip())).sign(msg)
    return {H_SOURCE: source_id, H_TIMESTAMP: str(ts), H_NONCE: nonce, H_SIGNATURE: sig.hex()}


def sign_body(private_key_hex: str, purpose: str, source_id: str, body: dict, timestamp: int | None = None, *,
              app_id: str) -> dict:
    return sign(private_key_hex, purpose, source_id, body.get("messages"), body.get("pgw_subjects"), timestamp,
                app_id=app_id)


class ReplayCacheFull(Exception):
    """The replay cache holds REPLAY_CACHE_MAX live entries: refuse (fail closed) rather than forget one."""


class ReplayCache:
    """Signed messages already accepted, each kept until its timestamp leaves the ±max_skew window (after which a
    resend is refused as stale anyway). Bounded: when full of live entries, add() raises ReplayCacheFull.
    In-process only: several gateway replicas need a shared store, or a message can be replayed once per replica."""

    def __init__(self, max_entries: int = REPLAY_CACHE_MAX, max_skew: int = MAX_SKEW_S):
        self.max_entries, self.max_skew = max_entries, max_skew
        self._seen: dict[bytes, int] = {}
        self._expiry: list[tuple[int, bytes]] = []
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._seen)

    def add(self, key: bytes, timestamp: int, now: float | None = None) -> None:
        """Record `key`; AttestationError("replayed") if already seen, ReplayCacheFull if no room."""
        now = time.time() if now is None else now
        with self._lock:
            while self._expiry and self._expiry[0][0] < now:
                _, old = heapq.heappop(self._expiry)
                self._seen.pop(old, None)
            if key in self._seen:
                raise AttestationError("replayed")
            if len(self._seen) >= self.max_entries:
                raise ReplayCacheFull()
            exp = timestamp + self.max_skew
            self._seen[key] = exp
            heapq.heappush(self._expiry, (exp, key))


class ReplayStoreUnavailable(Exception):
    """The replay store could not record or check a message: refuse (fail closed); never accept unrecorded."""


class SqliteReplayStore:
    """Persistent replay store shared by every gateway process on a host (one SQLite file, WAL). Survives restarts,
    so a message accepted before a restart cannot be replayed after it. A primary-key insert is the atomic check:
    two processes racing on the same message cannot both succeed. Any database error raises ReplayStoreUnavailable
    (the request is refused). Not for network filesystems: replicas on several hosts need a shared database with
    the same insert-or-fail semantics (SECURITY_BOUNDARY.md §4)."""

    def __init__(self, path: str, max_entries: int = REPLAY_CACHE_MAX, max_skew: int = MAX_SKEW_S):
        import sqlite3
        self._sqlite3 = sqlite3
        self.max_entries, self.max_skew, self.path = max_entries, max_skew, path
        self._lock = threading.Lock()
        try:
            self._db = sqlite3.connect(path, timeout=2.0, isolation_level=None, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("CREATE TABLE IF NOT EXISTS seen (k BLOB PRIMARY KEY, exp INTEGER NOT NULL)")
            self._db.execute("CREATE INDEX IF NOT EXISTS seen_exp ON seen(exp)")
        except sqlite3.Error as e:
            raise ReplayStoreUnavailable(type(e).__name__) from None

    def __len__(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    def add(self, key: bytes, timestamp: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        sq = self._sqlite3
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    self._db.execute("DELETE FROM seen WHERE exp < ?", (int(now),))
                    if self._db.execute("SELECT COUNT(*) FROM seen").fetchone()[0] >= self.max_entries:
                        raise ReplayCacheFull()
                    self._db.execute("INSERT INTO seen (k, exp) VALUES (?, ?)", (key, int(timestamp) + self.max_skew))
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
                self._db.execute("COMMIT")
            except sq.IntegrityError:
                raise AttestationError("replayed") from None
            except sq.Error as e:
                raise ReplayStoreUnavailable(type(e).__name__) from None


def verify(pubkeys: dict, purpose: str, source_id: str, timestamp: str, signature: str, messages,
           pgw_subjects=None, now: float | None = None, max_skew: int = MAX_SKEW_S, *, app_id: str, nonce: str,
           replay: ReplayCache | None = None) -> str:
    """Check a request's attestation against the contract's pinned keys {source_id: pubkey hex}, for the
    authenticated calling app. With `replay`, a verified message is accepted only once.
    Returns the verified source id; raises AttestationError with a content-free reason (or ReplayCacheFull)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not source_id:
        raise AttestationError("missing")
    if source_id not in pubkeys:
        raise AttestationError("unknown_source")
    if not signature:
        raise AttestationError("unsigned")
    if not timestamp or not _TS.fullmatch(timestamp):
        raise AttestationError("bad_timestamp")
    if not nonce or not _NONCE.fullmatch(nonce):
        raise AttestationError("bad_nonce")
    if abs((time.time() if now is None else now) - int(timestamp)) > max_skew:
        raise AttestationError("stale_timestamp")
    if not _HEX_SIG.fullmatch(signature):
        raise AttestationError("bad_signature")
    try:
        msg = canonical_message(app_id, purpose, source_id, int(timestamp), nonce,
                                content_digest(messages, pgw_subjects))
    except (TypeError, ValueError):
        raise AttestationError("uncanonical_content") from None
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkeys[source_id])).verify(bytes.fromhex(signature), msg)
    except InvalidSignature:
        raise AttestationError("bad_signature") from None
    if replay is not None:   # only verified messages enter the cache, so forgeries cannot fill it
        replay.add(hashlib.sha256(msg).digest(), int(timestamp), now)
    return source_id


def load_demo_keys() -> dict[str, str]:
    """{source_id: private key hex} of the committed DEMO keys. Synthetic demos and tests only."""
    keys = json.loads(DEMO_KEYS_FILE.read_text())["keys"]
    return {k: v["private_key"] for k, v in keys.items()}


def generate_key() -> tuple[str, str]:
    """(private key hex, public key hex)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
    k = Ed25519PrivateKey.generate()
    return (k.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex(),
            k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex())


def _main(argv: list[str]) -> str:
    if len(argv) == 2 and argv[0] == "keygen":
        path = Path(argv[1])
        if path.exists():
            raise SystemExit(f"{path} exists; refusing to overwrite a source signing key")
        priv, pub = generate_key()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(priv + "\n")
        return (f"private key written to {path} (keep it in the source system only; never in the app or gateway)\n"
                f"contract external_input.sanitizers entry: <source-id>: \"{pub}\"")
    raise SystemExit("usage: python -m pgw.product.attest keygen KEYFILE")


if __name__ == "__main__":
    print(_main(sys.argv[1:]))
