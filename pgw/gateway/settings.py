"""Gateway configuration. Everything comes from env in production; tests build Settings directly."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


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
        )
