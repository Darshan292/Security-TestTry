"""JSONL record format and the document builder.

Token-classifier record (one JSON object per line), the same span shape the repo's evaluation corpora use
(`start`/`end` are Python str indices, `end` exclusive, `type` is a gateway entity type):

    {"id": "t000012-0", "genre": "payslip", "lang": "en", "text": "...",
     "spans": [{"start": 10, "end": 22, "type": "PAN"}],
     "source": "synthetic", "generator": "llm:Qwen/Qwen2.5-7B-Instruct+slots",
     "template_id": "t000012"}

`source` is always "synthetic". `generator` names how the text was produced; spans ALWAYS come from the slot filler or
a programmatic builder, never from an LLM. `template_id` (absent for programmatic records, which carry a
`program_id`) is the unit of the train/dev split.

Content-gate record:
    {"id": "...", "genre": "...", "lang": "en", "text": "...", "labels": ["HEALTH"], "source": "synthetic",
     "generator": "..."}
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Iterable, Iterator

from .bio import validate_spans
from .labels import ENTITY_TYPES, GATE_LABELS

SOURCE = "synthetic"


def _open(path, mode: str):
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, mode + "t", encoding="utf-8")
    return open(p, mode, encoding="utf-8")


def write_jsonl(path, records: Iterable[dict], append: bool = False) -> int:
    n = 0
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _open(path, "a" if append else "w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def read_jsonl(path) -> Iterator[dict]:
    with _open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def make_record(rid: str, genre: str, lang: str, text: str, spans: list[dict], generator: str, **extra) -> dict:
    rec = {"id": rid, "genre": genre, "lang": lang, "text": text,
           "spans": sorted(({"start": s["start"], "end": s["end"], "type": s["type"]} for s in spans),
                           key=lambda s: (s["start"], s["end"])),
           "source": SOURCE, "generator": generator}
    rec.update(extra)
    validate_record(rec)
    return rec


def validate_record(rec: dict) -> None:
    for k in ("id", "genre", "lang", "text", "spans", "source", "generator"):
        if k not in rec:
            raise ValueError(f"record missing {k!r}")
    if rec["source"] != SOURCE:
        raise ValueError("source must be 'synthetic'")
    for s in rec["spans"]:
        if s["type"] not in ENTITY_TYPES:
            raise ValueError(f"unknown entity type {s['type']!r}")
        if not rec["text"][s["start"]:s["end"]].strip():
            raise ValueError("empty or blank span")
    validate_spans(rec["text"], rec["spans"])


def make_gate_record(rid: str, genre: str, lang: str, text: str, labels: list[str], generator: str, **extra) -> dict:
    labels = sorted(set(labels))
    bad = [l for l in labels if l not in GATE_LABELS]
    if bad:
        raise ValueError(f"unknown gate labels {bad}")
    if "NONE" in labels and len(labels) > 1:
        raise ValueError("NONE is exclusive")
    if not labels:
        labels = ["NONE"]
    rec = {"id": rid, "genre": genre, "lang": lang, "text": text, "labels": labels, "source": SOURCE, "generator": generator}
    rec.update(extra)
    return rec


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class DocBuilder:
    """Accumulates text and exact character spans. `lit` adds unlabelled text; `val` adds a labelled value
    (type=None adds an unlabelled value, i.e. a hard negative)."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._len = 0
        self.spans: list[dict] = []

    def lit(self, s: str) -> "DocBuilder":
        self._parts.append(s)
        self._len += len(s)
        return self

    def val(self, s: str, typ: str | None) -> "DocBuilder":
        if typ is not None:
            self.spans.append({"start": self._len, "end": self._len + len(s), "type": typ})
        return self.lit(s)

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def __len__(self) -> int:
        return self._len
