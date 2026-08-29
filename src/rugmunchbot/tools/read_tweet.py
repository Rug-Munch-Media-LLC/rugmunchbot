"""read_tweet tool — the chat loop's X-post reader via ApiPass (never scrape).

The bot reads X posts EXCLUSIVELY through the ApiPass X source
(``rugmunchbot.tools.apipass_x``): one key, real tweet payloads, and a hard
≤6 task-creations/hr budget (``RMI_APIPASS_MAX_CALLS_PER_HOUR``). No nitter
mirrors, no web-extract of x.com pages, no guest-API scraping — ApiPass only.

Fail-open everywhere: missing key, spent budget, failed task, or an
unprovisioned ``twitter-api-simple`` model all return ``ok=False`` with an
honest reason — the tool NEVER fabricates tweet content, and the chat layer
answers "I can't read X right now" instead of guessing.

Target parsing (shared with the ``/tweet`` command):
  * ``https://x.com/foo/status/123`` / ``https://twitter.com/foo/status/123``
    (incl. ``/i/status/``) → tweet-detail by id
  * bare numeric id (6-25 digits)                    → tweet-detail by id
  * ``@handle`` / ``handle`` (≤15 chars)             → user-tweets (latest 5)
"""

from __future__ import annotations

import re
from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.tools.read_tweet")

#: X post URL carrying a numeric status id (x.com / twitter.com, incl. /i/status).
_X_STATUS_RE = re.compile(r"https?://(?:x\.com|twitter\.com)/(?:[^/]+)/status/(\d+)", re.IGNORECASE)
#: Bare numeric tweet id (Twitter snowflakes are 15-19 digits).
_TWEET_ID_RE = re.compile(r"^\d{6,25}$")
#: X handle (@ optional; X handles are 1-15 chars of A-Z 0-9 _).
_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9_]{1,15}$")

#: User/chat-facing reason when the X rail is unavailable (no fabrication).
_UNAVAILABLE = (
    "ApiPass X rail unavailable: no key configured, the hourly budget is spent, "
    "or the twitter-api-simple model is not enabled on the account. The bot "
    "never scrapes X directly — this tool fails open by design (operator: see "
    "docs/TROUBLESHOOTING.md → ApiPass)."
)


def parse_target(text: str) -> dict[str, str]:
    """Classify input → ``{"tweet_id"} | {"handle"}`` (one key, empty on miss).

    URL and numeric-id inputs resolve to a tweet id (``fetch_tweet_detail``);
    a handle resolves to the account's recent posts (``fetch_user_tweets``).
    """
    s = (text or "").strip()
    if not s:
        return {}
    m = _X_STATUS_RE.search(s)
    if m:
        return {"tweet_id": m.group(1)}
    if _TWEET_ID_RE.match(s):
        return {"tweet_id": s}
    if s.isdigit():
        # All-numeric input that isn't a valid tweet id is not a handle.
        return {}
    if s.lower().startswith(("http://", "https://")):
        # A non-X URL is not an X post — unsupported here.
        return {}
    if _HANDLE_RE.match(s):
        return {"handle": s.lstrip("@")}
    return {}


async def read_tweet(
    *, url: str = "", tweet_id: str = "", handle: str = "", **ctx: Any
) -> ToolResult:
    """Read an X post (URL or numeric id) or an account's recent posts.

    Args:
        url: An X post URL (``https://x.com/foo/status/<id>``).
        tweet_id: Bare numeric tweet id.
        handle: X handle (``@foo`` or ``foo``) — returns the latest posts.
        **ctx: Ignored shared context.

    Returns:
        ``ToolResult`` — ``ok=True`` with the normalized tweet dict (single
        post) or a list of tweet dicts (handle mode). ``ok=False`` with an
        honest reason when the X rail is unavailable.
    """
    target = (url or tweet_id or handle or "").strip()
    if not target:
        return ToolResult(tool="read_tweet", ok=False, error="x url, tweet id, or handle required")

    from rugmunchbot.tools import apipass_x as ax

    if not ax.api_key_configured():
        return ToolResult(
            tool="read_tweet",
            ok=False,
            error="no ApiPass key configured — X reading is off (never scrapes)",
            source="apipass_x",
        )

    parsed = parse_target(target)
    tid = parsed.get("tweet_id", "")
    if tid:
        tweet = await ax.fetch_tweet_detail(tid)
        if not tweet:
            return ToolResult(tool="read_tweet", ok=False, error=_UNAVAILABLE, source="apipass_x")
        return ToolResult(tool="read_tweet", ok=True, data=tweet, source="apipass_x")

    handle_clean = parsed.get("handle", "")
    if not handle_clean:
        return ToolResult(
            tool="read_tweet",
            ok=False,
            error="couldn't parse that as an X post URL, tweet id, or @handle",
        )
    tweets = await ax.fetch_user_tweets(handle_clean, limit=5)
    if not tweets:
        return ToolResult(tool="read_tweet", ok=False, error=_UNAVAILABLE, source="apipass_x")
    return ToolResult(tool="read_tweet", ok=True, data=tweets, source="apipass_x")


__all__ = [
    "parse_target",
    "read_tweet",
]
