# VENDORED 2026-08-17 from rmi-backend/app/domains/content/briefing.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Daily Rug Report assembler — draft → review → publish.

Implements the §3 content skeleton + §6 pipeline from the daily-rug-report
methodology (2026-08-20). The pipeline is:

    1. ``gather_briefing_context()`` — best-effort, structured context pulled
       from the market snapshot (CoinGecko, same pattern as
       ``app.routers.chat._fetch_market_context``) and RAG collections
       (news_v2 / market_intel / known_scams / threat_intel / defi_exploits /
       whale_transfers). Top stories are reranked by the Qwen3 reranker
       for importance before inclusion.
    2. ``draft_briefing()`` — renders the fixed §3 skeleton. The default
        writer is the remote-LLM ``llm_writer`` (production), which re-writes
        the prose from the gathered context; whenever the LLM is unavailable,
        disabled, or returns empty, a deterministic template fallback produces
        a real, accurate, source-cited post from the context alone.
    3. ``review_briefing()`` — pure fact-check: every number in the draft
       must appear in the context (hallucination gate), and metric-bearing
       claim bullets must carry a source reference (URL or address).
    4. ``publish_briefing()`` — publishes via ``app.content_platform.create_post``
       with ``post_type="briefing"``. Long-form goes to Ghost (blog.rugmunch.io),
       compact version to Telegram @cryptorugmuncher.

Grounding rules (methodology §3, non-negotiable):
    - Every metric cites a source or is omitted. No invented prices/mcap/%.
      Missing values render as "N/A" — never guessed.
    - Addresses on every movers / rug-watch token so a reader can /scan.
    - Zero hype.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

COINGECKO_API = "https://api.coingecko.com/api/v3"

_BRIEFING_TITLE = "Daily Rug Report"

# Logical Telegram channels for the briefing micro-post (methodology §7):
# the @cryptorugmuncher channel first, RMI Crypto News as fallback. Resolved
# via ``telegram_transport.CHANNEL_IDS`` (env ``RMI_MAIN_CHANNEL_ID`` /
# ``RMI_CRYPTONEWS_CHANNEL_ID``); when neither is configured the step is
# skipped and logged — it never raises.
BRIEFING_TELEGRAM_CHANNELS = ("main_channel", "rmicryptonews")

# Telegram soft limit is 4096 chars; keep the compact briefing under it.
BRIEFING_TELEGRAM_LIMIT = 3900

# Token budget for the LLM briefing call. A Daily Rug Report is short prose,
# so 512 tokens is the default; operators can raise it with
# ``RMI_BRIEFING_MAX_TOKENS``.
DEFAULT_BRIEFING_MAX_TOKENS = 512


def _briefing_max_tokens() -> int:
    """Token budget for the briefing LLM call (env-overridable, default 512).

    Reads ``RMI_BRIEFING_MAX_TOKENS``; any missing/unparseable/non-positive
    value falls back to :data:`DEFAULT_BRIEFING_MAX_TOKENS`.
    """
    raw = os.getenv("RMI_BRIEFING_MAX_TOKENS")
    if raw is None:
        return DEFAULT_BRIEFING_MAX_TOKENS
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return DEFAULT_BRIEFING_MAX_TOKENS
    return max(1, value)


# System prompt for the LLM writer (methodology §3 skeleton + §4 voice +
# grounding rules). Mirrors the RMI Terminal grounding style (chat.py) with
# the briefing's non-negotiable gates: cite-or-omit, zero hype, addresses on
# every mover, exact skeleton order, "N/A" for empty sections.
BRIEFING_SYSTEM_PROMPT = """You are the Daily Rug Report writer — a forensic-grade crypto analyst at Rug Munch Intelligence.

Voice (from the RMI brand guide — non-negotiable):
- Authoritative, not arrogant. Sharp and precise. Evidence-first.
- Zero hype: no "moon", no "don't miss", no "potential 100x". Say what IS.
- Crypto-native, but use GM/WAGMI sparingly. Witty but measured.
- Write concise, readable prose — fresh copy, not templated filler.

Grounding rules (hard gates — never violated):
- Every metric cites a source or is omitted. Never invent prices, market caps, percentages, volumes, or flows.
- If a number is not present in the provided context, write "N/A" — never guess.
- Include the on-chain address on every movers / rug-watch token so a reader can /scan instantly.
- Rug & Scam Watch names the mechanism (honeypot / mintable / LP pulled), not just the name.

Format:
- Follow the skeleton section order exactly — do not reorder, rename, drop, or add sections.
- Fill each section with prose built ONLY from the provided context.
- If a section has no context data, write "N/A".
- Paragraphs: one idea per paragraph, separated by a blank line. Never run
  multiple ideas into a wall of text. Keep paragraphs to 1-3 sentences.
- Links: embed source URLs inline as markdown links on the relevant phrase
  (e.g. `[CoinDesk](https://...`) — never dump raw URLs on their own line.
  One link per item is enough; drop duplicate URLs.
- Addresses: put the on-chain address in backticks right after the token name
  so a reader can /scan it instantly — do not paste full explorer URLs.
- Keep the whole brief tight: aim for 5-8 short paragraphs total, not a
  sprawling essay. Telegram posts read best when every line earns its place.
"""

# §3 fixed skeleton — section names in render order.
SECTIONS = [
    "Market Snapshot",
    "Top Stories",
    "Movers & Trending",
    "Rug & Scam Watch",
    "On-Chain Notable",
    "The Take",
]

# RAG collection -> briefing block mapping (methodology §5).
_RAG_BLOCKS: list[tuple[str, str, list[str], int]] = [
    ("top_stories", "top crypto market news today", ["news_v2"], 5),
    ("movers", "token trending price movers 24h volume", ["news_v2", "market_intel"], 5),
    (
        "rug_watch",
        "rug pull honeypot scam exploit last 24h",
        ["known_scams", "threat_intel", "defi_exploits", "scam_patterns"],
        5,
    ),
    ("whale_flows", "whale transfer cex flow smart money", ["whale_transfers"], 5),
]

_EVM_ADDR_RE = re.compile(r"0x[a-fA-F0-9]{40}")
_BASE58_ADDR_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

_MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)([KMB]?)\b", re.IGNORECASE)
_PCT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")

_URL_RE = re.compile(r"https?://\S+")


# ── Pure helpers ────────────────────────────────────────────────────────────


def _fmt_price(value: Any) -> str:
    """Format a USD price; missing/zero → 'N/A'. Never invents a price."""
    if value is None or value == "" or value == 0:
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_pct(value: Any) -> str:
    """Format a 24h % change with ▲/▼ arrows; missing → 'N/A'."""
    if value is None or value == "":
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if v > 0:
        return f"▲{v:.1f}%"
    if v < 0:
        return f"▼{abs(v):.1f}%"
    return "0.0%"


def _fmt_usd(value: Any) -> str:
    """Compact USD amount with K/M/B suffix; missing → 'N/A'."""
    if value is None or value == "" or value == 0:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if abs(v) >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


def assemble_market_snapshot(market: dict[str, Any]) -> str:
    """Render the BTC/ETH/SOL price + 24h change line from a market dict.

    The market dict uses the ``chat._fetch_market_context`` keys
    (``btc_price`` / ``btc_change_24h`` / …). Missing or zero values render
    as "N/A" — nothing is ever invented.
    """
    return (
        f"BTC {_fmt_price(market.get('btc_price'))} ({_fmt_pct(market.get('btc_change_24h'))}) · "
        f"ETH {_fmt_price(market.get('eth_price'))} ({_fmt_pct(market.get('eth_change_24h'))}) · "
        f"SOL {_fmt_price(market.get('sol_price'))} ({_fmt_pct(market.get('sol_change_24h'))})"
    )


def _extract_address(text: str) -> str:
    """First token address (EVM 0x…40 or Solana base58 32-44) in text, else ''."""
    if not text:
        return ""
    m = _EVM_ADDR_RE.search(text)
    if m:
        return m.group(0)
    m = _BASE58_ADDR_RE.search(text)
    if m:
        return m.group(0)
    return ""


def _hit_to_item(hit: dict[str, Any], fallback_title: str) -> dict[str, Any]:
    """Convert a RAG hit dict (compat ``three_pillar_search`` shape) to a briefing item."""
    metadata = hit.get("metadata") or {}
    content = (hit.get("content") or hit.get("text") or "").strip()
    title = (
        metadata.get("title") or metadata.get("name") or metadata.get("symbol") or fallback_title
    )
    url = (
        metadata.get("url")
        or metadata.get("link")
        or metadata.get("source_url")
        or metadata.get("source")
        or ""
    )
    source = metadata.get("outlet") or metadata.get("publisher") or metadata.get("source") or ""
    if not url and source.startswith("http"):
        url, source = source, ""
    detail = content[:400]
    return {
        "title": str(title)[:120],
        "detail": detail,
        "source": str(source)[:80],
        "url": str(url),
        "address": str(metadata.get("address") or _extract_address(content))[:64],
    }


# ── Gather (async, best-effort, never raises) ──────────────────────────────


async def _fetch_market_data() -> dict[str, Any]:
    """CoinGecko BTC/ETH/SOL snapshot — mirrors ``chat._fetch_market_context``."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{COINGECKO_API}/simple/price",
                params={
                    "ids": "bitcoin,ethereum,solana",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                    "include_24hr_vol": "true",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "btc_price": data.get("bitcoin", {}).get("usd"),
                    "btc_change_24h": data.get("bitcoin", {}).get("usd_24h_change"),
                    "eth_price": data.get("ethereum", {}).get("usd"),
                    "eth_change_24h": data.get("ethereum", {}).get("usd_24h_change"),
                    "sol_price": data.get("solana", {}).get("usd"),
                    "sol_change_24h": data.get("solana", {}).get("usd_24h_change"),
                }
    except Exception as e:  # fail-open — market block is optional
        logger.debug("briefing_market_fetch_failed err=%s", str(e)[:120])
    return {}


async def _fetch_rag_block(query: str, collections: list[str], limit: int) -> list[dict[str, Any]]:
    """One RAG block via ``three_pillar_search``; returns [] on any failure."""
    try:
        from rugmunchbot._vendor.rag_pkg.compat import three_pillar_search

        result = await three_pillar_search(
            query=query,
            collections=collections,
            limit=limit,
            min_similarity=0.3,
        )
        return [_hit_to_item(h, collections[0]) for h in result.get("results", [])[:limit]]
    except Exception as e:  # fail-open — RAG is an enhancement, never a gate
        logger.debug("briefing_rag_block_failed colls=%s err=%s", collections, str(e)[:120])
        return []


async def _rerank_stories(
    items: list[dict[str, Any]],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Rerank top stories by importance using the Qwen3 reranker.

    Uses the ``content_digest`` instruction template to prioritize: unusual
    whale activity, imminent exchange risks, liquidation cascades, and DeFi
    protocol anomalies over routine price movements. Falls back to original
    order when the reranker is unavailable.
    """
    if not items:
        return items
    try:
        from rugbrain.reranker import rerank_instructed

        docs = [f"{it.get('title', '')} — {it.get('detail', '')[:200]}" for it in items]
        results = await rerank_instructed(
            "content_digest", "top crypto market news today", docs, top_k=top_k
        )
        if not results:
            return items[:top_k]
        reranked: list[dict[str, Any]] = []
        for r in results:
            idx = r["index"]
            if idx < len(items):
                reranked.append(items[idx])
        return reranked[:top_k]
    except Exception as e:
        logger.debug("briefing_rerank_failed err=%s", str(e)[:120])
        return items[:top_k]


async def gather_briefing_context() -> dict[str, Any]:
    """Pull every §5 data block into one structured context dict.

    Returns a dict with keys ``market`` (raw CoinGecko dict) and one list per
    RAG block (``top_stories``, ``movers``, ``rug_watch``, ``whale_flows``),
    each element ``{title, detail, source, url, address}``. Top stories are
    reranked by the Qwen3 reranker for importance. Empty on failure — this
    function never raises.
    """
    context: dict[str, Any] = {
        "market": await _fetch_market_data(),
        "top_stories": [],
        "movers": [],
        "rug_watch": [],
        "whale_flows": [],
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    for key, query, collections, limit in _RAG_BLOCKS:
        context[key] = await _fetch_rag_block(query, collections, limit)
    # Rerank top stories by importance using the Qwen3 reranker
    if context["top_stories"]:
        context["top_stories"] = await _rerank_stories(context["top_stories"], top_k=5)
    return context


# ── Draft ───────────────────────────────────────────────────────────────────

WriterCallable = Callable[[dict[str, Any], dict[str, Any]], Awaitable[str]]

CompletionCallable = Callable[..., Awaitable[dict[str, Any]]]


def _llm_writer_enabled() -> bool:
    """Whether ``draft_briefing``'s default writer is the LLM writer.

    Production (``ENVIRONMENT=prod``) uses ``llm_writer`` by default so the
    Morning Brief is drafted by the remote LLM. Dev/test keep the deterministic
    template (pure, fast, deterministic) unless explicitly overridden with
    ``RMI_BRIEFING_LLM_WRITER=1``. Explicit ``0``/``false``/``no``/``off``
    always disables the LLM default.
    """
    override = os.getenv("RMI_BRIEFING_LLM_WRITER")
    if override is not None:
        return override.strip().lower() not in ("0", "false", "no", "off")
    return os.getenv("ENVIRONMENT", "dev").strip().lower() in ("prod", "production")


async def llm_writer(
    context: dict[str, Any],
    skeleton: dict[str, Any],
    *,
    model: str | None = None,
    completion: CompletionCallable | None = None,
) -> str:
    """Draft the briefing prose with a remote LLM (methodology §6).

    Builds a system prompt with the §3 skeleton, §4 voice, and grounding
    rules, then asks ``completion`` (default ``ai_router.chat_completion`` →
    remote long/deep tier) to write the post filling the skeleton using ONLY
    the provided context. Returns "" on any failure (completion raises,
    non-dict result, empty content) so ``draft_briefing`` falls back to the
    deterministic template — publishing is never blocked on the LLM.

    ``completion`` is injectable so tests can assert on the messages without
    hitting the network. ``max_tokens`` is capped at :func:`_briefing_max_tokens`
    (default 512, env ``RMI_BRIEFING_MAX_TOKENS``) so the call stays inside
    the provider timeout.
    """
    completion_call: CompletionCallable
    if completion is None:
        from rugmunchbot.llm_config import chat_completion as _chat_completion

        completion_call = _chat_completion
    else:
        completion_call = completion

    user_prompt = (
        "# Morning Brief skeleton — fill this, in this exact order\n"
        f"{json.dumps(skeleton, indent=2, default=str, ensure_ascii=False)}\n\n"
        "# Gathered context — the ONLY allowed facts (cite-or-omit; missing = N/A)\n"
        f"{json.dumps(context, indent=2, default=str, ensure_ascii=False)}"
    )
    try:
        result = await completion_call(
            messages=[
                {"role": "system", "content": BRIEFING_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            model=model,  # None → resolved by task_type="analysis" → deep tier
            task_type="analysis",
            temperature=0.6,
            max_tokens=_briefing_max_tokens(),
        )
    except Exception as e:  # fail-open: never block publishing on the LLM
        logger.warning("briefing_llm_writer_failed err=%s", str(e)[:160])
        return ""
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    return content.strip() if isinstance(content, str) else ""


def _render_section_lines(name: str, items: list[dict[str, Any]], bullet: str = "•") -> list[str]:
    """Render one context block into display lines (used by the fallback)."""
    if name == "Top Stories":
        return [
            f"{i + 1}. {it['title']} — {it['detail']}"
            + (f" — {it['source']} {it['url']}" if it.get("url") else "")
            for i, it in enumerate(items)
        ]
    lines: list[str] = []
    for it in items:
        prefix = f"{bullet} {it['title']}"
        if name in ("Movers & Trending", "Rug & Scam Watch"):
            prefix += f" — {it.get('address') or 'N/A'}"
        suffix = f" — {it['source']} {it['url']}" if it.get("url") else ""
        lines.append(f"{prefix} — {it['detail']}{suffix}")
    return lines


def _build_skeleton(context: dict[str, Any]) -> dict[str, Any]:
    """Structured §3 skeleton: sections keyed by name, each a list of lines or None."""
    market = context.get("market") or {}
    sections: dict[str, list[str] | None] = {}
    block_keys = {
        "top_stories": "Top Stories",
        "movers": "Movers & Trending",
        "rug_watch": "Rug & Scam Watch",
        "whale_flows": "On-Chain Notable",
    }
    for key, name in block_keys.items():
        items = context.get(key) or []
        sections[name] = _render_section_lines(name, items) if items else None

    # The Take — deterministic 1-2 sentence grounded summary (never invented).
    take_items: list[str] = []
    btc = market.get("btc_change_24h")
    eth = market.get("eth_change_24h")
    sol = market.get("sol_change_24h")
    if btc is not None or eth is not None or sol is not None:
        bits = []
        if btc is not None:
            bits.append(f"BTC {_fmt_pct(btc)}")
        if eth is not None:
            bits.append(f"ETH {_fmt_pct(eth)}")
        if sol is not None:
            bits.append(f"SOL {_fmt_pct(sol)}")
        take_items.append(f"Markets are mixed: {' · '.join(bits)} vs 24h ago.")
    stories = context.get("top_stories") or []
    if stories:
        take_items.append(f"Top narrative: {stories[0]['title']}.")
    sections["The Take"] = take_items if take_items else None

    return {
        "title": f"{_BRIEFING_TITLE} — {datetime.now(UTC).strftime('%A, %B %d')}",
        "market_snapshot": assemble_market_snapshot(market),
        "sections": sections,
    }


def _render_fallback(skeleton: dict[str, Any]) -> str:
    """Deterministic markdown post from the skeleton — real, cited, N/A-safe."""
    lines = [
        f"🔍 {skeleton['title']}",
        "──────────────────────────",
        "📊 Market Snapshot",
        skeleton["market_snapshot"],
    ]
    for name in SECTIONS[1:]:
        lines.append("")
        icon = {
            "Top Stories": "🗞️",
            "Movers & Trending": "🔥",
            "Rug & Scam Watch": "🚨",
            "On-Chain Notable": "🐋",
            "The Take": "🧠",
        }[name]
        lines.append(f"{icon} {name}")
        section_lines = skeleton["sections"].get(name)
        if section_lines:
            lines.extend(section_lines)
        else:
            lines.append("N/A")
    lines.extend(
        [
            "",
            "──────────────────────────",
            "📋 Daily Rug Report · rugmunch.io · @cryptorugmunch",
        ]
    )
    return "\n".join(lines)


async def draft_briefing(context: dict[str, Any], *, writer: WriterCallable | None = None) -> str:
    """Assemble the §3 briefing from ``context``.

    ``writer`` is an injectable async callable ``(context, skeleton) -> str``
    (e.g. the LLM writer from methodology §6). When omitted, the default is
    ``llm_writer`` in production (``ENVIRONMENT=prod``, or explicitly
    ``RMI_BRIEFING_LLM_WRITER=1``) and the deterministic template otherwise.
    Whenever the active writer raises or returns empty — or no writer is
    active — the deterministic template fallback renders a real, source-cited
    post with "N/A" for empty sections: publishing is never blocked on the LLM.
    """
    skeleton = _build_skeleton(context)
    if writer is None and _llm_writer_enabled():
        writer = llm_writer
    if writer is not None:
        try:
            prose = await writer(context, skeleton)
            if isinstance(prose, str) and prose.strip():
                return prose
            logger.debug("briefing_writer_returned_empty — using fallback")
        except Exception as e:  # fail-open: never block publishing on the LLM
            logger.warning("briefing_writer_failed — using fallback err=%s", str(e)[:160])
    return _render_fallback(skeleton)


# ── Review (pure fact-check) ────────────────────────────────────────────────


def _numbers_in(text: str) -> set[float]:
    """Normalized numeric values (money + percent) found in ``text``.

    Values are rounded to one decimal place — the coarsest precision the
    briefing display formats at (percentages render as ``▲0.5%``) — so a
    displayed value always matches the raw context value it came from
    (e.g. raw ``0.5051345496638839`` → displayed ``▲0.5%`` → ``0.5``).
    """
    values: set[float] = set()
    for m in _MONEY_RE.finditer(text):
        raw, suffix = m.group(1), m.group(2)
        with suppress(ValueError):
            values.add(
                round(
                    float(raw.replace(",", ""))
                    * {"K": 1e3, "M": 1e6, "B": 1e9}.get(suffix.upper(), 1.0),
                    1,
                )
            )
    for m in _PCT_RE.finditer(text):
        with suppress(ValueError):
            values.add(round(float(m.group(1)), 1))
    return values


def _context_grounding(context: dict[str, Any]) -> set[float]:
    """Every numeric value present anywhere in the context (the grounding set).

    Includes bare decimal numbers (e.g. market prices stored as floats or
    numeric strings — CoinGecko returns ``usd_24h_change`` as a string) plus
    money/percent values inside free-text fields. All values are normalized
    to one decimal place, matching the display precision of the draft, so a
    correctly-formatted metric is never flagged. This set is only ever used
    as the *allowed* set — it is deliberately generous.
    """
    texts: list[str] = []
    market = context.get("market") or {}
    for v in market.values():
        if v is None or v == "":
            continue
        texts.append(str(v))
    for key in ("top_stories", "movers", "rug_watch", "whale_flows"):
        for item in context.get(key) or []:
            if isinstance(item, dict):
                texts.extend(str(v) for v in item.values() if v is not None)
    if not texts:
        return set()
    grounded: set[float] = set()
    for t in texts:
        grounded |= _numbers_in(t)
        for m in re.finditer(r"\d+(?:\.\d+)?", t):
            with suppress(ValueError):
                grounded.add(round(float(m.group(0)), 1))
    return grounded


def review_briefing(draft: str, context: dict[str, Any]) -> dict[str, Any]:
    """Pure fact-check of a briefing draft against the gathered context.

    Flags:
      - ``hallucination`` — a number in the draft (USD amount or %) that does
        not appear anywhere in the context. "N/A" lines carry no numbers, so
        they are never flagged.
      - ``unsourced`` — a metric-bearing claim bullet (story/mover/rug/whale)
        that has no source reference (URL or token address) in the line.

    Returns ``{"ok": bool, "flags": [{"type", "line", "metric"}]}``.
    """
    flags: list[dict[str, Any]] = []
    grounding = _context_grounding(context)
    for line in draft.splitlines():
        numbers = _numbers_in(line)
        if not numbers:
            continue
        for value in numbers:
            if value not in grounding:
                flags.append({"type": "hallucination", "line": line[:200], "metric": value})
        # Source-presence: metric-bearing claim bullets need a URL or address.
        is_claim_bullet = line.lstrip().startswith(
            ("•", "- ", "* ", "1. ", "2. ", "3. ", "4. ", "5. ")
        )
        if is_claim_bullet and not (_URL_RE.search(line) or _extract_address(line)):
            flags.append({"type": "unsourced", "line": line[:200], "metric": None})
    return {"ok": not flags, "flags": flags}


# ── Publish ─────────────────────────────────────────────────────────────────


async def publish_briefing(
    draft: str,
    *,
    now: datetime | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Publish the briefing via Ghost ``create_post`` (post_type="briefing").

    The Ghost post is titled ``"Daily Rug Report — {date}"`` (methodology §2),
    the drafted body is the post content, and ``tags`` (default ``["briefing"]``)
    are attached on top of the auto-added ``briefing`` tag. ``now`` is
    injectable for deterministic titles (tests / scheduled runs).

    Returns the Ghost post ``{id, url, ...}`` on success or an
    ``{"error": ...}`` dict on failure — it never raises (``create_post`` is
    already fail-open; the session cookie or network may be unavailable).
    """
    from rugmunchbot._vendor.content_platform import create_post

    stamp = (now or datetime.now(UTC)).strftime("%A, %B %d, %Y")
    return await create_post(
        content=draft,
        post_type="briefing",
        title=f"{_BRIEFING_TITLE} — {stamp}",
        tags=tags or ["briefing"],
    )


def _compact_briefing(draft: str, *, limit: int = BRIEFING_TELEGRAM_LIMIT) -> str:
    """Compact the briefing for Telegram: head + "The Take" + footer.

    The full draft can exceed Telegram's 4096-char soft limit. The compact
    version keeps the opening sections (Market Snapshot / movers / rug watch),
    the "The Take" verdict, and the bot footer — dropping the middle stories.
    Short drafts pass through unchanged.
    """
    text = (draft or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head_limit = int(limit * 0.7)
    head = text[:head_limit].rstrip()
    take = ""
    for marker in ("🧠 The Take", "The Take"):
        idx = text.rfind(marker)
        if idx > len(head):
            take = text[idx:].strip()
            break
    footer = "📋 Daily Rug Report · rugmunch.io · @cryptorugmunch"
    if take:
        return f"{head}\n\n…\n\n{take}\n\n{footer}"[:limit]
    return f"{head}\n\n…\n\n{footer}"[:limit]


async def publish_briefing_telegram(
    draft: str,
    *,
    client: Any | None = None,
    channel_ids: dict[str, int | str | None] | None = None,
) -> dict[str, Any]:
    """Post a compact briefing to the news/main Telegram channel (optional).

    Resolves the target chat id from ``channel_ids`` (default: the transport's
    ``CHANNEL_IDS``, env-driven): ``main_channel`` first, ``rmicryptonews`` as
    fallback. When neither channel is configured the step is skipped and
    logged — this function never raises. ``client`` is injectable (the
    headless client) so tests can capture the send without network.

    Returns ``{"sent", "skipped", "errors"}`` (plus ``reason="no_chat_id"``
    when skipped).
    """
    if channel_ids is None:
        from rugmunchbot._vendor.telegram_transport import CHANNEL_IDS

        channel_ids = CHANNEL_IDS
    chat_id = next(
        (channel_ids.get(c) for c in BRIEFING_TELEGRAM_CHANNELS if channel_ids.get(c) is not None),
        None,
    )
    if chat_id is None:
        logger.warning(
            "briefing_telegram_skipped reason=no_chat_id channels=%s",
            list(BRIEFING_TELEGRAM_CHANNELS),
        )
        return {"sent": 0, "skipped": 1, "errors": [], "reason": "no_chat_id"}
    try:
        transport: Any
        if client is None:
            from rugmunchbot._vendor.headless import get_headless_client

            transport = await get_headless_client()
        else:
            transport = client
        ok = await transport.send_message(chat_id, _compact_briefing(draft), parse_mode=None)
    except Exception as e:  # fail-open: a broken transport never crashes the run
        logger.warning("briefing_telegram_send_failed err=%s", str(e)[:160])
        return {"sent": 0, "skipped": 0, "errors": [str(e)[:200]]}
    if ok:
        return {"sent": 1, "skipped": 0, "errors": []}
    logger.warning("briefing_telegram_send_returned_false chat_id=%s", chat_id)
    return {"sent": 0, "skipped": 0, "errors": ["send_message returned False"]}
