"""URL extraction and allow/block matching (pure, no I/O)."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s<>\"')]+|(?<![\w.])t\.me/[^\s<>\"')]+", re.IGNORECASE)
INVITE_RE = re.compile(r"(t\.me|telegram\.me)/(\+|joinchat/)", re.IGNORECASE)


def norm(text: str) -> str:
    """Fold unicode look-alikes and collapse whitespace before matching."""
    folded = unicodedata.normalize("NFKD", text or "")
    ascii_only = folded.encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def extract_urls(text: str, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    """URLs visible in the text plus entity targets (hidden ``text_link``)."""
    urls = list(_URL_RE.findall(text or ""))
    urls.extend(extra)
    return tuple(dict.fromkeys(u.rstrip(".,;:!?") for u in urls if u))


def host(url: str) -> str:
    raw = url if "://" in url else f"https://{url}"
    found = (urlparse(raw).hostname or "").lower()
    return found[4:] if found.startswith("www.") else found


@lru_cache(maxsize=64)
def patterns(promos: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in promos)


def link_allowed(url: str, allow: tuple[str, ...]) -> bool:
    """Allowlist entries are either a domain or ``domain/prefix``."""
    found, path = host(url), (urlparse(url).path or "").lower()
    for entry in allow:
        if "/" in entry:
            allowed_host, _, prefix = entry.partition("/")
            if found.endswith(allowed_host) and path.startswith(f"/{prefix}"):
                return True
        elif found == entry or found.endswith(f".{entry}"):
            return True
    return False


def scam_needle(url: str, scam_domains: tuple[str, ...]) -> str | None:
    blob = f"{host(url)} {url}".lower()
    for needle in scam_domains:
        if needle and needle in blob:
            return needle
    return None


__all__ = [
    "INVITE_RE",
    "extract_urls",
    "host",
    "link_allowed",
    "norm",
    "patterns",
    "scam_needle",
]
