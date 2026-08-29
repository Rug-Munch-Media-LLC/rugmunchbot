"""SSRF guard tests for story_ingest._validate_url.

The bot must never fetch private/loopback/link-local/reserved hosts —
internal infrastructure (backend, metrics, gopass hosts) and cloud
metadata endpoints (169.254.169.254) are off-limits for a consumer bot.
"""

import socket

import pytest

from rugmunchbot._vendor.rag_pkg.story_ingest import (
    StoryFetchError,
    _host_is_private,
    _is_private_ip,
    _validate_url,
)

# ── IP literal classification ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.8.8.8",
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "169.254.0.1",
        "100.64.0.1",  # CGNAT (RFC 6598 doc range — gitleaks:allow)
        "100.127.255.254",
        "0.0.0.0",  # noqa: S104 — test string, not a bind
        "192.0.2.1",  # documentation
        "198.51.100.1",
        "203.0.113.1",
        "224.0.0.1",  # multicast
        "240.0.0.1",  # reserved
        "::1",
        "::",
        "fc00::1",  # ULA
        "fe80::1",  # link-local
        "2001:db8::1",  # documentation
        "::ffff:10.0.0.1",  # v4-mapped private
    ],
)
def test_private_ips_flagged(ip: str) -> None:
    assert _is_private_ip(ip) is True


@pytest.mark.parametrize(
    "ip",
    [
        "8.8.8.8",
        "1.1.1.1",
        "104.16.132.229",
        "2606:4700::1111",
        "2001:4860:4860::8888",
    ],
)
def test_public_ips_allowed(ip: str) -> None:
    assert _is_private_ip(ip) is False


# ── Host classification (literal + DNS) ────────────────────────────────────


def test_host_literal_private() -> None:
    assert _host_is_private("127.0.0.1") is True
    assert _host_is_private("169.254.169.254") is True
    assert _host_is_private("8.8.8.8") is False


def test_host_resolves_to_private(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, _port: int, proto: int = socket.IPPROTO_TCP) -> list:
        if host == "internal.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]
        if host == "public.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        raise OSError("no address")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert _host_is_private("internal.example.com") is True
    assert _host_is_private("public.example.com") is False


def test_host_unresolvable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, _port: int, proto: int = socket.IPPROTO_TCP) -> list:
        raise OSError("no address")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert _host_is_private("does-not-exist.invalid") is True


# ── _validate_url end-to-end ───────────────────────────────────────────────


def test_validate_rejects_private_literal() -> None:
    with pytest.raises(StoryFetchError, match="private/internal"):
        _validate_url("http://127.0.0.1:9191/metrics")
    with pytest.raises(StoryFetchError, match="private/internal"):
        _validate_url("https://169.254.169.254/latest/meta-data/")


def test_validate_rejects_private_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, _port: int, proto: int = socket.IPPROTO_TCP) -> list:
        if host == "talos.internal":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))]
        raise OSError("no address")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(StoryFetchError, match="private/internal"):
        _validate_url("http://talos.internal:8000/admin")


def test_validate_accepts_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, _port: int, proto: int = socket.IPPROTO_TCP) -> list:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert _validate_url("https://example.com/article") == "https://example.com/article"


def test_validate_keeps_existing_checks() -> None:
    with pytest.raises(StoryFetchError, match="invalid URL"):
        _validate_url("")
    with pytest.raises(StoryFetchError, match="only http/https"):
        _validate_url("file:///etc/passwd")
    with pytest.raises(StoryFetchError, match="embedded credentials"):
        _validate_url("https://user:pass@example.com/")
