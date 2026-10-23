"""Shared post skeleton: headline, evidence, source, timestamp, risk note."""

from __future__ import annotations

from typing import Any

from rugmunchbot.channels.formatters._primitives import NewsPost

from ._fmt import stamp


def compose(
    *, headline: str, evidence: list[str], source: str, updated: Any, risk: str
) -> NewsPost:
    """Assemble the five-block post contract every channel post must satisfy."""
    lines = [headline, ""]
    lines.extend(e for e in evidence if e)
    lines.extend(
        [
            "",
            f"<b>Source:</b> {source or 'unknown'}",
            f"<b>Time:</b> {stamp(updated)}",
            f"<b>Risk:</b> {risk}",
        ]
    )
    return NewsPost("\n".join(lines))
