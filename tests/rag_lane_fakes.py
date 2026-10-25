"""Shared fakes for the RAG lane tests (no network, no monkeypatch sites).

Installed through ``patch(target, value)`` on module attributes — the shape
``tests/test_news_rerank_lane.py`` already uses.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

DIMS = 4096


class Resp:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.text = str(payload)[:160]

    def json(self) -> dict[str, Any]:
        return self._payload


class Client:
    """One fake httpx client; ``handler(url, kw)`` returns a response or raises."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.calls: list[str] = []

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def post(self, url: str, **kw: Any) -> Resp:
        self.calls.append(url)
        return self._handler(url, kw)

    async def put(self, url: str, **kw: Any) -> Resp:
        self.calls.append(url)
        return self._handler(url, kw)


class Harness(ExitStack):
    """Install elementwise patches; every ``set`` is undone on exit."""

    def set(self, target: str, value: Any) -> None:
        self.enter_context(patch(target, value))


def lane(handler: Any) -> Harness:
    """Patch the shared httpx client, corpus width and auth for one test."""
    stack = Harness()
    stack.set("rugmunchbot.tools.rag_qdrant.httpx.AsyncClient", lambda *a, **k: Client(handler))
    stack.set("rugmunchbot.tools.rag_qdrant.expected_dims", lambda: DIMS)
    stack.set("rugmunchbot.tools.rag_qdrant.rag_headers", dict)
    return stack


def embed_ok(dims: int = DIMS) -> Any:
    return lambda url, kw: Resp({"embedding": [0.01] * dims})


def search_ok(points: list[dict[str, Any]] | None = None) -> Any:
    pts = (
        points
        if points is not None
        else [
            {"id": "p1", "score": 0.9, "payload": {"content": "reentrancy pattern", "doc_id": "d1"}}
        ]
    )
    return lambda url, kw: Resp({"result": pts})
