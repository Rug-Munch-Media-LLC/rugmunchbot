#!/usr/bin/env python3
"""KOL wallet tracker — declared wallets only, alerts on NEW positions.

The rule that makes this feature defensible is the declaration: a wallet is
tracked only when the KOL publicly attached it to their handle. Nothing is
inferred from a wallet graph, a name match, or a hunch — an inferred wallet is
an accusation, and this feed is not in the accusation business.

A "new position" is a mint that was not in the previous snapshot and is above
the dust floor. Positions that shrink or vanish are reported separately as
exits, because an exit from a KOL wallet is the signal followers actually act
on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Ignore dust: a position under this notional (USD) is not a position.
DUST_USD = 250.0

#: Declaration provenance, most to least authoritative.
SOURCE_RANK: dict[str, int] = {"self-declared": 0, "platform-verified": 1, "partner-declared": 2}


@dataclass(frozen=True, slots=True)
class Declaration:
    """One KOL handle bound to one wallet, with where the binding came from."""

    handle: str
    address: str
    chain: str
    source: str = "self-declared"

    @property
    def tracked(self) -> bool:
        """Only ranked provenance is tracked; an unknown source is not."""
        return self.source in SOURCE_RANK


@dataclass(frozen=True, slots=True)
class Position:
    """One held mint and its notional value."""

    mint: str
    symbol: str
    usd: float


@dataclass
class TrackedKOL:
    """A declared KOL and the last snapshot we alerted from."""

    declaration: Declaration
    last: dict[str, Position] = field(default_factory=dict)

    def diff(self, current: list[Position]) -> tuple[list[Position], list[Position]]:
        """``(opened, closed)`` against the previous snapshot, dust excluded."""
        fresh = {p.mint: p for p in current if p.usd >= DUST_USD}
        opened = [p for mint, p in fresh.items() if mint not in self.last]
        closed = [pos for mint, pos in self.last.items() if mint not in fresh]
        self.last = fresh
        return opened, closed


class Registry:
    """Declared KOLs by handle, and by wallet for reverse lookups."""

    def __init__(self) -> None:
        self._by_handle: dict[str, TrackedKOL] = {}
        self._by_address: dict[str, str] = {}

    def declare(self, declaration: Declaration) -> bool:
        """Track a declaration. False when the provenance is not acceptable.

        One wallet belongs to one handle: a re-declaration moves the wallet,
        it does not let two handles share it (which would double-post).
        """
        if not declaration.tracked:
            return False
        handle = declaration.handle.strip().lstrip("@").lower()
        if not handle or not declaration.address:
            return False
        previous = self._by_address.get(declaration.address)
        if previous is not None and previous != handle:
            self._by_handle.pop(previous, None)
        self._by_address[declaration.address] = handle
        self._by_handle[handle] = TrackedKOL(
            declaration=Declaration(
                handle=handle,
                address=declaration.address,
                chain=declaration.chain,
                source=declaration.source,
            )
        )
        return True

    def drop(self, handle: str) -> bool:
        """Stop tracking a handle (a KOL withdrew the declaration)."""
        kol = self._by_handle.pop(handle.strip().lstrip("@").lower(), None)
        if kol is None:
            return False
        self._by_address.pop(kol.declaration.address, None)
        return True

    def get(self, handle: str) -> TrackedKOL | None:
        """The tracked KOL for a handle, or ``None``."""
        return self._by_handle.get(handle.strip().lstrip("@").lower())

    def for_wallet(self, address: str) -> TrackedKOL | None:
        """The tracked KOL that declared ``address``, or ``None``."""
        handle = self._by_address.get(address)
        return self._by_handle.get(handle) if handle else None

    def handles(self) -> list[str]:
        """Every tracked handle, sorted — the ``/koltrack`` listing."""
        return sorted(self._by_handle)

    def __len__(self) -> int:
        return len(self._by_handle)


def render_positions(kol: TrackedKOL, opened: list[Position], closed: list[Position]) -> str:
    """The alert card: what the KOL just bought and what they left."""
    lines = [
        f"👁️ <b>@{kol.declaration.handle}</b> — new position"
        if opened
        else f"👁️ <b>@{kol.declaration.handle}</b> — position change"
    ]
    for pos in opened[:4]:
        lines.append(f"\u2795 <b>{pos.symbol}</b> — ${pos.usd:,.0f}")
    for pos in closed[:4]:
        lines.append(f"\u2796 left <b>{pos.symbol}</b> (was ${pos.usd:,.0f})")
    lines += [
        "",
        f"<code>{kol.declaration.address}</code> ({kol.declaration.chain})",
        f"<i>declared by the KOL · {kol.declaration.source}</i>",
    ]
    return "\n".join(lines)


def render_listing(registry: Registry) -> str:
    """``/koltrack`` — who is tracked and on what basis."""
    if not len(registry):
        return (
            "👁️ <b>KOL tracker</b>\n\nNo declared wallets yet.\n"
            "A wallet is tracked only when the KOL publicly declares it — "
            "run /koltrack add &lt;handle&gt; &lt;address&gt; &lt;chain&gt; and it "
            "enters review."
        )
    lines = ["👁️ <b>Tracked KOL wallets</b>", ""]
    for handle in registry.handles():
        kol = registry.get(handle)
        if kol is None:
            continue
        lines.append(
            f"• <b>@{handle}</b> — <code>{kol.declaration.address[:10]}…</code> "
            f"({kol.declaration.chain}, {kol.declaration.source})"
        )
    lines += ["", "<i>Declared wallets only. A new position alerts within one tick.</i>"]
    return "\n".join(lines)
