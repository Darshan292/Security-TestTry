"""Scoped HMAC aliases (ADR-0002).

alias   = "[" TYPE "_" crockford32(HMAC-SHA256(k_scope, TYPE || 0x1F || canon(value)))[:6] "]"
k_scope = HKDF-SHA256(k_tenant_alias, info = "pgw/alias/v1" || scope_id)

Same value in the same scope -> same alias, with no stored state.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .detect.numwords import to_digits
from .types import EntityType

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TYPE_NAMES = "|".join(sorted((t.value for t in EntityType), key=len, reverse=True))

# Strict form as we emit it, and the lenient forms we accept back from a model.
# Suffix = one decimal digit + Crockford base32 (no I, L, O, U). Ordinary identifiers such as
# "Phone_Number" or "Bank_Account_Number" can therefore never parse as an alias.
_CK = "0-9A-HJKMNP-TV-Z"
ALIAS_RE = re.compile(rf"\[?\b({_TYPE_NAMES})_([0-9][{_CK}]{{5}}(?:[{_CK}]{{2}})?)\b\]?", re.IGNORECASE)
MAX_ALIAS_LEN = 1 + max(len(t.value) for t in EntityType) + 1 + 8 + 1

# Mangled forms a model produces (re-audit N9). Used by the restorer ONLY together with this request's alias map: a
# match is restored only if its normalised key (TYPE_CODE, upper case, no whitespace) is a known alias. Three shapes:
#   both brackets ([ or full-width ［ ... ] or ］): whitespace (also a newline) inside the brackets, a space, hyphen or
#     underscore between type and code, whitespace inside the code ("[PERSON 2E68HT]", "[ person-2e68ht ]",
#     "[PERSON_2E\n68HT]");
#   one bracket missing ("[PERSON 2E68HT", "PERSON-2E68HT]"): separator variants only, no whitespace in the code, and
#     the code must end (or start) at a word boundary.
# The bare strict form ("person_2e68ht", both brackets missing) stays ALIAS_RE's.
_OPEN, _CLOSE = "[\\[\uff3b]", "[\\]\uff3d]"
_CODE_WS = rf"[0-9](?:\s{{0,2}}[{_CK}]){{5}}(?:(?:\s{{0,2}}[{_CK}]){{2}})?"
_CODE = rf"[0-9][{_CK}]{{5}}(?:[{_CK}]{{2}})?"
_SEP_WS = r"(?:\s{0,2}[_\-]\s{0,2}|\s{1,3})"
_SEP = r"(?:[ \t]?[_\-][ \t]?|[ \t]{1,2})"
LENIENT_ALIAS_RE = re.compile(
    rf"{_OPEN}\s{{0,2}}(?P<t1>{_TYPE_NAMES}){_SEP_WS}(?P<c1>{_CODE_WS})\s{{0,2}}{_CLOSE}"
    rf"|{_OPEN}[ \t]?(?P<t2>{_TYPE_NAMES}){_SEP}(?P<c2>{_CODE})(?![0-9A-Za-z_])"
    rf"|(?<![0-9A-Za-z_])(?P<t3>{_TYPE_NAMES}){_SEP}(?P<c3>{_CODE})[ \t]?{_CLOSE}", re.IGNORECASE)
# A tail that may still grow into a bracketed lenient alias (streaming hold-back; bounded by LENIENT_HOLD chars).
LENIENT_PREFIX_RE = re.compile(rf"{_OPEN}\s{{0,2}}[A-Za-z_]{{0,12}}(?:[\s_\-]{{1,4}}[0-9A-Za-z\s]{{0,16}})?")
LENIENT_HOLD = 40
_WS = re.compile(r"\s+")


def lenient_key(m: re.Match) -> str:
    """'TYPE_CODE' (upper case, whitespace removed) of a LENIENT_ALIAS_RE match."""
    t = m.group("t1") or m.group("t2") or m.group("t3")
    c = m.group("c1") or m.group("c2") or m.group("c3")
    return f"{t.upper()}_{_WS.sub('', c).upper()}"


def scope_key(k_tenant_alias: bytes, scope_id: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=b"pgw/alias/v1" + scope_id.encode(),
    ).derive(k_tenant_alias)


def _fold(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def canon(t: EntityType, value: str) -> str:
    """Type-specific canonical form so formatting variants share an alias."""
    v = unicodedata.normalize("NFKC", value)
    if t in (EntityType.AADHAAR, EntityType.CARD, EntityType.BANK_ACCOUNT):
        return to_digits(v)              # digits, also when spelled in words ("nine eight ...")
    if t is EntityType.PHONE:
        d = to_digits(v)
        return d[-10:] if len(d) > 10 and (d.startswith("91") or d.startswith("0")) else d
    if t in (EntityType.PAN, EntityType.IFSC, EntityType.EMPLOYEE_ID):
        return re.sub(r"[\s_\-]", "", v).upper()
    if t in (EntityType.EMAIL, EntityType.UPI_ID):
        return v.strip().lower()
    return _fold(v)


def _b32(digest: bytes, n: int) -> str:
    """First char is a decimal digit (log2 10 bits), the rest Crockford base32: ~28.3 bits for n=6."""
    bits = int.from_bytes(digest[:6], "big")
    return str(digest[6] % 10) + "".join(CROCKFORD[(bits >> (43 - 5 * i)) & 31] for i in range(n - 1))


def make_alias(k_scope: bytes, t: EntityType, value: str, length: int = 6) -> str:
    mac = hmac.new(k_scope, t.value.encode() + b"\x1f" + canon(t, value).encode(), hashlib.sha256).digest()
    return f"[{t.value}_{_b32(mac, length)}]"


class AliasAllocator:
    """Allocates aliases for one request; resolves the (rare) 30-bit collision deterministically."""

    def __init__(self, k_scope: bytes):
        self.k_scope = k_scope
        self._by_alias: dict[str, tuple[EntityType, str]] = {}   # alias -> (type, canon)
        self._by_key: dict[tuple[EntityType, str], str] = {}     # (type, canon) -> alias

    def get(self, t: EntityType, value: str) -> str:
        key = (t, canon(t, value))
        if key in self._by_key:
            return self._by_key[key]
        a = make_alias(self.k_scope, t, value)
        other = self._by_alias.get(a)
        if other is not None and other != key:
            # Collision: the lexicographically larger canonical value gets the 8-char suffix.
            if key[1] > other[1]:
                a = make_alias(self.k_scope, t, value, 8)
            else:
                longer = make_alias(self.k_scope, other[0], other[1], 8)
                self._by_alias[longer] = other
                self._by_key[other] = longer
                # `a` stays with the new value; the old holder moves to the long form.
        self._by_alias[a] = key
        self._by_key[key] = a
        return a


    def claim(self, t: EntityType, value: str, alias: str) -> bool:
        """Point `value` at an alias chosen elsewhere (a name the conversation already linked to a person). Refused,
        and nothing changes, when another value in this request holds that alias."""
        key = (t, canon(t, value))
        other = self._by_alias.get(alias)
        if other is not None and other != key:
            return False
        old = self._by_key.get(key)
        if old is not None and self._by_alias.get(old) == key:
            del self._by_alias[old]
        self._by_alias[alias] = key
        self._by_key[key] = alias
        return True


def parse_alias(text: str) -> tuple[str, str] | None:
    """'[person_7qx2ma' / 'PERSON_7QX2MA]' -> ('PERSON', '7QX2MA'); None if not alias-shaped."""
    m = ALIAS_RE.fullmatch(text)
    return (m.group(1).upper(), m.group(2).upper()) if m else None
