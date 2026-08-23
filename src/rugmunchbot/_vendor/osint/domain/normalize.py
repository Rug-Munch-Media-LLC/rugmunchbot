# VENDORED 2026-08-23 from rmi-backend/app/osint/domain/normalize.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Domain normalization — validate and canonicalize a user-supplied domain.

Strips scheme, path, query, fragment, port, and trailing dot; lowercases;
IDNA-encodes non-ASCII labels. Raises :class:`ValueError` on garbage.
"""

from __future__ import annotations

import ipaddress
import re

#: Domain pattern: labels of 1-63 chars, at least two labels, TLD of 2-63
#: letters, total length <= 253 (RFC 1035/1123, applied post-IDNA).
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalize_domain(value: str) -> str:
    """Normalize and validate a user-supplied domain.

    Strips scheme, path, query, fragment, port, and trailing dot; lowercases;
    IDNA-encodes non-ASCII labels. Raises :class:`ValueError` on garbage.

    Examples:
        ``"https://Scam-Example.COM/order?id=1"`` -> ``"scam-example.com"``
        ``"192.168.0.1"`` or ``"not a domain"`` -> :class:`ValueError`
    """
    raw = value.strip()
    if not raw:
        raise ValueError("empty domain")
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    for sep in ("/", "?", "#"):
        raw = raw.split(sep, 1)[0]
    if raw.count(":") == 1 and raw.rsplit(":", 1)[1].isdigit():
        raw = raw.rsplit(":", 1)[0]
    raw = raw.strip().rstrip(".")

    try:
        ipaddress.ip_address(raw)
    except ValueError:
        pass
    else:
        raise ValueError(f"expected a domain name, got an IP address: {raw!r}")

    try:
        ascii_domain = raw.lower().encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid domain name: {raw!r}") from exc

    if not _DOMAIN_RE.match(ascii_domain) or len(ascii_domain) > 253:
        raise ValueError(f"invalid domain name: {raw!r}")
    return ascii_domain
