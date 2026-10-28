"""OnnxSpanModel: a GLiNER-style span classifier on ONNX Runtime CPU, without torch.

The model input follows the GLiNER ONNX export (gliner's `convert_to_onnx`): the prompt
``<<ENT>> label1 <<ENT>> label2 ... <<SEP>> word1 word2 ...`` is tokenised as pre-split words, and the graph takes

    input_ids [B, T], attention_mask [B, T]       token ids of [cls] prompt words [sep]
    words_mask [B, T]                             1-based index of the TEXT word a token starts, else 0
    text_lengths [B, 1]                           number of text words
    span_idx [B, W*K, 2], span_mask [B, W*K]      every span (i, i+k) for k < max_width (masked beyond the text)

and returns ``logits [B, W, K, C]`` (C = number of prompt labels). Scores are sigmoid(logits); spans above the
labels.json `threshold` are decoded greedily into non-overlapping spans (flat NER), highest score first.

Long texts are split into word windows of `max_words` that overlap by a quarter; a window whose prompt plus words
exceeds `max_length` tokens is split again. Candidates from all windows are decoded together.

Scope: the word splitter is GLiNER's default regex (`\\w+(?:[-_]\\w+)*|\\S`). Models exported with another splitter
or with a different input signature are refused at load (ArtifactError). This backend is tested only with a tiny
synthetic graph of the same signature (tests/ml_fixtures.py); no real GLiNER export has been run through it here,
so validate a real export with eval/ml before relying on it (docs/ML_ARCHITECTURE.md).
"""
from __future__ import annotations

import re

from .artifact import Artifact, ArtifactError
from .base import MlUnavailable, Span, clean_text, trim_span
from .onnx_util import input_spec, load_tokenizer, make_session, sigmoid, special_ids

_WORD = re.compile(r"\w+(?:[-_]\w+)*|\S")
_INPUTS = {"input_ids": "int", "attention_mask": "int", "words_mask": "int", "text_lengths": "int",
           "span_idx": "int", "span_mask": "any"}


class OnnxSpanModel:
    def __init__(self, art: Artifact, intra_op_threads: int = 1, inter_op_threads: int = 1, batch_size: int = 8):
        if art.task != "span-classification":
            raise ArtifactError("OnnxSpanModel needs a span-classification artefact")
        doc = art.labels
        self.labels: list[str] = list(doc["labels"])
        self.max_width = int(doc.get("max_width", 12))
        self.max_words = int(doc.get("max_words", 256))
        self.max_length = int(doc.get("max_length", 512))
        self.threshold = float(doc.get("threshold", 0.5))
        self.ent_token = doc.get("ent_token", "<<ENT>>")
        self.sep_token = doc.get("sep_token", "<<SEP>>")
        self.batch_size = max(1, int(batch_size))
        self.session = make_session(art.files["model.onnx"], intra_op_threads, inter_op_threads)
        self.tok = load_tokenizer(art)
        self.cls_id, self.sep_id, self.pad_id = special_ids(self.tok, doc)
        self.inputs = input_spec(self.session, _INPUTS)
        missing = sorted(set(_INPUTS) - set(self.inputs))
        if missing:
            raise ArtifactError(f"model.onnx lacks GLiNER inputs {missing}")
        self.output_name = self.session.get_outputs()[0].name
        self.prompt: list[str] = []
        for lab in self.labels:
            self.prompt += [self.ent_token, lab]
        self.prompt.append(self.sep_token)

    def warm(self) -> None:
        self.detect("Warm up text for Priya Nair, priya.nair@example.com.")

    def detect(self, text: str) -> list[Span]:
        return self.detect_many([text])[0]

    def detect_many(self, texts: list[str]) -> list[list[Span]]:
        try:
            cleaned = [clean_text(t) for t in texts]
            uniq = list(dict.fromkeys(cleaned))
            res = {t: self._detect(t) for t in uniq}
            return [list(res[t]) for t in cleaned]
        except (MlUnavailable, ArtifactError):
            raise
        except Exception as e:  # noqa: BLE001
            raise MlUnavailable(f"span model failed: {type(e).__name__}") from None

    # -- internals ---------------------------------------------------------------------------------------------
    def _encode(self, words: list[str]):
        enc = self.tok.encode(self.prompt + words, is_pretokenized=True, add_special_tokens=False)
        ids = ([self.cls_id] if self.cls_id is not None else []) + list(enc.ids) \
            + ([self.sep_id] if self.sep_id is not None else [])
        off = 1 if self.cls_id is not None else 0
        wmask = [0] * len(ids)
        seen = set()
        for k, w in enumerate(enc.word_ids):
            if w is not None and w >= len(self.prompt) and w not in seen:
                seen.add(w)
                wmask[k + off] = w - len(self.prompt) + 1
        return ids, wmask

    def _windows(self, words: list[str], a: int, b: int, out: list) -> None:
        ids, wmask = self._encode(words[a:b])
        if len(ids) > self.max_length:
            if b - a <= 1:
                raise MlUnavailable("span model: one word exceeds max_length")
            mid = (a + b) // 2
            self._windows(words, a, mid, out)
            self._windows(words, mid, b, out)
            return
        out.append((a, b, ids, wmask))

    def _detect(self, text: str) -> list[Span]:
        import numpy as np
        words = [(m.start(), m.end()) for m in _WORD.finditer(text)]
        if not words:
            return []
        toks = [text[s:e] for s, e in words]
        jobs: list = []
        step = max(1, self.max_words - self.max_words // 4)
        a = 0
        while True:
            b = min(a + self.max_words, len(words))
            self._windows(toks, a, b, jobs)
            if b >= len(words):
                break
            a += step
        cands: list[tuple[float, int, int, int]] = []        # (score, first word, last word, label)
        K = self.max_width
        for k0 in range(0, len(jobs), self.batch_size):
            chunk = jobs[k0:k0 + self.batch_size]
            T = max(len(j[2]) for j in chunk)
            W = max(j[1] - j[0] for j in chunk)
            B = len(chunk)
            ids = np.full((B, T), self.pad_id, dtype=np.int64)
            att = np.zeros((B, T), dtype=np.int64)
            wm = np.zeros((B, T), dtype=np.int64)
            tl = np.zeros((B, 1), dtype=np.int64)
            sidx = np.zeros((B, W * K, 2), dtype=np.int64)
            smask = np.zeros((B, W * K), dtype=bool)
            for r, (a, b, tid, wmask) in enumerate(chunk):
                n = b - a
                ids[r, :len(tid)] = tid
                att[r, :len(tid)] = 1
                wm[r, :len(wmask)] = wmask
                tl[r, 0] = n
                for i in range(W):
                    for k in range(K):
                        if i + k < n:
                            sidx[r, i * K + k] = (i, i + k)
                            smask[r, i * K + k] = True
            feed = {"input_ids": ids, "attention_mask": att, "words_mask": wm, "text_lengths": tl,
                    "span_idx": sidx, "span_mask": smask}
            feed = {n: feed[n].astype(dt) for n, dt in self.inputs.items()}
            logits = self.session.run([self.output_name], feed)[0]
            if logits.ndim != 4 or logits.shape[0] != B or logits.shape[2] != K or logits.shape[3] != len(self.labels):
                raise MlUnavailable("span model output has an unexpected shape")
            probs = sigmoid(logits.astype(np.float32))
            for r, (a, b, _, _) in enumerate(chunk):
                n = b - a
                hit = np.argwhere(probs[r, :n] > self.threshold)
                for i, k, c in hit:
                    if i + k < n:
                        cands.append((float(probs[r, i, k, c]), a + int(i), a + int(i + k), int(c)))
        taken: list[tuple[int, int]] = []
        out: list[Span] = []
        for sc, i, j, c in sorted(cands, key=lambda x: (-x[0], x[1], x[2])):
            if any(not (j < ta or i > tb) for ta, tb in taken):
                continue
            taken.append((i, j))
            s, e = trim_span(text, words[i][0], words[j][1])
            if e > s:
                out.append(Span(s, e, self.labels[c], round(sc, 6)))
        return sorted(out, key=lambda s: s.start)
