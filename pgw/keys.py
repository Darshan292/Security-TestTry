"""Per-tenant key material with versioned key ids, and `python -m pgw.keys` to manage it.

Key file (PGW_TENANT_KEYS_FILE; JSON, or YAML with the same shape), mode 0600:

    {"platform": {"active": "k1", "keys": {"k1": "<64 hex>"}},
     "tenants":  {"acme": {"active": "k2", "keys": {"k1": "<64 hex>", "k2": "<64 hex>"}}}}

* Every tenant has its own random master per key id, so one tenant's key never derives another's.
  Purpose keys are HKDF(master[tenant][key_id], info="pgw/{purpose}/v1/{tenant}") as before.
* Tenant "*" is the PLATFORM namespace (roster EDM index, legacy restore-ledger key, audit-chain MAC). It
  holds no tenant data keys; it has its own master so tenant keys stay independent of it.
* A tenant with no entry raises KeyUnavailable: the request fails closed (503) and nothing is sent.
  There is no fallback to the platform key or another tenant's key.
* Rotation adds a key id and makes it active. New conversations / ledger rows use the active id; existing
  conversations keep their pinned id and ledger rows keep theirs while that id is still in the file.
  Retire an old id only after the restore-ledger TTL (24h) has passed since rotation.

CLI (never prints key material; writes atomically with mode 0600; takes effect on gateway restart):
    python -m pgw.keys init    [--file F] [--import-master-from-env]
    python -m pgw.keys rotate  --tenant T [--file F]          (T="*" rotates the platform key)
    python -m pgw.keys retire  --tenant T --key-id K [--file F]
    python -m pgw.keys list    [--file F]
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path

import yaml

from .pipeline import KeyUnavailable, Keys, hkdf

PLATFORM = "*"
_KID = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
log = logging.getLogger("pgw.keys")


class KeyFileError(Exception):
    """The key file is missing, malformed or readable by others. Startup fails."""


def _check_ring(name: str, ring) -> None:
    if not isinstance(ring, dict) or not isinstance(ring.get("keys"), dict) or not ring["keys"]:
        raise KeyFileError(f"{name}: needs a non-empty 'keys' map")
    if ring.get("active") not in ring["keys"]:
        raise KeyFileError(f"{name}: 'active' must name one of its key ids")
    for kid, hx in ring["keys"].items():
        if not isinstance(kid, str) or not _KID.match(kid):
            raise KeyFileError(f"{name}: invalid key id")
        try:
            ok = len(bytes.fromhex(hx)) == 32
        except (TypeError, ValueError):
            ok = False
        if not ok:   # never echo the value
            raise KeyFileError(f"{name}/{kid}: key must be 64 hex chars")


def _validate(doc) -> dict:
    if not isinstance(doc, dict) or set(doc) - {"platform", "tenants"}:
        raise KeyFileError("key file must have exactly 'platform' and 'tenants'")
    _check_ring("platform", doc.get("platform"))
    tenants = doc.get("tenants") or {}
    if not isinstance(tenants, dict):
        raise KeyFileError("'tenants' must be a map")
    for t, ring in tenants.items():
        if not isinstance(t, str) or not t or t == PLATFORM:
            raise KeyFileError("invalid tenant name")
        _check_ring(f"tenant {t!r}", ring)
    return {"platform": doc["platform"], "tenants": tenants}


def read_file(path: str | os.PathLike) -> dict:
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        raise KeyFileError("key file not found") from None
    if os.name == "posix" and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise KeyFileError("key file must not be accessible to group/others (chmod 600)")
    try:
        doc = yaml.safe_load(p.read_text())
    except yaml.YAMLError:
        raise KeyFileError("key file is not valid JSON/YAML") from None
    return _validate(doc)


def write_file(path: str | os.PathLike, doc: dict) -> None:
    """Atomic replace: temp file (0600) in the same directory, fsync, rename, fsync directory."""
    import json
    _validate(doc)
    p = Path(path).resolve()
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".pgw-keys-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    dfd = os.open(p.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


class TenantKeyring:
    """Same interface as pipeline.Keys (active_id / has / derive), keyed per tenant and key id."""

    def __init__(self, doc: dict):
        doc = _validate(doc)
        self._rings = {PLATFORM: doc["platform"], **doc["tenants"]}

    @classmethod
    def load(cls, path: str | os.PathLike) -> "TenantKeyring":
        return cls(read_file(path))

    def tenants(self) -> set[str]:
        return set(self._rings) - {PLATFORM}

    def _ring(self, tenant: str) -> dict:
        ring = self._rings.get(tenant)
        if ring is None:
            raise KeyUnavailable("no key configured for tenant")
        return ring

    def active_id(self, tenant: str) -> str:
        return self._ring(tenant)["active"]

    def has(self, tenant: str, key_id: str) -> bool:
        return key_id in self._rings.get(tenant, {}).get("keys", {})

    def derive(self, tenant: str, purpose: str, key_id: str | None = None) -> bytes:
        ring = self._ring(tenant)
        kid = ring["active"] if key_id is None else key_id
        if kid not in ring["keys"]:
            raise KeyUnavailable("unknown key id for tenant")
        return hkdf(bytes.fromhex(ring["keys"][kid]), tenant, purpose)


def build_keys(settings, registry=()) -> "Keys | TenantKeyring":
    """Gateway startup: the tenant key file if configured (fail closed per tenant), else the single master."""
    path = getattr(settings, "tenant_keys_file", None)
    if path:
        ring = TenantKeyring.load(path)
        missing = sorted({a.tenant for a in registry} - ring.tenants())
        if missing:   # startup continues; their requests fail closed with 503
            log.warning("event='tenant_keys_missing' count=%d", len(missing))
        return ring
    return Keys(bytes.fromhex(settings.master_key)) if settings.master_key else Keys.from_env()


# --- CLI ----------------------------------------------------------------------

def _next_kid(keys: dict) -> str:
    n = max((int(k[1:]) for k in keys if re.fullmatch(r"k\d+", k)), default=0)
    return f"k{n + 1}"


def _ring_of(doc: dict, tenant: str) -> dict | None:
    return doc["platform"] if tenant == PLATFORM else doc["tenants"].get(tenant)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pgw.keys")
    ap.add_argument("command", choices=["init", "rotate", "retire", "list"])
    ap.add_argument("--file", default=os.environ.get("PGW_TENANT_KEYS_FILE"))
    ap.add_argument("--tenant")
    ap.add_argument("--key-id")
    ap.add_argument("--import-master-from-env", action="store_true",
                    help="init only: use PGW_MASTER_KEY as platform key k1 (keeps legacy restore-ledger rows valid)")
    a = ap.parse_args(argv)
    if not a.file:
        ap.error("--file or PGW_TENANT_KEYS_FILE is required")
    try:
        if a.command == "init":
            if Path(a.file).exists():
                raise KeyFileError("key file already exists")
            hx = os.environ.get("PGW_MASTER_KEY", "") if a.import_master_from_env else secrets.token_hex(32)
            write_file(a.file, {"platform": {"active": "k1", "keys": {"k1": hx}}, "tenants": {}})
            print("initialised platform=k1")
            return 0
        doc = read_file(a.file)
        if a.command == "list":
            for t, ring in [(PLATFORM, doc["platform"]), *sorted(doc["tenants"].items())]:
                print(f"tenant={t} active={ring['active']} key_ids={','.join(sorted(ring['keys']))}")
            return 0
        if not a.tenant:
            ap.error("--tenant is required")
        ring = _ring_of(doc, a.tenant)
        if a.command == "rotate":
            if ring is None:
                if a.tenant == PLATFORM or not a.tenant.strip():
                    raise KeyFileError("invalid tenant name")
                ring = doc["tenants"][a.tenant] = {"active": None, "keys": {}}
            kid = _next_kid(ring["keys"])
            ring["keys"][kid] = secrets.token_hex(32)
            ring["active"] = kid
        else:   # retire
            if ring is None or a.key_id not in ring["keys"]:
                raise KeyFileError("unknown tenant or key id")
            if ring["active"] == a.key_id:
                raise KeyFileError("cannot retire the active key id; rotate first")
            del ring["keys"][a.key_id]
        write_file(a.file, doc)
        print(f"tenant={a.tenant} active={ring['active']} key_ids={','.join(sorted(ring['keys']))}")
        return 0
    except KeyFileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
