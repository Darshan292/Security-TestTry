"""Starlette app for the privacy gateway: POST /v1/chat/completions.

Fail-closed everywhere: any detector/ledger exception -> 503, egress hit -> 500, and no error body
or log line ever contains request text or values (ADR-0007).
"""
from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import hashlib
import hmac
import json
import re
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from anyio import to_thread
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ..detect import idshape, structure
from ..detect.edm import RestoreLedger, RosterIndex
from ..detect.recognizers import detect_d2
from ..egress import blank_own, check as egress_check
from ..pipeline import ConversationIdRequired, Keys, Protector, UnsupportedContent
from ..policy import PolicyPack, UnknownPurpose
from ..stream import Restorer, compact_id
from ..product import attest, integrity, local_compute, subjects
from ..product import records as _records
from ..product.content import unreliable_script
from ..product.contracts import CLASS_OF, Contract, ContractError, DataClass, load_contract
from ..types import BlockedRequest, Context, EgressViolation, EntityType
from . import report as _report
from . import rollback
from .settings import (MODES, Settings, apply_deployment_profile, check_destination, check_private_destination,
                       profile_address_ack, profile_location, profile_serial)
from .upstream import (AnthropicUpstream, MockLLMUpstream, OpenAICompatUpstream, Upstream, UpstreamError,
                       is_standin_entry, private_identity)

log = logging.getLogger("pgw.gateway")

# Demo-only step tracing (demo/ui_server.py). Enabled only programmatically via Settings.trace AND a per-request
# X-PGW-Trace header; never from the environment. Trace data includes content: use only on a trusted local machine.
_TRACE: contextvars.ContextVar = contextvars.ContextVar("pgw_trace", default=None)


def _tr(stage: str, **data) -> None:
    fn = _TRACE.get()
    if fn is None:
        return
    try:
        fn(stage, data)
    except Exception as e:  # a broken observer must never affect the request
        log.warning("event='trace_failed' error_type=%r", type(e).__name__)

FORWARDED = ("model", "stream", "temperature", "max_tokens", "max_completion_tokens", "top_p", "stop", "tools",
             "tool_choice", "n", "stream_options")   # what may reach the provider besides the protected messages
ALLOWED_TOP = {"model", "messages", "stream", "temperature", "max_tokens", "max_completion_tokens", "top_p",
               "stop", "tools", "tool_choice", "n", "stream_options",
               "pgw_subjects"}   # gateway-only fields (pgw_*) are never forwarded
LOG_FIELDS = ("event", "request_id", "tenant", "app", "purpose", "model", "status", "decision", "error_type",
              "latency_ms", "gate_events", "stream", "integrity", "ner", "mode", "provider")


def _log(level: int, **fields) -> None:
    """Allow-listed structured logging. Values are coerced to short scalars; nothing free-form."""
    parts = []
    for k in LOG_FIELDS:
        if k in fields and fields[k] is not None:
            v = fields[k]
            parts.append(f"{k}={v if isinstance(v, (int, float, bool)) else str(v)[:64]!r}")
    log.log(level, " ".join(parts))


def _err(status: int, etype: str, message: str, rid: str | None = None, **extra) -> JSONResponse:
    body: dict = {"error": {"type": etype, "message": message, **extra}}
    headers = {"X-PGW-Request-Id": rid} if rid else {}
    return JSONResponse(body, status_code=status, headers=headers)


class _Reject(Exception):
    def __init__(self, status: int, etype: str, message: str, **extra):
        self.status, self.etype, self.message, self.extra = status, etype, message, extra


@dataclass(frozen=True)
class AppEntry:
    app_id: str
    tenant: str
    key_sha256: str
    purposes: frozenset
    restore_output: bool
    models: frozenset
    tasks: tuple = ()      # ((purpose, contract_path), ...): apps with tasks only use contracted purposes
    transparency: bool = False   # return the pgw_report to this app (docs/TRANSPARENCY_REPORT.md); signed opt-in


def load_registry(path: str, data: bytes | None = None) -> list[AppEntry]:
    raw = yaml.safe_load(Path(path).read_text() if data is None else data.decode("utf-8"))
    base = Path(path).resolve().parent
    out = []
    for a in raw["apps"]:
        tasks = tuple(sorted((p, str(c if Path(c).is_absolute() else (base / c).resolve()))
                             for p, c in (a.get("tasks") or {}).items()))
        if any(p not in a["purposes"] for p, _ in tasks):
            raise ValueError(f"app {a['app_id']}: every task purpose must be a registered purpose")
        transparency = a.get("transparency", False)
        if not isinstance(transparency, bool):   # a typo must not turn the report on (or silently off)
            raise ValueError(f"app {a['app_id']}: transparency must be true or false")
        out.append(AppEntry(a["app_id"], a["tenant"], a["key_sha256"].lower(), frozenset(a["purposes"]),
                            bool(a.get("restore_output", True)), frozenset(a["models"]), tasks, transparency))
    return out


_LONG_DIGITS = re.compile(r"(?<!\d)\d(?:[\s\-./]?\d){8,}(?!\d)")

# Words shown instead of a C3 redaction marker that the model copied into its answer (contract may override).
# A neutral word reads correctly after any phrasing ("account (withheld)", "Aadhaar (withheld) was submitted").
REDACTION_TEXT = {"DEFAULT": "(withheld)"}
# Every withheld-value marker a reply can contain: all C3 types plus the identifier backstop's marker. Derived from
# the type table, so a new protected type can never reach the user as a raw "[TYPE]" marker.
_MARKER = re.compile(r"\[(" + "|".join(sorted(
    {t.value for t, c in CLASS_OF.items() if c is DataClass.C3} | {EntityType.IDENTIFIER.value},
    key=len, reverse=True)) + r")\]")
_ORG_SENTINEL = 0xE100
STRUCTURED_MESSAGE = ("the free text contains record-structured data (a table, an export or JSON/XML/key-value "
                      "records); records are not sent to an external model on the free-text route. Send them "
                      "through this purpose's field-contract route or the in-house route; not sent externally")


def render_markers(text, contract) -> str:
    if not isinstance(text, str) or contract is None:
        return text
    table = {**REDACTION_TEXT, **contract.redaction_text}
    return _MARKER.sub(lambda m: table.get(m.group(1), table["DEFAULT"]), text)


def _withheld_markers(text, contract) -> int:
    """Withheld-value markers in an answer before they are rendered as words (transparency report count)."""
    return len(_MARKER.findall(text)) if isinstance(text, str) and contract is not None else 0


def _c3_in(texts) -> bool:
    """True if any C3 value (government/financial identifier, credential) is in these answer texts."""
    return any(CLASS_OF.get(d.type) is DataClass.C3 for t in texts if t for d in detect_d2(t))


PLACEHOLDER_NOTE = ("Tokens in square brackets such as [PERSON_...] are placeholders for confidential values. "
                    "Keep them exactly as written wherever you refer to them; do not guess what they stand for. "
                    "A type name in brackets without a code, such as [BANK_ACCOUNT], marks a value that was withheld "
                    "entirely: never copy it into your answer; refer to it in words (e.g. 'the registered bank "
                    "account').")


def load_contracts(registry: list[AppEntry], policy: PolicyPack, contents: dict | None = None,
                   allow_demo_keys: bool = False) -> tuple[dict[str, Contract], PolicyPack]:
    """Contracts by purpose, and a policy pack extended with their per-type actions. Any problem stops startup.
    A source key whose private half is public (attest.DEMO_PUBKEYS) is refused unless allow_demo_keys (development).

    The map is GLOBAL and keyed by purpose (as is the policy pack's action table): a purpose means the same contract
    for every app and tenant. Tenant-specific contracts are not supported; two bindings of one purpose must be
    byte-identical files, whatever their name/version strings say."""
    contracts: dict[str, Contract] = {}
    digests: dict[str, str] = {}
    for app in registry:
        for purpose, path in app.tasks:
            data = (contents or {}).get(str(Path(path).resolve())) or Path(path).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            c = load_contract(path, data)
            if purpose in policy.purposes:
                raise ContractError(f"purpose {purpose!r} is already defined by the policy pack; contracted "
                                    "purposes must be distinct so contracts cannot change other apps' behaviour")
            if purpose in contracts and (contracts[purpose].ref != c.ref or digests[purpose] != digest):
                raise ContractError(f"purpose {purpose!r} is bound to contract files with different content "
                                    "(tenant-specific contracts are not supported; one purpose = one contract)")
            digests[purpose] = digest
            demo = sorted(s for s, k in c.external_input.keys.items() if k in attest.DEMO_PUBKEYS)
            if demo and not allow_demo_keys:
                raise ContractError(f"purpose {purpose!r}: source key(s) {demo} are the repository's DEMO keys "
                                    "(their private keys are public); replace them with your sources' keys and "
                                    "re-sign the config")
            if demo:
                _log(logging.WARNING, event="demo_source_keys_development_only", purpose=purpose)
            if c.route == "LOCAL_COMPUTE" and c.compute not in local_compute.FUNCTIONS:
                raise ContractError(f"unknown compute function {c.compute!r}")
            contracts[purpose] = c
    if not contracts:
        return contracts, policy
    purposes = {**policy.purposes, **{p: c.actions() for p, c in contracts.items()}}
    return contracts, dataclasses.replace(policy, purposes=purposes)


def build_upstream(s: Settings) -> Upstream:
    """Real providers are only built for an allowlisted https destination (deploy/README.md: the network layer
    must enforce the same rule, so application code cannot reach a provider around the gateway)."""
    if s.upstream is not None:
        return s.upstream  # type: ignore[return-value]
    if s.upstream_kind == "mock":
        return MockLLMUpstream("summarise")
    if s.upstream_kind in ("openai", "anthropic") and not (s.provider_zero_retention and s.provider_region):
        raise ValueError("real provider refused: attest PGW_PROVIDER_ZERO_RETENTION_ATTESTED=1 and set "
                         "PGW_PROVIDER_REGION (contracted data terms; see OPERATIONS.md)")
    if s.upstream_kind == "openai":
        check_destination(s.upstream_url, s.egress_allowlist)
        return OpenAICompatUpstream(s.upstream_url, s.openai_api_key, timeout=s.upstream_timeout_s)
    if s.upstream_kind == "anthropic":
        check_destination(s.anthropic_base_url, s.egress_allowlist)
        return AnthropicUpstream(s.anthropic_api_key, base_url=s.anthropic_base_url, timeout=s.upstream_timeout_s)
    raise ValueError("PGW_UPSTREAM must be mock|openai|anthropic")


def _text_size(x) -> int:
    """Characters in every string inside a messages structure (bounded by the body-size cap)."""
    if isinstance(x, str):
        return len(x)
    if isinstance(x, list):
        return sum(_text_size(v) for v in x)
    if isinstance(x, dict):
        return sum(_text_size(v) for v in x.values())
    return 0


_NUM = (int, float)
_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _is_num(v, lo, hi) -> bool:
    return isinstance(v, _NUM) and not isinstance(v, bool) and lo <= v <= hi


def _is_int(v, lo, hi) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def check_parameters(body: dict, contracted: bool) -> None:
    """Every non-message field leaves the building with the request, so each one is held to a strict type and
    range: a parameter can never carry free text (audit finding: values in temperature/max_tokens went out raw).
    Contracted purposes take no tools, tool_choice or stop at all: no contract needs them and they are free text.
    Raises _Reject(400); the message never echoes a value."""
    def bad(name):
        raise _Reject(400, "invalid_parameter", f"{name}: invalid type or value")
    if "stream" in body and not isinstance(body["stream"], bool):
        bad("stream")
    if "temperature" in body and not _is_num(body["temperature"], 0, 2):
        bad("temperature")
    if "top_p" in body and not _is_num(body["top_p"], 0, 1):
        bad("top_p")
    for k in ("max_tokens", "max_completion_tokens"):
        if k in body and not _is_int(body[k], 1, 200_000):
            bad(k)
    if "n" in body and not _is_int(body["n"], 1, 1):
        raise _Reject(400, "unsupported_parameter", "n must be 1")
    if "stream_options" in body:
        so = body["stream_options"]
        if not isinstance(so, dict) or set(so) - {"include_usage"} or not isinstance(so.get("include_usage", False), bool):
            bad("stream_options")
    if "stop" in body:
        st = body["stop"]
        items = [st] if isinstance(st, str) else st
        if not isinstance(items, list) or len(items) > 4 or not all(isinstance(x, str) and len(x) <= 32 for x in items):
            bad("stop")
    if contracted and any(k in body for k in ("tools", "tool_choice", "stop")):
        # tools and stop sequences are free text that no detector here fully covers (NER does not scan them)
        raise _Reject(400, "unsupported_parameter", "tools and stop are not available for contracted purposes")
    if "tool_choice" in body:
        tc = body["tool_choice"]
        ok = tc in ("none", "auto", "required") or (
            isinstance(tc, dict) and set(tc) == {"type", "function"} and tc["type"] == "function"
            and isinstance(tc["function"], dict) and set(tc["function"]) == {"name"}
            and isinstance(tc["function"]["name"], str) and _TOOL_NAME.fullmatch(tc["function"]["name"]))
        if not ok:
            bad("tool_choice")
    if "tools" in body and not isinstance(body["tools"], list):
        bad("tools")


def _sealed_paths(settings: Settings, contracts_paths) -> list[str]:
    return [settings.registry_path, settings.policy_path, *sorted(set(contracts_paths))]


def check_startup_mode(settings: Settings, registry: list[AppEntry]) -> None:
    """Production invariants that no configuration may relax. Raises ValueError (startup stops)."""
    if settings.mode not in MODES:
        raise ValueError(f"PGW_MODE must be one of {MODES}")
    prod = settings.mode == "production"
    if settings.legacy_uncontracted and prod:
        raise ValueError("the legacy uncontracted path is development-only and cannot be enabled in production")
    if prod and settings.integrity_manifest is None:
        raise ValueError("production requires the integrity manifest")
    if prod and settings.trace is not None:
        raise ValueError("the trace hook is development-only")
    if not settings.legacy_uncontracted:
        for a in registry:   # every registered purpose must be bound to a data contract
            missing = sorted(a.purposes - {p for p, _ in a.tasks})
            if missing:
                raise ContractError(f"app {a.app_id!r}: purposes without a data contract: {missing} "
                                    "(uncontracted purposes are not served)")


def load_roster(path: str, key: bytes, data: bytes | None = None) -> RosterIndex:
    idx = RosterIndex(key)
    for line in (Path(path).read_text() if data is None else data.decode("utf-8")).splitlines():
        if line.strip():
            idx.add_employee(json.loads(line))
    return idx


from ..audit import ChainedLedger as _ChainedLedger   # noqa: E402


def probe_private(private, private_model: str, production: bool) -> str:
    """Identity of the in-boundary endpoint (re-audit N2): "model", "standin" or "unverified" (development only).

    The endpoint answers GET /v1/models (OpenAI-compatible; vLLM, Ollama, llama.cpp server and pgw.private_llm all
    do). pgw.private_llm without model weights always reports the stand-in id/backend marker, fixed in code.
    Production refuses a stand-in, an endpoint that cannot be probed and a malformed answer (ValueError)."""
    identify = getattr(private, "identify", None)
    try:
        if identify is None:
            raise UpstreamError("private endpoint exposes no identity probe")
        kind, ids = private_identity(identify())
    except Exception as e:
        if production:
            raise ValueError("production refuses a private (in-boundary) endpoint whose identity cannot be verified "
                             f"(GET /v1/models: {type(e).__name__}); see OPERATIONS.md") from None
        _log(logging.WARNING, event="private_model_unverified_development_only", error_type=type(e).__name__)
        return "unverified"
    if kind == "standin":
        if production:
            raise ValueError("production refuses the extractive stand-in as the private (in-boundary) model: a "
                             "contract routes content to PRIVATE, so a real model endpoint is required "
                             "(OPERATIONS.md, 'In-boundary model')")
        for _ in range(3):   # loud on purpose: answers on the private route are NOT from a language model
            _log(logging.WARNING, event="PRIVATE_MODEL_IS_EXTRACTIVE_STANDIN_NOT_AN_LLM_development_only")
        return "standin"
    if private_model not in ids:
        _log(logging.WARNING, event="private_model_not_listed_by_endpoint", model=private_model[:64])
    return "model"


class EgressLedger(_ChainedLedger):
    """Append-only JSONL, one line per upstream call. Metadata only, never content. Hash-chained, fsync'd and
    MAC'd with the platform audit key when a tenant key file is configured (pgw.audit); a log that does not
    verify stops startup."""


@dataclass
class _ChoiceState:
    restorer: Restorer
    tools: dict[int, dict] = field(default_factory=dict)
    finish: str | None = None


def create_app(settings: Settings) -> Starlette:
    # Provider SDK/HTTP client loggers never log above WARNING here (their debug output includes request bodies).
    for name in ("anthropic", "httpx", "httpcore", "openai"):
        logging.getLogger(name).setLevel(max(logging.WARNING, logging.getLogger(name).level))
    if settings.mode not in MODES:
        raise ValueError(f"PGW_MODE must be one of {MODES}")
    prod = settings.mode == "production"
    # Integrity first: nothing from an unsealed or modified registry/policy/contract file is acted on. Each file is
    # read exactly once; the bytes that are verified are the bytes that are parsed (no swap between check and use).
    snapshot: dict[str, bytes] = {}

    def read(path) -> bytes:
        key_ = str(Path(path).resolve())
        if key_ not in snapshot:
            snapshot[key_] = Path(path).read_bytes()
        return snapshot[key_]

    hmac_key = bytes.fromhex(settings.policy_signing_key) if settings.policy_signing_key else None
    # Deployment profile (audit S4): in production every security-relevant setting comes from the sealed, signed
    # registry/deployment.yaml; an environment value that differs stops startup. It is verified before any other
    # setting is used, so the registry, policy and roster paths below are already the signed ones.
    check_startup_mode(settings, [])   # mode invariants first (development switches, manifest required)
    loc = profile_location(settings)
    if prod and loc is None:
        raise ValueError("production requires the integrity manifest")
    profile_bytes = None
    if loc is not None and loc[1].is_file():
        profile_bytes = read(loc[1])
        if prod:
            integrity.verify([loc[1]], settings.integrity_manifest, hmac_key, settings.require_signed_policy,
                             trusted_pubkey=settings.trusted_config_pubkey, require_trusted=True,
                             contents=snapshot)
    settings, differs = apply_deployment_profile(settings, profile_bytes, loc[0] if loc else None)
    for name in differs:
        log.warning("event='deployment_profile_differs_development_only' setting=%r", name)
    # Rollback (re-audit N5): an older, validly signed tree is refused by the pinned minimum serial and by the
    # persisted high-water mark (pgw.gateway.rollback). Recorded only after every other startup check passed.
    config_serial = profile_serial(profile_bytes)
    for event in rollback.check(config_serial, settings.min_config_serial, settings.state_dir, prod):
        _log(logging.WARNING, event=event)
    # a state directory this gateway has never started production with (no config high-water mark yet): the only
    # case in which a missing disclosure-control history may be created silently (pgw.product.disclosure)
    fresh_state = bool(prod and settings.state_dir and rollback.read_high_water(settings.state_dir) is None)

    registry = load_registry(settings.registry_path, read(settings.registry_path))
    sealed = _sealed_paths(settings, [c for a in registry for _, c in a.tasks])
    # Field schemas and computation rules of RECORDS contracts (docs/HRMS_CONTRACTS.md): every file under
    # registry/schemas and registry/rules is sealed, used or not, so an unsealed one stops startup too.
    schema_root = Path(settings.registry_path).resolve().parent
    sealed += _records.schema_files(schema_root)
    if loc is not None and profile_bytes is not None:
        sealed.append(str(loc[1]))
    # The roster is a detection input: swapping it for an empty one silently disables roster matching (audit S4).
    # Production seals it like the rest of the config; development may run an unsealed roster (warned).
    if settings.roster_path and prod:
        sealed.append(settings.roster_path)
    elif settings.roster_path:
        _log(logging.WARNING, event="roster_unsealed_development_only")
    for p in sealed:
        read(p)
    if settings.integrity_manifest is not None:
        integrity_ref = integrity.verify(sealed, settings.integrity_manifest, hmac_key, settings.require_signed_policy,
                                         trusted_pubkey=settings.trusted_config_pubkey, require_trusted=prod,
                                         contents=snapshot)
        if not settings.trusted_config_pubkey:
            _log(logging.WARNING, event="integrity_manifest_untrusted_development_only")
    else:
        integrity_ref = "unchecked"
    check_startup_mode(settings, registry)
    if settings.integrity_manifest is None:
        _log(logging.WARNING, event="integrity_check_disabled_development_only")
    if settings.legacy_uncontracted:
        _log(logging.WARNING, event="legacy_uncontracted_path_enabled_development_only")
    policy = PolicyPack.load(settings.policy_path, read(settings.policy_path))
    from ..keys import build_keys   # per-tenant key file if configured, else the single master key
    keys: Keys = build_keys(settings, registry)   # or pgw.keys.TenantKeyring: same interface
    contracts, policy = load_contracts(registry, policy, snapshot, allow_demo_keys=not prod)
    record_plans = {p: _records.bind(p, c, schema_root, read) for p, c in contracts.items() if c.route == "RECORDS"}
    for p in contracts:   # contracted (RESTRICTED-by-default) purposes never stream unrestored text
        if policy.stream_mode(p) != "buffered":
            raise ContractError(f"purpose {p!r}: contracted purposes must use buffered output")
    roster = (load_roster(settings.roster_path, keys.derive("*", "edm"), read(settings.roster_path))
              if settings.roster_path else None)
    ledger = RestoreLedger(keys.derive("*", "ledger"), settings.ledger_db_path, keys=keys)
    # Plugin slots (docs/PLUGINS.md): outside validators/recognizers, modes from the signed profile only. An unknown
    # plugin or mode, or a configured plugin whose library is missing, stops startup (PluginConfigError).
    from .. import plugins as _plugins
    plugin_set = _plugins.build(settings.plugins)
    if plugin_set is not None:
        log.warning("event='plugins_enabled' modes=%r", sorted(plugin_set.modes.items()))
    protector = Protector(policy, keys, roster, ledger, plugins=plugin_set)
    # Contracted purposes (Path A and the free-text field of Path B) also get the local NER. A model that does
    # not load stops startup (NerUnavailable); a model that fails at request time fails that request closed.
    contract_protector = protector
    # ML layer (docs/ML_ARCHITECTURE.md). Each model's directory is verified against the sha256 pinned in the signed
    # profile before anything else uses it: a mismatch stops startup (pgw.ml.artifact.ArtifactError).
    from .settings import check_ml_settings
    check_ml_settings(settings)
    ml_detector = gate = None
    ml_cap = settings.protect_timeout_s * 0.8
    if contracts and settings.ml_detector is not None:
        from ..ml.config import parse_detector
        from ..ml.runtime import open_detector
        ml_detector = open_detector(parse_detector(settings.ml_detector), ml_cap)
    from . import coverage as _coverage   # address recall the contracts need (requires_detectors)
    coverage, cov_event = _coverage.check(contracts, settings.ml_detector, prod, profile_address_ack(profile_bytes))
    if cov_event:
        _log(logging.WARNING, event=cov_event)
    gated = {p: c for p, c in contracts.items() if c.content_gate is not None}
    if gated:
        if settings.content_gate_model is None:
            raise ContractError(f"purpose(s) {sorted(gated)} declare a content_gate but no content_gate_model is "
                                "configured in the signed deployment profile")
        from ..ml.config import parse_gate
        gate_cfg = parse_gate(settings.content_gate_model)
        for p, c in gated.items():
            unknown = sorted(set(c.content_gate["labels"]) - set(gate_cfg.labels))
            if unknown:
                raise ContractError(f"purpose {p!r}: content_gate labels {unknown} are not labels of the configured "
                                    "content_gate_model")
        from ..ml.runtime import open_gate
        gate = open_gate(gate_cfg, ml_cap)
    if contracts and settings.ner_model:
        from ..detect.ner import NerDetector
        if settings.ner_workers > 0:   # bounded worker processes (fail closed when full/slow); 0 = in-process
            from ..detect.ner_pool import NerPool
            ner = NerPool(settings.ner_model, settings.ner_workers, settings.ner_queue_max,
                          min(settings.ner_timeout_s, settings.protect_timeout_s * 0.8))
        else:
            ner = NerDetector(settings.ner_model)
            ner.warm()
        contract_protector = Protector(policy, keys, roster, ledger, ner=ner, ml=ml_detector,
                                       heuristics=settings.legacy_heuristics, plugins=plugin_set)
    elif contracts and ml_detector is not None:   # the ML detector replaces spaCy (signed profile decision)
        _log(logging.WARNING, event="ner_replaced_by_ml_detector")
        contract_protector = Protector(policy, keys, roster, ledger, ml=ml_detector,
                                       heuristics=settings.legacy_heuristics, plugins=plugin_set)
    elif contracts:   # production reaches this only with a signed ner_off_justification in the profile
        _log(logging.WARNING, event="ner_disabled_for_contracted_purposes")
    contract_protector.pin_block = coverage["ADDRESS"] == _coverage.PIN_ONLY   # structure-only block around a PIN
    upstream = build_upstream(settings)
    if (not prod and not isinstance(upstream, MockLLMUpstream) and not settings.dev_allow_real_provider):
        # Development config may be unsigned, so it must stay isolated: no real provider unless set in code.
        raise ValueError("development mode only reaches the mock provider (dev_allow_real_provider is code-only)")
    if prod and settings.dev_allow_real_provider:
        raise ValueError("dev_allow_real_provider is a development-only switch")
    private = settings.private_upstream
    if private is None and settings.private_upstream_url:
        check_private_destination(settings.private_upstream_url, settings.private_allowlist,
                                  settings.egress_allowlist)
        import httpx   # in-boundary traffic must never be routed through an egress proxy from the environment
        private = OpenAICompatUpstream(settings.private_upstream_url, "", timeout=settings.upstream_timeout_s,
                                       client=httpx.AsyncClient(timeout=settings.upstream_timeout_s, trust_env=False))
    for p, c in contracts.items():
        if c.classes[DataClass.C4] == "PRIVATE" and private is None:
            raise ContractError(f"purpose {p!r} routes C4 content privately but no private endpoint is configured")
        if c.external_input.unattested == "PRIVATE" and private is None:
            raise ContractError(f"purpose {p!r} routes unattested input privately but no private endpoint is "
                                "configured")
    for p, c in gated.items():
        if c.content_gate["action"] == "PRIVATE" and private is None:
            raise ContractError(f"purpose {p!r} routes gated content privately but no private endpoint is configured")
    needs_private = any(c.classes[DataClass.C4] == "PRIVATE" or c.external_input.unattested == "PRIVATE"
                        or (c.content_gate or {}).get("action") == "PRIVATE"
                        or (c.structured_content == "PRIVATE" and private is not None) for c in contracts.values())
    for p, c in contracts.items():   # without an in-house model, structured content is refused (never sent out)
        if c.structured_content == "PRIVATE" and private is None:
            _log(logging.WARNING, event="structured_content_private_without_endpoint_refuses", purpose=p[:64])
    private_kind = probe_private(private, settings.private_model, prod) if needs_private else None
    egress_ledger = EgressLedger(settings.egress_ledger_path, keys if settings.tenant_keys_file else None)
    slots = threading.BoundedSemaphore(settings.max_concurrent_protect)
    if prod:   # every startup check above passed: raise the high-water mark (atomic, fsync'd, locked)
        if rollback.record(config_serial, settings.state_dir):
            _log(logging.WARNING, event="config_high_water_initialised")
    _log(logging.INFO if prod else logging.WARNING, event="started", mode=settings.mode, integrity=integrity_ref,
         ner=settings.ner_model if contracts else None, provider=upstream.name)

    async def bounded(fn, *args, timeout: float | None = None):
        """Run CPU work in a worker with a deadline. The slot is released only when the worker really finishes,
        so a request that times out still occupies capacity and cannot be multiplied into a CPU exhaustion.
        timeout: a sealed per-contract deadline (RECORDS compute); default the profile's protect_timeout_s."""
        if not slots.acquire(blocking=False):
            raise _Reject(503, "gateway_busy", "gateway at capacity; request not sent")

        def work():
            try:
                return fn(*args)
            finally:
                slots.release()
        try:
            return await asyncio.wait_for(to_thread.run_sync(work, abandon_on_cancel=True),
                                          timeout or settings.protect_timeout_s)
        except TimeoutError:
            raise _Reject(503, "protection_timeout", "protection took too long; request not sent") from None

    async def read_body(request: Request, limit: int | None = None) -> bytes:
        limit = limit or settings.max_body_bytes
        cl = request.headers.get("content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > limit):
            raise _Reject(413, "request_too_large", "request body exceeds the size limit")
        buf, n = [], 0
        async for chunk in request.stream():
            n += len(chunk)
            if n > limit:
                raise _Reject(413, "request_too_large", "request body exceeds the size limit")
            buf.append(chunk)
        return b"".join(buf)

    def authenticate(request: Request) -> AppEntry:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer ") or not auth[7:].strip():
            raise _Reject(401, "unauthorized", "missing or invalid credentials")
        digest = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
        found = None
        for a in registry:   # constant-time compare against every entry
            if hmac.compare_digest(a.key_sha256, digest):
                found = a
        if found is None:
            raise _Reject(401, "unauthorized", "missing or invalid credentials")
        return found

    def ledger_line(rid, app, purpose, model, body_hash, counts, decision, ms, reason=None, contract=None,
                    n_subjects=None, input_source=None, attestation=None):
        try:
            extra = {"contract": contract.ref, "route": contract.route} if contract is not None else {}
            if input_source is not None:   # only ever set after the source's signature verified
                extra["input_source"] = input_source
                extra["input_verified"] = True
            if attestation is not None:    # content-free reason the attestation failed
                extra["input_attestation"] = attestation
            if n_subjects is not None:
                extra["declared_subjects"] = n_subjects
            egress_ledger.write(request_id=rid, tenant=app.tenant, app=app.app_id, purpose=purpose, model=model,
                                provider=upstream.name, body_sha256=body_hash, entity_counts=counts,
                                provider_terms={"region": settings.provider_region,
                                                "zero_retention_attested": settings.provider_zero_retention},
                                policy=policy.ref, integrity=integrity_ref, mode=settings.mode, decision=decision,
                                latency_ms=round(ms, 2), reason=reason, ts=round(time.time(), 3), **extra)
        except Exception as e:  # audit write failure must not leak content; fail closed
            _log(logging.ERROR, event="egress_ledger_write_failed", request_id=rid, error_type=type(e).__name__)
            raise _Reject(503, "ledger_unavailable", "audit ledger unavailable") from None

    if settings.trace is not None:
        _log(logging.WARNING, event="trace_hook_enabled_demo_only")
    # Single-use attestations. Production needs the persistent store (shared by every process on the host and
    # surviving restarts); the in-memory cache is development-only (SECURITY_BOUNDARY.md §4).
    if settings.replay_db_path:
        replay_cache = attest.SqliteReplayStore(settings.replay_db_path, attest.REPLAY_CACHE_MAX)
    elif prod and contracts:
        raise ValueError("production requires a persistent attestation replay store (PGW_REPLAY_DB)")
    else:
        replay_cache = attest.ReplayCache(attest.REPLAY_CACHE_MAX)
    # Disclosure-control query history (SECURITY_BOUNDARY.md §12): durable, per (tenant, app, purpose, dataset), shared
    # by every process on the host. Production needs a persistent file; development without a state directory keeps
    # it in memory (lost on restart, warned).
    disclosure_history = None
    if any(p.control is not None and p.control.differencing for p in record_plans.values()):
        dpath = settings.disclosure_db_path or (str(Path(settings.state_dir) / _records.disclosure.HISTORY_DB_NAME)
                                                if settings.state_dir else None)
        if dpath is None and prod:
            raise ValueError("production requires a persistent disclosure-control history (PGW_STATE_DIR or "
                             "PGW_DISCLOSURE_DB)")
        if dpath is None:
            _log(logging.WARNING, event="disclosure_history_in_memory_development_only")
        # integrity at startup: a missing, replaced or older history (vs the high-water mark in the state directory)
        # stops startup in production (HistoryIntegrityError); recovery is the deliberate, logged
        # `python -m pgw.product.disclosure history recover`
        disclosure_history = _records.disclosure.HistoryStore(dpath or ":memory:", state_dir=settings.state_dir,
                                                              production=prod, fresh=fresh_state)
        _sz = disclosure_history.sizes()
        log.info("event='disclosure_history_opened' persistent=%r scopes=%d cells=%d", dpath is not None,
                 _sz["scopes"], _sz["cells"])

    def history_scope(app: AppEntry, purpose: str):
        """Who asks, for the differencing history of a RECORDS request: the authenticated app's tenant and id (the
        rate-limited caller; the gateway has no end-user identity: the workspace authenticates users by SSO but calls
        with one app key) and the purpose. The scope itself is (tenant, dataset) unless the sealed config separates
        apps or purposes. Never the conversation id: it is caller-chosen."""
        if disclosure_history is None:
            return None
        tenant = app.tenant

        def keyfn(key_id):
            kid = key_id or keys.active_id(tenant)
            return kid, keys.derive(tenant, _records.disclosure.KEY_PURPOSE, kid)
        return _records.disclosure.HistoryScope(disclosure_history, tenant, app.app_id, purpose, keyfn)

    def report_of(rep, outcome: str, error: dict | None = None) -> dict | None:
        """The finished pgw_report, or None if building it failed (logged by type only; the response is not
        otherwise affected). Never called for an app without transparency."""
        try:
            return rep.build(outcome, error)
        except Exception as e:
            _log(logging.ERROR, event="report_failed", request_id=rep.rid, error_type=type(e).__name__)
            return None

    def refused(rep, status: int, etype: str, message: str, extra: dict) -> dict:
        if not rep.enabled:
            return extra
        rep.fail_open()
        outcome = "refused" if status < 500 or etype == "PGW_EGRESS_VIOLATION" else "error"
        return {**extra, "pgw_report": report_of(rep, outcome, {"type": etype, "message": message})}

    async def chat(request: Request) -> Response:
        t_start = time.perf_counter()
        rid = "req_" + uuid.uuid4().hex[:24]
        token = None
        box: list = [_report.NULL]   # the request's report once the app is authenticated and has opted in
        if settings.trace is not None and request.headers.get("x-pgw-trace"):
            run = request.headers["x-pgw-trace"]
            token = _TRACE.set(lambda stage, data: settings.trace(run, stage, data))
        try:
            resp = await _chat(request, rid, box, t_start)
        except _Reject as r:
            _log(logging.INFO, event="reject", request_id=rid, status=r.status, error_type=r.etype)
            _tr("error", status=r.status, error_type=r.etype, message=r.message)
            return _err(r.status, r.etype, r.message, rid, **refused(box[0], r.status, r.etype, r.message, r.extra))
        except Exception as e:    # never include exception text: it may carry request data
            _log(logging.ERROR, event="internal_error", request_id=rid, error_type=type(e).__name__)
            _tr("error", status=500, error_type="internal_error", message="internal error")
            return _err(500, "internal_error", "internal error", rid,
                        **refused(box[0], 500, "internal_error", "internal error", {}))
        finally:
            if token is not None:
                _TRACE.reset(token)
        return resp

    async def _chat(request: Request, rid: str, box: list, t_start: float) -> Response:
        app = authenticate(request)
        # Nothing before this point (no app known: 401, pre-auth failures) ever carries a report.
        rep = box[0] = _report.Report(rid, t_start, coverage) if app.transparency else _report.NULL
        purpose = request.headers.get("x-pgw-purpose", "").strip()
        if not purpose:
            raise _Reject(400, "purpose_required", "X-PGW-Purpose header is required")
        if purpose not in app.purposes:
            raise _Reject(403, "purpose_forbidden", "purpose not permitted for this app")
        if rep.enabled:
            rep.purpose = purpose
        # Mandatory data contract, resolved before the body is even read: an uncontracted purpose never reaches
        # detection, a provider or the ledger. The legacy path exists only in an explicit development mode.
        tasks = dict(app.tasks)
        if purpose in tasks:
            contract: Contract | None = contracts[purpose]
        elif settings.legacy_uncontracted and settings.mode == "development":
            contract = None
        else:
            raise _Reject(403, "no_data_contract", "purpose has no data contract for this app")
        if rep.enabled:
            rep.contract, rep.route = contract, contract.route if contract is not None else "EXT_PSEUDO"
        _tr("auth", app=app.app_id, tenant=app.tenant, purpose=purpose)
        conv = request.headers.get("x-pgw-conversation-id", "").strip() or None
        if conv and len(conv) > 128:
            raise _Reject(400, "invalid_conversation_id", "conversation id too long")

        # RECORDS contracts carry whole HRMS exports (only small computed results leave): their sealed size bound
        # replaces the profile's free-text limits for the body and the message content.
        plan = record_plans.get(purpose) if contract is not None else None
        raw_body = await read_body(request, plan.spec.max_bytes if plan is not None else None)
        try:
            body = json.loads(raw_body)
        except ValueError:
            raise _Reject(400, "invalid_request", "body is not valid JSON") from None
        if not isinstance(body, dict):
            raise _Reject(400, "invalid_request", "body must be a JSON object")
        extra = set(body) - ALLOWED_TOP
        if extra:   # key NAMES are client-chosen but never values; still, do not echo them
            raise _Reject(400, "unsupported_parameter", f"{len(extra)} unsupported top-level field(s)")
        model = body.get("model")
        if not isinstance(model, str) or not isinstance(body.get("messages"), list) or not body["messages"]:
            raise _Reject(400, "invalid_request", "model (string) and non-empty messages (list) are required")
        check_parameters(body, contract is not None)
        if model not in app.models:
            raise _Reject(403, "model_forbidden", "model not permitted for this app")
        if len(body["messages"]) > settings.max_messages:
            raise _Reject(413, "content_too_large", "too many messages")
        if _text_size(body["messages"]) > (plan.spec.max_bytes if plan is not None else settings.max_content_chars):
            raise _Reject(413, "content_too_large", "message content exceeds the size limit")
        stream = bool(body.get("stream", False))

        # tools/stop are not message content, but they still leave the building: they must be clean.
        aux = [json.dumps(body[k], ensure_ascii=False) for k in ("tools", "stop") if k in body]
        try:
            dirty = any(detect_d2(a) or (roster is not None and roster.detect(a)) for a in aux)
        except Exception:
            raise _Reject(503, "detector_unavailable", "protection unavailable; request not sent") from None
        if dirty:
            raise _Reject(400, "sensitive_data_in_parameters", "tools/stop contain data that must not be sent")

        ctx = Context(app.tenant, app.app_id, purpose, conv, app.restore_output)
        t0 = time.perf_counter()

        # --- data contract and route (PRODUCT_ARCHITECTURE §2); every failure here is before any send ---
        if contract is not None:
            _tr("contract", contract=contract.ref, route=contract.route, task_type=contract.task_type,
                classes={k.value: v for k, v in contract.classes.items()},
                restore=sorted(c.value for c in contract.restore), guidance=bool(contract.guidance),
                org_contacts=len(contract.org_contacts), public_constants=len(contract.public_constants))
        messages, declared, withheld, subj, raw_outputs = body["messages"], None, [], [], None
        record_result = None
        input_source = None
        if contract is not None:
            ctx = dataclasses.replace(ctx, alias_ns=contract.ref)
        if "pgw_subjects" in body and (contract is None or contract.subjects is None):
            raise _Reject(400, "declared_subjects_not_supported", "this purpose's data contract takes no subjects")
        if contract is not None and contract.subjects is not None:
            try:
                subj = subjects.parse(body.get("pgw_subjects") or {}, contract.subjects)
            except subjects.SubjectsError as e:
                raise _Reject(400, "invalid_declared_subjects", str(e)) from None
            declared = subjects.matchers(subj)
            roles: dict = {}
            for role, _t, _v in subj:
                roles[role] = roles.get(role, 0) + 1
            _tr("subjects", declared_values=len(subj), roles=roles, matchers=len(declared))
            if subjects.found_in(aux, declared):
                raise _Reject(400, "sensitive_data_in_parameters", "tools/stop contain data that must not be sent")
        if contract is None:
            rep.end("received")
            rep.skip("verify_source", "compute")
        if contract is not None:
            # every string, incl. list-form content parts and tool args. A RECORDS payload is not free text: its values
            # are classified by the field schema, so the script and content checks below do not read it.
            texts = subjects.request_texts(messages) if plan is None else []
            if _text_size(messages) > (plan.spec.max_bytes if plan is not None else contract.max_chars):
                raise _Reject(413, "content_too_large", "request exceeds the data contract's size limit")
            rep.end("received")
            rep.begin("verify_source")
            # NOT_EXPECTED attributes (medical, disciplinary, ...) are not detected by the gateway. Free text goes
            # to an external model only with a valid signature, by a source key this contract pins, over exactly
            # these messages and subjects; otherwise it is refused or kept in-boundary (SECURITY_BOUNDARY.md §4).
            # Verified here, on the parsed body, before any send and before anything rewrites the messages.
            h = request.headers
            try:
                input_source = attest.verify(contract.external_input.keys, purpose,
                                             h.get("x-pgw-input-source", "").strip(),
                                             h.get("x-pgw-input-timestamp", "").strip(),
                                             h.get("x-pgw-input-signature", "").strip(),
                                             body["messages"], body.get("pgw_subjects"), app_id=app.app_id,
                                             nonce=h.get("x-pgw-input-nonce", "").strip(), replay=replay_cache)
            except attest.ReplayCacheFull:
                _log(logging.ERROR, event="attestation_replay_cache_full", request_id=rid)
                raise _Reject(503, "gateway_busy", "attestation replay cache full; request not sent") from None
            except attest.ReplayStoreUnavailable:
                _log(logging.ERROR, event="attestation_replay_store_unavailable", request_id=rid)
                raise _Reject(503, "replay_store_unavailable",
                              "attestation replay store unavailable; request not sent") from None
            except attest.AttestationError as e:
                rep.end("verify_source", "failed")
                rep.check("source_verified", "fail")
                _tr("input_source", attested=bool(h.get("x-pgw-input-source")), accepted=False,
                    reason=e.reason, unattested=contract.external_input.unattested)
                if contract.external_input.unattested == "PRIVATE":
                    return await _private(rid, app, purpose, model, body, ctx, contract, t0, "input_not_sanitized",
                                          e.reason, declared_ids(contract, subj), rep)
                ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000,
                            "input_not_sanitized", contract, attestation=e.reason)
                raise _Reject(422, "input_not_source_sanitized",
                              "this purpose only sends input signed by an allowlisted source-side sanitizer "
                              "(X-PGW-Input-Source/-Timestamp/-Nonce/-Signature); not sent externally",
                              attestation=e.reason) from None
            rep.end("verify_source")
            if rep.enabled:
                rep.input_source, rep.verified = input_source, True
                rep.check("source_verified", "pass")
            _tr("input_source", attested=True, accepted=True, verified=True, sanitizer=input_source)
            if contract.content_gate is not None:
                # Special-category content stays in-house (or is refused) EVEN WHEN validly signed: the signature
                # says who sanitised the text, not that its subject matter may leave. A gate failure fails closed.
                cg = contract.content_gate
                rep.begin("content_gate")
                try:
                    scores = await bounded(gate.classify, "\n".join(texts))
                except _Reject:
                    rep.end("content_gate", "failed")
                    raise
                except Exception as e:
                    rep.end("content_gate", "failed")
                    _log(logging.ERROR, event="content_gate_failed", request_id=rid, error_type=type(e).__name__)
                    ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000,
                                "content_gate_unavailable", contract)
                    raise _Reject(503, "content_gate_unavailable",
                                  "content check unavailable; request not sent") from None
                from ..ml.gate import triggered
                hit = triggered(scores, cg["labels"], cg["threshold"])
                rep.end("content_gate")
                rep.check("content_gate", "fail" if hit else "pass")
                _tr("content_gate", labels=list(cg["labels"]), triggered=hit, action=cg["action"])
                if hit and cg["action"] == "PRIVATE":
                    return await _private(rid, app, purpose, model, body, ctx, contract, t0, "content_gate",
                                          declared=declared_ids(contract, subj), rep=rep)
                if hit:
                    if rep.enabled:
                        rep.skip("compute", "protect", "egress_check")
                    ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000,
                                "content_gate", contract)
                    raise _Reject(422, "special_category_content",
                                  "content in a category this purpose keeps in-house; not sent externally")
            if contract.route == "EXT_PSEUDO":
                # Record-structured data (tables, exports, JSON/XML/LDIF records) never goes to the external model on
                # the free-text path, signed or not (SECURITY_BOUNDARY.md §11). A check failure fails closed.
                rep.begin("structure_check")
                try:
                    regions = await bounded(structure.find_in, texts)
                except _Reject:
                    rep.end("structure_check", "failed")
                    raise
                except Exception as e:
                    rep.end("structure_check", "failed")
                    _log(logging.ERROR, event="structure_check_failed", request_id=rid, error_type=type(e).__name__)
                    ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000,
                                "structure_check_unavailable", contract)
                    raise _Reject(503, "structure_check_unavailable",
                                  "structure check unavailable; request not sent") from None
                in_house = bool(regions) and contract.structured_content == "PRIVATE" and private is not None
                rep.end("structure_check", "failed" if regions and not in_house else "done")
                rep.check("structure_check", "fail" if regions else "pass")
                found = [r.summary() for _, r in regions[:10]]
                _tr("structure_check", regions=found, action=contract.structured_content)
                if in_house:
                    return await _private(rid, app, purpose, model, body, ctx, contract, t0, "structured_content",
                                          declared=declared_ids(contract, subj), rep=rep)
                if regions:
                    if rep.enabled:
                        rep.skip("compute", "protect", "egress_check")
                    ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000,
                                "structured_content", contract)
                    raise _Reject(422, "structured_data_in_free_text", STRUCTURED_MESSAGE, regions=found)
            t_c4 = time.perf_counter()
            if contract.classes[DataClass.C4] == "PRIVATE" and any(unreliable_script(t) for t in texts):
                return await _private(rid, app, purpose, model, body, ctx, contract, t0,
                                      declared=declared_ids(contract, subj), rep=rep)
            if contract.classes[DataClass.C4] == "REFUSE" and any(unreliable_script(t) for t in texts):
                if rep.enabled:   # the content check is part of protection: that is where the request stopped
                    rep.skip("compute")
                    rep.record("protect", "failed", (time.perf_counter() - t_c4) * 1000)
                ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000,
                            "content_class_C4", contract)
                raise _Reject(422, "content_requires_private_route",
                              "content in a script the gateway cannot protect reliably; not sent externally")
            rep.carry("protect", (time.perf_counter() - t_c4) * 1000)
            if contract.route not in ("LOCAL_COMPUTE", "RECORDS"):
                rep.skip("compute")
            if contract.route == "LOCAL_COMPUTE":
                _tr("route", route="LOCAL_COMPUTE", reason="contract computes inside the gateway")
                rep.begin("compute")
                try:
                    cr = await bounded(local_compute.run, contract, messages)
                except _Reject:
                    raise
                except local_compute.PayloadError as e:
                    raise _Reject(400, "invalid_contract_payload", str(e)) from None
                except Exception as e:
                    _log(logging.ERROR, event="compute_failed", request_id=rid, error_type=type(e).__name__)
                    raise _Reject(503, "compute_unavailable", "local computation failed; request not sent") from None
                rep.end("compute")
                if rep.enabled:
                    rep.compute = cr
                messages = [{"role": "user", "content": cr.prompt}]
                declared, withheld, raw_outputs = cr.declared, cr.withheld, cr.raw
                _tr("local_compute", function=contract.compute,
                    outputs=[{"name": n, "egress": (contract.outputs[n].egress if n in contract.outputs
                                                     else "INTERNAL")} for n in cr.outputs],
                    withheld_forms=len(cr.withheld), prompt_built=True)
            if plan is not None:
                _tr("route", route="RECORDS", reason="field contract: computed results and aliases only")
                rep.begin("compute")
                try:
                    rr = await bounded(_records.run, plan, messages, history_scope(app, purpose),
                                       timeout=plan.spec.timeout_s)
                except _Reject:
                    rep.end("compute", "failed")
                    raise
                except _records.disclosure.DisclosureRefused as e:
                    # small-group disclosure control (differencing): nothing is sent; the report says why (counts)
                    rep.end("compute", "failed")
                    if rep.enabled:
                        rep.records = e.report
                    ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000,
                                "disclosure_control", contract)
                    raise _Reject(429 if e.code == _records.disclosure.REFUSE_RATE else 422,
                                  "disclosure_control_refused", str(e), reason=e.code) from None
                except _records.disclosure.HistoryUnavailable as e:
                    rep.end("compute", "failed")
                    _log(logging.ERROR, event="disclosure_history_unavailable", request_id=rid,
                         error_type=str(e))
                    ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000,
                                "disclosure_history_unavailable", contract)
                    raise _Reject(503, "disclosure_store_unavailable",
                                  "disclosure-control history unavailable; request not sent") from None
                except local_compute.PayloadError as e:
                    rep.end("compute", "failed")
                    ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000,
                                "invalid_record_payload", contract)
                    raise _Reject(400, "invalid_contract_payload", str(e)) from None
                except Exception as e:
                    rep.end("compute", "failed")
                    _log(logging.ERROR, event="compute_failed", request_id=rid, error_type=type(e).__name__)
                    raise _Reject(503, "compute_unavailable", "local computation failed; request not sent") from None
                rep.end("compute")
                if rep.enabled:
                    rep.compute, rep.records = rr, rr.report
                messages = [{"role": "user", "content": rr.prompt}]
                declared, raw_outputs, record_result = rr.declared, rr.raw, rr
                _tr("records", schema=rr.report["schema"], rows=rr.report["rows"], left=rr.report["left"],
                    unknown_fields=rr.report["unknown"]["fields"])

        rep.begin("protect")
        if subj:   # after all size checks, off the event loop, under the deadline
            n = await bounded(subjects.count_occurrences, subjects.request_texts(messages), declared)
            if n > subjects.MAX_OCCURRENCES:
                raise _Reject(413, "content_too_large", "too many occurrences of declared subjects")

        # --- protect + egress (fail closed) ---
        if contract is None or contract.route != "LOCAL_COMPUTE":
            _tr("route", route=contract.route if contract else "LEGACY",
                reason="external LLM on protected text" if contract else "uncontracted legacy purpose")
        prot_impl = contract_protector if contract is not None else protector
        org = contract.org_contacts if contract is not None else ()
        consts = contract.public_constants if contract is not None else ()
        held = (*org, *consts)
        if held:   # organisational C0 contacts and public constants are held out of detection (sentinels) and
            # put back after protection. Contacts match in any case; constants exactly, as whole numbers
            # ("Rs 7,000" is not held out of "Rs 7,000,000").
            messages = json.loads(json.dumps(messages))
            for mi, m in enumerate(messages):
                if isinstance(m.get("content"), str):
                    parts = [(None, m)]
                else:
                    parts = [(pi, p) for pi, p in enumerate(m.get("content") or [])
                             if isinstance(p, dict) and isinstance(p.get("text"), str)]
                for pi, part in parts:
                    key = "content" if part is m else "text"
                    part[key], maps = _report.held_out(part[key], held, len(org), _ORG_SENTINEL, rep.enabled)
                    if maps is not None:   # transparency only: offsets back to the caller's original string
                        rep.held_maps[(mi, pi)] = maps
        try:
            prot = await (bounded(prot_impl.protect, messages, ctx) if declared is None
                          else bounded(prot_impl.protect, messages, ctx, declared))
        except _Reject as r:
            ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000, r.etype,
                        contract)
            raise
        except BlockedRequest as e:
            ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000, "policy",
                        contract)
            raise _Reject(422, "request_blocked", "request contains data that policy forbids sending",
                          types=list(e.types)) from None
        except (UnsupportedContent, ConversationIdRequired) as e:
            msg = ("conversation id required for multi-turn restore" if isinstance(e, ConversationIdRequired)
                   else "unsupported message content")
            raise _Reject(400, "invalid_request" if isinstance(e, UnsupportedContent) else "conversation_id_required",
                          msg) from None
        except UnknownPurpose:
            raise _Reject(403, "purpose_forbidden", "purpose has no policy") from None
        except Exception as e:
            _log(logging.ERROR, event="protect_failed", request_id=rid, error_type=type(e).__name__)
            ledger_line(rid, app, purpose, model, None, {}, "BLOCKED", (time.perf_counter() - t0) * 1000, "detector_error",
                        contract)
            raise _Reject(503, "detector_unavailable", "protection unavailable; request not sent") from None

        if held:
            for m in prot.messages:
                parts = [m] if isinstance(m.get("content"), str) else [
                    p for p in (m.get("content") or []) if isinstance(p, dict) and isinstance(p.get("text"), str)]
                for part in parts:
                    key = "content" if part is m else "text"
                    for i, oc in enumerate(held):
                        part[key] = part[key].replace(chr(_ORG_SENTINEL + i), oc)
        # RECORDS: the protected prompt with its computed values still behind sentinels (checked below)
        record_protected = (prot.messages[0]["content"] if record_result is not None else None)
        if raw_outputs:   # contract-RAW local-compute outputs, held out of detection, go back in now
            for m in prot.messages:
                if isinstance(m.get("content"), str):
                    m["content"] = local_compute.restore_raw(m["content"], raw_outputs)
        if subj:
            subjects.prefer_declared_spelling(prot, subj)
            if ctx.conversation_id:   # later turns re-protect these even if the app does not re-declare them
                try:
                    ledger.record(ctx, subjects.ledger_items(subj))
                except Exception as e:
                    _log(logging.ERROR, event="ledger_record_failed", request_id=rid, error_type=type(e).__name__)
                    raise _Reject(503, "ledger_unavailable", "conversation ledger unavailable; request not sent") from None
        # explicit allowlist: only parameters check_parameters() has typed ever reach the provider
        up_body = {k: body[k] for k in FORWARDED if k in body}
        up_body["messages"] = prot.messages
        if contract is not None:   # tell the model how to treat placeholders (added after detection)
            note = PLACEHOLDER_NOTE + (("\n\n" + contract.guidance) if contract.guidance else "")
            up_body["messages"] = [{"role": "system", "content": note}] + list(prot.messages)
        if rep.enabled:
            rep.prot, rep.declared = prot, (declared if subj else ())
        rep.end("protect")
        rep.begin("egress_check")
        _tr("protect", entities=[{"type": e.type.value, "action": e.action.value, "replacement": e.replacement,
                                  "value": e.value} for e in prot.entities.values()],
            counts=prot.type_counts(), ner=bool(getattr(prot_impl, "ner", None)))
        wire = upstream.wire_bytes(up_body)
        body_hash = hashlib.sha256(wire).hexdigest()
        check_id = "egress_clean"
        # the gateway's own text in the payload (placeholder note, contract guidance, model name) is not user data
        own = (PLACEHOLDER_NOTE, model, *((contract.guidance,) if contract and contract.guidance else ()))
        try:
            egress_check(blank_own(wire, own), prot.sensitive_values + withheld)
            rep.check("egress_clean", "pass")
            check_id = "declared_absent"
            subjects.check_outbound(wire, declared if subj else [], (*own, *held))
            if subj:
                rep.check("declared_absent", "pass")
            if record_result is not None:   # no input value the schema did not classify KEEP, in any form
                check_id = "records_egress"
                record_result.check_protected(record_protected)
                record_result.check_final(prot.messages[0]["content"])
                rep.check("records_egress", "pass")
        except EgressViolation:
            rep.check(check_id, "fail")
            ms = (time.perf_counter() - t0) * 1000
            ledger_line(rid, app, purpose, model, body_hash, prot.type_counts(), "VIOLATION", ms, "egress", contract)
            _log(logging.CRITICAL, event="egress_violation", request_id=rid, tenant=app.tenant, app=app.app_id)
            raise _Reject(500, "PGW_EGRESS_VIOLATION", "egress invariant violated; request not sent") from None
        except Exception as e:
            _log(logging.ERROR, event="egress_failed", request_id=rid, error_type=type(e).__name__)
            raise _Reject(503, "detector_unavailable", "protection unavailable; request not sent") from None
        rep.end("egress_check")
        if rep.enabled:
            rep.outbound = {"destination": "external", "provider": upstream.name, "model": model,
                            "messages": up_body["messages"], "gateway_note_included": contract is not None,
                            "sha256": body_hash, "bytes": len(wire)}
        ms = (time.perf_counter() - t0) * 1000
        _tr("egress", values_checked=len(prot.sensitive_values) + len(withheld),
            declared_matchers=len(declared) if subj else 0, result="passed")
        _tr("send", provider=upstream.name, model=model, bytes=len(wire), sha256=body_hash,
            outbound=wire.decode("utf-8", "replace"))
        ledger_line(rid, app, purpose, model, body_hash, prot.type_counts(), "SENT", ms, None, contract,
                    len(subj) if contract is not None and contract.subjects is not None else None, input_source)
        _log(logging.INFO, event="sent", request_id=rid, tenant=app.tenant, app=app.app_id, purpose=purpose,
             model=model, decision="SENT", latency_ms=round(ms, 2), stream=stream)

        never = policy.never_restore
        if contract is not None:   # only the classes the contract lists are restored
            never = frozenset(never | {t for t in EntityType if not contract.restorable(t)})

        # Identifier-shaped values the model may legitimately write back (N8): what this request sent it in clear,
        # and the values restore will put back itself. Anything else identifier-shaped in the answer is masked.
        sent_text = "\n".join(subjects.request_texts(prot.messages))
        known_ids = {compact_id(sent_text[d.start:d.end]) for d in idshape.detect(sent_text)}
        known_ids |= {compact_id(e.value) for e in prot.alias_map.values()
                      if isinstance(e.value, str) and e.type not in never}

        def new_restorer() -> Restorer:
            return Restorer(prot.alias_map, restore=app.restore_output, never_restore=never,
                            roster=roster, mode=policy.stream_mode(purpose),
                            allowed=frozenset(o.lower() for o in ((*contract.org_contacts, *contract.public_constants)
                                                                  if contract else ())),
                            business=contract.business_identifiers if contract else (),
                            known_ids=frozenset(known_ids))

        def record(items) -> None:
            if ctx.conversation_id:
                ledger.record(ctx, items)

        if not stream:
            return await _non_stream(rid, up_body, new_restorer, record, contract, rep)
        return await _stream(rid, up_body, new_restorer, record, contract, rep)

    def declared_ids(contract, subj) -> frozenset:
        """Compact forms of the declared subjects' restorable values (e.g. the requester's employee id): the app
        supplied them and would get them back restored on the external route, so the private gate lets them pass."""
        return frozenset(compact_id(v) for _r, t, v in subj if isinstance(v, str) and contract.restorable(t))

    async def _private(rid, app, purpose, model, body, ctx, contract, t0, reason="content_class_C4",
                       attestation=None, declared: frozenset = frozenset(), rep=_report.NULL) -> Response:
        """C4 content the gateway cannot protect reliably goes, unprotected, ONLY to the in-boundary model.
        The external upstream is never called. The answer still passes the output gate (raw C3 is masked)."""
        if rep.enabled:   # in-boundary: nothing is computed, replaced or egress-checked (nothing leaves)
            rep.route = "PRIVATE_LOCAL"
            rep.skip("compute", "protect", "egress_check")
        if body.get("stream"):
            raise _Reject(400, "unsupported_parameter", "streaming is not available on the private route")
        p_body = {k: v for k, v in body.items() if not k.startswith("pgw_") and k not in ("tools", "tool_choice")}
        p_body["model"] = settings.private_model
        _tr("route", route="PRIVATE_LOCAL", reason={
            "input_not_sanitized": "input not attested as source-sanitized: in-boundary model only",
            "content_gate": "special-category content (content gate): in-boundary model only",
            "structured_content": "record-structured data in free text: in-boundary model only"}.get(
            reason, "content the gateway cannot read reliably (C4): in-boundary model only"))
        p_wire = private.wire_bytes(p_body)
        _tr("send", provider="private (in-boundary)", model=settings.private_model, bytes=len(p_wire),
            sha256=hashlib.sha256(p_wire).hexdigest(), outbound=p_wire.decode("utf-8", "replace"),
            note="unprotected, but never leaves the boundary")
        ms = (time.perf_counter() - t0) * 1000
        ledger_line(rid, app, purpose, model, hashlib.sha256(private.wire_bytes(p_body)).hexdigest(), {},
                    "SENT_PRIVATE", ms, reason, dataclasses.replace(contract, route="PRIVATE_LOCAL"),
                    attestation=attestation)
        if rep.enabled:
            rep.outbound = {"destination": "in_house", "provider": "in_house", "model": settings.private_model,
                            "messages": p_body["messages"], "gateway_note_included": False,
                            "sha256": hashlib.sha256(p_wire).hexdigest(), "bytes": len(p_wire)}
        rep.begin("provider")
        try:
            t_up = time.perf_counter()
            data = await private.complete(p_body)
            _tr("provider", ms=round((time.perf_counter() - t_up) * 1000, 1), finish="stop",
                raw_text=((data.get("choices") or [{}])[0].get("message") or {}).get("content"))
        except Exception as e:
            _log(logging.ERROR, event="private_upstream_failed", request_id=rid, error_type=type(e).__name__)
            raise _Reject(502, "private_upstream_error", "private model unavailable; nothing was sent externally") from None
        rep.end("provider")
        if rep.enabled and isinstance(data, dict) and isinstance(data.get("model"), str):
            # the model id the in-boundary server itself reports (the stand-in says "extractive-standin")
            rep.outbound["model"] = data["model"][:128]
        # A reply from the stand-in is marked (development) or withheld (production: the endpoint changed after the
        # startup probe). The marking depends on the endpoint's own fixed identity, never on configuration.
        standin = private_kind == "standin" or (isinstance(data, dict) and is_standin_entry(data))
        if standin and prod:
            _log(logging.CRITICAL, event="private_model_standin_reply_refused", request_id=rid)
            raise _Reject(503, "private_model_unavailable", "in-boundary model is a stand-in; response withheld")
        rep.begin("restore")
        n_masked = 0
        try:
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            out = None
            if isinstance(text, str):   # C3 never returns to the app, whichever model produced it
                dets = detect_d2(text)
                masks = [(d.start, d.end, d.type.value) for d in dets if CLASS_OF.get(d.type) is DataClass.C3]
                # identifier-shaped tokens no recognizer typed (N8): a business reference format of the contract
                # or a declared subject's restorable value stays, anything else is withheld like C3
                typed = [(d.start, d.end) for d in dets]
                masks += [(d.start, d.end, d.type.value)
                          for d in idshape.exempt_business(text, idshape.detect(text), contract.business_identifiers)
                          if not any(s < d.end and d.start < e for s, e in typed)
                          and compact_id(text[d.start:d.end]) not in declared]
                out = text
                taken: list[tuple[int, int]] = []
                for s_, e_, t_ in sorted(masks, key=lambda x: (-x[0], x[0] - x[1])):
                    if any(not (e_ <= a or s_ >= b) for a, b in taken):
                        continue
                    taken.append((s_, e_))
                    out = out[:s_] + f"[{t_}]" + out[e_:]
                # Detectors cannot read C4 content reliably (that is why it is here): also mask every long digit
                # run (account / Aadhaar / card lengths, any separators), whatever script surrounds it.
                out, n_long = _LONG_DIGITS.subn("[NUMBER]", out)
                n_masked = len(taken) + n_long
            _tr("restore", restored=0, gate="C3 and long digit runs masked in the in-boundary model's answer",
                answer=out)
        except Exception as e:
            _log(logging.ERROR, event="restore_failed", request_id=rid, error_type=type(e).__name__)
            raise _Reject(503, "restore_unavailable", "response withheld") from None
        resp_body = {"id": data.get("id"), "object": "chat.completion", "created": data.get("created"),
                     "model": data.get("model"), "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": out}}]}
        if rep.enabled:
            rep.end("restore")
            rep.restore_done, rep.restored, rep.kept_withheld = True, 0, n_masked
            rep.check("c3_not_returned", "fail" if _c3_in([out]) else "pass")
            resp_body["pgw_report"] = report_of(rep, "kept_inside")
        return JSONResponse(resp_body,
                            headers={"X-PGW-Request-Id": rid, "X-PGW-Route": "PRIVATE_LOCAL",
                                     "X-PGW-Private-Model": "standin" if standin else private_kind or "unverified"})

    async def _non_stream(rid, up_body, new_restorer, record, contract=None, rep=_report.NULL) -> Response:
        rep.begin("provider")
        try:
            t_up = time.perf_counter()
            data = await upstream.complete(up_body)
            _tr("provider", ms=round((time.perf_counter() - t_up) * 1000, 1),
                finish=((data.get("choices") or [{}])[0].get("finish_reason")),
                raw_text=((data.get("choices") or [{}])[0].get("message") or {}).get("content"))
        except Exception as e:   # provider failure: nothing is returned to the app
            _log(logging.ERROR, event="upstream_failed", request_id=rid, error_type=type(e).__name__)
            # demo trace only: UpstreamError messages are content-free by design ("upstream status 401", ...)
            _tr("provider_error", cause=str(e)[:120] if isinstance(e, UpstreamError) else type(e).__name__)
            raise _Reject(502, "upstream_error", "upstream provider error") from None
        rep.end("provider")
        rep.begin("restore")
        n_restored = n_kept = 0
        try:
            restored: list = []
            events = 0
            out_choices = []
            for ch in data.get("choices") or []:
                m = ch.get("message") or {}
                msg: dict = {"role": "assistant", "content": None}   # unknown fields are dropped, not forwarded
                if isinstance(m.get("content"), str):
                    r = new_restorer()
                    gated = r.feed(m["content"]) + r.finish()
                    msg["content"] = render_markers(gated, contract)
                    restored += r.restored
                    events += len(r.events)
                    if rep.enabled:
                        n_restored += r.n_restored
                        n_kept += r.n_kept + _withheld_markers(gated, contract)
                calls = []
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    r = new_restorer()
                    args = r.feed(fn.get("arguments") or "") + r.finish()
                    restored += r.restored
                    events += len(r.events)
                    n_restored += r.n_restored
                    n_kept += r.n_kept
                    calls.append({"id": tc.get("id"), "type": "function",
                                  "function": {"name": fn.get("name"), "arguments": args}})
                if calls:
                    msg["tool_calls"] = calls
                out_choices.append({"index": ch.get("index", 0), "message": msg,
                                    "finish_reason": ch.get("finish_reason")})
            record(restored)
            _tr("restore", restored=len(restored), gate_events=events,
                answer=out_choices[0]["message"]["content"] if out_choices else None)
        except Exception as e:
            _log(logging.ERROR, event="restore_failed", request_id=rid, error_type=type(e).__name__)
            raise _Reject(503, "restore_unavailable", "response withheld") from None
        out = {"id": data.get("id"), "object": "chat.completion", "created": data.get("created"),
               "model": data.get("model"), "choices": out_choices}
        if isinstance(data.get("usage"), dict):
            out["usage"] = data["usage"]
        if rep.enabled:
            rep.end("restore")
            rep.restore_done, rep.restored, rep.kept_withheld = True, n_restored, n_kept
            texts = [c["message"]["content"] for c in out_choices if isinstance(c["message"]["content"], str)]
            texts += [tc["function"]["arguments"] for c in out_choices for tc in c["message"].get("tool_calls") or []
                      if isinstance(tc["function"]["arguments"], str)]
            rep.check("c3_not_returned", "fail" if _c3_in(texts) else "pass")
            out["pgw_report"] = report_of(rep, "sent")
        return JSONResponse(out, headers={"X-PGW-Request-Id": rid, "X-PGW-Gate-Events": str(events)})

    async def _stream(rid, up_body, new_restorer, record, contract=None, rep=_report.NULL) -> Response:
        rep.begin("provider")
        t_up = time.perf_counter()
        it = upstream.stream(up_body).__aiter__()
        try:   # prime: a failure before the first event can still be a proper HTTP error
            first = await it.__anext__()
        except StopAsyncIteration:
            first = None
        except Exception as e:
            _log(logging.ERROR, event="upstream_failed", request_id=rid, error_type=type(e).__name__, stream=True)
            raise _Reject(502, "upstream_error", "upstream provider error") from None

        def sse(obj) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        async def gen():
            states: dict[int, _ChoiceState] = {}
            meta = {"id": None, "created": None, "model": None}
            usage = None
            done = False
            restored: list = []
            events = 0
            # transparency only: time spent restoring (the rest of the stream's wall time is the provider's), what
            # was emitted (for the C3 check) and the restore counts
            t_restore, emitted, n_kept = 0.0, [], 0
            on = rep.enabled

            def chunk(idx, delta, finish=None):
                return sse({"id": meta["id"], "object": "chat.completion.chunk", "created": meta["created"],
                            "model": meta["model"], "choices": [{"index": idx, "delta": delta,
                                                                 "finish_reason": finish}]})

            async def events_iter():
                if first is not None:
                    yield first
                async for x in it:
                    yield x
            try:
                async for raw in events_iter():
                    if raw.strip() == "[DONE]":
                        done = True
                        break
                    obj = json.loads(raw)
                    if not isinstance(obj, dict) or "error" in obj:
                        raise UpstreamError("upstream stream error")
                    for k in meta:
                        meta[k] = meta[k] if meta[k] is not None else obj.get(k)
                    if isinstance(obj.get("usage"), dict):
                        usage = obj["usage"]
                    for ch in obj.get("choices") or []:
                        idx = ch.get("index", 0)
                        st = states.get(idx)
                        if st is None:
                            st = states[idx] = _ChoiceState(new_restorer())
                            yield chunk(idx, {"role": "assistant", "content": ""})
                        delta = ch.get("delta") or {}
                        if isinstance(delta.get("content"), str) and delta["content"]:
                            if on:
                                t_r = time.perf_counter()
                                out = st.restorer.feed(delta["content"])
                                t_restore += time.perf_counter() - t_r
                                emitted.append(out)
                            else:
                                out = st.restorer.feed(delta["content"])
                            if out:
                                yield chunk(idx, {"content": out})
                        for tc in delta.get("tool_calls") or []:
                            t = st.tools.setdefault(tc.get("index", 0), {"id": None, "name": None, "args": []})
                            t["id"] = tc.get("id") or t["id"]
                            fn = tc.get("function") or {}
                            t["name"] = fn.get("name") or t["name"]
                            if fn.get("arguments"):
                                t["args"].append(fn["arguments"])
                        if ch.get("finish_reason"):
                            st.finish = ch["finish_reason"]
                            done = True
                if not done:
                    raise UpstreamError("upstream stream ended early")
                if on:
                    rep.record("provider", "done", (time.perf_counter() - t_up - t_restore) * 1000)
                    t_r = time.perf_counter()
                # upstream finished cleanly: flush holdback, then whole tool calls, then finish
                for idx, st in sorted(states.items()):
                    gated = st.restorer.finish()
                    tail = render_markers(gated, contract)
                    if on:
                        emitted.append(tail)
                        n_kept += _withheld_markers(gated, contract)
                    if tail:
                        yield chunk(idx, {"content": tail})
                    events += len(st.restorer.events)
                    restored += st.restorer.restored
                    for ti, t in sorted(st.tools.items()):
                        r = new_restorer()
                        args = r.feed("".join(t["args"])) + r.finish()
                        restored += r.restored
                        events += len(r.events)
                        if on:
                            emitted.append(args)
                            rep.restored += r.n_restored
                            n_kept += r.n_kept
                        yield chunk(idx, {"tool_calls": [{"index": ti, "id": t["id"], "type": "function",
                                                          "function": {"name": t["name"], "arguments": args}}]})
                    yield chunk(idx, {}, st.finish or "stop")
                record(restored)
                if usage:
                    yield sse({"id": meta["id"], "object": "chat.completion.chunk", "created": meta["created"],
                               "model": meta["model"], "choices": [], "usage": usage})
                yield f": pgw-gate-events={events}\n\n"
                if on:
                    rep.record("restore", "done", (t_restore + time.perf_counter() - t_r) * 1000)
                    rep.restore_done = True
                    rep.restored += sum(st.restorer.n_restored for st in states.values())
                    rep.kept_withheld = n_kept + sum(st.restorer.n_kept for st in states.values())
                    rep.check("c3_not_returned", "fail" if _c3_in(["".join(emitted)]) else "pass")
                    yield sse({"pgw_report": report_of(rep, "sent")})
                yield "data: [DONE]\n\n"
            except Exception as e:
                # Held-back/unflushed text is dropped: unrestored aliases are never emitted as final.
                _log(logging.ERROR, event="stream_failed", request_id=rid, error_type=type(e).__name__, stream=True)
                try:
                    for st in states.values():
                        restored += st.restorer.restored
                    record(restored)   # what already reached the client
                except Exception as e2:
                    # Not swallowed: a later turn of this conversation cannot re-protect what was released here.
                    _log(logging.CRITICAL, event="restore_ledger_record_failed", request_id=rid,
                         error_type=type(e2).__name__, stream=True)
                if on:   # the report comes before the terminal error event, and carries no request text
                    if "provider" not in rep.stages:
                        rep.record("provider", "failed", (time.perf_counter() - t_up - t_restore) * 1000)
                    else:
                        rep.record("restore", "failed", 0.0)
                    err = {"type": "stream_interrupted", "message": "stream interrupted; response incomplete"}
                    yield sse({"pgw_report": report_of(rep, "error", err)})
                yield sse({"error": {"type": "stream_interrupted", "message": "stream interrupted; response incomplete"}})

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"X-PGW-Request-Id": rid, "Cache-Control": "no-store"})

    async def health(request: Request) -> Response:
        out = {"status": "ok", "mode": settings.mode, "coverage": coverage}
        if disclosure_history is not None:   # counts only (operator metric)
            out["disclosure_history"] = await to_thread.run_sync(disclosure_history.metrics.snapshot)
        return JSONResponse(out)

    import contextlib

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:   # release NER worker processes (each holds a model copy) when the app stops
            for comp in (getattr(contract_protector, "ner", None), ml_detector, gate, disclosure_history):
                close = getattr(comp, "close", None)
                if close is not None:
                    close()

    app_ = Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"]),
                             Route("/healthz", health, methods=["GET"])], lifespan=lifespan)
    # Plugin shadow/union counters (operator metrics, never in pgw_report): app.state.pgw_plugin_metrics.snapshot()
    app_.state.pgw_plugin_metrics = plugin_set.metrics if plugin_set is not None else None
    # Disclosure-control history counters (answered, refused_differencing, refused_capacity, store_unavailable, scopes,
    # cells): app.state.pgw_disclosure_metrics.snapshot(); also on /healthz as counts. The store itself (operator
    # actions: python -m pgw.product.disclosure history list|log|compact|recover) is app.state.pgw_disclosure_history.
    app_.state.pgw_disclosure_history = disclosure_history
    app_.state.pgw_disclosure_metrics = disclosure_history.metrics if disclosure_history is not None else None
    return app_
