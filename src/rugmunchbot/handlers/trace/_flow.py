#!/usr/bin/env python3
"""
RugMunchBot -- /trace command: Cross-chain fund flow tracing.

Hunter tier and above. Traces fund flows across chains, identifies
funding sources, exchange interactions, and connected wallets.
"""

from __future__ import annotations

import asyncio
import html
from typing import Any

from rugmunchbot._vendor.ai_guardrails import sanitize_output
from rugmunchbot.config import (
    fmt_number,
)
from rugmunchbot.formatters import (
    short_addr,
    thin_sep,
)
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.trace")

EXCHANGE_LABELS = {
    "binance": "🏦 Binance",
    "coinbase": "🏦 Coinbase",
    "kraken": "🏦 Kraken",
    "bybit": "🏦 Bybit",
    "okx": "🏦 OKX",
    "gate": "🏦 Gate.io",
    "kucoin": "🏦 KuCoin",
    "mexc": "🏦 MEXC",
    "bitget": "🏦 Bitget",
    "huobi": "🏦 HTX",
    "unknown_exchange": "🏦 Exchange",
}



# ── Own-Data fund-flow trace + AI read ─────────────────────────────────────
# /trace runs a deterministic fund-flow trace against OUR OWN ClickHouse
# chain-indexer data (wallet_flow engine: evm_transfers + wallet_labels +
# dex_events), then hands a compact summary to the snappy LLM for a
# plain-language read. Both layers fail open: ClickHouse down → fall back to
# the legacy external-API path; LLM down/timeout → deterministic flow only.

#: Hard cap for the ClickHouse flow-summary call — never stall the command.
#: Matches the engine's internal budget (10s hop queries + 30s USD oracle
#: normalization + buffer); subsequent calls hit the 5m Redis cache.
_TRACE_FLOW_TIMEOUT = 45.0
#: Hard cap for the AI flow-read LLM call (snappy tier) — never stall a reply.
_TRACE_AI_TIMEOUT = 8.0
#: Max chars of rendered LLM output (LLM text is untrusted — always escaped).
_TRACE_AI_MAX_CHARS = 900

_TRACE_READ_SYSTEM = (
    "You are the RugMunch fund-flow analyst. Write 2-3 SHORT plain-English "
    "sentences interpreting a wallet's fund-flow trace for a crypto "
    "investigator. Use ONLY the facts provided in the trace JSON — never "
    "invent amounts, addresses, exchange names or claims. Describe how the "
    "funds moved (e.g. funded by X, routed to Y, ending at exchange Z), "
    "where they ended up (exchange vs non-exchange share), and whether the "
    "pattern suggests cashing out, accumulation, or wash-like round-tripping. "
    "No markdown, no emojis, no headers, no apologies."
)


async def _fetch_clickhouse_flow(address: str, chain: str) -> dict[str, Any] | None:
    """Best-effort fund-flow summary from our own ClickHouse chain-indexer data.

    Wraps the bot-owned ``build_flow_summary`` (canonical
    ``rugmunch_scanner.unified.engines.wallet_flow`` graph: hop-1/hop-2
    evm_transfers, wallet_labels, dex_events). Never raises — returns ``None``
    on any failure so /trace fails open to the legacy external-API path.
    """
    try:
        from rugmunchbot.handlers.trace._flow_summary import build_flow_summary

        summary = await asyncio.wait_for(
            build_flow_summary(address, chain, hops=2),
            timeout=_TRACE_FLOW_TIMEOUT,
        )
        if not summary or not isinstance(summary, dict):
            return None
        if int(summary.get("total_tx_count") or 0) == 0:
            return None
        return summary
    except Exception as exc:  # fail-open boundary for /trace
        logger.debug("trace_clickhouse_flow_failed: %s", exc)
        return None


async def _render_ai_flow_read(flow_summary: dict[str, Any]) -> str:
    """Ask the snappy LLM for a plain-language read of the deterministic flow.

    Passes the compact JSON summary (only measured facts) to
    ``chat_with_fallback(messages, "snappy")`` bounded by ``_TRACE_AI_TIMEOUT``.
    Fail-open: timeout, rate-limit, blank answer all return "".
    """
    if not flow_summary:
        return ""
    import json

    from rugmunchbot.llm_config import chat_with_fallback

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _TRACE_READ_SYSTEM},
        {
            "role": "user",
            "content": (
                "Wallet fund-flow trace (JSON, only facts we measured):\n"
                f"{json.dumps(flow_summary, default=str)[:1000]}\n\n"
                "Write the flow read now."
            ),
        },
    ]
    try:
        text = await asyncio.wait_for(chat_with_fallback(messages, "snappy"), timeout=_TRACE_AI_TIMEOUT)
    except Exception as e:  # fail-open boundary for the AI read
        logger.warning("trace AI read failed: %s", e)
        text = ""
    text = (text or "").strip()
    if not text:
        return ""
    return f"🧠 <b>AI Flow Read</b>\n{html.escape(sanitize_output(text))[:_TRACE_AI_MAX_CHARS]}"


def _fmt_counterparty(cp: dict[str, Any], usd_unavailable: bool) -> str:
    """One line for a top counterparty: addr + type emoji + label + $/tx."""
    addr = short_addr(str(cp.get("address") or ""))
    ctype = str(cp.get("type") or "")
    label = str(cp.get("label") or "")
    usd = float(cp.get("usd") or 0)
    tx = int(cp.get("tx_count") or 0)
    emoji = {
        "CEX": "🏦",
        "DEX": "💱",
        "bridge": "🌉",
        "mixer": "🌪️",
        "contract": "📄",
    }.get(ctype, "")
    parts = [f"<code>{addr}</code>"]
    if emoji:
        parts.append(emoji)
    if label:
        parts.append(label)
    if usd_unavailable or usd <= 0:
        parts.append(f"{tx} tx")
    else:
        parts.append(f"${fmt_number(usd)} · {tx} tx")
    return "  " + " ".join(parts)


def _render_flow_block(summary: dict[str, Any]) -> list[str]:
    """Deterministic 'Fund Flow (own chain data)' section from the summary."""
    d = thin_sep()
    usd_unavailable = bool(summary.get("usd_unavailable"))
    in_tx = int(summary.get("in_tx_count") or 0)
    out_tx = int(summary.get("out_tx_count") or 0)

    if usd_unavailable:
        flow_line = f"  ⬇️ In: <b>{in_tx} tx</b>  ⬆️ Out: <b>{out_tx} tx</b> (USD n/a)"
    else:
        flow_line = (
            f"  ⬇️ In: <b>{fmt_number(float(summary.get('in_usd') or 0))}</b> ({in_tx} tx)  "
            f"⬆️ Out: <b>{fmt_number(float(summary.get('out_usd') or 0))}</b> ({out_tx} tx)"
        )

    exch = summary.get("exchange") or {}
    if usd_unavailable:
        share_pct = float(exch.get("share_tx_pct") or 0)
        share_label = f"{share_pct:.0f}% (by tx)"
    else:
        share_pct = float(exch.get("share_pct") or 0)
        share_label = f"{share_pct:.0f}%"

    lines = [
        "🧭 <b>Fund Flow (own chain data)</b>",
        d,
        flow_line,
        f"  🏦 Exchange share: <b>{share_label}</b> ({int(exch.get('tx_count') or 0)} exchange tx)",
    ]

    top_in = summary.get("counterparty_in") or []
    if top_in:
        lines.extend(["", "  ⬇️ <b>Top senders</b>"])
        for cp in top_in[:3]:
            lines.append(_fmt_counterparty(cp, usd_unavailable))

    top_out = summary.get("counterparty_out") or []
    if top_out:
        lines.extend(["", "  ⬆️ <b>Top recipients</b>"])
        for cp in top_out[:3]:
            lines.append(_fmt_counterparty(cp, usd_unavailable))

    wash = summary.get("wash") or {}
    if wash.get("signal"):
        lines.extend(
            [
                "",
                f"  🔁 <b>Wash pattern</b>: {int(wash.get('reciprocal_pairs') or 0)} round-trip "
                f"pair(s), {int(wash.get('tx_count') or 0)} tx",
            ]
        )
    return lines


