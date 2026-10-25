"""Paid-placement disclosure for trending — count + badge (pure).

Paid placement must never be presented as organic. The badge marks each paid
row and the footer states how many of the N placements are paid, so a reader
can never infer that a promoted token earned its rank.
"""

from __future__ import annotations

PAID_BADGE = " \u00b7 paid"
# Typed source failure — never render an empty list as "no trending".
TRENDING_UNAVAILABLE_SOURCE = "unavailable (source)"


def disclosure_footer(total: int, paid: int) -> str:
    """Footer line: how many of the N shown placements are paid.

    Always emitted (even at 0) so the absence of promotion is stated, not
    merely implied.
    """
    total = max(int(total), 0)
    paid = max(min(int(paid), total), 0)
    if paid == 0:
        return f"0 of {total} are paid placements \u2014 no promotion on this list."
    return (
        f"{paid} of {total} are paid placements (\u00b7 paid) \u2014 "
        "disclosed, never ranked as organic."
    )
