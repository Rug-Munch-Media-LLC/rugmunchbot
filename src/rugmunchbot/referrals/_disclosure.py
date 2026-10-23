"""Single-source referral disclosure text."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._types import Referral

#: Shown next to every rendered referral link (FTC-style attribution).
DISCLOSURE = "\u24d8 We may earn a fee when you trade through this link."


def disclosure_text(referral: Referral) -> str:
    """Disclosure for a referral — empty when there is nothing to link."""
    return DISCLOSURE if referral.available else ""
