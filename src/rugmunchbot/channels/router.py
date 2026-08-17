"""Pure news-routing decision — every scored story goes to EXACTLY ONE destination.

The tg_news post path scores each swept article with the reranker
(``channels/importance.py``) and asks :func:`route_news` where the story
belongs. The returned destination maps to a chat id in the scheduler
(``channels/scheduler.py::_route_chat_id``):

* ``"skip"``  — importance below the news floor (default 0.4): don't post
* ``"news"``  — news-band importance (default 0.4-0.7): the news channel
* ``"main"``  — importance at/above the main gate (default 0.7): the
                operator's @cryptorugmuncher channel
* ``"alpha"`` — alpha-category story below the main gate: the alpha channel

Precedence matters: ``"main"`` always wins over ``"alpha"`` (an alpha-category
story that also clears the main gate is a main-channel story), and the alpha
category wins over the news band (alpha-category stories never land on the
news channel).

This module is intentionally **pure** — no network, no DB, no Telegram. The
thresholds are module constants read once at import from env (matching the
``channels/importance.py`` pattern); the decision function itself does no I/O.

Env knobs (read once at import, overridable):
* ``RMI_ROUTE_NEWS_MIN_IMPORTANCE`` — news-band floor (default 0.4)
* ``RMI_ROUTE_MAIN_MIN_IMPORTANCE`` — main-channel gate (default 0.7)
"""

from __future__ import annotations

import os

#: Default news-band floor — below this a story is skipped. Matches the news
#: channel's ``channels/importance.py::IMPORTANCE_THRESHOLD`` default so the
#: routed bands and the legacy gate agree.
ROUTE_NEWS_MIN_IMPORTANCE_DEFAULT: float = 0.4
#: Default main-channel gate — at/above this a story is a "bigger story" for
#: the operator's @cryptorugmuncher channel (mirrors
#: ``personal_channel.py::PERSONAL_CHANNEL_THRESHOLD`` default).
ROUTE_MAIN_MIN_IMPORTANCE_DEFAULT: float = 0.7
#: Swept category string that routes a story to the alpha channel (matches
#: the ``classify_channel_category`` "alpha" bucket in ``telegram_news``).
ROUTE_ALPHA_CATEGORY: str = "alpha"


def _env_float(name: str, default: float) -> float:
    """Clamped 0..1 float env helper (same semantics as importance.py)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0.0, min(1.0, float(raw.strip())))
    except (TypeError, ValueError):
        return default


#: News-band floor (env ``RMI_ROUTE_NEWS_MIN_IMPORTANCE``).
ROUTE_NEWS_MIN_IMPORTANCE: float = _env_float("RMI_ROUTE_NEWS_MIN_IMPORTANCE", ROUTE_NEWS_MIN_IMPORTANCE_DEFAULT)
#: Main-channel gate (env ``RMI_ROUTE_MAIN_MIN_IMPORTANCE``).
ROUTE_MAIN_MIN_IMPORTANCE: float = _env_float("RMI_ROUTE_MAIN_MIN_IMPORTANCE", ROUTE_MAIN_MIN_IMPORTANCE_DEFAULT)


def route_news(importance: float, category: str) -> str:
    """Route one scored story to exactly one destination.

    Returns ``"skip"`` / ``"news"`` / ``"main"`` / ``"alpha"``.

    Bands (defaults, env-overridable):
    * importance < ``ROUTE_NEWS_MIN_IMPORTANCE`` (0.4)        → ``"skip"``
    * ``ROUTE_NEWS_MIN_IMPORTANCE`` ≤ importance
      < ``ROUTE_MAIN_MIN_IMPORTANCE`` (0.4-0.7), not alpha     → ``"news"``
    * importance ≥ ``ROUTE_MAIN_MIN_IMPORTANCE`` (0.7)         → ``"main"``
    * category == ``ROUTE_ALPHA_CATEGORY`` (alpha) and
      importance < ``ROUTE_MAIN_MIN_IMPORTANCE``               → ``"alpha"``

    ``"main"`` always beats ``"alpha"``: an alpha-category story that also
    clears the main gate is a main-channel story, never an alpha one.
    """
    if importance >= ROUTE_MAIN_MIN_IMPORTANCE:
        return "main"
    if category == ROUTE_ALPHA_CATEGORY:
        return "alpha"
    if importance >= ROUTE_NEWS_MIN_IMPORTANCE:
        return "news"
    return "skip"


__all__ = [
    "ROUTE_ALPHA_CATEGORY",
    "ROUTE_MAIN_MIN_IMPORTANCE",
    "ROUTE_MAIN_MIN_IMPORTANCE_DEFAULT",
    "ROUTE_NEWS_MIN_IMPORTANCE",
    "ROUTE_NEWS_MIN_IMPORTANCE_DEFAULT",
    "route_news",
]
