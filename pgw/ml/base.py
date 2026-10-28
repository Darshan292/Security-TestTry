"""Shared contracts of the ML layer. No third-party imports here."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class MlUnavailable(Exception):
    """An ML component could not give a trustworthy answer (load failure, timeout, queue full, crash, bad input).
    Callers must fail closed: the request is not sent."""


@dataclass(frozen=True)
class Span:
    """A detected span. Offsets are Python str indices (code points) into the exact text passed in, end exclusive.
    `type` is the MODEL's entity label (without BIO prefix); the deployment profile maps it to a gateway type."""
    start: int
    end: int
    type: str
    score: float


@runtime_checkable
class SpanDetector(Protocol):
    def detect(self, text: str) -> list[Span]: ...

    def detect_many(self, texts: list[str]) -> list[list[Span]]: ...

    def warm(self) -> None: ...


@runtime_checkable
class ContentGate(Protocol):
    def classify(self, text: str) -> dict[str, float]: ...

    def classify_many(self, texts: list[str]) -> list[dict[str, float]]: ...

    def warm(self) -> None: ...


_SURR = re.compile("[\ud800-\udfff]")


def clean_text(text) -> str:
    """Validate the input and replace lone surrogates (which a Rust tokenizer cannot take) by U+FFFD: same length,
    so offsets are unchanged."""
    if not isinstance(text, str):
        raise MlUnavailable(f"expected str, got {type(text).__name__}")
    return _SURR.sub("�", text)


def trim_span(text: str, s: int, e: int) -> tuple[int, int]:
    """Shrink [s, e) past whitespace at both edges (byte-level BPE offsets often include a leading space)."""
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e
