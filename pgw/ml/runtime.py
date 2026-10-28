"""Building the ML stages the gateway uses, in-process or behind a worker pool.

- ``MlEntityStage``: any SpanDetector -> gateway ``Detection``s (model labels mapped by the signed profile, per-type
  minimum scores). Same interface as the spaCy NER detector (``detect_many``), so the pipeline treats both alike.
- ``GateRunner``: any ContentGate, checked against the pinned label set.
- ``open_detector`` / ``open_gate``: verify the artefact against its pin (startup stops on a mismatch), then start a
  ``DetectorPool`` / ``GatePool`` (workers > 0) or build in-process (workers = 0; tests). Each worker verifies the
  artefact again from the bytes it loads.
"""
from __future__ import annotations

from ..types import Detection, EntityType
from . import artifact
from .artifact import ArtifactError
from .base import MlUnavailable
from .config import IGNORE, DetectorConfig, GateConfig, parse_detector, parse_gate
from .pool import DetectorPool, GatePool

SOURCE_PREFIX = "D4:ml"


class MlEntityStage:
    def __init__(self, backend, cfg: DetectorConfig):
        self.backend, self.cfg = backend, cfg
        self.source = f"{SOURCE_PREFIX}:{cfg.backend}"

    def warm(self) -> None:
        self.backend.warm()

    def detect(self, text: str) -> list[Detection]:
        return self.detect_many([text])[0]

    def detect_many(self, texts: list[str]) -> list[list[Detection]]:
        for t in texts:
            if not isinstance(t, str):
                raise MlUnavailable(f"expected str, got {type(t).__name__}")
        spans = self.backend.detect_many(list(texts))
        if not isinstance(spans, list) or len(spans) != len(texts):
            raise MlUnavailable("detector returned the wrong number of results")
        out = []
        for text, ss in zip(texts, spans, strict=True):
            dets = []
            for s in ss:
                gw = self.cfg.labels.get(s.type)
                if gw is None:                 # a label the signed profile does not know: never guess
                    raise MlUnavailable("detector emitted a label that is not pinned in the profile")
                if not (isinstance(s.start, int) and isinstance(s.end, int) and 0 <= s.start < s.end <= len(text)):
                    raise MlUnavailable("detector emitted a span outside the text")
                if gw == IGNORE or not s.score >= self.cfg.threshold(gw):
                    continue
                dets.append(Detection(s.start, s.end, EntityType(gw), float(s.score), self.source))
            out.append(dets)
        return out


class GateRunner:
    def __init__(self, backend, cfg: GateConfig):
        self.backend, self.cfg = backend, cfg

    def warm(self) -> None:
        self.backend.warm()

    def classify(self, text: str) -> dict[str, float]:
        return self.classify_many([text])[0]

    def classify_many(self, texts: list[str]) -> list[dict[str, float]]:
        res = self.backend.classify_many(list(texts))
        if not isinstance(res, list) or len(res) != len(texts):
            raise MlUnavailable("content gate returned the wrong number of results")
        for r in res:
            if not isinstance(r, dict) or set(r) != set(self.cfg.labels) or not all(
                    isinstance(v, float) and 0.0 <= v <= 1.0 for v in r.values()):
                raise MlUnavailable("content gate returned scores for an unexpected label set")
        return res


def verify_detector(cfg: DetectorConfig) -> artifact.Artifact:
    art = artifact.load(cfg.path, cfg.sha256)
    if art.task not in ("token-classification", "span-classification"):
        raise ArtifactError(f"ml_detector: a {art.task} model is not a span detector")
    if (cfg.backend == "token") != (art.task == "token-classification"):
        raise ArtifactError(f"ml_detector.backend {cfg.backend!r} does not match the artefact task {art.task!r}")
    model_labels = set(artifact.entity_labels(art.labels))
    if model_labels != set(cfg.labels):
        raise ArtifactError("ml_detector.labels must map exactly the model's entity labels "
                            f"(model: {sorted(model_labels)}, profile: {sorted(cfg.labels)})")
    return art


def verify_gate(cfg: GateConfig) -> artifact.Artifact:
    art = artifact.load(cfg.path, cfg.sha256, task="sequence-classification")
    if set(art.labels["labels"]) != set(cfg.labels):
        raise ArtifactError("content_gate_model.labels must list exactly the model's labels "
                            f"(model: {sorted(art.labels['labels'])}, profile: {sorted(cfg.labels)})")
    return art


def build_detector(mapping: dict) -> MlEntityStage:
    """Factory (also run inside each worker process): verify, load, wrap."""
    cfg = parse_detector(mapping)
    art = verify_detector(cfg)
    if cfg.backend == "token":
        from .token_classifier import OnnxTokenClassifier
        backend = OnnxTokenClassifier(art, cfg.intra_op_threads, cfg.inter_op_threads, cfg.batch_size)
    else:
        from .span_model import OnnxSpanModel
        backend = OnnxSpanModel(art, cfg.intra_op_threads, cfg.inter_op_threads, cfg.batch_size)
    return MlEntityStage(backend, cfg)


def build_gate(mapping: dict) -> GateRunner:
    cfg = parse_gate(mapping)
    art = verify_gate(cfg)
    from .gate import OnnxSequenceClassifier
    return GateRunner(OnnxSequenceClassifier(art, cfg.intra_op_threads, cfg.inter_op_threads, cfg.batch_size), cfg)


def open_detector(cfg: DetectorConfig, timeout_cap_s: float | None = None):
    verify_detector(cfg)                       # a mismatch stops startup with a clear ArtifactError
    timeout = min(cfg.timeout_s, timeout_cap_s) if timeout_cap_s else cfg.timeout_s
    if cfg.workers > 0:
        return DetectorPool("pgw.ml.runtime:build_detector", (cfg.as_mapping(),), cfg.workers, cfg.queue_max,
                            timeout, name="ml_detector")
    det = build_detector(cfg.as_mapping())
    det.warm()
    return det


def open_gate(cfg: GateConfig, timeout_cap_s: float | None = None):
    verify_gate(cfg)
    timeout = min(cfg.timeout_s, timeout_cap_s) if timeout_cap_s else cfg.timeout_s
    if cfg.workers > 0:
        return GatePool("pgw.ml.runtime:build_gate", (cfg.as_mapping(),), cfg.workers, cfg.queue_max, timeout,
                        name="content_gate")
    gate = build_gate(cfg.as_mapping())
    gate.warm()
    return gate
