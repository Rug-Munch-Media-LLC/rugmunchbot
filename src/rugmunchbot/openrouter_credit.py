#!/usr/bin/env python3
"""RugMunchBot -- owner-only OpenRouter credit balance check.

Powers ``/credits`` and the ``/admin`` dashboard credit line. Calls
``GET https://openrouter.ai/api/v1/key`` with the configured backup OpenRouter
key and surfaces usage / limit / remaining / free-tier status.

The API key is resolved from ``app.llm_config.OPENROUTER_BACKUP_API_KEY``
(env ``OPENROUTER_BACKUP_API_KEY``, falling back to ``OPENROUTER_API_KEY``) —
it is NEVER logged, echoed or returned from this module. Fail-open: any
network / auth / JSON error returns ``ok=False`` and the caller renders a
graceful line instead of crashing the command.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")

OPENROUTER_KEY_ENDPOINT = "https://openrouter.ai/api/v1/key"
_CREDIT_TIMEOUT = 8.0


def _fmt_usd(value: float | None) -> str:
    """Render a USD amount (or ``unlimited`` when the field is absent)."""
    if value is None:
        return "unlimited"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "unlimited"


async def fetch_openrouter_credit() -> dict[str, Any]:
    """Return the OpenRouter key credit summary (never the key itself).

    Response keys: ``ok``, ``usage``, ``limit``, ``limit_remaining``,
    ``is_free_tier``, ``error``. ``limit`` / ``limit_remaining`` are ``None``
    when the key has no cap (OpenRouter reports ``limit: null`` for uncapped
    keys); ``usage`` is the cumulative USD spend.
    """
    try:
        from app.llm_config import OPENROUTER_BACKUP_API_KEY

        api_key = (OPENROUTER_BACKUP_API_KEY or "").strip()
    except Exception as exc:  # pragma: no cover - import-time guard
        logger.debug("openrouter key config unavailable: %s", exc)
        return {"ok": False, "error": "key_not_configured"}
    if not api_key:
        return {"ok": False, "error": "key_not_configured"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://rugmunch.io",
        "X-Title": "RMI Bot Admin",
    }
    try:
        async with httpx.AsyncClient(timeout=_CREDIT_TIMEOUT) as client:
            r = await client.get(OPENROUTER_KEY_ENDPOINT, headers=headers)
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as exc:
        logger.debug("openrouter credit check failed: %s", exc)
        return {"ok": False, "error": "network"}

    if r.status_code != 200:
        return {"ok": False, "error": f"http_{r.status_code}"}
    try:
        data = r.json().get("data") or {}
    except ValueError:
        return {"ok": False, "error": "bad_json"}

    def _num(key: str) -> float | None:
        val = data.get(key)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    usage = _num("usage")
    limit = _num("limit")
    limit_remaining = _num("limit_remaining")
    if limit_remaining is None and limit is not None and usage is not None:
        limit_remaining = max(0.0, limit - usage)
    return {
        "ok": True,
        "usage": usage,
        "limit": limit,
        "limit_remaining": limit_remaining,
        "is_free_tier": bool(data.get("is_free_tier", False)),
        "error": None,
    }


def format_credit_summary(data: dict[str, Any]) -> str:
    """Render the OpenRouter credit block for ``/credits`` and ``/admin``.

    HTML-safe (used inside ParseMode.HTML messages). Never renders the key.
    """
    if not data.get("ok"):
        error = str(data.get("error") or "unknown")
        hint = " \u2014 set OPENROUTER_BACKUP_API_KEY" if error == "key_not_configured" else ""
        return f"\u26a0\ufe0f OpenRouter credit check unavailable ({error}){hint}"
    usage = data.get("usage")
    limit = data.get("limit")
    remaining = data.get("limit_remaining")
    if usage is None and limit is None and remaining is None:
        return "\U0001f4b3 OpenRouter: <b>no usage data</b>"
    free_note = " (free tier)" if data.get("is_free_tier") else ""
    lines = [f"\U0001f4b3 <b>OpenRouter credits</b>{free_note}"]
    if usage is not None:
        lines.append(f"\U0001f4c8 Usage: <b>{_fmt_usd(usage)}</b>")
    if limit is None:
        lines.append("\u267b\ufe0f Limit: <b>unlimited</b>")
    elif remaining is not None:
        lines.append(f"\U0001f9fe Remaining: <b>{_fmt_usd(remaining)}</b> / {_fmt_usd(limit)}")
    else:
        lines.append(f"\u267b\ufe0f Limit: <b>{_fmt_usd(limit)}</b>")
    return "\n".join(lines)
