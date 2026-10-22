"""`/desk` — the latest write-up from the desk, with source and timestamp.

One source of truth for content (2026-10-23): the desk reads the SAME static
store the site feed and the channel posters write (``posts.json`` under
``RMI_FEED_DIR``), and renders through the SAME link primitives
(``feed_site_line`` / ``site_news_line`` from
``channels.formatters._primitives``). A story therefore cannot exist on
feed.rugmunch.io but be invisible in the bot, and the Telegram card and the
site card carry identical attribution.

Pure render helpers (no Telegram API) so the dry harness and the tests can
render a fixture store without a network or a bot token.
"""

from __future__ import annotations

import html
import json
import os
import re
from typing import Any

from rugmunchbot.channels.formatters._primitives import feed_site_line, site_news_line

DEFAULT_STORE = os.path.join(
    os.getenv("RMI_FEED_DIR", "/var/www/feed.rugmunch.io"), "data", "posts.json"
)

#: How many stories `/desk` shows. Three is one phone screen.
DESK_LIMIT = 3

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _plain(text: str) -> str:
    """Strip HTML tags/entities to a single-spaced plain-text line."""
    cleaned = html.unescape(_TAG_RE.sub(" ", text or ""))
    return _WS_RE.sub(" ", cleaned).strip()


_BOLD_RE = re.compile(r"<b>(.*?)</b>", re.DOTALL)


def _headline(post: dict[str, Any]) -> str:
    """Best available headline: explicit title, bold lead sentence, first line.

    Posters open with either ``<b>Exploit: ...</b>`` or a separator banner
    (``━━━ Midday Market Report ━━━``). Taking the first *bold* segment (and
    falling back to the first line) avoids pasting the whole story body into
    the headline slot, which is what a naive first-line read produced.
    """
    for key in ("title", "headline"):
        value = _plain(str(post.get(key) or ""))
        if value:
            return value[:110]
    raw = str(post.get("text") or "")
    bold = _BOLD_RE.search(raw)
    lead = _plain(bold.group(1) if bold else raw)
    lead = lead.strip("\u2501 \u2014-\u00b7").strip()
    if not lead:
        lines = [line for line in raw.splitlines() if _plain(line)]
        lead = _plain(lines[0]) if lines else ""
    return (lead or "Untitled")[:110]


def _source_line(post: dict[str, Any]) -> str:
    """Attribution: the story's own source when it is a real URL, else kind."""
    source = str(post.get("source") or "").strip()
    if source.startswith(("http://", "https://")):
        return f'<a href="{html.escape(source, quote=True)}">source</a>'
    kind = _plain(str(post.get("kind") or "desk")).replace("_", " ")
    return f"<i>{html.escape(kind)}</i>"


def load_latest_posts(limit: int = DESK_LIMIT, path: str | None = None) -> list[dict[str, Any]]:
    """Newest ``limit`` posts from the feed store (newest first).

    Tolerant reader: the store is a top-level array in production but was
    seeded as ``{"posts": []}``, and a partially-written file must never take
    the command down. Returns ``[]`` on any failure — the caller degrades to
    the site links instead of an error.
    """
    store = path or DEFAULT_STORE
    try:
        with open(store) as fh:
            loaded = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    posts = loaded.get("posts") if isinstance(loaded, dict) else loaded
    if not isinstance(posts, list):
        return []
    usable = [p for p in posts if isinstance(p, dict) and (p.get("text") or p.get("title"))]
    # Chronological by ``ts`` when every candidate carries one (the feed store
    # is append-ordered, but a re-mirrored or backfilled post must not jump to
    # the top); otherwise trust the store order and reverse it.
    stamped = [p for p in usable if str(p.get("ts") or "")]
    if len(stamped) == len(usable):
        usable.sort(key=lambda p: str(p.get("ts")), reverse=True)
    else:
        usable = list(reversed(usable))
    return usable[: max(0, limit)]


def render_desk(posts: list[dict[str, Any]], limit: int = DESK_LIMIT) -> str:
    """Render the desk card (HTML). Every story carries source + timestamp + link."""
    lines = ["\U0001f4f0 <b>The Desk \u2014 latest intelligence</b>", "\u2501" * 26]
    if not posts:
        lines.append(
            "\nThe desk has not filed yet.\n"
            "Every story lands on the site first \u2014 the feed below is live."
        )
        lines.append(f"\n{feed_site_line()}\n{site_news_line()}")
        return "\n".join(lines)
    for index, post in enumerate(posts[:limit], 1):
        stamp = _plain(str(post.get("ts") or post.get("published_at") or ""))
        # ISO timestamp → "2026-10-22 16:30Z" so the date stays readable and
        # sortable without pulling in a date library.
        stamp = stamp.replace("T", " ")[:16].rstrip("Z") + "Z" if stamp else "undated"
        slug = str(post.get("slug") or "").strip()
        lines.append(f"\n<b>{index}. {html.escape(_headline(post))}</b>")
        body = _plain(str(post.get("text") or ""))
        # Do not repeat the headline as the excerpt (bold-led posts would
        # otherwise print the same sentence twice).
        headline = _plain(_headline(post))
        if body.startswith(headline):
            body = body[len(headline) :].lstrip(" \u2014-\u00b7:")
        if body:
            excerpt = body[:180] + ("\u2026" if len(body) > 180 else "")
            lines.append(html.escape(excerpt))
        lines.append(f"{stamp} \u00b7 {_source_line(post)}")
        lines.append(feed_site_line(slug) if slug else site_news_line())
    lines.append(
        "\n\u2501\u2501\u2501\nWant the full story? <b>/news</b> \u00b7 "
        "<b>/research &lt;token&gt;</b> \u00b7 <b>/chat</b> to ask the desk"
    )
    return "\n".join(lines)


async def cmd_desk(update: Any, _ctx: Any) -> None:
    """/desk — latest write-ups with source, timestamp and a Read-more link."""
    message = getattr(update, "effective_message", None)
    if message is None:
        return
    await message.reply_text(
        render_desk(load_latest_posts()),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


__all__ = ["DESK_LIMIT", "cmd_desk", "load_latest_posts", "render_desk"]
