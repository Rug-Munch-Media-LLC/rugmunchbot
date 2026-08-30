# VENDORED 2026-08-24 from rmi-backend/app/catalog/service.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""T27 CatalogService - unified read/write API for RMI.

Per v4.0 §T27. The CatalogService is the ONLY sanctioned way to read or
write catalog data. Domain facades call this; they never touch stores directly.

Architecture:
  - Lazy store clients (init on first use, retry on failure)
  - Graceful degradation: unreachable stores return None/empty, not crash
  - Redis cache layer (always available since rugmunchbot-redis is up)
  - Cross-store ID conventions from app.catalog.models

Recipe coverage:
  Recipe 1: find_tokens_by_deployer_history   (Postgres + Neo4j)
  Recipe 2: find_similar_tokens                (Qdrant + Postgres)
  Recipe 3: get_token_risk                     (Redis + Postgres + Neo4j)
  Recipe 4: news_price_correlation             (Postgres, basic v1)
  Recipe 5: resolve_entity                     (Neo4j Cypher)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import Any

import httpx

from rugmunchbot._vendor.catalog_models import (
    COLLECTIONS,
    Chain,
    Token,
    Wallet,
    utcnow,
)
from rugmunchbot._vendor.settings_shim import resolve_service_url
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rugbrain_client import rugbrain_search

log = get_logger(__name__)

# ── Connection config (from env, with sensible defaults for local dev) ──
# Override via env vars in docker-compose.yml for production.
DEFAULT_CONFIG: dict[str, Any] = {
    "redis": {
        "host": os.getenv("REDIS_HOST", "rugmunchbot-redis"),
        "port": int(os.getenv("REDIS_PORT", "6379")),
        "password": os.getenv("REDIS_PASSWORD", ""),
        "db": int(os.getenv("REDIS_DB", "0")),
    },
    "postgres": {
        "host": os.getenv("POSTGRES_HOST", "rmi-postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "user": os.getenv("POSTGRES_USER", "rmi"),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
        "database": os.getenv("POSTGRES_DB", "rmi"),
    },
    "neo4j": {
        "uri": os.getenv("NEO4J_URI", "bolt://rmi-neo4j:7687"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", ""),
    },
    "qdrant": {
        "url": resolve_service_url("QDRANT_URL", docker="http://rmi-qdrant:6333", local="http://localhost:6333"),
        "api_key": os.getenv("QDRANT_API_KEY", ""),
    },
    "minio": {
        "endpoint": os.getenv("MINIO_ENDPOINT", "rmi-minio:9000"),
        "access_key": os.getenv("MINIO_ACCESS_KEY", ""),
        "secret_key": os.getenv("MINIO_SECRET_KEY", ""),
    },
}


class StoreHealth:
    """Tracks which stores are reachable. Updated on each probe."""

    def __init__(self) -> None:
        self.redis: bool = False
        self.postgres: bool = False
        self.neo4j: bool = False
        self.qdrant: bool = False
        self.minio: bool = False
        self.last_checked: float = 0.0


class CatalogService:
    """Unified read/write API for RMI data.

    Use `get_catalog()` to get the singleton instance. All methods are
    async and graceful-degrade - if a store is unreachable, the method
    returns None or an empty result with a logged warning.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self._health = StoreHealth()
        self._redis: Any = None
        self._pg_pool: Any = None
        self._neo_driver: Any = None
        self._qdrant: Any = None  # httpx.AsyncClient
        self._init_lock = asyncio.Lock()

    # ── Store init (lazy) ────────────────────────────────────────────
    async def _init_stores(self) -> None:
        async with self._init_lock:
            if self._redis is not None:
                return  # already init
            # Redis (always try first - used for everything)
            try:
                import redis.asyncio as aioredis

                cfg = self.config["redis"]
                self._redis = aioredis.Redis(
                    host=cfg["host"],
                    port=cfg["port"],
                    password=cfg["password"],
                    db=cfg["db"],
                    decode_responses=True,
                )
                await self._redis.ping()
                self._health.redis = True
                log.info("catalog_redis_ok")
            except Exception as e:
                log.warning("catalog_redis_fail: %s", e)
                self._health.redis = False

            # Postgres
            try:
                import asyncpg

                cfg = self.config["postgres"]
                if cfg["password"]:
                    self._pg_pool = await asyncpg.create_pool(
                        host=cfg["host"],
                        port=cfg["port"],
                        user=cfg["user"],
                        password=cfg["password"],
                        database=cfg["database"],
                        min_size=1,
                        max_size=8,
                        command_timeout=10,
                    )
                    self._health.postgres = True
                    log.info("catalog_postgres_ok")
            except Exception as e:
                log.warning("catalog_postgres_fail: %s", e)
                self._health.postgres = False

            # Neo4j (NEO4J_AUTH=none means no password)
            try:
                from neo4j import AsyncGraphDatabase

                cfg = self.config["neo4j"]
                auth = (cfg["user"], cfg["password"]) if cfg["password"] else None
                self._neo_driver = AsyncGraphDatabase.driver(cfg["uri"], auth=auth)
                async with self._neo_driver.session() as s:
                    await (await s.run("RETURN 1")).consume()
                self._health.neo4j = True
                log.info("catalog_neo4j_ok")
            except Exception as e:
                log.warning("catalog_neo4j_fail: %s", e)
                self._health.neo4j = False

            # MinIO (HTTP health probe)
            try:
                import socket

                host, port = self.config["minio"]["endpoint"].rsplit(":", 1)
                s = socket.socket()
                s.settimeout(3)
                s.connect((host, int(port)))
                s.close()
                self._health.minio = True
                log.info("catalog_minio_ok")
            except Exception as e:
                log.warning("catalog_minio_fail: %s", e)
                self._health.minio = False

            # Qdrant (HTTP)
            try:
                cfg = self.config["qdrant"]
                headers = {"api-key": cfg["api_key"]} if cfg["api_key"] else {}
                self._qdrant = httpx.AsyncClient(base_url=cfg["url"], headers=headers, timeout=5.0)
                r = await self._qdrant.get("/collections")
                if r.status_code == 200:
                    self._health.qdrant = True
                    log.info("catalog_qdrant_ok")
                else:
                    log.warning("catalog_qdrant_http_%d", r.status_code)
            except Exception as e:
                log.warning("catalog_qdrant_fail: %s", e)
                self._health.qdrant = False

            self._health.last_checked = time.time()

    # ── Health probe ────────────────────────────────────────────────
    async def probe_stores(self) -> dict[str, bool]:
        await self._init_stores()
        return {
            "redis": self._health.redis,
            "postgres": self._health.postgres,
            "neo4j": self._health.neo4j,
            "qdrant": self._health.qdrant,
            "minio": self._health.minio,
        }

    # ── Tokens (Postgres primary + Redis cache) ─────────────────────
    async def get_token(self, chain: Chain, address: str) -> Token | None:
        await self._init_stores()
        cache_key = f"catalog:token:{chain.value}:{address}"
        if self._health.redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    return Token.model_validate_json(cached)
            except Exception as e:
                log.debug("token_cache_get_fail: %s", e)
        if not self._health.postgres:
            return None
        try:
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM tokens WHERE chain=$1 AND address=$2",
                    chain.value,
                    address,
                )
            if not row:
                return None
            token = Token(**dict(row))
            if self._health.redis:
                try:
                    await self._redis.setex(cache_key, 3600, token.model_dump_json())
                except Exception as e:
                    log.debug("token_cache_set_fail: %s", e)
            return token
        except Exception as e:
            log.warning("token_get_fail: %s", e)
            return None

    async def save_token(self, token: Token) -> bool:
        await self._init_stores()
        if not self._health.postgres:
            log.warning("token_save_no_postgres")
            return False
        try:
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO tokens (
                        token_id, chain, address, symbol, name, decimals,
                        deployer_wallet_id, deployed_at, initial_supply,
                        current_supply, is_honeypot, is_mintable, is_proxy,
                        tax_buy_bps, tax_sell_bps, risk_tier, risk_score,
                        risk_factors, rag_embedding_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                              $14,$15,$16,$17,$18,$19)
                    ON CONFLICT (token_id) DO UPDATE SET
                        symbol=EXCLUDED.symbol,
                        name=EXCLUDED.name,
                        current_supply=EXCLUDED.current_supply,
                        is_honeypot=EXCLUDED.is_honeypot,
                        is_mintable=EXCLUDED.is_mintable,
                        risk_tier=EXCLUDED.risk_tier,
                        risk_score=EXCLUDED.risk_score,
                        risk_factors=EXCLUDED.risk_factors
                    """,
                    token.token_id,
                    token.chain.value,
                    token.address,
                    token.symbol,
                    token.name,
                    token.decimals,
                    token.deployer_wallet_id,
                    token.deployed_at,
                    token.initial_supply,
                    token.current_supply,
                    token.is_honeypot,
                    token.is_mintable,
                    token.is_proxy,
                    token.tax_buy_bps,
                    token.tax_sell_bps,
                    token.risk_tier.value if token.risk_tier else None,
                    token.risk_score,
                    token.risk_factors,
                    token.rag_embedding_id,
                )
            # Invalidate cache
            if self._health.redis:
                with contextlib.suppress(Exception):
                    await self._redis.delete(f"catalog:token:{token.chain.value}:{token.address}")
            return True
        except Exception as e:
            log.warning("token_save_fail: %s", e)
            return False

    # ── Wallets (Neo4j primary) ──────────────────────────────────────
    async def get_wallet(self, chain: Chain, address: str) -> Wallet | None:
        await self._init_stores()
        if not self._health.neo4j:
            return None
        wallet_id = f"{chain.value}:{address}"
        try:
            async with self._neo_driver.session() as s:
                result = await (
                    await s.run(
                        "MATCH (w:Wallet {wallet_id: $id}) RETURN w",
                        id=wallet_id,
                    )
                ).single()
            if not result:
                return None
            node = dict(result["w"])
            # Normalize datetime strings
            for k in ("first_seen", "last_seen"):
                if isinstance(node.get(k), str):
                    from datetime import datetime

                    with contextlib.suppress(Exception):
                        node[k] = datetime.fromisoformat(node[k].replace("Z", "+00:00"))
            return Wallet(**node)
        except Exception as e:
            log.warning("wallet_get_fail: %s", e)
            return None

    async def save_wallet(self, wallet: Wallet) -> bool:
        await self._init_stores()
        if not self._health.neo4j:
            return False
        try:
            async with self._neo_driver.session() as s:
                await (
                    await s.run(
                        """
                        MERGE (w:Wallet {wallet_id: $wallet_id})
                        SET w.chain=$chain, w.address=$address,
                            w.first_seen=$first_seen, w.last_seen=$last_seen,
                            w.tx_count=$tx_count, w.total_volume_usd=$total_volume_usd,
                            w.is_deployer=$is_deployer,
                            w.is_known_exchange=$is_known_exchange,
                            w.is_suspicious=$is_suspicious,
                            w.reputation_score=$reputation_score
                        """,
                        wallet_id=wallet.wallet_id,
                        chain=wallet.chain.value,
                        address=wallet.address,
                        first_seen=wallet.first_seen.isoformat(),
                        last_seen=wallet.last_seen.isoformat(),
                        tx_count=wallet.tx_count,
                        total_volume_usd=wallet.total_volume_usd,
                        is_deployer=wallet.is_deployer,
                        is_known_exchange=wallet.is_known_exchange,
                        is_suspicious=wallet.is_suspicious,
                        reputation_score=wallet.reputation_score,
                    )
                ).consume()
            return True
        except Exception as e:
            log.warning("wallet_save_fail: %s", e)
            return False

    # ── Recipe 1: find tokens by deployer history ───────────────────
    async def find_tokens_by_deployer_history(
        self, min_rug_count: int = 1, chain: Chain | None = None, limit: int = 50
    ) -> list[Token]:
        """Find tokens deployed by wallets that have rug-pulled before.

        Cross-store: Neo4j for the wallet filter, Postgres for the tokens.
        """
        await self._init_stores()
        if not (self._health.neo4j and self._health.postgres):
            log.warning("recipe1_stores_unavailable")
            return []
        try:
            # Step 1: Neo4j - find wallets with rug_count >= min_rug_count
            async with self._neo_driver.session() as s:
                result = await s.run(
                    "MATCH (w:Deployer) WHERE w.rug_count >= $min RETURN w.wallet_id LIMIT 200",
                    min=min_rug_count,
                )
                wallets = [r["w.wallet_id"] async for r in result]
            if not wallets:
                return []
            # Step 2: Postgres - find tokens deployed by those wallets
            async with self._pg_pool.acquire() as conn:
                query = "SELECT * FROM tokens WHERE deployer_wallet_id = ANY($1::text[]) "
                params: list[Any] = [wallets]
                if chain:
                    query += "AND chain=$2 "
                    params.append(chain.value)
                query += "ORDER BY deployed_at DESC LIMIT $3"
                params.append(limit)
                rows = await conn.fetch(query, *params)
            return [Token(**dict(r)) for r in rows]
        except Exception as e:
            log.warning("recipe1_fail: %s", e)
            return []

    # ── Recipe 3: get_token_risk (3-store composition) ──────────────
    async def get_token_risk(self, chain: Chain, address: str) -> dict[str, Any]:
        """Real-time risk score - composes Redis cache + Postgres + Neo4j.

        Returns dict (not Token) because it's a cross-store projection.
        Cache TTL 60s.
        """
        await self._init_stores()
        cache_key = f"catalog:risk:{chain.value}:{address}"
        if self._health.redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    return json.loads(cached)
            except (ConnectionError, OSError, json.JSONDecodeError):
                pass

        token = await self.get_token(chain, address)
        deployer_reputation: int | None = None
        if token and token.deployer_wallet_id and self._health.neo4j:
            try:
                async with self._neo_driver.session() as s:
                    r = await (
                        await s.run(
                            "MATCH (d:Deployer {wallet_id: $id}) RETURN d.reputation_score AS rep, d.rug_count AS rugs",
                            id=token.deployer_wallet_id,
                        )
                    ).single()
                if r:
                    deployer_reputation = r["rep"]
            except Exception as e:
                log.debug("risk_neo4j_fail: %s", e)

        token_score = token.risk_score if token and token.risk_score is not None else 50
        if deployer_reputation is not None:
            score = int(0.6 * token_score + 0.4 * (100 - deployer_reputation))
        else:
            score = token_score
        score = max(0, min(100, score))
        if score < 25:
            tier = "low"
        elif score < 50:
            tier = "medium"
        elif score < 75:
            tier = "high"
        else:
            tier = "critical"
        result: dict[str, Any] = {
            "score": score,
            "tier": tier,
            "factors": token.risk_factors if token else [],
            "token": token.model_dump() if token else None,
            "deployer_reputation": deployer_reputation,
            "fetched_at": utcnow().isoformat(),
        }
        if self._health.redis:
            with contextlib.suppress(Exception):
                await self._redis.setex(cache_key, 60, json.dumps(result, default=str))
        return result

    # ── Recipe 5: resolve_entity (Neo4j Cypher) ─────────────────────
    async def resolve_entity(self, wallet_id: str, max_chains: int = 5) -> dict[str, Any]:
        """Cross-chain entity resolution via Neo4j.

        Uses the SAME_AS / FUNDED_BY_SAME / CLONE_OF / BEHAVIORAL_MATCH
        edges defined in v4.0 §T32 with path-weighted confidence.
        """
        await self._init_stores()
        if not self._health.neo4j:
            return {"entity_id": None, "wallets": [], "note": "neo4j unavailable"}
        try:
            async with self._neo_driver.session() as s:
                result = await (
                    await s.run(
                        """
                        MATCH (start:Wallet {wallet_id: $wallet_id})
                        OPTIONAL MATCH path = (start)-[:SAME_AS|FUNDED_BY_SAME|CLONE_OF|BEHAVIORAL_MATCH*1..3]-(other:Wallet)
                        WHERE start <> other
                        WITH start, other, path,
                             reduce(conf = 1.0, r IN relationships(path) | conf * r.confidence) AS path_conf
                        ORDER BY path_conf DESC
                        LIMIT $limit
                        RETURN start.entity_id AS entity_id,
                               collect({
                                   wallet_id: other.wallet_id,
                                   chain: other.chain,
                                   address: other.address,
                                   confidence: path_conf
                               }) AS cross_chain_wallets
                        """,
                        wallet_id=wallet_id,
                        limit=max_chains,
                    )
                ).single()
            if not result:
                return {"entity_id": None, "wallets": [], "note": "no edges"}
            wallets = [w for w in (result["cross_chain_wallets"] or []) if w.get("wallet_id")]
            return {
                "entity_id": result["entity_id"],
                "wallets": wallets,
                "note": f"{len(wallets)} cross-chain matches",
            }
        except Exception as e:
            log.warning("resolve_entity_fail: %s", e)
            return {"entity_id": None, "wallets": [], "error": str(e)}

    # ── RAG bridge: wire app/rag/engine.py into the catalog ─────────
    async def rag_search(self, query: str, collection: str = "scam_intel", top_k: int = 5) -> list[dict[str, Any]]:
        """Search the RAG system. Returns raw hits (catalog-agnostic).

        Use this when you want RAG results as raw search hits.
        Use get_token_risk / etc. when you want catalog-typed results.
        """
        try:
            return await rugbrain_search(query=query, collections=[collection], limit=top_k)
        except Exception as e:
            log.warning("rag_search_fail: %s", e)
            return []

    async def rag_ingest(
        self,
        content: str,
        collection: str = "scam_intel",
        doc_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ingest a fact into RAG via rugbrain HTTP API.

        Returns the RAG ingest result plus the Qdrant point_id for cross-store ref.
        """
        from rugmunchbot._vendor.rugbrain_client import rugbrain_ingest

        doc_id = doc_id or f"finding:{content[:16]}"
        try:
            ok = await rugbrain_ingest(
                content=content,
                collection=collection,
                doc_id=doc_id,
                metadata=metadata or {},
            )
            return {"status": "ok" if ok else "failed", "qdrant_point_id": doc_id}
        except Exception as e:
            log.warning("rag_ingest_fail: %s", e)
            return {"status": "failed", "error": str(e)}

    async def attach_rag_to_token(self, chain: Chain, address: str, qdrant_point_id: str) -> bool:
        """Link an existing Qdrant point to a Token row as rag_embedding_id."""
        token = await self.get_token(chain, address)
        if not token:
            return False
        token.rag_embedding_id = qdrant_point_id
        return await self.save_token(token)

    # ── Stats / introspection ────────────────────────────────────────
    async def stats(self) -> dict[str, Any]:
        await self._init_stores()
        out: dict[str, Any] = {
            "stores": await self.probe_stores(),
            "collections": COLLECTIONS,
        }
        if self._health.postgres:
            try:
                async with self._pg_pool.acquire() as conn:
                    out["tokens"] = await conn.fetchval("SELECT COUNT(*) FROM tokens") or 0
                    out["alerts"] = await conn.fetchval("SELECT COUNT(*) FROM alerts") or 0
            except Exception as e:
                out["postgres_error"] = str(e)
        if self._health.neo4j:
            try:
                async with self._neo_driver.session() as s:
                    wallets = await (await s.run("MATCH (w:Wallet) RETURN COUNT(w) AS c")).single()
                    deployers = await (await s.run("MATCH (d:Deployer) RETURN COUNT(d) AS c")).single()
                    out["wallets"] = wallets["c"] or 0
                    out["deployers"] = deployers["c"] or 0
            except Exception as e:
                out["neo4j_error"] = str(e)
        if self._health.qdrant:
            try:
                cols = await self._qdrant.get("/collections")
                if cols.status_code == 200:
                    out["qdrant_collections"] = [
                        c["name"] for c in cols.json().get("result", {}).get("collections", [])
                    ]
            except Exception as e:
                out["qdrant_error"] = str(e)
        return out


# ── Singleton accessor ──────────────────────────────────────────────
_catalog: CatalogService | None = None


def get_catalog() -> CatalogService:
    """Get the global CatalogService. Lazy-init on first call."""
    global _catalog
    if _catalog is None:
        _catalog = CatalogService()
    return _catalog
