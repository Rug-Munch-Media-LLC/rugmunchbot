"""Launchpad detection — OSS fail-open no-op.

The live bot detects token launchpads via a private engine registry
(``app.domains.scanner.engines.launchpad_registry``). OSS returns ``None``
so the scan report simply omits the launchpad line.
"""

from __future__ import annotations

from typing import Any


def detect_launchpad(deployer_addr: str, chain: str) -> dict[str, Any] | None:
    """No-op: no launchpad registry in OSS."""
    return None


__all__ = ["detect_launchpad"]
