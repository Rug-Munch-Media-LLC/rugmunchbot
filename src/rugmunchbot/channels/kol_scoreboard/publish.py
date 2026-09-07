"""KOL Scoreboard — publishing/runner.

Split from the former monolithic ``kol_scoreboard.py``: the scheduler
entry point that sweeps, records, detects deletions, refreshes cards,
and posts the scoreboard + Hall-of-Shame subset to @rmialerts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.kol_scoreboard.data import (
    FRESH_CALL_HOURS,
    KOL_SCOREBOARD_ENABLED,
    KOL_SCOREBOARD_MAX_ALERTS,
    KolCall,
    KolIdentity,
    _captured_within,
    _export_site_bridge,
    build_scoreboard_cards,
    detect_deleted_calls,
    link_wallet_from_call,
    sweep_tracked_kol_tweets,
)
from rugmunchbot.channels.kol_scoreboard.formatting import format_report_card
from rugmunchbot.channels.kol_scoreboard.scoring import (
    compute_report_card,
    rank_hall_of_shame,
    rank_scoreboard,
)
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from rugmunchbot.channels.kol_scoreboard.data import KolReportCard

logger = get_logger("rmi_bot.channels.kol_scoreboard")


async def run_kol_scoreboard_watch(ctx: Any = None) -> int:
    """Sweep one KOL → record calls → wallet link → deletes → name changes →
    refresh cards → post scoreboard + hall-of-shame subset to @rmialerts.

    Returns the number of alerts posted (0..``KOL_SCOREBOARD_MAX_ALERTS``).
    Fail-open at every stage — a quiet tick is a valid outcome.

    [REVIEW 2026-09-07] The backend ``GET /api/v1/kol/leaderboard`` is a
    CONSUMER of this bot's own DB (``kol_scores`` ⋈ ``kol_identities`` via
    ``load_scorecard``) — the bot is the source of truth and publishes the
    scoreboard here. There is no bot-side handler that renders a leaderboard
    from the backend, so there is nothing to wire: the bot already owns this
    data and the backend reads it. No change needed.
    """
    from rugmunchbot.channels.scheduler._core import _chat_id, _release, _remember, _safe_send
    from rugmunchbot.db import (
        list_kol_calls,
        list_kol_name_changes,
        mark_kol_call_deleted,
        record_kol_call,
        resolve_handle,
        upsert_kol_score,
    )

    chat_id = _chat_id("alerts")
    if not chat_id:
        logger.debug("kol_scoreboard:skipped_no_alerts_chat")
        return 0
    if not KOL_SCOREBOARD_ENABLED:
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
            link_wallet_from_call(call)
            logger.info(
                "kol_scoreboard:recorded handle=%s tweet=%s type=%s",
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
                "kol_scoreboard:deleted_call handle=%s tweet=%s token=%s",
                call.handle,
                call.tweet_id,
                call.token_symbol or call.token_address,
            )

    # Refresh cached report cards for the affected handles.
    affected = {c.handle for c in (*calls, *deleted)}
    for handle in affected:
        handle_calls = [KolCall.from_row(r) for r in list_kol_calls(handle=handle, limit=100)]
        identity = KolIdentity.from_row(resolve_handle(handle))
        name_changes = len(
            list(list_kol_name_changes(kol_id=(identity.kol_id if identity else handle.lower())))
        )
        card = compute_report_card(handle_calls, name_changes=name_changes, identity=identity)
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

    # Build cards for every KOL with recorded activity (scoreboard pool).
    cards: list[KolReportCard] = build_scoreboard_cards()
    scoreboard = rank_scoreboard(cards, limit=KOL_SCOREBOARD_MAX_ALERTS + 2)
    hall = rank_hall_of_shame(cards, limit=KOL_SCOREBOARD_MAX_ALERTS + 2)

    today = datetime.now(UTC).strftime("%Y%m%d")
    posted = 0
    for card in scoreboard[:KOL_SCOREBOARD_MAX_ALERTS]:
        dedupe_key = f"kol_scoreboard:{card.kol_id}:{today}"
        if not _remember("kol_scoreboard", dedupe_key):
            continue
        if ctx is None:
            _release("kol_scoreboard", dedupe_key)
            continue
        text = format_report_card(card)
        if await _safe_send(ctx.bot, chat_id, text, content_type="alert"):
            posted += 1
            logger.info(
                "kol_scoreboard:alerted handle=%s grade=%s score=%.1f",
                card.handle,
                card.grade,
                card.score,
            )
        else:
            _release("kol_scoreboard", dedupe_key)

    # Best-effort site bridge: JSONL export in the backend CallStore shape.
    _export_site_bridge()

    logger.info(
        "kol_scoreboard:run_done swept=%s recorded=%s deleted=%s scoreboard=%s hall=%s posted=%s",
        len(calls),
        recorded,
        len(deleted),
        len(scoreboard),
        len(hall),
        posted,
    )
    return posted
