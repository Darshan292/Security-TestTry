"""End-user identity asserted by an app (workspace -> gateway), bound to the request by an HMAC.

The gateway authenticates the APP (bearer key), never the person. A front end that does authenticate people (the HR
Workspace: trusted-proxy SSO) may pass who is asking so the gateway can rate-limit per person (fair share) instead of
per app. The identity is therefore ASSERTED BY THE APP: the gateway trusts it exactly as far as it trusts the app key.
What the HMAC adds is binding, not authentication: the identity cannot be injected or swapped by anything between
app and gateway that does not hold the app key, and it cannot be replayed on another purpose, another body or after
the timestamp window.

Headers (all three or none):
    X-PGW-End-User       opaque id, [A-Za-z0-9._:@+-]{1,128} (the workspace sends "sso-" + a hash of the SSO identity)
    X-PGW-End-User-Ts    unix seconds, accepted within +-WINDOW_S of the gateway clock
    X-PGW-End-User-Sig   hex HMAC-SHA256, key = the app's bearer key, over
                         "pgw-end-user/v1\\n" purpose "\\n" ts "\\n" user "\\n" sha256(body)
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time

HEADER_USER = "X-PGW-End-User"
HEADER_TS = "X-PGW-End-User-Ts"
HEADER_SIG = "X-PGW-End-User-Sig"
USER_RE = re.compile(r"[A-Za-z0-9._:@+-]{1,128}")
WINDOW_S = 300
_TAG = b"pgw-end-user/v1\n"


class EndUserError(ValueError):
    """The asserted identity is malformed, unsigned, mis-signed or stale (content-free message)."""


def opaque_id(sso_identity: str) -> str:
    """The id a front end sends for an SSO identity: stable, case-insensitive, no personal data on the wire."""
    return "sso-" + hashlib.sha256(sso_identity.strip().casefold().encode("utf-8")).hexdigest()[:32]


def _mac(key: str, purpose: str, ts: str, user: str, body: bytes) -> str:
    msg = _TAG + f"{purpose}\n{ts}\n{user}\n".encode() + hashlib.sha256(body).digest()
    return hmac.new(key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def sign(app_key: str, purpose: str, user: str, body: bytes, ts: int | None = None) -> dict:
    if not USER_RE.fullmatch(user):
        raise EndUserError("end-user id has characters outside [A-Za-z0-9._:@+-] or is too long")
    t = str(int(time.time()) if ts is None else ts)
    return {HEADER_USER: user, HEADER_TS: t, HEADER_SIG: _mac(app_key, purpose, t, user, body)}


def verify(headers, app_key: str, purpose: str, body: bytes, now: float | None = None) -> str | None:
    """The verified end-user id, or None if the request carries none. Raises EndUserError for any partial, repeated,
    malformed, stale or mis-signed assertion (never treated as 'anonymous': that would let an attacker shed a limit)."""
    got = {n: headers.getlist(n.lower()) if hasattr(headers, "getlist") else
           ([headers[n]] if n in headers else []) for n in (HEADER_USER, HEADER_TS, HEADER_SIG)}
    if not any(got.values()):
        return None
    if any(len(v) != 1 for v in got.values()):
        raise EndUserError("end-user assertion incomplete or repeated")
    user, ts, sig = (got[n][0].strip() for n in (HEADER_USER, HEADER_TS, HEADER_SIG))
    if not USER_RE.fullmatch(user) or not re.fullmatch(r"[0-9]{1,12}", ts) or not re.fullmatch(r"[0-9a-f]{64}", sig):
        raise EndUserError("end-user assertion malformed")
    if abs((time.time() if now is None else now) - int(ts)) > WINDOW_S:
        raise EndUserError("end-user assertion outside the time window")
    if not hmac.compare_digest(sig, _mac(app_key, purpose, ts, user, body)):
        raise EndUserError("end-user assertion signature invalid")
    return user
