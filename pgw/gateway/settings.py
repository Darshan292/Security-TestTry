"""Gateway configuration. Everything comes from env in production; tests build Settings directly."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_EGRESS_ALLOWLIST: tuple[str, ...] = ("api.anthropic.com",)
DEFAULT_PRIVATE_ALLOWLIST: tuple[str, ...] = ("127.0.0.1", "localhost", "::1", "pgw-private-llm")


def check_private_destination(url: str, private_allowlist, egress_allowlist) -> None:
    """The private (C4) route may only reach an in-boundary endpoint: an exact host from the private allowlist,
    never a host that is also an external provider destination. Raises ValueError otherwise."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 or c == "\\" for c in url):
        raise ValueError("private destination: malformed URL")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or parts.username or parts.password or not host:
        raise ValueError("private destination: http(s) URL without userinfo required")
    if host not in {h.lower() for h in private_allowlist}:
        raise ValueError(f"private destination not allowlisted: {host}")
    if any(host == str(e).lower().rsplit(":", 1)[0] for e in egress_allowlist):
        raise ValueError("private destination must not be an external provider host")


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


MODES = ("production", "development")


@dataclass
class Settings:
    # Deployment mode. "production" (the default, and the env default) requires a manifest signed with the trusted
    # config key, refuses uncontracted purposes and cannot enable any development-only switch. "development" is an
    # explicit opt-in for tests/demos on synthetic data: unsigned manifests are accepted (with a warning) and the
    # legacy uncontracted path may be enabled. Env: PGW_MODE. The gateway cannot detect a deployment that declares
    # itself "development" falsely; the mode is logged at startup, recorded on every audit line and on /healthz.
    mode: str = "production"
    # DEVELOPMENT ONLY: serve purposes that have no data contract through the generic policy pack (the pre-contract
    # path). Never read from the environment; create_app refuses it unless mode == "development".
    legacy_uncontracted: bool = False
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
    # Per-tenant keys with rotation (pgw.keys). When set, master_key is not used. Env: PGW_TENANT_KEYS_FILE
    tenant_keys_file: str | None = None
    # Provider data terms (organisational control, attested here so it is explicit and audited): a real external
    # provider is not built unless zero data retention / no training is attested and a processing region is named.
    provider_region: str | None = None
    provider_zero_retention: bool = False
    # Exact-hostname allowlist for outbound provider calls (see check_destination). Env: PGW_EGRESS_ALLOWLIST
    egress_allowlist: tuple[str, ...] = DEFAULT_EGRESS_ALLOWLIST
    anthropic_base_url: str = "https://api.anthropic.com"   # passed explicitly: the SDK's env override is ignored
    upstream_timeout_s: float = 60.0
    # Local NER (D4) for contracted purposes. None = off (tests/demos); from_env defaults it ON.
    ner_model: str | None = None
    ner_detector_override: object | None = field(default=None, repr=False)
    # NER worker processes (pgw.detect.ner_pool). 0 = in-process NerDetector (tests); from_env defaults to min(4, cpu).
    ner_workers: int = 0
    ner_queue_max: int = 8                 # requests allowed to wait for a worker; beyond it: fail closed at once
    ner_timeout_s: float = 8.0             # per NER call (queue wait + inference); must stay below protect_timeout_s
    sanitizer_hmac_keys: dict[str, bytes] = field(default_factory=dict, repr=False)
    attestation_db_path: str | None = None
    # Resource limits (fail closed with 413/503; nothing is sent).
    max_body_bytes: int = 262_144
    max_content_chars: int = 50_000        # all strings in `messages`, summed
    max_messages: int = 200
    protect_timeout_s: float = 10.0        # detection + local compute, per request
    max_concurrent_protect: int = 8        # worker slots; a timed-out worker keeps its slot until it really ends
    # Registry/policy/contract integrity (pgw.product.integrity). None disables the check: development mode only.
    integrity_manifest: str | None = DEFAULT_MANIFEST
    # Ed25519 public key (64 hex chars) that must have signed the manifest. Required in production. It must come
    # from deployment configuration the config authors cannot write (secret store / image), not from the repo.
    trusted_config_pubkey: str | None = None
    policy_signing_key: str = field(default="", repr=False)   # legacy HMAC key (hex); if set, its MAC must verify
    require_signed_policy: bool = False
    # Private (C4) route: one in-boundary OpenAI-compatible endpoint. None = no private route (contracts that
    # need it then stop startup).
    private_upstream_url: str | None = None
    private_model: str = "local"
    private_allowlist: tuple[str, ...] = DEFAULT_PRIVATE_ALLOWLIST
    private_upstream: object | None = field(default=None, repr=False)   # injected (tests)
    upstream: object | None = field(default=None, repr=False)   # injected Upstream (tests/demos)
    trace: object | None = field(default=None, repr=False)   # demo-only step observer; never set from env

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        e = os.environ if env is None else env
        mode = e.get("PGW_MODE", "production").strip().lower()
        ner_model = _off_or(e.get("PGW_NER_MODEL", "en_core_web_lg"))
        if mode == "production" and ner_model is None:
            raise ValueError("PGW_NER_MODEL cannot be disabled in production; configure the pinned NER model")
        return cls(
            mode=mode,
            # legacy_uncontracted is deliberately not read from the environment.
            policy_path=e.get("PGW_POLICY", cls.policy_path),
            registry_path=e.get("PGW_REGISTRY", cls.registry_path),
            roster_path=e.get("PGW_ROSTER") or None,
            ledger_db_path=e.get("PGW_LEDGER_DB", cls.ledger_db_path),
            egress_ledger_path=e.get("PGW_EGRESS_LEDGER", cls.egress_ledger_path),
            attestation_db_path=e.get("PGW_ATTESTATION_DB_PATH") or None,
            sanitizer_hmac_keys=parse_sanitizer_hmac_keys(e.get("PGW_SANITIZER_HMAC_KEYS", "{}")),
            upstream_kind=e.get("PGW_UPSTREAM", "mock"),
            upstream_url=e.get("PGW_UPSTREAM_URL", cls.upstream_url),
            openai_api_key=e.get("OPENAI_API_KEY", ""),
            anthropic_api_key=e.get("ANTHROPIC_API_KEY", ""),
            master_key=e.get("PGW_MASTER_KEY", ""),
            tenant_keys_file=e.get("PGW_TENANT_KEYS_FILE") or None,
            provider_region=e.get("PGW_PROVIDER_REGION") or None,
            provider_zero_retention=e.get("PGW_PROVIDER_ZERO_RETENTION_ATTESTED", "") == "1",
            egress_allowlist=parse_allowlist(e.get("PGW_EGRESS_ALLOWLIST")),
            anthropic_base_url=e.get("PGW_ANTHROPIC_BASE_URL", cls.anthropic_base_url),
            upstream_timeout_s=float(e.get("PGW_UPSTREAM_TIMEOUT_S", cls.upstream_timeout_s)),
            ner_model=ner_model,
            ner_workers=int(e.get("PGW_NER_WORKERS", max(1, min(4, os.cpu_count() or 1)))),
            ner_queue_max=int(e.get("PGW_NER_QUEUE_MAX", cls.ner_queue_max)),
            ner_timeout_s=float(e.get("PGW_NER_TIMEOUT_S", cls.ner_timeout_s)),
            max_body_bytes=int(e.get("PGW_MAX_BODY_BYTES", cls.max_body_bytes)),
            max_content_chars=int(e.get("PGW_MAX_CONTENT_CHARS", cls.max_content_chars)),
            max_messages=int(e.get("PGW_MAX_MESSAGES", cls.max_messages)),
            protect_timeout_s=float(e.get("PGW_PROTECT_TIMEOUT_S", cls.protect_timeout_s)),
            max_concurrent_protect=int(e.get("PGW_MAX_CONCURRENT_PROTECT", cls.max_concurrent_protect)),
            integrity_manifest=e.get("PGW_INTEGRITY_MANIFEST", cls.integrity_manifest),   # no off switch in env
            trusted_config_pubkey=e.get("PGW_TRUSTED_CONFIG_PUBKEY") or None,
            policy_signing_key=e.get("PGW_POLICY_SIGNING_KEY", ""),
            require_signed_policy=e.get("PGW_REQUIRE_SIGNED_POLICY", "") == "1",
            private_upstream_url=e.get("PGW_PRIVATE_UPSTREAM_URL") or None,
            private_model=e.get("PGW_PRIVATE_MODEL", "local"),
        )


def parse_sanitizer_hmac_keys(raw):
    if not raw: return {}
    try: obj=json.loads(raw)
    except (TypeError,ValueError): raise ValueError("PGW_SANITIZER_HMAC_KEYS must be a JSON object") from None
    if not isinstance(obj,dict): raise ValueError("PGW_SANITIZER_HMAC_KEYS must be a JSON object")
    out={}
    for sid,value in obj.items():
        if not isinstance(sid,str) or not re.fullmatch(r"[a-z0-9][a-z0-9._@-]{0,63}",sid): raise ValueError("invalid sanitizer ID")
        if not isinstance(value,str) or len(value)<64 or len(value)%2 or not re.fullmatch(r"[0-9a-fA-F]+",value): raise ValueError("sanitizer key must be at least 32 bytes encoded as hex")
        out[sid]=bytes.fromhex(value)
    return out
