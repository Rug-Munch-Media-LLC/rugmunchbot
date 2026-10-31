"""Grounding footer for chat answers (split out of ``_delivery.py``).

The footer names the intelligence sources that actually fed an answer. Names
are untrusted bridge metadata: they are scrubbed for internal API references
and HTML-escaped here, after the answer's own escape pass, so a source name
can never inject markup into a reply.
"""

from __future__ import annotations

from html import escape

from rugmunchbot.handlers.chat_ai._llm._sources import _scrub_internal_api_refs

__all__ = ["grounding_footer"]

_SOURCES_ABOVE = 100  # counts above this are described, not enumerated


def _named_sources(source_labels: list[tuple[str, bool]], distinct_sources: int | None) -> str:
    """Comma-joined, escaped source names plus an ``… +N more`` tail."""
    names = ", ".join(escape(_scrub_internal_api_refs(name)) for name, _is_ours in source_labels)
    if distinct_sources is not None and distinct_sources > len(source_labels):
        names += f", \u2026 +{distinct_sources - len(source_labels)} more"
    return names


def grounding_footer(
    grounding_count: int,
    source_labels: list[tuple[str, bool]] | None = None,
    distinct_sources: int | None = None,
) -> str:
    """Footer sentence for a grounded answer; ``""`` when nothing to say."""
    if grounding_count <= 0:
        return ""
    if source_labels:
        count_word = "source" if grounding_count == 1 else "sources"
        return f"Grounded in {grounding_count} {count_word}: {_named_sources(source_labels, distinct_sources)}"
    if grounding_count > _SOURCES_ABOVE:
        return "Grounded in hundreds of intelligence sources"
    if grounding_count == 1:
        return "Grounded in 1 intelligence source"
    return f"Grounded in {grounding_count} intelligence sources"
