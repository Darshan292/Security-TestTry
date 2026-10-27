"""HR Workspace settings, from the environment (docs/WORKSPACE.md lists every variable).

Production (the default) refuses to start without: the gateway URL and app key file, a trusted-proxy allowlist (the
SSO reverse proxy's addresses), the public https origin, and keys that are not the repository's demo keys. Secrets
are read from files and are never part of repr() or any response.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ..product import attest

MODES = ("production", "development")
DEMO_APP_KEY = "hr-workspace-key-change-me"   # noqa: S105 the demo key in registry/apps.yaml; production refuses it
_HEADER = re.compile(r"[A-Za-z0-9-]{1,64}")
_SHA = re.compile(r"[0-9a-f]{64}")
_INTEGRATION_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class WorkspaceSettings:
    mode: str = "production"
    host: str = "127.0.0.1"
    port: int = 8090
    gateway_url: str = ""
    gateway_key: str = field(default="", repr=False)          # the app key (bearer) the gateway knows us by
    source_key: str | None = field(default=None, repr=False)  # Ed25519 private key hex for FORM_SOURCE_ID
    app_id: str = "hr-workspace"                              # our app id at the gateway (bound into signatures)
    model: str = "mock-1"
    identity_header: str = "X-Forwarded-Email"
    trusted_proxies: tuple[str, ...] = ()                     # CIDRs of the SSO reverse proxy
    public_origin: str | None = None                          # e.g. https://hr-workspace.example.com
    dev_user: str = "dev.user@localhost"
    gateway_timeout_s: float = 60.0
    max_body_bytes: int = 128 * 1024
    # Helpdesk integrations that may submit pre-signed tickets: ((integration id, sha256 of its bearer token), ...)
    integrations: tuple[tuple[str, str], ...] = ()

    @property
    def production(self) -> bool:
        return self.mode == "production"

    def networks(self) -> tuple:
        return tuple(ipaddress.ip_network(c, strict=False) for c in self.trusted_proxies)

    def validate(self) -> WorkspaceSettings:
        if self.mode not in MODES:
            raise SettingsError(f"WS_MODE must be one of {MODES}")
        u = urlsplit(self.gateway_url)
        if u.scheme not in ("http", "https") or not u.hostname or u.query or u.fragment:
            raise SettingsError("WS_GATEWAY_URL must be an http(s) URL of the privacy gateway")
        if not self.gateway_key:
            raise SettingsError("WS_GATEWAY_KEY_FILE is required (the workspace's app key at the gateway)")
        if not _HEADER.fullmatch(self.identity_header):
            raise SettingsError("WS_IDENTITY_HEADER must be a header name")
        try:
            self.networks()
        except ValueError:
            raise SettingsError("WS_TRUSTED_PROXIES must be a comma-separated list of IPs/CIDRs") from None
        if self.source_key is not None:
            try:
                pub = _public_key(self.source_key)
            except ValueError:
                raise SettingsError("WS_SOURCE_KEY_FILE must hold a 64-hex Ed25519 private key") from None
        for iid, h in self.integrations:
            if not _INTEGRATION_ID.fullmatch(iid) or not _SHA.fullmatch(h):
                raise SettingsError("WS_INTEGRATIONS_FILE: lines must be '<integration-id> <sha256 of token>'")
        if not 1 <= self.gateway_timeout_s <= 600 or not 1024 <= self.max_body_bytes <= 4 * 1024 * 1024:
            raise SettingsError("WS_GATEWAY_TIMEOUT_S / WS_MAX_BODY_BYTES out of range")
        if self.production:
            if not self.trusted_proxies:
                raise SettingsError("production requires WS_TRUSTED_PROXIES (the SSO reverse proxy's addresses)")
            o = urlsplit(self.public_origin or "")
            if o.scheme != "https" or not o.hostname or o.path not in ("", "/"):
                raise SettingsError("production requires WS_PUBLIC_ORIGIN, the https origin users open")
            if self.gateway_key == DEMO_APP_KEY:
                raise SettingsError("production refuses the demo app key; issue a key and register its sha256")
            if self.source_key is not None and pub in attest.DEMO_PUBKEYS:
                raise SettingsError("production refuses a DEMO source key (its private half is public); generate "
                                    "one with python -m pgw.product.attest keygen KEYFILE")
        return self

    @classmethod
    def from_env(cls, env=None) -> WorkspaceSettings:
        env = os.environ if env is None else env

        def secret(name: str) -> str | None:
            path = env.get(name)
            if not path:
                return None
            try:
                return Path(path).read_text().strip()
            except OSError as e:
                raise SettingsError(f"{name}: cannot read the file ({type(e).__name__})") from None

        integrations = ()
        if env.get("WS_INTEGRATIONS_FILE"):
            lines = (secret("WS_INTEGRATIONS_FILE") or "").splitlines()
            integrations = tuple(tuple(ln.split()) for ln in lines if ln.strip() and not ln.startswith("#"))
            if any(len(x) != 2 for x in integrations):
                raise SettingsError("WS_INTEGRATIONS_FILE: lines must be '<integration-id> <sha256 of token>'")
        try:
            return cls(
                mode=env.get("WS_MODE", "production"), host=env.get("WS_HOST", "127.0.0.1"),
                port=int(env.get("WS_PORT", "8090")), gateway_url=env.get("WS_GATEWAY_URL", "").rstrip("/"),
                gateway_key=secret("WS_GATEWAY_KEY_FILE") or "", source_key=secret("WS_SOURCE_KEY_FILE"),
                app_id=env.get("WS_APP_ID", "hr-workspace"), model=env.get("WS_MODEL", "mock-1"),
                identity_header=env.get("WS_IDENTITY_HEADER", "X-Forwarded-Email"),
                trusted_proxies=tuple(c.strip() for c in env.get("WS_TRUSTED_PROXIES", "").split(",") if c.strip()),
                public_origin=(env.get("WS_PUBLIC_ORIGIN") or "").rstrip("/") or None,
                dev_user=env.get("WS_DEV_USER", "dev.user@localhost"),
                gateway_timeout_s=float(env.get("WS_GATEWAY_TIMEOUT_S", "60")),
                max_body_bytes=int(env.get("WS_MAX_BODY_BYTES", str(128 * 1024))),
                integrations=integrations,  # type: ignore[arg-type]
            ).validate()
        except ValueError as e:
            if isinstance(e, SettingsError):
                raise
            raise SettingsError(f"invalid workspace setting ({type(e).__name__})") from None


def _public_key(private_hex: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    if not re.fullmatch(r"[0-9a-fA-F]{64}", private_hex or ""):
        raise ValueError("bad key")
    k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    return k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
