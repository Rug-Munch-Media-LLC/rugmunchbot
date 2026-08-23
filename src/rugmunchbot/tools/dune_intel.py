"""Dune on-chain analytics — curated query digest as a bot tool.

Wraps the vendored dune provider (real data only; "—" on failure per its
doctrine). Gives chat/market-analysis access to curated on-chain metrics.
"""

from __future__ import annotations

import logging

from rugmunchbot.tools.registry import ToolResult

logger = logging.getLogger(__name__)


async def dune_intel(*, limit: int = 3) -> ToolResult:
    """ToolResult: digest of curated Dune queries (real on-chain data)."""
    try:
        from rugmunchbot._vendor.dune import dune_onchain_intel

        payload = await dune_onchain_intel(limit=max(1, min(int(limit), 10)))
        queries = payload.get("queries") or {}
        ok_count = sum(1 for q in queries.values() if "error" not in q)
        return ToolResult(tool="dune_intel", ok=ok_count > 0, data={
            "source": "dune",
            "queries_ok": ok_count,
            "queries_total": len(queries),
            "digest": payload,
        })
    except Exception as exc:
        logger.warning("dune_intel:failed err=%s", str(exc)[:140])
        return ToolResult(tool="dune_intel", ok=False, data={"error": f"dune unavailable: {exc!r}"[:160]})
