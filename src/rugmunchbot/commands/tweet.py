"""X post reader — /tweet & /tweets (X intel owned by rugbrain/RAG).

X ingestion and fetching are owned by **rugbrain** (the RAG service). The
bot no longer implements X fetching (the ApiPass rail is retired); it may
consume X-derived intel through the RAG HTTP API (``tools/rag_lookup.py``).

This command is retained as a fail-open notice so users get an honest
answer instead of a silent failure. It never scrapes X directly and never
fabricates tweet content.

Usage:
    /tweet <x-post-url-or-id>  — read ONE X post
    /tweet <@handle>           — recent posts from an account
    /tweets <@handle>          — alias that forces handle mode
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.tweet_cmd")

#: User-facing note: X reading moved to rugbrain (RAG).
_MOVED_NOTE = (
    "⚠️ <b>X reading has moved.</b>\n\n"
    "X/Twitter ingestion is now owned by <b>rugbrain</b> (our RAG service). "
    "The bot no longer fetches X posts directly — it consumes X-derived "
    "intel through the RAG API. This command is retired and never scrapes X."
)

_USAGE = (
    "📰 <b>Read an X post</b>\n\n"
    "X reading is now handled by rugbrain (RAG). This command is retired.\n\n"
    "Usage (historical):\n"
    "• <code>/tweet &lt;x-post-url&gt;</code> — read one post\n"
    "• <code>/tweet &lt;tweet-id&gt;</code> — read by numeric id\n"
    "• <code>/tweet @handle</code> — recent posts from an account\n"
    "• <code>/tweets @handle</code> — same, handle mode"
)


def _esc(text: Any) -> str:
    return html.escape(str(text or ""), quote=False)


def format_tweet(tweet: dict[str, Any]) -> str:
    """One normalized tweet → compact HTML card (safe, no fabrication)."""
    handle = str(tweet.get("handle") or "unknown")
    name = str(tweet.get("author_name") or handle)
    text = str(tweet.get("text") or "").strip()
    if len(text) > 500:
        text = text[:497] + "…"
    url = str(tweet.get("url") or "")

    lines = [
        f"🐦 <b>@{_esc(handle)}</b> · {_esc(name)}",
        "",
        _esc(text),
    ]
    metrics: list[str] = []
    if tweet.get("like_count"):
        metrics.append(f"👍 {int(tweet['like_count']):,}")
    if tweet.get("retweet_count"):
        metrics.append(f"🔁 {int(tweet['retweet_count']):,}")
    if tweet.get("reply_count"):
        metrics.append(f"💬 {int(tweet['reply_count']):,}")
    if metrics:
        lines.append("")
        lines.append(" · ".join(metrics))
    if tweet.get("created_at"):
        lines.append(f"🕐 {_esc(str(tweet['created_at'])[:40])}")
    if url:
        lines.append(f"🔗 {_esc(url)}")
    return "\n".join(lines)


def format_tweet_list(tweets: list[dict[str, Any]], handle: str, limit: int = 5) -> str:
    """Recent posts from one account → compact list card."""
    shown = tweets[: max(1, min(int(limit), len(tweets)))]
    lines = [f"🐦 <b>Recent posts from @{_esc(handle)}</b> (latest {len(shown)}):", ""]
    for t in shown:
        text = str(t.get("text") or "").strip().replace("\n", " ")[:180]
        author = str(t.get("handle") or handle)
        likes = int(t.get("like_count") or 0)
        rts = int(t.get("retweet_count") or 0)
        url = str(t.get("url") or "")
        lines.append(f"• <b>@{_esc(author)}</b> — {_esc(text)}")
        lines.append(f"  👍{likes:,} 🔁{rts:,} · {_esc(url)}")
    return "\n".join(lines)


async def cmd_tweet(update: Any, context: Any) -> None:
    """Handle /tweet — retired: X reading is owned by rugbrain (RAG)."""
    msg = update.effective_message
    if msg is None:
        return
    raw = " ".join(getattr(context, "args", None) or []).strip()
    if not raw:
        await msg.reply_text(_USAGE, parse_mode="HTML")
        return
    await msg.reply_text(_MOVED_NOTE, parse_mode="HTML")


async def cmd_tweets(update: Any, context: Any) -> None:
    """Handle /tweets — alias documenting handle mode (shares /tweet logic)."""
    await cmd_tweet(update, context)


__all__ = ["cmd_tweet", "cmd_tweets", "format_tweet", "format_tweet_list"]
