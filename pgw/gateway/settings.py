"""Gateway configuration. Everything comes from env in production; tests build Settings directly."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_EGRESS_ALLOWLIST: tuple[str, ...] = ("api.anthropic.com",)
DEFAULT_MANIFEST = str(Path(__file__).resolve().parents[2] / "policies" / "MANIFEST.json")


def _off_or(v: str | None) -> str | None:
    return None if v is None or v.strip().lower() in ("", "none", "off") else v.strip()


def parse_allowlist(raw: str | None) -> tuple[str, ...]:
    """Comma-separated hostnames (optionally host:port) -> normalised tuple. Empty/unset -> default."""
    items = tuple(x.strip().lower() for x in (raw or "").split(",") if x.strip())
    return items or DEFAULT_EGRESS_ALLOWLIST


def check_destination(url: str, allowlist) -> None:
    """Raise ValueError unless `url` is https and its EXACT hostname is in `allowlist`.

    Allowlist entries are bare hostnames ("api.anthropic.com", port 443 only) or
    "host:port" (that port only). Matching is exact and case-insensitive: no
    wildcards, no suffix/prefix matching, no trailing-dot or IP-literal leniency
    (an IP literal passes only if that exact literal is listed). Userinfo is rejected.
    """
    if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 or c == "\\" for c in url):
        raise ValueError("egress destination: malformed URL")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port           # raises ValueError on a bad port
    except ValueError as exc:
        raise ValueError(f"egress destination: malformed URL ({exc})") from None
    if parts.scheme.lower() != "https":
        raise ValueError("egress destination: scheme must be https")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise ValueError("egress destination: userinfo not allowed")
    if not host or "*" in host:
        raise ValueError("egress destination: missing or invalid host")
    host = host.lower()
    port = 443 if port is None else port
    for entry in allowlist:
        e = str(entry).strip().lower()
        if not e or "*" in e:
            continue                 # wildcards are never honoured
        e_host, sep, e_port = e.rpartition(":") if e.count(":") == 1 else (e, "", "")
        if not sep:
            e_host, e_port = e, "443"
        if host == e_host and str(port) == e_port:
            return
    raise ValueError(f"egress destination not allowlisted: {host}:{port}")


@dataclass
class Settings:
    policy_path: str = "policies/hris-default.yaml"
    registry_path: str = "registry/apps.yaml"
    roster_path: str | None = None            # optional JSONL of employees
    ledger_db_path: str = "pgw_restore_ledger.sqlite"
    egress_ledger_path: str = "pgw_egress.jsonl"
    upstream_kind: str = "mock"               # mock | openai | anthropic
    upstream_url: str = "https://api.openai.com/v1"
    openai_api_key: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)
    master_key: str = field(default="", repr=False)   # 64 hex chars
    # Exact-hostname allowlist for outbound provider calls (see check_destination). Env: PGW_EGRESS_ALLOWLIST
    egress_allowlist: tuple[str, ...] = DEFAULT_EGRESS_ALLOWLIST
    anthropic_base_url: str = "https://api.anthropic.com"   # passed explicitly: the SDK's env override is ignored
    upstream_timeout_s: float = 60.0
    # Local NER (D4) for contracted purposes. None = off (tests/demos); from_env defaults it ON.
    ner_model: str | None = None
    # Resource limits (fail closed with 413/503; nothing is sent).
    max_body_bytes: int = 262_144
    max_content_chars: int = 50_000        # all strings in `messages`, summed
    max_messages: int = 200
    protect_timeout_s: float = 10.0        # detection + local compute, per request
    max_concurrent_protect: int = 8        # worker slots; a timed-out worker keeps its slot until it really ends
    # Policy/contract integrity (pgw.product.integrity). None disables the check (tests with throwaway contracts).
    integrity_manifest: str | None = DEFAULT_MANIFEST
    policy_signing_key: str = field(default="", repr=False)   # hex; if set, the manifest signature must verify
    require_signed_policy: bool = False
    upstream: object | None = field(default=None, repr=False)   # injected Upstream (tests/demos)

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        e = os.environ if env is None else env
        return cls(
            policy_path=e.get("PGW_POLICY", cls.policy_path),
            registry_path=e.get("PGW_REGISTRY", cls.registry_path),
            roster_path=e.get("PGW_ROSTER") or None,
            ledger_db_path=e.get("PGW_LEDGER_DB", cls.ledger_db_path),
            egress_ledger_path=e.get("PGW_EGRESS_LEDGER", cls.egress_ledger_path),
            upstream_kind=e.get("PGW_UPSTREAM", "mock"),
            upstream_url=e.get("PGW_UPSTREAM_URL", cls.upstream_url),
            openai_api_key=e.get("OPENAI_API_KEY", ""),
            anthropic_api_key=e.get("ANTHROPIC_API_KEY", ""),
            master_key=e.get("PGW_MASTER_KEY", ""),
            egress_allowlist=parse_allowlist(e.get("PGW_EGRESS_ALLOWLIST")),
            anthropic_base_url=e.get("PGW_ANTHROPIC_BASE_URL", cls.anthropic_base_url),
            upstream_timeout_s=float(e.get("PGW_UPSTREAM_TIMEOUT_S", cls.upstream_timeout_s)),
            ner_model=_off_or(e.get("PGW_NER_MODEL", "en_core_web_lg")),
            max_body_bytes=int(e.get("PGW_MAX_BODY_BYTES", cls.max_body_bytes)),
            max_content_chars=int(e.get("PGW_MAX_CONTENT_CHARS", cls.max_content_chars)),
            max_messages=int(e.get("PGW_MAX_MESSAGES", cls.max_messages)),
            protect_timeout_s=float(e.get("PGW_PROTECT_TIMEOUT_S", cls.protect_timeout_s)),
            max_concurrent_protect=int(e.get("PGW_MAX_CONCURRENT_PROTECT", cls.max_concurrent_protect)),
            integrity_manifest=e.get("PGW_INTEGRITY_MANIFEST", cls.integrity_manifest),   # no off switch in env
            policy_signing_key=e.get("PGW_POLICY_SIGNING_KEY", ""),
            require_signed_policy=e.get("PGW_REQUIRE_SIGNED_POLICY", "") == "1",
        )
