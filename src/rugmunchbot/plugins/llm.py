"""LLM router seam — provider-order semantics preserved.

The private chat_ai pipeline calls the private chat API (tier-1) then falls
back to local LLM hosts (tier-2/3). OSS exposes the same order as a router:
primary (operator chat API) → local OpenAI-compatible endpoint → none.

Env: BOT_LLM_API_BASE, BOT_LLM_MODEL, BOT_LLM_API_KEY (see config.py).
"""

from __future__ import annotations

from typing import Any, Protocol, cast

import httpx


class LLMRouter(Protocol):
    async def complete(self, messages: list[dict[str, str]], **kw: Any) -> str | None: ...


class LocalLLMRouter:
    """OpenAI-compatible chat completions against a local endpoint."""

    def __init__(
        self, base_url: str | None = None, model: str | None = None, api_key: str = ""
    ) -> None:
        self.base_url = (base_url or "http://127.0.0.1:8085/v1/chat/completions").rstrip("/")
        self.model = model or "local-model"
        self.api_key = api_key

    async def complete(self, messages: list[dict[str, str]], **kw: Any) -> str | None:
        if not self.base_url:
            return None
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload: dict[str, Any] = {"model": self.model, "messages": messages, **kw}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(self.base_url, json=payload, headers=headers)
                if r.status_code != 200:
                    return None
                data = r.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content")
                return cast("str | None", content)
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError):
            return None


def get_llm_router() -> Any:
    from rugmunchbot.config import BOT_LLM_API_BASE, BOT_LLM_API_KEY, BOT_LLM_MODEL

    return LocalLLMRouter(BOT_LLM_API_BASE, BOT_LLM_MODEL, BOT_LLM_API_KEY)
