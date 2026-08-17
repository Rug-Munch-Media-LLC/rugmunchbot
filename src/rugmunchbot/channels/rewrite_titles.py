"""LLM title rewriting for the TG-news posted card (env-gated, fail-open).

Submodule of ``channels/`` — owns the optional LLM headline rewrite that
humanizes a raw channel headline before it is posted to the news/alpha
channel. It is **not** part of persistence: the sweep stores the rewritten
title on ``NewsArticle.post_title`` and the sync post-dict builder
(``channels/telegram_news/_digest._article_to_post_dict``) prefers it, while
``NewsArticle.title`` (and therefore ``news_items``) keeps the original
headline.

Env gate: ``RMI_NEWS_TITLE_REWRITE_ENABLED`` (default ``"0"`` = off). When
disabled, the LLM errors, or a single rewrite exceeds 5s, the original title
is returned unchanged. The bot's existing tier-1 LLM client
(``handlers/chat_ai/_llm._call_backend_chat``) is reused — it already ships
``PERSONA_CONTEXT`` (``prompts.py``) via ``context_override``, so the rewrite
stays in the bot's canonical voice without duplicating the persona prompt.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Final

# PERSONA_CONTEXT is a contract pin: the rewrite reuses the bot's tier-1
# client (_call_backend_chat), which already ships it via context_override.
from app.domains.telegram.rugmunchbot.prompts import PERSONA_CONTEXT  # noqa: F401
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.tg_news")

#: Env gate — off by default (opt-in feature).
TITLE_REWRITE_ENABLED: bool = os.getenv("RMI_NEWS_TITLE_REWRITE_ENABLED", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

#: Hard deadline for ONE rewrite (task contract: >5s → return original).
_TITLE_REWRITE_TIMEOUT: Final = 5.0

#: Title length cap — mirrors ``normalize._TITLE_MAX_CHARS``.
_TITLE_MAX_CHARS: Final = 120

#: Rewrite instruction — output ONLY the new title, no markdown, no quotes.
_REWRITE_PROMPT: Final = (
    "Rewrite the following crypto-news headline into a concise, human-sounding, "
    "non-clickbait title. Rules: no ALL-CAPS, no emoji, no quotes, no leading "
    "'BREAKING:'/'URGENT' prefix, keep the crypto tone, at most 12 words, plain "
    "text only. Output ONLY the rewritten title.\n\nHeadline: {title}"
)

#: Characters the model may wrap the title in — stripped before acceptance.
_STRIP_CHARS_RE: Final = re.compile(r"[\"'\u201c\u201d\u2018\u2019*_`]")
_WHITESPACE_RE: Final = re.compile(r"\s+")


def _clean_rewritten(raw: object) -> str:
    """Normalize a model response into a usable title (never raises)."""
    text = str(raw or "").strip()
    text = _STRIP_CHARS_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:_TITLE_MAX_CHARS].rstrip()


async def rewrite_news_title(title: str) -> str:
    """Rewrite *title* into a concise, human-sounding headline (fail-open).

    Returns the original *title* unchanged when:
      * ``RMI_NEWS_TITLE_REWRITE_ENABLED`` is off (default);
      * the bot's backend chat client is unavailable or errors;
      * a single rewrite exceeds ``_TITLE_REWRITE_TIMEOUT`` (5s);
      * the model returns an empty / no-op result.

    Never raises. Reuses ``_call_backend_chat`` (the bot's existing tier-1
    LLM client), which already injects ``PERSONA_CONTEXT`` from
    ``prompts.py`` via ``context_override``.
    """
    original = (title or "").strip()
    if not original or not TITLE_REWRITE_ENABLED:
        return original
    try:
        # Lazy import keeps this module import-light (no chat_ai deps at
        # module scope) and mirrors the codebase's lazy-client pattern.
        from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm import _call_backend_chat

        result = await asyncio.wait_for(
            # record_usage=False: channel title rewrites are NOT chat calls —
            # keep the owner /admin model-usage counters chat-pipeline-only.
            _call_backend_chat(_REWRITE_PROMPT.format(title=original), user_id=0, record_usage=False),
            timeout=_TITLE_REWRITE_TIMEOUT,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("tg_news:title_rewrite_unavailable err=%s", str(exc)[:200])
        return original
    if not isinstance(result, dict):
        return original
    rewritten = _clean_rewritten(result.get("message"))
    if not rewritten or rewritten.lower() == original.lower():
        return original
    return rewritten


__all__ = ["TITLE_REWRITE_ENABLED", "rewrite_news_title"]
