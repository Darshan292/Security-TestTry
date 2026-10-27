"""Configuration rollback protection (re-audit N5).

An older configuration tree that was validly signed still verifies under the trusted key: pointing
PGW_INTEGRITY_MANIFEST at it used to start the gateway with whatever that tree said (NER off, a 10M-character limit,
a model or key hash removed since). The signed deployment profile therefore carries a monotonic integer
`config_serial`, and production refuses a serial below either of two independent floors:

1. the pinned minimum `PGW_MIN_CONFIG_SERIAL` (operational; same custody as PGW_TRUSTED_CONFIG_PUBKEY: a store the
   config authors cannot write). It holds on a fresh host or an empty state directory;
2. the persisted high-water mark `<PGW_STATE_DIR>/config_serial.json`: the highest serial this gateway's state
   directory has started with. A missing file is initialised (first start, logged); a file that exists but cannot
   be read or parsed stops startup. It is never reset by the gateway. Written atomically (temp file, fsync, rename,
   directory fsync) under an exclusive lock, so two processes starting at once keep the maximum.

Equal and higher serials start. A deliberate rollback is a new signed change: the old content re-signed with a
serial above the current one (OPERATIONS.md). Development: serials are optional, a configured floor that is not met
only logs a warning, and the high-water mark is never written (an unsigned development tree must not be able to
raise it and lock production out).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path

HWM_NAME = "config_serial.json"
LOCK_NAME = "config_serial.lock"
HWM_KIND = "pgw-config-high-water"


class RollbackError(ValueError):
    """The configuration's serial is below a floor, or the high-water mark cannot be trusted. Startup stops."""


def _valid_serial(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def read_high_water(state_dir: str | Path) -> int | None:
    """The recorded serial, None if no file exists yet. Any other problem raises RollbackError (fail closed)."""
    path = Path(state_dir) / HWM_NAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise RollbackError(f"config high-water mark {HWM_NAME} is unreadable; startup refused (not reset)") from None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    if not (isinstance(doc, dict) and set(doc) == {"kind", "config_serial"} and doc["kind"] == HWM_KIND
            and _valid_serial(doc["config_serial"])):
        raise RollbackError(f"config high-water mark {HWM_NAME} is corrupt; startup refused (not reset). "
                            "Investigate, then restore it from backup or re-create it with the serial of the "
                            "configuration last deployed (OPERATIONS.md)")
    return doc["config_serial"]


def _write_atomic(state_dir: Path, serial: int) -> None:
    path = state_dir / HWM_NAME
    tmp = state_dir / f".{HWM_NAME}.{os.getpid()}.tmp"
    data = (json.dumps({"kind": HWM_KIND, "config_serial": serial}, sort_keys=True) + "\n").encode()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
    dfd = os.open(state_dir, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


@contextlib.contextmanager
def _locked(state_dir: Path):
    fd = os.open(state_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)   # releases the lock


def _state_dir(state_dir) -> Path:
    if not state_dir:
        raise RollbackError("production requires a state directory for the config high-water mark (PGW_STATE_DIR)")
    p = Path(state_dir)
    if not p.is_dir():
        raise RollbackError("the state directory (PGW_STATE_DIR) does not exist; it is not created automatically, so "
                            "a missing or remounted directory cannot silently reset the high-water mark")
    return p


def check(serial: int | None, min_serial: int | None, state_dir, production: bool) -> list[str]:
    """Refuse a configuration older than the pinned minimum or the recorded high-water mark. Production raises
    RollbackError; development returns warnings (event names) instead. Returns [] when everything holds."""
    problems: list[str] = []
    if production:
        if not _valid_serial(serial):
            raise RollbackError("production requires config_serial (an integer >= 1) in the signed deployment profile")
        if not _valid_serial(min_serial):
            raise RollbackError("production requires the pinned minimum config serial PGW_MIN_CONFIG_SERIAL "
                                "(an integer >= 1, kept with the trust anchor)")
        if serial < min_serial:
            raise RollbackError(f"configuration rollback refused: config_serial {serial} is below the pinned "
                                f"minimum {min_serial} (PGW_MIN_CONFIG_SERIAL)")
        hwm = read_high_water(_state_dir(state_dir))
        if hwm is not None and serial < hwm:
            raise RollbackError(f"configuration rollback refused: config_serial {serial} is below the high-water "
                                f"mark {hwm} this gateway has already started with ({HWM_NAME})")
        return problems
    if serial is None:
        return problems
    if _valid_serial(min_serial) and serial < min_serial:
        problems.append("config_serial_below_pinned_minimum_development_only")
    if state_dir and Path(state_dir).is_dir():
        try:
            hwm = read_high_water(state_dir)
        except RollbackError:
            problems.append("config_high_water_unreadable_development_only")
        else:
            if hwm is not None and serial < hwm:
                problems.append("config_serial_below_high_water_development_only")
    return problems


def record(serial: int, state_dir) -> bool:
    """Production, after every other startup check passed: raise the high-water mark to `serial`. Re-checked under
    the lock, so a concurrent start with a higher serial is never lowered. Returns True if the file was created."""
    d = _state_dir(state_dir)
    with _locked(d):
        hwm = read_high_water(d)
        if hwm is not None and serial < hwm:
            raise RollbackError(f"configuration rollback refused: config_serial {serial} is below the high-water "
                                f"mark {hwm} ({HWM_NAME}, raised by a concurrent start)")
        if hwm is None or serial > hwm:
            _write_atomic(d, serial)
        return hwm is None
