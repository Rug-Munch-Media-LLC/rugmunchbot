"""Personal channel (@cryptorugmuncher) — reranker-gated, operator-voice posts.

The **personal channel** is the operator's own Telegram surface
(``t.me/cryptorugmuncher`` — registry role ``main``, gopass
``rmi/telegram/CHANNEL_CRYPTO_RUGMUNCHER``). It receives ONLY the "bigger
stories": the same Infinity ``BAAI/bge-reranker-v2-m3`` importance scoring the
news channel uses (``channels/importance.py``), but gated by a **stricter
threshold** — ``RMI_PERSONAL_CHANNEL_THRESHOLD`` (default 0.7) instead of the
news channel's 0.4. Everything else (cadence, chat id, enabled flag, ghost
cross-post) is env-configurable.

Pipeline (per tick, fail-open at every step):

1. **Sweep** — reuse ``channels/telegram_news.py::sweep_telegram_news`` (same
   headless Telethon read + normalize + dedupe machinery; no second client).
2. **Score** — reuse ``channels/importance.py::score_importance`` (single
   Infinity reranker; heuristic fallback when the reranker is down).
3. **Gate** — :func:`select_personal` keeps only items at/above the stricter
   threshold. In reranker-down (fallback) mode it keeps the top-N by heuristic
   score (mirrors ``select_important`` semantics) so the channel never goes
   silent during a reranker outage — it only rearranges real swept content.
4. **Post** — one caption per story in the operator's voice (see
   :mod:`voice` below), deduped via the scheduler's in-memory ``_remember``
   set (prefix ``personal``).
5. **Cross-post** — every posted story is pushed to the Ghost blog
   (``blog.rugmunch.io``) via the Ghost **Admin API** client in this module.

Voice
-----
There IS a canonical operator voice: ``PERSONA_CONTEXT`` (v10) in
``app/domains/telegram/rugmunchbot/prompts.py`` — "sharp degen-fluent
intelligence analyst": blunt, data-first, no corporate-speak, never fabricate
metrics. This module reuses that voice: captions are built from the
``CAPTION_TEMPLATES`` block below (marked **operator voice — edit here**),
which the operator tunes directly. Captions only summarize the *real* article
(title + a clipped excerpt of the body + source + canonical link) — they never
invent facts.

Ghost
-----
The cross-post client targets real Ghost **Admin API v5** endpoints
(``POST {url}/ghost/api/admin/posts/``) authenticated with a JWT-signed Admin
API key (``GHOST_ADMIN_API_KEY``, format ``<id>:<secret>``). Env vars:
``GHOST_ADMIN_API_URL`` (default ``https://blog.rugmunch.io``) and
``GHOST_ADMIN_API_KEY``. When the key is missing the cross-post logs and
skips (fail-open) — the Telegram post still ships. There is an existing
session-cookie Ghost client in ``app/content_platform.py``
(``create_post``); this module deliberately uses the Admin API key path
because a scheduled job must not depend on a browser login cookie.

Env knobs (all optional):
* ``RMI_PERSONAL_CHANNEL_ENABLED``            — master switch (default OFF —
  opt-in, so nothing posts until the operator enables it [REVIEW])
* ``RMI_PERSONAL_CHANNEL_INTERVAL_MINUTES``   — cadence (default 180)
* ``RMI_PERSONAL_CHANNEL_THRESHOLD``          — stricter rerank gate
  (default 0.7; must be >= the news channel's 0.4 to be meaningfully
  stricter, but the code does not enforce that)
* ``RMI_PERSONAL_CHANNEL_ID``                 — optional chat-id override;
  falls back to the registry ``main`` role (gopass → env → fallback)
* ``RMI_PERSONAL_CHANNEL_POST_LIMIT``         — max stories per tick
  (default 3)
* ``RMI_PERSONAL_CHANNEL_GHOST_ENABLED``      — cross-post to Ghost
  (default on; no-op without ``GHOST_ADMIN_API_KEY``)
* ``RMI_GHOST_CROSSPOST_ENABLED``             — master cross-post switch for
  the story router (default OFF; cross-post stays disabled until the operator
  opts in AND provisions ``GHOST_ADMIN_API_KEY``)
* ``GHOST_ADMIN_API_URL`` / ``GHOST_ADMIN_API_KEY`` — Ghost Admin API
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import TYPE_CHECKING, Any

from app.domains.telegram.rugmunchbot.channels.importance import (
    IMPORTANCE_THRESHOLD,
    ScoredArticle,
    score_importance,
)
from app.domains.telegram.rugmunchbot.channels.telegram_news import sweep_telegram_news
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

    from app.domains.news_ingest.models import NewsArticle

logger = get_logger("rmi_bot.channels.personal")

# ── Env knobs ────────────────────────────────────────────────────────────


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0.0, min(1.0, float(raw.strip())))
    except (TypeError, ValueError):
        return default


#: Master switch — intentionally default OFF. This posts to the operator's
#: own channel; flipping it on is an explicit operator decision.
PERSONAL_CHANNEL_ENABLED: bool = _env_bool("RMI_PERSONAL_CHANNEL_ENABLED", False)
PERSONAL_CHANNEL_INTERVAL_MINUTES: int = _env_int("RMI_PERSONAL_CHANNEL_INTERVAL_MINUTES", 180)
PERSONAL_CHANNEL_THRESHOLD: float = _env_float("RMI_PERSONAL_CHANNEL_THRESHOLD", 0.7)
PERSONAL_CHANNEL_POST_LIMIT: int = _env_int("RMI_PERSONAL_CHANNEL_POST_LIMIT", 3)
PERSONAL_CHANNEL_GHOST_ENABLED: bool = _env_bool("RMI_PERSONAL_CHANNEL_GHOST_ENABLED", True)

#: Master Ghost cross-post switch — intentionally default OFF. The story
#: router's cross-post stays disabled until the operator opts in AND
#: provisions ``GHOST_ADMIN_API_KEY`` (fail-open: the Telegram post still
#: ships either way).
GHOST_CROSSPOST_ENABLED: bool = _env_bool("RMI_GHOST_CROSSPOST_ENABLED", False)

#: Ghost Admin API config. The URL defaults to the canonical blog; the key is
#: the Admin API key (``<id>:<secret>``). See the module docstring for the
#: fail-open contract and the gopass TODO below.
GHOST_ADMIN_API_URL: str = os.getenv("GHOST_ADMIN_API_URL", "https://blog.rugmunch.io")
GHOST_ADMIN_API_KEY: str = os.getenv("GHOST_ADMIN_API_KEY", "")

# ══════════════════════════════════════════════════════════════════════════
# OPERATOR VOICE — edit here
# ══════════════════════════════════════════════════════════════════════════
# Canonical voice source: PERSONA_CONTEXT (v10) in
# app/domains/telegram/rugmunchbot/prompts.py — "sharp degen-fluent
# intelligence analyst": blunt truth-telling, data-first, no corporate-speak,
# never fabricate metrics. These templates render ONE story per post in that
# voice. Placeholders: {title} (real headline), {lead} (clipped excerpt of
# the real body), {source} (channel name), {url} (canonical t.me link).
# The operator tunes these phrasings directly.
CAPTION_TEMPLATES: tuple[str, ...] = (
    "🚨 {title}\n\n{lead}\n\nFull story: {url}\n— via {source}",
    "{title}\n\nThis one matters: {lead}\n\n{url} · via {source}",
    "🔥 {title}\n\n{lead}\n\nRead the full story: {url} (source: {source})",
    "{title}\n\n{lead} — {url}\n\nvia {source}",
)

_LEAD_CHARS = 240


def _lead(article: NewsArticle) -> str:
    """First sentence(s) of the real body, clipped. Never invents content."""
    text = (article.body or article.title or "").strip()
    if not text:
        return ""
    if len(text) <= _LEAD_CHARS:
        return text
    cut = text[:_LEAD_CHARS]
    # Prefer breaking at a sentence boundary when one exists in the clip.
    for boundary in (". ", "! ", "? ", ".\n"):
        idx = cut.rfind(boundary)
        if idx > 40:  # keep at least a meaningful first sentence
            return cut[: idx + 1].strip()
    return cut.rstrip() + "…"


def build_caption(article: NewsArticle, template_idx: int | None = None) -> str:
    """One operator-voice caption for a story.

    ``template_idx`` is deterministic (hash of the canonical source URL) when
    omitted, so the same story renders the same way on re-post. The caption is
    built exclusively from the real article — title, clipped body lead,
    source channel, canonical URL. No facts are fabricated.
    """
    if template_idx is None:
        digest = hashlib.sha256(article.source_url.encode("utf-8", errors="ignore")).hexdigest()
        template_idx = int(digest[:8], 16) % len(CAPTION_TEMPLATES)
    template = CAPTION_TEMPLATES[template_idx % len(CAPTION_TEMPLATES)]
    return template.format(
        title=article.title.strip(),
        lead=_lead(article),
        source=article.source_channel or "Telegram",
        url=article.source_url.strip(),
    )


# ── Selection (stricter reranker gate) ──────────────────────────────────────


def select_personal(scored: list[ScoredArticle], top_n: int | None = None) -> list[ScoredArticle]:
    """Return the stories worth the personal channel, best first.

    Rerank mode: keep ``importance >= PERSONAL_CHANNEL_THRESHOLD`` (default
    0.7 — the "bigger stories" above the news channel's ``IMPORTANCE_THRESHOLD``
    of 0.4), sorted by importance desc. Fallback mode (reranker down): keep
    the top-N by heuristic score so the channel still flows during an outage.
    Never invents content — it only rearranges what the sweep normalized.
    """
    limit = max(1, top_n or PERSONAL_CHANNEL_POST_LIMIT)
    if not scored:
        return []
    if scored[0].fallback:
        ordered = sorted(scored, key=lambda s: s.importance, reverse=True)
    else:
        ordered = sorted(
            (s for s in scored if s.importance >= PERSONAL_CHANNEL_THRESHOLD),
            key=lambda s: s.importance,
            reverse=True,
        )
    return ordered[:limit]


# ── Ghost Admin API client (fail-open) ──────────────────────────────────────

#: TODO(ghost-admin-key): provision GHOST_ADMIN_API_KEY for the cross-post
#: job. secrets-store entry: ghost-admin-api-key (name
#: only — never log the value). Content API key (GHOST_CONTENT_API_KEY) is
#: read-only and cannot publish, hence the Admin key requirement.

#: True once the missing-key skip has been logged (log-once per process).
_ghost_no_key_logged: bool = False


def _article_image_url(article: NewsArticle) -> str:
    """Story hero image for the Ghost post (best-effort, never raises).

    Prefers the ``image_url`` attribute the story pipeline is adding; falls
    back to the sweep's ``media`` reference, then to the first inline image
    URL extracted from the article text (``news_ingest.extract_image_url``).
    """
    if article is None:
        return ""
    for attr in ("image_url", "media"):
        raw = getattr(article, attr, None)
        if isinstance(raw, str) and raw.strip() and raw.strip().lower() not in ("none", "null"):
            return raw.strip()
    try:
        from app.domains.news_ingest.ingest import extract_image_url

        return extract_image_url(article)
    except Exception:  # soft dependency — degrade silently
        return ""


def _article_canonical_url(article: NewsArticle) -> str:
    """Canonical source URL for the Ghost post (best-effort, never raises).

    Prefers the ``canonical_url`` attribute the story pipeline is adding;
    falls back to the first non-``t.me`` URL extracted from the article text
    (``news_ingest.extract_canonical_url``). Returns ``""`` when nothing is
    usable (the caller falls back to ``article.source_url``).
    """
    if article is None:
        return ""
    raw = getattr(article, "canonical_url", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    try:
        from app.domains.news_ingest.ingest import extract_canonical_url

        return extract_canonical_url(article)
    except Exception:  # soft dependency — degrade silently
        return ""


def ghost_admin_jwt(api_key: str) -> str:
    """Sign a short-lived Ghost Admin API JWT from ``<id>:<secret>``.

    Real Ghost Admin API v5 auth: header ``kid`` = key id, HS256 signature
    over the base64url header+payload, ``aud`` = ``/admin/``. Raises
    ``ValueError`` for malformed keys (callers fail open).
    """
    parts = api_key.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("GHOST_ADMIN_API_KEY must be <id>:<secret>")
    key_id, secret = parts[0], parts[1]
    iat = int(time.time())

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": key_id}, separators=(",", ":")).encode("ascii"))
    payload = _b64url(
        json.dumps({"iat": iat, "exp": iat + 5 * 60, "aud": "/admin/"}, separators=(",", ":")).encode("ascii")
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = _b64url(hmac.new(secret.encode("ascii"), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _lexical_body(text: str) -> str:
    """Convert plain text to Ghost lexical JSON (paragraph nodes)."""
    children: list[dict[str, Any]] = []
    for para in (p.strip() for p in text.split("\n\n") if p.strip()):
        children.append(
            {
                "children": [
                    {
                        "detail": 0,
                        "format": 0,
                        "mode": "normal",
                        "style": "",
                        "text": para.replace("\n", " "),
                        "type": "text",
                        "version": 1,
                    }
                ],
                "direction": "ltr",
                "format": "",
                "indent": 0,
                "type": "paragraph",
                "version": 1,
            }
        )
    return json.dumps(
        {
            "root": {
                "children": children,
                "direction": "ltr",
                "format": "",
                "indent": 0,
                "type": "root",
                "version": 1,
            }
        }
    )


async def ghost_publish_article(
    article: NewsArticle,
    caption: str,
    tags: list[str] | None = None,
    *,
    url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Publish one story to the Ghost blog via the Admin API (fail-open).

    Returns a result dict with ``published: True`` + post metadata on success,
    or ``{"published": False, "error": ...}`` on any failure. Missing key/URL,
    network errors, and Ghost HTTP errors all log and return — the Telegram
    post is never blocked by the cross-post.
    """
    base_url = (url or GHOST_ADMIN_API_URL).rstrip("/")
    key = api_key or GHOST_ADMIN_API_KEY
    if not key:
        global _ghost_no_key_logged
        if not _ghost_no_key_logged:
            _ghost_no_key_logged = True
            logger.info("personal:ghost_skipped_no_admin_key url=%s", base_url)
        return {
            "published": False,
            "skipped": True,
            "error": "GHOST_ADMIN_API_KEY not set",
            "help": "secrets-store: ghost-admin-api-key",
        }
    if not base_url:
        logger.warning("personal:ghost_skipped_no_url")
        return {"published": False, "error": "GHOST_ADMIN_API_URL not set"}

    try:
        token = ghost_admin_jwt(key)
    except ValueError as exc:
        logger.warning("personal:ghost_bad_api_key err=%s", str(exc))
        return {"published": False, "error": str(exc)}

    all_tags = list(dict.fromkeys((tags or []) + ["news"]))
    excerpt = _lead(article)[:300] or article.title[:300]
    # Story hero image + canonical source link — both best-effort; Ghost's
    # create payload has no writable ``url`` field, so the canonical source
    # is passed via ``canonical_url`` (plus it already appears in the caption
    # text rendered into ``lexical`` below).
    feature_image = _article_image_url(article) or None
    canonical_url = _article_canonical_url(article) or (article.source_url.strip() or None)
    payload = {
        "posts": [
            {
                "title": article.title,
                "lexical": _lexical_body(caption),
                "custom_excerpt": excerpt,
                "status": "published",
                "visibility": "public",
                "feature_image": feature_image,
                "canonical_url": canonical_url,
                "tags": [{"name": tag} for tag in all_tags],
            }
        ]
    }
    try:
        client = get_shared_client(8.0)
        resp = await client.post(
            f"{base_url}/ghost/api/admin/posts/",
            headers={
                "Authorization": f"Ghost {token}",
                "Content-Type": "application/json",
                "Ghost-Version": "5.0",
                "Accept-Version": "v5.0",
            },
            json=payload,
        )
        if resp.status_code in (200, 201):
            post = resp.json().get("posts", [{}])[0]
            logger.info("personal:ghost_published id=%s slug=%s", post.get("id"), post.get("slug"))
            return {
                "published": True,
                "id": post.get("id"),
                "slug": post.get("slug"),
                "url": post.get("url", f"{base_url}/ghost/#/editor/post/{post.get('id', '')}"),
                "title": post.get("title"),
            }
        logger.warning("personal:ghost_http_error status=%s body=%s", resp.status_code, resp.text[:200])
        return {"published": False, "error": f"Ghost HTTP {resp.status_code}", "detail": resp.text[:200]}
    except Exception as exc:  # fail-open: cross-post must never break the tick
        logger.warning("personal:ghost_publish_failed err=%s", str(exc)[:200])
        return {"published": False, "error": str(exc)}


async def publish_to_ghost(
    article: NewsArticle,
    caption: str | None = None,
    *,
    tags: list[str] | None = None,
    url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Public cross-post entry point for the story router (fail-open, OFF by default).

    The "main" routing branch (this module's ``post_personal_channel`` and any
    future story ``router.py``) calls this with just the article. Behavior:

    * **Gated** on ``RMI_GHOST_CROSSPOST_ENABLED`` (default ``0``) — the
      cross-post stays OFF until the operator provisions
      ``GHOST_ADMIN_API_KEY`` and opts in; while disabled it logs at INFO and
      returns ``{"published": False, "skipped": ...}`` without touching the
      network, so the Telegram post path is never blocked.
    * **Fail-open** — missing key/URL, bad key format, network errors, and
      Ghost HTTP errors all log and return (see
      :func:`ghost_publish_article`).
    * Builds an operator-voice caption when none is passed, then delegates to
      :func:`ghost_publish_article` (title = the article's rewritten title,
      body = caption rendered as Ghost lexical, plus ``feature_image`` and
      ``canonical_url`` from the article).

    Returns the same result-dict shape as :func:`ghost_publish_article`.
    """
    if not GHOST_CROSSPOST_ENABLED:
        logger.info("personal:ghost_crosspost_disabled flag=RMI_GHOST_CROSSPOST_ENABLED")
        return {"published": False, "skipped": "RMI_GHOST_CROSSPOST_ENABLED=0"}
    body = (caption or "").strip() or build_caption(article)
    return await ghost_publish_article(article, body, tags=tags, url=url, api_key=api_key)


# ── Consumer ────────────────────────────────────────────────────────────────


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
        from app.domains.telegram.rugmunchbot.config import _resolve_channel_id

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
    from app.domains.telegram.rugmunchbot.channels.scheduler import (
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
