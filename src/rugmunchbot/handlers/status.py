"""/status — live data-source tier health (win 49).

Ops transparency: shows every data layer, whether it answered recently,
and what falls back to what. Users see the machine is healthy; operators
see exactly which tier degraded before anyone asks.
"""

from __future__ import annotations

import html
import time
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from rugmunchbot.config._endpoints import backend_headers

_START = time.monotonic()


def _e(s: Any) -> str:
    return html.escape(str(s), quote=False)


async def _probe(
    name: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[str, bool, float]:
    import httpx

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            if json_body is not None:
                r = await c.post(url, json=json_body, headers=headers or {})
            else:
                r = await c.get(url, headers=headers or {})
        ms = (time.monotonic() - t0) * 1000
        return name, r.status_code == 200, ms
    except Exception:
        return name, False, (time.monotonic() - t0) * 1000


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    wait = await update.message.reply_text("🩺 Checking all data tiers...")
    from rugmunchbot.config._endpoints import BACKEND_URL

    checks = await __import__("asyncio").gather(
        _probe(
            "backend",
            f"{BACKEND_URL}/api/v1/market/hyperliquid-liquidations?limit=5",
            headers=backend_headers(),
        ),
        _probe("gateway-engines", "http://127.0.0.1:8010/health"),
        _probe("deep-embedder", "http://127.0.0.1:8004/health"),
        _probe("reranker-4b", "http://127.0.0.1:8003/health"),
        _probe("chat-8b", "http://127.0.0.1:8009/health"),
    )

    def _mark(ok: bool, ms: float) -> str:
        return f"{'✅' if ok else '❌'} ({ms:.0f}ms)"

    lines = [
        "<b>━━━ SYSTEM STATUS ━━━</b>",
        f"<i>uptime {time.monotonic() - _START:.0f}s this process</i>",
        "",
        "<b>Data Sources</b>",
        f"  {_mark(*checks[0])} backend (liq aggregator)",
        f"  {_mark(*checks[1])} model gateway (5 engines)",
        "",
        "<b>Model Services</b>",
        f"  {_mark(*checks[2])} deep embedder 4B (:8004)",
        f"  {_mark(*checks[3])} reranker 4B (:8003)",
        f"  {_mark(*checks[4])} chat 8B (:8009)",
        "",
        "<b>Fallback Doctrine</b>",
        "  own index → free pool → keyed freemium → proxy → paid",
    ]
    try:
        from rugmunchbot.tools import get_registry

        reg = get_registry()
        rows = []
        for tool, st in sorted(reg.stats.items()):
            if st.get("calls"):
                rows.append(
                    f"  · {_e(tool)}: {st['calls']} calls, "
                    f"{st['ok']} ok, {st['errors']} err, {st['cached']} cached"
                )
        if rows:
            lines.extend(["", "<b>Tool Metrics (this process)</b>", *rows])
    except Exception:
        pass
    lines.extend(["", "via @rugmunchbot · rugmunch.io"])
    await wait.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def register_status(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    app.add_handler(CommandHandler("status", cmd_status))
