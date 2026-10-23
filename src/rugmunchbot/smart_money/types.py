"""Value types for the smart-money / whale content surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Feed:
    """One fetched feed and the honest state of that fetch.

    ``items`` is empty when every source failed or returned nothing.  The
    caller must render the fallback message using ``note`` + ``tried`` — it
    must never invent a number to fill the gap.
    """

    kind: str
    items: tuple[dict[str, Any], ...] = ()
    source: str = ""
    updated_at: str = ""
    note: str = ""
    tried: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.items)
