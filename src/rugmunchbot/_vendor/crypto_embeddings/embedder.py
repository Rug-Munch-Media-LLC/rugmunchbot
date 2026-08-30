# VENDORED 2026-08-24 from rmi-backend/app/crypto_embeddings/embedder.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""CryptoEmbedder - multi-head embedding engine for crypto scam detection."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Any

import numpy as np

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

from rugmunchbot._vendor.crypto_embeddings.features import (  # noqa: E402 — imported after logger init; kept below module header for readability
    extract_contract_features,
    extract_transaction_features,
    extract_wallet_features,
)
from rugmunchbot._vendor.crypto_embeddings.models import (  # noqa: E402 — module docstring precedes import block
    COLLECTIONS,
    DIMS,
    EmbeddingResult,
)
from rugmunchbot._vendor.crypto_embeddings.providers import (  # noqa: E402 — module docstring precedes import block
    ContractCodeEmbedder,
    HuggingFaceEmbedder,
    LocalEmbedder,
    OpenRouterEmbedder,
    Qwen3Embedder,
)

REDIS_HOST = os.getenv("REDIS_HOST", "rugmunchbot-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PW = os.getenv("REDIS_PASSWORD", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")


class CryptoEmbedder:
    """Multi-head embedder for crypto scam detection."""

    def __init__(self) -> None:
        self._local: LocalEmbedder | None = None  # always None — local model path hard-disabled (memory safety)
        self._qwen3: Qwen3Embedder | None = None
        self._openrouter: OpenRouterEmbedder | None = None
        self._huggingface: HuggingFaceEmbedder | None = None
        self._redis: Any = None
        self._initialized = False
        self._cache_hits = 0
        self._cache_misses = 0

    async def initialize(self) -> None:
        """Initialize embedding providers."""
        or_key = os.environ.get("OPENROUTER_API_KEY", "") or OPENROUTER_KEY
        hf_token = os.environ.get("HUGGINGFACE_TOKEN", "") or HF_TOKEN

        self._qwen3 = Qwen3Embedder()
        logger.info("CryptoEmbedder: Qwen3-Embedding-4B ready (PRIMARY, 2560d)")
        # Local BGE-small fallback is HARD-DISABLED: loading sentence-transformers/torch
        # in-process (~300MB resident, never unloaded) caused memory pressure. Do not re-enable.
        self._local = None

        if or_key:
            self._openrouter = OpenRouterEmbedder(api_key=or_key)
            logger.info("CryptoEmbedder: OpenRouter ready (fallback, 2048d)")
        if hf_token:
            self._huggingface = HuggingFaceEmbedder(token=hf_token)
            logger.info("CryptoEmbedder: HuggingFace ready (fallback, 1024d)")

        self._initialized = True
        logger.info("CryptoEmbedder initialized")

    # ─── Redis connection ───────────────────────────────────────

    async def _get_redis(self) -> Any:
        import redis.asyncio as redis

        if self._redis is None:
            self._redis = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PW or None,
                db=0,
                decode_responses=True,
            )
        return self._redis

    # ─── Semantic embedding pipeline ────────────────────────────

    async def _semantic_embed(self, texts: list[str], head: str = "semantic") -> list[list[float]]:
        if not texts:
            return []
        if self._qwen3:
            try:
                return await self._qwen3.embed(texts)
            except Exception as e:
                logger.warning(f"Qwen3 embedding failed: {e}")
        if self._openrouter:
            try:
                return await self._openrouter.embed(texts)
            except Exception as e:
                logger.warning(f"OpenRouter/NVIDIA embedding failed: {e}")
        if self._huggingface:
            try:
                return await self._huggingface.embed(texts)
            except Exception as e:
                logger.warning(f"HuggingFace embedding failed: {e}")
        raise RuntimeError(
            "CryptoEmbedder: ALL remote embedding backends failed "
            "(Qwen3/OpenRouter/HuggingFace). Local in-process model is disabled "
            "for memory safety — no fallback loaded. Check remote embedder connectivity."
        )

    async def _semantic_embed_one(self, text: str, head: str = "semantic") -> list[float]:
        cache_key = await self._cache_key(head, text)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached
        results = await self._semantic_embed([text], head)
        if not results:
            raise RuntimeError(
                "CryptoEmbedder: _semantic_embed returned empty for single text. "  # T04: fail loud
            )
        vector = results[0]
        await self._cache_set(cache_key, vector, ttl=86400)
        return vector

    def _hash_embed(self, text: str, dims: int = 1024) -> list[float]:
        """Deterministic hash-based embedding (emergency fallback)."""
        vec = np.zeros(dims, dtype=np.float32)
        words = re.findall(r"\w+", text.lower())
        for word in words:
            h = int(hashlib.sha256(word.encode()).hexdigest(), 16)
            for i in range(min(8, dims)):
                vec[(h + i) % dims] += 0.01
        norm = np.linalg.norm(vec)
        return (vec / norm).tolist() if norm > 0 else vec.tolist()

    async def _cache_key(self, prefix: str, text: str) -> str:
        h = hashlib.sha256(text.encode()).hexdigest()[:32]
        return f"emb:{prefix}:{h}"

    async def _cache_get(self, key: str) -> list[float] | None:
        try:
            r = await self._get_redis()
            data = await r.get(key)
            if data:
                self._cache_hits += 1
                return json.loads(data)
            self._cache_misses += 1
        except json.JSONDecodeError:
            pass
        return None

    async def _cache_set(self, key: str, vector: list[float], ttl: int = 86400) -> None:
        try:
            r = await self._get_redis()
            await r.setex(key, ttl, json.dumps(vector))
        except json.JSONDecodeError:
            pass

    # ─── SPECIALIZED EMBED METHODS ──────────────────────────────

    async def embed_token_scam(
        self,
        name: str,
        symbol: str,
        description: str = "",
        contract_code: str = "",
        creator_address: str = "",
        chain: str = "solana",
        metadata: dict[str, Any] | None = None,
    ) -> EmbeddingResult:
        semantic_text = f"""Token Analysis: {name} ({symbol}) on {chain}.
Description: {description[:2000]}
Creator: {creator_address}
Metadata: {json.dumps(metadata or {})[:1000]}"""
        semantic_vec = await self._semantic_embed_one(semantic_text, "semantic")
        semantic_dim = len(semantic_vec)
        code_vec = extract_contract_features(contract_code).tolist() if contract_code else [0.0] * 128
        behavioral = extract_transaction_features(metadata or {})
        behavioral_vec = behavioral.tolist()
        wallet = extract_wallet_features(
            {
                "address": creator_address,
                "labels": metadata.get("creator_labels", []) if metadata else [],
            }
        )
        wallet_vec = wallet.tolist()
        combined = semantic_vec + code_vec + behavioral_vec + wallet_vec
        return EmbeddingResult(
            vector=combined,
            dims=len(combined),
            model="crypto_multihead_v1",
            head="token_scam",
            metadata={
                "name": name,
                "symbol": symbol,
                "chain": chain,
                "semantic_dim": semantic_dim,
                "has_code": bool(contract_code),
                "has_behavioral": bool(metadata),
            },
        )

    async def embed_wallet(
        self,
        address: str,
        transactions: list[dict[str, Any]] | None = None,
        labels: list[str] | None = None,
        chain: str = "solana",
        balance_usd: float = 0,
    ) -> EmbeddingResult:
        wallet_data = {
            "address": address,
            "labels": labels or [],
            "balance_usd": balance_usd,
            "transaction_count": len(transactions or []),
        }
        wallet_vec = extract_wallet_features(wallet_data).tolist()
        behavioral_vec = extract_transaction_features({"transactions": transactions or []}).tolist()
        semantic_text = f"Wallet {address} on {chain}. Labels: {', '.join(labels or ['unknown'])}. Activity: {len(transactions or [])} transactions."
        semantic_vec = await self._semantic_embed_one(semantic_text, "semantic")
        combined = semantic_vec + wallet_vec + behavioral_vec
        return EmbeddingResult(
            vector=combined,
            dims=len(combined),
            model="crypto_multihead_v1",
            head="wallet",
            metadata={"address": address, "chain": chain, "labels": labels or [], "tx_count": len(transactions or [])},
        )

    async def embed_scam_pattern(
        self,
        pattern_name: str,
        description: str,
        code_snippets: list[str] | None = None,
        indicators: list[str] | None = None,
        severity: str = "high",
    ) -> EmbeddingResult:
        semantic_text = f"""SCAM PATTERN: {pattern_name}
Severity: {severity}
Description: {description}
Indicators: {", ".join(indicators or [])}
Code patterns: {"; ".join(code_snippets or [])[:3000]}"""
        semantic_vec = await self._semantic_embed_one(semantic_text, "semantic")
        all_code = "\n".join(code_snippets or [])
        code_vec = extract_contract_features(all_code).tolist()
        combined = semantic_vec + code_vec
        return EmbeddingResult(
            vector=combined,
            dims=len(combined),
            model="crypto_multihead_v1",
            head="scam_pattern",
            metadata={"pattern": pattern_name, "severity": severity, "indicators": indicators or []},
        )

    async def embed_contract(
        self, address: str, code: str, chain: str = "ethereum", verified: bool = False
    ) -> EmbeddingResult:
        import numpy as np

        result = await ContractCodeEmbedder.embed(code, semantic_embedder=self._openrouter)
        dims_val = len(result)
        return EmbeddingResult(
            vector=result.tolist() if isinstance(result, np.ndarray) else result,
            dims=dims_val,
            model="contract_code_v1",
            head="contract",
        )

    async def embed_news(
        self, title: str, content: str, source: str = "", tags: list[str] | None = None
    ) -> EmbeddingResult:
        text = f"TITLE: {title}\nSOURCE: {source}\nTAGS: {', '.join(tags or [])}\nCONTENT: {content[:3000]}"
        vec = await self._semantic_embed_one(text, "semantic")
        return EmbeddingResult(
            vector=vec,
            dims=len(vec),
            model="openai/text-embedding-3-large",
            head="news",
            metadata={"title": title, "source": source, "tags": tags or []},
        )

    async def embed_query(self, query: str) -> list[float]:
        augmented = f"Cryptocurrency scam detection query: {query}"
        return await self._semantic_embed_one(augmented, "semantic")

    # ─── SIMILARITY & SEARCH ────────────────────────────────────

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        a_arr = np.array(a, dtype=np.float32)
        b_arr = np.array(b, dtype=np.float32)
        dot = np.dot(a_arr, b_arr)
        norm_a = np.linalg.norm(a_arr)
        norm_b = np.linalg.norm(b_arr)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

    @staticmethod
    def weighted_similarity(
        vec_a: list[float],
        vec_b: list[float],
        semantic_dim: int,
        weights: tuple[float, float, float, float] = (0.4, 0.2, 0.2, 0.2),
    ) -> float:
        if len(vec_a) != len(vec_b):
            min_len = min(len(vec_a), len(vec_b))
            return CryptoEmbedder.cosine_similarity(vec_a[:min_len], vec_b[:min_len])
        code_start = semantic_dim
        behav_start = code_start + 128
        wallet_start = behav_start + 64
        sem_sim = CryptoEmbedder.cosine_similarity(vec_a[:semantic_dim], vec_b[:semantic_dim])
        code_sim = (
            CryptoEmbedder.cosine_similarity(vec_a[code_start:behav_start], vec_b[code_start:behav_start])
            if len(vec_a) > code_start
            else 0.0
        )
        behav_sim = (
            CryptoEmbedder.cosine_similarity(vec_a[behav_start:wallet_start], vec_b[behav_start:wallet_start])
            if len(vec_a) > behav_start
            else 0.0
        )
        wallet_sim = (
            CryptoEmbedder.cosine_similarity(vec_a[wallet_start:], vec_b[wallet_start:])
            if len(vec_a) > wallet_start
            else 0.0
        )
        return weights[0] * sem_sim + weights[1] * code_sim + weights[2] * behav_sim + weights[3] * wallet_sim

    # ─── VECTOR STORE INTEGRATION ───────────────────────────────

    async def store(
        self,
        collection: str,
        doc_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
        content: str = "",
    ) -> bool:
        try:
            r = await self._get_redis()
            key = f"rag:{collection}:{doc_id}"
            doc = {
                "id": doc_id,
                "vector": vector,
                "metadata": metadata or {},
                "content": content[:5000],
                "stored_at": datetime.now(UTC).isoformat(),
            }
            await r.setex(key, 86400 * 30, json.dumps(doc))
            await r.sadd(f"rag:idx:{collection}", doc_id)
            return True
        except Exception as e:
            logger.error(f"Failed to store embedding: {e}")
            return False

    async def search(
        self, query: str, collection: str = "scam_patterns", limit: int = 10, min_similarity: float = 0.6
    ) -> list[dict[str, Any]]:
        query_vec = await self.embed_query(query)
        q_dim = len(query_vec)
        try:
            r = await self._get_redis()
            doc_ids = await r.smembers(f"rag:idx:{collection}")
            if not doc_ids:
                return []
            pipe = r.pipeline()
            for did in doc_ids:
                pipe.get(f"rag:{collection}:{did}")
            results = await pipe.execute()
            scored = []
            for _i, data in enumerate(results):
                if not data:
                    continue
                try:
                    doc = json.loads(data)
                except json.JSONDecodeError:
                    continue
                doc_vec = doc.get("vector", [])
                if isinstance(doc_vec, str):
                    try:
                        doc_vec = json.loads(doc_vec)
                    except (ConnectionError, OSError, json.JSONDecodeError):
                        continue
                if not doc_vec or not isinstance(doc_vec, list):
                    continue
                compare_len = min(q_dim, len(doc_vec))
                sim = self.cosine_similarity(query_vec[:compare_len], doc_vec[:compare_len])
                if sim >= min_similarity:
                    scored.append(
                        {
                            "id": doc["id"],
                            "similarity": round(sim, 4),
                            "metadata": doc.get("metadata", {}),
                            "content": doc.get("content", "")[:500],
                        }
                    )
            scored.sort(key=lambda x: x["similarity"], reverse=True)
            return scored[:limit]
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "initialized": self._initialized,
            "providers": {
                "local_bge": False,  # hard-disabled: in-process model load removed (memory safety)
                "openrouter": self._openrouter is not None,
                "huggingface": self._huggingface is not None,
            },
            "primary": "qwen3-embedding-4b (2560d, remote)",
            "cache": {
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "ratio": round(self._cache_hits / max(1, self._cache_hits + self._cache_misses), 3),
            },
            "dimensions": DIMS,
            "collections": COLLECTIONS,
        }
