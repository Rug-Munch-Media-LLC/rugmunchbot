"""Scam-watch config — env switches, channel registry, dialog heuristics."""

from __future__ import annotations

import os
from enum import StrEnum

from rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)


logger = get_logger("rmi_bot.channels.scam_watch")


class ScamSourceType(StrEnum):
    """Finer source classification for scam/shill channels (watch signals).

    ``NewsArticle.category`` stays ``"scam_shill"`` for every item; the
    subtype is carried on the source dict (registry / discovery) so the
    scanner feed can attribute a shill to its channel archetype.
    """

    SHILL_CHANNEL = "shill_channel"
    PUMP_GROUP = "pump_group"
    ALPHA_RUG_SHILL = "alpha_rug_shill"
    FAKE_AIRDROP = "fake_airdrop"
    SEED_PHRASE_SCAM = "seed_phrase_scam"
    SCAM_TOKEN_CHANNEL = "scam_token_channel"  # noqa: S105 — enum value, not a credential


_DIALOG_HEURISTICS: tuple[tuple[ScamSourceType, tuple[str, ...]], ...] = (
    (
        ScamSourceType.SEED_PHRASE_SCAM,
        (
            "seed phrase",
            "private key",
            "wallet drain",
            "wallet recovery",
            "12 words",
            "recover wallet",
            "sync wallet",
        ),
    ),
    (
        ScamSourceType.FAKE_AIRDROP,
        (
            "airdrop",
            "presale",
            "giveaway",
            "free coins",
            "free tokens",
            "claim bonus",
            "whitelist spot",
        ),
    ),
    (
        ScamSourceType.PUMP_GROUP,
        ("pump", "pnd", "pump and dump", "pump group", "pump signal", "pumps"),
    ),
    (ScamSourceType.SHILL_CHANNEL, ("shill", "shiller", "shillx", "promo")),
    (
        ScamSourceType.ALPHA_RUG_SHILL,
        ("alpha", "100x", "1000x", "moon", "mooner", "gem", "calls", "signals"),
    ),
    (ScamSourceType.SCAM_TOKEN_CHANNEL, ("rug", "honeypot", "scam token", "scam coin")),
)


_OWN_EXCLUDE_KEYWORDS: tuple[str, ...] = ("rugmunch", "rugmunchbot", "cryptorugmunch")


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def re_split(value: str) -> list[str]:
    """Split an env list on commas, pipes, or whitespace."""
    return [p for p in value.replace(",", "|").replace(" ", "|").split("|") if p]


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return list(default)
    return [p.strip().lstrip("@") for p in re_split(raw) if p.strip()]


SCAM_WATCH_ENABLED = _env_bool("RMI_SCAM_WATCH_ENABLED", True)


SCAM_WATCH_INTERVAL_MINUTES = _env_int("RMI_SCAM_WATCH_INTERVAL_MINUTES", 60)


SCAM_WATCH_SWEEP_HOURS = _env_int("RMI_SCAM_WATCH_SWEEP_HOURS", 6)


SCAM_WATCH_PER_CHANNEL_LIMIT = _env_int("RMI_SCAM_WATCH_PER_CHANNEL_LIMIT", 30)


SCAM_WATCH_MAX_DISCOVERED = _env_int("RMI_SCAM_WATCH_MAX_DISCOVERED", 50)


SCAM_WATCH_SCAN_LIMIT = _env_int("RMI_SCAM_WATCH_SCAN_LIMIT", 5)


SCAM_WATCH_QUEUE_LIMIT = _env_int("RMI_SCAM_WATCH_QUEUE_LIMIT", 50)


SCAM_WATCH_ENQUEUE = _env_bool("RMI_SCAM_WATCH_ENQUEUE", True)


SCAM_WATCH_INCLUDE_WATCHERS = _env_bool("RMI_SCAM_WATCH_INCLUDE_WATCHERS", False)


_DEFAULT_SCAM_CHANNELS: tuple[str, ...] = (
    # Security watchers (scan tokens they flag)
    "CertiKAlert",
    "PeckShieldAlert",
    "ScamSniffer",
    "SlowMist",
    "zachxbt",
    "cryptobanter",
    # Call/signal/alpha channels that shill tokens — auto-scan every address
    "Caesars_Calls",  # 40k members — call channel
    "PowsGemCalls",  # 113k members — gem calls
    "renzosalpha",  # 83k members — alpha calls
    "unfolded",  # 119k members — call channel
    "mad_apes_gambles",  # 101k members — gamble calls
    "solearlytrending",  # 109k members — early trending
    "lyxedegen",  # 43k members — degen calls
    "CatfishcallsbyPoe",  # 36k members — call channel
    "ramcalls",  # 42k members — gem calls
    "LevisAlpha",  # 32k members — alpha calls
    "masonsalpha",  # 27k members — alpha calls
    "Bossy_Callz",  # 14k members — call channel
    "eunicalls",  # 29k members — calls
    "DarkyCalls",  # 8k members — calls
    "ALSTEIN_GEMCLUB",  # 10k members — gem club
    "x100xcallz",  # 24k members — 100x calls
    "x666calls",  # 18k members — calls
    "soltrenchtrending",  # 13k members — trench trending
    "solana100xhunt",  # 40k members — low cap gambles
    "cryptoprof93",  # 8k members — crypto professor calls
    "CarnagecallsGambles",  # 7k members — gamble calls
    "wulfcryptocalls",  # wolf crypto calls — low cap shills
    "WolfsCryptoCallz",  # wolf crypto calls alt handle
    "Shahlito",  # shahlito calls — scam shills
    "WatcherGuru",  # crypto news + shills aggregator
    "SpidersJournal",  # spyder's journal — calls/signals
    "WimarX",  # ardizor scam network — call channel
    "danny_crypton",  # ardizor scam network — call channel (also in news sources)
    "ardizoralpha",  # ardizor alpha — ardizor scam network calls
    "ardizor",  # ardizor main — ardizor scam network
    "solsticesmoonshots",  # moon shot calls
    "erics_calls",  # eric's call channel — scam shills
    "CryptoFrogsGems",  # crypto frogs gem calls — scam shills
    "crypto_memes",  # crypto memes — meme coin shills
    "DiorAlpha",  # Dior's Alpha calls
    "ShmooCalls",  # Shmoo's Calls
    "marcellcooks",  # 45k members — trench calls
    "MiladyCalls",  # 13k members — degen calls
    "Hellokook",  # 16k members — kook calls
    "NoahhCalls",  # 6k members — alpha group
    "SugarCalls",  # 34k members — calls
    "sugarydick",  # sugar calls — scam shill channel
    "WulfCalls",  # 29k members — calls
    "YenniDegen",  # 31k members — degen
    "multichaintrending",  # 33k members — trending events
    "SoITeam100x",  # 3k members — 100x calls
    "autosnipeai",  # 3k members — sniper lounge
    "DogenCalls",  # DOGEN'S CALLS
    "LegionCallz",  # LEGION calls
    "SolbixSignal",  # 24k members — bot signals
    "MajorSOLTrending",  # 55k members — SOL trending
    "Meraki",  # 23k members
    "MrpunkGamble",  # 8k members
    "PhanesHub",  # 49k members
)


SCAM_CHANNELS = _env_list("RMI_SCAM_CHANNELS", list(_DEFAULT_SCAM_CHANNELS))


SCAM_WATCH_EXCLUDE = _env_list("RMI_SCAM_WATCH_EXCLUDE", [])
