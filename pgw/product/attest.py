"""Cryptographic source attestation for free text (SECURITY_BOUNDARY.md §4, audit finding S3).

A contract allowlists source-side sanitizers/exports and pins one Ed25519 public key for each (sealed, signed config).
The source system holds the private key; the gateway and the calling app never do. For each request the source signs

    DOMAIN "\\n" json([purpose, source_id, timestamp, sha256(canonical({"messages": ..., "pgw_subjects": ...}))])

and the app forwards the request with X-PGW-Input-Source / X-PGW-Input-Timestamp / X-PGW-Input-Signature.
The gateway recomputes the digest over the parsed body it is about to process, so any change to any message (any
role, any list-form content part, tool arguments, order, count) or to pgw_subjects invalidates the signature.

What this proves: the holder of the source key signed exactly this content for this purpose, recently.
What it does not prove: that the source's sanitizer actually removed NOT_EXPECTED content, or that the key is held
only by the source system.

    python -m pgw.product.attest keygen KEYFILE    # new source key; prints the public key for the contract
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

DOMAIN = b"pgw-input-attestation/v1"
MAX_SKEW_S = 300            # accepted clock difference between the source's timestamp and the gateway, each way
H_SOURCE, H_TIMESTAMP, H_SIGNATURE = "X-PGW-Input-Source", "X-PGW-Input-Timestamp", "X-PGW-Input-Signature"

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
})
DEMO_KEYS_FILE = Path(__file__).resolve().parents[2] / "tests/fixtures/DEMO_ONLY_source_signing_keys.json"

_HEX_KEY = re.compile(r"[0-9a-f]{64}")
_HEX_SIG = re.compile(r"[0-9a-fA-F]{128}")
_TS = re.compile(r"[0-9]{1,12}")


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
    Ed25519PublicKey.from_public_bytes(raw)
    if raw == bytes(32):
        raise ValueError("source public key is the all-zero key")
    return raw


def content_digest(messages, pgw_subjects=None) -> str:
    """sha256 hex of the canonical JSON of {"messages": ..., "pgw_subjects": ... or null}: sorted keys, no
    whitespace, ASCII escapes. NaN/Infinity are not JSON and are refused (ValueError)."""
    doc = {"messages": messages, "pgw_subjects": pgw_subjects}
    data = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def canonical_message(purpose: str, source_id: str, timestamp: int, digest: str) -> bytes:
    """The exact bytes signed: a domain tag, then an unambiguous JSON array (no delimiter collisions)."""
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        raise TypeError("timestamp must be an int (unix seconds)")
    fields = json.dumps([purpose, source_id, timestamp, digest], separators=(",", ":"), ensure_ascii=True)
    return DOMAIN + b"\n" + fields.encode("ascii")


def sign(private_key_hex: str, purpose: str, source_id: str, messages, pgw_subjects=None,
         timestamp: int | None = None) -> dict:
    """Headers a source system attaches to a request (used by sources, demos and tests alike)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    ts = int(time.time()) if timestamp is None else timestamp
    msg = canonical_message(purpose, source_id, ts, content_digest(messages, pgw_subjects))
    sig = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex.strip())).sign(msg)
    return {H_SOURCE: source_id, H_TIMESTAMP: str(ts), H_SIGNATURE: sig.hex()}


def sign_body(private_key_hex: str, purpose: str, source_id: str, body: dict, timestamp: int | None = None) -> dict:
    return sign(private_key_hex, purpose, source_id, body.get("messages"), body.get("pgw_subjects"), timestamp)


def verify(pubkeys: dict, purpose: str, source_id: str, timestamp: str, signature: str, messages,
           pgw_subjects=None, now: float | None = None, max_skew: int = MAX_SKEW_S) -> str:
    """Check a request's attestation against the contract's pinned keys {source_id: pubkey hex}.
    Returns the verified source id; raises AttestationError with a content-free reason otherwise."""
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
    if abs((time.time() if now is None else now) - int(timestamp)) > max_skew:
        raise AttestationError("stale_timestamp")
    if not _HEX_SIG.fullmatch(signature):
        raise AttestationError("bad_signature")
    try:
        msg = canonical_message(purpose, source_id, int(timestamp), content_digest(messages, pgw_subjects))
    except (TypeError, ValueError):
        raise AttestationError("uncanonical_content") from None
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkeys[source_id])).verify(bytes.fromhex(signature), msg)
    except InvalidSignature:
        raise AttestationError("bad_signature") from None
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
