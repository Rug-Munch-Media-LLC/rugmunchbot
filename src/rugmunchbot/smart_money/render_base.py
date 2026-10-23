"""Shared post skeleton: headline, evidence, source, timestamp, risk note."""

from __future__ import annotations

from typing import Any

from rugmunchbot.channels._story_cta import attach_story_cta
from rugmunchbot.channels.formatters._primitives import NewsPost

from ._fmt import stamp


def compose(
    *,
    headline: str,
    evidence: list[str],
    source: str,
    updated: Any,
    risk: str,
    lane: str = "",
    payload: dict[str, Any] | None = None,
) -> NewsPost:
    """Assemble the five-block post contract every channel post must satisfy.

    When *lane* is given the canonical story CTA row is appended (story slug
    from *payload* when present, else the lane's section page). Lanes that
    predate the contract pass no lane and get the unchanged skeleton.
    """
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
    text = "\n".join(lines)
    if lane:
        text = attach_story_cta(text, payload, lane)
    return NewsPost(text)
