"""Personal channel consumer — sweep → rerank gate → operator-voice posts.

Extracted verbatim from the former single-module ``channels/personal_channel.py``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from rugmunchbot.channels.importance import (
    IMPORTANCE_THRESHOLD,
    score_importance,
)
from rugmunchbot.channels.personal_channel._config import (
    PERSONAL_CHANNEL_GHOST_ENABLED,
    PERSONAL_CHANNEL_THRESHOLD,
)
from rugmunchbot.channels.personal_channel._content import (
    build_caption,
    select_personal,
)
from rugmunchbot.channels.personal_channel._ghost import publish_to_ghost
from rugmunchbot.channels.telegram_news import sweep_telegram_news
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.channels.personal")


def resolve_personal_chat_id() -> str | None:
    """Resolve the personal-channel chat id.

    Priority: ``RMI_PERSONAL_CHANNEL_ID`` env (dedicated override) → the
    registry ``main`` role (secrets-store ``CHANNEL_CRYPTO_RUGMUNCHER``
    → ``RMI_MAIN_CHANNEL_ID`` → documented fallback) via the bot config's
    memoized resolver. Returns ``None`` when nothing resolved.
    """
    dedicated = os.getenv("RMI_PERSONAL_CHANNEL_ID", "").strip()
    if dedicated:
        return dedicated
    try:
        from rugmunchbot.config import _resolve_channel_id

        return _resolve_channel_id("main")
    except Exception:  # fail-open: config import must not crash the job
        logger.warning("personal:chat_id_resolution_failed", exc_info=True)
        return None


async def post_personal_channel(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Sweep → rerank (stricter gate) → post operator-voice captions → Ghost.

    Returns the number of stories posted (0-3). Fails open at every step:
    missing chat id, empty sweep, reranker outage, Telegram send error, or
    Ghost outage all log and return without raising. Only stories above
    ``RMI_PERSONAL_CHANNEL_THRESHOLD`` (or the top-N under the heuristic
    fallback) are posted — the "bigger stories".
    """
    # Lazy import keeps the scheduler ↔ this module import graph acyclic
    # (scheduler.py imports this module at module scope for registration).
    from rugmunchbot.channels.scheduler import (
        _dedupe_key,
        _remember,
        _safe_send,
    )

    chat_id = resolve_personal_chat_id()
    if not chat_id:
        logger.info("personal:no_chat_id_skipped")
        return 0

    articles = await sweep_telegram_news()
    if not articles:
        logger.debug("personal:no_fresh_articles")
        return 0

    if PERSONAL_CHANNEL_THRESHOLD < IMPORTANCE_THRESHOLD:
        logger.warning(
            "personal:threshold_not_stricter personal=%s news=%s — personal gate below news gate",
            PERSONAL_CHANNEL_THRESHOLD,
            IMPORTANCE_THRESHOLD,
        )

    scored = await score_importance(articles)
    selected = select_personal(scored)
    if not selected:
        logger.info(
            "personal:importance_gate_none_passed articles=%s threshold=%s fallback=%s",
            len(articles),
            PERSONAL_CHANNEL_THRESHOLD,
            any(s.fallback for s in scored),
        )
        return 0
    logger.info(
        "personal:importance_selected selected=%s of %s threshold=%s news_threshold=%s fallback=%s",
        len(selected),
        len(scored),
        PERSONAL_CHANNEL_THRESHOLD,
        IMPORTANCE_THRESHOLD,
        any(s.fallback for s in scored),
    )

    posted = 0
    for scored_article in selected:
        article = scored_article.article
        key = _dedupe_key("personal", {"url": article.source_url, "title": article.title})
        if not _remember("personal", key):
            continue
        caption = build_caption(article)
        ok = await _safe_send(ctx.bot, chat_id, caption)
        if not ok:
            continue
        posted += 1
        logger.info(
            "personal:posted title=%s score=%s query=%s",
            article.title[:60],
            scored_article.importance,
            scored_article.best_query,
        )
        if PERSONAL_CHANNEL_GHOST_ENABLED:
            await publish_to_ghost(article, caption)
    if posted:
        logger.info("personal:tick_done posted=%s", posted)
    return posted
