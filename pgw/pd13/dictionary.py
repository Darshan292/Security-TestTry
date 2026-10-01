"""L3 key dictionary: loader and matcher for ``dictionary.json``.

Each entry is ``{"key", "concept", "provenance"}`` (+ optional ``needs_evidence`` / ``suffix`` / ``anyword`` booleans).
``concept`` is one of the 13 taxonomy types or RELEASE_DATE / RELEASE_ID / RELEASE_AMOUNT / RELEASE_TEXT.
``provenance`` is ``standard:<source + section/URL>`` (only the sources listed in PD13_DESIGN.md) or
``dev3:<item id>`` (key learned from dev3build).

A key is normalised to lower-case letters and digits with camelCase / snake_case / punctuation removed
(``Date_of_Birth``, ``dateOfBirth``, ``date of birth`` -> ``dateofbirth``). A ``/`` in an entry key is a
path suffix (``Cdtr/Nm`` matches an ``Nm`` element whose parent is ``Cdtr``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pgw.types import EntityType

TYPE_CONCEPTS = {t.value for t in EntityType}
RELEASE_CONCEPTS = {"RELEASE_DATE", "RELEASE_ID", "RELEASE_AMOUNT", "RELEASE_TEXT"}
CONCEPTS = TYPE_CONCEPTS | RELEASE_CONCEPTS
PATH = Path(__file__).with_name("dictionary.json")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def key_words(key: str) -> list[str]:
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", _CAMEL.sub(" ", key)) if w]


def norm_key(key: str) -> str:
    return "".join(key_words(key))


@dataclass(frozen=True)
class Entry:
    key: str
    concept: str
    provenance: str
    needs_evidence: bool = False
    suffix: bool = False
    anyword: bool = False


class Dictionary:
    def __init__(self, entries: list[dict]):
        self.entries = [Entry(e["key"], e["concept"], e["provenance"], bool(e.get("needs_evidence")),
                              bool(e.get("suffix")), bool(e.get("anyword"))) for e in entries]
        self._exact: dict[str, Entry] = {}
        self._path: dict[str, Entry] = {}
        for e in self.entries:
            if "/" in e.key:
                self._path["/".join(norm_key(p) for p in e.key.split("/"))] = e
            else:
                self._exact.setdefault(norm_key(e.key), e)
        self._suffix = {norm_key(e.key): e for e in self.entries if e.suffix and "/" not in e.key}
        self._anyword = {norm_key(e.key): e for e in self.entries if e.anyword and "/" not in e.key}

    @classmethod
    def load(cls, path: Path | str = PATH) -> "Dictionary":
        return cls(json.loads(Path(path).read_text()))

    def lookup(self, path: tuple, key: str | None) -> Entry | None:
        """Entry for a field. A key made of ``/``-separated segments (bilingual labels, ``native / English``) is
        looked up as a whole and then segment by segment."""
        if key is None:
            return None
        e = self._lookup(path, key)
        if e is None and "/" in key:
            for seg in key.split("/"):
                if key_words(seg):
                    e = self._lookup((), seg)
                    if e is not None:
                        break
        return e

    def _lookup(self, path: tuple, key: str) -> Entry | None:
        comps = [norm_key(re.sub(r"^@", "", p)) for p in path if p] if path else [norm_key(key)]
        for n in range(min(4, len(comps)), 1, -1):
            e = self._path.get("/".join(comps[-n:]))
            if e is not None:
                return e
        words = key_words(key)
        for i in range(len(words)):
            sub = "".join(words[i:])
            e = self._exact.get(sub) if i == 0 or len(words) - i >= 2 else None
            if e is not None:
                return e
        if words:
            e = self._suffix.get(words[-1])
            if e is not None and len(words) >= 2:
                return e
            for w in words:                                      # a role word anywhere in the label ("Nominee for dues")
                e = self._anyword.get(w)
                if e is not None and len(words) >= 2:
                    return e
        return None
