"""Detector coverage the served contracts need (`requires_detectors`), checked once at startup.

ADDRESS has no complete rule-based recall (pgw.detect.places is deliberately narrow: house markers, street words, PIN
context). Free-form addresses are only found by an ML detector whose `labels` map a model label (privacy-filter
`private_address`, GLiNER `address`, ...) to ADDRESS. So that nobody believes addresses are protected when they are not:

- production refuses to start when a served contract requires ADDRESS and no ml_detector label maps to it, unless the
  signed profile carries the explicit opt-out `address_coverage: pin_only`;
- development starts with a warning;
- the resulting coverage ("pin_only" or "ml:<first 12 hex of the pinned model bundle sha256>") is on /healthz and in
  every pgw_report, so the state is visible to operators and to the calling application.
"""
from __future__ import annotations

PIN_ONLY = "pin_only"


class CoverageError(ValueError):
    """A served contract needs detector coverage the signed profile neither provides nor waives. Startup stops."""


def address_coverage(ml_detector: dict | None) -> str:
    if ml_detector and "ADDRESS" in (ml_detector.get("labels") or {}).values():
        return "ml:" + str(ml_detector["sha256"])[:12]
    return PIN_ONLY


def check(contracts: dict, ml_detector: dict | None, prod: bool, acked: bool) -> tuple[dict[str, str], str | None]:
    """(coverage {type: state}, warning event or None). Raises CoverageError in production without cover or opt-out."""
    needing = sorted(p for p, c in contracts.items() if "ADDRESS" in c.requires_detectors)
    cov = address_coverage(ml_detector)
    out = {"ADDRESS": cov}
    if not needing or cov != PIN_ONLY:
        return out, None
    if not prod:
        return out, "address_coverage_pin_only_development"
    if not acked:
        raise CoverageError(
            f"purpose(s) {needing} carry free text that can hold postal addresses (requires_detectors: [ADDRESS]) but "
            "no ml_detector label maps to ADDRESS, so only house-marked addresses and PIN codes would be protected. "
            "Configure an ml_detector whose labels map an address label (e.g. private_address) to ADDRESS, or "
            "have the profile accept the limit explicitly with `address_coverage: pin_only` and re-sign it "
            "(OPERATIONS.md, 'Address coverage')")
    return out, "address_coverage_pin_only_acknowledged"
