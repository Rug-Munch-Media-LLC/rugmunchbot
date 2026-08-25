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

from rugmunchbot.config import backend_headers
from rugmunchbot.formatters import detect_chain
from rugmunchbot.handlers.chat_ai._markup import _format_scan_summary
from rugmunchbot.handlers.chat_ai._routing._constants import (
    BRIDGE_CONTEXT_ENDPOINT,
    BRIDGE_TIMEOUT,
    SCAN_ENDPOINT,
    SCAN_TIMEOUT,
)
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.chat_ai")


async def _fetch_scan_context(address: str) -> str | None:
    """Best-effort quick scanner read → compact risk summary line.

    ``POST /api/v1/bot/scan`` is Redis-cached and fast; on any failure or
    missing data it returns ``None`` so chat never blocks on the scanner.

    2026-08-25 quality push: $TICKER / free-text targets resolve to a real
    contract address first (DexScreener search) so scan-intent questions
    about a SYMBOL ground on our scanner forensics instead of answering
    from thin market data.
    """
    chain = detect_chain(address)
    if not chain or chain == "unknown":
        # Not an address shape → try ticker resolution before giving up.
        try:
            from rugmunchbot.tools.scanner._resolve_target import resolve_scan_target

            resolved = await resolve_scan_target(address)
            if resolved is None:
                return None
            address = resolved.address
            chain = resolved.chain or ""
        except Exception:
            return None
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

    Quick health check first (1s) — skips entirely when the bridge is
    down so chat never stalls on it. Returns ``(blocks, sources, actions)``
    or ``None`` on any failure.
    """
    # Quick health check — skip the bridge if it's not responding
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            hr = await client.get(f"{BRIDGE_CONTEXT_ENDPOINT.rsplit('/', 1)[0]}/health")
            if hr.status_code != 200:
                return None
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError):
        return None

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(BRIDGE_TIMEOUT, connect=3.0, read=BRIDGE_TIMEOUT)
        ) as client:
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
    for key in (
        "scanner_context",
        "web_context",
        "rag_context",
        "market_context",
        "rundown_context",
    ):
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
