"""KOL Scoreboard — pure scoring/ranking logic.

Split from the former monolithic ``kol_scoreboard.py``: classification
heuristics, report-card aggregation, scoring, grading, and worst-first
ranking. Pure + deterministic; no I/O except the fail-open denylist read.
"""

from __future__ import annotations

from rugmunchbot.channels.kol_scoreboard.data import (
    _ADDRESS_RE,
    _CALL_VERB_RE,
    _PUMP_RE,
    _RUG_VERB_RE,
    _TICKER_RE,
    KolCall,
    KolIdentity,
    KolReportCard,
)

#: Letter-grade bands (lower score = better).
_GRADE_BANDS: tuple[tuple[float, str], ...] = (
    (5.0, "A"),
    (15.0, "B"),
    (30.0, "C"),
    (50.0, "D"),
    (80.0, "F"),
)

#: Repeat-scammer thresholds — Hall of Shame membership (not just a bad grade).
REPEAT_SCAMMER_MIN_DELETED = 2
REPEAT_SCAMMER_MIN_SCAM_CALLS = 2
REPEAT_SCAMMER_MIN_SHILLS = 3


def classify_call(tweet_text: str) -> str:
    """Heuristic call type for one tweet: 'shill' | 'rug' | 'buy' | 'other'."""
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
    return ticker, addr


def extract_wallet_address(tweet_text: str) -> str:
    """Best-effort wallet address in a call tweet (solana base58 / EVM 0x)."""
    text = str(tweet_text or "")
    m = _ADDRESS_RE.search(text)
    if not m:
        return ""
    addr = m.group(1)
    if addr.startswith("0x") and len(addr) < 32:
        return ""  # 0x contract-ish / too short to be a wallet
    if len(addr) >= 32:
        return addr
    return ""


def compute_report_card(
    calls: list[KolCall],
    *,
    wallet_address: str = "",
    name_changes: int = 0,
    identity: KolIdentity | None = None,
    traced_wallets: list[dict[str, str]] | None = None,
) -> KolReportCard:
    """Aggregate one KOL's calls into a report card (pure).

    Counts deletions, shill/rug calls, wins/losses (from resolved call
    outcomes), tokens mentioned, and calls on tokens on the scanner
    denylist. ``is_repeat_scammer`` gates Hall-of-Shame membership.
    ``traced_wallets`` carries the wallet KB entries (address + chain +
    confidence) so the card can render tracing evidence.
    """
    if not calls:
        return KolReportCard(
            handle="",
            wallet_address=wallet_address,
            wallets=_clean_wallets(traced_wallets),
            name_changes=name_changes,
        )
    handle = calls[0].handle
    total = len(calls)
    deleted = sum(1 for c in calls if c.is_deleted)
    shill = sum(1 for c in calls if classify_call(c.tweet_text) in ("shill", "rug"))
    wins = sum(1 for c in calls if c.result == "win")
    losses = sum(1 for c in calls if c.result == "loss")
    tokens = {c.token_symbol or "" for c in calls if c.token_symbol}
    tokens.discard("")
    known_scam = _known_scam_token_count(calls)
    repeat = (
        deleted >= REPEAT_SCAMMER_MIN_DELETED
        or known_scam >= REPEAT_SCAMMER_MIN_SCAM_CALLS
        or shill >= REPEAT_SCAMMER_MIN_SHILLS
    )
    card = KolReportCard(
        handle=handle,
        kol_id=(identity.kol_id if identity else "") or handle.lower(),
        name=(identity.name if identity else "") or handle,
        category=(identity.category if identity else "") or "",
        scammer_group=(identity.scammer_group if identity else "") or "",
        total_calls=total,
        deleted_calls=deleted,
        shill_calls=shill,
        scam_token_calls=known_scam,
        wins=wins,
        losses=losses,
        tokens_mentioned=len(tokens),
        wallet_address=wallet_address
        or (identity.wallets[0] if identity and identity.wallets else "")
        or next((c.wallet_address for c in calls if c.wallet_address), ""),
        wallets=_clean_wallets(traced_wallets),
        name_changes=name_changes,
        is_repeat_scammer=repeat,
    )
    card.score = score_card(card)
    card.grade = grade_for(card.score)
    return card


def _clean_wallets(entries: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """Normalize wallet KB entries to {address, chain, confidence} (pure)."""
    if not entries:
        return []
    cleaned: list[dict[str, str]] = []
    for e in entries:
        address = str(e.get("address") or "").strip() if isinstance(e, dict) else str(e).strip()
        if not address:
            continue
        if any(c.get("address", "").lower() == address.lower() for c in cleaned):
            continue
        cleaned.append(
            {
                "address": address,
                "chain": str(e.get("chain") or "") if isinstance(e, dict) else "",
                "confidence": str(e.get("confidence") or "") if isinstance(e, dict) else "",
            }
        )
    return cleaned


def _known_scam_token_count(calls: list[KolCall]) -> int:
    """Calls whose token address is on the bot's known-scam denylist."""
    try:
        from rugmunchbot.db import get_known_scam_addresses

        denylist = {str(a).lower() for a in get_known_scam_addresses()}
    except Exception:  # fail-open — denylist unavailable means 0
        return 0
    return sum(1 for c in calls if str(c.token_address or "").lower() in denylist)


def score_card(card: KolReportCard) -> float:
    """Score — higher is worse. Deletions dominate; wins earn a small offset.

    Weighting rationale: deleting a call after the fact is deliberate
    retraction (the strongest dishonesty signal), so it dominates. Repeated
    shilling and calls on known-scam tokens add weight. Clean call records
    (wins, no deletions) stay near 0.
    """
    score = 0.0
    score += 40.0 * card.deleted_calls
    score += 15.0 * card.shill_calls
    score += 25.0 * card.scam_token_calls
    if card.wallet_address:
        score += 2.0  # attribution known — the card is actionable
    if card.shill_calls >= REPEAT_SCAMMER_MIN_SHILLS and card.total_calls >= 5:
        score += 10.0  # coordinated behavior
    score -= min(2.0 * card.wins, 8.0)  # wins offset, capped
    return round(max(0.0, score), 1)


def grade_for(score: float) -> str:
    """Letter grade for a score (<5 → A+, ≥80 → F)."""
    grade = "A+"
    for threshold, band in _GRADE_BANDS:
        if score >= threshold:
            grade = band
    return grade


def rank_scoreboard(cards: list[KolReportCard], limit: int = 10) -> list[KolReportCard]:
    """Worst-first ranking over ALL cards (the scoreboard)."""
    ranked = sorted(cards, key=lambda c: (c.score, c.deleted_calls, c.losses), reverse=True)
    return ranked[: max(1, limit)]


def rank_hall_of_shame(cards: list[KolReportCard], limit: int = 10) -> list[KolReportCard]:
    """Worst-first ranking of REPEAT SCAMMERS only (Hall of Shame subset)."""
    qualified = [c for c in cards if c.in_hall_of_shame]
    qualified.sort(key=lambda c: (c.score, c.deleted_calls), reverse=True)
    return qualified[: max(1, limit)]
