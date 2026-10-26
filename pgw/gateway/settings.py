"""Gateway configuration, and the sealed deployment profile that pins every security-relevant setting.

Settings come from the environment (`Settings.from_env`) or are built directly (tests, demos). The registry, policy,
contracts and roster are sealed and Ed25519-signed (pgw.product.integrity), but the environment is not: whoever
controls it could quietly weaken protection while the signed config still verifies (audit S4). So every setting
that changes what can leave, how much is inspected or where data goes is read in production from the signed
**deployment profile** `registry/deployment.yaml` (sealed by the `registry/*.yaml` glob, located next to the
manifest: `<manifest>/../../registry/deployment.yaml`, never from an environment variable).

Rules (`apply_deployment_profile`, called by `create_app` before anything else is loaded):

- production: the profile is required, must be sealed and signed, and must state every PROFILE field. Each field
  takes the profile's value. A value the environment or the caller sets that differs from the profile stops startup
  with a message naming the setting (never its value). An unset value, or one equal to the profile's, is fine.
  "Unset" means: for `from_env`, the variable is absent; for directly built Settings, the field holds its built-in
  default (such a value is replaced by the profile's, so it cannot weaken anything).
- production: NER off (`ner_model: null`) is refused unless the profile also gives `ner_off_justification`, a
  written reason of at least 20 characters. It is a signed decision, not an environment switch.
- development: the profile is optional; when present, differences are logged as warnings and the Settings win.

Classification of every Settings field (`FIELD_CLASS`; a test fails if a field is missing):

| Field | Class | Env | Why |
|---|---|---|---|
| registry_path, policy_path | profile | PGW_REGISTRY, PGW_POLICY | Choose among sealed files: a different sealed registry/policy could be weaker |
| roster_path | profile | PGW_ROSTER | Unsetting the roster silently disables roster name matching (audit S4) |
| upstream_kind, upstream_url, anthropic_base_url | profile | PGW_UPSTREAM, PGW_UPSTREAM_URL, PGW_ANTHROPIC_BASE_URL | Where data goes |
| egress_allowlist | profile | PGW_EGRESS_ALLOWLIST | Which external hosts may receive data |
| provider_zero_retention, provider_region | profile | PGW_PROVIDER_ZERO_RETENTION_ATTESTED, PGW_PROVIDER_REGION | The provider-terms attestation that unlocks a real provider |
| upstream_timeout_s | profile | PGW_UPSTREAM_TIMEOUT_S | Limit (borderline: affects availability more than disclosure; pinned because the brief covers all limits) |
| ner_model, ner_workers, ner_queue_max, ner_timeout_s | profile | PGW_NER_* | How much is inspected; workers=0 also drops the fail-closed worker pool |
| max_body_bytes, max_content_chars, max_messages, protect_timeout_s, max_concurrent_protect | profile | PGW_MAX_*, PGW_PROTECT_TIMEOUT_S | How much is inspected and how much detection work a caller can force |
| private_upstream_url, private_allowlist | profile | PGW_PRIVATE_UPSTREAM_URL, (code only) | Where C4/unattested content goes; the allowlist is what keeps it in-boundary |
| legacy_uncontracted, dev_allow_real_provider, trace | code | never read from env | Security-relevant development switches; production refuses them in code (`check_startup_mode`, `create_app`) |
| upstream, private_upstream | code | never read from env | Injected objects (tests/demos). Only code already running in the process can set them, and such code is outside any configuration control |
| mode | operational | PGW_MODE | Bootstrap: it decides whether the signed profile is required, so it cannot live in it. Logged, on every ledger line and /healthz (SECURITY_BOUNDARY.md §6.3) |
| integrity_manifest | operational | PGW_INTEGRITY_MANIFEST | Path; whatever it names must verify under the trusted key (rollback limit: SECURITY_BOUNDARY.md §2) |
| trusted_config_pubkey | operational | PGW_TRUSTED_CONFIG_PUBKEY | The trust anchor cannot be inside what it verifies; must come from a store config authors cannot write |
| policy_signing_key, master_key, openai_api_key, anthropic_api_key | operational | PGW_POLICY_SIGNING_KEY, PGW_MASTER_KEY, *_API_KEY | Secrets: must stay out of the repo |
| tenant_keys_file | operational | PGW_TENANT_KEYS_FILE | Path to secrets |
| require_signed_policy | operational | PGW_REQUIRE_SIGNED_POLICY | Borderline: it can only add the legacy HMAC check; production already requires Ed25519 |
| ledger_db_path, egress_ledger_path, replay_db_path | operational | PGW_LEDGER_DB, PGW_EGRESS_LEDGER, PGW_REPLAY_DB | State and log destinations (borderline for the audit log: its protection is the hash chain and shipping to WORM storage, OPERATIONS.md) |
| private_model | operational | PGW_PRIVATE_MODEL | Borderline: the model name at the in-boundary endpoint; it does not change where data goes or what is inspected |
"""
from __future__ import annotations

import dataclasses
import os
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
    # DEVELOPMENT ONLY: allow a non-mock external provider while the config is unsigned (synthetic-data validation
    # runs such as scripts/pilot_validation.py and the demo inspector). Never read from the environment, so a
    # deployment configured by env in development mode can only reach the mock provider.
    dev_allow_real_provider: bool = False
    policy_path: str = "policies/hris-default.yaml"
    registry_path: str = "registry/apps.yaml"
    roster_path: str | None = None            # optional JSONL of employees
    ledger_db_path: str = "pgw_restore_ledger.sqlite"
    replay_db_path: str | None = None          # persistent single-use attestation store; required in production
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
    # NER worker processes (pgw.detect.ner_pool). 0 = in-process NerDetector (tests); from_env defaults to min(4, cpu).
    ner_workers: int = 0
    ner_queue_max: int = 8                 # requests allowed to wait for a worker; beyond it: fail closed at once
    ner_timeout_s: float = 8.0             # per NER call (queue wait + inference); must stay below protect_timeout_s
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
        s = cls._from_env(e)
        # Which profile-pinned settings the environment actually set (absent = unset, the profile decides).
        s._env_set = frozenset(f for f, var in PROFILE_ENV.items() if var and var in e)
        return s

    @classmethod
    def _from_env(cls, e) -> "Settings":
        return cls(
            mode=e.get("PGW_MODE", "production").strip().lower(),
            # legacy_uncontracted is deliberately not read from the environment.
            policy_path=e.get("PGW_POLICY", cls.policy_path),
            registry_path=e.get("PGW_REGISTRY", cls.registry_path),
            roster_path=e.get("PGW_ROSTER") or None,
            ledger_db_path=e.get("PGW_LEDGER_DB", cls.ledger_db_path),
            replay_db_path=e.get("PGW_REPLAY_DB") or None,
            egress_ledger_path=e.get("PGW_EGRESS_LEDGER", cls.egress_ledger_path),
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
            ner_model=_off_or(e.get("PGW_NER_MODEL", "en_core_web_lg")),
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


# --- classification and the sealed deployment profile -------------------------------------------------------------

PROFILE, CODE, OPERATIONAL = "profile", "code", "operational"
FIELD_CLASS: dict[str, str] = {
    "mode": OPERATIONAL, "legacy_uncontracted": CODE, "dev_allow_real_provider": CODE,
    "policy_path": PROFILE, "registry_path": PROFILE, "roster_path": PROFILE,
    "ledger_db_path": OPERATIONAL, "egress_ledger_path": OPERATIONAL, "replay_db_path": OPERATIONAL,
    "upstream_kind": PROFILE, "upstream_url": PROFILE,
    "openai_api_key": OPERATIONAL, "anthropic_api_key": OPERATIONAL, "master_key": OPERATIONAL,
    "tenant_keys_file": OPERATIONAL, "provider_region": PROFILE, "provider_zero_retention": PROFILE,
    "egress_allowlist": PROFILE, "anthropic_base_url": PROFILE, "upstream_timeout_s": PROFILE,
    "ner_model": PROFILE, "ner_workers": PROFILE, "ner_queue_max": PROFILE, "ner_timeout_s": PROFILE,
    "max_body_bytes": PROFILE, "max_content_chars": PROFILE, "max_messages": PROFILE,
    "protect_timeout_s": PROFILE, "max_concurrent_protect": PROFILE,
    "integrity_manifest": OPERATIONAL, "trusted_config_pubkey": OPERATIONAL, "policy_signing_key": OPERATIONAL,
    "require_signed_policy": OPERATIONAL, "private_upstream_url": PROFILE, "private_model": OPERATIONAL,
    "private_allowlist": PROFILE, "private_upstream": CODE, "upstream": CODE, "trace": CODE,
}
# Value kind of each profile field, and the environment variable that can set it ("" = not read from env).
PROFILE_KIND: dict[str, str] = {
    "registry_path": "path", "policy_path": "path", "roster_path": "path?",
    "upstream_kind": "str", "upstream_url": "str", "anthropic_base_url": "str", "egress_allowlist": "hosts",
    "provider_region": "str?", "provider_zero_retention": "bool", "upstream_timeout_s": "float",
    "ner_model": "str?", "ner_workers": "int0", "ner_queue_max": "int0", "ner_timeout_s": "float",
    "max_body_bytes": "int", "max_content_chars": "int", "max_messages": "int", "protect_timeout_s": "float",
    "max_concurrent_protect": "int", "private_upstream_url": "str?", "private_allowlist": "hosts",
}
PROFILE_ENV: dict[str, str] = {
    "registry_path": "PGW_REGISTRY", "policy_path": "PGW_POLICY", "roster_path": "PGW_ROSTER",
    "upstream_kind": "PGW_UPSTREAM", "upstream_url": "PGW_UPSTREAM_URL", "anthropic_base_url": "PGW_ANTHROPIC_BASE_URL",
    "egress_allowlist": "PGW_EGRESS_ALLOWLIST", "provider_region": "PGW_PROVIDER_REGION",
    "provider_zero_retention": "PGW_PROVIDER_ZERO_RETENTION_ATTESTED", "upstream_timeout_s": "PGW_UPSTREAM_TIMEOUT_S",
    "ner_model": "PGW_NER_MODEL", "ner_workers": "PGW_NER_WORKERS", "ner_queue_max": "PGW_NER_QUEUE_MAX",
    "ner_timeout_s": "PGW_NER_TIMEOUT_S", "max_body_bytes": "PGW_MAX_BODY_BYTES",
    "max_content_chars": "PGW_MAX_CONTENT_CHARS", "max_messages": "PGW_MAX_MESSAGES",
    "protect_timeout_s": "PGW_PROTECT_TIMEOUT_S", "max_concurrent_protect": "PGW_MAX_CONCURRENT_PROTECT",
    "private_upstream_url": "PGW_PRIVATE_UPSTREAM_URL", "private_allowlist": "",
}
PROFILE_FIELDS = tuple(f for f, c in FIELD_CLASS.items() if c == PROFILE)
PROFILE_DOC_KIND = "pgw-deployment-profile"
PROFILE_REL = "registry/deployment.yaml"
MIN_JUSTIFICATION = 20
UPSTREAM_KINDS = ("mock", "openai", "anthropic")


class ProfileError(ValueError):
    """The deployment profile is missing, malformed, or contradicted by the environment. Startup stops."""


def profile_location(settings: Settings) -> tuple[Path, Path] | None:
    """(sealed tree base, profile path), derived from the manifest location only. None without a manifest."""
    if settings.integrity_manifest is None:
        return None
    base = Path(settings.integrity_manifest).resolve().parent.parent
    return base, base / PROFILE_REL


def _check(name: str, kind: str, v):
    bad = ProfileError(f"deployment profile: {name!r} has an invalid value (expected {kind})")
    opt = kind.endswith("?")
    if v is None:
        if opt:
            return None
        raise bad
    k = kind.rstrip("?")
    if k == "path":
        if not isinstance(v, str) or not v or Path(v).is_absolute() or ".." in Path(v).parts:
            raise bad
        return v
    if k == "str":
        if not isinstance(v, str) or not v.strip():
            raise bad
        if name == "upstream_kind" and v not in UPSTREAM_KINDS:
            raise bad
        return v.strip()
    if k == "bool":
        if not isinstance(v, bool):
            raise bad
        return v
    if k in ("int", "int0"):
        if not isinstance(v, int) or isinstance(v, bool) or v < (0 if k == "int0" else 1):
            raise bad
        return v
    if k == "float":
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not v > 0:
            raise bad
        return float(v)
    if k == "hosts":
        if not isinstance(v, list) or not v or not all(isinstance(h, str) and h.strip() for h in v):
            raise bad
        return tuple(h.strip().lower() for h in v)
    raise bad   # pragma: no cover - every kind is handled above


def parse_profile(data: bytes) -> tuple[dict, str | None]:
    """Validate a deployment profile. Returns ({field: value}, ner_off_justification)."""
    import yaml
    try:
        doc = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        raise ProfileError("deployment profile is not valid YAML") from None
    if not isinstance(doc, dict) or doc.get("kind") != PROFILE_DOC_KIND:
        raise ProfileError(f"deployment profile must declare kind: {PROFILE_DOC_KIND}")
    extra_top = set(doc) - {"kind", "version", "settings", "ner_off_justification"}
    if extra_top or not isinstance(doc.get("version"), int) or not isinstance(doc.get("settings"), dict):
        raise ProfileError("deployment profile needs exactly: kind, version (int), settings (mapping), "
                           "and optionally ner_off_justification")
    raw = doc["settings"]
    unknown = sorted(set(raw) - set(PROFILE_FIELDS))
    missing = [f for f in PROFILE_FIELDS if f not in raw]
    if unknown:
        raise ProfileError(f"deployment profile: unknown setting(s) {unknown} (only security-relevant settings "
                           "belong in the profile)")
    if missing:
        raise ProfileError(f"deployment profile must state every security-relevant setting; missing {missing}")
    vals = {f: _check(f, PROFILE_KIND[f], raw[f]) for f in PROFILE_FIELDS}
    just = doc.get("ner_off_justification")
    if just is not None and not isinstance(just, str):
        raise ProfileError("deployment profile: ner_off_justification must be text")
    return vals, just


def _norm(kind: str, v, base: Path | None, from_profile: bool):
    if v is None:
        return None
    k = kind.rstrip("?")
    if k == "path":
        p = Path(v)
        return str(((base / p) if from_profile and base is not None else p).resolve())
    if k == "hosts":
        return tuple(sorted({str(h).strip().lower() for h in v}))
    if k == "float":
        return float(v)
    if k == "str":
        return str(v).strip()
    return v


_DEFAULTS = {f.name: f.default for f in dataclasses.fields(Settings)}


def _is_set(settings: Settings, name: str) -> bool:
    env_set = getattr(settings, "_env_set", None)
    if env_set is not None:          # built by from_env: set means the variable was present
        return name in env_set
    return getattr(settings, name) != _DEFAULTS[name]


def profile_differences(settings: Settings, vals: dict, base: Path | None, only_set: bool) -> list[str]:
    """Profile fields whose Settings value differs from the profile (only the ones actually set, if only_set)."""
    out = []
    for f in PROFILE_FIELDS:
        if only_set and not _is_set(settings, f):
            continue
        k = PROFILE_KIND[f]
        if _norm(k, getattr(settings, f), base, False) != _norm(k, vals[f], base, True):
            out.append(f)
    return out


def apply_deployment_profile(settings: Settings, data: bytes | None, base: Path | None) -> tuple[Settings, list[str]]:
    """Production: return Settings whose security-relevant fields are exactly the (already verified) profile's, or
    raise ProfileError. Development: return the Settings unchanged and the names of fields that differ."""
    prod = settings.mode == "production"
    if data is None:
        if prod:
            raise ProfileError(f"production requires the signed deployment profile {PROFILE_REL}")
        return settings, []
    vals, just = parse_profile(data)
    if not prod:
        return settings, profile_differences(settings, vals, base, only_set=False)
    diff = profile_differences(settings, vals, base, only_set=True)
    if diff:
        f = diff[0]
        src = f"environment variable {PROFILE_ENV[f]} or Settings.{f}" if PROFILE_ENV[f] else f"Settings.{f}"
        raise ProfileError(f"setting {f!r} (from {src}) differs from the signed deployment profile {PROFILE_REL}; "
                           f"production takes it from the profile only. Unset it, or change the profile, have the "
                           f"change reviewed and re-signed (OPERATIONS.md)"
                           + (f" [also differ: {diff[1:]}]" if len(diff) > 1 else ""))
    if vals["ner_model"] is None:
        if not isinstance(just, str) or len(just.strip()) < MIN_JUSTIFICATION:
            raise ProfileError("production refuses NER off (ner_model: null) unless the signed deployment profile "
                               f"gives ner_off_justification (at least {MIN_JUSTIFICATION} characters)")
    elif just is not None:
        raise ProfileError("deployment profile: ner_off_justification is only valid with ner_model: null")
    eff = {f: (str(base / v) if v is not None and PROFILE_KIND[f].startswith("path") else v)
           for f, v in vals.items()}
    return dataclasses.replace(settings, **eff), []
