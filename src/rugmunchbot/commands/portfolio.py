"""
👛 Wallet Portfolio Scanner
=============================
Scan wallets and return token holdings with portfolio value.
Supports comma-separated multi-address scanning.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, backend_headers
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

logger = get_logger("rmi.portfolio")


async def _fetch_solana_portfolio(client: httpx.AsyncClient, address: str) -> dict[str, Any]:
    """Fetch Solana token balances from Shyft (free tier).

    Returns dict with {balance_usd, top_tokens: [{symbol, name, balance,
    value_usd, mint}]}.
    """
    result: dict[str, Any] = {
        "balance_usd": 0.0,
        "top_tokens": [],
        "tx_count": 0,
        "error": None,
    }
    # Try backend first
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/databus/wallet/{address}/tokens", headers=backend_headers(), timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            result["balance_usd"] = float(data.get("balance_usd", 0))
            result["top_tokens"] = data.get("top_tokens", [])
            result["tx_count"] = data.get("tx_count", 0)
            return result
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("Backend portfolio failed for %s: %s", address, exc)

    return result


async def _fetch_evm_portfolio(client: httpx.AsyncClient, address: str) -> dict[str, Any]:
    """Fetch EVM token balances.

    Uses backend first, falls back to basic info.
    """
    result: dict[str, Any] = {
        "balance_usd": 0.0,
        "top_tokens": [],
        "tx_count": 0,
        "error": None,
    }
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/databus/wallet/{address}/tokens", headers=backend_headers(), timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            result["balance_usd"] = float(data.get("balance_usd", 0))
            result["top_tokens"] = data.get("top_tokens", [])
            result["tx_count"] = data.get("tx_count", 0)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("Backend EVM portfolio failed for %s: %s", address, exc)

    return result


def _format_usd(value: float) -> str:
    """Format USD value nicely."""
    if value >= 1000:
        return f"${value:,.2f}"
    return f"${value:.4f}"


async def cmd_portfolio(update, ctx) -> None:
    """Handle /portfolio command — scan one or more wallet addresses.

    Accepts comma-separated addresses to compare portfolios.
    Shows mini-table with token, balance, price, value, and % of portfolio.
    """
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "👛 <b>Usage:</b> <code>/portfolio &lt;address1&gt;[,address2,...]</code>\n\n"
            "Examples:\n"
            "  <code>/portfolio 0x...single</code>\n"
            "  <code>/portfolio 0x...one, So1...two</code>\n\n"
            "Shows token holdings, estimated value, and portfolio breakdown.",
            parse_mode="HTML",
        )
        return

    raw_addresses = args[0]
    addresses = [a.strip() for a in raw_addresses.split(",") if a.strip()]
    max_addrs = min(len(addresses), 3)  # limit to 3 addresses max
    addresses = addresses[:max_addrs]

    await msg.chat.send_action("typing")

    from app.domains.telegram.rugmunchbot.formatters import (
        is_evm,
        is_sol,
    )

    client = get_shared_client(25)
    # Fetch every address concurrently (was a sequential per-address loop
    # — up to 3 x 10s worst case). Unrecognized formats yield None.
    async def _fetch_portfolio(address: str) -> dict[str, Any] | None:
        if is_sol(address):
            return await _fetch_solana_portfolio(client, address)
        if is_evm(address):
            return await _fetch_evm_portfolio(client, address)
        return None

    # Re-arm typing — the parallel fetch takes as long as the slowest
    # address, so the indicator would otherwise expire mid-wait.
    await msg.chat.send_action("typing")
    portfolios = await asyncio.gather(*(_fetch_portfolio(a) for a in addresses))

    for addr_idx, (address, portfolio) in enumerate(zip(addresses, portfolios, strict=True), 1):
        if portfolio is None:
            await msg.reply_text(
                f"❌ Unrecognized address format: <code>{address}</code>",
                parse_mode="HTML",
            )
            continue
        try:
            top_tokens = portfolio.get("top_tokens", [])
            balance = portfolio.get("balance_usd", 0)
            tx_count = portfolio.get("tx_count", 0)

            # Build display lines
            label = f"👛 <b>Portfolio #{addr_idx}:</b> <code>{address}</code>"

            lines = [label, ""]

            # Summary row
            summary_parts = []
            summary_parts.append(f"💰 <b>Value:</b> {_format_usd(balance)}")
            summary_parts.append(f"📝 <b>TX:</b> {tx_count:,}" if tx_count else "📝 <b>TX:</b> N/A")
            lines.append(" | ".join(summary_parts))

            # Token holdings table
            if top_tokens:
                lines.extend(["", "🪙 <b>Holdings</b>"])

                # Calculate total value for percentages
                total_value = sum(float(t.get("value_usd", 0)) for t in top_tokens)
                if total_value == 0:
                    total_value = balance

                # Header row
                lines.append("  <code>#  Symbol         Balance    Value       %</code>")

                for i, t in enumerate(top_tokens[:8], 1):
                    sym = (t.get("symbol") or "???")[:10]
                    bal = t.get("balance", t.get("balance_raw", ""))
                    val = float(t.get("value_usd", 0))
                    val_str = _format_usd(val)
                    pct = (val / total_value * 100) if total_value > 0 else 0

                    # Format balance
                    if isinstance(bal, (int, float)) and bal > 0:
                        if bal >= 1000:
                            bal_str = f"{bal:,.2f}"
                        elif bal >= 1:
                            bal_str = f"{bal:.4f}"
                        elif bal >= 0.001:
                            bal_str = f"{bal:.6f}"
                        else:
                            bal_str = f"{bal:.8f}"
                        bal_str = bal_str[:12]
                    else:
                        bal_str = "—"
                        val_str = _format_usd(val or 0)
                        pct = (val / total_value * 100) if total_value > 0 else 0

                    pct_str = f"{pct:.1f}%"
                    lines.append(f"  <code>{i:<2} {sym:<14} {bal_str:<12} {val_str:<10} {pct_str}</code>")

                lines.append(f"\n  <b>Total:</b> {_format_usd(total_value)}")

            if not top_tokens:
                lines.append("\n  No token holdings data available.")

            lines.append("")

            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:4000] + "\n\n<i>...truncated</i>"

            await msg.reply_text(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.error("Portfolio failed for %s: %s", address, e)
            await msg.reply_text(
                f"❌ Could not scan <code>{address}</code>. Make sure the address is valid.",
                parse_mode="HTML",
            )


__all__ = ["cmd_portfolio"]
