"""Plugin slots for outside components, run side by side with the built-in detection (docs/PLUGINS.md).

Nothing existing is replaced: a plugin is off, runs in shadow (compared, counted, never acted on) or, for
recognizers, in union (its spans are added to masking). Modes are set only in the signed deployment profile.
Importing this package imports no third-party library; adapters load theirs when built at startup.
"""
from .base import (OFF, RECOGNIZER, SHADOW, UNION, VALIDATOR, PluginConfigError, PluginError, Recognizer,
                   Validator, Verdict)
from .registry import REGISTRY, PluginSet, build, parse_plugins

__all__ = ["OFF", "RECOGNIZER", "REGISTRY", "SHADOW", "UNION", "VALIDATOR", "PluginConfigError", "PluginError",
           "PluginSet", "Recognizer", "Validator", "Verdict", "build", "parse_plugins"]
