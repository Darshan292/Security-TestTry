"""Model-agnostic ML detection layer (docs/ML_ARCHITECTURE.md).

Interfaces (any future model drops in behind them):

- ``SpanDetector.detect(text) -> list[Span]``: character spans with a model label and a score (pgw.ml.base).
  Backends: ``OnnxTokenClassifier`` (BIO/BILOU token classification, pgw.ml.token_classifier) and
  ``OnnxSpanModel`` (GLiNER-style span classification, pgw.ml.span_model).
- ``ContentGate.classify(text) -> {label: score}``: special-category content gate (pgw.ml.gate).

Every model is a directory artefact (model.onnx, tokenizer.json, labels.json, MODEL_CARD.md) whose sha256 manifest is
pinned in the signed deployment profile (pgw.ml.artifact). Inference runs in bounded worker processes that fail
closed (pgw.ml.pool). Any failure raises ``MlUnavailable``; the gateway turns it into "nothing sent".

Third-party imports (onnxruntime, tokenizers, numpy) are lazy: the gateway without an ML model configured never
imports them (optional extra, requirements/ml.txt).
"""
from .base import MlUnavailable, Span, SpanDetector

__all__ = ["MlUnavailable", "Span", "SpanDetector"]
