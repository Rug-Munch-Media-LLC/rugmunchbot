from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels._scan_commentary import attach_scan_commentary
from rugmunchbot.channels.content import (
    fetch_alpha_content,
    fetch_scan_notes,
    fetch_suspect_scan_notes,
)
from rugmunchbot.channels.formatters import (
    format_alpha_post,
    format_main_escalation,
    format_scan_card,
)
from rugmunchbot.channels.router import (
    ROUTE_ALPHA_CATEGORY,
)
from rugmunchbot.config import (
    ALPHA_CHANNEL_LIMIT,
    SCAN_CHANNEL_LIMIT,
    SCAN_CHANNEL_TICK_LIMIT,
)
from rugmunchbot.support.image_policy import ensure_post_image

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

from typing import TYPE_CHECKING

from rugmunchbot.channels.scheduler._core import (  # noqa: F401
    _FIRST_RUN_SECONDS,
    _chat_id,
    _dedupe_key,
    _elapsed_ms,
    _main_admission,
    _post_photo_aware,
    _posted_keys,
    _record_main_post,
    _remember,
    _safe_send,
    _score_and_partition,
    logger,
)
from rugmunchbot.channels.scheduler._tick import (  # noqa: F401
    _load_channel_state,
    _tick_wrapper,
)

#: Content kinds that are NOT suspicious-token scans and must never post to
#: @rmiscans — news, whale/smart-money alerts, system notifications, product
#: updates, shill/alpha intel, roundups/digests. Mirrors the news-channel
#: gate's blocked vocabulary so the two channels never cross-contaminate.
_SCAN_BLOCKED_KINDS: frozenset[str] = frozenset(
    {
        # news
        "news",
        "breaking_news",
        "crypto_news",
        "headline",
        # whale / smart-money / kol / alpha
        "whale_move",
        "whale_alert",
        "whale",
        "smart_money",
        "smart_money_feed",
        "kol_trade",
        "kol_shill",
        "alpha",
        # system / product / roundup / digest
        "system",
        "system_notification",
        "notification",
        "product_update",
        "changelog",
        "update",
        "roundup",
        "digest",
        "market_summary",
        "market-summary",
        "quality",
        # shill intel
        "shill_evidence",
        "shill_watch",
        "shill_campaign",
        "shill",
    }
)


def _scan_content_type_gate(item: dict[str, Any]) -> str:
    """@rmiscans content-type gate. Returns "" when *item* may post as a
    suspicious-token scan card, else the reason.

    The channel-doctrine filter that keeps @rmiscans suspicious-token-scans
    ONLY. Blocks any item whose ``kind``/``content_type``/``alert_type`` is a
    non-scan content type (news, whale alerts, system notifications, product
    updates, shill/alpha intel, roundups) so a whale alert or a system note
    can never render as a scan card. Also drops items with no token address —
    a stray non-token record must never post. Pure — never raises; unknown or
    missing kinds pass (the address check still applies).
    """
    kind = (
        str(item.get("kind") or item.get("content_type") or item.get("alert_type") or "")
        .strip()
        .lower()
    )
    if kind in _SCAN_BLOCKED_KINDS:
        return f"blocked_kind({kind})"
    # Structural gate: a suspicious-token scan card needs a token address. An
    # item without one is not a token scan (e.g. a system note) and must not
    # post. Name/chain/risk are rendered by ``format_scan_card`` (fail-open to
    # honest placeholders) and are not hard requirements here.
    if not str(item.get("address") or item.get("token_address") or "").strip():
        return "no_token_address"
    return ""


async def post_scan_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the day's notable scans to @rmiscans, one compact card each.
    High-importance scans (≥0.7) ALSO escalate solo to @cryptorugmuncher.

    Returns the number of cards posted (0..``SCAN_CHANNEL_TICK_LIMIT``). The
    day's most notable scanned tokens (launch-alert ≥70 rail + scan history,
    ``channels/content.py::fetch_scan_notes``) are deduped per token
    (``_remember("scans", …)`` so the same token is never reposted), reranked
    (``channels/importance.py``), and every item at/above the news floor
    (``route_news``, default 0.4) posts as its own compact card — real risk
    reasons (honeypot / bundle / LP removed / flagged deployer) + risk score
    + token + chain + scan link (rugmunch.io/scanner), each card topped with
    a bounded witty one-liner (``attach_scan_commentary``, fail-open to no
    line). When the backend rails are quiet, the 2026-08-29 churn rail
    (``fetch_suspect_scan_notes``) runs the full unified v2 scanner on the
    scanner's flagged honeypots/rugs so @rmiscans churns real suspicious-token
    content — each card carries the FULL RiskReport block (deployer, top-25,
    first-buyer, fresh%, CEX-funded%, bundle/cluster, 30-factor,
    determination) + links to the bot / website / other channels. Items
    scoring ≥0.7 ALSO post solo to @cryptorugmuncher so the most notable
    token bangers reach the broader audience. Fail-open: an empty pool (no
    scans today / backend down) posts nothing, and a reranker outage keeps
    the channel flowing with the heuristic fallback (full window) exactly
    like the alerts job.
    """
    started = time.perf_counter()
    chat_id = _chat_id("scans")
    if not chat_id:
        logger.info(
            "channels:tick",
            job="channel_scans",
            items_swept=0,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_chat_id",
        )
        return 0
    scans = await fetch_scan_notes(SCAN_CHANNEL_LIMIT)
    pending = [item for item in scans if item.get("scan_pending")]
    full = [item for item in scans if not item.get("scan_pending")]
    pending_fresh = [
        item
        for item in pending
        if _remember(
            "scans_pending",
            "pending_"
            + str(item.get("address") or item.get("token_address") or "").strip().lower(),
        )
    ]
    fresh = [item for item in full if _remember("scans", _dedupe_key("scans", item))]
    # 2026-08-29 churn rail (2026-09-02 trigger fix): the backend
    # launch-alert + scan-history rails keep returning the SAME tokens every
    # tick, which dedupe to zero fresh items — the old trigger (raw pool
    # size < tick limit) never fired once the backend pool was non-empty,
    # so @rmiscans posted nothing forever ("no_fresh" every tick). Trigger
    # on the FRESH pool instead: whenever fewer than the tick cap of
    # not-yet-posted items exist, supplement with fresh FULL scans of the
    # scanner's flagged honeypots/rugs (unified v2 scanner, fail-open) so
    # the channel churns real suspicious-token content every tick.
    if len(fresh) + len(pending_fresh) < SCAN_CHANNEL_TICK_LIMIT:
        churned = await fetch_suspect_scan_notes(limit=SCAN_CHANNEL_TICK_LIMIT)
        if churned:
            seen = {
                str(i.get("address") or i.get("token_address") or "").strip().lower()
                for i in (*scans, *fresh, *pending_fresh)
            }
            for c in churned:
                addr = str(c.get("address") or c.get("token_address") or "").strip().lower()
                if not addr or addr in seen:
                    continue
                seen.add(addr)
                scans.append(c)
                if c.get("scan_pending"):
                    if _remember(
                        "scans_pending",
                        "pending_" + addr,
                    ):
                        pending_fresh.append(c)
                elif _remember("scans", _dedupe_key("scans", c)):
                    fresh.append(c)
    # Scans-of-interest honesty (2026-08-29): a ``scan_pending`` note is a
    if not fresh and not pending_fresh:
        logger.info(
            "channels:tick",
            job="channel_scans",
            items_swept=len(scans),
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_fresh",
        )
        return 0
    main_items, keep, reranker_down = await _score_and_partition(
        fresh, category="news", source="scanner"
    )
    # 2026-08-29 churn bypass: sus-token cards from the churn rail
    # (``fetch_suspect_scan_notes``) are relevant BY CONSTRUCTION — the
    # scans channel exists to surface suspicious tokens with full scan
    # output, and the trend-risk sketch already flagged them. The reranker's
    # news-importance floor (0.4) was built for headline value and drops
    # sketch-only cards, so churn items post even below the floor (still
    # deduped per token + bounded by the tick cap). Backend rail items keep
    # the reranker gate.
    churn_flagged = {str(i.get("address") or "").strip().lower() for i in scans if i.get("churned")}
    keep_set = {str(i.get("address") or "").strip().lower() for i in keep}
    for item in fresh:
        addr = str(item.get("address") or "").strip().lower()
        if addr in churn_flagged and addr not in keep_set:
            keep.append(item)
    # Post at most SCAN_CHANNEL_TICK_LIMIT cards per tick: the reranker gate
    # (skip <0.4) + per-token dedupe are the volume controls; the tick cap
    # just spreads the day's notable scans across the day (~15-20/day at the
    # default 60m cadence).
    # Content-type gate (2026-09-03): @rmiscans is suspicious-token-scans
    # ONLY — drop any item that is not a genuine suspicious token scan (news,
    # whale alerts, system notifications, product updates, shill/alpha intel,
    # roundups) before it can render as a card.
    keep = [i for i in keep if not _scan_content_type_gate(i)]
    main_items = [i for i in main_items if not _scan_content_type_gate(i)]
    posted = 0
    for item in sorted(keep, key=lambda i: i.get("importance", 0.0), reverse=True)[
        :SCAN_CHANNEL_TICK_LIMIT
    ]:
        # One bounded witty one-liner per card (fail-open to no line).
        item = await attach_scan_commentary(item)
        ok = await _post_photo_aware(
            ctx.bot,
            chat_id,
            ensure_post_image(format_scan_card(item)),
            _safe_send,
            content_type="scan",
        )
        if ok:
            posted += 1
    # Pending "Scanning…" teasers — bounded by the tick cap, own dedupe
    # namespace, never escalated to main. No commentary (they are status
    # cards, not final verdicts). Same content-type gate: only genuine
    # suspicious-token scans post.
    pending_fresh = [i for i in pending_fresh if not _scan_content_type_gate(i)]
    remaining = max(0, SCAN_CHANNEL_TICK_LIMIT - posted)
    for item in sorted(pending_fresh, key=lambda i: i.get("importance", 0.0), reverse=True)[
        :remaining
    ]:
        ok = await _post_photo_aware(
            ctx.bot,
            chat_id,
            ensure_post_image(format_scan_card(item)),
            _safe_send,
            content_type="scan",
        )
        if ok:
            posted += 1
    # Escalate high-importance scans (≥0.7) to the main channel @cryptorugmuncher
    # so the most notable token scans also reach the broader audience.
    main_chat_id = _chat_id("main")
    if main_chat_id and main_items:
        for item in sorted(main_items, key=lambda i: i.get("importance", 0.0), reverse=True):
            if not _remember("scansmain", _dedupe_key("scansmain", item)):
                continue
            # Repeat-suppression (2026-08-25): the same scam-channel story
            # surfaces under many token addresses; address-keyed dedupe alone
            # re-escalated it to main every tick. Also key on normalized
            # title+verdict so near-identical cards collapse for a day.
            import hashlib as _hl

            content_sig = _hl.sha256(
                (
                    str(item.get("title") or item.get("name") or "").strip().lower()
                    + "|"
                    + str(item.get("verdict") or item.get("summary") or "").strip().lower()[:120]
                ).encode()
            ).hexdigest()[:16]
            if not _remember("scansmain_content", f"scansmain_content:{content_sig}"):
                logger.info("main_repeat_suppressed title=%s", str(item.get("title"))[:60])
                continue
            # WO-2 hard cap: ≤9/day on main; overflow drops (the scans
            # card above already carries the item natively).
            if await _main_admission("scan") != "main":
                continue
            item = await attach_scan_commentary(item)
            ok = await _post_photo_aware(
                ctx.bot,
                main_chat_id,
                ensure_post_image(format_scan_card(item)),
                _safe_send,
                content_type="scan",
            )
            if ok:
                posted += 1
                await _record_main_post()
                # W30 top-of-food-chain mirror: high-importance scan cards
                # (≥0.7) that escalate to main also land on feed.rugmunch.io.
                try:
                    from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

                    mirror_post_to_feed(
                        {
                            "text": str(format_scan_card(item)),
                            "story_ref": str(
                                item.get("address")
                                or item.get("url")
                                or item.get("title")
                                or item.get("name")
                                or ""
                            ),
                            "source": "scanner",
                            "kind": "scan",
                        }
                    )
                except Exception as exc:
                    logger.warning("scan_feed_mirror_failed err=%s", str(exc)[:120])
    logger.info(
        "channels:tick",
        job="channel_scans",
        items_swept=len(scans),
        items_scored=len(fresh) + len(pending_fresh),
        items_posted=posted,
        items_failed=0,
        latency_ms=_elapsed_ms(started),
        reason=("reranker_fallback" if reranker_down else "below_floor" if not posted else None),
    )
    return posted


async def post_alpha_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the curated alpha digest (whale/smart/launch/KOL); escalate ≥0.7 to main.

    Returns the number of messages posted (0-1 native digest + 0..N main
    escalations). Whale/smart/launch/KOL items are deduped per section,
    reranked (``channels/importance.py``), and routed through ``route_news``
    (category "alpha"): below the news floor they are dropped (rerank mode
    only), the rest form the alpha digest (verdict + links, photo when
    available), and items ≥0.7 ALSO post solo to @cryptorugmuncher — each
    main post mirrors to feed.rugmunch.io (W30 top-of-food-chain mirror).
    Fail-open: a reranker outage keeps the digest flowing (heuristic
    fallback) and never blocks the JobQueue.
    """
    started = time.perf_counter()
    chat_id = _chat_id("alpha")
    if not chat_id:
        logger.info(
            "channels:tick",
            job="channel_alpha",
            items_swept=0,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_chat_id",
        )
        return 0
    content = await fetch_alpha_content(
        chain="solana",
        whale_limit=max(1, ALPHA_CHANNEL_LIMIT),
        smart_limit=ALPHA_CHANNEL_LIMIT,
        launch_limit=ALPHA_CHANNEL_LIMIT,
        kol_limit=ALPHA_CHANNEL_LIMIT,
    )
    whale_fresh = [
        m for m in content["whale_moves"] if _remember("alpha:whale", _dedupe_key("whale", m))
    ]
    smart_fresh = [
        w for w in content["smart_money"] if _remember("alpha:smart", _dedupe_key("smart", w))
    ]
    launch_fresh = [
        item
        for item in content["launches"]
        if _remember("alpha:launch", _dedupe_key("launch", item))
    ]
    kol_fresh = [
        k for k in content.get("kol_trades", []) if _remember("alpha:kol", _dedupe_key("kol", k))
    ]
    swept = (
        len(content["whale_moves"])
        + len(content["smart_money"])
        + len(content["launches"])
        + len(content.get("kol_trades", []))
    )
    scored = len(whale_fresh) + len(smart_fresh) + len(launch_fresh) + len(kol_fresh)
    if not scored:
        logger.info(
            "channels:tick",
            job="channel_alpha",
            items_swept=swept,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_fresh",
        )
        return 0
    whale_main, whale_keep, whale_down = await _score_and_partition(
        whale_fresh, category=ROUTE_ALPHA_CATEGORY, source="WhaleWatcher"
    )
    smart_main, smart_keep, smart_down = await _score_and_partition(
        smart_fresh, category=ROUTE_ALPHA_CATEGORY, source="smart_money_feed"
    )
    launch_main, launch_keep, launch_down = await _score_and_partition(
        launch_fresh, category=ROUTE_ALPHA_CATEGORY, source="launch_feed"
    )
    kol_main, kol_keep, kol_down = await _score_and_partition(
        kol_fresh, category=ROUTE_ALPHA_CATEGORY, source="kol_trades"
    )
    reranker_down = whale_down or smart_down or launch_down or kol_down
    posted = 0
    if whale_keep or smart_keep or launch_keep or kol_keep:
        ok = await _post_photo_aware(
            ctx.bot,
            chat_id,
            format_alpha_post(whale_keep, smart_keep, launch_keep, kol_trades=kol_keep),
            _safe_send,
            content_type="alpha",
        )
        if ok:
            posted += 1
    main_chat_id = _chat_id("main")
    if main_chat_id:
        escalated = sorted(
            [*whale_main, *smart_main, *launch_main, *kol_main],
            key=lambda i: i.get("importance", 0.0),
            reverse=True,
        )
        for item in escalated:
            if not _remember("alphamain", _dedupe_key("alphamain", item)):
                continue
            # WO-2 hard cap: ≤9/day on main; overflow drops (the alpha
            # digest above already carries the item natively).
            if await _main_admission("alpha") != "main":
                continue
            main_text = format_main_escalation(item)
            ok = await _post_photo_aware(
                ctx.bot, main_chat_id, main_text, _safe_send, content_type="alpha"
            )
            if ok:
                posted += 1
                await _record_main_post()
                # W30 top-of-food-chain mirror: every main post lands on
                # feed.rugmunch.io (best-effort, never blocks the tick).
                try:
                    from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

                    mirror_post_to_feed(
                        {
                            "text": str(main_text),
                            "story_ref": str(
                                item.get("url") or item.get("address") or item.get("title") or ""
                            ),
                            "source": "alpha",
                            "kind": "alpha",
                        }
                    )
                except Exception as exc:
                    logger.warning("alpha_feed_mirror_failed err=%s", str(exc)[:120])
    if posted:
        logger.info(
            "channels:alpha posted=%s whales=%s smart=%s launches=%s kol=%s main=%s",
            posted,
            len(whale_keep),
            len(smart_keep),
            len(launch_keep),
            len(kol_keep),
            len(escalated) if main_chat_id else 0,
        )
    logger.info(
        "channels:tick",
        job="channel_alpha",
        items_swept=swept,
        items_scored=scored,
        items_posted=posted,
        items_failed=0,
        latency_ms=_elapsed_ms(started),
        reason=("reranker_fallback" if reranker_down else "below_floor" if not posted else None),
    )
    return posted
