"""Shill-campaign detection (pure) + URL helpers.

Split out of ``channels/shill_watch.py`` during the de-godding pass. Holds
the ``ShillCampaign`` dataclass, the pure ``detect_shill_campaigns`` grouping
logic, and the X / Telegram post-URL recognizers used as screenshot targets.
Pure module: no network, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot.channels.scam_watch._classify import extract_token_addresses
from rugmunchbot.channels.shill_watch._config import (
    SHILL_MIN_ACCOUNTS,
    SHILL_MIN_MENTIONS,
    SHILL_WINDOW_HOURS,
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
    importance: float = 0.0  # rerank importance (2026-08-30 routing)
    screenshots: list[dict[str, str]] = field(default_factory=list)  # {url, image_path, text}
    scanner_evidence: dict[str, Any] = field(
        default_factory=dict
    )  # {scanned, risk_level, safety_score, bullets, note}
    #: 2026-09-04 KOL reporting: per-account enrichment (follower count +
    #: track record from ``data/kol/wall_of_shame.json``) so the alert can
    #: name the KOL, their reach, and their prior shill/rug history.
    kol_stats: dict[str, Any] = field(default_factory=dict)

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


# ── X status URL detection (screenshot evidence target) ────────────────────
# A shill tweet is captured from its public ``x.com/<handle>/status/<id>``
# (or ``twitter.com`` / ``/i/status/``) page. Reuses the same status-id
# recognition the read-tweet tool uses so the two rails share one vocabulary.
_X_STATUS_RE = re.compile(
    r"https?://(?:x\.com|twitter\.com)/(?:[^/]+|i)/status/(\d+)", re.IGNORECASE
)
_X_STATUS_CANON = "https://x.com/i/status/{id}"


def _x_status_url(url: str) -> str:
    """Canonical X status URL for the first status id in ``url`` (or "")."""
    m = _X_STATUS_RE.search(str(url or ""))
    return _X_STATUS_CANON.format(id=m.group(1)) if m else ""


# ── Telegram post URL detection (2026-09-01, KOL Telegram capture) ─────────
# A monitored KOL channel/group post is captured from its PUBLIC web preview
# ``https://t.me/s/<channel>/<msg_id>`` — the same Playwright + VL-4B OCR
# pipeline used for X posts (``tools.shill_shot.capture_and_ocr``), so one
# code path serves both origins. The bot's ``NewsArticle.source_url`` already
# carries the ``https://t.me/<channel>/<msg_id>`` fallback for channel posts.
_TG_POST_RE = re.compile(r"https?://t\.me/([^/?#]+)(?:/(\d+))?", re.IGNORECASE)


def _tg_post_url(url: str) -> str:
    """Public ``t.me/s/`` web-preview URL for a t.me post link (or "")."""
    m = _TG_POST_RE.search(str(url or ""))
    if not m:
        return ""
    channel, msg_id = m.group(1), m.group(2)
    if not channel or not msg_id:
        return ""
    return f"https://t.me/s/{channel}/{msg_id}"


def _esc(value: object) -> str:
    """HTML-escape for Telegram parse_mode=html."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
