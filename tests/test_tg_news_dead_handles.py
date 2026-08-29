"""Dead/renamed Telegram handles must not enter the sweep (2026-08-29).

Contract: the curated news list (``_DEFAULT_CHANNELS``) and the scam_watch
list (``_DEFAULT_SCAM_CHANNELS``) must not carry handles Telegram can no
longer resolve ("No user has X as username" / "Nobody is using this
username" / private-and-lacking-permission). Every dead handle previously
cost an MTProto round-trip per sweep tick before the 3-failure auto-disable
cooldown kicked in — and scam_watch's sweep bypassed the cooldown gate
entirely, so its dead channels failed EVERY tick.

Tests:
* ``channel_sources()`` (the tg_news explicit fallback list) carries none of
  the confirmed-dead handles.
* scam_watch's ``SCAM_CHANNELS`` default carries none of the confirmed-dead
  call-channel handles.
* scam_watch's ``sweep_scam_channels`` honors the cooldown gate (a
  cooldowned channel is never read).
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.channels.scam_watch._config import SCAM_CHANNELS
from rugmunchbot.channels.telegram_news._sources._channels import channel_sources

#: Handles probed DEAD on 2026-08-29 (t.me preview shows "Telegram: Contact
#: @X"; MTProto: ResolveUsernameRequest / GetHistoryRequest). Kept here so a
#: future resurrection/rename can be re-enabled deliberately.
DEAD_NEWS_HANDLES = {
    "ambcrypto",
    "bitcoinist",
    "trustnodes",
    "cryptopanic",
    "cryptonewscom",
    "cryptonewsdose",
    "binance_news",
    "kucoinnews",
    "cryptocomnews",
    "okx_announcements",
    "tokeninsight",
    "cryptorank_io",
    "cryptonews",
    "news_btc",
    "blockchainnews",
    "slowmist",
    "ethereumsecurity",
    "fuzzlandalert",
    "beosinalert",
    "openzeppelin",
    "hackenio",
    "arkhamintel",
    "altcoindaily",
    "nansen_ai",
    "chainfeeds",
    "duneanalytics",
    "nerdstaking",
    "uniswap",
    "base",
    "solana",
    "sui",
    "aavegrants",
    "compound",
    "makerdao",
    "blockworks",
    "cryptodaily",
}

DEAD_SCAM_HANDLES = {
    "slowmist",
    "cryptobanter",
    "wolfscryptocallz",
    "dioralpha",
    "sugarcalls",
    "wulfcalls",
    "yennidegen",
}


def test_curated_news_list_has_no_dead_handles():
    """The tg_news explicit fallback list is free of confirmed-dead handles."""
    sources = channel_sources()
    active = {str(s["username"]).strip().lstrip("@").lower() for s in sources}
    leaked = sorted(active & DEAD_NEWS_HANDLES)
    assert not leaked, f"dead news handles still swept: {leaked}"


def test_scam_watch_list_has_no_dead_handles():
    """scam_watch's default call-channel list is free of confirmed-dead handles."""
    active = {str(u).strip().lstrip("@").lower() for u in SCAM_CHANNELS}
    leaked = sorted(active & DEAD_SCAM_HANDLES)
    assert not leaked, f"dead scam handles still swept: {leaked}"


class _FakeClient:
    """Minimal client exposing ``iter_messages``; records what was read."""

    def __init__(self) -> None:
        self.read: list[str] = []

    async def iter_messages(self, entity: Any, limit: int = 50):
        self.read.append(str(entity).strip().lstrip("@").lower())
        if False:  # pragma: no cover — make this an async generator
            yield None


@pytest.mark.asyncio
async def test_scam_watch_sweep_honors_cooldown_gate(monkeypatch):
    """A cooldowned channel is skipped — the scam_watch sweep never reads it."""
    from rugmunchbot.channels import scam_watch as sw_mod
    from rugmunchbot.channels.scam_watch import _discover as sw_discover

    dead = "wulfcalls"  # would otherwise be read every tick
    alive = "Caesars_Calls"

    # Cooldown the dead handle: the gate must skip it without an RPC.
    monkeypatch.setattr(
        sw_discover,
        "is_channel_cooldown",
        lambda username: str(username).strip().lstrip("@").lower() == dead,
    )
    # Force the explicit source list so no discovery is needed.
    monkeypatch.setattr(sw_discover, "SCAM_CHANNELS", [dead, alive])
    monkeypatch.setattr(sw_mod, "SCAM_WATCH_MAX_DISCOVERED", 0)
    monkeypatch.setattr(sw_mod, "SCAM_WATCH_ENABLED", True)

    client = _FakeClient()
    articles = await sw_discover.sweep_scam_channels(client=client)

    assert articles == []
    assert dead not in client.read, "cooldowned dead channel was still read"
    assert alive.lower() in client.read, "live channel should still be swept"
