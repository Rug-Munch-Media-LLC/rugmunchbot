"""Data provider seam — own-data-first fallback chain.

The live data_resolver.py hardcodes private backend routes
(/api/v1/databus/*, /api/v1/rag/v2/search, /api/v1/market/*). OSS makes the
chain fully provider-injected; the default is public APIs + local RAG.

Canonical private behavior: rmi-backend rugmunchbot/data_resolver.py.
"""

from __future__ import annotations

from typing import Any, Protocol


class DataProvider(Protocol):
    async def resolve(self, query: str, kind: str, **kw: Any) -> Any: ...


class NoopDataProvider:
    async def resolve(self, query: str, kind: str, **kw: Any) -> Any:
        return None


def get_data_provider() -> Any:
    return NoopDataProvider()
