"""Address-risk rule: turn the worst scanner verdict into an Action."""

from __future__ import annotations

from rugmunchbot.moderation import messages
from rugmunchbot.moderation.models import (
    Action,
    AddressVerdict,
)


def evaluate_address(
    verdicts: dict[str, AddressVerdict], policy, snippet: str = ""
) -> Action | None:
    """Escalate the worst address verdict into a warning (or delete) action."""
    bad = [v for v in verdicts.values() if v.hazardous]
    if not bad:
        return None
    worst = min(bad, key=lambda v: (v.verdict != "avoid", v.safety_score or 99))
    delete = bool(policy.address_delete or worst.verdict == "avoid")
    card = messages.address_warning(
        worst.address,
        worst.chain,
        worst.verdict,
        worst.reason or "scanner flagged it",
        policy.appeal_note,
    )
    return Action(
        rule="address_risk",
        action="delete" if delete else "warn",
        reason=f"{worst.verdict} address ({worst.reason})",
        matched=worst.address or snippet,
        reply=card,
        delete=delete,
        meta={"address": worst.address, "verdict": worst.verdict, "score": worst.safety_score},
    )


__all__ = ["evaluate_address"]
