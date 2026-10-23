"""Long-chat-history compression for the remote LLM prompt.

History is REFERENCE ONLY (see ``_clients``) but it still has to survive the
prompt budget. The old path kept the last 2 turns at 300 chars each, which
for a long thread meant:

  * the tail of every turn was cut mid-sentence,
  * addresses / ``$TICKERS`` mentioned past char 300 vanished — the model then
    "re-asked" or contradicted a fact the user had already given,
  * nothing at all survived from turns 3+.

This module replaces blind truncation with a compressed block:

1. **Deterministic base** — the most recent turns, each trimmed to its own
   window, joined as inert ``[role]`` lines (never real turns: a trailing bare
   user turn makes the model *continue* the old answer instead of answering
   the new question).
2. **Measured compression** — when the block exceeds ``MIN_COMPRESS_CHARS`` of
   prose the cc-compress / LLMLingua lane (``127.0.0.1:8077``, extractive,
   identifier-guarded) shrinks it. Measured on Talos 2026-10-24: ratio 0.34,
   ``identifier_guard=passed``, 0.6 ms. Enabled by default for this path
   (``CC_COMPRESS_CHAT_HISTORY=0`` disables); every failure falls OPEN to the
   deterministic block, so the lane being down is never a chat outage.
3. **Identifier re-attachment** — after compression, every address / ``$TICKER``
   from the original block is checked; any that the extractive pass deleted is
   re-appended on a ``FACTS`` line. The remote guard is the first line of
   defence, this check is the one that decides.
"""

from __future__ import annotations

import os
import re

import httpx

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.chat_ai")

# Budgets (chars). Snappy = fast tier: leaner block, faster answer.
_HISTORY_MSGS = 6
_HISTORY_CHARS = 600
_SNAPPY_HISTORY_MSGS = 4
_SNAPPY_HISTORY_CHARS = 400

_RATE = 0.5
_TIMEOUT_S = 2.0
MIN_COMPRESS_CHARS = 240
_DEFAULT_URL = "http://127.0.0.1:8077/compress"

# Facts the extractive pass must never delete (checked after the call).
_FACT_RE = re.compile(r"\$[A-Za-z]{2,12}\b|\b0x[a-fA-F0-9]{40}\b|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


def _endpoint() -> str | None:
    """Compression endpoint, or ``None`` when the path is disabled."""
    if os.getenv("CC_COMPRESS_CHAT_HISTORY", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    return os.getenv("CC_COMPRESS_URL") or os.getenv("CC_COMPRESS_PACKS_URL") or _DEFAULT_URL


def _base_block(history: list[dict[str, str]], msgs: int, chars: int) -> str:
    """Deterministic ``[role] content`` block from the most recent turns."""
    lines: list[str] = []
    for msg in history[-msgs:]:
        role = msg.get("role", "user")
        content = str(msg.get("content") or "")
        if role in ("user", "assistant") and content:
            lines.append(f"[{role}] {content[:chars]}")
    return "\n".join(lines)


async def _compress(block: str) -> str:
    """Compress ``block`` through the lane; return it UNCHANGED on any failure."""
    url = _endpoint()
    if url is None or len(block) < MIN_COMPRESS_CHARS:
        return block
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(url, json={"text": block, "rate": _RATE})
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # fail-open: a lane outage is not a chat outage
        logger.debug("chat history compression unavailable: %s", exc)
        return block
    out = str(payload.get("compressed_prompt") or "")
    if not out or len(out) >= len(block):
        return block
    logger.debug(
        "chat history compressed %s→%s tokens (guard=%s)",
        payload.get("origin_tokens"),
        payload.get("compressed_tokens"),
        payload.get("identifier_guard"),
    )
    return out


def _reattach_facts(original: str, compressed: str) -> str:
    """Re-append any address / ticker the extractive pass dropped."""
    missing: list[str] = []
    for fact in _FACT_RE.findall(original):
        if fact not in compressed and fact not in missing:
            missing.append(fact)
    if not missing:
        return compressed
    return f"{compressed}\nFACTS (verbatim): {' '.join(missing[:20])}"


async def compress_history(history: list[dict[str, str]] | None, *, is_snappy: bool = False) -> str:
    """Render ``history`` as a compressed, fact-preserving prompt block.

    Returns ``""`` when there is no usable history, so callers can skip the
    block entirely. Never raises: the deterministic block is the floor.
    """
    if not history:
        return ""
    msgs = _SNAPPY_HISTORY_MSGS if is_snappy else _HISTORY_MSGS
    chars = _SNAPPY_HISTORY_CHARS if is_snappy else _HISTORY_CHARS
    block = _base_block(history, msgs, chars)
    if not block:
        return ""
    compressed = await _compress(block)
    return _reattach_facts(block, compressed)


__all__ = ["compress_history"]
