"""
👛 Wallet Portfolio Scanner
=============================
Scan wallets and return token holdings with portfolio value.
Supports comma-separated multi-address scanning.

FAIL-LOUD contract (audit 2026-08-21 §F): wallet holdings come from OUR
backend via :func:`backend_json_get`, which RAISES :class:`BackendApiError`
on any non-200 / network failure. A dead backend must surface its HTTP
status — the old code swallowed every non-200 into an empty dict, so users
saw "$0.00 / no holdings" and could not tell a dead backend from an empty
wallet. Genuine no-data is the backend's honest contract:
200 + ``{"data": null, "reason": "no_data"}`` → graceful empty state.

[REVIEW] The backend ``/wallet/{addr}/tokens`` route currently passes raw
provider payloads through (e.g. Alchemy JSON-RPC ``result.tokenBalances`` —
hex balances, no symbols, no USD prices). Those shapes are DETECTED and
reported honestly ("N holdings found, valuation unavailable") instead of
being rendered as $0.00; a priced-holdings enrichment on the backend side
is the real fix.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import BackendApiError
from rugmunchbot.tools.registry import backend_json_get

logger = get_logger("rmi.portfolio")


def _to_float(value: Any) -> float | None:
    """Best-effort float; ``None`` when the value is missing/garbage."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN guard


def _empty_result() -> dict[str, Any]:
    return {
        "balance_usd": 0.0,
        "top_tokens": [],
        "tx_count": 0,
        "error": None,
        "no_data": False,
        "unvalued_tokens": 0,
    }


def _parse_portfolio_body(body: Any) -> dict[str, Any]:
    """Normalize the backend ``/wallet/{addr}/tokens`` response.

    Handled shapes (in order):
      1. Honest no-data contract: 200 + ``{"data": null, "reason": "no_data"}``
         → ``no_data=True`` (graceful empty state, NOT an error).
      2. Enriched shape: top-level ``balance_usd`` / ``top_tokens`` / ``tx_count``.
      3. Generic list shapes: ``{"tokens": [...]}`` / ``{"balances": [...]}``
         (also nested under ``data``). Items carrying a USD value contribute
         to ``balance_usd``; value-less items are counted as ``unvalued_tokens``.
      4. Raw provider passthrough (Alchemy JSON-RPC ``result.tokenBalances``):
         counted as ``unvalued_tokens`` — never rendered as $0.00.
    """
    result = _empty_result()
    if not isinstance(body, dict):
        return result

    # 1. Honest no-data contract (backend convenience.py::_wallet_no_data).
    if body.get("data") is None and body.get("reason") == "no_data":
        result["no_data"] = True
        return result

    # 2. Enriched shape.
    if "balance_usd" in body or "top_tokens" in body:
        result["balance_usd"] = _to_float(body.get("balance_usd")) or 0.0
        tokens = body.get("top_tokens")
        result["top_tokens"] = (
            [t for t in tokens if isinstance(t, dict)] if isinstance(tokens, list) else []
        )
        tx = body.get("tx_count")
        result["tx_count"] = int(tx) if isinstance(tx, (int, float)) else 0
        return result

    # 3. Generic list shapes (top-level or nested under ``data``).
    items: list[Any] = []
    for key in ("tokens", "balances"):
        v = body.get(key)
        if isinstance(v, list):
            items = v
            break
    data = body.get("data")
    if not items and isinstance(data, list):
        items = data
    if not items and isinstance(data, dict):
        for key in ("tokens", "balances"):
            v = data.get(key)
            if isinstance(v, list):
                items = v
                break
    if items:
        total = 0.0
        have_value = False
        norm: list[dict[str, Any]] = []
        for t in items:
            if not isinstance(t, dict):
                continue
            val = _to_float(t.get("value_usd", t.get("value", t.get("usd_value"))))
            if val is not None:
                have_value = True
                total += val
            norm.append(
                {
                    "symbol": t.get("symbol") or t.get("token_symbol") or "???",
                    "name": t.get("name") or t.get("token_name") or "",
                    "balance": t.get("balance", t.get("amount", t.get("balance_raw", ""))),
                    "value_usd": val if val is not None else 0,
                    "mint": t.get("mint") or t.get("address") or t.get("contract_address") or "",
                }
            )
        result["top_tokens"] = norm
        if have_value:
            result["balance_usd"] = total
        else:
            result["unvalued_tokens"] = len(norm)
        return result

    # 4. Raw provider passthrough (Alchemy JSON-RPC shape).
    inner = body.get("result")
    token_balances = inner.get("tokenBalances") if isinstance(inner, dict) else None
    if isinstance(token_balances, list):
        result["unvalued_tokens"] = len([x for x in token_balances if isinstance(x, dict)])
        return result

    return result


async def _fetch_portfolio_from_backend(address: str) -> dict[str, Any]:
    """Fetch wallet holdings from OUR backend — LOUD on any real failure.

    Returns the normalized result dict; ``error`` is set (with the HTTP
    status) when the backend call fails, ``no_data`` when the backend
    honestly reports an empty wallet. Never raises.
    """
    result = _empty_result()
    try:
        body = await backend_json_get(f"/api/v1/databus/wallet/{address}/tokens", timeout=10.0)
    except BackendApiError as exc:
        result["error"] = (
            f"backend unavailable (HTTP {exc.status_code})"
            if exc.status_code
            else "backend unreachable"
        )
        logger.debug("portfolio backend error for %s: %s", address, exc)
        return result
    return _parse_portfolio_body(body)


async def _fetch_solana_portfolio(client: Any, address: str) -> dict[str, Any]:
    """Fetch Solana token holdings (own backend first — loud contract).

    Returns dict with {balance_usd, top_tokens: [{symbol, name, balance,
    value_usd, mint}], tx_count, error, no_data, unvalued_tokens}.
    """
    return await _fetch_portfolio_from_backend(address)


async def _fetch_evm_portfolio(client: Any, address: str) -> dict[str, Any]:
    """Fetch EVM token balances (own backend first — loud contract)."""
    return await _fetch_portfolio_from_backend(address)


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

    from rugmunchbot.formatters import (
        is_evm,
        is_sol,
    )

    client = None  # backend helper owns its pooled client; kept for signature compat

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
        label = f"👛 <b>Portfolio #{addr_idx}:</b> <code>{address}</code>"
        # LOUD (audit §F): a backend failure is NOT an empty wallet — show
        # the honest reason instead of "$0.00 / no holdings".
        if portfolio.get("error"):
            await msg.reply_text(
                f"{label}\n\n⚠️ Could not load holdings — {portfolio['error']}. Try again shortly.",
                parse_mode="HTML",
            )
            continue
        # Honest no-data contract: graceful empty state.
        if portfolio.get("no_data"):
            await msg.reply_text(
                f"{label}\n\nNo token holdings found for this wallet.",
                parse_mode="HTML",
            )
            continue
        # Raw provider passthrough detected (holdings exist but carry no USD
        # prices yet) — report the count instead of a fabricated $0.00.
        unvalued = int(portfolio.get("unvalued_tokens") or 0)
        if unvalued and not portfolio.get("top_tokens") and not portfolio.get("balance_usd"):
            await msg.reply_text(
                f"{label}\n\n🪙 {unvalued} token holdings found, but USD valuation is "
                "unavailable for this wallet right now.",
                parse_mode="HTML",
            )
            continue
        try:
            top_tokens = portfolio.get("top_tokens", [])
            balance = portfolio.get("balance_usd", 0)
            tx_count = portfolio.get("tx_count", 0)

            # Build display lines
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
                    lines.append(
                        f"  <code>{i:<2} {sym:<14} {bal_str:<12} {val_str:<10} {pct_str}</code>"
                    )

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

        except (ConnectionError, OSError, TimeoutError) as e:
            logger.error("Portfolio failed for %s: %s", address, e)
            await msg.reply_text(
                f"❌ Could not scan <code>{address}</code>. Make sure the address is valid.",
                parse_mode="HTML",
            )


__all__ = ["cmd_portfolio"]
