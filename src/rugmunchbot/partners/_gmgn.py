"""GMGN affiliate URL — the ONE builder for every GMGN link we ship.

GMGN attributes traffic through the token slug, not a query string: a
shareable token URL is ``https://gmgn.ai/<chain>/token/<code>_<contract>``.
The home page carries ``?chain=<slug>&ref=<code>`` instead. Both shapes are
built here so no surface (footer, buttons, partner strip) can drift into a
``?ref=`` token link that credits nobody.

Code resolution: ``RMI_REF_GMGN_CODE`` (slug) wins; a full https URL in
``RMI_REF_GMGN_URL`` (or ``RMI_REF_GMGN``) overrides both and is returned
verbatim.
"""

from __future__ import annotations

import os
from urllib.parse import quote

#: Public marketing code — not a credential.
DEFAULT_GMGN_CODE = "rugmunch"

GMGN_BASE = "https://gmgn.ai"

#: Our chain name → GMGN route slug (GMGN uses short routes, not chain names).
GMGN_CHAIN_SLUGS: dict[str, str] = {
    "solana": "sol",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "avalanche": "avax",
    "fantom": "fantom",
    "tron": "tron",
}


def gmgn_slug(chain: str) -> str:
    """GMGN route slug for a chain (unknown chains pass through lowercased)."""
    key = (chain or "").strip().lower()
    return GMGN_CHAIN_SLUGS.get(key, key or "sol")


def _override_url() -> str:
    for var in ("RMI_REF_GMGN_URL", "RMI_REF_GMGN"):
        raw = (os.environ.get(var) or "").strip()
        if raw.startswith("https://"):
            return raw
    return ""


def _code() -> str:
    explicit = (os.environ.get("RMI_REF_GMGN_CODE") or "").strip()
    if explicit:
        return explicit
    legacy = (os.environ.get("RMI_REF_GMGN") or "").strip()
    if legacy and not legacy.startswith("https://"):
        return legacy
    return DEFAULT_GMGN_CODE


def gmgn_url(*, token: str = "", chain: str = "") -> str:
    """GMGN link for one surface: token page when *token* is set, else home."""
    override = _override_url()
    if override:
        return override
    slug = gmgn_slug(chain)
    contract = (token or "").strip()
    if not contract:
        return f"{GMGN_BASE}/?chain={slug}&ref={_code()}"
    return f"{GMGN_BASE}/{slug}/token/{_code()}_{quote(contract, safe='')}"


__all__ = ["DEFAULT_GMGN_CODE", "GMGN_BASE", "GMGN_CHAIN_SLUGS", "gmgn_slug", "gmgn_url"]
