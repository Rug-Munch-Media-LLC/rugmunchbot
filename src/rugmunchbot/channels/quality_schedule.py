"""10-post/day main-channel quality schedule — dedup + rerank + slotting.

Channel-quality overhaul (2026-08-29): @cryptorugmuncher runs a strict
daily editorial budget of 10 high-quality posts:

* **2 fixed market-update slots** — morning (07:00 ET) + midday (13:00 ET)
  market briefs ("The Morning Rug Market Report" / "The Midday Rug Market
  Report"). Those are separate JobQueue jobs
  (``channels/scheduler/_register.py``: ``daily_market_summary`` /
  ``daily_market_summary_midday``); this module RESERVES their slots in the
  schedule so the operator dry-run and any UI see the full 10-post day.
* **8 reranked quality-content slots** — the best fresh stories from the
  RAG content-distribution lane (``channels/scheduler/_posts_rag.py``),
  deduped twice (stable-key first, then qwen3-4B embedding cosine), reranked
  by the deep 4B cross-encoder (``_vendor/rag_pkg/rerank.py``, tier
  ``"deep"`` — the 0.6B speed tier is no longer used here), and posted with
  commentary + source link (the RAG lane already enforces the quality /
  recency / source gates before this module ever sees an item).

No double-post guarantee: the same ``story_ref``/URL can never occupy two
slots, and near-duplicate copy (embedding cosine >= ``EMBED_DEDUP_THRESHOLD``)
collapses into one entry with an honest ``dedupe_note``. The scheduler job
additionally runs every slot through the global ``_remember`` dedupe before
send (same namespace as the RAG poster), so a story already posted anywhere
cannot appear here.

Every model call is fail-open: a dead embedder/reranker degrades to
stable-key dedup + the existing per-item ``score``/``importance`` (never
invents content), and the schedule still builds — quality over volume, but
silence is not the default.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.quality_schedule")

#: Total daily editorial budget on the main channel (matches the WO-2 cap
#: default in ``channels/main_cap.py`` and the owner strategy doc).
MAIN_POSTS_PER_DAY = int(os.getenv("RMI_MAIN_POSTS_PER_DAY", "10"))
#: Market-update slots reserved out of the total (morning + midday).
MARKET_SLOTS_PER_DAY = int(os.getenv("RMI_MAIN_MARKET_SLOTS_PER_DAY", "2"))
#: Reranked quality-content slots (10 - 2).
QUALITY_SLOTS_PER_DAY = max(0, MAIN_POSTS_PER_DAY - MARKET_SLOTS_PER_DAY)

#: Target slot times (UTC) for the quality-content slots, spread through the
#: day so the channel has rhythm instead of bursts. The job enforces the
#: COUNT (budget); these times are the editorial plan the dry-run renders.
QUALITY_SLOT_TIMES_UTC: tuple[str, ...] = tuple(
    t.strip()
    for t in os.getenv(
        "RMI_MAIN_QUALITY_SLOT_TIMES",
        "08:00,09:00,10:00,12:00,14:00,16:00,18:00,20:00",
    ).split(",")
    if t.strip()
)[:QUALITY_SLOTS_PER_DAY]

#: Market slots: (label, hour, minute, tz_name) — mirrors the two registered
#: jobs (07:00/13:00 America/New_York). Rendered in the dry-run only.
MARKET_SLOT_DEFS: tuple[tuple[str, str], ...] = (
    ("market-morning", "07:00"),
    ("market-midday", "13:00"),
)

#: Human edition names for the two reserved market slots (dry-run titles).
MARKET_EDITION_LABELS: dict[str, str] = {
    "market-morning": "The Morning Rug Market Report",
    "market-midday": "The Midday Rug Market Report",
}

#: Embedding-cosine threshold above which two candidates are the "same
#: story" for dedup purposes (qwen3-4B embeddings, 0..1 cosine). Calibrated
#: 2026-08-29 on the live 4B embedder: same-story paraphrases ~0.91, related
#: but distinct stories 0.4-0.6, unrelated < 0.5.
EMBED_DEDUP_THRESHOLD = float(os.getenv("RMI_DAILY_EMBED_DEDUP_THRESHOLD", "0.90"))
#: Max candidates considered per day — bounds the embed/rerank cost.
MAX_CANDIDATES = int(os.getenv("RMI_DAILY_QUALITY_MAX_CANDIDATES", "24"))
#: Reranker query — phrased to lexically overlap "worth publishing" signals.
QUALITY_RERANK_QUERY = os.getenv(
    "RMI_DAILY_QUALITY_RERANK_QUERY",
    "high-quality crypto market intelligence story worth publishing on a curated channel",
)
#: Default importance when neither rerank nor item score is available.
_DEFAULT_IMPORTANCE = 0.5

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


@dataclass(slots=True)
class ScheduleEntry:
    """One planned main-channel post in the daily schedule."""

    slot_index: int
    slot_time: str  # "HH:MM" (UTC for quality, local ET for market)
    kind: str  # "market-morning" | "market-midday" | "quality"
    title: str
    source: str
    importance: float
    item: dict[str, Any] | None = None  # None for fixed market slots
    dedupe_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": f"{self.slot_index:02d}",
            "time_utc": self.slot_time,
            "kind": self.kind,
            "title": self.title,
            "source": self.source,
            "importance": round(self.importance, 4),
            "dedupe_note": self.dedupe_note,
        }


# ── identity helpers (pure) ────────────────────────────────────────────────


def _stable_key(item: dict[str, Any]) -> str:
    """Cross-lane story identity: story_ref → url → title/address.

    Mirrors the global dedupe contract in ``channels/scheduler/_core.py``:
    lane prefixes are ignored, so the same story can never occupy two slots.
    """
    for key in ("story_ref", "url", "source_url", "link", "canonical_url"):
        value = str(item.get(key) or "").strip()
        if value and value not in ("None", "null"):
            # Strip lane prefixes ("rag:https://…" → "https://…") like the
            # global dedupe does, but never mangle a bare URL itself.
            if ":" in value and not value.startswith(("http://", "https://", "t.me/")):
                return value.split(":", 1)[-1]
            return value
    title = str(item.get("title") or item.get("name") or item.get("symbol") or "").strip()
    addr = str(item.get("address") or item.get("token_address") or "").strip()
    return addr or title or str(id(item))


def _candidate_text(item: dict[str, Any]) -> str:
    """Text the reranker/embedder sees: title + body + link (bounded)."""
    parts = [
        str(item.get("title") or item.get("name") or item.get("symbol") or "").strip(),
        str(item.get("text") or item.get("summary") or item.get("body") or "").strip(),
    ]
    text = " ".join(p for p in parts if p)[:1500]
    url = str(item.get("url") or item.get("source_url") or item.get("link") or "").strip()
    if url:
        text = f"{text} {url}" if text else url
    return text or _stable_key(item)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 on mismatch)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=True))
        na = sum(float(x) * float(x) for x in a) ** 0.5
        nb = sum(float(y) * float(y) for y in b) ** 0.5
    except (TypeError, ValueError):
        return 0.0
    if not na or not nb:
        return 0.0
    return float(dot / (na * nb))


# ── embed-based dedup (qwen3-4B, fail-open) ────────────────────────────────

#: Total wall-clock budget for one embed-dedup pass. Measured live
#: 2026-08-29: one 8-text batch ≈ 25-30s through the 4B embedder on the CPU
#: box — this budget must clear that (fail-open beyond it).
EMBED_TIMEOUT_S = float(os.getenv("RMI_DAILY_EMBED_TIMEOUT_S", "40"))
#: Max texts embedded per pass (bounds the 4B cost further).
MAX_EMBED_TEXTS = int(os.getenv("RMI_DAILY_MAX_EMBED_TEXTS", "8"))


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Qwen3-Embedding-4B via an OpenAI-compatible /embeddings endpoint.

    Defaults to the local self-hosted llama.cpp (``EMBEDDER_URL`` /
    ``EMBEDDER_MODEL`` — frugality-first, zero cost). To route the content
    dedup through **DeepInfra** (hosted Qwen3-Embedding-4B) instead, set:
      ``EMBEDDER_URL=https://api.deepinfra.com/v1/openai``,
      ``EMBEDDER_MODEL=Qwen/Qwen3-Embedding-4B``,
      ``EMBEDDER_API_KEY=<DEEPINFRA_API_KEY>``.
    When ``EMBEDDER_API_KEY`` is set a ``Bearer`` auth header is sent, so the
    same client works against DeepInfra (or any authed OpenAI-compatible
    gateway) without a different code path — the content selection is never
    hard-wired to a local embedder.

    One batch POST, bounded by ``EMBED_TIMEOUT_S`` via ``asyncio.wait_for``
    so a slow/cold embedder never stalls the schedule builder or a job tick.
    The 4B model serves 2560-dim vectors; any non-empty vector is accepted
    (no strict dim pin — the old 1024 assumption silently disabled dedup).
    Zero-vectors on failure — callers treat those as "no signal" and keep
    the candidate.
    """
    import asyncio

    import httpx

    url = os.getenv("EMBEDDER_URL", "http://127.0.0.1:8004/v1")
    model = os.getenv("EMBEDDER_MODEL", "qwen3-embedding-4b")
    dims = int(os.getenv("EMBEDDER_DIMS", "1024"))
    api_key = os.getenv("EMBEDDER_API_KEY", "").strip()
    texts = texts[:MAX_EMBED_TEXTS]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None

    async def _batch() -> list[list[float]]:
        async with httpx.AsyncClient(timeout=EMBED_TIMEOUT_S + 60) as client:
            resp = await client.post(
                f"{url.rstrip('/')}/embeddings",
                json={"model": model, "input": [t[:8000] for t in texts]},
                headers=headers,
            )
            if resp.status_code != 200:
                return []
            data = resp.json().get("data", [])
            ordered = sorted(data, key=lambda x: int(x.get("index", 0)))
            out: list[list[float]] = []
            for row in ordered:
                vec = row.get("embedding", [])
                if isinstance(vec, list) and len(vec) >= 256:  # sanity, not a dim pin
                    out.append(vec)
                else:
                    out.append([0.0] * dims)
            return out

    try:
        results = await asyncio.wait_for(_batch(), timeout=EMBED_TIMEOUT_S)
        if not results:
            return [[0.0] * dims for _ in texts]
        return results
    except Exception as exc:
        logger.debug("quality_schedule:embedder_unavailable err=%s", str(exc)[:120])
        return [[0.0] * dims for _ in texts]


async def dedupe_by_embedding(
    items: list[dict[str, Any]],
    threshold: float = EMBED_DEDUP_THRESHOLD,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Drop near-duplicate copy using qwen3-4B embeddings.

    Returns ``(kept, notes)`` where ``notes[key]`` explains why a candidate
    was dropped (``"embed_similar_to:<kept-key>"``). Fail-open: a dead
    embedder keeps every candidate unchanged (stable-key dedup already ran).
    """
    if not items:
        return [], {}
    vectors = await _embed_texts([_candidate_text(it) for it in items])
    if not vectors or all(not any(v) for v in vectors):
        logger.debug("quality_schedule:embed_dedup_no_signal")
        return items, {}

    kept: list[dict[str, Any]] = []
    notes: dict[str, str] = {}
    for idx, item in enumerate(items):
        vec = vectors[idx] if idx < len(vectors) else []
        if not vec or not any(vec):  # all-zero = no signal (real embeddings
            kept.append(item)  # contain legitimate zeros — only all-zero is bad)
            continue
        dropped_by: str | None = None
        for kept_item, kept_vec in zip(kept, vectors[: len(kept)], strict=False):
            if cosine_similarity(vec, kept_vec) >= threshold:
                dropped_by = _stable_key(kept_item)
                break
        if dropped_by is not None:
            notes[_stable_key(item)] = f"embed_similar_to:{dropped_by[:48]}"
        else:
            kept.append(item)
    return kept, notes


# ── rerank (deep 4B cross-encoder, fail-open) ──────────────────────────────

#: Deep reranker endpoint (Qwen3-Reranker-4B — the 0.6B speed tier is not
#: used on this path). Mirrors ``rag_pkg.rerank.RERANK_URL`` default.
DEEP_RERANK_URL = os.getenv("RMI_DAILY_RERANK_URL", "http://127.0.0.1:8003")
#: Noise floor for RELATIVE ordering — deliberately far below the vendored
#: wrapper's 0.10 (whose sigmoid filter empties the batch); we only sort.
RERANK_ORDER_FLOOR = float(os.getenv("RMI_DAILY_RERANK_FLOOR", "0.01"))
#: Timeout for one rerank call. Measured live 2026-08-29 on the CPU box:
#: ~4.4s/doc through the 4B cross-encoder — 12 docs ≈ 53s, 6 docs (the live
#: job's candidate pool) ≈ 26s at the 120-min cadence. Fail-open beyond it.
RERANK_TIMEOUT_S = float(os.getenv("RMI_DAILY_RERANK_TIMEOUT_S", "60"))


async def rerank_quality(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach ``importance`` (0..1) to each item, best-first, via the 4B.

    Calls the Qwen3-Reranker-4B directly (``/rerank``) — the vendored
    ``rag_pkg.rerank`` wrapper applies a vertical [TASK] instruction + a
    0.10 noise floor that empties the batch, and this path only needs
    RELATIVE ordering. Fail-open: any reranker outage keeps the items in
    input order with their existing ``score``/``importance`` (default 0.5)
    — the schedule still builds, the model never gates content it did not
    see.
    """
    import asyncio

    import httpx

    if not items:
        return []
    documents = [_candidate_text(it) for it in items[:MAX_CANDIDATES]]

    async def _call() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=RERANK_TIMEOUT_S + 15) as client:
            resp = await client.post(
                f"{DEEP_RERANK_URL.rstrip('/')}/rerank",
                json={
                    "query": QUALITY_RERANK_QUERY,
                    "documents": documents,
                    "top_n": len(documents),
                },
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                scores: dict[int, float] = {}
                for r in results:
                    idx = int(r.get("index", -1))
                    score = float(r.get("relevance_score", 0.0))
                    if 0 <= idx < len(items) and score >= RERANK_ORDER_FLOOR:
                        scores[idx] = score
                if scores:
                    for idx, item in enumerate(items):
                        item["importance"] = round(max(0.0, min(1.0, scores.get(idx, 0.0))), 4)
                    return sorted(items, key=lambda i: i.get("importance", 0.0), reverse=True)
            return []

    try:
        ranked = await asyncio.wait_for(_call(), timeout=RERANK_TIMEOUT_S)
        if ranked:
            return ranked
    except Exception as exc:
        logger.debug("quality_schedule:rerank_unavailable err=%s", str(exc)[:120])

    for item in items:
        raw = item.get("score", item.get("rerank_score", item.get("importance")))
        try:
            item["importance"] = (
                round(max(0.0, min(1.0, float(raw))), 4) if raw is not None else _DEFAULT_IMPORTANCE
            )
        except (TypeError, ValueError):
            item["importance"] = _DEFAULT_IMPORTANCE
    return sorted(items, key=lambda i: i.get("importance", 0.0), reverse=True)


# ── daily schedule builder ─────────────────────────────────────────────────


async def build_daily_quality_schedule(
    items: list[dict[str, Any]],
    *,
    posted_today: int = 0,
    date: str | None = None,
    run_embed_dedup: bool = True,
    run_rerank: bool = True,
    seen_keys: set[str] | None = None,
) -> list[ScheduleEntry]:
    """Build the day's 10-post schedule: 2 market slots + quality slots.

    Args:
        items: Fresh candidate drafts (post-gate — quality/recency/source
            already applied by the RAG lane).
        posted_today: Number of quality-content posts already made today
            (reduces the remaining quality slots; market slots are fixed).
        date: ``YYYY-MM-DD`` label for the day (default today UTC).
        run_embed_dedup: When True, collapse near-duplicate copy via
            qwen3-4B embeddings (fail-open to key-dedup only).
        run_rerank: When True, order by the deep 4B reranker (fail-open to
            the item's existing score).
        seen_keys: Keys already posted anywhere today — excluded (global
            no-double-post contract, mirrors ``_remember``).

    Returns exactly ``MAIN_POSTS_PER_DAY`` entries when enough unique
    candidates exist: 2 market slots + up to ``QUALITY_SLOTS_PER_DAY``
    quality entries (fewer when candidates run out). Never raises.
    """
    day = date or datetime.now(UTC).strftime("%Y-%m-%d")
    schedule: list[ScheduleEntry] = []

    # 1) Fixed market slots (reserved — their jobs run independently).
    for idx, (kind, slot_time) in enumerate(MARKET_SLOT_DEFS[:MARKET_SLOTS_PER_DAY]):
        edition_label = MARKET_EDITION_LABELS.get(kind, "The Morning Rug Market Report")
        schedule.append(
            ScheduleEntry(
                slot_index=idx,
                slot_time=slot_time,
                kind=kind,
                title=f"{edition_label} ({day})",
                source="daily_market_summary",
                importance=1.0,
            )
        )

    # 2) Stable-key dedup (same story can never occupy two slots) + exclude
    #    anything already posted today.
    seen = set(seen_keys or {})
    unique: list[dict[str, Any]] = []
    key_notes: dict[str, str] = {}
    for item in items[:MAX_CANDIDATES]:
        key = _stable_key(item)
        if key in seen or key in {_stable_key(u) for u in unique}:
            key_notes[key] = "duplicate_key"
            continue
        unique.append(item)
        seen.add(key)

    # 3) Embed-based near-duplicate collapse (fail-open).
    if run_embed_dedup and len(unique) > 1:
        unique, embed_notes = await dedupe_by_embedding(unique)
    else:
        embed_notes = {}

    # 4) Rerank best-first (fail-open).
    if run_rerank:
        unique = await rerank_quality(unique)

    # 5) Fill the remaining quality slots (budget: 8 - posted_today).
    remaining = max(0, QUALITY_SLOTS_PER_DAY - posted_today)
    for i, item in enumerate(unique[:remaining]):
        slot_index = MARKET_SLOTS_PER_DAY + i
        slot_time = QUALITY_SLOT_TIMES_UTC[i] if i < len(QUALITY_SLOT_TIMES_UTC) else "--:--"
        key = _stable_key(item)
        note = embed_notes.get(key, "") or key_notes.get(key, "")
        schedule.append(
            ScheduleEntry(
                slot_index=slot_index,
                slot_time=slot_time,
                kind="quality",
                title=str(
                    item.get("title") or item.get("name") or item.get("symbol") or "Untitled"
                )[:120],
                source=str(
                    item.get("story_ref")
                    or item.get("source")
                    or item.get("source_channel")
                    or "rag"
                )[:80],
                importance=float(item.get("importance", _DEFAULT_IMPORTANCE)),
                item=item,
                dedupe_note=note,
            )
        )

    # Unfilled quality slots are rendered explicitly so the budget is visible.
    while len(schedule) < MAIN_POSTS_PER_DAY:
        slot_index = len(schedule)
        slot_time = (
            QUALITY_SLOT_TIMES_UTC[slot_index - MARKET_SLOTS_PER_DAY]
            if slot_index - MARKET_SLOTS_PER_DAY < len(QUALITY_SLOT_TIMES_UTC)
            else "--:--"
        )
        schedule.append(
            ScheduleEntry(
                slot_index=slot_index,
                slot_time=slot_time,
                kind="quality",
                title="(unfilled — no fresh candidate cleared the gates)",
                source="",
                importance=0.0,
            )
        )

    return schedule


def render_schedule(schedule: list[ScheduleEntry]) -> str:
    """Human-readable dry-run output for the 10-post day (no secrets).

    Market brief slots render in their configured local zone (America/New_York
    — the daily summary job's tz); quality slots render UTC.
    """
    lines = [
        f"DAILY MAIN SCHEDULE — {MAIN_POSTS_PER_DAY} posts/day "
        "(market briefs America/New_York · quality UTC)"
    ]
    for entry in schedule:
        note = f"  [{entry.dedupe_note}]" if entry.dedupe_note else ""
        lines.append(
            f"  {entry.slot_index:02d}. {entry.slot_time}  {entry.kind:<15} "
            f"{entry.importance:>5.2f}  {entry.title}{note}"
        )
    return "\n".join(lines)


__all__ = [
    "EMBED_DEDUP_THRESHOLD",
    "MAIN_POSTS_PER_DAY",
    "MARKET_EDITION_LABELS",
    "MARKET_SLOTS_PER_DAY",
    "MARKET_SLOT_DEFS",
    "MAX_CANDIDATES",
    "QUALITY_RERANK_QUERY",
    "QUALITY_SLOTS_PER_DAY",
    "QUALITY_SLOT_TIMES_UTC",
    "ScheduleEntry",
    "build_daily_quality_schedule",
    "cosine_similarity",
    "dedupe_by_embedding",
    "render_schedule",
    "rerank_quality",
]
