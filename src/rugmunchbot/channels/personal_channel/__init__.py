"""Personal channel (@cryptorugmuncher) — reranker-gated, operator-voice posts.

Modularized from the former single-module ``channels/personal_channel.py``
(2026-08-22). This ``__init__`` is a pure re-export shim: every public name and
signature is unchanged, and callers keep importing
``channels.personal_channel`` exactly as before.

The **personal channel** is the operator's own Telegram surface
(``t.me/cryptorugmuncher`` — registry role ``main``, gopass
``rmi/telegram/CHANNEL_CRYPTO_RUGMUNCHER``). It receives ONLY the "bigger
stories": the same qwen3 ``qwen3-reranker-4b`` importance scoring the
news channel uses (``channels/importance.py``), but gated by a **stricter
threshold** — ``RMI_PERSONAL_CHANNEL_THRESHOLD`` (default 0.7) instead of the
news channel's 0.4. Everything else (cadence, chat id, enabled flag, ghost
cross-post) is env-configurable.

Pipeline (per tick, fail-open at every step):

1. **Sweep** — reuse ``channels/telegram_news.py::sweep_telegram_news`` (same
   headless Telethon read + normalize + dedupe machinery; no second client).
2. **Score** — reuse ``channels/importance.py::score_importance`` (single
   qwen3 reranker; heuristic fallback when the reranker is down).
3. **Gate** — :func:`select_personal` keeps only items at/above the stricter
   threshold. In reranker-down (fallback) mode it keeps the top-N by heuristic
   score (mirrors ``select_important`` semantics) so the channel never goes
   silent during a reranker outage — it only rearranges real swept content.
4. **Post** — one caption per story in the operator's voice (see
   :mod:`_content` below), deduped via the scheduler's in-memory ``_remember``
   set (prefix ``personal``).
5. **Cross-post** — every posted story is pushed to the Ghost blog
   (``blog.rugmunch.io``) via the Ghost **Admin API** client.

Layout:
  * ``_config.py``  — env knobs (master switches, cadence, Ghost Admin API)
  * ``_content.py`` — operator-voice captions + stricter rerank gate
  * ``_ghost.py``   — Ghost Admin API v5 cross-post client (fail-open)
  * ``_telegram.py``— sweep → gate → post consumer + chat-id resolution
"""

from __future__ import annotations

from rugmunchbot.channels.personal_channel._config import (
    GHOST_ADMIN_API_KEY as GHOST_ADMIN_API_KEY,
    GHOST_ADMIN_API_URL as GHOST_ADMIN_API_URL,
    GHOST_CROSSPOST_ENABLED as GHOST_CROSSPOST_ENABLED,
    PERSONAL_CHANNEL_ENABLED as PERSONAL_CHANNEL_ENABLED,
    PERSONAL_CHANNEL_GHOST_ENABLED as PERSONAL_CHANNEL_GHOST_ENABLED,
    PERSONAL_CHANNEL_INTERVAL_MINUTES as PERSONAL_CHANNEL_INTERVAL_MINUTES,
    PERSONAL_CHANNEL_POST_LIMIT as PERSONAL_CHANNEL_POST_LIMIT,
    PERSONAL_CHANNEL_THRESHOLD as PERSONAL_CHANNEL_THRESHOLD,
    _env_bool as _env_bool,
    _env_float as _env_float,
    _env_int as _env_int,
)
from rugmunchbot.channels.personal_channel._content import (
    CAPTION_TEMPLATES as CAPTION_TEMPLATES,
    _lead as _lead,
    build_caption as build_caption,
    select_personal as select_personal,
)
from rugmunchbot.channels.personal_channel._ghost import (
    _article_canonical_url as _article_canonical_url,
    _article_image_url as _article_image_url,
    _lexical_body as _lexical_body,
    ghost_admin_jwt as ghost_admin_jwt,
    ghost_publish_article as ghost_publish_article,
    publish_to_ghost as publish_to_ghost,
)
from rugmunchbot.channels.personal_channel._telegram import (
    post_personal_channel as post_personal_channel,
    resolve_personal_chat_id as resolve_personal_chat_id,
)

__all__ = [
    "CAPTION_TEMPLATES",
    "GHOST_ADMIN_API_KEY",
    "GHOST_ADMIN_API_URL",
    "GHOST_CROSSPOST_ENABLED",
    "PERSONAL_CHANNEL_ENABLED",
    "PERSONAL_CHANNEL_GHOST_ENABLED",
    "PERSONAL_CHANNEL_INTERVAL_MINUTES",
    "PERSONAL_CHANNEL_POST_LIMIT",
    "PERSONAL_CHANNEL_THRESHOLD",
    "build_caption",
    "ghost_admin_jwt",
    "ghost_publish_article",
    "post_personal_channel",
    "publish_to_ghost",
    "resolve_personal_chat_id",
    "select_personal",
]
