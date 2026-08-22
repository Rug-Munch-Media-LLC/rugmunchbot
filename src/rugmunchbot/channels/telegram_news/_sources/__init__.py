"""Telegram news-channel source config, categorization, and sweep (package).

Modularized from the former single-module ``_sources.py`` (2026-08-21).
This ``__init__`` is a pure re-export shim: every public name and signature
is unchanged, and callers keep importing
``channels.telegram_news._sources`` exactly as before.

Module-level state that tests patch (``TG_NEWS_CHANNELS``,
``TG_NEWS_AUTO_DISCOVER``, ``TG_NEWS_MAX_CHANNELS``, ``_build_headless_client``)
stays read through the ``telegram_news`` shim at call time (the ``_tg``
pattern in ``_channels.py`` / ``_sweep.py``) so ``patch.object(tg_news, ...)``
keeps working — see the package docstring of ``telegram_news``.

Layout:
  * ``_config.py``   — env-overridable channel list + category keywords
  * ``_channels.py`` — categorization + registry resolution
  * ``_fetch.py``    — headless client, WAF escalation, OG images
  * ``_tme.py``      — t.me link target resolution
  * ``_sweep.py``    — bounded read → normalize → time-filter → dedupe sweep
"""

from __future__ import annotations

from rugmunchbot.channels.telegram_news._sources._channels import (
    channel_sources as channel_sources,
    classify_channel_category as classify_channel_category,
    resolve_channel_sources as resolve_channel_sources,
)
from rugmunchbot.channels.telegram_news._sources._config import (
    _CHANNEL_ALPHA_KEYWORDS as _CHANNEL_ALPHA_KEYWORDS,
    _CHANNEL_NEWS_KEYWORDS as _CHANNEL_NEWS_KEYWORDS,
    _CHANNEL_OWN_KEYWORDS as _CHANNEL_OWN_KEYWORDS,
    _DEFAULT_CHANNELS as _DEFAULT_CHANNELS,
    TG_NEWS_AUTO_DISCOVER as TG_NEWS_AUTO_DISCOVER,
    TG_NEWS_CHANNELS as TG_NEWS_CHANNELS,
    TG_NEWS_ENABLED as TG_NEWS_ENABLED,
    TG_NEWS_INTERVAL_MINUTES as TG_NEWS_INTERVAL_MINUTES,
    TG_NEWS_MAX_CHANNELS as TG_NEWS_MAX_CHANNELS,
    TG_NEWS_PER_CHANNEL_LIMIT as TG_NEWS_PER_CHANNEL_LIMIT,
    TG_NEWS_PERSIST as TG_NEWS_PERSIST,
    TG_NEWS_POST_LIMIT as TG_NEWS_POST_LIMIT,
    TG_NEWS_RAG as TG_NEWS_RAG,
    TG_NEWS_SWEEP_HOURS as TG_NEWS_SWEEP_HOURS,
    TME_RESOLVE_ENABLED as TME_RESOLVE_ENABLED,
    _env_bool as _env_bool,
    _env_int as _env_int,
    _env_list as _env_list,
    re_split as re_split,
)
from rugmunchbot.channels.telegram_news._sources._fetch import (
    _ESCALATE_STATUS as _ESCALATE_STATUS,
    _FLARE_TIMEOUT as _FLARE_TIMEOUT,
    _OG_HTML_MAX_CHARS as _OG_HTML_MAX_CHARS,
    _OG_IMAGE_RES as _OG_IMAGE_RES,
    _OG_IMAGE_TIMEOUT as _OG_IMAGE_TIMEOUT,
    _WAF_MARKERS as _WAF_MARKERS,
    FLARESOLVERR_URL as FLARESOLVERR_URL,
    _as_utc as _as_utc,
    _build_headless_client as _build_headless_client,
    _fetch_og_image as _fetch_og_image,
    _fetch_page as _fetch_page,
    _fetch_via_flaresolverr as _fetch_via_flaresolverr,
    _inside_window as _inside_window,
    _is_waf_block as _is_waf_block,
    _normalize_image_url as _normalize_image_url,
    _should_fetch_og_image as _should_fetch_og_image,
)
from rugmunchbot.channels.telegram_news._sources._sweep import (
    _TITLE_REWRITE_CONCURRENCY as _TITLE_REWRITE_CONCURRENCY,
    _TITLE_REWRITE_MAX_ARTICLES as _TITLE_REWRITE_MAX_ARTICLES,
    _enrich_articles as _enrich_articles,
    sweep_telegram_news as sweep_telegram_news,
)
from rugmunchbot.channels.telegram_news._sources._tme import (
    _TME_CANONICAL_RES as _TME_CANONICAL_RES,
    _TME_HREF_RES as _TME_HREF_RES,
    _TME_IN_TEXT_RE as _TME_IN_TEXT_RE,
    _TME_OG_URL_RES as _TME_OG_URL_RES,
    _TME_RESOLVE_TIMEOUT as _TME_RESOLVE_TIMEOUT,
    _extract_tme_target as _extract_tme_target,
    _message_has_tme_link as _message_has_tme_link,
    resolve_tme_link as resolve_tme_link,
)
