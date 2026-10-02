"""Tamper-evident, append-only audit log (JSONL, metadata only), and `python -m pgw.audit verify <path>`.

Each row carries `seq` (1, 2, ...) and `prev` = sha256 of the previous row's exact line bytes (genesis: 64
zeros), so an edit, deletion, insertion or reordering anywhere before the last row breaks verification.
With platform keys available (pgw.pipeline.Keys / pgw.keys.TenantKeyring) each row also carries `kid` and
`mac` = HMAC-SHA256(platform audit key, canonical row without `mac`), so the chain cannot be recomputed by
someone without the key. Every append is fsync'd. A log whose existing content does not verify is never
appended to (the gateway refuses to start), and a writer that sees the file change under it stops writing.

Not detected by the file alone: truncation of the newest rows, or replacing the whole file with an older
copy of itself. Production must ship rows to WORM / remote storage that anchors the head (out of scope).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import threading

from .pipeline import KeyUnavailable

GENESIS = "0" * 64
NL = b"\n"
RESERVED = ("seq", "prev", "kid", "mac")


class AuditChainError(Exception):
    """The audit log does not verify (message holds a line number and reason, never row content)."""


def _canonical(row: dict) -> bytes:
    return json.dumps({k: v for k, v in row.items() if k != "mac"}, sort_keys=True).encode()


def _mac(key: bytes, row: dict) -> str:
    return hmac.new(key, _canonical(row), hashlib.sha256).hexdigest()


def verify(path: str, keys=None, require_mac: bool | None = None) -> tuple[int, str, int]:
    """Return (rows, head hash, byte size). Raise AuditChainError on any break.

    keys: platform keys to check MACs. require_mac defaults to `keys is not None` (so MACs cannot be stripped);
    rows that carry a MAC when no keys are given are refused unless require_mac is explicitly False."""
    if require_mac is None:
        require_mac = keys is not None
    if not os.path.exists(path):
        return 0, GENESIS, 0
    with open(path, "rb") as f:
        data = f.read()
    if data and not data.endswith(b"\n"):
        raise AuditChainError(f"line {data.count(NL) + 1}: incomplete final row")
    prev, n, mkeys = GENESIS, 0, {}
    for n, line in enumerate(data.splitlines(), 1):
        try:
            row = json.loads(line)
        except ValueError:
            raise AuditChainError(f"line {n}: not JSON") from None
        if not isinstance(row, dict):
            raise AuditChainError(f"line {n}: not an object")
        if row.get("seq") != n:
            raise AuditChainError(f"line {n}: sequence break (deleted, inserted or reordered rows)")
        if row.get("prev") != prev:
            raise AuditChainError(f"line {n}: hash chain break (edited, deleted or reordered rows)")
        if "mac" in row:
            if keys is None:
                if require_mac is not False:
                    raise AuditChainError(f"line {n}: row has a MAC but no key was given to check it")
            else:
                kid = row.get("kid")
                if kid not in mkeys:
                    try:
                        mkeys[kid] = keys.derive("*", "audit", kid)
                    except KeyUnavailable:
                        raise AuditChainError(f"line {n}: unknown audit key id") from None
                if not hmac.compare_digest(str(row["mac"]), _mac(mkeys[kid], row)):
                    raise AuditChainError(f"line {n}: MAC mismatch (row edited)")
        elif require_mac:
            raise AuditChainError(f"line {n}: row has no MAC")
        prev = hashlib.sha256(line).hexdigest()
    return n, prev, len(data)


class ChainedLedger:
    """Append-only hash-chained JSONL writer. Refuses to open a log that does not verify (fail closed)."""

    def __init__(self, path: str, keys=None):
        self.path = path
        self._lock = threading.Lock()
        self._mac_key, self._kid = None, None
        if keys is not None:
            self._kid = keys.active_id("*")
            self._mac_key = keys.derive("*", "audit", self._kid)
        # Existing rows must verify (MACs checked when we have keys; an unkeyed log stays unkeyed-checkable).
        self._seq, self._prev, self._size = verify(path, keys, require_mac=False if keys is None else None)
        self._broken = False

    def write(self, **rec) -> None:
        if any(k in rec for k in RESERVED):
            raise ValueError("reserved audit field")
        with self._lock:
            if self._broken:
                raise AuditChainError("audit log writer stopped after an earlier failure")
            row = {**rec, "seq": self._seq + 1, "prev": self._prev}
            if self._mac_key is not None:
                row["kid"] = self._kid
                row["mac"] = _mac(self._mac_key, row)
            line = json.dumps(row, sort_keys=True).encode()
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    if os.fstat(fd).st_size != self._size:   # someone else wrote/truncated: stop
                        raise AuditChainError("audit log changed outside this writer")
                    buf = line + b"\n"
                    while buf:
                        buf = buf[os.write(fd, buf):]
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except BaseException:
                self._broken = True
                raise
            self._seq += 1
            self._prev = hashlib.sha256(line).hexdigest()
            self._size += len(line) + 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pgw.audit")
    ap.add_argument("command", choices=["verify"])
    ap.add_argument("path")
    ap.add_argument("--keys-file", default=os.environ.get("PGW_TENANT_KEYS_FILE"),
                    help="tenant key file (platform key checks MACs); else PGW_MASTER_KEY if set")
    ap.add_argument("--no-mac", action="store_true", help="check the hash chain only, ignore MACs")
    a = ap.parse_args(argv)
    keys = None
    if not a.no_mac:
        if a.keys_file:
            from .keys import TenantKeyring
            keys = TenantKeyring.load(a.keys_file)
        elif os.environ.get("PGW_MASTER_KEY"):
            from .pipeline import Keys
            keys = Keys.from_env()
    try:
        n, head, _ = verify(a.path, keys, require_mac=False if a.no_mac else None)
    except AuditChainError as e:
        print(f"FAIL {e}", file=sys.stderr)
        return 1
    print(f"ok rows={n} head={head} macs={'checked' if keys is not None else 'not checked'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
