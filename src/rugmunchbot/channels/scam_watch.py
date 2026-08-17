"""Scam/shill Telegram channel ingestion → scanner/alert intelligence.

Sweeps Telegram channels that shill rugpull tokens (scam-token shill
channels, pump-and-dump groups, "alpha" groups shilling rugs, fake-airdrop /
seed-phrase-scam channels) through the same headless Telethon reader the news
channel uses (``app.domains.news_ingest``), normalizes messages as
``category="scam_shill"`` items, extracts token addresses/tickers (reusing
``extract_entities`` and the canonical EVM/Solana regexes in
``app.core.shared_utils``), and feeds them into the scanner:

1. **Submit-for-scan** — every newly-seen token address is submitted
   (best-effort, in-process) to ``app.domains.scanner.service.scan_token``
   (free tier — the same service ``POST /api/v1/scanner/scan`` triggers),
   bounded per run by ``RMI_SCAM_WATCH_SCAN_LIMIT``.
2. **Scanner queue** — the same addresses are pushed (JSON) to the Redis
   LIST ``bot:scam_watch:scan_queue`` as a durable record the scanner /
   alert pipeline can consume even when the in-process scan is unavailable.
3. **Cross-run dedupe** — addresses are deduped against the Redis SET
   ``bot:scam_watch:seen`` (plus a bounded in-memory cache) so a token
   shilled in one tick is not rescanned on the next.

Source taxonomy (``ScamSourceType``)
------------------------------------
* ``shill_channel``        — known public scam-token shill channels
* ``pump_group``           — pump-and-dump groups / pump-signal channels
* ``alpha_rug_shill``      — "alpha"/calls channels that shill rug tokens
* ``fake_airdrop``         — fake-airdrop / presale / giveaway channels
* ``seed_phrase_scam``     — seed-phrase / wallet-drain / recovery scams
* ``scam_token_channel``   — channels explicitly pushing scam tokens

Sources
-------
1. Explicit env list ``RMI_SCAM_CHANNELS`` (comma- or pipe-separated
   @handles; no secrets). Defaults to the vetted scam/shill handles already
   flagged in the followed-channels registry (``kind=scammer_caller`` /
   ``category=shill_promotions`` — CertiKAlert, PeckShieldAlert, ScamSniffer,
   SlowMist, zachxbt, cryptobanter) so the sweep never starts empty even if
   the registry file is missing. Set the env var to override.
2. The followed-channels registry ``app/databus/tg_channels.json`` —
   entries with ``kind=scammer_caller`` / ``kind=scammer_group`` and
   ``category=shill_promotions`` are picked up automatically (this is the
   registry that already flags e.g. ``cryptobanter`` as ``scam_shiller``).
   Legit scam-*watcher* categories (``scam_alerts`` / ``phishing_alerts``
   — e.g. SlowMist, ScamSniffer, zachxbt) are NOT swept by default; set
   ``RMI_SCAM_WATCH_INCLUDE_WATCHERS=true`` to include them.
3. Auto-discovery — ``app.domains.news_ingest.discovery``
   (``discover_followed_channels``) enumerates the dialogs the headless
   user session follows; channels whose title/username matches shill
   heuristics ("pump", "shill", "call", "alpha", "moon", "100x", ...) are
   flagged as candidates (capped by ``RMI_SCAM_WATCH_MAX_DISCOVERED``).
   Own channels / registry entries are skipped (rugmunch-name exclusion +
   ``RMI_SCAM_WATCH_EXCLUDE`` override).

Schedule
--------
Registered additively in ``channels/scheduler.py``
(``register_channel_jobs``): every ``RMI_SCAM_WATCH_INTERVAL_MINUTES``
(default 60) minutes, gated by ``RMI_SCAM_WATCH_ENABLED`` (default on) and
the master ``RMI_CHANNELS_ENABLED`` switch. Unlike the poster jobs this job
does not need a bot chat id — it feeds the scanner, it does not post.

Everything fails open: a missing headless client, a flaky channel,
discovery failing, Redis being unreachable, or a scanner outage logs and
skips — the JobQueue keeps running.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.core.redis import get_redis_async
from app.core.shared_utils import is_evm_address, is_solana_address
from app.domains.news_ingest.ingest import dedupe_articles, read_channel_messages
from app.domains.news_ingest.normalize import channel_message_to_article
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from app.domains.news_ingest.models import ChannelMessage, NewsArticle

logger = get_logger("rmi_bot.channels.scam_watch")

# ── Source taxonomy ──────────────────────────────────────────────────────────


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


# Dialog-title heuristics for auto-discovery (first match wins). Keywords are
# deliberately channel-TITLE-level signals — a title containing "pump" or
# "seed phrase" is far more diagnostic than the same word in a message body.
_DIALOG_HEURISTICS: tuple[tuple[ScamSourceType, tuple[str, ...]], ...] = (
    (
        ScamSourceType.SEED_PHRASE_SCAM,
        ("seed phrase", "private key", "wallet drain", "wallet recovery", "12 words", "recover wallet", "sync wallet"),
    ),
    (
        ScamSourceType.FAKE_AIRDROP,
        ("airdrop", "presale", "giveaway", "free coins", "free tokens", "claim bonus", "whitelist spot"),
    ),
    (ScamSourceType.PUMP_GROUP, ("pump", "pnd", "pump and dump", "pump group", "pump signal", "pumps")),
    (ScamSourceType.SHILL_CHANNEL, ("shill", "shiller", "shillx", "promo")),
    (ScamSourceType.ALPHA_RUG_SHILL, ("alpha", "100x", "1000x", "moon", "mooner", "gem", "calls", "signals")),
    (ScamSourceType.SCAM_TOKEN_CHANNEL, ("rug", "honeypot", "scam token", "scam coin")),
)

# Own channels must never be flagged by discovery. The user session follows
# our own news/alerts/alpha/scans channels — their titles contain the bot
# name, and "alpha" alone would otherwise false-positive the heuristic.
_OWN_EXCLUDE_KEYWORDS: tuple[str, ...] = ("rugmunch", "rugmunchbot", "cryptorugmunch")

# ── Env config (self-contained mirrors of telegram_news.py helpers) ─────────


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
SCAM_WATCH_MAX_DISCOVERED = _env_int("RMI_SCAM_WATCH_MAX_DISCOVERED", 20)
SCAM_WATCH_SCAN_LIMIT = _env_int("RMI_SCAM_WATCH_SCAN_LIMIT", 5)
SCAM_WATCH_QUEUE_LIMIT = _env_int("RMI_SCAM_WATCH_QUEUE_LIMIT", 50)
SCAM_WATCH_ENQUEUE = _env_bool("RMI_SCAM_WATCH_ENQUEUE", True)
SCAM_WATCH_INCLUDE_WATCHERS = _env_bool("RMI_SCAM_WATCH_INCLUDE_WATCHERS", False)
#: Explicit @handles to watch (comma/pipe separated). Defaults to the vetted
#: scam/shill handles already flagged in the followed-channels registry
#: (``kind=scammer_caller`` / ``category=shill_promotions``); env overrides.
_DEFAULT_SCAM_CHANNELS: tuple[str, ...] = (
    "CertiKAlert",
    "PeckShieldAlert",
    "ScamSniffer",
    "SlowMist",
    "zachxbt",
    "cryptobanter",
)
SCAM_CHANNELS = _env_list("RMI_SCAM_CHANNELS", list(_DEFAULT_SCAM_CHANNELS))
#: Extra handles/titles to exclude from dialog auto-discovery.
SCAM_WATCH_EXCLUDE = _env_list("RMI_SCAM_WATCH_EXCLUDE", [])


# ── Client bootstrap (fail-open) ─────────────────────────────────────────────


async def _build_headless_client() -> Any:
    """Return the singleton headless Telethon client, or ``None`` (never raises)."""
    try:
        from app.domains.telegram.headless import get_headless_client

        client = await get_headless_client()
        if getattr(client, "is_connected", False):
            return client
        return None
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:headless_client_unavailable err=%s", str(exc)[:200])
        return None


# ── Time filter ──────────────────────────────────────────────────────────────


def _as_utc(value: datetime) -> datetime:
    """Normalize a (possibly naive) datetime to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _inside_window(message: ChannelMessage, cutoff: datetime) -> bool:
    """True when a message falls inside the lookback window.

    Messages without a date are kept (best-effort — cross-run dedup on the
    seen-set still prevents re-ingesting ancient items).
    """
    posted = message.posted_at
    if posted is None:
        return True
    return _as_utc(posted) >= cutoff


# ── Sources: explicit env + registry + auto-discovered dialogs ──────────────


def _registry_scam_sources(include_watchers: bool | None = None) -> list[dict[str, Any]]:
    """Scam/shill channels from the followed-channels registry (tg_channels.json).

    Picks up ``kind=scammer_caller`` / ``kind=scammer_group`` entries and
    ``category=shill_promotions`` entries — channels the owner already
    flagged as shill/caller sources. Legit scam-*watcher* categories
    (``scam_alerts`` / ``phishing_alerts``) are excluded unless
    ``include_watchers=True``. Fail-open → ``[]``.
    """
    try:
        from app.databus.tg_channel_registry import load_tg_channels
    except ImportError:
        return []
    include_watchers = SCAM_WATCH_INCLUDE_WATCHERS if include_watchers is None else include_watchers
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        entries = load_tg_channels()
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:registry_failed err=%s", str(exc)[:200])
        return []
    for entry in entries:
        kind = str(entry.get("kind") or "")
        category = str(entry.get("category") or "")
        subtype: ScamSourceType | None = None
        if kind == "scammer_caller":
            subtype = ScamSourceType.SHILL_CHANNEL
        elif kind == "scammer_group":
            subtype = ScamSourceType.PUMP_GROUP
        elif category == "shill_promotions":
            subtype = ScamSourceType.SHILL_CHANNEL
        elif include_watchers and category in ("scam_alerts", "phishing_alerts"):
            subtype = ScamSourceType.SCAM_TOKEN_CHANNEL
        if subtype is None:
            continue
        key = str(entry.get("username") or "").strip().lstrip("@").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "username": key,
                "name": str(entry.get("name") or key),
                "category": "scam_shill",
                "subtype": subtype,
                "registry": True,
            }
        )
    return sources


def _classify_dialog(title: str, username: str) -> ScamSourceType | None:
    """Classify a followed channel (title/username) via shill heuristics.

    Returns ``None`` for own channels, user-excluded handles, and channels
    that do not match any shill keyword.
    """
    haystack = f"{title or ''} {username or ''}".lower()
    if any(word in haystack for word in _OWN_EXCLUDE_KEYWORDS):
        return None
    if any(word in haystack for word in SCAM_WATCH_EXCLUDE):
        return None
    for subtype, keywords in _DIALOG_HEURISTICS:
        if any(keyword in haystack for keyword in keywords):
            return subtype
    return None


async def discover_scam_channels(client: Any, max_discovered: int | None = None) -> list[dict[str, Any]]:
    """Auto-discover scam/shill channels from the followed dialogs list.

    Reuses ``app.domains.news_ingest.discovery.discover_followed_channels``
    (the canonical followed-channels mechanism — same headless client the
    news sweep reads through) and flags dialogs whose title/username matches
    shill keyword heuristics. Fail-open → ``[]``; bounded by
    ``max_discovered`` (default ``RMI_SCAM_WATCH_MAX_DISCOVERED``).
    """
    cap = max(0, max_discovered if max_discovered is not None else SCAM_WATCH_MAX_DISCOVERED)
    if cap <= 0:
        return []
    try:
        from app.domains.news_ingest.discovery import discover_followed_channels
    except ImportError:
        return []
    try:
        pairs = await discover_followed_channels(client)
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:discovery_failed err=%s", str(exc)[:200])
        return []
    sources: list[dict[str, Any]] = []
    for username, title in pairs:
        subtype = _classify_dialog(str(title or ""), str(username or ""))
        if subtype is None:
            continue
        handle = str(username or "").strip().lstrip("@").lower()
        if not handle:
            continue
        sources.append(
            {
                "username": handle,
                "name": str(title or handle),
                "category": "scam_shill",
                "subtype": subtype,
                "discovered": True,
            }
        )
        if len(sources) >= cap:
            break
    if sources:
        logger.info("scam_watch:discovered count=%s", len(sources))
    return sources


async def channel_sources(client: Any | None = None) -> list[dict[str, Any]]:
    """Effective source-config dicts (``username`` + ``category`` + ``subtype``).

    Order: explicit ``RMI_SCAM_CHANNELS`` → registry scam/shill entries →
    auto-discovered dialogs. Deduped by normalized @handle; never raises.
    """
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    for username in SCAM_CHANNELS:
        key = str(username).strip().lstrip("@").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "username": key,
                "name": key,
                "category": "scam_shill",
                "subtype": ScamSourceType.SHILL_CHANNEL,
                "explicit": True,
            }
        )

    for entry in _registry_scam_sources():
        key = str(entry.get("username") or "")
        if key in seen:
            continue
        seen.add(key)
        sources.append(entry)

    for entry in await discover_scam_channels(client):
        key = str(entry.get("username") or "")
        if key in seen:
            continue
        seen.add(key)
        sources.append(entry)

    return sources


# ── Sweep: read → normalize → time-filter → dedupe ──────────────────────────


async def sweep_scam_channels(
    *,
    client: Any | None = None,
    lookback_hours: int | None = None,
    per_channel_limit: int | None = None,
    max_discovered: int | None = None,
) -> list[NewsArticle]:
    """Read recent messages from the scam/shill channel sources.

    Every normalized item is tagged ``category="scam_shill"`` and carries
    the canonical entity list (``$TICKER`` / EVM / Solana — via
    ``extract_entities``). Returns deduped ``NewsArticle`` objects newer
    than ``lookback_hours`` (default ``RMI_SCAM_WATCH_SWEEP_HOURS``).
    Accepts an injected client (used by tests); when omitted the headless
    singleton is built (fail-open → ``[]``).
    """
    if client is None:
        client = await _build_headless_client()
    if client is None:
        logger.warning("scam_watch:sweep_skipped_no_client")
        return []

    sources = await channel_sources(client)
    if not sources:
        logger.info("scam_watch:no_sources_configured")
        return []

    lookback = max(1, lookback_hours or SCAM_WATCH_SWEEP_HOURS)
    per_channel = max(5, per_channel_limit or SCAM_WATCH_PER_CHANNEL_LIMIT)
    cutoff = datetime.now(UTC) - timedelta(hours=lookback)

    articles: list[NewsArticle] = []
    for source in sources:
        username = str(source.get("username") or "").strip().lstrip("@")
        if not username:
            continue
        messages = await read_channel_messages(client, username, per_channel)
        for message in messages:
            if not _inside_window(message, cutoff):
                continue
            article = channel_message_to_article(message, "scam_shill")
            if article is not None:
                articles.append(article)
    return dedupe_articles(articles)


# ── Address / ticker extraction (reuses canonical entity + address regexes) ─


def classify_entity(entity: str) -> str:
    """Classify a news_ingest entity as ``ticker`` / ``evm`` / ``solana`` / ``""``.

    Reuses ``app.core.shared_utils`` EVM/Solana regexes — the same shapes
    ``app.rag.dedup`` and the bot formatters use.
    """
    if not entity:
        return ""
    if entity.startswith("$"):
        return "ticker"
    if is_evm_address(entity):
        return "evm"
    if is_solana_address(entity):
        return "solana"
    return ""


def extract_token_addresses(article: NewsArticle) -> tuple[list[dict[str, str]], list[str]]:
    """Split an article's entities into (addresses, tickers).

    Addresses are unique and classified by chain (``ethereum`` for EVM,
    ``solana`` for base58); tickers are the ``$SYMBOL`` mentions (uppercase,
    ``$`` stripped). Reuses the canonical entity extraction
    (``extract_entities`` via ``channel_message_to_article``).
    """
    addresses: list[dict[str, str]] = []
    tickers: list[str] = []
    seen: set[str] = set()
    seen_tickers: set[str] = set()
    for entity in article.entities or []:
        kind = classify_entity(entity)
        if kind == "ticker":
            ticker = entity[1:]
            if ticker not in seen_tickers:
                seen_tickers.add(ticker)
                tickers.append(ticker)
        elif kind in ("evm", "solana") and entity not in seen:
            seen.add(entity)
            addresses.append({"address": entity, "chain": "ethereum" if kind == "evm" else "solana"})
    return addresses, tickers


def _collect_submissions(articles: list[NewsArticle]) -> list[dict[str, Any]]:
    """Flatten articles → unique (address, chain, attribution) records."""
    submissions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for article in articles or []:
        addresses, tickers = extract_token_addresses(article)
        for addr in addresses:
            key = f"{addr['chain']}:{addr['address']}"
            if key in seen:
                continue
            seen.add(key)
            submissions.append(
                {
                    "address": addr["address"],
                    "chain": addr["chain"],
                    "ticker": ",".join(tickers[:5]),
                    "source_channel": article.source_channel,
                    "source_url": article.source_url,
                    "category": article.category or "scam_shill",
                }
            )
    return submissions


# ── Feed the scanner: seen-dedupe → submit-for-scan + durable queue ─────────

_SEEN_KEY = "bot:scam_watch:seen"
_SCAN_QUEUE_KEY = "bot:scam_watch:scan_queue"
_SEEN_CACHE: set[str] = set()
_MAX_SEEN_CACHE = 2000


async def _mark_seen(address: str) -> bool:
    """Return True when *address* is new (and record it). Best-effort, never raises.

    Backed by the Redis SET ``bot:scam_watch:seen`` plus a bounded in-memory
    cache (so cross-process dedup survives even when Redis is down).
    """
    key = address.strip().lower()
    if not key:
        return False
    if key in _SEEN_CACHE:
        return False
    _SEEN_CACHE.add(key)
    if len(_SEEN_CACHE) > _MAX_SEEN_CACHE:
        _SEEN_CACHE.clear()
    try:
        r = get_redis_async()
        if r is not None:
            added = await r.sadd(_SEEN_KEY, key)
            if not added:
                return False
    except Exception as exc:  # fail-open: in-memory dedupe still applies
        logger.debug("scam_watch:seen_failed err=%s", str(exc)[:120])
    return True


async def submit_for_scan(address: str, chain: str = "ethereum") -> bool:
    """Best-effort in-process scanner submission (free tier). Never raises.

    Calls ``app.domains.scanner.service.scan_token`` — the same service the
    ``POST /api/v1/scanner/scan`` endpoint triggers. Fail-open: any import /
    runtime error logs and returns ``False``.
    """
    try:
        from app.domains.scanner.service import scan_token

        result = await scan_token(token_address=address, chain=chain, tiers=["free"])
        flags = list(getattr(result, "risk_flags", []) or [])
        safety = getattr(result, "safety_score", None)
        logger.info(
            "scam_watch:scan_done address=%s chain=%s safety=%s flags=%s",
            address[:12],
            chain,
            safety,
            flags[:5],
        )
        return True
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:scan_failed address=%s err=%s", address[:12], str(exc)[:120])
        return False


async def _enqueue_for_scanner(entry: dict[str, Any]) -> bool:
    """Push one discovered address onto the scanner queue (JSON, best-effort)."""
    try:
        r = get_redis_async()
        if r is None:
            return False
        await r.lpush(_SCAN_QUEUE_KEY, json.dumps(entry, default=str))
        return True
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:enqueue_failed err=%s", str(exc)[:120])
        return False


async def _feed_scanner(submissions: list[dict[str, Any]]) -> tuple[int, int]:
    """Submit new addresses to the scanner + enqueue durable records.

    Returns ``(submitted, enqueued)``. Both paths are best-effort and
    independently bounded (``RMI_SCAM_WATCH_SCAN_LIMIT`` /
    ``RMI_SCAM_WATCH_QUEUE_LIMIT``; ``RMI_SCAM_WATCH_ENQUEUE`` disables the
    queue). Only addresses not already in the seen-set are processed.
    """
    scan_budget = max(0, SCAM_WATCH_SCAN_LIMIT)
    queue_budget = max(0, SCAM_WATCH_QUEUE_LIMIT) if SCAM_WATCH_ENQUEUE else 0
    submitted = 0
    enqueued = 0
    for entry in submissions or []:
        if scan_budget <= 0 and queue_budget <= 0:
            break
        address = str(entry.get("address") or "").strip()
        if not address:
            continue
        if not await _mark_seen(address):
            continue  # already known from a previous run
        if scan_budget > 0:
            await submit_for_scan(address, str(entry.get("chain") or "ethereum"))
            submitted += 1
            scan_budget -= 1
        if queue_budget > 0:
            if await _enqueue_for_scanner(entry):
                enqueued += 1
            queue_budget -= 1
    return submitted, enqueued


# ── Scheduled job (feeds the scanner; does not post to a bot channel) ────────


async def run_scam_watch(ctx: Any = None) -> int:
    """Sweep scam/shill channels and feed extracted token addresses to the scanner.

    Returns the number of unique addresses processed (submitted + enqueued).
    Fails open at every step: no client, no sources, empty sweep, Redis or
    scanner outage all log and return without raising. ``ctx`` is accepted
    for JobQueue compatibility but unused (no posting).
    """
    try:
        articles = await sweep_scam_channels()
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:sweep_failed err=%s", str(exc)[:200])
        return 0
    if not articles:
        logger.debug("scam_watch:no_fresh_items")
        return 0
    submissions = _collect_submissions(articles)
    if not submissions:
        logger.debug("scam_watch:no_token_addresses")
        return 0
    submitted, enqueued = await _feed_scanner(submissions)
    logger.info(
        "scam_watch:run_done articles=%s unique_addresses=%s submitted=%s enqueued=%s",
        len(articles),
        len(submissions),
        submitted,
        enqueued,
    )
    return submitted + enqueued


__all__ = [
    "SCAM_CHANNELS",
    "SCAM_WATCH_ENABLED",
    "SCAM_WATCH_ENQUEUE",
    "SCAM_WATCH_INTERVAL_MINUTES",
    "SCAM_WATCH_MAX_DISCOVERED",
    "SCAM_WATCH_PER_CHANNEL_LIMIT",
    "SCAM_WATCH_QUEUE_LIMIT",
    "SCAM_WATCH_SCAN_LIMIT",
    "SCAM_WATCH_SWEEP_HOURS",
    "ScamSourceType",
    "channel_sources",
    "classify_entity",
    "discover_scam_channels",
    "extract_token_addresses",
    "run_scam_watch",
    "submit_for_scan",
    "sweep_scam_channels",
]
