"""Webhook security — URL validation + SSRF guards (DNS-rebinding safe).

Extracted verbatim from the former single-module ``handlers/webhook.py``.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

WEBHOOK_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)

# SSRF guard: network ranges the bot must never POST to (internal services).
_BLOCKED_IP_NETS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _is_blocked_address(addr: ipaddress._BaseAddress) -> bool:
    """True when addr is internal/private/link-local/loopback/multicast/unspecified."""
    if addr.is_multicast or addr.is_unspecified:  # type: ignore[attr-defined]  # PTB None-able fields
        return True
    return any(addr in net for net in _BLOCKED_IP_NETS)


def _validate_webhook_url(url: str) -> tuple[bool, str]:
    """Format-level validation: scheme, hostname, no embedded credentials.

    Returns (ok, error_message).
    """
    if not WEBHOOK_URL_RE.match(url):
        return False, "Invalid URL. Must be a valid http:// or https:// URL."

    try:
        parsed = urlsplit(url)
    except ValueError:
        return False, "Invalid URL. Could not parse it."

    if parsed.scheme not in ("http", "https"):
        return False, "Invalid URL. Only http:// and https:// are allowed."

    if parsed.hostname is None:
        return False, "Invalid URL. Missing hostname."

    if parsed.username or parsed.password:
        return False, "Invalid URL. URLs with embedded credentials (@user:pass) are not allowed."

    return True, ""


def _assert_safe_target(url: str) -> None:
    """Resolve the hostname and reject internal/private targets.

    Call immediately before making the request so DNS resolution happens
    right before the POST (prevents DNS-rebinding TOCTOU). Raises ValueError
    with a user-facing message when the target is unsafe.
    """
    ok, err = _validate_webhook_url(url)
    if not ok:
        raise ValueError(err)

    hostname = urlsplit(url).hostname

    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # User-facing message replaces the raw resolver error (no chain).
        raise ValueError(f"Could not resolve host: {hostname}") from None

    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if _is_blocked_address(addr):
            raise ValueError(
                f"URL host {hostname} resolves to {addr}, which is a blocked "
                "internal/private address. Webhook targets must be public."
            )
