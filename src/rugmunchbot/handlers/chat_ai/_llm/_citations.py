"""Live-tool citations — attribute every number to the source that produced it.

The message-level "Sources:" footer (:func:`_append_source_block`) only ever
received bridge / router / RAG sources. A price or risk answer grounded in
DexScreener or Birdeye therefore cited NOTHING: the grounding caption named a
provider but the reader had no link and no way to verify the figure, and a
tool-sourced number looked identical to a recalled one.

This module maps a :class:`ToolResult` ``source`` label (the tools report
``dexscreener:/latest/dex/tokens``, ``birdeye:/defi/price``, ``cache:scanresult``)
onto a citation entry ``{"title", "url", "type"}`` for the provider that
ACTUALLY answered, so the footer can cite it.

Rules (all three are the zero-fabrication contract, not style):

* Only ``ok=True`` results are cited — a failed tool attributed nothing.
* Only providers with a real public home are mapped. Internal endpoints
  (``backend:`` / ``bridge:``) and synthesised fallbacks (``knowledge-fallback``)
  get NO url; ``_delivery`` already labels those "RMI …".
* An unmapped label yields NO citation rather than a guessed domain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rugmunchbot.tools.registry import ToolResult

if TYPE_CHECKING:
    from collections.abc import Mapping

# (label substring, display name, public home) — specific providers first.
# Every entry is a source the bot's tools genuinely call; nothing is listed
# speculatively.
_PROVIDERS: tuple[tuple[str, str, str], ...] = (
    ("dexscreener", "DexScreener", "https://dexscreener.com"),
    ("birdeye", "Birdeye", "https://birdeye.so"),
    ("coingecko", "CoinGecko", "https://www.coingecko.com"),
    ("rugcheck", "RugCheck", "https://rugcheck.xyz"),
    ("goplus", "GoPlus", "https://gopluslabs.io"),
    ("helius", "Helius", "https://helius.dev"),
    ("defillama", "DefiLlama", "https://defillama.com"),
    ("dune", "Dune", "https://dune.com"),
    ("solscan", "Solscan", "https://solscan.io"),
    ("etherscan", "Etherscan", "https://etherscan.io"),
    ("bscscan", "BscScan", "https://bscscan.com"),
)

# Labels that are OURS or synthesised: no public URL exists, so they must
# never be rendered as a third-party citation.
_NOT_CITABLE = ("backend:", "bridge:", "cache:", "knowledge", "pattern_memory", "rugbrain")


def _provider_for(label: str) -> tuple[str, str] | None:
    """Resolve a tool ``source`` label to ``(display_name, home_url)``."""
    low = label.strip().lower()
    if not low or any(marker in low for marker in _NOT_CITABLE):
        return None
    for marker, name, url in _PROVIDERS:
        if marker in low:
            return name, url
    return None


def tool_citations(results: Mapping[str, ToolResult]) -> list[dict[str, str]]:
    """Citation entries for every live provider that fed this answer.

    Returns ``[]`` when no tool succeeded. One entry per provider (a price and
    a trends call both hitting DexScreener cite it once), ordered by first
    appearance so the footer reads in fetch order. The entry carries
    ``type="live_tool"`` because :func:`_append_source_block` renders
    URL-bearing entries as ``title — url`` regardless of type, while
    ``_delivery`` can tell a live-provider citation from a web/article hit.
    """
    cited: list[dict[str, str]] = []
    seen: set[str] = set()
    for result in results.values():
        if not isinstance(result, ToolResult) or not result.ok:
            continue
        resolved = _provider_for(str(result.source or ""))
        if resolved is None:
            continue
        name, url = resolved
        if name in seen:
            continue
        seen.add(name)
        cited.append({"title": name, "url": url, "type": "live_tool"})
    return cited


__all__ = ["tool_citations"]
