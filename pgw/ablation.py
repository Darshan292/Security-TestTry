"""Measurement-only ablation switches. Every component defaults to ON; switches may only SKIP a component.

D0 multi-view normalisation (base view always stays) | D1 field-context (incl. JSON _field_values)
D2 pattern recognisers | CUES person cues | D3 roster (not consulted) | D4 NER (not consulted) | D5 propagation.
Switches are read at call time through get(); use `use(cfg)` to scope a change.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class AblationConfig:
    D0: bool = True
    D1: bool = True
    D2: bool = True
    CUES: bool = True
    D3: bool = True
    D4: bool = True
    D5: bool = True

    def without(self, *names: str) -> "AblationConfig":
        return replace(self, **{n: False for n in names})


DEFAULT = AblationConfig()
# Ablation is evaluation-only, but PD13Protector uses scoped overrides in the live request path.
# A module-global value lets concurrent requests observe each other's temporary detector settings.
# ContextVar isolates each thread/task and its nested scopes; reset(token) restores the exact prior context.
_current: ContextVar[AblationConfig] = ContextVar("pgw_ablation_config", default=DEFAULT)


def get() -> AblationConfig:
    return _current.get()


def set_config(cfg: AblationConfig) -> None:
    _current.set(cfg)


@contextmanager
def use(cfg: AblationConfig):
    token = _current.set(cfg)
    try:
        yield cfg
    finally:
        _current.reset(token)
