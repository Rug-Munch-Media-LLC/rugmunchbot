"""Fit the rendered verdict card inside Telegram's 4096-character cap.

Pruning order is a product decision, not an accident:

  1. ``variants`` — the forensic groups, richest first. The card passes
     ``[first-buyers + CEX/holders, first-buyers only, []]`` so the CEX /
     holder-quality lines die before the first-buyer forensics, and both die
     before any core row;
  2. ``optional`` — the legacy droppable blocks (holders, deployer, links),
     dropped from the LAST block backwards, exactly as before;
  3. truncation — last resort, keeps the head intact and appends a marker.

The verdict, safety row, clusters and market block are never dropped.

One public entry point: :func:`fit_card`.
"""

from __future__ import annotations


def fit_card(
    base: list[str],
    tail: list[str],
    variants: list[list[str]],
    optional: list[list[str]],
    *,
    max_chars: int,
    truncated: str,
) -> str:
    """Join sections, dropping ``variants`` before ``optional`` before lines."""
    last = len(variants) - 1
    for index, variant in enumerate(variants):
        head = [*base, *variant, *tail]
        blocks = list(optional)
        while True:
            text = "\n".join(head + [line for block in blocks for line in block])
            if len(text) <= max_chars:
                return text
            # Every forensic variant is tried with the full optional set, so
            # the forensic groups always die before a legacy block does.
            if index < last or not blocks:
                break
            blocks.pop()
    keep: list[str] = []
    for line in [*base, *tail]:
        if len("\n".join([*keep, line])) > max_chars - len(truncated) - 1:
            break
        keep.append(line)
    return "\n".join([*keep, truncated])
