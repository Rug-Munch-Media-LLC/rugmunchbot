"""Plain-text content card renderer.

Editorial contract (``standards`` + editorial-standards skill):
  * every number carries its source (Polymarket Gamma / Data API) and a URL;
  * ``public on-chain evidence`` wording only — no ``insider``, ``manipulator``,
    ``scam`` or any accusation, and no wallet-to-person attribution;
  * risk + "not financial advice" line on every card;
  * referral/builder disclosure printed only when an operator-supplied value
    exists — the engine never constructs a referral link;
  * truncate, never invent: a missing number is rendered as ``n/a``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from rugmunchbot.polymarket.models import FlowEvidence, SelectedMarket, WalletState

MAX_CARD_CHARS = 3800


def _usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:,.0f}"


def _short(wallet: str) -> str:
    return f"{wallet[:6]}…{wallet[-4:]}" if len(wallet) > 12 else wallet


def _odds(market: SelectedMarket) -> str:
    if market.yes_price is None:
        return "YES n/a"
    if market.change_1d is None:
        return f"YES {market.yes_price * 100:.1f}%"
    return f"YES {market.yes_price * 100:.1f}% ({market.change_1d * 100:+.1f} pts/24h)"


def _timing(market: SelectedMarket) -> str:
    if not market.end_date:
        return "End date n/a"
    if market.hours_to_resolution is None:
        return f"Ends {market.end_date}"
    return f"Ends {market.end_date} (~{market.hours_to_resolution:.0f}h)"


def _flow_lines(flow: FlowEvidence) -> list[str]:
    if not flow.trades:
        return ["No taker trades at or above the threshold in the sampled window."]
    lines = [
        f"• {_short(t.wallet)} {t.side} {t.outcome} — {t.shares:,.0f} sh @ "
        f"{t.price:.3f} = ${t.notional_usd:,.0f}"
        for t in flow.trades
    ]
    if flow.total_notional_usd:
        lines.append(
            f"Observed total: ${flow.total_notional_usd:,.0f} across sampled public trades."
        )
    if flow.low_history_wallets:
        sample = ", ".join(_short(w) for w in flow.low_history_wallets[:3])
        lines.append(f"Low-history wallets in sample: {len(flow.low_history_wallets)} ({sample}).")
    if flow.same_outcome_wallets:
        lines.append(
            f"Same-outcome concentration: {len(flow.same_outcome_wallets)} distinct wallets "
            "on one outcome in the window."
        )
    return lines


def _disclosures(referral_url: str | None, builder_code: str | None) -> list[str]:
    lines: list[str] = []
    if referral_url:
        lines.append(f"Referral: {referral_url} — we may earn a share of Polymarket fees.")
    if builder_code:
        lines.append(
            f"Builder code: {builder_code} — orders placed through our builder profile may "
            "carry builder fees for us (Polymarket cap: 100 bps taker / 50 bps maker)."
        )
    return lines


def render_content_card(
    market: SelectedMarket,
    flow: FlowEvidence,
    wallet: WalletState,
    *,
    generated_at: datetime,
    referral_url: str | None = None,
    builder_code: str | None = None,
) -> str:
    """Render one card. Output is plain text (no parse_mode) and size-capped."""
    parts: list[str] = [
        "🎯 Polymarket market watch",
        "",
        market.question,
        f"{_odds(market)} · 24h vol {_usd(market.volume_24h_usd)} · "
        f"liquidity {_usd(market.liquidity_usd)} · total vol {_usd(market.volume_usd)}",
        f"{_timing(market)} · category: {market.category}",
        f"Source: {market.url}",
        "",
        "Public flow (Polymarket Data API, taker trades):",
        *_flow_lines(flow),
    ]
    if flow.window_note:
        parts.append(flow.window_note)
    parts.extend(
        [
            "",
            wallet.describe(),
            "",
            flow.limits_note,
            "Public on-chain data shows trades and market counts, not identity or intent.",
            "",
            "Not financial advice. Prediction markets can lose the full stake and are "
            "restricted in some jurisdictions.",
        ]
    )
    parts.extend(_disclosures(referral_url, builder_code))
    parts.append(f"Generated {generated_at:%Y-%m-%d %H:%M} UTC")
    text = "\n".join(parts)
    return text[:MAX_CARD_CHARS]
