"""OnnxTokenClassifier: a token-classification model (BERT/DeBERTa/XLM-R-style NER head) on ONNX Runtime CPU.

Pipeline per text: tokenizer.json (no truncation) -> token windows of `max_length` (special tokens included) that
overlap by `stride` tokens -> batched ONNX runs -> softmax -> each token takes the prediction of the window in which it
is farthest from an edge (most context) -> word aggregation ("first": a word takes its first sub-token's label) ->
BIO/BILOU/BIOES decoding (optionally constrained Viterbi, labels.json `viterbi`) -> character spans via the tokenizer's offsets (code-point offsets into the exact input string).

Any failure raises MlUnavailable (the caller fails closed); a model that does not fit its labels.json raises
ArtifactError at construction (startup stops).
"""
from __future__ import annotations

from .artifact import Artifact, ArtifactError
from .base import MlUnavailable, Span, clean_text, trim_span
from .decode import decode, viterbi_labels
from .onnx_util import input_spec, load_tokenizer, make_session, softmax, special_ids, windows

_INPUTS = {"input_ids": "int", "attention_mask": "int", "token_type_ids": "int"}


class OnnxTokenClassifier:
    def __init__(self, art: Artifact, intra_op_threads: int = 1, inter_op_threads: int = 1, batch_size: int = 8):
        if art.task != "token-classification":
            raise ArtifactError("OnnxTokenClassifier needs a token-classification artefact")
        doc = art.labels
        self.id2label: list[str] = list(doc["id2label"])
        self.max_length = int(doc.get("max_length", 512))
        self.stride = int(doc.get("stride", min(128, self.max_length // 4)))
        self.aggregation = doc.get("aggregation", "first")
        self.viterbi = (doc.get("viterbi") or {}).get("biases")     # BIOES constrained decoding (labels.json)
        self.batch_size = max(1, int(batch_size))
        self.session = make_session(art.files["model.onnx"], intra_op_threads, inter_op_threads)
        self.tok = load_tokenizer(art)
        self.cls_id, self.sep_id, self.pad_id = special_ids(self.tok, doc)
        self.n_special = (self.cls_id is not None) + (self.sep_id is not None)
        if self.max_length - self.n_special < 2:
            raise ArtifactError("labels.json: max_length too small for the special tokens")
        self.inputs = input_spec(self.session, _INPUTS)
        if "input_ids" not in self.inputs:
            raise ArtifactError("model.onnx has no input_ids input")
        out = self.session.get_outputs()[0]
        last = out.shape[-1] if out.shape else None
        if isinstance(last, int) and last != len(self.id2label):
            raise ArtifactError(f"model.onnx emits {last} labels, labels.json lists {len(self.id2label)}")
        self.output_name = out.name

    # -- API ---------------------------------------------------------------------------------------------------
    def warm(self) -> None:
        self.detect("Warm up text for Priya Nair, priya.nair@example.com.")

    def detect(self, text: str) -> list[Span]:
        return self.detect_many([text])[0]

    def detect_many(self, texts: list[str]) -> list[list[Span]]:
        try:
            return self._detect_many(texts)
        except (MlUnavailable, ArtifactError):
            raise
        except Exception as e:  # noqa: BLE001 - any inference failure maps to one fail-closed exception
            raise MlUnavailable(f"token classifier failed: {type(e).__name__}") from None

    # -- internals ---------------------------------------------------------------------------------------------
    def _detect_many(self, texts: list[str]) -> list[list[Span]]:
        import numpy as np
        cleaned = [clean_text(t) for t in texts]
        uniq = list(dict.fromkeys(cleaned))
        encs = self.tok.encode_batch(uniq, add_special_tokens=False) if uniq else []
        size = self.max_length - self.n_special
        jobs: list[tuple[int, int, int]] = []           # (text index, window start, window end)
        for ti, enc in enumerate(encs):
            if len(enc.ids):
                jobs += [(ti, a, b) for a, b in windows(len(enc.ids), size, self.stride)]
        n_lab = len(self.id2label)
        best_p = [np.zeros(len(e.ids), dtype=np.float32) for e in encs]
        best_l = [np.zeros(len(e.ids), dtype=np.int64) for e in encs]
        best_d = [np.full(len(e.ids), -1, dtype=np.int64) for e in encs]
        for k in range(0, len(jobs), self.batch_size):
            chunk = jobs[k:k + self.batch_size]
            seqs = []
            for ti, a, b in chunk:
                ids = list(encs[ti].ids[a:b])
                seqs.append(([self.cls_id] if self.cls_id is not None else []) + ids
                            + ([self.sep_id] if self.sep_id is not None else []))
            width = max(len(s) for s in seqs)
            ids = np.full((len(seqs), width), self.pad_id, dtype=np.int64)
            mask = np.zeros((len(seqs), width), dtype=np.int64)
            for r, s in enumerate(seqs):
                ids[r, :len(s)] = s
                mask[r, :len(s)] = 1
            feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
            feed = {n: feed[n].astype(dt) for n, dt in self.inputs.items()}
            logits = self.session.run([self.output_name], feed)[0]
            if logits.ndim != 3 or logits.shape[0] != len(seqs) or logits.shape[2] != n_lab:
                raise MlUnavailable("token classifier output has an unexpected shape")
            probs = softmax(logits.astype(np.float32))
            off = 1 if self.cls_id is not None else 0
            for r, (ti, a, b) in enumerate(chunk):
                p = probs[r, off:off + (b - a)]
                j = np.arange(b - a)
                if self.viterbi:
                    lab = np.asarray(viterbi_labels(np.log(np.maximum(p, 1e-30)), self.id2label, self.viterbi,
                                                    first=a == 0, last=b >= len(encs[ti].ids)), dtype=np.int64)
                    pr = p[j, lab]
                else:
                    lab = p.argmax(axis=-1)
                    pr = p.max(axis=-1)
                dist = np.minimum(j if a > 0 else j + 10 ** 9, (b - a - 1 - j) if b < len(encs[ti].ids) else 10 ** 9)
                sel = dist > best_d[ti][a:b]
                best_d[ti][a:b][sel] = dist[sel]
                best_l[ti][a:b][sel] = lab[sel]
                best_p[ti][a:b][sel] = pr[sel]
        by_text = {t: self._spans(t, encs[i], best_l[i], best_p[i]) for i, t in enumerate(uniq)}
        return [list(by_text[t]) for t in cleaned]

    def _spans(self, text: str, enc, labels, probs) -> list[Span]:
        n = len(enc.ids)
        if n == 0:
            return []
        offsets = enc.offsets
        word_ids = enc.word_ids
        units: list[tuple[str, float, int, int]] = []      # (tag, prob, char start, char end)
        if self.aggregation == "first" and all(w is not None for w in word_ids):
            i = 0
            while i < n:
                j = i
                while j + 1 < n and word_ids[j + 1] == word_ids[i]:
                    j += 1
                units.append((self.id2label[int(labels[i])], float(probs[i]), offsets[i][0], offsets[j][1]))
                i = j + 1
        else:
            units = [(self.id2label[int(labels[i])], float(probs[i]), offsets[i][0], offsets[i][1]) for i in range(n)]
        out = []
        for a, b, t in decode([u[0] for u in units]):
            s, e = trim_span(text, units[a][2], units[b][3])
            if e > s:
                score = sum(u[1] for u in units[a:b + 1]) / (b - a + 1)
                out.append(Span(s, e, t, round(score, 6)))
        return out
