"""Telegram news-channel ingestion → website news + bot news channel.

Sweeps a curated set of public crypto-news Telegram channels (via the same
headless Telethon user-session client the alerts domain uses), normalizes the
recent messages with ``app.domains.news_ingest``, then:

1. **Persists** newly-seen articles into the website news table
   (``news_items``, the table the ``/api/v1/news/*`` routes serve) — mirroring
   the RSS ingest path in ``app.domains.news.ingest``.
2. **Posts** the important subset to the bot's news channel (same chat id as
   the existing ``post_news_update`` job; same ``format_news_post`` +
   ``_safe_send`` machinery). Importance is the reranker-derived score from
   ``channels/importance.py`` (Infinity ``BAAI/bge-reranker-v2-m3``); every
   article is persisted, only those above ``RMI_NEWS_IMPORTANCE_THRESHOLD``
   are posted (fail-open: heuristic fallback when Infinity is down).
3. **Feeds RAG** — the freshly-swept articles are clustered into stories and
   embedded into the RAG ``news_v2`` collection (``_rag.py`` →
   ``ingest_into_rag``, the same collection the bot's ``get_news`` RAG tool
   searches), gated by ``RMI_TG_NEWS_RAG`` (default on). Fail-open like the
   rest of the pipeline.

Everything fails open: a missing headless client, a flaky channel, Postgres
being unreachable, or a Telegram send error logs and skips — the JobQueue keeps
running and the other half of the pipeline still works.

Read mechanism
--------------
``python-telegram-bot``'s Bot API cannot read arbitrary public channels the bot
is not a member of, so channel reads go through the **headless Telethon client**
(``app.domains.telegram.headless.get_headless_client`` → MTProto user session)
via the canonical thin reader ``app.domains.news_ingest.read_channel_messages``.
This is the exact mechanism the alerts domain (``telegram_transport``) and the
daily pipeline (``app.domains.content.daily_pipeline``) already use.

Setup required before the sweep returns data (documented, never hardcoded):
* ``telethon`` is a runtime dependency (see ``requirements.txt`` /
  ``pyproject.toml``).
* gopass entries the headless client reads in
  ``app.domains.telegram.headless_credentials``: ``rmi/telegram/api_id`` and
  ``rmi/telegram/api_hash`` exist in the store; ``rmi/telegram/phone`` must be
  created (``insert a phone entry in the secrets store (see headless_credentials)``). Until the phone entry exists
  the sweep degrades to ``[]`` and logs ``headless_client_unavailable``.

Channel list
------------
The sweep prefers the account's own followed/member public channels, discovered
at runtime via Telethon ``client.get_dialogs()``
(``app.domains.news_ingest.discover_followed_channels``) — gated by
``RMI_TG_NEWS_AUTO_DISCOVER`` (default on). Every discovered channel is
categorized by title/username heuristics (:func:`classify_channel_category`)
into **news** / **alpha** / **scam** / **other**:

* ``news``  → swept with ``category="crypto_news"`` → news channel
* ``alpha`` → swept with ``category="alpha"`` → alpha channel
* ``scam``  → left to ``channels/scam_watch.py`` (existing categorization,
  never duplicated here)
* ``other`` → skipped (personal chats / non-crypto noise)

The discovered sweep is bounded by ``RMI_TG_NEWS_MAX_CHANNELS`` (default 40);
per-channel message reads are bounded by ``RMI_TG_NEWS_PER_CHANNEL_LIMIT``
(default 30). When discovery is disabled, returns nothing, or the client is
unavailable, the sweep falls back to the env-overridable explicit list
``RMI_TG_NEWS_CHANNELS`` (comma- or pipe-separated @handles), then to
``_DEFAULT_CHANNELS`` (64 curated high-signal public crypto-news channels —
news outlets, security/exploit alerts, DeFi/protocol feeds, and
on-chain/alpha feeds; handles that
repeatedly fail to resolve are commented out and auto-disabled by the ingest
layer's per-channel cooldown). Several
default handles carry ``[REVIEW]`` notes in ``app/databus/tg_channels.json``;
verify them before production monitoring. No secrets involved.

Dedup
-----
Four layers:
* **In-batch** — ``dedupe_articles`` is **multi-key**: canonical source URL
  (the first non-``t.me`` link inside the message) → channel ``t.me`` URL →
  normalized title (lowercase, punctuation/emoji stripped, first 60 chars) →
  content hash. The same story that is rephrased or forwarded across channels
  — with a different body but the same original link or headline — collapses
  to one article.
* **Cross-run (Redis)** — ``app.domains.news_ingest.filter_seen_articles``
  checks the **same key set** (URL / title / raw content hash) against the
  shared ``app.rag.dedup`` ZSETs (``rag:dedup:content:tg_news``), so a story
  seen in a previous sweep — even rephrased — is never re-read/re-embedded.
  Fresh articles are marked (``mark_articles_ingested``, all keys) only AFTER
  a successful ``news_items`` insert (fail-open; Redis down never hides a
  story).
* **Cross-run (Postgres)** — the ``news_items.news_id`` primary key is derived
  from the canonical ``https://t.me/<channel>/<message_id>`` URL, so
  re-sweeping the same message is skipped by the ``SELECT 1 ...
  WHERE news_id=$1`` guard.
* **Post-level** — the scheduler's in-memory ``_remember`` set (key prefix
  ``tgnews`` / ``tgalpha``) stops the digest re-posting the same article on
  the next tick.

Post variety
------------
The news channel rotates post formats deterministically (by story count,
shared topic, and a stable batch-parity signal — see
:func:`_news_post_for`) instead of repeating the same digest every tick:

* **Breaking solo** — when the top ranked story carries a breaking marker
  (🚨/BREAKING/URGENT/JUST IN/FLASH in its title,
  :func:`is_breaking_article`), it is posted immediately on its own with the
  official link (:func:`format_breaking_post`).
* **Solo top-story card** — a single fresh story is posted as a ⭐ feature
  card: rewritten title + one-line hook + longer lede with the story's image
  attached when one exists (:func:`format_feature_post`).
* **Themed roundup** — when ≥2 stories share a ``$TOKEN`` topic they form a
  🧵 themed roundup (:func:`format_themed_post`).
* **Top-3 hybrid / numbered digest** — otherwise the batch alternates
  between the top-3 hybrid (lead card + image + short list,
  :func:`format_hybrid_post`) and the full numbered digest
  (:func:`format_news_post`) via a deterministic batch-parity signal.

High-importance (≥0.7) stories route to the operator's @cryptorugmuncher
channel and post **solo** — 🚨 breaking shape when the marker is present,
otherwise the compelling ⭐ feature card — with the story's image attached.

Importance
----------
Two scorers back the routing:
* **Rerank** (``channels/importance.py``, Infinity ``BAAI/bge-reranker-v2-m3``)
  — the primary gate for what is posted at all (``RMI_NEWS_IMPORTANCE_THRESHOLD``).
* **Lightweight** (``app.domains.news_ingest.lightweight_importance``) — a
  deterministic 0-1 score (source authority, breaking markers, crypto
  keywords, freshness) used as a stable secondary ordering and for the
  website feed items.

Pipeline order (efficiency contract)
------------------------------------
``read (bounded) → normalize → dedupe (content hash) → store (news_items +
Redis dedup mark) → reranker importance → route``.

Routing
-------
* **news** articles → persisted → posted to the news channel
* **alpha** articles → persisted → posted to the alpha channel (when
  ``_chat_id("alpha")`` resolves; skipped otherwise)
* **scam** → owned by ``channels/scam_watch.py`` (feeds the scanner)

Additive side-effects (all fail-open): freshly-persisted TG items are merged
into the website full-feed Redis cache (``inject_into_news_cache``) so
``/api/v1/news/feed`` + ``/headlines`` surface them with
``kind="telegram"`` + channel attribution — the Postgres-backed
``/api/v1/news/trending`` + ``POST /api/v1/news`` routes already serve TG
items directly from ``news_items``. A lightweight market-awareness snapshot
(top headlines + trending tokens) is published to
``rmi:news:market_awareness`` (``publish_market_awareness``) for the bot's
chat context.

Schedule
--------
Registered additively in ``channels/scheduler.py`` (``register_channel_jobs``):
every ``RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES`` (default 30 — ~2 posts/hour on
the news channel) minutes, gated by ``RMI_TG_NEWS_CHANNEL_ENABLED`` (default
on) and a resolvable news chat id.

Module layout
-------------
Thin re-export shim over the implementation submodules (the split keeps the
original module namespace — including ``__all__`` and every module-level name
tests patch — intact on this module):

* ``_sources.py`` — env config, channel list, categorization, source
  resolution, headless-client bootstrap, sweep (fetch/registry).
* ``_persist.py`` — ``news_items`` persistence (news_id / chain / token
  tagging, fail-open insert loop).
* ``_digest.py`` — digest formatting + website feed injection +
  market-awareness snapshot.
* ``_rag.py`` — RAG ``news_v2`` ingestion (story clustering + embed).
* ``_scheduler.py`` — the scheduled broadcast (``post_tg_news_update``).

Patch contract: tests patch module globals on **this** module
(``patch.object(tg_news, "sweep_telegram_news", ...)`` etc.); the submodules
resolve those names through this shim at call time, so the shim stays the
patch point.
"""

from __future__ import annotations

from app.core.redis import get_redis_async  # noqa: F401
from app.domains.news_ingest.discovery import discover_followed_channels  # noqa: F401
from app.domains.news_ingest.ingest import (
    DEFAULT_DEDUP_COLLECTION,  # noqa: F401
    article_dedup_keys,
    dedupe_articles,
    extract_canonical_url,
    extract_image_url,
    filter_seen_articles,
    is_breaking_article,
    lightweight_importance,
    mark_articles_ingested,
    normalize_title,
    rank_articles,
    read_channel_messages,  # noqa: F401
)
from app.domains.news_ingest.normalize import channel_message_to_article  # noqa: F401
from app.domains.telegram.rugmunchbot.channels.formatters import (
    format_breaking_post,
    format_feature_post,
    format_hybrid_post,
    format_news_post,
    format_themed_post,
)
from app.domains.telegram.rugmunchbot.channels.importance import (
    score_importance,  # noqa: F401
    select_important,  # noqa: F401
    top_headlines,  # noqa: F401
    trending_tokens,  # noqa: F401
)
from app.domains.telegram.rugmunchbot.channels.rewrite_titles import (
    TITLE_REWRITE_ENABLED,
    rewrite_news_title,  # noqa: F401
)
from app.domains.telegram.rugmunchbot.channels.telegram_news._digest import (
    _MARKET_AWARENESS_HEADLINES,  # noqa: F401
    _MARKET_AWARENESS_KEY,  # noqa: F401
    _MARKET_AWARENESS_TOKENS,  # noqa: F401
    _MARKET_AWARENESS_TTL,  # noqa: F401
    _NEWS_FEED_CACHE_KEY,  # noqa: F401
    _NEWS_FEED_CACHE_MAX,  # noqa: F401
    _TG_ARTICLE_KIND,  # noqa: F401
    _apply_commentaries,  # noqa: F401
    _article_to_feed_item,  # noqa: F401
    _article_to_post_dict,  # noqa: F401
    _batch_parity,  # noqa: F401
    _category_is_news,  # noqa: F401
    _news_post_for,  # noqa: F401
    _redis_json_get,  # noqa: F401
    _redis_json_set,  # noqa: F401
    _shared_topic,  # noqa: F401
    attach_commentaries,
    generate_commentary,
    inject_into_news_cache,
    publish_market_awareness,
    publish_story_to_feed,
)
from app.domains.telegram.rugmunchbot.channels.telegram_news._persist import (
    _CHAIN_KEYWORDS,  # noqa: F401
    _article_to_news_item,  # noqa: F401
    _detect_chains,  # noqa: F401
    _news_id_for,  # noqa: F401
    _tokens_from_entities,  # noqa: F401
    persist_articles,
)
from app.domains.telegram.rugmunchbot.channels.telegram_news._rag import ingest_into_rag
from app.domains.telegram.rugmunchbot.channels.telegram_news._scheduler import post_tg_news_update
from app.domains.telegram.rugmunchbot.channels.telegram_news._sources import (
    _CHANNEL_ALPHA_KEYWORDS,  # noqa: F401
    _CHANNEL_NEWS_KEYWORDS,  # noqa: F401
    _CHANNEL_OWN_KEYWORDS,  # noqa: F401
    _DEFAULT_CHANNELS,  # noqa: F401
    TG_NEWS_AUTO_DISCOVER,
    TG_NEWS_CHANNELS,
    TG_NEWS_ENABLED,
    TG_NEWS_INTERVAL_MINUTES,
    TG_NEWS_MAX_CHANNELS,
    TG_NEWS_PER_CHANNEL_LIMIT,
    TG_NEWS_PERSIST,  # noqa: F401
    TG_NEWS_POST_LIMIT,
    TG_NEWS_RAG,
    TG_NEWS_SWEEP_HOURS,
    TME_RESOLVE_ENABLED,
    _as_utc,  # noqa: F401
    _build_headless_client,  # noqa: F401
    _env_bool,  # noqa: F401
    _env_int,  # noqa: F401
    _env_list,  # noqa: F401
    _inside_window,  # noqa: F401
    channel_sources,
    classify_channel_category,
    re_split,  # noqa: F401
    resolve_channel_sources,
    sweep_telegram_news,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.tg_news")

__all__ = [
    "TG_NEWS_AUTO_DISCOVER",
    "TG_NEWS_CHANNELS",
    "TG_NEWS_ENABLED",
    "TG_NEWS_INTERVAL_MINUTES",
    "TG_NEWS_MAX_CHANNELS",
    "TG_NEWS_PER_CHANNEL_LIMIT",
    "TG_NEWS_POST_LIMIT",
    "TG_NEWS_RAG",
    "TG_NEWS_SWEEP_HOURS",
    "TITLE_REWRITE_ENABLED",
    "TME_RESOLVE_ENABLED",
    "article_dedup_keys",
    "attach_commentaries",
    "channel_sources",
    "classify_channel_category",
    "dedupe_articles",
    "extract_canonical_url",
    "extract_image_url",
    "filter_seen_articles",
    "format_breaking_post",
    "format_feature_post",
    "format_hybrid_post",
    "format_news_post",
    "format_themed_post",
    "generate_commentary",
    "ingest_into_rag",
    "inject_into_news_cache",
    "is_breaking_article",
    "lightweight_importance",
    "mark_articles_ingested",
    "normalize_title",
    "persist_articles",
    "post_tg_news_update",
    "publish_market_awareness",
    "publish_story_to_feed",
    "rank_articles",
    "resolve_channel_sources",
    "sweep_telegram_news",
]
