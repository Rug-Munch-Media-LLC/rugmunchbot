#!/usr/bin/env python3
"""Scam-callout feed: evidence cards that name the exact check that failed.

A callout is only worth posting if the reader can audit it. So every card is
built from the scanner's own check ids — the same ids the backend returns —
and each failing check carries the reason it is a scam signal in plain words.

A single heuristic is never a callout. ``min_checks`` defaults to two
independent failing checks, which is the same "two observations" rule the
shill lane uses; one failing check is a *suspicion* and goes to the review
queue instead, never to subscribers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Severity order, worst first. ``critical`` alone is not enough to publish —
#: two failing checks are (see ``eligible``).
SEVERITY: tuple[str, ...] = ("critical", "high", "medium", "low")

#: Check ids the backend scanner emits, mapped to the "why" the reader sees.
WHY: dict[str, str] = {
    "honeypot": "transfers can be blocked — buys go in, sells do not come out",
    "mint_authority_live": "the deployer can mint more supply at will and dump on you",
    "freeze_authority_live": "the deployer can freeze your wallet, so your tokens are not really yours",
    "owner_can_modify_tax": "sell tax can be raised to 99% after you buy",
    "lp_not_locked": "liquidity can be pulled at any moment, taking the pool with it",
    "lp_unlocked_soon": "the liquidity lock expires soon — the rug window is open",
    "top10_hold_over_50": "a handful of wallets hold the float and can exit in one block",
    "bundled_buys": "one actor bought the launch across many wallets to fake demand",
    "wash_trading": "volume is self-dealt — the tape is advertising, not liquidity",
    "deployer_rug_history": "this deployer has rugged before under other names",
    "fresh_wallets_only": "buyers are hours-old wallets funded from one source",
    "hidden_owner": "an undocumented owner key can still change the contract",
    "proxy_upgradeable": "the logic can be swapped after you buy",
    "blacklist_function": "wallets can be blacklisted from selling",
    "trading_cooldown": "a cooldown can trap sellers for hours",
}

#: Minimum independent failing checks before a callout is publishable.
MIN_CHECKS = 2


@dataclass(frozen=True, slots=True)
class Check:
    """One scanner finding: its id, severity, and the evidence line."""

    check_id: str
    severity: str
    detail: str = ""

    @property
    def why(self) -> str:
        """Plain-words reason, falling back to the scanner's own detail."""
        return WHY.get(self.check_id) or self.detail or "flagged by the scanner"


@dataclass(frozen=True, slots=True)
class Callout:
    """A scam callout: one token, its failing checks, its sources."""

    address: str
    chain: str
    symbol: str
    score: float
    checks: tuple[Check, ...]
    sources: tuple[str, ...] = field(default_factory=tuple)

    @property
    def failing(self) -> tuple[Check, ...]:
        """Checks that actually failed, worst severity first."""
        ordered = sorted(
            self.checks,
            key=lambda c: SEVERITY.index(c.severity) if c.severity in SEVERITY else len(SEVERITY),
        )
        return tuple(c for c in ordered if c.severity in ("critical", "high", "medium"))


def eligible(callout: Callout) -> bool:
    """Two independent failing checks, or nothing goes to subscribers."""
    return len(callout.failing) >= MIN_CHECKS


def build(
    address: str,
    chain: str,
    symbol: str,
    score: float,
    checks: list[Check],
    sources: tuple[str, ...] = (),
) -> Callout:
    """Assemble a callout, deduping checks by id (one id is one observation)."""
    seen: dict[str, Check] = {}
    for check in checks:
        seen.setdefault(check.check_id, check)
    return Callout(address, chain, symbol, float(score), tuple(seen.values()), sources)


def evidence_lines(callout: Callout) -> list[str]:
    """``check_id — why`` lines, capped so the card stays readable."""
    return [f"• <code>{c.check_id}</code> — {c.why}" for c in callout.failing[:5]]


def render(callout: Callout, *, tier: str) -> str:
    """The subscriber card: verdict, evidence, and how to verify it."""
    lines = [
        f"🛑 <b>SCAM CALLOUT — {callout.symbol}</b> ({callout.chain})",
        f"Risk <b>{callout.score:.0f}/100</b> · {len(callout.failing)} failing checks",
        "",
        "<b>Why this is a scam</b>",
        *evidence_lines(callout),
        "",
    ]
    if callout.sources:
        lines.append("Sources: " + " · ".join(callout.sources[:3]))
    lines.append(f"Verify: /scan {callout.address}")
    if tier == "free":
        lines.append("<i>Preview card — /premium for the full feed.</i>")
    return "\n".join(lines)


def feed_preview(callouts: list[Callout], *, limit: int = 5) -> str:
    """The ``/scamfeed`` digest: one line per callout, newest first."""
    usable = [c for c in callouts if eligible(c)][:limit]
    if not usable:
        return "🕊️ No confirmed scam callouts in the current window."
    lines = ["🛑 <b>Recent scam callouts</b>", ""]
    for callout in usable:
        top = callout.failing[0].check_id if callout.failing else "unknown"
        lines.append(
            f"• <b>{callout.symbol}</b> ({callout.chain}) — {callout.score:.0f}/100 "
            f"· <code>{top}</code>"
        )
    lines += ["", "Run /scamfeed <code>symbol</code> for the evidence card."]
    return "\n".join(lines)


def subscriber_only(
    user_id: int,
    *,
    resolve_tier: Callable[[int], str] | None = None,
) -> tuple[bool, str]:
    """Feed access: any paid tier. Refusal names the command that lifts it."""
    from rugmunchbot.premium.gating import gate

    return gate(user_id, "scam_feed", resolve_tier=resolve_tier)
