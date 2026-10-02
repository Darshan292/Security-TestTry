"""Policy/contract versioning and integrity (ADR-0006 "signed packs", minimal form).

`policies/MANIFEST.json` records, for every policy pack and data contract, its sha256 and declared version.
At startup the gateway refuses to run if any file it loads is missing from the manifest, differs from its
recorded hash, or declares a different version. If a signing key is configured, the manifest must carry a valid
HMAC-SHA256 signature; with `require_signed`, an unsigned manifest is refused too.

This is integrity and change control, not a governance platform: who may sign, and review of changes, stays
with the owners' normal change process.

    python -m pgw.product.integrity seal            # rewrite the manifest after a reviewed change
    PGW_POLICY_SIGNING_KEY=<hex> python -m pgw.product.integrity seal    # ... and sign it
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "policies" / "MANIFEST.json"
SEALED_GLOBS = ("policies/*.yaml", "contracts/*.yaml")


class IntegrityError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version(path: Path) -> int:
    return int(yaml.safe_load(path.read_text())["version"])


def _canonical(files: dict) -> bytes:
    return json.dumps(files, sort_keys=True, separators=(",", ":")).encode()


def _rel(path: Path, base: Path) -> str:
    return Path(os.path.relpath(Path(path).resolve(), base)).as_posix()


def seal(manifest: Path = DEFAULT_MANIFEST, key: bytes | None = None) -> dict:
    base = manifest.resolve().parent.parent
    files = {}
    for g in SEALED_GLOBS:
        for p in sorted(base.glob(g)):
            files[_rel(p, base)] = {"sha256": _sha(p), "version": _version(p)}
    doc = {"files": files, "signature": hmac.new(key, _canonical(files), hashlib.sha256).hexdigest() if key else None}
    manifest.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return doc


def verify(paths: list[str | Path], manifest: str | Path = DEFAULT_MANIFEST, key: bytes | None = None,
           require_signed: bool = False) -> str:
    """Check every loaded file against the manifest. Returns the manifest digest (recorded in the audit ledger)."""
    manifest = Path(manifest)
    try:
        doc = json.loads(manifest.read_text())
        files = doc["files"]
    except (OSError, ValueError, KeyError):
        raise IntegrityError("integrity manifest missing or unreadable") from None
    sig = doc.get("signature")
    if key is not None:
        if not sig or not hmac.compare_digest(sig, hmac.new(key, _canonical(files), hashlib.sha256).hexdigest()):
            raise IntegrityError("integrity manifest signature invalid")
    elif require_signed:
        raise IntegrityError("signed manifest required but no signing key configured")
    base = manifest.resolve().parent.parent
    for p in paths:
        rel = _rel(Path(p), base)
        entry = files.get(rel)
        if entry is None:
            raise IntegrityError(f"{rel} is not in the integrity manifest")
        if _sha(Path(p)) != entry["sha256"]:
            raise IntegrityError(f"{rel} differs from its sealed hash (unreviewed change?)")
        if _version(Path(p)) != entry["version"]:
            raise IntegrityError(f"{rel} declares a different version than sealed")
    return hashlib.sha256(_canonical(files)).hexdigest()[:16]


if __name__ == "__main__":
    if sys.argv[1:] != ["seal"]:
        raise SystemExit("usage: python -m pgw.product.integrity seal")
    k = os.environ.get("PGW_POLICY_SIGNING_KEY")
    d = seal(key=bytes.fromhex(k) if k else None)
    print(f"sealed {len(d['files'])} files; signed={'yes' if d['signature'] else 'no'}")
