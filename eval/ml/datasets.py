"""Dataset adapters for the backend-agnostic span evaluator (docs/ML_EVALUATION.md).

Every adapter yields ``Example(id, text, spans, genre, benign)``: gold spans are (start, end, TYPE) with code-point
offsets into `text`; `benign` are (start, end) spans of strings that must NOT be masked (over-masking).

Formats:
  pgw    our corpus JSONL (eval/data*, eval/data3): {"id", "text", "spans": [{"start","end","type"}], ...}; genre from
         "genre", else "family", else "slice", else the template_id prefix.
  bio    token/BIO JSONL exported from a Hugging Face dataset:
         {"id", "tokens": [...], "ner_tags": [...] (strings, or ints with "label_names" on the row or via
         --label-names), "genre"?, "spaces"?: [bool per token: a space follows]}. Text = tokens joined by single
         spaces (or by `spaces`); type names are mapped with --type-map (e.g. PER=PERSON).
  human  human-labelled real tickets, labelled inside the boundary (docs/ML_EVALUATION.md, "Labelling guide"):
         {"id", "genre", "text", "spans": [{"start","end","type"}], "benign": [{"start","end"}],
          "labeller": "<pseudonymous id>", "reviewer": "<pseudonymous id>", "status": "agreed" | "disputed",
          "labelled_on": "YYYY-MM-DD", "guide_version": "1"}
         Only status "agreed" rows are scored; disputed rows are counted and reported, never silently dropped.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

GENRE_KEYS = ("genre", "family", "slice")


@dataclass
class Example:
    id: str
    text: str
    spans: list[tuple[int, int, str]]
    genre: str = "all"
    benign: list[tuple[int, int]] = field(default_factory=list)


class DatasetError(ValueError):
    pass


def _rows(path: Path):
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if line.strip():
                try:
                    yield n, json.loads(line)
                except ValueError:
                    raise DatasetError(f"{path.name}:{n}: not JSON") from None


def _genre(r: dict) -> str:
    for k in GENRE_KEYS:
        if isinstance(r.get(k), str) and r[k]:
            return r[k]
    tid = r.get("template_id")
    return tid.split(".")[0] if isinstance(tid, str) and tid else "all"


def _spans(r: dict, n: int, where: str, key: str = "spans", typed: bool = True) -> list:
    out = []
    for s in r.get(key) or []:
        a, b = s.get("start"), s.get("end")
        if not (isinstance(a, int) and isinstance(b, int) and 0 <= a < b <= n):
            raise DatasetError(f"{where}: {key} offsets out of range")
        if typed:
            if not isinstance(s.get("type"), str):
                raise DatasetError(f"{where}: span without type")
            out.append((a, b, s["type"]))
        else:
            out.append((a, b))
    return out


def load_pgw(path: Path) -> list[Example]:
    out = []
    for n, r in _rows(path):
        t = r.get("text")
        if not isinstance(t, str):
            raise DatasetError(f"{path.name}:{n}: no text")
        out.append(Example(str(r.get("id", n)), t, _spans(r, len(t), f"{path.name}:{n}"), _genre(r),
                           _spans(r, len(t), f"{path.name}:{n}", "benign", typed=False)))
    return out


def load_bio(path: Path, label_names: list[str] | None = None, type_map: dict | None = None) -> list[Example]:
    out = []
    tm = type_map or {}
    for n, r in _rows(path):
        toks, tags = r.get("tokens"), r.get("ner_tags")
        if not isinstance(toks, list) or not isinstance(tags, list) or len(toks) != len(tags):
            raise DatasetError(f"{path.name}:{n}: tokens and ner_tags must be lists of equal length")
        names = r.get("label_names") or label_names
        tags = [names[t] if isinstance(t, int) else t for t in tags] if names else tags
        if not all(isinstance(t, str) for t in tags):
            raise DatasetError(f"{path.name}:{n}: integer tags need label_names")
        spaces = r.get("spaces") or [True] * len(toks)
        text, offs = "", []
        for i, tok in enumerate(toks):
            offs.append((len(text), len(text) + len(tok)))
            text += tok + (" " if spaces[i] and i < len(toks) - 1 else "")
        from pgw.ml.decode import decode
        spans = [(offs[a][0], offs[b][1], tm.get(t, t)) for a, b, t in decode(tags)]
        out.append(Example(str(r.get("id", n)), text, spans, _genre(r)))
    return out


def load_human(path: Path) -> tuple[list[Example], dict]:
    out, stats = [], {"rows": 0, "agreed": 0, "disputed": 0}
    for n, r in _rows(path):
        stats["rows"] += 1
        for k in ("id", "genre", "text", "labeller", "status"):
            if not isinstance(r.get(k), str) or not r[k]:
                raise DatasetError(f"{path.name}:{n}: human-labelled rows need {k}")
        if r["status"] not in ("agreed", "disputed"):
            raise DatasetError(f"{path.name}:{n}: status must be agreed or disputed")
        if r["status"] == "disputed":
            stats["disputed"] += 1
            continue
        stats["agreed"] += 1
        t = r["text"]
        out.append(Example(r["id"], t, _spans(r, len(t), f"{path.name}:{n}"), r["genre"],
                           _spans(r, len(t), f"{path.name}:{n}", "benign", typed=False)))
    return out, stats


def load(path: str | Path, fmt: str, **kw) -> tuple[list[Example], dict]:
    p = Path(path)
    if fmt == "pgw":
        ex = load_pgw(p)
        return ex, {"rows": len(ex)}
    if fmt == "bio":
        ex = load_bio(p, kw.get("label_names"), kw.get("type_map"))
        return ex, {"rows": len(ex)}
    if fmt == "human":
        return load_human(p)
    raise DatasetError(f"unknown format {fmt!r}")
