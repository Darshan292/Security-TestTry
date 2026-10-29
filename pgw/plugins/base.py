"""Plugin slots: the interfaces outside components implement (docs/PLUGINS.md).

Two slots:

- **Validator**: `validate(entity_type, candidate_text) -> Verdict` (VALID | INVALID | UNKNOWN). UNKNOWN means "this
  validator has no opinion" (a type it does not cover); it is never counted as a disagreement.
- **Recognizer**: `find(text) -> list[(start, end)]`, character spans of extra values of the recognizer's
  `entity_type` (offsets into exactly the string given, `end` exclusive).

Adapters must do no I/O at import or call time (no network, no file writes, no reads beyond importing their own
package); tests/test_plugins.py runs every adapter with sockets and file writes disabled.
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable


class Verdict(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


VALIDATOR, RECOGNIZER = "validator", "recognizer"

# Modes per slot. Validators have no `enforce` mode yet: switching the authority for what counts as an Aadhaar (or a
# PAN, a card) is a decision for humans after a held-out comparison, not a configuration option (docs/PLUGINS.md).
OFF, SHADOW, UNION = "off", "shadow", "union"
SLOT_MODES = {VALIDATOR: (OFF, SHADOW), RECOGNIZER: (OFF, SHADOW, UNION)}

# Entity types the validator slot knows (strings, not pgw.types.EntityType: some schemes such as GSTIN, EPIC and VID
# have no gateway type yet). IBAN is the scheme the gateway reports as BANK_ACCOUNT.
VALIDATOR_TYPES = ("AADHAAR", "VID", "PAN", "GSTIN", "EPIC", "IFSC", "CARD", "UPI_ID", "PHONE", "IBAN")


class PluginError(Exception):
    """A plugin failed while handling content. Carries the plugin name only, never content."""

    def __init__(self, plugin: str):
        self.plugin = plugin
        super().__init__(f"plugin {plugin!r} failed")


class PluginConfigError(ValueError):
    """The plugin configuration in the deployment profile is invalid, or a configured plugin cannot be built."""


@runtime_checkable
class Validator(Protocol):
    name: str
    types: frozenset

    def validate(self, entity_type: str, candidate_text: str) -> Verdict: ...


@runtime_checkable
class Recognizer(Protocol):
    name: str
    entity_type: str

    def find(self, text: str) -> list[tuple[int, int]]: ...
