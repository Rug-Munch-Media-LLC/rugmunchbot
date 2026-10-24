"""Partner referral catalog — links come from config, never invented.

``DEX_REF_LINKS`` (and the ``referrals`` package for Jupiter/Polymarket) is
the single source of truth for every URL and code. This module only adds
partner-level facts (display name, per-lane priority lives in ``_lanes``) and
turns a key into a ``Referral`` — ``url=None`` + ``reason`` when a real link
cannot be produced, so callers never fabricate one.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote

from rugmunchbot.config import CHAIN_IDS, DEX_REF_LINKS
from rugmunchbot.referrals import (
    STANDARD,
    Referral,
    jupiter_referral,
    polymarket_referral,
)

from ._gmgn import gmgn_url

#: Venues whose URL/code lives in ``config.DEX_REF_LINKS``.
DEX_PARTNERS: tuple[str, ...] = (
    "jupiter",
    "gmgn",
    "photon",
    "banana",
    "odin",
    "axiom",
    "padre",
)

#: Referrers with no link stored in this repo — reported as null + reason.
MANUAL_PARTNERS: dict[str, str] = {
    "hyperliquid": "Hyperliquid",
    "polymarket": "Polymarket",
}

_CODE_PARAM_RE = re.compile(r"([?&])(ref|start)=[^&]+")
_CODE_VALUE_RE = re.compile(r"(ref|start)=[^&]+")


def display_name(key: str) -> str:
    """Human label for a partner key (falls back to a title-cased key)."""
    dex = DEX_REF_LINKS.get(key)
    if dex:
        return str(dex["name"])
    return MANUAL_PARTNERS.get(key, (key or "partner").title())


def _apply_env_code(key: str, url: str) -> str:
    """Apply ``RMI_REF_<KEY>``: a code replaces the param, empty drops it."""
    raw = os.environ.get(f"RMI_REF_{key.upper()}")
    if raw is None:
        return url
    code = raw.strip()
    if not code:
        return _CODE_PARAM_RE.sub("", url)
    return _CODE_VALUE_RE.sub(lambda m: f"{m.group(1)}={quote(code, safe='@')}", url)


def _gmgn_referral(token: str, chain: str) -> Referral:
    """GMGN link via the canonical builder (token slug carries the code)."""
    dex = DEX_REF_LINKS["gmgn"]
    chains = [str(c).lower() for c in dex["chains"]]
    if chain and chain not in chains:
        return Referral("gmgn", STANDARD, None, None, f"gmgn:unsupported-chain:{chain}")
    url = gmgn_url(token=token, chain=chain or "solana")
    if not url.startswith("https://"):
        return Referral("gmgn", STANDARD, None, None, "gmgn:invalid-url")
    return Referral("gmgn", STANDARD, url, None)


def _dex_referral(key: str, token: str, chain: str) -> Referral:
    """Referral from a ``DEX_REF_LINKS`` template, or null + reason."""
    dex = DEX_REF_LINKS.get(key)
    if dex is None:
        return Referral(key, STANDARD, None, None, "partner:unknown")
    chains = [str(c).lower() for c in dex["chains"]]
    if chain and chain not in chains:
        return Referral(key, STANDARD, None, None, f"{key}:unsupported-chain:{chain}")
    template = str(dex["url"])
    if "{token}" in template and not token:
        home = str(dex.get("home") or "")
        if home.startswith("https://"):
            return Referral(key, STANDARD, _apply_env_code(key, home), None)
        return Referral(key, STANDARD, None, None, f"{key}:no-token")
    url = template.format(
        token=token,
        chain=chain or (chains[0] if chains else "solana"),
        chain_id=CHAIN_IDS.get(chain, ""),
    )
    url = _apply_env_code(key, url)
    if not url.startswith("https://"):
        return Referral(key, STANDARD, None, None, f"{key}:invalid-url")
    return Referral(key, STANDARD, url, None)


def referral_for(key: str, *, token: str = "", chain: str = "") -> Referral:
    """Resolve one partner key to a ``Referral`` (null + reason when unknown)."""
    key = (key or "").strip().lower()
    chain_l = (chain or "").strip().lower()
    token_s = (token or "").strip()
    if key == "jupiter":
        return jupiter_referral(token=token_s, chain=chain_l or "solana", purpose="content")
    if key == "polymarket":
        return polymarket_referral()
    if key == "hyperliquid":
        return Referral("hyperliquid", STANDARD, None, None, "hyperliquid:no-code-stored")
    if key == "gmgn":
        return _gmgn_referral(token_s, chain_l)
    return _dex_referral(key, token_s, chain_l)


def referral_snapshot(*, token: str = "", chain: str = "") -> list[dict[str, str | None]]:
    """Every partner as JSON-safe data — unavailable rows keep null + reason."""
    return [
        {**referral_for(key, token=token, chain=chain).as_dict(), "name": display_name(key)}
        for key in (*DEX_PARTNERS, *MANUAL_PARTNERS)
    ]
