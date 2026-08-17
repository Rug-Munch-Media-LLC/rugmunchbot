"""LLM clients — untrusted-context delimiters + source dedupe/rank/render.

All retrieved/external context is UNTRUSTED DATA: news articles / exploit
PoCs can embed instructions. Each block is wrapped in explicit
``<untrusted_context>`` delimiters; sources are de-duplicated by
normalized domain, ranked primary-first and rendered as plain text
``title — url`` so no markdown link ever leaks through the delivery
pipeline.
"""

from __future__ import annotations

import re


# ── Untrusted-context delimiters (indirect prompt-injection hardening) ─────
# All retrieved/external context — RAG hits, bridge market blocks, scan
# summaries, live tool data, real-time digests — is UNTRUSTED DATA: news
# articles / exploit PoCs can embed instructions that try to steer the model
# or the user. Each block is wrapped in explicit delimiters so the system
# prompts can tell the model to treat it strictly as reference data.
def _untrusted_block(block: str) -> str:
    return f"\n<untrusted_context>\n{block}\n</untrusted_context>\n"


# ── Source quality: dedupe + rank + plain-text citation rendering ──────────
# Primary / high-signal domains surface FIRST in the "Sources:" block —
# official docs, block explorers, major outlets and data aggregators beat
# random blogs and aggregator mirrors. Matching is substring-based on the
# normalized host so subdomains (docs.ethereum.org, explorer.solana.com)
# rank too.
_PRIMARY_SOURCE_MARKERS = (
    # block explorers / chain-native explorers
    "etherscan",
    "bscscan",
    "polygonscan",
    "basescan",
    "arbiscan",
    "solscan",
    "solana.fm",
    "explorer.",
    # official project docs / standards
    "ethereum.org",
    "bitcoin.org",
    "docs.",
    "github.com",
    # major crypto/media outlets + wire services
    "coindesk",
    "cointelegraph",
    "theblock",
    "decrypt",
    "blockworks",
    "reuters",
    "bloomberg",
    "forbes",
    "ft.com",
    "wsj",
    "apnews",
    "theguardian",
    "nytimes",
    # data aggregators / analytics
    "defillama",
    "coingecko",
    "coinmarketcap",
    "dexscreener",
    "messari",
)
# Markdown link syntax stripped from untrusted source titles / model answers
# so the final Telegram message can never show a literal ``[title](url)``
# leak (the delivery pipeline has no link pass). The inner text is kept.
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


def _normalize_source_domain(url: str) -> str:
    """Normalize a source URL to its bare domain (dedupe key).

    ``https://www.etherscan.io/token/0x…?a=1`` → ``etherscan.io``. Empty when
    the source carries no URL (it renders as a plain title, never deduped).
    """
    u = url.strip()
    if not u:
        return ""
    for prefix in ("https://", "http://", "//"):
        if u.startswith(prefix):
            u = u[len(prefix) :]
    u = u.split("/")[0].split("?")[0].split("#")[0].lower()
    if u.startswith("www."):
        u = u[4:]
    return u


def _source_is_primary(url: str) -> bool:
    """True when the source's domain is a primary/high-signal surface."""
    host = _normalize_source_domain(url)
    return bool(host and any(m in host for m in _PRIMARY_SOURCE_MARKERS))


# ── Source identity: dedupe + display name + ours-vs-external classification ──
# Shared by the message-level "Sources:" block (:func:`_append_source_block`)
# and the "⚡ Grounded in N sources" footer (``_delivery._send_answer``) so
# both surfaces agree on what a "distinct source" is: URL-bearing sources are
# de-duplicated by normalized domain (3 hits from coingecko.com == ONE source),
# and a source with no title/query renders by its bare domain — a real name,
# never a fabricated label.
_SOURCE_TYPE_LABELS = {
    "rag": "RMI knowledge base",
    "scanner": "RMI scanner",
    "market": "RMI market digest",
    "backend": "RMI knowledge base",
    "web": "web search",
}
# Source types that are OUR OWN intel (ClickHouse-backed indexer data, the
# scanner, the RAG knowledge base, the daily market digest, the backend tier-1
# RAG) — everything else is an external provider (DexScreener, CoinGecko, …).
# Labels are RMI-branded: raw internal keys (/api/v1/..., backend:/bridge:)
# never reach the footer or the answer body.
_OUR_SOURCE_TYPES = ("rag", "scanner", "market", "backend")


def _dedupe_sources(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    """De-duplicate sources by normalized domain (URL-bearing only — a source
    without a URL is never deduped). First occurrence per domain wins."""
    seen_domains: set[str] = set()
    deduped: list[dict[str, str]] = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        url = str(s.get("url") or "").strip()
        domain = _normalize_source_domain(url) if url else ""
        if domain and domain in seen_domains:
            continue
        if domain:
            seen_domains.add(domain)
        deduped.append(s)
    return deduped


def _source_display_name(s: dict[str, str]) -> str:
    """Best real display name for a source: title, else query, else bare domain.

    A URL-only source (no title/query) renders as its normalized bare domain —
    real, never fabricated, never a dead-link label. Markdown/link syntax is
    stripped so the escape → ``_markdown_to_html`` pipeline never mis-renders.
    """
    raw = str(s.get("title") or s.get("query") or "").strip()
    if raw:
        name = _MD_LINK_RE.sub(r"\1", raw)
        name = re.sub(r"[*_`\[\]]", "", name).strip()
        name = _scrub_internal_api_refs(name)
        return name[:80]
    url = str(s.get("url") or "").strip()
    if url:
        domain = _normalize_source_domain(url)
        if domain:
            return domain[:60]
    return ""


def _source_is_ours(s: dict[str, str]) -> bool:
    """True when the source is OUR OWN intel (RAG KB, scanner, market digest,
    backend RAG) rather than an external provider (DexScreener, CoinGecko, web).
    Ours sources render RMI-branded in the grounding footer."""
    stype = str(s.get("type") or "").strip().lower()
    return stype in _OUR_SOURCE_TYPES


def _source_type_label(stype: str) -> str:
    """Friendly label for a bare source type (``rag`` → "RMI knowledge
    base"). Only OUR source types and ``web`` have dedicated labels; unknown
    types render as "RMI data" so internal buckets never go unnamed."""
    return _SOURCE_TYPE_LABELS.get(stype, "RMI data")


# ── Internal source keys → professional RMI brand labels ──────────────────
# Raw internal API-surface keys (``backend:/api/v1/market/trending``,
# ``bridge:/chat/context``) must never reach the community footer or the
# answer body — they leak internal topology. Every internal key maps to a
# clean RMI brand label; unknown keys fall back to the caller's tool-name
# label (never a raw path).
_INTERNAL_KEY_LABELS = (
    ("backend:/api/v1/market/trending", "RMI market intel"),
    ("backend:/api/v1/news/trending", "RMI news intel"),
    ("backend:/api/v1/databus/market/prices", "RMI market intel"),
    ("backend:/api/v1/market/prices", "RMI market intel"),
    ("backend:/api/v1/databus/token", "RMI token data"),
    ("backend:/api/v2/scanner/token/scan", "RMI scanner"),
    ("backend:/api/v1/bot/scan", "RMI scanner"),
    ("backend:/api/v1/databus/wallet", "RMI wallet analytics"),
    ("backend:/api/v1/rugcharts", "RMI charts"),
    ("bridge:/chat/context", "RMI knowledge base"),
)

# Internal scheme prefixes + leftover /api/vN fragments stripped from
# model-emitted answer text (defense-in-depth for the answer body).
_INTERNAL_SCHEME_RE = re.compile(r"\b(?:backend|bridge):")
_INTERNAL_API_PATH_RE = re.compile(r"/api/v\d+[A-Za-z0-9_./{}:\-+]*")


def _internal_source_label(src: str) -> str | None:
    """Professional RMI label for an internal source key (``backend:/…``).

    Returns ``None`` when the key is unknown — the caller falls back to the
    tool-name label. Used by the grounding footer so raw internal API paths
    never reach the message.
    """
    s = (src or "").strip().lower()
    for key, label in _INTERNAL_KEY_LABELS:
        if s.startswith(key):
            return label
    return None


def _scrub_internal_api_refs(text: str) -> str:
    """Rename/strip internal API-surface tokens from an answer before send.

    The model occasionally echoes the raw grounding source key it was fed
    ("source: backend:/api/v1/market/trending") — internal API topology
    that must never reach the community. Known internal keys are renamed to
    their RMI brand label; any leftover ``backend:``/``bridge:`` scheme
    prefixes and bare ``/api/vN/...`` fragments are removed entirely.
    Idempotent; safe on plain text.
    """
    if not text:
        return text
    out = text
    for key, label in _INTERNAL_KEY_LABELS:
        out = out.replace(key, label)
    out = _INTERNAL_SCHEME_RE.sub("", out)
    out = _INTERNAL_API_PATH_RE.sub("", out)
    return out


# A model sometimes emits its OWN "Sources:" footer INLINE (e.g. after item
# 5 of a numbered list) instead of leaving the footer to the delivery layer.
# The ONE real footer is appended at the very end by _append_source_block, so
# everything from the first model-emitted "Sources:" header line onward is
# dropped — the answer body keeps only its items, and exactly one sources
# footer lands at the end.
_INLINE_SOURCES_RE = re.compile("(?ism)^[ \t]*(?:📚[ \t]*)?[Ss]ources?:.*")


def _strip_inline_sources(answer: str) -> str:
    """Remove a model-emitted inline "Sources:" footer from the answer body.

    The model occasionally writes its own "📚 Sources:" block mid-answer (the
    reported bug: it landed between list items 5 and 6 of a trending answer),
    duplicating the delivery footer. The canonical footer is appended at the
    very END of the message, so drop everything from the first model-emitted
    "Sources:" header line onward (plain-text content included). No-op when
    the model did the right thing.
    """
    if not answer:
        return answer
    return _INLINE_SOURCES_RE.sub("", answer)


def _links_to_plain_text(text: str) -> str:
    """Convert markdown ``[label](url)`` links to plain ``label — url``.

    The Telegram delivery pipeline (escape → ``_markdown_to_html``) has no
    link pass, so a model-emitted ``[title](url)`` would render literally.
    Converting to plain text keeps the URL tappable (Telegram auto-linkifies
    bare URLs) without markdown leakage. Applied to every answer path
    (backend + both remote tiers) before the source block is appended.
    """
    return _MD_LINK_RE.sub(lambda m: f"{m.group(1).strip()} \u2014 {m.group(2).strip()}", text)


def _append_source_block(
    answer: str,
    sources: list[dict[str, str]] | None,
    limit: int = 5,
) -> str:
    """Append a compact "Sources:" block to a remote reply.

    Surfaces up to ``limit`` (default 5) grounding sources as PLAIN TEXT
    (``title — url``), de-duplicated by normalized domain (one entry per
    site) and ranked so primary/high-signal surfaces (official docs,
    block explorers, major outlets) render FIRST — fallback answers cite
    where the intel came from. Plain text,
    NOT markdown links (``[title](url)``) and NOT raw ``<a href>`` HTML:
    both render literally in the final message because ``_send_answer``
    HTML-escapes every chunk and ``_markdown_to_html`` has no link pass
    (the code-review note on ``[title](url)`` showing verbatim). A bare URL
    is auto-linkified by Telegram even in HTML parse mode, so the source
    stays clickable. ``sources`` may carry ``title``/``url``, or the
    bridge's ``type``+``query`` metadata — the query doubles as the title
    when no real title exists, and a source WITHOUT a ``url`` renders as
    plain text (never a fabricated/dead link). Titles are NOT pre-escaped
    here (``_send_answer``'s ``html.escape`` is the single escape layer for
    this untrusted bridge metadata — pre-escaping would double-escape
    ``&``, same pattern as the grounding footer in ``_delivery``); markdown
    emphasis markers are stripped from titles so the escape →
    ``_markdown_to_html`` delivery pipeline never mis-renders them. Returns
    the answer unchanged when ``sources`` is empty/None.
    """
    # A model-emitted inline "Sources:" footer (mid-list) is stripped first —
    # the ONE real footer is appended at the end below (or the answer body
    # ends clean when there are no real sources).
    answer = _strip_inline_sources(answer)
    if not sources:
        return answer
    # De-duplicate by normalized domain (URL-bearing sources only — a source
    # without a URL renders as a plain title and is never deduped). The first
    # occurrence per domain wins — shared with the grounding footer so both
    # surfaces count the same DISTINCT sources.
    deduped = _dedupe_sources(sources)
    # Rank: primary/high-signal domains first, stable within each group.
    deduped.sort(key=lambda s: 0 if _source_is_primary(str(s.get("url") or "")) else 1)
    rendered: list[str] = []
    for s in deduped:
        if len(rendered) >= limit:
            break
        title = _source_display_name(s)
        if not title:
            continue
        url = str(s.get("url") or "").strip()[:200]
        if url:
            # URLs stay tappable: keep underscores (word-boundary guarded by
            # _markdown_to_html) but drop the rare `*`/backtick markers that
            # WOULD mis-render into bold/code spans.
            url = re.sub(r"[*`]", "", url).strip()
            rendered.append(f"{title} \u2014 {url}")
        else:
            rendered.append(title)
    if not rendered:
        return answer
    return f"{answer}\n\n\U0001f4da Sources: " + ", ".join(rendered)
