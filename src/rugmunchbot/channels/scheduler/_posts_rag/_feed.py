"""RAG-drafts — feed.rugmunch.io posts.json + Ghost CMS mirror.

References the package namespace (``_pkg``) for shared config and the W30
mirror-failure state so monkeypatching the package propagates.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import rugmunchbot.channels.scheduler._posts_rag as _pkg


def mirror_post_to_feed(item: dict[str, Any]) -> str | None:
    """Append a post to the feed site's JSON store. Returns slug (None on fail).

    Public entry point (2026-08-29): the exploit-alert poster
    (``channels.exploit_alerts``) reuses this exact writer so every
    main-channel post — RAG story or exploit alert — mirrors to
    feed.rugmunch.io through the same atomic file path. ``_mirror_to_feed``
    remains as a backward-compatible alias for ``_posts_rag`` callers.
    """
    try:
        os.makedirs(_pkg.FEED_DATA, exist_ok=True)
        path = os.path.join(_pkg.FEED_DATA, "posts.json")
        posts: list[dict[str, Any]] = []
        if os.path.exists(path):
            with open(path) as f:
                try:
                    loaded = json.load(f)
                except json.JSONDecodeError:
                    loaded = None
            # Tolerant read: the deployed store was seeded as a dict wrapper
            # ({"posts": []}) — the canonical shape is a top-level array.
            if isinstance(loaded, dict):
                posts = list(loaded.get("posts") or [])
            elif isinstance(loaded, list):
                posts = list(loaded)
        slug = f"post-{int(time.time())}"
        # Thumbnail (2026-09): carry the story image (og:image / evidence
        # screenshot) into the static feed store so the SPA can render it.
        # http(s)-only — a server-side path is useless to the browser.
        _image = str(item.get("image_url") or item.get("image") or "").strip()
        if not _image.startswith(("http://", "https://")):
            _image = ""
        # 2026-09-02 image-leak fix: the feed store must never carry banned
        # imagery (social profile avatars/headers, brand chrome) either.
        from rugmunchbot.support.image_policy import is_valid_image

        if _image and not is_valid_image(_image):
            _image = ""
        # Dedup guard (stable-key): a re-post of the same story_ref/source
        # into both surfaces must not double-post on the feed. The Telegram
        # lane already dedupes its sends; this keeps the static feed store
        # idempotent as a second line of defense.
        _ref = str(item.get("story_ref") or item.get("source") or "")
        _text = str(item.get("text", ""))
        _existing = next(
            (p for p in posts if p.get("source") == _ref and p.get("text") == _text),
            None,
        )
        if _existing is not None:
            return str(_existing.get("slug") or slug)
        _payload = {
            "slug": slug,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "text": _text,
            "source": _ref,
            "kind": item.get("kind", "story"),
        }
        if _image:
            _payload["image"] = _image
        posts.insert(0, _payload)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(posts[:500], f, indent=1)
        os.replace(tmp, path)
        # Ghost CMS mirror (2026-08-31): posts.json write succeeded — now
        # push to Ghost (canonical store for feed.rugmunch.io). Fail-open:
        # a Ghost outage returns None and the local slug is used.
        try:
            ghost_slug = _pkg.ghost_mirror_post(item, slug)
        except Exception as ghost_err:  # belt-and-braces: never lose the slug
            _pkg.logger.warning("ghost_mirror_unexpected err=%s", str(ghost_err)[:200])
            ghost_slug = None
        return ghost_slug or slug
    except Exception as e:
        # W30: mirror is MANDATORY (CONTENT-STRATEGY-CANONICAL v2) — a
        # failure must be visible, not a debug whisper. The operator alert
        # itself fires from post_rag_drafts (needs the bot handle); here we
        # only escalate the log level.
        _pkg.logger.warning("feed_mirror_failed err=%s", str(e)[:200])
        return None


_mirror_to_feed = mirror_post_to_feed  # backward-compatible alias


def _ghost_admin_jwt(api_key: str) -> str:
    """Sign a short-lived Ghost Admin API JWT (HS256, aud=/admin/)."""
    import base64
    import hashlib
    import hmac

    parts = api_key.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("GHOST_ADMIN_API_KEY must be <id>:<secret>")
    key_id, secret = parts
    iat = int(time.time())

    def _b64url(data: bytes) -> str:

        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = _b64url(
        json.dumps({"alg": "HS256", "typ": "JWT", "kid": key_id}, separators=(",", ":")).encode()
    )
    payload = _b64url(
        json.dumps({"iat": iat, "exp": iat + 300, "aud": "/admin/"}, separators=(",", ":")).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    # 2026-09-01 FIX: the Ghost admin secret is a HEX string — it must be
    # decoded from hex before HMAC signing. Signing with the raw ASCII
    # bytes produces "Invalid token: invalid signature" (the 401 that
    # blocked every mirror attempt).
    try:
        signing_key = bytes.fromhex(secret)
    except ValueError:
        signing_key = secret.encode()  # non-hex fallback
    signature = _b64url(hmac.new(signing_key, signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _ghost_text_to_html(text: str) -> str:
    """Plain text → minimal safe HTML paragraphs (escape everything)."""
    import html as _html

    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{_html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras) or "<p></p>"


def ghost_mirror_post(item: dict[str, Any], slug: str) -> str | None:
    """Push one mirrored post to Ghost via the Admin API (fail-open).

    Called from :func:`mirror_post_to_feed` after the posts.json write
    succeeds. Ghost is the canonical store for feed.rugmunch.io going
    forward; posts.json stays as the fallback payload. Returns the Ghost
    slug (``/p/{slug}`` link) or None on any failure — posting to Telegram
    is never blocked by the Ghost mirror.
    """
    if not _pkg.GHOST_ADMIN_API_KEY:
        if not _pkg._ghost_no_key_logged:
            _pkg._ghost_no_key_logged = True
            _pkg.logger.info("ghost_mirror_skipped_no_admin_key")
        return None
    try:
        import httpx

        token = _ghost_admin_jwt(_pkg.GHOST_ADMIN_API_KEY)
        _image = str(item.get("image_url") or item.get("image") or "").strip()
        # 2026-09-02 image-leak fix: Ghost feature_image gets the same
        # banned-pattern guard as the static feed store.
        from rugmunchbot.support.image_policy import is_valid_image

        if _image and not is_valid_image(_image):
            _image = ""
        # 2026-09-05: callers may pass explicit tags (e.g. briefings carry
        # ["briefing", "market-update", ...]); fall back to the kind tag.
        _tags = item.get("tags")
        _tag_names = (
            [str(t) for t in _tags if str(t).strip()]
            if isinstance(_tags, (list, tuple))
            else [str(item.get("kind", "story"))]
        ) or [str(item.get("kind", "story"))]
        payload = {
            "posts": [
                {
                    "title": str(item.get("title") or item.get("text", "")[:80] or slug),
                    "html": _ghost_text_to_html(str(item.get("text", ""))),
                    "status": "published",
                    "visibility": "public",
                    "slug": slug,
                    "tags": [{"name": t} for t in _tag_names],
                    **(
                        {"feature_image": _image}
                        if _image.startswith(("http://", "https://"))
                        else {}
                    ),
                    **(
                        {"custom_excerpt": str(item.get("text", ""))[:300]}
                        if item.get("text")
                        else {}
                    ),
                }
            ]
        }
        resp = httpx.post(
            f"{_pkg.GHOST_ADMIN_API_URL.rstrip('/')}/ghost/api/admin/posts/",
            headers={
                "Authorization": f"Ghost {token}",
                "Content-Type": "application/json",
                "Accept-Version": "v5.0",
            },
            json=payload,
            timeout=8.0,
        )
        if resp.status_code in (200, 201):
            post = resp.json().get("posts", [{}])[0]
            ghost_slug = str(post.get("slug") or slug)
            _pkg.logger.info("ghost_mirror_published slug=%s", ghost_slug)
            return ghost_slug
        _pkg.logger.warning(
            "ghost_mirror_http_error status=%s body=%s", resp.status_code, resp.text[:200]
        )
        return None
    except Exception as exc:  # fail-open: Ghost outage must never break posting
        _pkg.logger.warning("ghost_mirror_failed err=%s", str(exc)[:200])
        return None


def _rag_post_with_image(text: str, item: dict[str, Any]) -> Any:
    """Wrap a RAG draft's text as a ``NewsPost`` carrying its image (if any).

    Main-channel doctrine (2026-08-29): a flagship post attaches the story's
    image when the draft provides one — the photo-aware sender
    (``_post_photo_aware``) then posts photo + caption, falling back to text.
    Fail-open: no usable image → the plain text string (unchanged behavior).
    """
    # 2026-09-01 FINAL: story-doc image_url is UNTRUSTED — existing Qdrant
    # docs carry the operator's brand banner from the pre-fix era. Images
    # resolve ONLY from the article's own og:image (resolve_post_image in
    # the posting path below). This wrapper no longer attaches doc images.
    return text


async def _alert_mirror_failure(bot: Any, err_hint: str) -> None:
    """Notify the operator channel that the mandatory feed mirror broke.

    CONTENT-STRATEGY-CANONICAL v2 / W30: every main post MUST land on
    feed.rugmunch.io; a silent mirror gap breaks the operator-social
    contract. Alerts fire to the ``updates`` role (@rmiupdates) once per
    failure streak — never per tick. Fail-open: any error here is logged
    and swallowed; alerting must never break posting.
    """
    _pkg._mirror_fail_streak += 1
    if _pkg._mirror_fail_streak > 1:
        return  # already alerted for this streak
    _pkg.logger.warning(
        "feed_mirror_down streak_started=%d hint=%s", _pkg._mirror_fail_streak, err_hint[:80]
    )
    try:
        from rugmunchbot.config import _resolve_channel_id

        ops_chat = _resolve_channel_id("updates")
        if not ops_chat:
            return
        await bot.send_message(
            ops_chat,
            "\U0001f6a8 <b>Feed mirror DOWN</b>\n"
            "Main-channel posts are NOT reaching feed.rugmunch.io "
            "(mandatory mirror, strategy v2/W30).\n"
            f"hint: <code>{err_hint[:120]}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        _pkg.logger.warning("feed_mirror_alert_failed err=%s", str(e)[:120])


def _note_mirror_success() -> None:
    """Reset the W30 failure streak; log recovery exactly once."""
    if _pkg._mirror_fail_streak:
        _pkg.logger.info("feed_mirror_recovered after=%d failures", _pkg._mirror_fail_streak)
    _pkg._mirror_fail_streak = 0
