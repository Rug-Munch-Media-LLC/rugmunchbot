"""Coordinated shill-campaign detection — same token pushed by many accounts.

Channel-quality overhaul (2026-08-29), deliverable 4: a shill campaign is a
token (address or ``$TICKER``) that many distinct accounts / KOL call
channels push inside a short window. Legit news coverage is typically a
couple of outlets; a coordinated campaign is N distinct pushers in W hours
— that is the signal, and it belongs on the ALERTS channel
(@rmialerts, high-signal only — alongside exploit + scam/rug detections).

Pipeline:
  1. ``run_shill_watch`` sweeps the focused KOL/alpha channel list
     (``SHILL_WATCH_CHANNELS`` — bounded, ~10 channels, NOT the full
     200-channel news sweep) for the last ``SHILL_SWEEP_HOURS``.
  2. ``detect_shill_campaigns`` (pure) groups articles by token identity,
     counts DISTINCT pushing channels per token inside the window, and
     flags tokens with >= ``SHILL_MIN_ACCOUNTS`` distinct pushers and
     >= ``SHILL_MIN_MENTIONS`` total mentions.
  3. ``format_shill_alert`` renders the HTML card (token + pusher list +
     links), and the runner posts it to the alerts channel through the
     shared format-law chokepoint (``_safe_send``), deduped per token/day
     so a campaign alerts once, never spams.

Fail-open everywhere: a dead sweep client, empty sweep, or Redis outage
never crashes the tick — silence is a valid outcome.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot.channels.scam_watch._classify import extract_token_addresses
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.shill_watch")

SHILL_WATCH_ENABLED = os.getenv("RMI_SHILL_WATCH_ENABLED", "true").lower() in ("1", "true", "yes")
SHILL_INTERVAL_MIN = int(os.getenv("RMI_SHILL_WATCH_INTERVAL_MIN", "60"))
#: Bounded sweep window (hours) — shill campaigns are fast; no need to look
#: back a week. Must stay <= the news sweep window for consistent freshness.
SHILL_SWEEP_HOURS = int(os.getenv("RMI_SHILL_WATCH_SWEEP_HOURS", "6"))
#: Messages read per focused channel per sweep (bounded).
SHILL_PER_CHANNEL_LIMIT = int(os.getenv("RMI_SHILL_WATCH_PER_CHANNEL_LIMIT", "30"))
#: Distinct pushing accounts that make a token a campaign.
SHILL_MIN_ACCOUNTS = int(os.getenv("RMI_SHILL_MIN_ACCOUNTS", "3"))
#: Minimum total mentions (articles) across those accounts.
SHILL_MIN_MENTIONS = int(os.getenv("RMI_SHILL_MIN_MENTIONS", "3"))
#: Max alerts posted per run (bounded, no spam).
SHILL_MAX_ALERTS = int(os.getenv("RMI_SHILL_MAX_ALERTS", "3"))
#: Detection window for grouping (hours).
SHILL_WINDOW_HOURS = int(os.getenv("RMI_SHILL_WINDOW_HOURS", "24"))

#: Focused KOL/alpha/call-analysis channels the shill watcher sweeps.
#: Comma-separated env override (``RMI_SHILL_WATCH_CHANNELS``). Deliberately
#: small — this is the coordinated-pusher signal set, not the news feed.
_DEFAULT_SHILL_CHANNELS: tuple[str, ...] = (
    "CallAnalyser",  # KOL call analysis
    "walloftrophies",  # KOL Trending / CallAnalyser
    "thepumpalert",  # scam call-out channel
    "kamocalls",  # Kamo | Daily trades
    "CryptoCapoTG",  # CryptoCapo — calls + analysis
    "shahlito",  # Shah's Journal — research/calls
    "skeletonlounge",  # Skeleton Lounge Portal — calls
    "rickburpbotnews",  # Rick's Lab — research/alpha
    "dimethyeth",  # dime — research
    "OnChainWizard",  # OnChain Wizard — alpha
    "cryptowhaletrades",  # Crypto Whale Trading — whale/calls
)
SHILL_WATCH_CHANNELS: tuple[str, ...] = tuple(
    c.strip().lstrip("@")
    for c in os.getenv("RMI_SHILL_WATCH_CHANNELS", ",".join(_DEFAULT_SHILL_CHANNELS)).split(",")
    if c.strip()
)


@dataclass(slots=True)
class ShillCampaign:
    """One detected coordinated shill campaign."""

    token: str  # address or $TICKER
    chain: str
    accounts: list[str]  # distinct pushing channels, most-mentions first
    mentions: list[dict[str, str]] = field(default_factory=list)  # {channel, title, url}
    window_start: str = ""
    window_end: str = ""
    score: float = 0.0

    @property
    def key(self) -> str:
        """Stable dedupe identity: token + chain (per-day in the runner)."""
        return f"{self.chain}:{self.token}"


def _posted_at(article: Any) -> datetime:
    """Article timestamp (UTC); falls back to now for undated rows."""
    ts = getattr(article, "posted_at", None)
    if ts is None and isinstance(article, dict):
        ts = article.get("posted_at")
    if ts is None:
        return datetime.now(UTC)
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _article_fields(article: Any) -> tuple[str, str, str, list[str]]:
    """(title, channel, url, entities) for a NewsArticle or dict (fail-open)."""
    if isinstance(article, dict):
        title = str(article.get("title") or article.get("text") or "")[:140]
        channel = str(article.get("source_channel") or article.get("channel") or "unknown")
        url = str(article.get("source_url") or article.get("url") or "")[:240]
        entities = [str(e) for e in (article.get("entities") or [])]
        return title, channel, url, entities
    title = str(getattr(article, "title", "") or "")[:140]
    channel = str(getattr(article, "source_channel", "") or "unknown")
    url = str(getattr(article, "source_url", "") or "")[:240]
    entities = [str(e) for e in (getattr(article, "entities", None) or [])]
    return title, channel, url, entities


def _token_ids(article: Any) -> set[tuple[str, str]]:
    """Token identities for one article: (chain, address) + ("ticker", $SYM).

    Reuses the canonical entity extraction (``scam_watch._classify``
    ``extract_token_addresses``) so shill detection sees exactly the tokens
    the scanner feeds do — one vocabulary, no drift.
    """
    ids: set[tuple[str, str]] = set()
    if isinstance(article, dict):
        # Dicts (tests / synthetic rows) carry entities directly.
        for e in article.get("entities") or []:
            e = str(e)
            if e.startswith("$") and len(e) >= 3:
                ids.add(("ticker", e.upper()))
            elif len(e) >= 32:
                ids.add(("address", e))
        return ids
    try:
        addresses, tickers = extract_token_addresses(article)
    except Exception:  # fail-open — unknown extraction never breaks detection
        return ids
    for addr in addresses:
        ids.add(("address", str(addr.get("address") or "")))
        ids.add(("chain", str(addr.get("chain") or "ethereum")))
    for t in tickers:
        if t:
            ids.add(("ticker", "$" + t.upper()))
    return ids


def detect_shill_campaigns(
    articles: list[Any],
    *,
    window_hours: int = SHILL_WINDOW_HOURS,
    min_accounts: int = SHILL_MIN_ACCOUNTS,
    min_mentions: int = SHILL_MIN_MENTIONS,
) -> list[ShillCampaign]:
    """Flag tokens pushed by >= *min_accounts* distinct channels in a window.

    Pure + deterministic for a fixed input: groups by token identity, counts
    distinct pushing channels and total mentions inside ``window_hours``,
    and returns campaigns sorted by score (accounts*10 + mentions) desc.

    The 1-channel guard: a token mentioned only by one channel (however
    loudly) is coverage/call, NOT a coordinated campaign — the distinct-
    account count is the whole point.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=max(1, window_hours))

    buckets: dict[str, dict[str, Any]] = {}

    def _bucket(token: str, chain: str) -> dict[str, Any]:
        key = f"{chain}:{token}"
        if key not in buckets:
            buckets[key] = {
                "token": token,
                "chain": chain,
                "accounts": {},
                "mentions": [],
                "earliest": now,
            }
        return buckets[key]

    for article in articles or []:
        posted = _posted_at(article)
        if posted < cutoff:
            continue
        title, channel, url, _entities = _article_fields(article)
        token_ids = _token_ids(article)
        for kind, value in token_ids:
            if kind not in ("address", "ticker"):
                continue
            if not value or len(str(value)) < 3:
                continue
            if kind == "ticker" and str(value).upper().lstrip("$") in {
                "BTC",
                "ETH",
                "SOL",
                "USDT",
                "USDC",
                "BNB",
            }:
                # Blue-chip tickers are mentioned by everyone constantly —
                # "coordinated shill" for BTC is noise by definition.
                continue
            b = _bucket(str(value), "ethereum" if kind == "address" else "ticker")
            b["accounts"].setdefault(channel, 0)
            b["accounts"][channel] += 1
            b["earliest"] = min(b["earliest"], posted)
            b["mentions"].append(
                {
                    "channel": channel,
                    "title": title,
                    "url": url,
                }
            )

    campaigns: list[ShillCampaign] = []
    for b in buckets.values():
        accounts = sorted(b["accounts"].items(), key=lambda kv: -kv[1])
        if len(accounts) < min_accounts:
            continue
        mentions = b["mentions"]
        if len(mentions) < min_mentions:
            continue
        campaigns.append(
            ShillCampaign(
                token=b["token"],
                chain=b["chain"],
                accounts=[a for a, _ in accounts],
                mentions=mentions,
                window_start=b["earliest"].isoformat(),
                window_end=now.isoformat(),
                score=len(accounts) * 10.0 + len(mentions),
            )
        )
    return sorted(campaigns, key=lambda c: c.score, reverse=True)


def format_shill_alert(campaign: ShillCampaign) -> str:
    """HTML alert card for one campaign (Telegram parse_mode=html)."""
    token = str(campaign.token)
    if campaign.chain == "ticker":
        head = f"🚨 <b>Coordinated shill: {token}</b>"
    else:
        short = token[:8] + "…" + token[-6:] if len(token) > 16 else token
        head = f"🚨 <b>Coordinated shill: <code>{short}</code></b> ({campaign.chain})"
    lines = [
        head,
        "",
        f"{len(campaign.accounts)} distinct accounts pushed this token in the last "
        f"{SHILL_WINDOW_HOURS}h:",
        "",
    ]
    for channel in campaign.accounts[:6]:
        lines.append(f"  • {channel}")
    if len(campaign.accounts) > 6:
        lines.append(f"  …and {len(campaign.accounts) - 6} more")
    sample = campaign.mentions[:2]
    if sample:
        lines.append("")
        lines.append("Earliest pushes:")
        for m in sample:
            title = str(m.get("title") or "")[:90]
            url = str(m.get("url") or "")
            if url:
                lines.append(f'  → <a href="{url}">{title}</a>')
            else:
                lines.append(f"  → {title}")
    lines.append("")
    lines.append("Coordinated shill = high rug-pump risk. Verify before ape: /scan @rugmunchbot")
    return "\n".join(lines)


# ── focused sweep + runner ────────────────────────────────────────────────


async def _sweep_shill_sources(
    client: Any | None = None, lookback_hours: int | None = None
) -> list[Any]:
    """Read recent messages from the focused KOL/calls channel list (bounded).

    Returns ``NewsArticle`` objects (fail-open ``[]``). Uses the same
    news_ingest read/normalize path as the main sweep but ONLY the
    ``SHILL_WATCH_CHANNELS`` handles — the full 200-channel discovery sweep
    is left to the tg_news job, so the shill watcher stays cheap.
    """
    from rugmunchbot._vendor.news_ingest.ingest import read_channel_messages
    from rugmunchbot._vendor.news_ingest.normalize import channel_message_to_article
    from rugmunchbot.channels.telegram_news._sources._fetch import (
        _build_headless_client,
        _inside_window,
    )

    if client is None:
        try:
            client = await _build_headless_client()
        except Exception as exc:  # fail-open by contract
            logger.warning("shill_watch:no_client err=%s", str(exc)[:120])
            return []
    if client is None:
        logger.warning("shill_watch:sweep_skipped_no_client")
        return []

    lookback = max(1, lookback_hours or SHILL_SWEEP_HOURS)
    cutoff = datetime.now(UTC) - timedelta(hours=lookback)
    articles: list[Any] = []
    for username in SHILL_WATCH_CHANNELS:
        try:
            messages = await read_channel_messages(client, username, SHILL_PER_CHANNEL_LIMIT)
        except Exception as exc:  # a dead channel never blocks the sweep
            logger.debug("shill_watch:channel_failed channel=%s err=%s", username, str(exc)[:80])
            continue
        for message in messages:
            try:
                if not _inside_window(message, cutoff):
                    continue
                article = channel_message_to_article(message, "alpha")
            except Exception as exc:  # malformed row — skip, never raise
                logger.debug("shill_watch:bad_row channel=%s err=%s", username, str(exc)[:80])
                continue
            if article is not None:
                articles.append(article)
    return articles


async def run_shill_watch(ctx: Any = None) -> int:
    """Sweep KOL channels → detect campaigns → alert @rmialerts (deduped).

    Returns the number of alerts posted (0..``SHILL_MAX_ALERTS``). Dedupe is
    per token per calendar day (global ``_remember`` namespace), so a
    campaign alerts once a day, never every tick. Fail-open at every stage.
    """
    from rugmunchbot.channels.scheduler._core import _chat_id, _release, _remember, _safe_send

    chat_id = _chat_id("alerts")
    if not chat_id:
        logger.debug("shill_watch:skipped_no_alerts_chat")
        return 0

    articles = await _sweep_shill_sources()
    if not articles:
        logger.debug("shill_watch:no_fresh_articles")
        return 0

    campaigns = detect_shill_campaigns(articles)
    if not campaigns:
        logger.info("shill_watch:no_campaigns articles=%s", len(articles))
        return 0

    today = datetime.now(UTC).strftime("%Y%m%d")
    posted = 0
    for campaign in campaigns[:SHILL_MAX_ALERTS]:
        dedupe_key = f"shill:{campaign.key}:{today}"
        if not _remember("shill_watch", dedupe_key):
            continue
        if ctx is None:
            # No bot handle (manual/cron use) — release and skip.
            _release("shill_watch", dedupe_key)
            continue
        text = format_shill_alert(campaign)
        if await _safe_send(ctx.bot, chat_id, text):
            posted += 1
            logger.info(
                "shill_watch:alerted token=%s accounts=%s mentions=%s",
                campaign.token[:40],
                len(campaign.accounts),
                len(campaign.mentions),
            )
        else:
            _release("shill_watch", dedupe_key)
    logger.info(
        "shill_watch:run_done articles=%s campaigns=%s posted=%s",
        len(articles),
        len(campaigns),
        posted,
    )
    return posted


__all__ = [
    "SHILL_INTERVAL_MIN",
    "SHILL_MAX_ALERTS",
    "SHILL_MIN_ACCOUNTS",
    "SHILL_MIN_MENTIONS",
    "SHILL_SWEEP_HOURS",
    "SHILL_WATCH_CHANNELS",
    "SHILL_WATCH_ENABLED",
    "SHILL_WINDOW_HOURS",
    "ShillCampaign",
    "detect_shill_campaigns",
    "format_shill_alert",
    "run_shill_watch",
]
