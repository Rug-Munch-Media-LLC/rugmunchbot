"""KOL Hall of Shame — report cards for scammy KOLs / shillers.

Track KOLs the way a bounty hunter would: every call they make on a token
(real X tweets via ApiPass), every tweet they later DELETE (retraction is
the strongest shame signal), their wallet address when attributed, and how
often the tokens they shill are flagged by the scanner. Roll it all into a
per-KOL **report card** with a letter grade, and surface the worst offenders
on the ALERTS channel (@rmialerts — high-signal doctrine) + the site
(JSONL export in the backend ``CallStore`` shape).

Pipeline
--------
1. ``run_kol_shame_watch`` sweeps the tracked KOL handles (curated shill/
   scam-adjacent X accounts + the bot's existing shill-watch channels) via
   the ApiPass X source (``tools.apipass_x``), bounded by the ≤3/hr burn.
2. Every tweet that looks like a call ($TICKER / address mention, buy/rug
   verbs) is recorded via ``db.kol.record_kol_call`` (upsert by tweet_id).
3. ``detect_deleted_calls`` — a tweet recorded last window that now 404s
   (ApiPass tweet-detail returns None) is marked deleted via
   ``db.kol.mark_kol_call_deleted``.
4. ``compute_report_card`` (pure) aggregates a handle's calls into a card;
   ``shame_score`` weights deletions >> shilling >> scam-token calls.
5. ``format_hall_of_shame_post`` renders the HTML card and the runner posts
   it to @rmialerts, deduped per day (a KOL enters the hall once a day max).

Pure + deterministic where possible: scoring/formatting are pure functions
(the store is injected); only the sweep/alert I/O is async. Fail-open at
every stage — no key, dead ApiPass, or empty sweep is a silent no-op.

Env knobs (all optional):
* ``RMI_KOL_SHAME_ENABLED``      — master switch (default true)
* ``RMI_KOL_TRACKED_HANDLES``    — comma-separated X handles to track
* ``RMI_KOL_SHAME_INTERVAL_MIN`` — job cadence (default 90)
* ``RMI_KOL_SHAME_MAX_ALERTS``   — max hall entries per tick (default 2)
* ``RMI_KOL_CALL_STORE_PATH``    — JSONL export path (site bridge; default
  ``data/kol_hall_of_shame.jsonl``)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.kol_hall_of_shame")

KOL_SHAME_ENABLED = os.getenv("RMI_KOL_SHAME_ENABLED", "true").lower() in ("1", "true", "yes")
KOL_SHAME_INTERVAL_MIN = int(os.getenv("RMI_KOL_SHAME_INTERVAL_MIN", "90"))
KOL_SHAME_MAX_ALERTS = int(os.getenv("RMI_KOL_SHAME_MAX_ALERTS", "2"))
#: Calls older than this are ignored for new-entry purposes (freshness gate).
FRESH_CALL_HOURS = int(os.getenv("RMI_KOL_SHAME_FRESH_HOURS", "72"))
#: Default tracked X handles — curated shill/scam-adjacent CT accounts
#: (mirrors rmi-backend's known_scammers + shill_watch Telegram set).
_DEFAULT_TRACKED_HANDLES: tuple[str, ...] = (
    "SahilArora0",  # serial celebrity-token launcher (US DOJ charged)
    "avi_eisen",  # Mango Markets oracle manipulation (convicted)
    "walloftrophies",  # KOL Trending / CallAnalyser
    "thepumpalert",  # scam call-out channel
    "kamocalls",  # Kamo | Daily trades
    "CryptoCapoTG",  # CryptoCapo — calls + analysis
    "shahlito",  # Shah's Journal — calls
    "cryptowhaletrades",  # Crypto Whale Trading — whale/calls
    "MustStopMurad",  # memecoin KOL — track for shill patterns
    "gabrielhaines",  # entertainment-adjacent token promotions
)
KOL_TRACKED_HANDLES: tuple[str, ...] = tuple(
    h.strip().lstrip("@")
    for h in os.getenv("RMI_KOL_TRACKED_HANDLES", ",".join(_DEFAULT_TRACKED_HANDLES)).split(",")
    if h.strip()
)
#: Call-language needles (cheap heuristic; the reranker refines downstream).
_CALL_VERB_RE = re.compile(r"\b(buy|bought|long|call|entered|entry|position)\b", re.I)
_RUG_VERB_RE = re.compile(r"\b(rug|dump|scam|ponzi|exit|rekt)\b", re.I)
_PUMP_RE = re.compile(r"\b(pump|gem|100x|moon|mooner|ape|all[- ]?in|breakout|alpha|launch)\b", re.I)
_TICKER_RE = re.compile(r"\$([A-Z0-9]{2,12})\b")
_ADDRESS_RE = re.compile(r"\b(0x[a-fA-F0-9]{6,}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")

#: Letter-grade bands for the report card (lower shame score = better).
_GRADE_BANDS: tuple[tuple[float, str], ...] = (
    (5.0, "A"),
    (15.0, "B"),
    (30.0, "C"),
    (50.0, "D"),
    (80.0, "F"),
)


# ══════════════════════════════════════════════════════════════════════════
# DATA MODEL
# ══════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class KolCall:
    """One captured KOL call on a token."""

    handle: str
    tweet_id: str
    token_symbol: str = ""
    token_address: str = ""
    chain: str = ""
    call_type: str = "buy"
    tweet_text: str = ""
    posted_at: str = ""
    source_url: str = ""
    wallet_address: str = ""
    is_deleted: bool = False
    deleted_at: str = ""
    captured_at: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> KolCall:
        return cls(
            handle=str(row.get("handle") or ""),
            tweet_id=str(row.get("tweet_id") or ""),
            token_symbol=str(row.get("token_symbol") or ""),
            token_address=str(row.get("token_address") or ""),
            chain=str(row.get("chain") or ""),
            call_type=str(row.get("call_type") or "buy"),
            tweet_text=str(row.get("tweet_text") or ""),
            posted_at=str(row.get("posted_at") or ""),
            source_url=str(row.get("source_url") or ""),
            wallet_address=str(row.get("wallet_address") or ""),
            is_deleted=bool(row.get("is_deleted") or 0),
            deleted_at=str(row.get("deleted_at") or ""),
            captured_at=str(row.get("captured_at") or ""),
        )


@dataclass(slots=True)
class KolReportCard:
    """Per-KOL aggregated report card."""

    handle: str
    total_calls: int = 0
    deleted_calls: int = 0
    shill_calls: int = 0
    scam_token_calls: int = 0
    tokens_mentioned: int = 0
    wallet_address: str = ""
    score: float = 0.0
    grade: str = "N/A"

    @property
    def in_hall_of_shame(self) -> bool:
        """True when the card is bad enough to warrant the hall (grade ≥ B)."""
        return self.score >= 15.0


# ══════════════════════════════════════════════════════════════════════════
# PURE MODEL — classification, aggregation, scoring
# ══════════════════════════════════════════════════════════════════════════


def classify_call(tweet_text: str) -> str:
    """Heuristic call type for one tweet: 'shill' | 'rug' | 'buy' | 'other'.

    A tweet is a call when it carries a call verb and/or a token mention.
    ``shill`` = promotional pump language (gem/100x/moon/ape/…) + a token
    mention; ``buy`` = a plain call with no hype framing; ``rug`` = the
    tweet itself warns of a rug/dump (a caution call, tracked separately).
    """
    text = str(tweet_text or "")
    if not text:
        return "other"
    if _RUG_VERB_RE.search(text):
        return "rug"
    has_token = bool(_TICKER_RE.search(text) or _ADDRESS_RE.search(text))
    if not has_token and not _CALL_VERB_RE.search(text):
        return "other"
    if has_token and _PUMP_RE.search(text):
        return "shill"
    return "buy"


def extract_tokens(tweet_text: str) -> tuple[str, str]:
    """(ticker, address) mentioned in a tweet ("" when absent)."""
    text = str(tweet_text or "")
    m = _TICKER_RE.search(text)
    ticker = m.group(1).upper() if m else ""
    a = _ADDRESS_RE.search(text)
    addr = a.group(1) if a else ""
    if addr and not addr.startswith("0x") and len(addr) >= 32:
        pass  # solana-style base58 address — keep as-is
    return ticker, addr


def compute_report_card(calls: list[KolCall], *, wallet_address: str = "") -> KolReportCard:
    """Aggregate one handle's calls into a report card (pure).

    Counts deletions (the shame core), shill/rug calls, tokens mentioned,
    and calls on tokens known to the scanner denylist. ``wallet_address``
    comes from attribution when known. Pure — deterministic for a fixed
    input; never raises.
    """
    if not calls:
        return KolReportCard(handle="", wallet_address=wallet_address)
    handle = calls[0].handle
    total = len(calls)
    deleted = sum(1 for c in calls if c.is_deleted)
    shill = sum(1 for c in calls if classify_call(c.tweet_text) in ("shill", "rug"))
    tokens = {c.token_symbol or "" for c in calls if c.token_symbol}
    tokens.discard("")
    known_scam = _known_scam_token_count(calls)
    card = KolReportCard(
        handle=handle,
        total_calls=total,
        deleted_calls=deleted,
        shill_calls=shill,
        scam_token_calls=known_scam,
        tokens_mentioned=len(tokens),
        wallet_address=wallet_address
        or next((c.wallet_address for c in calls if c.wallet_address), ""),
    )
    card.score = shame_score(card)
    card.grade = grade_for(card.score)
    return card


def _known_scam_token_count(calls: list[KolCall]) -> int:
    """Calls whose token address is on the bot's known-scam denylist."""
    try:
        from rugmunchbot.db import get_known_scam_addresses

        denylist = {str(a).lower() for a in get_known_scam_addresses()}
    except Exception:  # fail-open — denylist unavailable means 0
        return 0
    return sum(1 for c in calls if str(c.token_address or "").lower() in denylist)


def shame_score(card: KolReportCard) -> float:
    """Shame score — higher is worse. Deletion is the heaviest signal.

    Weighting rationale: deleting a call after the fact is deliberate
    retraction (the strongest dishonesty signal), so it dominates. Repeated
    shilling and calls on known-scam tokens add weight. A KOL with many
    clean calls but zero deletions/shills stays near 0.
    """
    score = 0.0
    score += 40.0 * card.deleted_calls
    score += 15.0 * card.shill_calls
    score += 25.0 * card.scam_token_calls
    if card.wallet_address:
        score += 2.0  # attribution known — the card is actionable
    # Repeated shilling scales: 3+ shill-heavy calls is coordinated behavior.
    if card.shill_calls >= 3 and card.total_calls >= 5:
        score += 10.0
    return round(score, 1)


def grade_for(score: float) -> str:
    """Letter grade for a shame score (<5 → A+, ≥80 → F)."""
    grade = "A+"
    for threshold, band in _GRADE_BANDS:
        if score >= threshold:
            grade = band
    return grade


def rank_hall_of_shame(cards: list[KolReportCard], limit: int = 10) -> list[KolReportCard]:
    """Worst-first ranking; only cards that qualify for the hall (score ≥ 15)."""
    qualified = [c for c in cards if c.in_hall_of_shame]
    qualified.sort(key=lambda c: (c.score, c.deleted_calls), reverse=True)
    return qualified[: max(1, limit)]


# ══════════════════════════════════════════════════════════════════════════
# FORMATTERS (Telegram HTML)
# ══════════════════════════════════════════════════════════════════════════


def format_report_card(card: KolReportCard) -> str:
    """One KOL's report card as an HTML block."""
    handle = card.handle or "unknown"
    head = f"🧾 <b>KOL report card: @{handle}</b> — grade {card.grade}"
    lines = [
        head,
        f"• calls: {card.total_calls}",
        f"• deleted calls: {card.deleted_calls}",
        f"• shill/rug calls: {card.shill_calls}",
        f"• tokens on scam denylist: {card.scam_token_calls}",
        f"• tokens mentioned: {card.tokens_mentioned}",
    ]
    if card.wallet_address:
        short = card.wallet_address[:8] + "…" + card.wallet_address[-6:]
        lines.append(f"• wallet: <code>{short}</code>")
    lines.append(f"• shame score: <b>{card.score:.1f}</b>")
    return "\n".join(lines)


def format_hall_of_shame_post(cards: list[KolReportCard]) -> str:
    """HTML post listing the worst KOLs, worst first (dedupe-ready)."""
    if not cards:
        return ""
    lines = ["🪦 <b>KOL HALL OF SHAME</b> — worst KOL report cards right now", ""]
    for card in cards[:5]:
        entry = f"@{card.handle} — grade <b>{card.grade}</b> (score {card.score:.0f})"
        detail = []
        if card.deleted_calls:
            detail.append(f"{card.deleted_calls} deleted")
        if card.shill_calls:
            detail.append(f"{card.shill_calls} shills")
        if card.scam_token_calls:
            detail.append(f"{card.scam_token_calls} scam-token calls")
        if detail:
            entry += f" — {', '.join(detail)}"
        lines.append(entry)
    lines.append("")
    lines.append(
        "Deleted calls + repeated shilling = pattern of bad faith. Verify before ape: /scan @rugmunchbot"
    )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# SWEEP + RUNNER (async I/O; store + bot are injected)
# ══════════════════════════════════════════════════════════════════════════


def _call_from_tweet(tweet: dict[str, Any]) -> KolCall | None:
    """Build a KolCall from a normalized ApiPass tweet (None if not a call)."""
    text = str(tweet.get("text") or "")
    handle = str(tweet.get("handle") or "")
    tweet_id = str(tweet.get("tweet_id") or "")
    if not text or not handle or not tweet_id:
        return None
    ctype = classify_call(text)
    if ctype == "other":
        return None
    ticker, addr = extract_tokens(text)
    return KolCall(
        handle=handle,
        tweet_id=tweet_id,
        token_symbol=ticker,
        token_address=addr,
        chain="ethereum" if addr and addr.startswith("0x") else "solana" if addr else "",
        call_type=ctype,
        tweet_text=text[:500],
        posted_at=str(tweet.get("created_at") or ""),
        source_url=str(tweet.get("url") or ""),
    )


async def sweep_tracked_kol_tweets() -> list[KolCall]:
    """Fetch recent tweets from every tracked KOL via ApiPass (bounded).

    Returns call-shaped ``KolCall`` objects only (non-call tweets are
    dropped). Bounded by the ApiPass hourly budget: with the default 3
    calls/hr and ~12 tracked handles, one handle per tick is swept
    round-robin (``RMI_KOL_TRACKED_HANDLE_INDEX`` start offset), so no tick
    exceeds the burn. Fail-open ``[]``.
    """
    try:
        from rugmunchbot.tools.apipass_x import fetch_user_tweets
    except Exception:  # pragma: no cover — import failure fails open
        logger.warning("kol_shame:apipass_import_failed")
        return []

    # Round-robin: sweep one handle per tick (budget-compliant).
    offset = int(os.getenv("RMI_KOL_TRACKED_HANDLE_INDEX", "0"))
    handles = list(KOL_TRACKED_HANDLES)
    if not handles:
        return []
    idx = offset % len(handles)
    handle = handles[idx]
    try:
        tweets = await fetch_user_tweets(handle, limit=10)
    except Exception as exc:  # fail-open by contract
        logger.debug("kol_shame:sweep_failed handle=%s err=%s", handle, str(exc)[:120])
        return []
    calls: list[KolCall] = []
    for tweet in tweets:
        call = _call_from_tweet(tweet)
        if call is not None:
            calls.append(call)
    logger.info("kol_shame:swept handle=%s tweets=%s calls=%s", handle, len(tweets), len(calls))
    return calls


async def detect_deleted_calls(
    calls: list[KolCall], window_hours: int = FRESH_CALL_HOURS
) -> list[KolCall]:
    """Re-check this window's recorded calls; mark the ones now gone.

    A call whose tweet now 404s on ApiPass (tweet-detail → None) is
    retracted. Uses one budget slot total (the calls are re-fetched in a
    single batch via the free polling rail; only the detail checks burn).
    Fail-open: ApiPass unavailable → no deletions detected this tick.
    """
    try:
        from rugmunchbot.tools.apipass_x import fetch_tweet_detail
    except Exception:
        return []

    deleted: list[KolCall] = []
    for call in calls[:3]:  # bounded detail checks per tick
        detail = await fetch_tweet_detail(call.tweet_id)
        if detail is None:
            deleted.append(call)
    return deleted


def _card_from_rows(
    calls: list[KolCall], cached: dict[str, dict[str, Any]]
) -> KolReportCard | None:
    """Build one card from fresh rows + a cached aggregate (pure wrapper)."""
    if not calls:
        return None
    handle = calls[0].handle
    wallet = ""
    for c in calls:
        if c.wallet_address:
            wallet = c.wallet_address
            break
    if not wallet:
        cached_row = cached.get(handle)
        if cached_row:
            wallet = str(cached_row.get("wallet_address") or "")
    return compute_report_card(calls, wallet_address=wallet)


async def run_kol_shame_watch(ctx: Any = None) -> int:
    """Sweep tracked KOLs → record calls → detect deletes → alert the hall.

    Returns the number of alerts posted (0..``KOL_SHAME_MAX_ALERTS``).
    Posts the KOL Hall of Shame to @rmialerts, deduped per day per card.
    Fail-open at every stage — a quiet tick is a valid outcome.
    """
    from rugmunchbot.channels.scheduler._core import _chat_id, _release, _remember, _safe_send
    from rugmunchbot.db import (
        get_kol_scores,
        list_kol_calls,
        mark_kol_call_deleted,
        record_kol_call,
        upsert_kol_score,
    )

    chat_id = _chat_id("alerts")
    if not chat_id:
        logger.debug("kol_shame:skipped_no_alerts_chat")
        return 0
    if not KOL_SHAME_ENABLED:
        return 0

    calls = await sweep_tracked_kol_tweets()
    recorded = 0
    for call in calls:
        if record_kol_call(
            handle=call.handle,
            tweet_id=call.tweet_id,
            token_symbol=call.token_symbol,
            token_address=call.token_address,
            chain=call.chain,
            call_type=call.call_type,
            tweet_text=call.tweet_text,
            posted_at=call.posted_at,
            source_url=call.source_url,
            wallet_address=call.wallet_address,
        ):
            recorded += 1
            logger.info(
                "kol_shame:recorded handle=%s tweet=%s type=%s",
                call.handle,
                call.tweet_id,
                call.call_type,
            )

    # Detect deletions among the last window's recorded calls (bounded).
    recent = [KolCall.from_row(r) for r in list_kol_calls(limit=20)]
    fresh = [c for c in recent if _captured_within(c, FRESH_CALL_HOURS)]
    deleted = await detect_deleted_calls(fresh)
    for call in deleted:
        if mark_kol_call_deleted(call.tweet_id):
            logger.warning(
                "kol_shame:deleted_call handle=%s tweet=%s token=%s",
                call.handle,
                call.tweet_id,
                call.token_symbol or call.token_address,
            )

    # Refresh cached report cards for the affected handles.
    affected = {c.handle for c in (*calls, *deleted)}
    for handle in affected:
        handle_calls = [KolCall.from_row(r) for r in list_kol_calls(handle=handle, limit=100)]
        card = compute_report_card(handle_calls)
        upsert_kol_score(
            handle=handle,
            total_calls=card.total_calls,
            deleted_calls=card.deleted_calls,
            shill_calls=card.shill_calls,
            scam_token_calls=card.scam_token_calls,
            tokens_mentioned=card.tokens_mentioned,
            score=card.score,
            grade=card.grade,
        )

    # Post the hall (deduped per card per day).
    cached = {str(r["handle"]): r for r in get_kol_scores(limit=100)}
    cards: list[KolReportCard] = []
    for row in cached.values():
        handle_rows = [
            KolCall.from_row(r) for r in list_kol_calls(handle=str(row["handle"]), limit=100)
        ]
        built = _card_from_rows(handle_rows, cached)
        if built is not None:
            cards.append(built)
    hall = rank_hall_of_shame(cards, limit=KOL_SHAME_MAX_ALERTS + 2)

    today = datetime.now(UTC).strftime("%Y%m%d")
    posted = 0
    for card in hall[:KOL_SHAME_MAX_ALERTS]:
        dedupe_key = f"kol_shame:{card.handle}:{today}"
        if not _remember("kol_shame", dedupe_key):
            continue
        if ctx is None:
            _release("kol_shame", dedupe_key)
            continue
        text = format_report_card(card)
        if await _safe_send(ctx.bot, chat_id, text):
            posted += 1
            logger.info(
                "kol_shame:alerted handle=%s grade=%s score=%.1f",
                card.handle,
                card.grade,
                card.score,
            )
        else:
            _release("kol_shame", dedupe_key)

    # Best-effort site bridge: JSONL export in the backend CallStore shape.
    _export_site_bridge()

    logger.info(
        "kol_shame:run_done swept=%s recorded=%s deleted=%s hall=%s posted=%s",
        len(calls),
        recorded,
        len(deleted),
        len(hall),
        posted,
    )
    return posted


def _captured_within(call: KolCall, hours: float) -> bool:
    """True when a call was captured within the last *hours*."""
    try:
        ts = datetime.fromisoformat(call.captured_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return datetime.now(UTC) - ts.astimezone(UTC) <= timedelta(hours=max(1, hours))
    except (ValueError, TypeError):
        return True  # undated rows stay in the window


def _export_site_bridge() -> None:
    """Write the JSONL export so the site's ``/api/v1/kol/scammer-calls`` can render.

    Default path is the bot's own data dir; an operator pointing
    ``RMI_KOL_CALL_STORE_PATH`` at the backend's
    ``app/databus/scammer_calls.json`` wires the hall directly onto the site.
    Fail-open.
    """
    try:
        from rugmunchbot.db import export_kol_calls_jsonl

        path = os.getenv(
            "RMI_KOL_CALL_STORE_PATH",
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data",
                "kol_hall_of_shame.jsonl",
            ),
        )
        export_kol_calls_jsonl(path)
    except Exception as exc:  # fail-open by contract
        logger.debug("kol_shame:export_failed err=%s", str(exc)[:120])


__all__ = [
    "FRESH_CALL_HOURS",
    "KOL_SHAME_ENABLED",
    "KOL_SHAME_INTERVAL_MIN",
    "KOL_SHAME_MAX_ALERTS",
    "KOL_TRACKED_HANDLES",
    "KolCall",
    "KolReportCard",
    "classify_call",
    "compute_report_card",
    "detect_deleted_calls",
    "extract_tokens",
    "format_hall_of_shame_post",
    "format_report_card",
    "grade_for",
    "rank_hall_of_shame",
    "run_kol_shame_watch",
    "shame_score",
    "sweep_tracked_kol_tweets",
]
