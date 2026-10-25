"""News-headline tool — live top headlines from our own backend news feed.

Split out of ``tools/trending`` when trending became a pure engine proxy:
headlines are a separate data source (``/api/v1/news/trending``) and were
dropped by that refactor, breaking the tool registry import contract.
"""

from __future__ import annotations

from rugmunchbot.tools.registry import ToolResult, backend_json_get


async def news_headlines(*, limit: int = 5) -> ToolResult:
    """Real top news headlines from our backend news feed."""
    body = await backend_json_get("/api/v1/news/trending", params={"limit": limit}, timeout=6.0)
    if not body:
        return ToolResult(
            tool="news_headlines",
            ok=False,
            error="no live news (backend /api/v1/news/trending unavailable)",
        )
    items = body.get("items") or body.get("articles") or []
    headlines = [
        {"title": it["title"].strip(), "url": it.get("url") or "", "source": it.get("source") or ""}
        for it in items[:limit]
        if isinstance(it, dict) and (it.get("title") or "").strip()
    ]
    if not headlines:
        return ToolResult(tool="news_headlines", ok=False, error="no headlines returned")
    return ToolResult(
        tool="news_headlines",
        ok=True,
        data={"headlines": headlines},
        symbols=[],
        addresses=[],
        source="backend:/api/v1/news/trending",
    )


__all__ = ["news_headlines"]
