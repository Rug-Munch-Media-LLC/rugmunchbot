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
    bad = [
        v
        for v in verdicts.values()
        if v.verdict == "avoid"
        or (
            v.verdict == "caution"
            and (v.safety_score is None or v.safety_score < policy.address_score)
        )
    ]
    if not bad:
        return None
    worst = min(bad, key=lambda v: (v.verdict != "avoid", v.safety_score or 99))
    action = policy.address_avoid_action if worst.verdict == "avoid" else policy.address_action
    delete = bool(policy.address_delete or action == "delete")
    card = messages.address_warning(
        worst.address,
        worst.chain,
        worst.verdict,
        worst.reason or "scanner flagged it",
        policy.appeal_note,
    )
    return Action(
        rule="address_risk",
        action=action,
        reason=f"{worst.verdict} address ({worst.reason})",
        matched=worst.address or snippet,
        reply=card,
        delete=delete,
        meta={"address": worst.address, "verdict": worst.verdict, "score": worst.safety_score},
    )


__all__ = ["evaluate_address"]
