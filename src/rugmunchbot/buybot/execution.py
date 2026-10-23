"""Injectable swap seam for the buy buttons.

Nothing in this module holds a key, signs anything, or pretends a trade
happened. With no swap service configured the executor returns a *preview*
result carrying the reason, and the caller shows that reason verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = ["BuyExecutor", "BuyRequest", "ExecutionResult", "SwapService"]

PREVIEW_REASON = "swap service not configured — nothing was signed, sent or settled"


class SwapService(Protocol):
    """An operator-provided adapter that submits a real swap and returns its tx hash."""

    async def buy(self, request: BuyRequest) -> str:
        """Submit *request* and return the transaction hash (empty/None on failure)."""
        ...


@dataclass(frozen=True)
class BuyRequest:
    """Everything a swap adapter needs. No private key is ever part of it."""

    chat_id: int
    user_id: int
    chain: str
    token: str
    amount: float
    idempotency_key: str


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of a buy attempt. ``preview`` is NOT success."""

    status: str
    reason: str
    tx: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "submitted"


class BuyExecutor:
    """Runs the configured swap adapter, or explains why it cannot."""

    def __init__(self, swap: SwapService | None = None) -> None:
        self._swap = swap

    @property
    def configured(self) -> bool:
        return self._swap is not None

    async def buy(self, request: BuyRequest) -> ExecutionResult:
        """Submit *request*, or return a preview when no adapter is configured."""
        if self._swap is None:
            return ExecutionResult("preview", PREVIEW_REASON)
        try:
            tx = await self._swap.buy(request)
        except Exception as error:  # surfaced to the user, never swallowed
            return ExecutionResult("failed", f"swap failed: {type(error).__name__}")
        if not tx:
            return ExecutionResult("failed", "swap service returned no transaction")
        return ExecutionResult("submitted", "submitted", str(tx))
