"""Entity classification + token-address extraction for scam content."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rugmunchbot._vendor.shared_utils import is_evm_address, is_solana_address

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle  # noqa: F401


def classify_entity(entity: str) -> str:
    """Classify a news_ingest entity as ``ticker`` / ``evm`` / ``solana`` / ``""``.

    Reuses ``app.core.shared_utils`` EVM/Solana regexes — the same shapes
    ``app.rag.dedup`` and the bot formatters use.
    """
    if not entity:
        return ""
    if entity.startswith("$"):
        return "ticker"
    if is_evm_address(entity):
        return "evm"
    if is_solana_address(entity):
        return "solana"
    return ""


def extract_token_addresses(article: NewsArticle) -> tuple[list[dict[str, str]], list[str]]:
    """Split an article's entities into (addresses, tickers).

    Addresses are unique and classified by chain (``ethereum`` for EVM,
    ``solana`` for base58); tickers are the ``$SYMBOL`` mentions (uppercase,
    ``$`` stripped). Reuses the canonical entity extraction
    (``extract_entities`` via ``channel_message_to_article``).
    """
    addresses: list[dict[str, str]] = []
    tickers: list[str] = []
    seen: set[str] = set()
    seen_tickers: set[str] = set()
    for entity in article.entities or []:
        kind = classify_entity(entity)
        if kind == "ticker":
            ticker = entity[1:]
            if ticker not in seen_tickers:
                seen_tickers.add(ticker)
                tickers.append(ticker)
        elif kind in ("evm", "solana") and entity not in seen:
            seen.add(entity)
            addresses.append(
                {"address": entity, "chain": "ethereum" if kind == "evm" else "solana"}
            )
    return addresses, tickers
