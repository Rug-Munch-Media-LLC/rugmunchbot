"""GMGN + Jupiter canonical link contracts (2026-10-24 monetization pass).

GMGN credits a referral through the token SLUG (`<code>_<contract>`), so a
`?ref=` token link is a fake link and must never be emitted. Jupiter's UI
ignores `?ref=` too — its attribution is the on-chain `referralAccount` plus
50 bps — so the trade link stays plain.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager

import pytest

from rugmunchbot.formatters._base._links import footer_links, gmgn_url
from rugmunchbot.partners import gmgn_slug, referral_for, referral_snapshot
from rugmunchbot.partners._gmgn import gmgn_url as canonical_gmgn
from rugmunchbot.referrals import (
    DISCLOSURE,
    PLATFORM_FEE_BPS,
    jupiter_referral,
    jupiter_swap_params,
)

TOKEN = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
HREF_RE = re.compile(r'href="([^"]+)"')


def test_gmgn_token_link_uses_the_slug_not_a_query_code() -> None:
    url = canonical_gmgn(token=TOKEN, chain="solana")
    assert url == f"https://gmgn.ai/sol/token/rugmunch_{TOKEN}"
    assert "?ref=" not in url


def test_gmgn_home_carries_chain_and_ref() -> None:
    assert canonical_gmgn(chain="base") == "https://gmgn.ai/?chain=base&ref=rugmunch"


@pytest.mark.parametrize(
    ("chain", "slug"),
    [("solana", "sol"), ("ethereum", "eth"), ("base", "base"), ("avalanche", "avax")],
)
def test_gmgn_chain_slugs(chain: str, slug: str) -> None:
    assert gmgn_slug(chain) == slug
    assert (
        canonical_gmgn(token=TOKEN, chain=chain) == f"https://gmgn.ai/{slug}/token/rugmunch_{TOKEN}"
    )


@contextmanager
def _env(**values: str):
    """Scoped env override — keeps the ratchet clean (no monkeypatch fixture)."""
    saved = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def test_gmgn_env_code_override_changes_the_slug() -> None:
    with _env(RMI_REF_GMGN_CODE="altcode"):
        assert canonical_gmgn(token=TOKEN, chain="solana").endswith(f"/altcode_{TOKEN}")


def test_gmgn_full_url_override_wins() -> None:
    with _env(RMI_REF_GMGN_URL="https://gmgn.ai/sol/token/rugmunch_custom"):
        assert canonical_gmgn(token=TOKEN) == "https://gmgn.ai/sol/token/rugmunch_custom"


def test_gmgn_unsupported_chain_is_null_with_reason() -> None:
    ref = referral_for("gmgn", token=TOKEN, chain="solana")  # supported
    assert ref.available
    unknown = referral_for("gmgn", token=TOKEN, chain="unknownchain")
    assert unknown.url is None
    assert unknown.reason == "gmgn:unsupported-chain:unknownchain"


def test_formatter_helper_delegates_to_the_one_builder() -> None:
    assert gmgn_url(TOKEN, "solana") == canonical_gmgn(token=TOKEN, chain="solana")


def test_jupiter_link_is_plain_and_attribution_is_onchain() -> None:
    ref = jupiter_referral(token=TOKEN, chain="solana", purpose="scan")
    assert ref.url == f"https://jup.ag/swap/SOL-{TOKEN}"
    assert "?ref=" not in str(ref.url)
    params = jupiter_swap_params(token=TOKEN)
    assert params["platformFeeBps"] == PLATFORM_FEE_BPS == 50
    assert set(params) == {"referralAccount", "platformFeeBps"}


def test_scan_footer_leads_with_jupiter_and_gmgn_and_caps_refs() -> None:
    footer = footer_links(TOKEN, "solana", purpose="scan")
    refs = [h for h in HREF_RE.findall(footer) if "jup.ag" in h or "gmgn.ai" in h]
    assert refs[0].startswith("https://jup.ag/swap/SOL-")
    assert refs[1].startswith("https://gmgn.ai/sol/token/rugmunch_")
    # Referral links (the two above plus one more) never exceed three.
    assert (
        len(
            [
                h
                for h in HREF_RE.findall(footer)
                if "t.me" not in h
                and "rugmunch.io" not in h
                and "dexscreener" not in h
                and "dextools" not in h
            ]
        )
        <= 3
    )
    assert footer.count(DISCLOSURE) == 1


def test_ref_matrix_reports_every_partner_honestly() -> None:
    rows = {row["platform"]: row for row in referral_snapshot(token=TOKEN, chain="solana")}
    for key in ("jupiter", "gmgn", "photon"):
        assert rows[key]["url"], f"{key} should have a configured link"
    assert rows["polymarket"]["url"] is None
    assert rows["polymarket"]["reason"] == "polymarket:not-configured"
    assert rows["hyperliquid"]["reason"] == "hyperliquid:no-code-stored"
