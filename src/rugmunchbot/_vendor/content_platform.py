# VENDORED 2026-08-17 from rmi-backend/app/content_platform.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
Ghost Content Platform - @CryptoRugMunch Feed engine.

Post types:
  micro      - Short posts (≤4000 chars, like X posts)
  thread     - Connected series of posts (X-style threads)
  article    - Standard longform blog post
  dev        - Development updates
  announcement - Platform announcements
  newsletter - Weekly deep-dive newsletter
  briefing   - Daily intelligence briefing (auto-generated)
  paid       - Premium gated content

Auth: Session cookie from owner login. Cookie refreshed on 401.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

GHOST_API = "https://blog.rugmunch.io"
GHOST_CONTENT_KEY = os.getenv("GHOST_CONTENT_API_KEY", "")
GHOST_SESSION_COOKIE = "/home/dev/rmi/backend/.secrets/ghost_session_cookies"

PROFILE = {
    "name": "Rug Munch Intelligence",
    "handle": "@CryptoRugMunch",
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
    """Read saved session cookie for Ghost admin auth."""
    try:
        with open(GHOST_SESSION_COOKIE) as f:
            for line in f:
                if "ghost-admin-api-session" in line:
                    return line.strip()
    except FileNotFoundError:
        pass
    return None


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

    # Convert to Ghost lexical format
    paragraphs = content.split("\n\n")
    children = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
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

    lexical = json.dumps(
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

    session = _get_session_cookie()
    if not session:
        return {"error": "No Ghost session cookie", "help": "Login at /ghost first"}

    try:
        cookie_header = session.split("\t")
        cookie = f"{cookie_header[5]}={cookie_header[6]}" if len(cookie_header) > 6 else session
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{GHOST_API}/ghost/api/admin/posts/",
                headers={"Content-Type": "application/json", "Origin": GHOST_API, "Cookie": cookie},
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
            elif resp.status_code == 401:
                return {"error": "Session expired - re-login at /ghost"}
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
        from rugmunchbot._vendor.rag_pkg.compat import three_pillar_search

        for topic, query in [
            ("Scam Alerts", "rug pull honeypot scam today"),
            ("Exploit Watch", "defi exploit hack attack today"),
            ("Market Intel", "crypto market movement whale alert"),
        ]:
            results = await three_pillar_search(
                query=query,
                collections=["known_scams", "forensic_reports", "market_intel"],
                limit=2,
            )
            if results.get("results"):
                content_parts.append(f"\n## {topic}")
                for r in results["results"][:2]:
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
        from rugmunchbot._vendor.rag_pkg.compat import three_pillar_search

        for section, query in [
            ("🔴 Top Threats", "major scam exploit hack this week"),
            ("🟡 Emerging Patterns", "new scam technique pattern emerging"),
            ("🟢 Market Overview", "crypto market weekly summary"),
            ("🔬 Deep Dive", "smart contract vulnerability analysis forensic"),
        ]:
            results = await three_pillar_search(
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
