"""Frozen data shapes for the Polymarket content engine.

Every field is either read straight from Polymarket's public APIs or computed
from those fields. Nothing here is inferred, scored or "risk-rated": the
engine publishes *public on-chain evidence*, never a verdict about a person.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SelectedMarket:
    """One open market chosen for a content card."""

    condition_id: str
    question: str
    slug: str
    category: str
    yes_price: float | None
    volume_usd: float
    volume_24h_usd: float
    liquidity_usd: float
    change_1d: float | None
    end_date: str | None
    hours_to_resolution: float | None
    url: str


@dataclass(frozen=True)
class FlowTrade:
    """A single public trade, with notional computed as shares x price."""

    wallet: str
    side: str
    outcome: str
    shares: float
    price: float
    notional_usd: float
    timestamp: int
    tx_hash: str


@dataclass(frozen=True)
class FlowEvidence:
    """Public trade flow around one market, stated as observations only."""

    condition_id: str
    trades: tuple[FlowTrade, ...] = ()
    low_history_wallets: tuple[str, ...] = ()
    same_outcome_wallets: tuple[str, ...] = ()
    total_notional_usd: float = 0.0
    window_note: str = ""
    limits_note: str = (
        "Public on-chain evidence only. Wallet labels are counts of markets "
        "traded, not identity. Nothing here is an accusation of wrongdoing."
    )
    sample: tuple[dict[str, object], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class WalletState:
    """Our own Polymarket wallet as the public API reports it."""

    address: str
    value_usd: float
    position_count: int
    positions: tuple[dict[str, object], ...] = ()
    value_known: bool = True

    @property
    def is_empty(self) -> bool:
        return self.value_usd <= 0 and self.position_count == 0

    def describe(self) -> str:
        if not self.value_known:
            return f"Our Polymarket wallet {self.address}: value unavailable from the public API."
        if self.is_empty:
            return f"Our Polymarket wallet {self.address} holds $0.00 and has no open positions."
        return (
            f"Our Polymarket wallet {self.address} holds ${self.value_usd:,.2f} "
            f"across {self.position_count} open position(s)."
        )


@dataclass(frozen=True)
class ContentDraft:
    """A rendered card plus the facts needed to audit it later."""

    condition_id: str
    text: str
    source_url: str
    dedupe_key: str
    generated_at: str
    dry_run: bool = True
