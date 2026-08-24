# VENDORED 2026-08-24 from rmi-backend/app/crypto_embeddings/providers.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Embedding providers: Local BGE, Qwen3-4B, OpenRouter, HuggingFace."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.crypto_embeddings.features import extract_contract_features
from rugmunchbot._vendor.crypto_embeddings.models import (
    OR_FALLBACK_EMBED,
    OR_PRIMARY_EMBED,
)

if TYPE_CHECKING:
    import numpy as np

logger = get_logger(__name__)

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")


class LocalBGEEmbedder:
    """Local BAAI BGE-small-en-v1.5 embedder — HARD-DISABLED.

    The in-process sentence-transformers/torch path (~300MB resident in the bot
    process, never unloaded) was the direct cause of memory pressure. This class
    now fails fast and can NEVER load a local model or touch torch. Use the
    remote embedders (Qwen3 primary, OpenRouter, HuggingFace) or the hash
    fallback instead.
    """

    MODEL_NAME = "BAAI/bge-small-en-v1.5"

    @classmethod
    def _get_model(cls) -> Any:
        raise RuntimeError(
            "LocalBGEEmbedder disabled: in-process sentence-transformers/torch model "
            "loading removed for memory safety. Use remote embedders "
            "(Qwen3/OpenRouter/HuggingFace) or the hash fallback instead."
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self._get_model()  # always raises — no local model is ever loaded
        raise RuntimeError("LocalBGEEmbedder disabled (see _get_model)")

    async def embed_one(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]


class Qwen3Embedder:
    """Qwen3-Embedding-4B via llama.cpp (OpenAI-compatible /v1/embeddings). 2560d, zero cost."""

    EMBEDDER_URL = os.getenv("EMBEDDER_URL", "http://172.22.0.1:8004/v1")
    MODEL = "qwen3-embedding-4b"
    DIMS = 1024

    def __init__(self, qwen3_url: str | None = None) -> None:
        self.url = qwen3_url or os.getenv("EMBEDDER_URL", self.EMBEDDER_URL)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        results = []
        async with httpx.AsyncClient(timeout=120) as client:
            for text in texts:
                try:
                    resp = await client.post(
                        f"{self.url}/embeddings",
                        json={"model": self.MODEL, "input": [text[:8000]]},
                    )
                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                        if data:
                            vec = data[0].get("embedding", [])
                            if len(vec) == self.DIMS:
                                results.append(vec)
                                continue
                except httpx.HTTPError:
                    pass
                results.append([0.0] * self.DIMS)
        return results

    async def embed_one(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]


class OpenRouterEmbedder:
    """OpenRouter embedding API - NVIDIA Nemotron primary, qwen3 fallback."""

    BASE = "https://openrouter.ai/api/v1/embeddings"
    MODEL = OR_PRIMARY_EMBED
    FALLBACK = OR_FALLBACK_EMBED

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or OPENROUTER_KEY
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY required")

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import httpx

        model_id = model or self.MODEL

        async def _try_embed(m: str) -> list[list[float]]:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            payload = {"model": m, "input": texts}
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.post(self.BASE, headers=headers, json=payload)
                if resp.status_code == 429:
                    raise RuntimeError("RATE_LIMITED")
                resp.raise_for_status()
                data = resp.json()
                return [d["embedding"] for d in sorted(data["data"], key=lambda x: x["index"])]

        try:
            return await _try_embed(model_id)
        except Exception as e:
            if "RATE_LIMITED" in str(e) and model_id != self.FALLBACK:
                logger.warning(f"NVIDIA embed rate-limited, falling back to {self.FALLBACK}")
                return await _try_embed(self.FALLBACK)
            raise

    async def embed_one(self, text: str, model: str | None = None) -> list[float]:
        results = await self.embed([text], model)
        return results[0]


class HuggingFaceEmbedder:
    """HuggingFace Inference API - free tier fallback."""

    BASE = "https://api-inference.huggingface.co/models"
    MODEL = "qwen3-embedding-4b"

    def __init__(self, token: str | None = None) -> None:
        self.token = token or HF_TOKEN
        if not self.token:
            raise ValueError("HUGGINGFACE_TOKEN required")

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import asyncio as _asyncio

        import httpx

        model_id = model or self.MODEL
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "x-wait-for-model": "true",
        }
        url = f"{self.BASE}/{model_id}"
        async with httpx.AsyncClient(timeout=60) as client:
            results: list[list[float]] = []
            for text in texts:
                resp = await client.post(url, headers=headers, json={"inputs": text})
                if resp.status_code == 503:
                    await _asyncio.sleep(3)
                    resp = await client.post(url, headers=headers, json={"inputs": text})
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    if isinstance(data[0], list):
                        results.append(data[0] if len(data) == 1 else data[0])
                    else:
                        results.append(data)
                else:
                    logger.warning(f"HF returned unexpected shape: {type(data)}")
                    results.append([0.0] * 1024)
            return results

    async def embed_one(self, text: str, model: str | None = None) -> list[float]:
        results = await self.embed([text], model)
        return results[0]


class ContractCodeEmbedder:
    """Specialized contract code embedder using structural features + semantic embedding."""

    @staticmethod
    async def embed(code: str, semantic_embedder=None) -> np.ndarray:
        import numpy as np

        structural = extract_contract_features(code)
        if semantic_embedder and len(code) > 50:
            code_snippet = code[:4000]
            try:
                semantic_vec = await semantic_embedder.embed_one(f"Smart contract code: {code_snippet[:3000]}")
                semantic = np.array(semantic_vec[:192], dtype=np.float32)
                if len(semantic) < 192:
                    semantic = np.pad(semantic, (0, 192 - len(semantic)))
            except Exception as e:
                logger.warning(f"Semantic contract embedding failed: {e}")
                semantic = np.zeros(192, dtype=np.float32)
        else:
            semantic = np.zeros(192, dtype=np.float32)
        return np.concatenate([structural, semantic])
