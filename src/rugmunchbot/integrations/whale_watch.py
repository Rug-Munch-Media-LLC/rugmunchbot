"""Whale watcher adapter — OSS fail-open no-op.

The live bot pulls whale movements from a private Helius monitor
(``app.domains.alerts.whale_watch``). OSS returns ``[]`` so the alpha
channel simply has no whale-move section until an operator wires one.
"""

from __future__ import annotations

from typing import Any


def collect_whale_alerts(**kwargs: Any) -> list[Any]:
    """No-op: no whale monitor in OSS."""
    return []


__all__ = ["collect_whale_alerts"]
