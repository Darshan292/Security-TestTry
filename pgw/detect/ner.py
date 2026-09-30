"""D4: local NER for PERSON names that are not in the roster.

CPU only, no network at run time. Backed by a spaCy pipeline installed as a wheel
(en_core_web_sm/lg/trf, xx_ent_wiki_sm). spaCy gives no confidence, so every span
gets the fixed score ``SPACY_SCORE``.

Contract with the pipeline: ``detect`` returns PERSON detections with offsets into
the exact string passed in. Any internal failure (missing package or model, model
crash) raises ``NerUnavailable`` and nothing else; the caller fails closed on it.
"""
from __future__ import annotations

import re
import threading

from pgw.types import Detection, EntityType

SPACY_SCORE = 0.75
MAX_CHUNK = 100_000          # spaCy's default max_length is 1e6; stay far below for memory/latency
_PERSON_LABELS = {"PERSON", "PER"}
_DROP_PIPES = ("parser", "lemmatizer", "tagger", "attribute_ruler", "senter")

_LEAD = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "professor", "sir", "madam", "madame",
         "shri", "shree", "sri", "smt", "shrimati", "kumari", "mister", "messrs", "capt", "adv", "hon", "rev"}
_TRAIL = {"sir", "madam", "ji", "saheb", "sahib", "sahiba", "madame", "esq"}
_SURR = re.compile("[\ud800-\udfff]")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


class NerUnavailable(Exception):
    """The NER layer could not produce a trustworthy answer. Callers must fail closed."""


_models: dict[str, object] = {}
_models_lock = threading.Lock()


def _load(model: str):
    nlp = _models.get(model)
    if nlp is not None:
        return nlp
    with _models_lock:
        nlp = _models.get(model)
        if nlp is None:
            try:
                import spacy
                nlp = spacy.load(model)
                drop = [p for p in _DROP_PIPES if p in nlp.pipe_names]
                if drop:
                    nlp.select_pipes(disable=drop)
                if "ner" not in nlp.pipe_names:
                    raise ValueError(f"{model} has no ner component")
                nlp.max_length = MAX_CHUNK + 1000
            except Exception as e:  # noqa: BLE001 - everything maps to one exception type
                raise NerUnavailable(f"cannot load NER model {model!r}: {type(e).__name__}: {e}") from e
            _models[model] = nlp
    return nlp


def _trim(text: str, s: int, e: int) -> tuple[int, int]:
    """Shrink [s,e) past honorifics and non-letter edge characters."""
    while True:
        while s < e and not text[s].isalpha():
            s += 1
        while e > s and not text[e - 1].isalpha():
            e -= 1
        if s >= e:
            return s, s
        words = list(_WORD.finditer(text, s, e))
        if len(words) < 2:
            # a lone honorific ("Dr.") is not a name
            if words and words[0].group().lower() in _LEAD | _TRAIL:
                return s, s
            return s, e
        first, last = words[0], words[-1]
        if first.group().lower() in _LEAD:
            s = first.end()
            continue
        if last.group().lower() in _TRAIL:
            e = last.start()
            continue
        return s, e


def _chunks(text: str):
    """Yield (offset, chunk); split on newlines so no chunk exceeds MAX_CHUNK where possible."""
    n = len(text)
    if n <= MAX_CHUNK:
        yield 0, text
        return
    pos = 0
    while pos < n:
        end = min(pos + MAX_CHUNK, n)
        if end < n:
            nl = text.rfind("\n", pos, end)
            if nl > pos:
                end = nl + 1
            else:                      # one giant line: fall back to whitespace, then a hard cut
                ws = max(text.rfind(" ", pos, end), text.rfind("\t", pos, end))
                if ws > pos:
                    end = ws + 1
        yield pos, text[pos:end]
        pos = end


class NerDetector:
    def __init__(self, model: str, min_score: float = 0.0):
        self.model = model
        self.min_score = min_score
        self._source = f"D4:ner:{model}"

    @property
    def nlp(self):
        return _load(self.model)          # lazy, thread-safe, shared per process

    def warm(self) -> None:
        self.detect("Warm up text for Priya Nair.")

    def detect(self, text: str) -> list[Detection]:
        if not isinstance(text, str):
            raise NerUnavailable(f"expected str, got {type(text).__name__}")
        if not text.strip() or SPACY_SCORE < self.min_score:
            return []
        nlp = self.nlp
        text = _SURR.sub("\ufffd", text)   # lone surrogates crash spaCy; same length, offsets unchanged
        out: list[Detection] = []
        try:
            for off, chunk in _chunks(text):
                if not chunk.strip():
                    continue
                doc = nlp(chunk)
                for ent in doc.ents:
                    if ent.label_ not in _PERSON_LABELS:
                        continue
                    s, e = _trim(chunk, ent.start_char, ent.end_char)
                    if e > s:
                        out.append(Detection(off + s, off + e, EntityType.PERSON, SPACY_SCORE, self._source))
        except NerUnavailable:
            raise
        except BaseException as e:  # noqa: BLE001
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            raise NerUnavailable(f"NER inference failed: {type(e).__name__}: {e}") from e
        return out
