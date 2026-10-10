"""Registry/policy/contract versioning and integrity (ADR-0006 "signed packs", minimal form).

`policies/MANIFEST.json` records, for the app registry, every policy pack and every data contract, its sha256 and
declared version. At startup the gateway refuses to run if any file it loads is missing from the manifest, differs
from its recorded hash, or declares a different version.

Trust: the manifest itself is only trustworthy if it is signed. In production the gateway requires an Ed25519
signature that verifies under a public key supplied by the deployment (PGW_TRUSTED_CONFIG_PUBKEY). The private key
stays offline with whoever approves configuration changes; the gateway never holds it, so a process or person that
can write the config files (and read the gateway's environment) still cannot produce a valid manifest. Anyone who
can replace the trusted public key itself is outside what this check can stop.
The older symmetric HMAC (PGW_POLICY_SIGNING_KEY) is still verified if configured, but it is not a trust anchor:
whoever holds the gateway's environment holds that key.

This is integrity and change control, not a governance platform: who may sign, and review of changes, stays
with the owners' normal change process.

    python -m pgw.product.integrity keygen KEYFILE        # new offline signing key; prints the public key
    python -m pgw.product.integrity seal [--sign-key KEYFILE]   # rewrite (and sign) the manifest after review
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
SEALED_GLOBS = ("registry/*.yaml", "policies/*.yaml", "contracts/*.yaml")
_SIG_CONTEXT = b"pgw-config-manifest-v1\n"   # domain separation for the Ed25519 signature


class IntegrityError(RuntimeError):
    pass


def _sha(path: Path, data: bytes | None = None) -> str:
    return hashlib.sha256(path.read_bytes() if data is None else data).hexdigest()


def _canonical(files: dict) -> bytes:
    return json.dumps(files, sort_keys=True, separators=(",", ":")).encode()


def _rel(path: Path, base: Path) -> str:
    return Path(os.path.relpath(Path(path).resolve(), base)).as_posix()


def _version(path: Path, data: bytes | None = None) -> int:   # a registry without a version seals as 0
    raw = yaml.safe_load(path.read_text() if data is None else data.decode("utf-8"))
    return int(raw.get("version", 0)) if isinstance(raw, dict) else 0


def generate_signing_key() -> tuple[bytes, str]:
    """(raw 32-byte Ed25519 private key, public key hex)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
    k = Ed25519PrivateKey.generate()
    raw = k.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return raw, k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def _ed_sign(private_raw: bytes, files: dict) -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    k = Ed25519PrivateKey.from_private_bytes(private_raw)
    pub = k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {"key_id": hashlib.sha256(pub).hexdigest()[:16], "sig": k.sign(_SIG_CONTEXT + _canonical(files)).hex()}


def _ed_verify(pub_hex: str, files: dict, block) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex.strip()))
    except ValueError:
        raise IntegrityError("trusted config public key is malformed (64 hex chars expected)") from None
    if not isinstance(block, dict) or not isinstance(block.get("sig"), str):
        raise IntegrityError("integrity manifest is not signed by the trusted config key")
    try:
        pub.verify(bytes.fromhex(block["sig"]), _SIG_CONTEXT + _canonical(files))
    except (InvalidSignature, ValueError):
        raise IntegrityError("integrity manifest signature does not verify under the trusted config key") from None


def seal(manifest: Path = DEFAULT_MANIFEST, key: bytes | None = None, sign_key: bytes | None = None) -> dict:
    base = manifest.resolve().parent.parent
    files = {}
    for g in SEALED_GLOBS:
        for p in sorted(base.glob(g)):
            files[_rel(p, base)] = {"sha256": _sha(p), "version": _version(p)}
    doc = {"files": files, "signature": hmac.new(key, _canonical(files), hashlib.sha256).hexdigest() if key else None,
           "ed25519": _ed_sign(sign_key, files) if sign_key else None}
    manifest.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return doc


def verify(paths: list[str | Path], manifest: str | Path = DEFAULT_MANIFEST, key: bytes | None = None,
           require_signed: bool = False, trusted_pubkey: str | None = None, require_trusted: bool = False,
           contents: dict | None = None) -> str:
    """Check every loaded file against the manifest. Returns the manifest digest (recorded in the audit ledger).

    require_trusted (production): the manifest must carry an Ed25519 signature that verifies under trusted_pubkey.
    contents: {resolved path: bytes} already read by the caller. The caller then parses exactly these bytes, so a
    file swapped on disk between verification and parsing cannot be served unverified."""
    manifest = Path(manifest)
    try:
        doc = json.loads(manifest.read_text())
        files = doc["files"]
        if not isinstance(files, dict):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise IntegrityError("integrity manifest missing or unreadable") from None
    if require_trusted and not trusted_pubkey:
        raise IntegrityError("production requires a trusted config public key (PGW_TRUSTED_CONFIG_PUBKEY)")
    if trusted_pubkey:
        _ed_verify(trusted_pubkey, files, doc.get("ed25519"))
    sig = doc.get("signature")
    if key is not None:
        if not sig or not hmac.compare_digest(sig, hmac.new(key, _canonical(files), hashlib.sha256).hexdigest()):
            raise IntegrityError("integrity manifest signature invalid")
    elif require_signed and not trusted_pubkey:
        raise IntegrityError("signed manifest required but no signing key configured")
    base = manifest.resolve().parent.parent
    for p in paths:
        rel = _rel(Path(p), base)
        entry = files.get(rel)
        if entry is None:
            raise IntegrityError(f"{rel} is not in the integrity manifest")
        data = (contents or {}).get(str(Path(p).resolve()))
        if _sha(Path(p), data) != entry["sha256"]:
            raise IntegrityError(f"{rel} differs from its sealed hash (unreviewed change?)")
        if _version(Path(p), data) != entry["version"]:
            raise IntegrityError(f"{rel} declares a different version than sealed")
    return hashlib.sha256(_canonical(files)).hexdigest()[:16]


def _main(argv: list[str]) -> str:
    usage = "usage: python -m pgw.product.integrity keygen KEYFILE | seal [--sign-key KEYFILE]"
    if len(argv) == 2 and argv[0] == "keygen":
        path = Path(argv[1])
        if path.exists():
            raise SystemExit(f"{path} exists; refusing to overwrite a signing key")
        raw, pub = generate_signing_key()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(raw.hex() + "\n")
        return f"private key written to {path} (keep it offline)\nPGW_TRUSTED_CONFIG_PUBKEY={pub}"
    if argv[:1] == ["seal"] and (len(argv) == 1 or (len(argv) == 3 and argv[1] == "--sign-key")):
        sign = bytes.fromhex(Path(argv[2]).read_text().strip()) if len(argv) == 3 else None
        k = os.environ.get("PGW_POLICY_SIGNING_KEY")
        d = seal(key=bytes.fromhex(k) if k else None, sign_key=sign)
        return (f"sealed {len(d['files'])} files; ed25519-signed={'yes' if d['ed25519'] else 'NO (development only)'}"
                f"; hmac={'yes' if d['signature'] else 'no'}")
    raise SystemExit(usage)


if __name__ == "__main__":
    print(_main(sys.argv[1:]))
