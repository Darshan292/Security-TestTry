"""Plugin slots (docs/PLUGINS.md): adapters on spec vectors, profile validation, shadow never changes output, union
adds masking (the egress check still runs), errors per mode, no network or file writes, bounded cost.
Synthetic data only; vectors come from the schemes' specifications and networks' published test numbers."""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

pytest.importorskip("stdnum")
pytest.importorskip("phonenumbers")
pytest.importorskip("detect_secrets")

from pgw.gateway import app as gw_app  # noqa: E402
from pgw.gateway.app import create_app  # noqa: E402
from pgw.gateway.settings import ProfileError, Settings, parse_profile  # noqa: E402
from pgw.gateway.upstream import MockLLMUpstream  # noqa: E402
from pgw.pipeline import Keys, Protector  # noqa: E402
from pgw.plugins import PluginConfigError, PluginError, Verdict, build, parse_plugins  # noqa: E402
from pgw.plugins.adapters import (BuiltinValidator, DetectSecretsRecognizer, PhonenumbersValidator,  # noqa: E402
                                  StdnumValidator)
from pgw.plugins.candidates import MAX_CANDIDATES, candidates  # noqa: E402
from pgw.policy import PolicyPack  # noqa: E402
from tests.sealed import attest, production  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
V, I, U = Verdict.VALID, Verdict.INVALID, Verdict.UNKNOWN
KEY = "plugin-app-key"
MAILCHIMP = "0123456789abcdef" * 2 + "-" + "us12"    # built at run time so secret scanners skip the fixture; shape of a Mailchimp API key; the built-in has no rule for it


@pytest.fixture(scope="module")
def adapters():
    return {"builtin": BuiltinValidator(), "stdnum": StdnumValidator(), "phonenumbers": PhonenumbersValidator(),
            "ds": DetectSecretsRecognizer()}


# --- adapters on spec vectors ---------------------------------------------------------------------------------------

def _iban(country: str, bban: str) -> str:
    """ISO 13616 / ISO 7064 MOD 97-10 check digits computed from the standard's formula (not from either validator)."""
    n = int("".join(str(int(c, 36)) for c in bban + country + "00"))
    return f"{country}{98 - n % 97:02d}{bban}"


AADHAAR = [("234123412346", V), ("2341 2341 2346", V), ("2341-2341-2346", V), ("234123412347", I),
           ("123412341234", I), ("034123412346", I), ("23412341234", I), ("2341 2341 234X", I)]
PAN = [("ACUPA7085R", V), ("ABCPE1234F", V), ("AAACC1234A", V), ("ABMXA3211G", I), ("ABMPA32111", I),
       ("ACUPA7085RR", I), ("1BCPE1234F", I)]
CARDS_VALID = ["4111111111111111", "4012 8888 8888 1881", "5555555555554444", "2223003122003222", "378282246310005",
               "6011111111111117"]                    # Visa, Mastercard (51-55 and 2-series), Amex, Discover test numbers
CARDS_INVALID = ["4111111111111112", "5555555555554440", "411111111111", "41111111111111111111"]


@pytest.mark.parametrize("value,want", AADHAAR)
def test_aadhaar_vectors_builtin_and_stdnum(adapters, value, want):
    # UIDAI scheme: 12 digits, first digit 2-9, Verhoeff check digit (234123412346: the stdnum/UIDAI example)
    assert adapters["builtin"].validate("AADHAAR", value) is want
    assert adapters["stdnum"].validate("AADHAAR", value) is want


@pytest.mark.parametrize("value,want", PAN)
def test_pan_vectors_builtin_and_stdnum(adapters, value, want):
    # CBDT: AAAAA9999A, 4th character = holder type (P C H F A T B L J G)
    assert adapters["builtin"].validate("PAN", value) is want
    assert adapters["stdnum"].validate("PAN", value) is want


@pytest.mark.parametrize("value", CARDS_VALID)
def test_published_test_cards_are_valid(adapters, value):
    assert adapters["builtin"].validate("CARD", value) is V
    assert adapters["stdnum"].validate("CARD", value) is V


@pytest.mark.parametrize("value", CARDS_INVALID)
def test_bad_luhn_or_length_cards_are_invalid(adapters, value):
    assert adapters["builtin"].validate("CARD", value) is I
    assert adapters["stdnum"].validate("CARD", value) is I


def test_iban_vectors(adapters):
    for v in ("GB82 WEST 1234 5698 7654 32", "DE89 3704 0044 0532 0130 00", _iban("NL", "ABNA0417164300")):
        assert adapters["builtin"].validate("IBAN", v) is V and adapters["stdnum"].validate("IBAN", v) is V, v
    for v in ("GB82 WEST 1234 5698 7654 33", "DE89 3704 0044 0532 0130 0", "XX82WEST12345698765432"):
        assert adapters["builtin"].validate("IBAN", v) is I and adapters["stdnum"].validate("IBAN", v) is I, v


def test_stdnum_only_schemes(adapters):
    s, b = adapters["stdnum"], adapters["builtin"]
    for t, good, bad in (("GSTIN", "27AAPFU0939F1ZV", "27AAPFU0939F1ZO"), ("EPIC", "WKH1186253", "WKH1186263"),
                         ("VID", "2341234123412341", "2341234123412342")):
        assert s.validate(t, good) is V and s.validate(t, bad) is I
        assert b.validate(t, good) is U                 # the built-in has no opinion: never a disagreement
    assert s.validate("IFSC", "SBIN0001234") is U and b.validate("IFSC", "SBIN0001234") is V
    assert b.validate("IFSC", "SBIN1001234") is I       # 5th character must be 0 (RBI)


def test_phone_vectors(adapters):
    p, b = adapters["phonenumbers"], adapters["builtin"]
    for v in ("+91 98765 43210", "9876543210", "(+91) 98765 43210", "022-2345 6789", "+1 650 253 0000"):
        assert p.validate("PHONE", v) is V and b.validate("PHONE", v) is V, v
    for v in ("12345", "+91 98765", "phone"):
        assert p.validate("PHONE", v) is I and b.validate("PHONE", v) is I, v
    assert p.validate("PAN", "ABCPE1234F") is U


def test_detect_secrets_spans_cover_the_whole_secret(adapters):
    ds = adapters["ds"]
    text = f"aws AKIAIOSFODNN7EXAMPLE and mailchimp {MAILCHIMP} and gh ghp_{'a1B2' * 9} end"
    found = {text[s:e] for s, e in ds.find(text)}
    assert "AKIAIOSFODNN7EXAMPLE" in found and MAILCHIMP in found
    assert any(f.startswith("ghp_") and len(f) == 40 for f in found)   # not just the reported "ghp" group
    assert ds.find("Please summarise the leave policy for March.") == []
    assert "IPPublicDetector" not in ds.plugin_names


# --- spec disagreements (scripts/compare_validators.py; docs/PLUGINS.md "Findings") ----------------------------------

@pytest.mark.xfail(strict=True, reason="built-in CARD issuer-prefix list (recognizers._iin_ok) omits issued ranges: "
                   "JCB 3528-3589, UnionPay 62, Diners 300-305, Discover 644-649, Maestro 67 (ISO/IEC 7812 IINs). "
                   "Luhn-valid cards of those networks are not recognised as CARD. Not changed silently: a fix "
                   "trades precision (Luhn-valid account numbers) and needs a measured decision")
@pytest.mark.parametrize("value", ["3530111333300000", "6200000000000005", "30569309025904", "6445644564456445",
                                   "6759649826438453"])
def test_builtin_card_accepts_every_issued_network(adapters, value):
    assert adapters["builtin"].validate("CARD", value) is V


@pytest.mark.xfail(strict=True, reason="built-in PAN accepts sequential number 0000; CBDT numbers run 0001-9999 "
                   "(stdnum rejects it). Lenient only: it can over-mask, never leak")
def test_builtin_pan_rejects_serial_0000(adapters):
    assert adapters["builtin"].validate("PAN", "ACUPA0000R") is I


@pytest.mark.xfail(strict=True, reason="stdnum accepts PAN holder type K ('Krish (Trust Krish)', marked possibly "
                   "discontinued in stdnum itself); the Income Tax Department's list is P C H F A T B L J G")
def test_stdnum_pan_rejects_holder_type_k(adapters):
    assert adapters["stdnum"].validate("PAN", "AAAKA1234A") is I


@pytest.mark.xfail(strict=True, reason="built-in IBAN checks mod-97 and the country length only, not the country's "
                   "registered BBAN format (SWIFT IBAN registry; stdnum checks it): NL needs 4 letters + 10 digits. "
                   "Lenient only: over-masks, never leaks")
def test_builtin_iban_checks_the_bban_format(adapters):
    assert adapters["builtin"].validate("IBAN", _iban("NL", "1234ABCD567890")) is I


@pytest.mark.xfail(strict=True, reason="built-in PHONE accepts landlines whose subscriber number starts with 0, 1, 8 "
                   "or 9; India's National Numbering Plan starts fixed-line subscriber numbers with 2-6 "
                   "(phonenumbers rejects them). Lenient only: over-masks, never leaks")
def test_builtin_landline_subscriber_first_digit(adapters):
    assert adapters["builtin"].validate("PHONE", "033-9123 4567") is I


def test_documented_disagreement_aadhaar_palindrome(adapters):
    # stdnum rejects Verhoeff-valid palindromes ("Aadhaar cannot be a palindrome"); the built-in accepts them. The
    # rule could not be traced to a UIDAI publication from here, so neither side is marked wrong. For masking the
    # built-in's acceptance is the safe side. Kept as a test so a change on either side is noticed.
    n = "200009900002"
    assert adapters["builtin"].validate("AADHAAR", n) is V and adapters["stdnum"].validate("AADHAAR", n) is I


def test_documented_phonenumbers_is_plan_validation_not_detection(adapters):
    # A date-time fragment is a valid Indian fixed-line number by the numbering plan (area code 20 + 8 digits): a
    # phonenumbers VALID is not evidence of a phone number in prose. The built-in pattern rejects it.
    v = "2026-10-28 14"
    assert adapters["phonenumbers"].validate("PHONE", v) is V and adapters["builtin"].validate("PHONE", v) is I


# --- configuration -----------------------------------------------------------------------------------------------------

def test_parse_plugins_defaults_and_refusals():
    assert parse_plugins(None) == {"detect_secrets": "off", "phonenumbers": "off", "stdnum": "off"}
    assert parse_plugins({"detect_secrets": "union"})["detect_secrets"] == "union"
    for bad, msg in (({"nope": "shadow"}, "unknown plugin"), ({"stdnum": "union"}, "mode must be"),
                     ({"stdnum": "enforce"}, "enforce is not offered"), ({"phonenumbers": False}, "quoted"),
                     ({"detect_secrets": "enforce"}, "mode must be"), (["stdnum"], "mapping")):
        with pytest.raises(PluginConfigError, match=msg):
            parse_plugins(bad)
    assert build(None) is None and build({"stdnum": "off"}) is None


def test_shipped_profile_has_every_plugin_off():
    vals, _ = parse_profile((ROOT / "registry/deployment.yaml").read_bytes())
    assert set(vals["plugins"].values()) == {"off"}


def apps():
    return [{"app_id": "pilot", "tenant": "acme", "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(),
             "restore_output": True, "models": ["mock-1"], "purposes": ["ticket_summary"], "transparency": True,
             "tasks": {"ticket_summary": str(ROOT / "contracts/ticket_summary.yaml")}}]


def gateway(tmp: Path, plugins, master: str | None = None):
    ext = MockLLMUpstream("summarise")
    cfg = production(tmp, apps(), deployment={"plugins": plugins})
    s = Settings(**cfg, ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                 master_key=master or os.urandom(32).hex(), upstream=ext)
    app = create_app(s)
    return TestClient(app), ext, app


def send(client, text, conv: str | None = None):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": text}],
            "pgw_subjects": {"requester": {"name": "Imran Ansari"}}}
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary", **attest("ticket_summary", body)}
    if conv:   # a fixed conversation id fixes the alias scope (otherwise random per request)
        h["X-PGW-Conversation-Id"] = conv
    return client.post("/v1/chat/completions", json=body, headers=h)


@pytest.mark.parametrize("plugins,msg", [({"nope": "shadow"}, "unknown plugin"), ({"stdnum": "union"}, "mode"),
                                         ({"stdnum": "enforce"}, "enforce")])
def test_bad_plugin_profile_stops_production_startup(tmp_path, plugins, msg):
    with pytest.raises(ProfileError, match=msg):
        gateway(tmp_path, plugins)


def test_missing_library_stops_startup(monkeypatch):
    import pgw.plugins.registry as reg

    def boom():
        raise ImportError("no module")
    monkeypatch.setitem(reg.REGISTRY, "stdnum", reg.PluginSpec("stdnum", "validator", boom, "x", "python-stdnum"))
    with pytest.raises(PluginConfigError, match="not installed"):
        build({"stdnum": "shadow"})


# --- gateway behaviour ---------------------------------------------------------------------------------------------

TICKET = ("Please summarise: Imran Ansari (PAN ABCPE1234F, Aadhaar 2341 2341 2346, card 3530111333300000, "
          "mobile +91 98765 43210, landline 033-9123 4567) cannot log in; IT pasted the key AKIAIOSFODNN7EXAMPLE.")
SHADOW_ALL = {"stdnum": "shadow", "phonenumbers": "shadow", "detect_secrets": "shadow"}


def test_shadow_never_changes_what_leaves_or_the_report(tmp_path):
    master = os.urandom(32).hex()
    c_off, ext_off, _ = gateway(tmp_path / "off", None, master)
    c_sh, ext_sh, app = gateway(tmp_path / "sh", SHADOW_ALL, master)
    r_off, r_sh = send(c_off, TICKET, "conv-1"), send(c_sh, TICKET, "conv-1")
    assert r_off.status_code == r_sh.status_code == 200, (r_off.text, r_sh.text)
    assert ext_off.received[-1] == ext_sh.received[-1]                       # byte-identical upstream body
    assert _no_timing(r_off.json()["pgw_report"]) == _no_timing(r_sh.json()["pgw_report"])   # no shadow trace
    snap = app.state.pgw_plugin_metrics.snapshot()
    assert snap["stdnum|disagree|CARD|INVALID|VALID"] >= 1                   # counted (JCB test number)
    assert snap["phonenumbers|disagree|PHONE|VALID|INVALID"] >= 1            # counted (landline 033-9...)
    blob = json.dumps(snap)
    for value in ("ABCPE1234F", "2341", "3530111333300000", "98765", "AKIA"):
        assert value not in blob                                             # counts only, never values


def _no_timing(x):
    if isinstance(x, dict):
        return {k: _no_timing(v) for k, v in x.items() if k not in ("ms", "request_id", "latency_ms")}
    if isinstance(x, list):
        return [_no_timing(v) for v in x]
    return x


def test_shadow_disagreement_log_has_no_values(tmp_path, caplog):
    c, _, _ = gateway(tmp_path, SHADOW_ALL)
    with caplog.at_level("INFO", logger="pgw.plugins"):
        assert send(c, TICKET).status_code == 200
    text = caplog.text
    assert "plugin_shadow" in text
    for value in ("3530111333300000", "9123", "ABCPE1234F", "AKIAIOSFODNN7EXAMPLE"):
        assert value not in text


def test_union_adds_masking_and_the_egress_check_still_runs(tmp_path, monkeypatch):
    text = f"Please summarise: the newsletter tool failed with key {MAILCHIMP} for Imran Ansari."
    c_off, ext_off, _ = gateway(tmp_path / "off", None)
    assert send(c_off, text).status_code == 200
    assert "abcdef" in ext_off.received[-1].decode()                        # built-in alone: pieces of it leave
    seen = []
    real = gw_app.egress_check
    monkeypatch.setattr(gw_app, "egress_check", lambda *a, **k: (seen.append(a), real(*a, **k))[1])
    c_un, ext_un, _ = gateway(tmp_path / "un", {"detect_secrets": "union"})
    r = send(c_un, text)
    assert r.status_code == 200, r.text
    sent = ext_un.received[-1].decode()
    assert "abcdef" not in sent and "us12" not in sent and "0123456789" not in sent
    assert seen, "egress check did not run"
    assert any(MAILCHIMP in json.dumps(a, default=str) for a in seen)        # the added value is in its watch list
    rep = r.json()["pgw_report"]
    assert any(d["type"] == "SECRET" and d["source"] == "detected" for d in rep["detected"])


def test_plugin_errors_per_mode(tmp_path, monkeypatch):
    def broken(self, text):
        raise RuntimeError("boom")
    monkeypatch.setattr(DetectSecretsRecognizer, "find", broken)
    c, ext, app = gateway(tmp_path / "sh", {"detect_secrets": "shadow"})
    assert send(c, TICKET).status_code == 200                               # shadow: counted, request unaffected
    assert app.state.pgw_plugin_metrics.snapshot()["detect_secrets|error|-|-|-"] >= 1
    c2, ext2, _ = gateway(tmp_path / "un", {"detect_secrets": "union"})
    r = send(c2, TICKET)
    assert r.status_code == 503 and r.json()["error"]["type"] == "detector_unavailable"   # union: fail closed
    assert not ext2.received


def test_shadow_validator_error_is_counted_not_raised(monkeypatch):
    ps = build({"stdnum": "shadow"})
    monkeypatch.setattr(StdnumValidator, "validate", lambda self, t, c: 1 / 0)
    assert ps.apply("PAN ABCPE1234F", []) == []
    assert ps.metrics.snapshot()["stdnum|error|-|-|-"] == 1
    ps_u = build({"detect_secrets": "union"})
    monkeypatch.setattr(DetectSecretsRecognizer, "find", lambda self, t: 1 / 0)
    with pytest.raises(PluginError):
        ps_u.apply("x", [])


def test_union_never_releases_a_character_the_builtin_masks():
    pol = PolicyPack.load(ROOT / "policies/hris-default.yaml")
    p0 = Protector(pol, Keys(bytes(32)))
    p1 = Protector(pol, Keys(bytes(32)), plugins=build({"detect_secrets": "union"}))
    texts = [f"aws_secret_access_key=AKIAIOSFODNN7EXAMPLE and Priya Nair's key {MAILCHIMP}",
             f"token {MAILCHIMP}, call +91 98765 43210, PAN ABCPE1234F",
             "header eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl for Imran Ansari",
             "//registry.npmjs.org/:_authToken=npm_AbCdEfGh1234 owner priya.nair@example.com 9876543210"]

    def masked(p, t):
        cov = set()
        for d in p.analyze([{"role": "user", "content": t}], _ctx())[0]:
            cov |= set(range(d.start, d.end))
        return cov
    for t in texts:
        assert masked(p0, t) <= masked(p1, t), t
    t = texts[0]
    assert {t.index(MAILCHIMP) + k for k in range(len(MAILCHIMP))} <= masked(p1, t)   # the whole key, not pieces


def _ctx():
    from pgw.types import Context
    return Context("acme", "pilot", "ticket_summary", restore_output=False)


# --- no I/O, bounded cost ------------------------------------------------------------------------------------------

_NO_IO = r"""
import builtins, io, socket, sys
def deny(*a, **k):
    raise AssertionError("network or file write attempted")
for name in ("create_connection", "getaddrinfo", "gethostbyname"):
    setattr(socket, name, deny)
for name in ("connect", "connect_ex", "sendto", "bind"):
    setattr(socket.socket, name, deny)
real_open = builtins.open
def guarded(file, mode="r", *a, **k):
    if any(c in str(mode) for c in "wax+"):
        deny()
    return real_open(file, mode, *a, **k)
builtins.open = io.open = guarded
from pgw.plugins import build                      # import: no network, no writes
ps = build({"stdnum": "shadow", "phonenumbers": "shadow", "detect_secrets": "union"})
def deny_all(*a, **k):
    raise AssertionError("file opened at call time")
builtins.open = io.open = deny_all                 # call time: not even reads
text = ("PAN ABCPE1234F Aadhaar 2341 2341 2346 card 4111111111111111 GSTIN 27AAPFU0939F1ZV VID 2341234123412341 "
        "IBAN GB82 WEST 1234 5698 7654 32 call +44 20 7946 0000 or +91 98765 43210 key AKIAIOSFODNN7EXAMPLE "
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig //r.npmjs.org/:_authToken=npm_abcdefghij")
spans = ps.apply(text, [])
assert spans, "union found nothing"
assert any(k.endswith("compared|IBAN|-|-") or "IBAN" in k for k in ps.metrics.snapshot())
assert not any("error" in k for k in ps.metrics.snapshot()), ps.metrics.snapshot()
print("ok")
"""


def test_plugins_do_no_network_or_file_io():
    r = subprocess.run([sys.executable, "-c", _NO_IO], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr[-2000:]


def test_candidates_are_capped():
    cands, capped = candidates("PAN ABCPE1234F " * (MAX_CANDIDATES + 10))
    assert capped and len(cands) == MAX_CANDIDATES


def test_adversarial_inputs_stay_fast_with_every_plugin_on():
    from tests.test_dos_bounds import CASES, LIMIT, SIZE, UPPERCASE
    pol = PolicyPack.load(ROOT / "policies/hris-default.yaml")
    ps = build({"stdnum": "shadow", "phonenumbers": "shadow", "detect_secrets": "union"})
    extra = {"uppercase": UPPERCASE, "eyJ_then_dot": lambda n: "eyJ" * (n // 3) + ".",
             "npm_key_lines": lambda n: ("/" * 1000 + "/:_authToken=npm_x\n") * (n // 1020),
             "npm_key_fail": lambda n: "/" * n + "/:_authToken=npm_", "quotes": lambda n: '"' * n}
    for name, gen in {**CASES, **extra}.items():
        text = gen(SIZE)
        t = min(_timed(ps.apply, text) for _ in range(2))
        assert t < LIMIT / 3, (name, t)                 # the plugins' own share of the detect_text budget
    p1 = Protector(pol, Keys(bytes(32)), plugins=ps)
    for name in ("digit_run", "slashes", "dots"):
        assert min(_timed(lambda t: p1.detect_text(t), CASES[name](SIZE)) for _ in range(3)) < LIMIT, name


def _timed(fn, text):
    t = time.perf_counter()
    try:
        fn(text, []) if fn.__name__ == "apply" else fn(text)
    except PluginError:
        pass                                            # e.g. the span cap: fails closed, still bounded in time
    return time.perf_counter() - t
