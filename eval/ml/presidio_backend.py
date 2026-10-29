"""Presidio (MIT) as an EVALUATION backend only: nothing in pgw/ imports it, it is not a runtime dependency.

    AnalyzerEngine + the spaCy model already installed for the gateway (en_core_web_lg by default) + Presidio's India
    recognizers (IN_AADHAAR, IN_PAN, IN_GSTIN, IN_PASSPORT, IN_VOTER, IN_VEHICLE_REGISTRATION), which the default
    registry leaves disabled.

No network I/O: the spaCy model is loaded from disk, and the one component that can fetch from the network,
``EmailRecognizer`` (it calls ``tldextract.extract``, which downloads the public-suffix list), is replaced by a subclass
that uses a ``TLDExtract(suffix_list_urls=(), cache_dir=None)`` bound to the snapshot bundled in the wheel.
tests/test_presidio_backend.py proves it by blocking sockets.

Entity mapping (reviewed; ENTITY_MAP). Anything not in the table is IGNORED and COUNTED (``Presidio.notes()``).
Presidio's score is kept as the span score; ``min_score`` filters (default 0.4).
"""
from __future__ import annotations

from collections import Counter

from pgw.types import EntityType

ET = EntityType
ENTITY_MAP: dict[str, EntityType] = {
    "PERSON": ET.PERSON,
    "EMAIL_ADDRESS": ET.EMAIL,
    "PHONE_NUMBER": ET.PHONE,
    "IN_AADHAAR": ET.AADHAAR,
    "IN_PAN": ET.PAN,
    "CREDIT_CARD": ET.CARD,
    # No gateway type of their own: identifier-shaped values of a recognised scheme (IDENTIFIER, class REF).
    "IN_PASSPORT": ET.IDENTIFIER,
    "IN_VOTER": ET.IDENTIFIER,
    "IN_VEHICLE_REGISTRATION": ET.IDENTIFIER,
    "IN_GSTIN": ET.IDENTIFIER,
}
# Deliberately ignored (kept here so a review sees the decision): LOCATION, ORGANIZATION, NRP, AGE, URL, IP_ADDRESS,
# MAC_ADDRESS, IBAN_CODE, CRYPTO, DATE_TIME (a date is a DATE_OF_BIRTH only with a birth cue; Presidio has no such
# cue logic), MEDICAL_LICENSE, US_*/UK_* recognizers, and spaCy's generic ID.
INDIA_ENTITIES = ("IN_AADHAAR", "IN_PAN", "IN_GSTIN", "IN_PASSPORT", "IN_VOTER", "IN_VEHICLE_REGISTRATION")


class PresidioUnavailable(RuntimeError):
    pass


def _offline_email_recognizer():
    import tldextract
    from presidio_analyzer.predefined_recognizers import EmailRecognizer

    extractor = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=(), fallback_to_snapshot=True)

    class OfflineEmailRecognizer(EmailRecognizer):
        def validate_result(self, pattern_text: str):  # noqa: D102 (same rule as upstream, offline extractor)
            return extractor(pattern_text).fqdn != ""

    return OfflineEmailRecognizer()


def build_engine(model: str = "en_core_web_lg"):
    try:
        from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_analyzer.predefined_recognizers import (
            InAadhaarRecognizer, InGstinRecognizer, InPanRecognizer, InPassportRecognizer,
            InVehicleRegistrationRecognizer, InVoterRecognizer)
    except ImportError as e:
        raise PresidioUnavailable("presidio-analyzer is not installed (pip install -r requirements/eval.txt)") from e
    nlp = NlpEngineProvider(nlp_configuration={"nlp_engine_name": "spacy",
                                               "models": [{"lang_code": "en", "model_name": model}]}).create_engine()
    reg = RecognizerRegistry(supported_languages=["en"])
    reg.load_predefined_recognizers(languages=["en"], nlp_engine=nlp)
    reg.recognizers = [r for r in reg.recognizers if type(r).__name__ != "EmailRecognizer"]
    reg.add_recognizer(_offline_email_recognizer())
    for cls in (InAadhaarRecognizer, InPanRecognizer, InGstinRecognizer, InPassportRecognizer,
                InVoterRecognizer, InVehicleRegistrationRecognizer):
        reg.add_recognizer(cls())
    return AnalyzerEngine(registry=reg, nlp_engine=nlp, supported_languages=["en"])


class Presidio:
    """text -> [(start, end, gateway TYPE)] after mapping, overlap resolution and the score filter."""

    def __init__(self, model: str = "en_core_web_lg", min_score: float = 0.4, engine=None):
        self.engine = engine or build_engine(model)
        self.min_score = min_score
        self.ignored: Counter = Counter()
        self.mapped: Counter = Counter()
        self.below_threshold = 0

    def detect(self, text: str) -> list[tuple[int, int, str, float]]:
        found = self.engine.analyze(text=text, language="en", score_threshold=0.0)
        keep = []
        for r in found:
            if r.entity_type not in ENTITY_MAP:
                self.ignored[r.entity_type] += 1
                continue
            if r.score < self.min_score:
                self.below_threshold += 1
                continue
            keep.append(r)
        keep.sort(key=lambda r: (-r.score, -(r.end - r.start), r.start))
        taken: list = []
        for r in keep:
            if any(r.start < t.end and t.start < r.end for t in taken):
                continue
            taken.append(r)
            self.mapped[r.entity_type] += 1
        return sorted(((r.start, r.end, ENTITY_MAP[r.entity_type].value, round(float(r.score), 4)) for r in taken))

    def spans(self, text: str) -> list[tuple[int, int, str]]:
        return [(s, e, t) for s, e, t, _ in self.detect(text)]

    def notes(self) -> dict:
        return {"presidio_min_score": self.min_score, "mapped_entities": dict(sorted(self.mapped.items())),
                "ignored_entities": dict(sorted(self.ignored.items())), "ignored_total": sum(self.ignored.values()),
                "below_threshold": self.below_threshold}


def mask(text: str, spans: list[tuple[int, int, str]]) -> str:
    out = text
    for s, e, t in sorted(spans, key=lambda x: x[0], reverse=True):
        out = out[:s] + f"[{t}]" + out[e:]
    return out
