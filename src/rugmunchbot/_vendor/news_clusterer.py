# VENDORED 2026-08-17 from rmi-backend/app/domains/news/clusterer.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""T03 - News story clustering (G04 FIX).

Per MINIMAX_M3_TASKS.md T03. MinHash + DBSCAN dedupes raw RSS items into
single "stories" so AI agents don't see the same CoinDesk/The Block story
counted 2-3x in their signal.

Algorithm:
  1. MinHash signature (128 permutations) on shingled title+body (first 500 chars)
  2. Time-aware DBSCAN over the whole feed window, Jaccard threshold 0.6,
     eps=0.15, neighbor pair must ALSO fall within `window_minutes` of each
     other (default 72h so multi-day stories cluster).
  3. Each cluster = one story with all source URLs, sentiment avg, item count,
     distinct source count and a bias distribution across sources.
  4. Persistent cluster keys derived from the representative's MinHash
     signature (content-based, stable across runs) — a story re-clustered
     later maps to the same cluster_id and merges in Postgres.
  5. Persist clusters to Postgres `news_clusters` table; raw items unchanged

Endpoints:
  GET /api/v1/news?clustered=true    returns stories (clusters), not raw items
  GET /api/v1/news                   raw items (legacy)

This module is pure logic - no I/O at import time. Router/background job
call `cluster_items(items) -> list[StoryCluster]`.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.news_ratings import SOURCE_CREDIBILITY, rate_article

logger = get_logger(__name__)

# ── Tokenization ────────────────────────────────────────────────────
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)


def _shingles(text: str, k: int = 3) -> set[str]:
    """k-shingle set of lowercased alphanumeric tokens. For Jaccard."""
    if not text:
        return set()
    toks = _TOKEN_RE.findall(text.lower())
    if len(toks) < k:
        return set(toks)
    return {" ".join(toks[i : i + k]) for i in range(len(toks) - k + 1)}


# ── MinHash ─────────────────────────────────────────────────────────
_NUM_PERM = 128
_MAX_HASH = (1 << 32) - 1


def _minhash_signature(shingles: set[str], seed: int = 42) -> list[int]:
    """128-permutation MinHash signature of a shingle set.

    Uses SHA-256 seeded permutations - fast, deterministic, no numpy.
    """
    if not shingles:
        return [_MAX_HASH] * _NUM_PERM
    sig: list[int] = []
    for i in range(_NUM_PERM):
        m = _MAX_HASH
        for s in shingles:
            h = int.from_bytes(
                hashlib.sha256(f"{i}:{seed}:{s}".encode()).digest()[:4],
                "big",
            )
            if h < m:
                m = h
        sig.append(m)
    return sig


def _jaccard_minhash(a: list[int], b: list[int]) -> float:
    """Estimate Jaccard similarity from two MinHash signatures."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(1 for x, y in zip(a, b, strict=False) if x == y) / len(a)


def _persistent_cluster_key(sig: list[int]) -> str:
    """Content-derived persistent cluster key.

    Hashed from the MinHash signature itself, so the same story produces
    the same cluster_id across runs — regardless of which items happen to
    be in the current window. This lets `persist_clusters` merge new
    sources into an existing story instead of creating a fresh id each run.
    """
    digest = hashlib.sha1(
        "|".join(str(x) for x in sig).encode(),
        usedforsecurity=False,
    ).hexdigest()
    return digest[:16]


def _bias_for_source(source: str) -> str:
    """Bias label for a source name; 'center' if unknown."""
    if not source:
        return "center"
    key = source.lower().strip().replace("_", " ").replace("-", " ")
    if key in SOURCE_CREDIBILITY:
        return str(SOURCE_CREDIBILITY[key].get("bias", "center"))
    for known_key, cred in SOURCE_CREDIBILITY.items():
        if known_key in key or key in known_key:
            return str(cred.get("bias", "center"))
    return "center"


# ── DBSCAN (pure-python, no sklearn dep) ────────────────────────────
def _dbscan(
    signatures: list[list[int]],
    times: list[datetime],
    eps: float = 0.4,
    min_samples: int = 2,
    window_minutes: int = 4320,
) -> list[int]:
    """Density-based clustering with a time-window constraint.

    Returns cluster id per item (-1 = noise).

    Similarity is Jaccard (estimated via MinHash). Neighbours are pairs
    with Jaccard distance <= eps (i.e. similarity >= 1 - eps) AND whose
    published_at times are within `window_minutes` of each other. The time
    constraint replaces the old fixed 30-minute bucketing — a story that
    unfolds over days still chains together as long as each pair of
    sources lands within the window, while unrelated-but-similar headlines
    far apart in time stay separate.
    """
    n = len(signatures)
    labels = [-1] * n
    cluster_id = 0
    max_delta = timedelta(minutes=window_minutes)
    for i in range(n):
        if labels[i] != -1:
            continue
        neighbors: list[int] = []
        for j in range(n):
            if i == j:
                continue
            if abs(times[i] - times[j]) > max_delta:
                continue
            if (1.0 - _jaccard_minhash(signatures[i], signatures[j])) <= eps:
                neighbors.append(j)
        if len(neighbors) < min_samples - 1:
            # not enough neighbours - mark as noise (may become border later)
            continue
        labels[i] = cluster_id
        seed_set = list(neighbors)
        k = 0
        while k < len(seed_set):
            q = seed_set[k]
            if labels[q] == -1:
                labels[q] = cluster_id
                q_neighbors: list[int] = []
                for j in range(n):
                    if j == q:
                        continue
                    if abs(times[q] - times[j]) > max_delta:
                        continue
                    if (1.0 - _jaccard_minhash(signatures[q], signatures[j])) <= eps:
                        q_neighbors.append(j)
                if len(q_neighbors) >= min_samples - 1:
                    seed_set.extend(q_neighbors)
            elif labels[q] is None or labels[q] == -1:
                labels[q] = cluster_id
            k += 1
        cluster_id += 1
    return labels


# ── Domain types ────────────────────────────────────────────────────
@dataclass
class NewsItem:
    """Minimal news item for clustering. Adapts from DB rows or dicts."""

    id: str
    title: str
    body: str = ""
    source: str = ""
    url: str = ""
    published_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    sentiment: float = 0.0

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> NewsItem:
        published = row.get("published_at") or row.get("created_at")
        if isinstance(published, str):
            try:
                published = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                published = datetime.now(UTC)
        elif not isinstance(published, datetime):
            published = datetime.now(UTC)
        return cls(
            id=str(row.get("id", row.get("news_id", ""))),
            title=row.get("title", "") or "",
            body=(row.get("body") or row.get("summary") or "")[:500],
            source=row.get("source", "") or "",
            url=row.get("url", "") or "",
            published_at=published,
            sentiment=float(row.get("sentiment", 0.0) or 0.0),
        )


@dataclass
class StoryCluster:
    """One deduplicated story spanning 1+ source items."""

    cluster_id: str
    representative_title: str
    source_urls: list[str]
    sources: list[str]
    first_seen: datetime
    last_updated: datetime
    item_count: int
    sentiment_avg: float
    item_ids: list[str]
    source_count: int = 0
    bias_distribution: dict[str, int] = field(default_factory=dict)
    # Representative article (longest title member) + its rating card.
    # Enriched at cluster time so RAG docs can cite bias/sentiment/factual
    # without a second rating pass. Defaults keep hand-built constructors
    # (tests, callers) working unchanged.
    representative_body: str = ""
    representative_url: str = ""
    sentiment_label: str = "neutral"
    bias: str = "center"
    factual_rating: str = "unrated"
    source_tier: int = 3

    def __post_init__(self) -> None:
        if self.source_count == 0 and self.sources:
            self.source_count = len(self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "representative_title": self.representative_title,
            "source_urls": self.source_urls,
            "sources": self.sources,
            "first_seen": self.first_seen.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "item_count": self.item_count,
            "sentiment_avg": round(self.sentiment_avg, 3),
            "item_ids": self.item_ids,
            "source_count": self.source_count,
            "bias_distribution": self.bias_distribution,
            "sentiment_label": self.sentiment_label,
            "bias": self.bias,
            "factual_rating": self.factual_rating,
            "source_tier": self.source_tier,
        }


# ── Main entry point ────────────────────────────────────────────────
def cluster_items(
    items: list[NewsItem],
    window_minutes: int = 4320,
    eps: float = 0.4,
    min_samples: int = 2,
) -> list[StoryCluster]:
    """Cluster news items into stories.

    Items are clustered with time-aware DBSCAN over the full feed window:
    two items join the same story when Jaccard similarity >= 0.6 (distance
    <= eps=0.4) AND they are published within `window_minutes` (default
    72h = 4320 min). Multi-day stories therefore cluster — the old fixed
    30-minute buckets split them apart. Single-item clusters are kept
    (they're "noise" in DBSCAN terms but valid singleton stories).

    Cluster ids are content-derived (persistent) so re-clustering the same
    story later yields the same key and Postgres upserts merge members.
    """
    t0 = time.time()
    if not items:
        return []

    ordered = sorted(items, key=lambda x: x.published_at)
    sigs = [_minhash_signature(_shingles(f"{it.title} {it.body}")) for it in ordered]
    times = [it.published_at for it in ordered]
    labels = _dbscan(sigs, times, eps=eps, min_samples=min_samples, window_minutes=window_minutes)

    # Singletons (label == -1) still become stories
    clusters: dict[int, list[int]] = {}
    for idx, lbl in enumerate(labels):
        clusters.setdefault(lbl if lbl != -1 else idx, []).append(idx)

    stories: list[StoryCluster] = []
    for _cid, indices in clusters.items():
        members = [ordered[i] for i in indices]
        # Pick representative = longest title (usually the most descriptive)
        rep = max(members, key=lambda x: len(x.title))
        rep_sig = _minhash_signature(_shingles(f"{rep.title} {rep.body}"))
        sentiments = [m.sentiment for m in members if m.sentiment is not None]
        avg_sent = sum(sentiments) / len(sentiments) if sentiments else 0.0
        source_set = sorted({m.source for m in members if m.source})
        bias_dist: dict[str, int] = {}
        for m in members:
            b = _bias_for_source(m.source)
            bias_dist[b] = bias_dist.get(b, 0) + 1
        # Rating card for the representative article — lets RAG docs cite
        # bias / sentiment / factuality / tier per story.
        rep_rating = rate_article(title=rep.title, content=rep.body, source=rep.source)
        stories.append(
            StoryCluster(
                cluster_id=_persistent_cluster_key(rep_sig),
                representative_title=rep.title,
                source_urls=[m.url for m in members if m.url],
                sources=source_set,
                first_seen=min(m.published_at for m in members),
                last_updated=max(m.published_at for m in members),
                item_count=len(members),
                sentiment_avg=avg_sent,
                item_ids=[m.id for m in members],
                source_count=len(source_set),
                bias_distribution=bias_dist,
                representative_body=rep.body,
                representative_url=rep.url,
                sentiment_label=rep_rating.sentiment_label,
                bias=rep_rating.bias,
                factual_rating=rep_rating.factual_rating,
                source_tier=rep_rating.source_tier,
            )
        )

    logger.info(
        "news_clustered items=%d stories=%d elapsed_ms=%.1f",
        len(items),
        len(stories),
        (time.time() - t0) * 1000,
    )
    return stories


# ── DB persistence (optional, lazy import) ──────────────────────────
_PG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS news_clusters (
  cluster_id           TEXT PRIMARY KEY,
  representative_title TEXT NOT NULL,
  first_seen           TIMESTAMPTZ NOT NULL,
  last_updated         TIMESTAMPTZ NOT NULL,
  item_count           INTEGER NOT NULL DEFAULT 0,
  sentiment_avg        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  source_count         INTEGER NOT NULL DEFAULT 0,
  bias_distribution    JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_urls          JSONB NOT NULL DEFAULT '[]'::jsonb,
  sources              JSONB NOT NULL DEFAULT '[]'::jsonb,
  item_ids             JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS news_clusters_last_updated_idx
  ON news_clusters (last_updated DESC);
ALTER TABLE news_clusters ADD COLUMN IF NOT EXISTS source_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE news_clusters ADD COLUMN IF NOT EXISTS bias_distribution JSONB NOT NULL DEFAULT '{}'::jsonb;
"""


async def ensure_schema() -> bool:
    """Create news_clusters table if missing. Returns True on success."""
    try:
        import asyncpg

        from rugmunchbot._vendor.db_pool import PG_URL

        conn = await asyncpg.connect(PG_URL)
        try:
            await conn.execute(_PG_SCHEMA_SQL)
        finally:
            await conn.close()
        logger.info("news_clusters_schema_ready")
        return True
    except (KeyError, ValueError, TypeError) as exc:
        logger.debug("ensure_schema_error", exc_info=True)
        logger.warning("news_clusters_schema_failed err=%s", exc)
        return False


def _dedupe_keep_order(values: list[Any]) -> list[Any]:
    """Dedupe a list preserving first-seen order."""
    seen: set[str] = set()
    out: list[Any] = []
    for v in values:
        vk = str(v)
        if vk not in seen:
            seen.add(vk)
            out.append(v)
    return out


async def persist_clusters(stories: list[StoryCluster]) -> int:
    """Upsert stories to Postgres, merging with any existing cluster.

    Cluster ids are content-derived, so a story re-clustered across runs
    hits the same row. Existing members (source_urls, sources, item_ids,
    first_seen) are merged rather than replaced, and last_updated advances
    to the newest member — a running story keeps accumulating sources.
    Returns rows affected.
    """
    if not stories:
        return 0
    try:
        import json

        import asyncpg

        from rugmunchbot._vendor.db_pool import PG_URL

        conn = await asyncpg.connect(PG_URL)
        try:
            # Load existing rows for merge
            ids = [s.cluster_id for s in stories]
            existing_rows = await conn.fetch(
                "SELECT cluster_id, source_urls, sources, item_ids, first_seen, last_updated "
                "FROM news_clusters WHERE cluster_id = ANY($1::text[])",
                ids,
            )
            existing = {r["cluster_id"]: r for r in existing_rows}

            rows = []
            for s in stories:
                ex = existing.get(s.cluster_id)
                source_urls = _dedupe_keep_order([*(ex["source_urls"] if ex else []), *s.source_urls])
                sources = _dedupe_keep_order([*(ex["sources"] if ex else []), *s.sources])
                item_ids = _dedupe_keep_order([*(ex["item_ids"] if ex else []), *s.item_ids])
                first_seen = min(s.first_seen, ex["first_seen"]) if ex else s.first_seen
                last_updated = max(s.last_updated, ex["last_updated"]) if ex else s.last_updated
                # Bias distribution: recount over merged sources
                bias_dist: dict[str, int] = {}
                for src in sources:
                    b = _bias_for_source(src)
                    bias_dist[b] = bias_dist.get(b, 0) + 1
                rows.append(
                    (
                        s.cluster_id,
                        s.representative_title,
                        first_seen,
                        last_updated,
                        len(item_ids),
                        s.sentiment_avg,
                        len(sources),
                        json.dumps(bias_dist),
                        json.dumps(source_urls),
                        json.dumps(sources),
                        json.dumps(item_ids),
                    )
                )
            await conn.executemany(
                """
                INSERT INTO news_clusters
                  (cluster_id, representative_title, first_seen, last_updated,
                   item_count, sentiment_avg, source_count, bias_distribution,
                   source_urls, sources, item_ids)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb,$11::jsonb)
                ON CONFLICT (cluster_id) DO UPDATE SET
                  representative_title = EXCLUDED.representative_title,
                  first_seen = EXCLUDED.first_seen,
                  last_updated = EXCLUDED.last_updated,
                  item_count = EXCLUDED.item_count,
                  sentiment_avg = EXCLUDED.sentiment_avg,
                  source_count = EXCLUDED.source_count,
                  bias_distribution = EXCLUDED.bias_distribution,
                  source_urls = EXCLUDED.source_urls,
                  sources = EXCLUDED.sources,
                  item_ids = EXCLUDED.item_ids
                """,
                rows,
            )
            return len(rows)
        finally:
            await conn.close()
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.debug("unknown_error", exc_info=True)
        logger.warning("news_clusters_persist_failed err=%s", exc)
        return 0


async def load_recent_clusters(limit: int = 50) -> list[dict[str, Any]]:
    """Load recent clusters from Postgres."""
    try:
        import json

        import asyncpg

        from rugmunchbot._vendor.db_pool import PG_URL

        conn = await asyncpg.connect(PG_URL)
        try:
            rows = await conn.fetch(
                "SELECT * FROM news_clusters ORDER BY last_updated DESC LIMIT $1",
                limit,
            )
            return [
                {
                    "cluster_id": r["cluster_id"],
                    "representative_title": r["representative_title"],
                    "first_seen": r["first_seen"].isoformat(),
                    "last_updated": r["last_updated"].isoformat(),
                    "item_count": r["item_count"],
                    "sentiment_avg": r["sentiment_avg"],
                    "source_count": r.get("source_count", 0),
                    "bias_distribution": json.loads(r["bias_distribution"]) if r.get("bias_distribution") else {},
                    "source_urls": json.loads(r["source_urls"]),
                    "sources": json.loads(r["sources"]),
                    "item_ids": json.loads(r["item_ids"]),
                }
                for r in rows
            ]
        finally:
            await conn.close()
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.debug("load_recent_clusters_error", exc_info=True)
        logger.warning("news_clusters_load_failed err=%s", exc)
        return []


def story_doc_id(cluster_id: str) -> str:
    """Stable RAG doc_id for a story: ``news:{cluster_id}``.

    Cluster ids are content-derived (persistent), so the same story
    re-clustered in a later batch maps to the same doc_id → Qdrant upserts
    to the same point (uuid5-stable). This is what collapses "same article
    from 20 sources" into one RAG doc.
    """
    return f"news:{cluster_id}"


def build_story_rag_docs(
    items: list[NewsItem],
    window_minutes: int = 4320,
    eps: float = 0.4,
    min_samples: int = 2,
) -> list[dict[str, Any]]:
    """Cluster *items* into stories and emit ONE RAG-ready doc per story.

    Dedup contract: same headline/story from N sources collapses into a
    single doc whose ``doc_id`` is ``news:{cluster_id}`` and whose metadata
    carries ``source_count``, ``sources``, ``source_urls`` and the
    ``bias_distribution`` — the bot can cite "this story has N sources"
    and the bias-spectrum panel can render the per-story spread.

    Each returned doc::

        {"doc_id": "news:<cluster_id>",
         "content": "Title: <representative title>\\n\\n<representative body>",
         "metadata": {cluster_id, source_count, sources, source_urls,
                      bias_distribution, sentiment_avg, sentiment_label,
                      bias, factual_rating, source_tier, item_ids,
                      news_id, title, source, url, published_at}}

    Items are the clusterer's minimal ``NewsItem`` (id, title, body,
    source, url, published_at, sentiment) — adapt rows/dicts before calling.
    """
    stories = cluster_items(items, window_minutes=window_minutes, eps=eps, min_samples=min_samples)
    docs: list[dict[str, Any]] = []
    for s in stories:
        doc_id = story_doc_id(s.cluster_id)
        content = f"Title: {s.representative_title}\n\n{s.representative_body or ''}".strip()
        metadata: dict[str, Any] = {
            "news_id": doc_id,
            "title": s.representative_title,
            "cluster_id": s.cluster_id,
            "source_count": s.source_count,
            "sources": s.sources,
            "source_urls": s.source_urls,
            "bias_distribution": s.bias_distribution,
            "sentiment_avg": round(s.sentiment_avg, 3),
            "sentiment_label": s.sentiment_label,
            "bias": s.bias,
            "factual_rating": s.factual_rating,
            "source_tier": s.source_tier,
            "item_ids": s.item_ids,
            "published_at": s.last_updated.isoformat(),
            "source": s.sources[0] if s.sources else "",
            "url": s.source_urls[0] if s.source_urls else "",
        }
        docs.append({"doc_id": doc_id, "content": content, "metadata": metadata})
    return docs


async def load_story_sources(cluster_id: str) -> dict[str, Any] | None:
    """Load one story cluster with its full per-source distribution.

    Powers the bias-spectrum panel: given a story's ``cluster_id``, return
    the distinct sources, per-source URLs, bias distribution and sentiment
    average. Returns None when the cluster is unknown.
    """
    try:
        import json

        import asyncpg

        from rugmunchbot._vendor.db_pool import PG_URL

        conn = await asyncpg.connect(PG_URL)
        try:
            row = await conn.fetchrow(
                "SELECT * FROM news_clusters WHERE cluster_id = $1",
                cluster_id,
            )
            if not row:
                return None
            return {
                "cluster_id": row["cluster_id"],
                "representative_title": row["representative_title"],
                "first_seen": row["first_seen"].isoformat(),
                "last_updated": row["last_updated"].isoformat(),
                "item_count": row["item_count"],
                "sentiment_avg": row["sentiment_avg"],
                "source_count": row.get("source_count", 0),
                "bias_distribution": json.loads(row["bias_distribution"]) if row.get("bias_distribution") else {},
                "source_urls": json.loads(row["source_urls"]),
                "sources": json.loads(row["sources"]),
                "item_ids": json.loads(row["item_ids"]),
            }
        finally:
            await conn.close()
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.debug("load_story_sources_error", exc_info=True)
        logger.warning("news_clusters_load_failed cluster=%s err=%s", cluster_id, exc)
        return None


__all__ = [
    "NewsItem",
    "StoryCluster",
    "build_story_rag_docs",
    "cluster_items",
    "ensure_schema",
    "load_recent_clusters",
    "load_story_sources",
    "persist_clusters",
    "story_doc_id",
]
