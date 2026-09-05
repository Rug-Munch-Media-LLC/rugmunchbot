"""Data/image fetching helpers for the daily market briefs."""

from __future__ import annotations

from typing import Any

from rugmunchbot.channels.daily_market_summary._config import logger


def _pick_report_image(context: dict[str, Any]) -> str:
    """Resolve the report image from the TOP STORY's article URL (og:image).

    2026-09-01 FINAL: story-doc ``image_url`` metadata is UNTRUSTED —
    existing Qdrant docs carry the operator's brand banner from the
    pre-fix era (it shipped on posts 7879/7883/7886). The report image
    resolves ONLY from the top story's own article URL via the thumb
    proxy; no image → "" → plain-text posting. Sync wrapper over the
    async resolver (the caller is sync context).
    """
    import asyncio

    from rugmunchbot.support.image_policy import resolve_post_image

    for item in context.get("news") or []:
        if not isinstance(item, dict):
            continue
        source_url = str(item.get("url") or item.get("source_url") or "").strip()
        if not source_url.startswith(("http://", "https://")):
            continue
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                # Called from async context — schedule and wait
                resolved = asyncio.create_task(resolve_post_image("news", source_url=source_url))
                return resolved.result() if resolved.done() else ""
            resolved = asyncio.run(resolve_post_image("news", source_url=source_url))
            if resolved:
                return resolved
        except Exception as exc:
            logger.debug(
                "report_image_resolve_failed src=%s err=%s", source_url[:60], str(exc)[:80]
            )
            continue
    return ""
