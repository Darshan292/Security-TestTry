"""Plugin registry, profile validation, and the per-request runtime (docs/PLUGINS.md).

Configuration lives ONLY in the signed deployment profile (`plugins:` in registry/deployment.yaml; FIELD_CLASS
profile, not read from the environment):

    plugins:
      stdnum: "off"            # validator: off | shadow
      phonenumbers: "off"      # validator: off | shadow
      detect_secrets: "off"    # recognizer: off | shadow | union

(`off` must be quoted: YAML reads a bare off as the boolean false.) An unknown plugin name or a mode the slot does
not offer stops startup. Every plugin defaults to off; `plugins: null` means all off.

Modes:
- off: not built, not imported.
- shadow: runs on every inspected string, its verdicts/spans are compared with the built-in ones, and only COUNTS
  of disagreements (by plugin, entity type and verdict pair) are recorded in process metrics and the log. Never the
  value, never a hash of it. A shadow plugin that raises is counted and ignored: it never affects the request.
- union (recognizers only): the plugin's spans are ADDED to detection and go through the existing overlap
  resolution with scores chosen so that no character the built-in masks is ever released (_union_detections). A union plugin that raises fails the request
  closed (503), like the ML detector.
- Validators have no enforce mode: replacing the authority for what counts as an Aadhaar is a human decision after
  a held-out comparison (docs/PLUGINS.md), not a configuration switch.
"""
from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from ..types import Detection, EntityType
from . import adapters
from .base import (OFF, RECOGNIZER, SHADOW, SLOT_MODES, UNION, VALIDATOR, PluginConfigError, PluginError, Verdict)
from .candidates import candidates

log = logging.getLogger("pgw.plugins")

# Union-mode spans enter the existing overlap resolution (pipeline._resolve: higher score, then longer span) with a
# score chosen so that no character the built-in detection masks is ever released:
# - a span already inside one existing detection adds nothing and is dropped here;
# - a span that partially overlaps an existing detection gets score 0.0 and loses to it (the existing one stays;
#   the plugin adds nothing there);
# - a span that only CONTAINS existing detections gets the highest of their scores; being longer it wins the tie and
#   replaces them, so it masks a superset of their characters (as SECRET, which is never restored).
UNION_FLOOR = 0.0


def _union_detections(name: str, et: EntityType, spans, dets: list[Detection], n: int) -> list[Detection]:
    out = []
    for s, e in spans:
        if not 0 <= s < e <= n:
            continue
        over = [d for d in dets if d.start < e and s < d.end]
        if any(d.start <= s and e <= d.end for d in over):
            continue
        partial = any(not (s <= d.start and d.end <= e) for d in over)
        score = UNION_FLOOR if partial else max((d.score for d in over), default=UNION_FLOOR)
        out.append(Detection(s, e, et, score, f"P:{name}"))
    return out


@dataclass(frozen=True)
class PluginSpec:
    name: str
    slot: str
    factory: Callable[[], object]
    licence: str
    distribution: str


REGISTRY: dict[str, PluginSpec] = {s.name: s for s in (
    PluginSpec("stdnum", VALIDATOR, adapters.StdnumValidator, "LGPL-2.1-or-later", "python-stdnum"),
    PluginSpec("phonenumbers", VALIDATOR, adapters.PhonenumbersValidator, "Apache-2.0", "phonenumbers"),
    PluginSpec("detect_secrets", RECOGNIZER, adapters.DetectSecretsRecognizer, "Apache-2.0", "detect-secrets"),
)}


def parse_plugins(raw) -> dict[str, str]:
    """Validate the profile's `plugins` mapping. Returns {name: mode} for EVERY registered plugin (off filled in)."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PluginConfigError("plugins must be a mapping {plugin name: mode}")
    unknown = sorted(str(k) for k in raw if k not in REGISTRY)
    if unknown:
        raise PluginConfigError(f"plugins: unknown plugin(s) {unknown} (known: {sorted(REGISTRY)})")
    out = {}
    for name, spec in sorted(REGISTRY.items()):
        mode = raw.get(name, OFF)
        if mode is False:
            raise PluginConfigError(f"plugins.{name}: write the mode as a quoted string (\"off\"); YAML reads a bare "
                                    "off as the boolean false")
        modes = SLOT_MODES[spec.slot]
        if not isinstance(mode, str) or mode not in modes:
            extra = (" (enforce is not offered for validators: changing which values count as valid is a decision "
                     "for after a held-out comparison; docs/PLUGINS.md)") if spec.slot == VALIDATOR else ""
            raise PluginConfigError(f"plugins.{name}: mode must be one of {list(modes)}{extra}")
        out[name] = mode
    return out


def active(cfg: dict | None) -> dict[str, str]:
    """The non-off entries of a (possibly partial or None) plugin configuration, validated."""
    return {n: m for n, m in parse_plugins(cfg).items() if m != OFF}


class PluginMetrics:
    """Process-local counters, keyed by fixed vocabularies only (plugin name, slot, entity type, verdicts/outcomes).
    Operator metrics: never part of the transparency report, never holding content."""

    def __init__(self):
        self._lock = threading.Lock()
        self._c: Counter = Counter()

    def add(self, counts: Counter) -> None:
        if counts:
            with self._lock:
                self._c.update(counts)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"|".join(k): v for k, v in sorted(self._c.items())}


def _log_counts(counts: Counter) -> None:
    for (plugin, kind, etype, builtin, other), n in sorted(counts.items()):
        if kind in ("disagree", "error", "capped") or (kind == "plugin_only" and other == "VALID"):
            log.info("event='plugin_shadow' plugin=%r kind=%r entity_type=%r builtin=%r plugin_verdict=%r count=%d",
                     plugin, kind, etype, builtin, other, n)


class PluginSet:
    """The configured plugins of one gateway. `apply(text, detections)` is called by the Protector once per
    inspected string (the original text, after the built-in detection) and returns the union-mode spans to add."""

    def __init__(self, modes: dict[str, str], built: dict[str, object]):
        self.modes = dict(modes)
        self.metrics = PluginMetrics()
        self.builtin = adapters.BuiltinValidator()
        self.shadow_validators = [built[n] for n, m in sorted(modes.items())
                                  if m == SHADOW and REGISTRY[n].slot == VALIDATOR]
        self.shadow_recognizers = [built[n] for n, m in sorted(modes.items())
                                   if m == SHADOW and REGISTRY[n].slot == RECOGNIZER]
        self.union_recognizers = [built[n] for n, m in sorted(modes.items()) if m == UNION]
        self._val_types = frozenset().union(*(v.types for v in self.shadow_validators)) \
            if self.shadow_validators else frozenset()

    def apply(self, text: str, dets: list[Detection]) -> list[Detection]:
        added: list[Detection] = []
        for rec in self.union_recognizers:          # authoritative additions: any failure fails the request closed
            try:
                spans = rec.find(text)
            except Exception:  # noqa: BLE001 - any failure of an authoritative plugin fails the request closed
                raise PluginError(rec.name) from None
            added += _union_detections(rec.name, EntityType(rec.entity_type), spans, dets, len(text))
        counts: Counter = Counter()
        if self.shadow_validators:
            self._shadow_validate(text, counts)
        for rec in self.shadow_recognizers:
            self._shadow_recognize(rec, text, dets, counts)
        if counts:
            self.metrics.add(counts)
            _log_counts(counts)
        return added

    def _shadow_validate(self, text: str, counts: Counter) -> None:
        try:
            cands, capped = candidates(text, self._val_types)
        except Exception:  # noqa: BLE001 - shadow: counted, never affects the request
            counts[("candidates", "error", "-", "-", "-")] += 1
            return
        if capped:
            counts[("candidates", "capped", "-", "-", "-")] += 1
        for v in self.shadow_validators:
            try:
                for t, cs, ce in cands:
                    if t not in v.types:
                        continue
                    c = text[cs:ce]
                    other = v.validate(t, c)
                    if other is Verdict.UNKNOWN:
                        continue
                    mine = self.builtin.validate(t, c)
                    counts[(v.name, "compared", t, "-", "-")] += 1
                    if mine is Verdict.UNKNOWN:    # coverage, not disagreement: the built-in has no opinion here
                        counts[(v.name, "plugin_only", t, mine.value, other.value)] += 1
                    elif mine is not other:
                        counts[(v.name, "disagree", t, mine.value, other.value)] += 1
            except Exception:  # noqa: BLE001 - shadow: counted, never affects the request
                counts[(v.name, "error", "-", "-", "-")] += 1

    def _shadow_recognize(self, rec, text: str, dets: list[Detection], counts: Counter) -> None:
        try:
            spans = rec.find(text)
        except Exception:  # noqa: BLE001 - shadow: counted, never affects the request
            counts[(rec.name, "error", "-", "-", "-")] += 1
            return
        et = rec.entity_type
        n = len(text)
        same, other, theirs = bytearray(n + 1), bytearray(n + 1), bytearray(n + 1)   # character coverage maps
        same_spans = set()
        for d in dets:
            if 0 <= d.start < d.end <= n:
                cov = same if d.type.value == et else other
                cov[d.start:d.end] = b"\x01" * (d.end - d.start)
                if cov is same:
                    same_spans.add((d.start, d.end))
        for s, e in spans:
            theirs[s:e] = b"\x01" * (e - s)
        for s, e in spans:
            counts[(rec.name, "compared", et, "-", "-")] += 1
            if same.find(1, s, e) != -1:
                continue
            mine = "OTHER_TYPE" if other.find(1, s, e) != -1 else "NONE"
            counts[(rec.name, "disagree", et, mine, "FOUND")] += 1
        for s, e in same_spans:
            if theirs.find(1, s, e) == -1:
                counts[(rec.name, "disagree", et, "FOUND", "NONE")] += 1


def build(cfg: dict | None) -> PluginSet | None:
    """Build the configured plugins at startup (None when every plugin is off). A library that is missing or fails
    to load stops startup: a configured plugin that cannot run is a configuration error, not a silent no-op."""
    modes = active(cfg)
    if not modes:
        return None
    built = {}
    for name in sorted(modes):
        spec = REGISTRY[name]
        try:
            built[name] = spec.factory()
        except ImportError:
            raise PluginConfigError(f"plugin {name!r} is configured ({modes[name]}) but {spec.distribution} is not "
                                    "installed (pip install --require-hashes -r requirements/plugins.txt)") from None
    return PluginSet(modes, built)
