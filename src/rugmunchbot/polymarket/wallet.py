"""Our own Polymarket wallet, as the public Data API reports it.

The wallet ``0x1E3AC01d0fdb976179790BDD02823196A92705C9`` returned
``[{"value": 0}]`` from ``/value`` and ``[]`` from ``/positions`` when checked
on 2026-10-24. An unavailable value is reported as unavailable — never as $0.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.polymarket.models import WalletState


def build_wallet_state(
    address: str, value_usd: float | None, raw_positions: list[dict[str, Any]]
) -> WalletState:
    """Summarise the wallet honestly; ``value_usd=None`` means the API failed."""
    positions = tuple(p for p in raw_positions if isinstance(p, dict))
    return WalletState(
        address=address,
        value_usd=float(value_usd) if value_usd is not None else 0.0,
        position_count=len(positions),
        positions=positions,
        value_known=value_usd is not None,
    )
