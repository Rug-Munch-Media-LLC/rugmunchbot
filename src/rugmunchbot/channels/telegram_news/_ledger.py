"""Per-edition published-id ledger + content-hash near-duplicate guard.

Fills the gap between the quality gate (lint/dedupe) and the send (2026-09).
A briefing report must not only be clean — it must be NEW (content hash not
among the last N published for this edition) and non-blank. Wired as the
final pre-send gate on the Morning Rug and Midday Market Update briefings.

Fail-open by contract: a Redis outage never skips a legitimately clean
report — the gate degrades to "ok" and posts. It only ever SAYS NO to
content that is genuinely blank or a fresh byte-identical duplicate.

This module is the SOLE home of the content-hash ledger; the per-ARTICLE
freshness set lives in scheduler._remember and the morning→midday story
exclusion lives in _edition_dedup (do not duplicate their keys here).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from rugmunchbot.support.image_policy import is_valid_image
from rugmunchbot.support.quality_gate import PLACEHOLDER_IMAGE_URL

logger = logging.getLogger("rmi_bot.channels.telegram_news.ledger")

#: RugMunch's own 400x400 app favicon — the platform brand logo. NEVER the
#: cryptorugmunch X / Telegram profile pic (pbs.twimg.com/profile_images)
#: or any brand chrome; those are banned at is_valid_image().
RMI_LOGO_URL: Final[str] = PLACEHOLDER_IMAGE_URL

#: Redis key holding the last N content hashes for an edition.
_LEDGER_KEY_FMT = "ledger:{namespace}:hashes"
#: Prefix for the per-destination published-id set (``_redis_key``).
#: Was referenced but never defined -> NameError on every publish
#: attempt since 2026-10-12 (root cause of the stalled @rmicryptonews).
_LEDGER_PREFIX: Final[str] = "ledger:dest:"
#: Block a re-run only if byte-identical content was published within
#: the last N editions (10 ≈ days of daily briefings).
DEFAULT_HISTORY: Final[int] = 10
#: Key TTL — 3 days; old hashes self-clean.
_LEDGER_TTL: Final[int] = 86400 * 3


def content_hash(text: str) -> str:
    """Stable sha256 of the report's whitespace-normalized body."""
    return hashlib.sha256(" ".join(str(text or "").split()).encode()).hexdigest()


@dataclass
class Evaluation:
    """Result of the final pre-send gate."""

    allowed: bool
    reason: str
    content_hash: str


def _get_client() -> Any | None:
    """Lazy sync Redis client (fail-open via None)."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis

        return get_redis()
    except Exception as exc:  # fail-open by contract
        logger.debug("ledger:redis_client_failed err=%s", str(exc)[:100])
        return None


def record_publication(
    namespace: str,
    h: str,
    *,
    n_history: int = DEFAULT_HISTORY,
    ttl: int = _LEDGER_TTL,
    log: Any = None,
) -> None:
    """Append a published content hash to the edition ledger (fail-open)."""
    if not h:
        return
    client = _get_client()
    if client is None:
        return
    key = _LEDGER_KEY_FMT.format(namespace=namespace)
    try:
        client.lpush(key, h)
        client.ltrim(key, 0, n_history - 1)
        client.expire(key, ttl)
        if log is not None:
            log.info("ledger:recorded ns=%s hash=%s", namespace, h[:12])
    except Exception as exc:  # fail-open — never block the edition
        if log is not None:
            log.warning("ledger:record_failed ns=%s err=%s", namespace, str(exc)[:120])


def is_duplicate_recent(namespace: str, h: str, *, n: int = DEFAULT_HISTORY) -> bool:
    """True if *h* is already among the last *n* published hashes (fail-open)."""
    client = _get_client()
    if client is None or not h:
        return False
    key = _LEDGER_KEY_FMT.format(namespace=namespace)
    try:
        recent = client.lrange(key, 0, n - 1)
        decoded = {
            item.decode() if isinstance(item, (bytes, bytearray)) else str(item)
            for item in recent
        }
        return h in decoded
    except Exception as exc:  # fail-open — a Redis hiccup never blocks a post
        logger.debug("ledger:dup_check_failed ns=%s err=%s", namespace, str(exc)[:100])
        return False


def evaluate_report(
    report: str,
    *,
    namespace: str,
    min_words: int = 100,
    n_history: int = DEFAULT_HISTORY,
    log: Any = None,
) -> Evaluation:
    """Final pre-send gate: reject blank/degenerate/duplicate reports.

    Returns ``Evaluation(allowed=False, reason="content_blank_or_dup:<detail>")``
    for empty/short reports and recent duplicates; otherwise
    ``Evaluation(allowed=True, reason="ok", content_hash=h)``.
    """
    h = content_hash(report)
    if not report or not report.strip():
        _log_block(log, namespace, "content_blank_or_dup:empty_report", h)
        return Evaluation(False, "content_blank_or_dup:empty_report", h)
    words = len(report.split())
    if words < min_words:
        _log_block(
            log,
            namespace,
            f"content_blank_or_dup:too_short words={words} min={min_words}",
            h,
        )
        return Evaluation(False, "content_blank_or_dup:too_short", h)
    if is_duplicate_recent(namespace, h, n=n_history):
        _log_block(log, namespace, "content_blank_or_dup:recent_duplicate", h)
        return Evaluation(False, "content_blank_or_dup:recent_duplicate", h)
    return Evaluation(True, "ok", h)


def _log_block(log: Any, namespace: str, reason: str, h: str) -> None:
    """Emit the structured ``content_blank_or_dup`` block log at both sinks."""
    msg = "ledger:publication_blocked ns=%s reason=%s hash=%s ts=%s"
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    if log is not None:
        log.warning("ledger:publication_blocked ns=%s reason=%s hash=%s", namespace, reason, h[:12])
    logger.warning(msg, namespace, reason, h[:12], ts)


def select_edition_image(*, candidate: str = "", fallback: str = RMI_LOGO_URL) -> str:
    """Final image authority for a briefing: resolved image OR the RMI logo.

    Accepts an already-resolved candidate (story og:image or the quality-gate
    placeholder) and enforces the doctrine: a valid, non-banned image is kept;
    an empty/invalid one lands on the RugMunch Intelligence logo (the platform
    favicon, NEVER the cryptorugmunch X/Telegram profile pic or brand chrome).
    """
    if candidate and is_valid_image(candidate):
        return str(candidate)
    return fallback


def item_key(article: Any) -> str:
    """Canonical identity of a news item — the ledger key.

    Prefers the canonical source URL (the original article link, which is the
    strongest "same story" signal already used by ``article_dedup_keys``),
    then the article's own ``source_url`` / ``t.me`` URL, then the news id
    used by the ``news_items`` table. The result is ``nid:<sha256>[:16]`` —
    stable across ticks, restarts and reworded headlines, so the same story
    always resolves to one key no matter which lane holds it.

    Falls back to a hash of the title when nothing else is available, so a
    source that exposes no URL still dedups.
    """
    url = ""
    for attr in ("canonical_url", "resolved_url", "source_url", "url"):
        value = getattr(article, attr, None)
        if isinstance(value, str) and value.strip():
            url = value.strip()
            break
    if url:
        return f"nid:{hashlib.sha256(url.encode('utf-8', errors='ignore')).hexdigest()[:16]}"
    title = getattr(article, "title", None) or ""
    if isinstance(title, str) and title.strip():
        return f"tid:{hashlib.sha256(title.strip().lower().encode('utf-8', errors='ignore')).hexdigest()[:16]}"
    news_id = getattr(article, "news_id", None) or ""
    return f"nid:{hashlib.sha256(str(news_id).encode('utf-8', errors='ignore')).hexdigest()[:16]}"


def _redis_key(dest: str) -> str:
    return f"{_LEDGER_PREFIX}{dest}"


def _client() -> Any:
    """Async Redis client, or ``None`` (never raises)."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        return get_redis_async()
    except Exception:
        return None


async def is_published(dest: str, key: str) -> bool:
    """True when *key* was already published to *dest*.

    Checked BEFORE a Telegram post AND before a feed-page post. Fail-open:
    Redis unavailable → False (allow the publish, so an outage never silences
    the channel permanently).
    """
    if not key:
        return False
    rkey = _redis_key(dest)
    client = _client()
    if client is not None:
        try:
            return bool(await client.sismember(rkey, key))
        except Exception as exc:  # fail-open by contract
            logger.debug("ledger:check_failed dest=%s err=%s", dest, str(exc)[:80])
    return key in _in_memory.get(dest, set())


async def mark_published(dest: str, key: str) -> None:
    """Record *key* as published to *dest*. Never raises.

    Called only AFTER a successful publish. Fail-open: Redis unavailable →
    the in-process set still stops an immediate re-post inside this process.
    """
    if not key:
        return
    rkey = _redis_key(dest)
    client = _client()
    if client is not None:
        try:
            await client.sadd(rkey, key)
            await client.expire(rkey, LEDGER_TTL_SECONDS)
            return
        except Exception as exc:  # fail-open — record in memory instead
            logger.debug("ledger:mark_failed dest=%s err=%s", dest, str(exc)[:80])
    _in_memory.setdefault(dest, set()).add(key)


__all__ = ["content_hash", "evaluate_report", "is_duplicate_recent", "is_published", "item_key", "mark_published", "record_publication", "select_edition_image"]
