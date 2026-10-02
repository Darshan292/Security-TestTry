import pytest

from pgw.gateway.settings import Settings, check_destination, parse_allowlist

AL = ("api.anthropic.com",)


@pytest.mark.parametrize("url", [
    "https://api.anthropic.com",
    "https://api.anthropic.com/v1/messages",
    "https://api.anthropic.com:443/v1",
    "https://API.Anthropic.COM/v1",           # host is case-insensitive
])
def test_allowed(url):
    check_destination(url, AL)


@pytest.mark.parametrize("url", [
    "https://api.anthropic.com.evil.com",
    "https://api.anthropic.com.evil.com/v1",
    "https://evil.com/api.anthropic.com",
    "https://evil.com?x=api.anthropic.com",
    "https://evil.com#@api.anthropic.com",
    "https://user@api.anthropic.com",
    "https://user:pw@api.anthropic.com",
    "https://api.anthropic.com@evil.com",
    "https://evil.com@api.anthropic.com",
    "http://api.anthropic.com",
    "ftp://api.anthropic.com",
    "//api.anthropic.com",
    "api.anthropic.com",
    "https://api.anthropic.com.",             # trailing dot: not an exact match
    "https://API.ANTHROPIC.COM.",
    "https://xapi.anthropic.com",
    "https://anthropic.com",
    "https://evilapi.anthropic.com",
    "https://api.anthropic.com:8443",         # non-443 port, not listed
    "https://api.anthropic.com:80",
    "https://api.anthropic.com:abc",
    "https://1.2.3.4",
    "https://[::1]",
    "https://api.anthropic.com\\@evil.com",
    "https://api.anthropic.com\n.evil.com",
    "https://api.anthropic.com evil.com",
    "",
])
def test_rejected(url):
    with pytest.raises(ValueError):
        check_destination(url, AL)


def test_wildcards_and_suffixes_never_match():
    for entry in ("*.anthropic.com", "*", ".anthropic.com", "anthropic.com"):
        with pytest.raises(ValueError):
            check_destination("https://api.anthropic.com", (entry,))
    with pytest.raises(ValueError):
        check_destination("https://*.anthropic.com", ("*.anthropic.com",))


def test_empty_allowlist_denies_everything():
    with pytest.raises(ValueError):
        check_destination("https://api.anthropic.com", ())


def test_port_only_if_entry_includes_it():
    check_destination("https://gw.internal:8443/x", ("gw.internal:8443",))
    with pytest.raises(ValueError):
        check_destination("https://gw.internal", ("gw.internal:8443",))
    with pytest.raises(ValueError):
        check_destination("https://gw.internal:9443", ("gw.internal:8443",))
    with pytest.raises(ValueError):
        check_destination("https://gw.internal:8443", ("gw.internal",))


def test_ip_literal_only_if_listed():
    check_destination("https://10.0.0.5/x", ("10.0.0.5",))
    with pytest.raises(ValueError):
        check_destination("https://10.0.0.6", ("10.0.0.5",))


def test_settings_field_default_and_env():
    assert Settings().egress_allowlist == ("api.anthropic.com",)
    assert Settings.from_env({}).egress_allowlist == ("api.anthropic.com",)
    s = Settings.from_env({"PGW_EGRESS_ALLOWLIST": " API.openai.com , api.anthropic.com ,,"})
    assert s.egress_allowlist == ("api.openai.com", "api.anthropic.com")
    assert parse_allowlist("") == ("api.anthropic.com",)
    check_destination("https://api.openai.com/v1", s.egress_allowlist)
