"""Scanner scoring constants — OSS empty default.

The live bot flags "bluechip" addresses from a private static set
(``app.domains.scanner.unified.scoring.BLUECHIP_ADDRESSES``). OSS ships an
empty set; self-hosters can populate it.
"""

from __future__ import annotations

BLUECHIP_ADDRESSES: frozenset[str] = frozenset()

__all__ = ["BLUECHIP_ADDRESSES"]
