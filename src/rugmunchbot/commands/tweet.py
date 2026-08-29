"""X post reader — /tweet & /tweets via ApiPass (never direct scrape).

The bot reads X posts EXCLUSIVELY through the ApiPass X source
(``rugmunchbot.tools.apipass_x``): one key, real tweet payloads, and a hard
≤6 task-creations/hr budget (``RMI_APIPASS_MAX_CALLS_PER_HOUR``). No nitter
mirrors, no web-extract of x.com pages, no guest-API scraping — ApiPass only.

Usage:
    /tweet <x-post-url-or-id>  — read ONE X post (tweet-detail)
    /tweet <@handle>           — recent posts from an account (user-tweets)
    /tweets <@handle>          — alias that forces handle mode

Fail-open by design: missing key, spent budget, failed task, or an
unprovisioned ``twitter-api-simple`` model all produce a clean, honest reply
— the command never fabricates tweet content (operator checklist for the
provisioning case: docs/TROUBLESHOOTING.md → ApiPass).
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.read_tweet import parse_target

logger = get_logger("rmi_bot.tweet_cmd")

#: User-facing note when the X rail exists but could not return the post
#: (task failure / model not provisioned). Kept user-safe — no internal
#: paths or secrets; the full operator checklist lives in docs/.
_PROVISIONING_NOTE = (
    "⚠️ <b>Can't read that X post right now.</b>\n\n"
    "The bot reads X through its ApiPass rail, which couldn't return this "
    "post — the usual cause is the <code>twitter-api-simple</code> model not "
    "being enabled on the ApiPass account yet (the key authenticates, but "
    'the model answers a "Model type not found or not configured" 404).\n\n'
    "The operator has the enablement checklist — until it's done this command "
    "fails open and <b>never scrapes X directly</b>. Try again later."
)

_USAGE = (
    "📰 <b>Read an X post via ApiPass</b>\n\n"
    "Usage:\n"
    "• <code>/tweet &lt;x-post-url&gt;</code> — read one post\n"
    "• <code>/tweet &lt;tweet-id&gt;</code> — read by numeric id\n"
    "• <code>/tweet @handle</code> — recent posts from an account\n"
    "• <code>/tweets @handle</code> — same, handle mode\n\n"
    "Example: <code>/tweet https://x.com/foo/status/123456789</code>"
)

_NO_KEY_NOTE = (
    "⚠️ <b>X reading is unavailable right now.</b>\n\n"
    "No ApiPass key is configured on the bot yet, so the X rail is off. The "
    "bot never scrapes X directly — once the operator stores the key this "
    "command starts working."
)

_BUDGET_NOTE_TMPL = (
    "⚠️ <b>ApiPass hourly budget spent.</b>\n\n"
    "We're limited to {max} X reads per hour and that budget is used up. "
    "Try again in about an hour."
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
    """Handle /tweet — read an X post by URL, numeric id, or @handle."""
    msg = update.effective_message
    if msg is None:
        return
    raw = " ".join(getattr(context, "args", None) or []).strip()
    if not raw:
        await msg.reply_text(_USAGE, parse_mode="HTML")
        return

    parsed = parse_target(raw)
    if not parsed:
        await msg.reply_text(
            "❌ <b>Couldn't parse that.</b>\n\n"
            "Give me an X post URL, a numeric tweet id, or an @handle.\n"
            "Example: <code>/tweet https://x.com/foo/status/123456789</code>",
            parse_mode="HTML",
        )
        return

    try:
        from rugmunchbot.tools import apipass_x as ax
    except Exception:
        logger.exception("apipass_x import failed")
        await msg.reply_text(_PROVISIONING_NOTE, parse_mode="HTML")
        return

    if not ax.api_key_configured():
        await msg.reply_text(_NO_KEY_NOTE, parse_mode="HTML")
        return
    if ax.task_creates_remaining() <= 0:
        await msg.reply_text(_BUDGET_NOTE_TMPL.format(max=ax.MAX_CALLS_PER_HOUR), parse_mode="HTML")
        return

    try:
        await msg.chat.send_action("typing")
    except Exception as exc:  # best-effort; never break the read
        logger.debug("tweet_cmd:send_action_failed err=%s", str(exc)[:80])

    if "tweet_id" in parsed:
        tweet = await ax.fetch_tweet_detail(parsed["tweet_id"])
        if not tweet:
            await msg.reply_text(_PROVISIONING_NOTE, parse_mode="HTML")
            return
        await msg.reply_text(format_tweet(tweet), parse_mode="HTML", disable_web_page_preview=True)
        return

    handle = parsed.get("handle", "")
    tweets = await ax.fetch_user_tweets(handle, limit=5)
    if not tweets:
        await msg.reply_text(_PROVISIONING_NOTE, parse_mode="HTML")
        return
    await msg.reply_text(
        format_tweet_list(tweets, handle, limit=5), parse_mode="HTML", disable_web_page_preview=True
    )


async def cmd_tweets(update: Any, context: Any) -> None:
    """Handle /tweets — alias documenting handle mode (shares /tweet logic)."""
    await cmd_tweet(update, context)


__all__ = ["cmd_tweet", "cmd_tweets", "format_tweet", "format_tweet_list"]
