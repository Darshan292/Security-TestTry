"""Label sets for the ML detectors (pure Python, no dependency on the gateway package).

ENTITY_TYPES must equal the gateway's `pgw.types.EntityType` members minus the three excluded below.
tests/ml_train/test_labels.py derives the expected set from the enum, so this list cannot drift silently.

Excluded on purpose:
  AADHAAR_LIKE  is not a *kind* of value; it is the gateway's cue-less backstop verdict for a 12-digit number that
                happens to pass the Aadhaar checksum. The model labels such numbers AADHAAR (the value is an Aadhaar
                number or indistinguishable from one); the backstop stays in the deterministic layer.
  ADDRESS       is detected by the gateway's address structure rules (pgw.detect.places) and by an ML detector's address
                label (e.g. openai/privacy-filter's private_address, mapped in the ml_detector profile `labels`). The
                models trained here have no address class yet; adding one means new synthetic data and a new label set.
  DERIVED       is a value computed inside the gateway (LOCAL_COMPUTE outputs). It never appears in input text, so
                no detector can or should be trained to find it.
"""
from __future__ import annotations

ENTITY_TYPES: tuple[str, ...] = (
    "PERSON", "EMAIL", "PHONE", "EMPLOYEE_ID", "COMPENSATION", "DATE_OF_BIRTH", "AADHAAR", "PAN",
    "BANK_ACCOUNT", "CARD", "UPI_ID", "SECRET", "IFSC", "IDENTIFIER",
)
EXCLUDED_GATEWAY_TYPES: tuple[str, ...] = ("AADHAAR_LIKE", "ADDRESS", "DERIVED")

# Types whose surface form may legitimately contain a line break (a multi-line private key). Every other type is a
# single-line value; a span of one of them that crosses a newline is a labelling bug (e.g. a PERSON swallowing the
# next line's first word) and is rejected by bio.validate_spans.
NEWLINE_OK: frozenset[str] = frozenset({"SECRET"})

O = "O"
BIO_LABELS: tuple[str, ...] = (O,) + tuple(f"{p}-{t}" for t in ENTITY_TYPES for p in ("B", "I"))
LABEL2ID: dict[str, int] = {l: i for i, l in enumerate(BIO_LABELS)}
ID2LABEL: dict[int, str] = {i: l for l, i in LABEL2ID.items()}

# ----------------------------------------------------------------------------------------------- content gate
# Document/sentence level multi-label for special-category and purpose-dependent content. NONE is exclusive.
GATE_LABELS: tuple[str, ...] = (
    "HEALTH", "DISABILITY", "PREGNANCY", "SEXUAL_ORIENTATION", "RELIGION", "CASTE", "POLITICAL",
    "UNION_MEMBERSHIP", "BIOMETRIC", "CRIMINAL_OR_DISCIPLINARY", "PERFORMANCE_RATING", "FINANCIAL_HARDSHIP",
    "MINOR", "NONE",
)
GATE_POSITIVE: tuple[str, ...] = tuple(l for l in GATE_LABELS if l != "NONE")

# Mapping to pgw.product.contracts.PURPOSE_DEPENDENT (the contract-level stance names). Where the names overlap in
# meaning the gate label feeds that stance; the remaining gate labels are special-category data with no
# purpose-dependent counterpart (a contract cannot choose to allow them; they only drive refusal/routing).
# PURPOSE_DEPENDENT entries with NO gate label (leave_balance, attendance, eligibility_result, derived_compensation)
# are numeric/derived quantities that a sentence classifier cannot judge; they stay declarations in the contract.
GATE_TO_PURPOSE_DEPENDENT: dict[str, str] = {
    "HEALTH": "health_or_leave_reason",
    "DISABILITY": "health_or_leave_reason",
    "PREGNANCY": "health_or_leave_reason",
    "PERFORMANCE_RATING": "performance_rating",
    "CRIMINAL_OR_DISCIPLINARY": "disciplinary_outcome",
}
