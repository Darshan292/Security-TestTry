"""Special-category content gate: ``ContentGate.classify(text) -> {label: score}``.

A sequence classifier (e.g. health, disciplinary, union membership, religion, sexual orientation, ...: the label set is
the model's, documented in its MODEL_CARD.md) decides whether a request must stay in-house. A contract opts in with

    content_gate: {labels: [health, disciplinary], threshold: 0.5, action: PRIVATE | REFUSE}

and the gateway then routes the request to the in-boundary model (PRIVATE) or refuses it (REFUSE) when any listed
label scores at or above the threshold, EVEN WHEN the input carries a valid source signature: a signature says who
sanitised the text, not that its subject matter may leave. A gate that cannot answer fails the request closed.

``OnnxSequenceClassifier``: tokenizer.json, token windows of `max_length` overlapping by `stride`, one ONNX run per
batch of windows, sigmoid (multi_label) or softmax (single_label); a label's score for the text is its MAXIMUM over
the windows, so one sensitive passage anywhere in a long text is enough.
"""
from __future__ import annotations

from .artifact import Artifact, ArtifactError
from .base import ContentGate, MlUnavailable, clean_text
from .onnx_util import input_spec, load_tokenizer, make_session, sigmoid, softmax, special_ids, windows

_INPUTS = {"input_ids": "int", "attention_mask": "int", "token_type_ids": "int"}

__all__ = ["ContentGate", "OnnxSequenceClassifier", "triggered"]


def triggered(scores: dict[str, float], labels, threshold: float) -> list[str]:
    """The contract's labels whose score reaches the threshold. A label the scores lack counts as triggered (the
    model did not answer for it: fail closed)."""
    return [lab for lab in labels if scores.get(lab, 1.0) >= threshold]


class OnnxSequenceClassifier:
    def __init__(self, art: Artifact, intra_op_threads: int = 1, inter_op_threads: int = 1, batch_size: int = 8):
        if art.task != "sequence-classification":
            raise ArtifactError("OnnxSequenceClassifier needs a sequence-classification artefact")
        doc = art.labels
        self.labels: list[str] = list(doc["labels"])
        self.multi = doc.get("problem_type", "multi_label") == "multi_label"
        self.max_length = int(doc.get("max_length", 512))
        self.stride = int(doc.get("stride", min(128, self.max_length // 4)))
        self.batch_size = max(1, int(batch_size))
        self.session = make_session(art.files["model.onnx"], intra_op_threads, inter_op_threads)
        self.tok = load_tokenizer(art)
        self.cls_id, self.sep_id, self.pad_id = special_ids(self.tok, doc)
        self.n_special = (self.cls_id is not None) + (self.sep_id is not None)
        self.inputs = input_spec(self.session, _INPUTS)
        if "input_ids" not in self.inputs:
            raise ArtifactError("model.onnx has no input_ids input")
        out = self.session.get_outputs()[0]
        last = out.shape[-1] if out.shape else None
        if isinstance(last, int) and last != len(self.labels):
            raise ArtifactError(f"model.onnx emits {last} classes, labels.json lists {len(self.labels)}")
        self.output_name = out.name

    def warm(self) -> None:
        self.classify("Warm up text about a leave request.")

    def classify(self, text: str) -> dict[str, float]:
        return self.classify_many([text])[0]

    def classify_many(self, texts: list[str]) -> list[dict[str, float]]:
        try:
            return self._classify_many(texts)
        except (MlUnavailable, ArtifactError):
            raise
        except Exception as e:  # noqa: BLE001
            raise MlUnavailable(f"content gate failed: {type(e).__name__}") from None

    def _classify_many(self, texts: list[str]) -> list[dict[str, float]]:
        import numpy as np
        cleaned = [clean_text(t) for t in texts]
        uniq = list(dict.fromkeys(cleaned))
        encs = self.tok.encode_batch(uniq, add_special_tokens=False) if uniq else []
        size = self.max_length - self.n_special
        jobs = [(ti, a, b) for ti, e in enumerate(encs) if len(e.ids) for a, b in windows(len(e.ids), size, self.stride)]
        best = np.zeros((len(uniq), len(self.labels)), dtype=np.float32)
        for k in range(0, len(jobs), self.batch_size):
            chunk = jobs[k:k + self.batch_size]
            seqs = [([self.cls_id] if self.cls_id is not None else []) + list(encs[ti].ids[a:b])
                    + ([self.sep_id] if self.sep_id is not None else []) for ti, a, b in chunk]
            width = max(len(s) for s in seqs)
            ids = np.full((len(seqs), width), self.pad_id, dtype=np.int64)
            mask = np.zeros((len(seqs), width), dtype=np.int64)
            for r, s in enumerate(seqs):
                ids[r, :len(s)] = s
                mask[r, :len(s)] = 1
            feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
            feed = {n: feed[n].astype(dt) for n, dt in self.inputs.items()}
            logits = self.session.run([self.output_name], feed)[0]
            if logits.ndim != 2 or logits.shape != (len(seqs), len(self.labels)):
                raise MlUnavailable("content gate output has an unexpected shape")
            p = sigmoid(logits.astype(np.float32)) if self.multi else softmax(logits.astype(np.float32))
            for r, (ti, _, _) in enumerate(chunk):
                best[ti] = np.maximum(best[ti], p[r])
        res = {t: {lab: round(float(best[i, j]), 6) for j, lab in enumerate(self.labels)} for i, t in enumerate(uniq)}
        return [dict(res[t]) for t in cleaned]
