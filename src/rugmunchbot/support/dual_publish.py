"""Dual-surface publish — one story to Telegram AND the site feed.

CONTENT directive (2026-10-16): content (the Daily Rug + channel posts) must
land on BOTH the Telegram channel and the site/feed — never Telegram only.
This module is the single chokepoint that guarantees it:

* **Dedupe across BOTH surfaces** — one stable ``story_ref`` is the identity.
  Telegram dedupe rides the existing global ``_remember`` set; the feed store
  dedupes on ``source == story_ref``. A story that already posted to either
  surface is not re-posted to either.
* **Site-down tolerance** — a failed feed mirror is appended to a durable
  retry queue (``feed_retry_queue.json`` next to the feed store) and drained
  on the next publish. The Telegram post is never blocked by the site.
* **Status line** — every publish returns a one-line status: what posted
  where (``Telegram ✅ / site ✅`` or ``site ⏳ queued``).

Fail-open everywhere: a broken lane logs and degrades, never raises into the
poster. The Telegram send is the caller's responsibility (it owns the bot);
this module owns the feed half + the status contract.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("rmi_bot.support.dual_publish")

#: Retry queue lives beside the feed store so it survives restarts and is
#: visible to the same operator tooling that reads posts.json.
_QUEUE_FILENAME = "feed_retry_queue.json"
_MAX_QUEUE = 200
_MAX_ATTEMPTS = 5


@dataclass
class PublishStatus:
    """What posted where — the status line contract."""

    telegram: bool = False
    site: bool = False
    queued: bool = False
    slug: str = ""
    feed_url: str = ""
    detail: str = ""
    errors: list[str] = field(default_factory=list)

    def line(self) -> str:
        """One-line status: ``Telegram ✅ / site ✅`` or ``site ⏳ queued``."""
        tg = "✅" if self.telegram else "❌"
        if self.site:
            site = "✅"
        elif self.queued:
            site = "⏳ queued"
        else:
            site = "❌"
        out = f"Telegram {tg} / site {site}"
        if self.slug:
            out += f" ({self.slug})"
        if self.errors:
            out += f" — {self.errors[0][:120]}"
        return out


def _feed_data_dir() -> str:
    """Resolve the feed store dir the same way ``_posts_rag`` does."""
    try:
        from rugmunchbot.channels.scheduler._posts_rag import FEED_DATA

        return FEED_DATA
    except Exception:
        return os.path.join(os.getenv("RMI_FEED_DIR", ""), "data")


def _queue_path() -> str:
    return os.path.join(_feed_data_dir(), _QUEUE_FILENAME)


def _load_queue() -> list[dict[str, Any]]:
    path = _queue_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            loaded = json.load(f)
        return list(loaded) if isinstance(loaded, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("dual_publish:queue_read_failed err=%s", str(exc)[:120])
        return []


def _save_queue(items: list[dict[str, Any]]) -> None:
    path = _queue_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(items[-_MAX_QUEUE:], f, indent=1)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("dual_publish:queue_write_failed err=%s", str(exc)[:120])


def _enqueue(item: dict[str, Any]) -> None:
    """Append a failed feed item to the durable retry queue (deduped by ref)."""
    ref = str(item.get("story_ref") or item.get("source") or "")
    queue = [q for q in _load_queue() if str(q.get("story_ref") or "") != ref]
    entry = dict(item)
    entry["_attempts"] = int(item.get("_attempts", 0)) + 1
    entry["_queued_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    queue.append(entry)
    _save_queue(queue)


def _mirror(item: dict[str, Any]) -> str | None:
    """Call the shared feed mirror. Returns slug or None (fail-open)."""
    from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

    return mirror_post_to_feed(item)


def drain_retry_queue(*, limit: int = 10) -> int:
    """Retry queued feed items. Returns the count that landed.

    Called at the top of every publish so a site outage self-heals: once the
    feed is reachable again, the backlog flushes. Items that exceed
    ``_MAX_ATTEMPTS`` are dropped (logged) so the queue cannot grow forever.
    """
    queue = _load_queue()
    if not queue:
        return 0
    remaining: list[dict[str, Any]] = []
    landed = 0
    for item in queue[:limit]:
        attempts = int(item.get("_attempts", 0))
        if attempts >= _MAX_ATTEMPTS:
            logger.warning(
                "dual_publish:queue_drop ref=%s attempts=%d",
                str(item.get("story_ref") or "")[:60],
                attempts,
            )
            continue
        slug = _mirror(item)
        if slug:
            landed += 1
            logger.info(
                "dual_publish:queue_flushed ref=%s slug=%s",
                str(item.get("story_ref") or "")[:60],
                slug,
            )
        else:
            item["_attempts"] = attempts + 1
            remaining.append(item)
    remaining.extend(queue[limit:])
    _save_queue(remaining)
    return landed


def publish_to_site(
    item: dict[str, Any],
    *,
    status: PublishStatus | None = None,
) -> PublishStatus:
    """Mirror one item to the site feed, queueing on failure.

    ``item`` carries ``text``, ``story_ref``, ``kind``, ``title``, ``tags``,
    ``image_url`` — the same shape ``mirror_post_to_feed`` consumes. Returns
    the (mutated) :class:`PublishStatus` so the caller can render the line.
    """
    st = status or PublishStatus()
    ref = str(item.get("story_ref") or item.get("source") or "")
    if not ref:
        st.errors.append("missing story_ref")
        return st
    try:
        slug = _mirror(item)
    except Exception as exc:  # fail-open: site outage must never break posting
        slug = None
        st.errors.append(str(exc))
        logger.warning("dual_publish:site_publish_failed ref=%s err=%s", ref[:60], str(exc)[:160])
    if slug:
        st.site = True
        st.slug = str(slug)
        st.feed_url = f"https://rugmunch.io/blog/{slug}"
    else:
        _enqueue(item)
        st.queued = True
    return st


def status_line(status: PublishStatus) -> str:
    """Render the status line for logs / operator surfaces."""
    return status.line()
