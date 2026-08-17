"""Chat routing — best-effort scan + bridge context fetches (fail-open).

``_fetch_scan_context`` reads the Redis-cached quick scanner;
``_fetch_bridge_context`` pulls live context from the Chat RAG Bridge
(port 8095). Both are best-effort: they return ``None`` on any failure so
chat never blocks on them.
"""

from __future__ import annotations

import re
from typing import Any, cast

import httpx

from app.domains.telegram.rugmunchbot.config import backend_headers
from app.domains.telegram.rugmunchbot.formatters import detect_chain
from app.domains.telegram.rugmunchbot.handlers.chat_ai._markup import _format_scan_summary
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._constants import (
    BRIDGE_CONTEXT_ENDPOINT,
    BRIDGE_TIMEOUT,
    SCAN_ENDPOINT,
    SCAN_TIMEOUT,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.chat_ai")


async def _fetch_scan_context(address: str) -> str | None:
    """Best-effort quick scanner read → compact risk summary line.

    ``POST /api/v1/bot/scan`` is Redis-cached and fast; on any failure or
    missing data it returns ``None`` so chat never blocks on the scanner.
    """
    chain = detect_chain(address)
    try:
        async with httpx.AsyncClient(timeout=SCAN_TIMEOUT) as client:
            r = await client.post(
                SCAN_ENDPOINT,
                headers=backend_headers(),
                json={"address": address, "chain": chain},
            )
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("scan context unavailable (addr=%.16s): %s", address, e)
        return None
    if r.status_code != 200:
        logger.debug("scan context HTTP %d (addr=%.16s)", r.status_code, address)
        return None
    try:
        raw = r.json()
    except ValueError:
        return None
    if not isinstance(raw, dict) or raw.get("error"):
        return None
    data = cast("dict[str, Any]", raw)
    return _format_scan_summary(address, data)


async def _fetch_bridge_context(
    message: str,
    history: list[dict[str, str]] | None = None,
) -> tuple[str, list[dict[str, str]], list[dict[str, str]]] | None:
    """Live context from the Chat RAG Bridge (port 8095, fail-open).

    Returns ``(blocks, sources, actions)``:
      * ``blocks`` — one compact block combining the bridge's upstream
        context: SearXNG web search (live internet), RAG knowledge, market
        digest, token scanner and exploit rundown.
      * ``sources`` — the bridge's grounding citation metadata
        (``[{"type": "web"|"rag"|"scanner"|"market", ...}]``) used to render
        the real "Grounded in N intelligence sources" footer + source lines.
      * ``actions`` — suggested inline-button actions (deep-dive on a token,
        exploit rundown) built by the bridge's rundown block.

    ``None`` on any failure so chat never blocks on it.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(BRIDGE_TIMEOUT, connect=3.0, read=BRIDGE_TIMEOUT)) as client:
            r = await client.post(
                BRIDGE_CONTEXT_ENDPOINT,
                json={"message": message[:8000], "history": history},
            )
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("bridge context unavailable (user msg %.40s): %s", message, e)
        return None
    if r.status_code != 200:
        logger.debug("bridge context HTTP %d (user msg %.40s)", r.status_code, message)
        return None
    try:
        raw = r.json()
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    blocks = raw.get("blocks") if isinstance(raw.get("blocks"), dict) else {}
    if not blocks:
        return None
    # Order matters: scanner (address-specific) > web (live) > rag > market.
    parts: list[str] = []
    for key in ("scanner_context", "web_context", "rag_context", "market_context", "rundown_context"):
        val = (blocks.get(key) or "").strip()
        if val:
            # Strip meta-instructions the bridge embeds for ITS OWN llama call
            # ("introduce these with 'I found this on the web:'") — those leak
            # into our prompt and make the model parrot context instead of
            # answering. Keep the data, drop the stage directions.
            val = re.sub(
                r"\[[^\]]*\]\s*Fresh web results \(current[^)]*\)[:\-]?\s*",
                "[Web results (live)]\n",
                val,
            )
            val = re.sub(
                r"introduce these with ['\"]I found this on the web:['\"]\s*",
                "",
                val,
            )
            val = re.sub(r"\n{3,}", "\n\n", val).strip()
            if val:
                parts.append(val)
    if not parts:
        return None

    raw_sources = raw.get("sources")
    sources = raw_sources if isinstance(raw_sources, list) else []
    sources = [s for s in sources if isinstance(s, dict)]
    raw_actions = raw.get("actions")
    actions = raw_actions if isinstance(raw_actions, list) else []
    actions = [a for a in actions if isinstance(a, dict)]

    return "\n\n".join(parts), sources, actions
