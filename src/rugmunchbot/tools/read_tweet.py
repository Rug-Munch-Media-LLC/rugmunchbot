"""read_tweet tool — X-post reader (X intel owned by rugbrain/RAG).

X ingestion and fetching are owned by **rugbrain** (the RAG service). The
bot no longer implements X fetching (the ApiPass rail is retired); it may
consume X-derived intel through the RAG HTTP API (``tools/rag_lookup.py``).

``read_tweet`` is retained as a fail-open tool: it returns ``ok=False`` with
an honest reason — it NEVER fabricates tweet content and never scrapes X.

Target parsing (shared with the ``/tweet`` command and the intent router):
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
    "X reading is owned by rugbrain (RAG) — the bot no longer fetches X posts "
    "directly. X-derived intel is available through the RAG API. This tool "
    "fails open by design and never scrapes X."
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
    """Read an X post — retired: X reading is owned by rugbrain (RAG).

    Args:
        url: An X post URL (``https://x.com/foo/status/<id>``).
        tweet_id: Bare numeric tweet id.
        handle: X handle (``@foo`` or ``foo``).
        **ctx: Ignored shared context.

    Returns:
        ``ToolResult`` — always ``ok=False`` with an honest reason: the bot
        no longer fetches X posts (rugbrain owns X ingestion). Never
        fabricates tweet content and never scrapes X.
    """
    target = (url or tweet_id or handle or "").strip()
    if not target:
        return ToolResult(tool="read_tweet", ok=False, error="x url, tweet id, or handle required")
    return ToolResult(tool="read_tweet", ok=False, error=_UNAVAILABLE, source="rugbrain_rag")


__all__ = [
    "parse_target",
    "read_tweet",
]
