"""Plugin adapters: the built-in reference validator and the outside libraries behind the plugin slots.

Third-party libraries are imported only when an adapter is BUILT (startup, `build_*` below), never when this module
is imported, so a gateway with every plugin off neither needs nor loads them. Building also warms each library's
lazily loaded data (phone metadata, the IBAN registry), so that handling a request reads no file and opens no socket.

Licences (read from each wheel's METADATA; THIRD_PARTY_NOTICES.md): python-stdnum LGPL-2.1-or-later, used as an
unmodified installed library; phonenumbers Apache-2.0; detect-secrets Apache-2.0.
"""
from __future__ import annotations

import re

from ..detect import recognizers as R
from .base import Verdict

VALID, INVALID, UNKNOWN = Verdict.VALID, Verdict.INVALID, Verdict.UNKNOWN
_SEP = re.compile(r"[ \-]")


def _compact(s: str) -> str:
    """Strip surrounding whitespace and the separators both the repo and python-stdnum allow inside a number
    (space, hyphen). Layout rules (where groups may break) belong to the recognizers' candidate search, not to
    whether a value is valid."""
    return _SEP.sub("", s.strip())


def _v(ok: bool) -> Verdict:
    return VALID if ok else INVALID


# --- built-in: the repo's current checksum / format logic (pgw.detect.recognizers), as a validator -----------------

_AADHAAR = re.compile(r"[2-9][0-9]{11}")
_PAN_ANYCASE = re.compile(r"[A-Za-z]{3}[ABCFGHLJPTabcfghljpt][A-Za-z][0-9]{4}[A-Za-z]")   # = recognizers._PAN_ANY
_IFSC = re.compile(r"[A-Z]{4}0[A-Z0-9]{6}")                                                # = recognizers._IFSC
_UPI_SHAPE = re.compile(r"[A-Za-z0-9._\-]{2,64}@[A-Za-z][A-Za-z0-9]{1,30}")               # = recognizers._UPI_ANY


class BuiltinValidator:
    """Reference adapter over pgw.detect.recognizers: Verhoeff (Aadhaar), PAN holder-type pattern, IFSC pattern,
    Luhn + issuer prefix (card), UPI handle@PSP, the Indian/international phone patterns, IBAN mod-97 + length.
    It is what decides today; it is never configured, only compared against."""

    name = "builtin"
    types = frozenset({"AADHAAR", "PAN", "IFSC", "CARD", "UPI_ID", "PHONE", "IBAN"})

    def validate(self, entity_type: str, candidate_text: str) -> Verdict:
        t, s = entity_type, candidate_text
        if t == "AADHAAR":
            d = _compact(s)
            return _v(_AADHAAR.fullmatch(d) is not None and R.verhoeff_valid(d))
        if t == "PAN":
            return _v(_PAN_ANYCASE.fullmatch(_compact(s)) is not None)
        if t == "IFSC":
            return _v(_IFSC.fullmatch(s.strip()) is not None)
        if t == "CARD":
            d = _compact(s)
            return _v(d.isascii() and d.isdigit() and 13 <= len(d) <= 19 and R._iin_ok(d) and R.luhn_valid(d))
        if t == "UPI_ID":
            v = s.strip()
            if R._UPI.fullmatch(v):
                return VALID
            if _UPI_SHAPE.fullmatch(v):
                h = v.split("@", 1)[0]
                # <mobile>@<any psp> is a UPI id by its shape; other handles at an unknown PSP need a cue (context)
                return VALID if re.fullmatch(r"[6-9]\d{9}", h) else UNKNOWN
            return INVALID
        if t == "PHONE":
            v = s.strip()
            if R._PHONE_IN.fullmatch(v):
                return VALID
            if R._PHONE_LL.fullmatch(v) and sum(c.isdigit() for c in v) == 11:
                return VALID
            if R._PHONE_INTL.fullmatch(v) and 8 <= sum(c.isdigit() for c in v) <= 15:
                return VALID
            return INVALID
        if t == "IBAN":
            d = _compact(s)
            need = R._IBAN_LEN.get(d[:2].upper())
            return _v(need is not None and len(d) == need and R.iban_valid(d))
        return UNKNOWN


# --- python-stdnum ---------------------------------------------------------------------------------------------------

class StdnumValidator:
    """python-stdnum (LGPL-2.1+): stdnum.in_.aadhaar / vid / pan / gstin / epic, stdnum.iban, and stdnum.luhn for
    cards. stdnum has no card scheme, so CARD is framed here as ISO/IEC 7812: 13-19 digits and a Luhn check (no
    issuer-prefix list). No IFSC, UPI or phone module: UNKNOWN for those."""

    name = "stdnum"
    types = frozenset({"AADHAAR", "VID", "PAN", "GSTIN", "EPIC", "CARD", "IBAN"})

    def __init__(self):
        from stdnum import iban, luhn
        from stdnum.in_ import aadhaar, epic, gstin, pan, vid
        self._mod = {"AADHAAR": aadhaar, "VID": vid, "PAN": pan, "GSTIN": gstin, "EPIC": epic, "IBAN": iban}
        self._luhn = luhn
        iban.is_valid("DE89 3704 0044 0532 0130 00")     # warm: loads the IBAN registry (iban.dat) now, not per request

    def validate(self, entity_type: str, candidate_text: str) -> Verdict:
        if entity_type == "CARD":
            d = _compact(candidate_text)
            return _v(d.isascii() and d.isdigit() and 13 <= len(d) <= 19 and self._luhn.is_valid(d))
        mod = self._mod.get(entity_type)
        if mod is None:
            return UNKNOWN
        return _v(bool(mod.is_valid(candidate_text)))


# --- phonenumbers ----------------------------------------------------------------------------------------------------

class PhonenumbersValidator:
    """phonenumbers (Apache-2.0, a port of Google's libphonenumber). Default region IN. VALID = parses and
    is_valid_number (the number is in an allocated range of its region's numbering plan); INVALID otherwise,
    including numbers that are only is_possible_number (right length, unallocated range)."""

    name = "phonenumbers"
    types = frozenset({"PHONE"})

    def __init__(self, region: str = "IN"):
        import phonenumbers
        self._pn = phonenumbers
        self.region = region
        for warm in ("+91 98765 43210", "+1 650 253 0000", "+44 20 7946 0000", "+971 4 123 4567"):
            phonenumbers.is_valid_number(phonenumbers.parse(warm, region))   # load the common regions' metadata now

    def possible(self, candidate_text: str) -> bool:
        try:
            return bool(self._pn.is_possible_number(self._pn.parse(candidate_text, self.region)))
        except self._pn.NumberParseException:
            return False

    def validate(self, entity_type: str, candidate_text: str) -> Verdict:
        if entity_type != "PHONE":
            return UNKNOWN
        try:
            n = self._pn.parse(candidate_text, self.region)
        except self._pn.NumberParseException:
            return INVALID
        return _v(bool(self._pn.is_valid_number(n)))


# --- detect-secrets (recognizer) -------------------------------------------------------------------------------------

# detect-secrets plugins used: every regex-based detector except IPPublicDetector (an IP address is not a secret).
# Not used: the entropy and keyword plugins (they are line/file-type heuristics built for source files, not prose),
# and every plugin's verify() (it would call the vendor's API over the network).
_DS_EXCLUDE = frozenset({"IPPublicDetector"})
_MAX_SPANS = 4096
# Two detect-secrets regexes take quadratic time on adversarial input when run as-is (measured: 16 KB of "/" costs
# NpmDetector ~1 s, 100 KB would cost ~30 s; "eyJ" repeated without a dot does the same to JwtTokenDetector). The
# adapter does not change those regexes; it only decides WHERE they run, so that every match they can produce is
# still found and the cost stays linear:
# - NpmDetector `//.+/:_authToken=\s*(npm_.+|[hex-]{36}).*` can only match on a line holding "//", then at least one
#   character, then "/:_authToken=" followed by the token. That is checked with str.find (linear); the regex then runs
#   once (search) from the start of each such line, where its first attempt succeeds.
# - JwtTokenDetector can only match inside a maximal run of its own alphabet (below) that holds "eyJ" and a dot; it
#   runs on each such run of at most _MAX_JWT_RUN characters (a longer run is skipped: union mode then adds nothing
#   there, which is never less masking than plugins off).
_NPM_KEY = "/:_authToken="
_NPM_SUFFIX = re.compile(r"\s*(?:npm_.|[A-Fa-f0-9-]{36})")
_JWT_RUN = re.compile(r"[A-Za-z0-9\-_=.+/]+")
_MAX_JWT_RUN = 4096


def _npm_matches(rx: re.Pattern, text: str):
    starts, p = set(), text.find(_NPM_KEY)
    while p != -1:
        ls = text.rfind("\n", 0, p) + 1
        i = text.find("//", ls, p)
        if i != -1 and p >= i + 3 and _NPM_SUFFIX.match(text, p + len(_NPM_KEY)):
            starts.add(ls)
        p = text.find(_NPM_KEY, p + 1)
    for ls in sorted(starts):
        m = rx.search(text, ls)
        if m is not None:
            yield m.span()


def _jwt_matches(rx: re.Pattern, text: str):
    for run in _JWT_RUN.finditer(text):
        r = run.group()
        k = r.find("eyJ")
        if k == -1 or r.find(".", k) == -1 or len(r) > _MAX_JWT_RUN:
            continue
        for m in rx.finditer(r):
            yield run.start() + m.start(), run.start() + m.end()


_GUARDED = {"NpmDetector": _npm_matches, "JwtTokenDetector": _jwt_matches}


class DetectSecretsRecognizer:
    """detect-secrets (Yelp, Apache-2.0), used in-process: its plugin classes' own regexes run on the text and every
    match is mapped to the character span of the WHOLE regex match. Not the capture group detect-secrets reports as
    the secret value: for some plugins that group is only a prefix (GitHubTokenDetector reports "ghp" for a whole
    ghp_... token), so masking the reported value would leave the token itself in the text. The whole match can
    include a delimiter or a key name; masking more only reduces leaks. No file scanning, no baseline, no settings
    file, no verification calls.

    Note: `import detect_secrets.plugins.base` imports `requests` (module import only; it is used for verify(), which
    this adapter never calls)."""

    name = "detect_secrets"
    entity_type = "SECRET"

    def __init__(self):
        import importlib
        import inspect
        import pkgutil

        import detect_secrets.plugins as dsp
        from detect_secrets.plugins.base import RegexBasedDetector
        plugins = []
        for info in sorted(pkgutil.iter_modules(dsp.__path__), key=lambda m: m.name):
            mod = importlib.import_module(f"detect_secrets.plugins.{info.name}")
            for _, cls in sorted(inspect.getmembers(mod, inspect.isclass)):
                if (cls.__module__ == mod.__name__ and issubclass(cls, RegexBasedDetector)
                        and not inspect.isabstract(cls) and cls.__name__ not in _DS_EXCLUDE):
                    plugins.append(cls())
        if not plugins:
            raise RuntimeError("detect-secrets: no regex-based plugins found")
        self._plugins = plugins
        self._base_analyze = RegexBasedDetector.analyze_string
        self.plugin_names = tuple(type(p).__name__ for p in plugins)

    def find(self, text: str) -> list[tuple[int, int]]:
        spans: set[tuple[int, int]] = set()
        for p in self._plugins:
            # A plugin that refines its regex hits (the JWT detector decodes them) keeps only hits it still yields.
            refine = type(p).analyze_string is not self._base_analyze
            guard = _GUARDED.get(type(p).__name__)
            for rx in p.denylist:
                hits = guard(rx, text) if guard is not None else (m.span() for m in rx.finditer(text))
                for s, e in hits:
                    if e <= s or (refine and not any(True for _ in p.analyze_string(text[s:e]))):
                        continue
                    spans.add((s, e))
                    if len(spans) > _MAX_SPANS:
                        raise RuntimeError("detect-secrets: span limit exceeded")
        return sorted(spans)
