"""Personal channel Ghost cross-post — Admin API v5 client (fail-open).

Extracted verbatim from the former single-module ``channels/personal_channel.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.personal_channel._config import (
    GHOST_ADMIN_API_KEY,
    GHOST_ADMIN_API_URL,
    GHOST_CROSSPOST_ENABLED,
)
from rugmunchbot.channels.personal_channel._content import (
    _lead,
    build_caption,
)
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import get_shared_client

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import NewsArticle

logger = get_logger("rmi_bot.channels.personal")

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
        from rugmunchbot._vendor.news_ingest.ingest import extract_image_url

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
        from rugmunchbot._vendor.news_ingest.ingest import extract_canonical_url

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

    header = _b64url(
        json.dumps({"alg": "HS256", "typ": "JWT", "kid": key_id}, separators=(",", ":")).encode(
            "ascii"
        )
    )
    payload = _b64url(
        json.dumps(
            {"iat": iat, "exp": iat + 5 * 60, "aud": "/admin/"}, separators=(",", ":")
        ).encode("ascii")
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
        logger.warning(
            "personal:ghost_http_error status=%s body=%s", resp.status_code, resp.text[:200]
        )
        return {
            "published": False,
            "error": f"Ghost HTTP {resp.status_code}",
            "detail": resp.text[:200],
        }
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
