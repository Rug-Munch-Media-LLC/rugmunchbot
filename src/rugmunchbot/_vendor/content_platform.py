# VENDORED 2026-08-23 from rmi-backend/app/content_platform.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Ghost Content Platform - @cryptorugmunch Feed engine.

Headless Ghost CMS (blog.rugmunch.io) powering the rugmunch.io surfaces:
  blog.rugmunch.io      — product announcements (Ghost's own theme)
  feed.rugmunch.io      — @cryptorugmunch social feed (twitter-clone frontend)
  newsletter.rugmunch.io — weekly newsletter (Ghost-published)

Post types:
  micro      - Short posts (≤4000 chars, like X posts)
  thread     - Connected series of posts (X-style threads)
  article    - Standard longform blog post
  dev        - Development updates
  announcement - Platform announcements
  newsletter - Weekly deep-dive newsletter
  briefing   - Daily intelligence briefing (auto-generated)
  paid       - Premium gated content

Auth (Admin API, write): HS256 JWT signed with the Admin API key
  (``id:secret``, secret hex-decoded, ``kid=<id>``, ``aud=/admin/``,
  ``exp<=5min``) — read from ``/srv/secrets/ghost_admin_key`` (canonical
  Talos path), then ``GHOST_ADMIN_API_KEY`` env, then the legacy session
  cookie file. Content API (read) uses ``GHOST_CONTENT_API_KEY`` env.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

GHOST_API = "https://blog.rugmunch.io"
GHOST_CONTENT_KEY = os.getenv("GHOST_CONTENT_API_KEY", "")
GHOST_ADMIN_KEY_FILE = "/srv/secrets/ghost_admin_key"
GHOST_SESSION_COOKIE = "/home/dev/rmi/backend/.secrets/ghost_session_cookies"
GHOST_SESSION_COOKIE_TALOS = "/srv/secrets/ghost_session_cookies"

PROFILE = {
    "name": "Crypto Rug Muncher",
    "handle": "@cryptorugmunch",
    "avatar": "https://rugmunch.io/logo.png",
    "bio": "Crypto security platform. Real-time scam detection, cross-chain forensics, AI-powered investigation. Free. No signup. MCP-native.",
    "website": "https://rugmunch.io",
}

POST_TYPES: dict[str, dict[str, Any]] = {
    "micro": {"tag": "micro", "label": "Post", "icon": "💬", "limit": 4000},
    "thread": {"tag": "thread", "label": "Thread", "icon": "🧵", "limit": 4000},
    "article": {"tag": "article", "label": "Longform", "icon": "📄", "limit": 50000},
    "dev": {"tag": "dev", "label": "Dev Update", "icon": "⚡", "limit": 4000},
    "announcement": {"tag": "announcement", "label": "Announcement", "icon": "📢", "limit": 4000},
    "newsletter": {"tag": "newsletter", "label": "Newsletter", "icon": "📰", "limit": 50000},
    "briefing": {"tag": "briefing", "label": "Briefing", "icon": "🔍", "limit": 50000},
}

TYPE_LIMITS = {k: v["limit"] for k, v in POST_TYPES.items()}


def _get_session_cookie() -> str | None:
    """Read saved session cookie for Ghost admin auth (legacy fallback).

    Tries the canonical Talos path first, then the legacy Hydra-era path.
    """
    for path in (GHOST_SESSION_COOKIE_TALOS, GHOST_SESSION_COOKIE):
        try:
            with open(path) as f:
                for line in f:
                    if "ghost-admin-api-session" in line:
                        return line.strip()
        except FileNotFoundError:
            continue
    return None


def _read_admin_key() -> str | None:
    """Ghost Admin API key (``id:secret``) from file, then env."""
    for path in (GHOST_ADMIN_KEY_FILE,):
        try:
            with open(path) as f:
                value = f.read().strip()
                if value:
                    return value
        except FileNotFoundError:
            continue
    return os.getenv("GHOST_ADMIN_API_KEY") or None


def _ghost_admin_jwt(admin_key: str) -> str:
    """Build a Ghost Admin API JWT from an ``id:secret`` key.

    Ghost requires an HS256 JWT with ``kid=<api-key-id>``, ``aud=/admin/``
    and ``exp`` ≤ 5 minutes; the secret is hex-decoded before signing.
    """
    kid, secret = admin_key.split(":", 1)

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}, separators=(",", ":")).encode())
    now = int(time.time())
    payload = _b64url(json.dumps({"iat": now, "exp": now + 300, "aud": "/admin/"}, separators=(",", ":")).encode())
    try:
        secret_bytes = bytes.fromhex(secret)
    except ValueError:
        secret_bytes = secret.encode()
    signing_input = f"{header}.{payload}".encode()
    signature = _b64url(hmac.new(secret_bytes, signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _get_admin_auth() -> tuple[str, str] | None:
    """Admin auth header: (``"Bearer"``, JWT) preferred, cookie fallback."""
    admin_key = _read_admin_key()
    if admin_key:
        try:
            return "Bearer", _ghost_admin_jwt(admin_key)
        except Exception as exc:  # malformed key — fall through to cookie
            logger.warning("content_platform:admin_jwt_failed err=%s", str(exc)[:120])
    session = _get_session_cookie()
    if session:
        return "Cookie", session
    return None


def _admin_headers(auth: tuple[str, str]) -> dict[str, str]:
    """Request headers for an admin auth tuple (Bearer JWT or Cookie)."""
    scheme, value = auth
    if scheme == "Bearer":
        return {
            "Content-Type": "application/json",
            "Origin": GHOST_API,
            "Authorization": f"Ghost {value}",
            "Accept-Version": "v5.0",
        }
    cookie_header = value.split("\t")
    cookie = f"{cookie_header[5]}={cookie_header[6]}" if len(cookie_header) > 6 else value
    return {
        "Content-Type": "application/json",
        "Origin": GHOST_API,
        "Cookie": cookie,
        "Accept-Version": "v5.0",
    }


# ── Lexical builder (markdown / Telegram-HTML → Ghost lexical) ──────────────

_INLINE_RE = re.compile(
    r"\[([^\]]+)\]\(([^)]+)\)"  # markdown link [text](url)
    r"|<a\s+href=\"([^\"]+)\"[^>]*>(.*?)</a>"  # telegram link <a href="url">text</a>
    r"|<b>(.*?)</b>"  # bold
    r"|<i>(.*?)</i>"  # italic
    r"|\*\*([^*]+)\*\*"  # markdown bold
    r"|(?<!\*)\*([^*\n]+)\*(?!\*)"  # markdown italic
)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BULLET_RE = re.compile(r"^[-•*](?:\s*[-•*])*\s+")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")


def _text_node(text: str, fmt: int = 0) -> dict[str, Any]:
    return {
        "detail": 0,
        "format": fmt,
        "mode": "normal",
        "style": "",
        "text": text,
        "type": "text",
        "version": 1,
    }


def _link_node(text: str, url: str) -> dict[str, Any]:
    return {
        "type": "link",
        "version": 1,
        "rel": None,
        "target": None,
        "title": None,
        "url": url,
        "children": [_text_node(text)],
    }


def _inline_to_children(text: str) -> list[dict[str, Any]]:
    """Parse inline bold/italic/links (markdown + Telegram HTML) into nodes."""
    children: list[dict[str, Any]] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            children.append(_text_node(text[pos : m.start()]))
        if m.group(1) is not None:  # [text](url)
            children.append(_link_node(m.group(1), m.group(2)))
        elif m.group(3) is not None:  # <a href="url">text</a>
            children.append(_link_node(m.group(4), m.group(3)))
        elif m.group(5) is not None:  # <b>text</b>
            children.append(_text_node(m.group(5), fmt=2))
        elif m.group(6) is not None:  # <i>text</i>
            children.append(_text_node(m.group(6), fmt=1))
        elif m.group(7) is not None:  # **text**
            children.append(_text_node(m.group(7), fmt=2))
        elif m.group(8) is not None:  # *text*
            children.append(_text_node(m.group(8), fmt=1))
        pos = m.end()
    if pos < len(text):
        children.append(_text_node(text[pos:]))
    return children or [_text_node("")]


def _markdown_to_lexical(content: str) -> dict[str, Any]:
    """Convert markdown-ish / Telegram-HTML content to Ghost lexical JSON.

    Handles ``#``/``##`` headings, ``-``/``•`` bullet lists, ``**bold**``,
    ``*italic*``, ``[text](url)`` links and Telegram ``<b>/<i>/<a href>``
    tags. Everything else renders as paragraphs.
    """
    content = _BR_RE.sub("\n", content)
    children: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", content):
        block = block.strip()
        if not block:
            continue
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        heading = _HEADING_RE.match(lines[0])
        if heading:
            children.append(
                {
                    "type": "heading",
                    "version": 1,
                    "children": _inline_to_children(heading.group(2)),
                    "direction": "ltr",
                    "format": "",
                    "indent": 0,
                    "tag": f"h{len(heading.group(1))}",
                }
            )
            lines = lines[1:]
            if not lines:
                continue
        if all(_BULLET_RE.match(ln) for ln in lines):
            items = [
                {
                    "type": "listitem",
                    "version": 1,
                    "children": _inline_to_children(_BULLET_RE.sub("", ln)),
                    "direction": "ltr",
                    "format": "",
                    "indent": 0,
                    "mode": "normal",
                    "style": "",
                    "value": 1,
                }
                for ln in lines
            ]
            children.append(
                {
                    "type": "list",
                    "version": 1,
                    "listType": "bullet",
                    "start": 1,
                    "direction": "ltr",
                    "format": "",
                    "indent": 0,
                    "mode": "normal",
                    "style": "",
                    "tag": "ul",
                    "children": items,
                }
            )
            continue
        for ln in lines:
            children.append(
                {
                    "type": "paragraph",
                    "version": 1,
                    "children": _inline_to_children(ln),
                    "direction": "ltr",
                    "format": "",
                    "indent": 0,
                    "textFormat": 0,
                    "textStyle": "",
                }
            )
    return {
        "root": {
            "children": children,
            "direction": "ltr",
            "format": "",
            "indent": 0,
            "type": "root",
            "version": 1,
        }
    }


async def create_post(
    content: str,
    post_type: str = "micro",
    title: str = "",
    tags: list[str] | None = None,
    status: str = "published",
    visibility: str = "public",
) -> dict[str, Any]:
    """Create any type of post on Ghost."""

    tags = list(dict.fromkeys((tags or []) + [POST_TYPES[post_type]["tag"]]))
    limit = TYPE_LIMITS.get(post_type, 4000)
    if len(content) > limit:
        content = content[: limit - 3] + "..."

    if not title:
        title = content[:120] + ("..." if len(content) > 120 else "")

    # Convert to Ghost lexical format (headings/bold/italic/links/lists kept)
    lexical = json.dumps(_markdown_to_lexical(content))

    payload = {
        "posts": [
            {
                "title": title,
                "lexical": lexical,
                "status": status,
                "visibility": visibility,
                "tags": [{"name": t} for t in tags],
                "feature_image": PROFILE["avatar"],
            }
        ]
    }

    auth = _get_admin_auth()
    if not auth:
        return {
            "error": "No Ghost admin credentials",
            "help": "Set /srv/secrets/ghost_admin_key or GHOST_ADMIN_API_KEY",
        }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{GHOST_API}/ghost/api/admin/posts/",
                headers=_admin_headers(auth),
                json=payload,
            )
            if resp.status_code in (200, 201):
                p = resp.json()["posts"][0]
                return {
                    "id": p["id"],
                    "title": p["title"],
                    "url": p.get("url", f"{GHOST_API}/{p.get('slug', '')}"),
                    "status": p["status"],
                    "created_at": p["created_at"],
                    "type": post_type,
                    "tags": [t["name"] for t in p.get("tags", [])],
                    "feature_image": p.get("feature_image", PROFILE["avatar"]),
                    "excerpt": p.get("custom_excerpt") or content[:140],
                }
            if resp.status_code == 401 and auth[0] == "Bearer":
                # JWT rejected — retry once with the legacy session cookie.
                session = _get_session_cookie()
                if session:
                    resp = await client.post(
                        f"{GHOST_API}/ghost/api/admin/posts/",
                        headers=_admin_headers(("Cookie", session)),
                        json=payload,
                    )
                    if resp.status_code in (200, 201):
                        p = resp.json()["posts"][0]
                        return {
                            "id": p["id"],
                            "title": p["title"],
                            "url": p.get("url", f"{GHOST_API}/{p.get('slug', '')}"),
                            "status": p["status"],
                            "created_at": p["created_at"],
                            "type": post_type,
                            "tags": [t["name"] for t in p.get("tags", [])],
                            "feature_image": p.get("feature_image", PROFILE["avatar"]),
                            "excerpt": p.get("custom_excerpt") or content[:140],
                        }
            if resp.status_code == 401:
                return {"error": "Ghost admin auth rejected (JWT + cookie)"}
            return {"error": f"Ghost: HTTP {resp.status_code}", "detail": resp.text[:300]}
    except Exception as e:
        return {"error": str(e)}


async def get_feed(page: int = 1, limit: int = 20, tag: str = "", include: str = "tags,authors") -> dict[str, Any]:
    """Get feed via Ghost Content API."""
    if not GHOST_CONTENT_KEY:
        return {"posts": [], "error": "content key not configured"}

    params: dict[str, str | int] = {
        "key": GHOST_CONTENT_KEY,
        "limit": limit,
        "page": page,
        "include": include,
        "order": "published_at DESC",
        "formats": "lexical",
    }
    if tag:
        params["filter"] = f"tag:{tag}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{GHOST_API}/ghost/api/content/posts/",
                params=params,
                headers={"Accept-Version": "v5.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                posts = []
                for p in data.get("posts", []):
                    ptags = [t["name"] for t in p.get("tags", [])]
                    post_type = next((t for t in POST_TYPES if POST_TYPES[t]["tag"] in ptags), "article")
                    posts.append(
                        {
                            "id": p["id"],
                            "title": p["title"],
                            "excerpt": (p.get("custom_excerpt") or p.get("excerpt") or "")[:200],
                            "url": p.get("url", ""),
                            "feature_image": p.get("feature_image", PROFILE["avatar"]),
                            "created_at": p.get("published_at") or p["created_at"],
                            "reading_time": p.get("reading_time", 0),
                            "tags": ptags,
                            "type": post_type,
                            "type_label": POST_TYPES.get(post_type, {}).get("label", "Post"),
                            "type_icon": POST_TYPES.get(post_type, {}).get("icon", "📝"),
                        }
                    )
                return {
                    "posts": posts,
                    "page": page,
                    "total": data.get("meta", {}).get("pagination", {}).get("total", 0),
                    "profile": PROFILE,
                    "post_types": dict(POST_TYPES.items()),
                }
            return {"posts": [], "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"posts": [], "error": str(e)}


async def create_thread(posts: list[str], post_type: str = "thread") -> dict[str, Any]:
    """Create a thread - multiple connected posts."""
    results = []
    for i, content in enumerate(posts):
        title = f"Thread {i + 1}/{len(posts)}" if len(posts) > 1 else ""
        result = await create_post(content, post_type=post_type, title=title)
        results.append(result)
    return {
        "thread": results,
        "total": len(results),
        "url": results[0].get("url") if results else None,
    }


async def generate_briefing() -> dict[str, Any]:
    """Generate daily intelligence briefing — RMI Morning Brief §3/§6 pipeline.

    Runs gather → draft → review → publish through
    ``app.domains.content.briefing`` and returns a ``{draft, review, published}``
    summary. Non-breaking: on a catastrophic pipeline failure it falls back to
    the legacy RAG-snippet stub (``_generate_briefing_legacy``).
    """
    from rugmunchbot._vendor.briefing import draft_briefing, gather_briefing_context, publish_briefing, review_briefing

    try:
        context = await gather_briefing_context()
        draft = await draft_briefing(context)
        review = review_briefing(draft, context)
        published = await publish_briefing(draft)
        summary: dict[str, Any] = {
            "draft": draft,
            "review": review,
            "published": published,
        }
        if not review["ok"]:
            summary["warning"] = f"Review flagged {len(review['flags'])} items — verify before publishing"
        return summary
    except Exception as e:  # keep the endpoint alive — fall back to legacy stub
        logger.warning("briefing_pipeline_failed — using legacy fallback err=%s", str(e)[:160])
        return await _generate_briefing_legacy()


async def _generate_briefing_legacy() -> dict[str, Any]:
    """Legacy daily-briefing stub: raw RAG snippets (pre-§6 behavior)."""
    content_parts = [f"# 🕵️ Daily Intelligence Briefing\n\n*{datetime.now(UTC).strftime('%B %d, %Y')}*\n"]

    # Pull from RAG
    try:
        from rugmunchbot._vendor.rugbrain_client import rugbrain_search

        for topic, query in [
            ("Scam Alerts", "rug pull honeypot scam today"),
            ("Exploit Watch", "defi exploit hack attack today"),
            ("Market Intel", "crypto market movement whale alert"),
        ]:
            results = await rugbrain_search(
                query=query,
                collections=["known_scams", "forensic_reports", "market_intel"],
                limit=2,
            )
            if results:
                content_parts.append(f"\n## {topic}")
                for r in results[:2]:
                    snippet = (r.get("content") or "")[:300]
                    if snippet:
                        content_parts.append(f"- {snippet}")
    except (ConnectionError, OSError, TimeoutError):
        content_parts.append("\n*Automated briefing - full RAG analysis pending*")

    content = "\n".join(content_parts)
    return await create_post(
        content=content,
        post_type="briefing",
        title=f"Daily Briefing - {datetime.now(UTC).strftime('%B %d, %Y')}",
    )


async def generate_newsletter() -> dict[str, Any]:
    """Generate weekly newsletter with top stories and analysis."""
    content_parts = [f"# 📰 Weekly Intelligence Report\n\n*Week of {datetime.now(UTC).strftime('%B %d, %Y')}*\n"]

    try:
        from rugmunchbot._vendor.rugbrain_client import rugbrain_search

        for section, query in [
            ("🔴 Top Threats", "major scam exploit hack this week"),
            ("🟡 Emerging Patterns", "new scam technique pattern emerging"),
            ("🟢 Market Overview", "crypto market weekly summary"),
            ("🔬 Deep Dive", "smart contract vulnerability analysis forensic"),
        ]:
            results = await rugbrain_search(
                query=query,
                collections=["known_scams", "forensic_reports", "market_intel", "token_analysis"],
                limit=3,
            )
            if results.get("results"):
                content_parts.append(f"\n## {section}")
                for r in results["results"][:3]:
                    snippet = (r.get("content") or "")[:400]
                    if snippet:
                        content_parts.append(f"- {snippet}")
    except (ConnectionError, OSError, TimeoutError):
        content_parts.append("\n*Full RAG analysis in next edition*")

    content = "\n".join(content_parts)
    return await create_post(
        content=content,
        post_type="newsletter",
        title=f"Weekly Intelligence - {datetime.now(UTC).strftime('%B %d, %Y')}",
    )
