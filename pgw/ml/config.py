"""Validation of the ML entries of the signed deployment profile (`ml_detector`, `content_gate_model`).

Pure Python (no third-party imports): the profile is validated before anything is loaded.

ml_detector:
    path: models/pii-v1            # model directory (absolute, or relative to the sealed tree)
    sha256: <64 hex>               # the artefact manifest digest (python -m pgw.ml.artifact <dir>)
    backend: token                 # token (BIO/BILOU token classifier) | span (GLiNER-style span model)
    labels: {PER: PERSON, EMAIL: EMAIL, MISC: IGNORE}   # EVERY model entity label -> gateway type or IGNORE
    min_score_by_type: {DEFAULT: 0.5, PERSON: 0.4}     # per gateway type; DEFAULT covers the rest
    workers: 2                     # worker processes; 0 = in-process (tests only)
    queue_max: 8                   # requests allowed to wait for a worker; beyond: fail closed at once
    timeout_s: 5.0                 # per call (queue wait + inference); capped below protect_timeout_s
    intra_op_threads: 1            # ONNX Runtime threads per worker (workers * intra_op_threads <= cores)
    inter_op_threads: 1
    batch_size: 8                  # windows per ONNX run

content_gate_model:
    path, sha256, workers, queue_max, timeout_s, intra_op_threads, inter_op_threads, batch_size: as above
    labels: [health, disciplinary, ...]                 # EXACTLY the model's labels (labels.json)
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..types import EntityType

_HEX64 = re.compile(r"[0-9a-f]{64}")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.:/-]{0,63}")
IGNORE = "IGNORE"
BACKENDS = ("token", "span")
GATE_ACTIONS = ("PRIVATE", "REFUSE")
# Types an ML model may produce. DERIVED is computed inside the gateway, never detected.
ML_TYPES = frozenset(t.value for t in EntityType if t is not EntityType.DERIVED)

_RUNTIME_DEFAULTS = {"workers": 2, "queue_max": 8, "timeout_s": 5.0, "intra_op_threads": 1, "inter_op_threads": 1,
                     "batch_size": 8}


class MlConfigError(ValueError):
    """An ML profile entry is malformed. Startup stops."""


def _int(name: str, v, lo: int, hi: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise MlConfigError(f"{name} must be an integer in [{lo}, {hi}]")
    return v


def _float(name: str, v, lo: float, hi: float, lo_open: bool = True) -> float:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not (lo < v <= hi if lo_open else lo <= v <= hi):
        raise MlConfigError(f"{name} must be a number in {'(' if lo_open else '['}{lo}, {hi}]")
    return float(v)


@dataclass(frozen=True)
class RuntimeConfig:
    workers: int = 2
    queue_max: int = 8
    timeout_s: float = 5.0
    intra_op_threads: int = 1
    inter_op_threads: int = 1
    batch_size: int = 8


def _runtime(prefix: str, raw: dict) -> dict:
    return {
        "workers": _int(f"{prefix}.workers", raw.get("workers", _RUNTIME_DEFAULTS["workers"]), 0, 64),
        "queue_max": _int(f"{prefix}.queue_max", raw.get("queue_max", _RUNTIME_DEFAULTS["queue_max"]), 0, 1024),
        "timeout_s": _float(f"{prefix}.timeout_s", raw.get("timeout_s", _RUNTIME_DEFAULTS["timeout_s"]), 0, 600),
        "intra_op_threads": _int(f"{prefix}.intra_op_threads",
                                 raw.get("intra_op_threads", _RUNTIME_DEFAULTS["intra_op_threads"]), 1, 256),
        "inter_op_threads": _int(f"{prefix}.inter_op_threads",
                                 raw.get("inter_op_threads", _RUNTIME_DEFAULTS["inter_op_threads"]), 1, 256),
        "batch_size": _int(f"{prefix}.batch_size", raw.get("batch_size", _RUNTIME_DEFAULTS["batch_size"]), 1, 256),
    }


def _common(prefix: str, raw, keys: set[str]) -> tuple[str, str]:
    if not isinstance(raw, dict):
        raise MlConfigError(f"{prefix} must be a mapping or null")
    unknown = sorted(set(raw) - keys)
    if unknown:
        raise MlConfigError(f"{prefix}: unknown key(s) {unknown}")
    path = raw.get("path")
    if not isinstance(path, str) or not path.strip() or ".." in Path(path).parts:
        raise MlConfigError(f"{prefix}.path must be a directory path without '..'")
    sha = raw.get("sha256")
    if not isinstance(sha, str) or not _HEX64.fullmatch(sha):
        raise MlConfigError(f"{prefix}.sha256 must be the artefact manifest digest (64 lowercase hex characters)")
    return path.strip(), sha


@dataclass(frozen=True)
class DetectorConfig(RuntimeConfig):
    path: str = ""
    sha256: str = ""
    backend: str = "token"
    labels: dict = field(default_factory=dict)            # model label -> gateway type | IGNORE
    min_score_by_type: dict = field(default_factory=dict)  # gateway type | DEFAULT -> score

    def threshold(self, gw_type: str) -> float:
        return self.min_score_by_type.get(gw_type, self.min_score_by_type.get("DEFAULT", 1.0))

    def with_path(self, path: str) -> "DetectorConfig":
        return DetectorConfig(**{**asdict(self), "path": path})

    def as_mapping(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GateConfig(RuntimeConfig):
    path: str = ""
    sha256: str = ""
    labels: tuple = ()

    def with_path(self, path: str) -> "GateConfig":
        return GateConfig(**{**asdict(self), "path": path})

    def as_mapping(self) -> dict:
        d = asdict(self)
        d["labels"] = list(self.labels)
        return d


_DETECTOR_KEYS = {"path", "sha256", "backend", "labels", "min_score_by_type", *_RUNTIME_DEFAULTS}
_GATE_KEYS = {"path", "sha256", "labels", *_RUNTIME_DEFAULTS}


def parse_detector(raw) -> DetectorConfig:
    path, sha = _common("ml_detector", raw, _DETECTOR_KEYS)
    backend = raw.get("backend", "token")
    if backend not in BACKENDS:
        raise MlConfigError(f"ml_detector.backend must be one of {BACKENDS}")
    labels = raw.get("labels")
    if not isinstance(labels, dict) or not 1 <= len(labels) <= 128 or not all(
            isinstance(k, str) and _LABEL.fullmatch(k) for k in labels):
        raise MlConfigError("ml_detector.labels must map every model entity label (1-128) to a gateway type")
    bad = sorted(str(k) for k, v in labels.items() if v != IGNORE and v not in ML_TYPES)
    if bad:
        raise MlConfigError(f"ml_detector.labels: unknown gateway type for {bad} (use a type or {IGNORE})")
    if all(v == IGNORE for v in labels.values()):
        raise MlConfigError("ml_detector.labels: at least one label must map to a gateway type")
    ms = raw.get("min_score_by_type")
    if not isinstance(ms, dict) or not ms:
        raise MlConfigError("ml_detector.min_score_by_type must be a mapping {type or DEFAULT: score}")
    scores = {}
    for k, v in ms.items():
        if k != "DEFAULT" and k not in ML_TYPES:
            raise MlConfigError(f"ml_detector.min_score_by_type: unknown type {k!r}")
        scores[k] = _float(f"ml_detector.min_score_by_type.{k}", v, 0.0, 1.0, lo_open=False)
    missing = sorted({v for v in labels.values() if v != IGNORE} - set(scores))
    if missing and "DEFAULT" not in scores:
        raise MlConfigError(f"ml_detector.min_score_by_type: no score for {missing} and no DEFAULT")
    return DetectorConfig(path=path, sha256=sha, backend=backend, labels=dict(sorted(labels.items())),
                          min_score_by_type=dict(sorted(scores.items())), **_runtime("ml_detector", raw))


def parse_gate(raw) -> GateConfig:
    path, sha = _common("content_gate_model", raw, _GATE_KEYS)
    labels = raw.get("labels")
    if not isinstance(labels, list) or not 1 <= len(labels) <= 64 or not all(
            isinstance(x, str) and _LABEL.fullmatch(x) for x in labels) or len(set(labels)) != len(labels):
        raise MlConfigError("content_gate_model.labels must list the model's labels (1-64, distinct)")
    return GateConfig(path=path, sha256=sha, labels=tuple(labels), **_runtime("content_gate_model", raw))


def parse_contract_gate(raw) -> dict | None:
    """A contract's `content_gate: {labels: [...], threshold: 0..1, action: PRIVATE|REFUSE}` (None if absent)."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"labels", "threshold", "action"}:
        raise MlConfigError("content_gate: a mapping with labels, threshold and action")
    labels = raw.get("labels")
    if not isinstance(labels, list) or not 1 <= len(labels) <= 64 or not all(
            isinstance(x, str) and _LABEL.fullmatch(x) for x in labels):
        raise MlConfigError("content_gate.labels: 1-64 model labels")
    thr = _float("content_gate.threshold", raw.get("threshold"), 0.0, 1.0, lo_open=False)
    if not 0.0 < thr < 1.0:
        raise MlConfigError("content_gate.threshold must be strictly between 0 and 1")
    action = str(raw.get("action", "")).upper()
    if action not in GATE_ACTIONS:
        raise MlConfigError(f"content_gate.action must be one of {GATE_ACTIONS}")
    return {"labels": tuple(dict.fromkeys(labels)), "threshold": thr, "action": action}
