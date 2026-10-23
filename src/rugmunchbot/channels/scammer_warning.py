"""Scammer-warning posts — flag shills from known scam-syndicate accounts.

When an account attributed to a seeded scammer group (e.g. the ZachXBT-named
``solana_syndicate`` network) shills a token, post a warning instead of
letting the shill pass as normal KOL noise:

  * ``alerts`` → @rmialerts        — every flagged-account shill
  * ``main``   → @cryptorugmuncher — high-signal shills (call classified
    shill/rug), mirrored to feed.rugmunch.io
  * feed       → feed.rugmunch.io  — ``mirror_post_to_feed`` atomic append

The warning cites ZachXBT as the source: the group's evidence URLs live in
``known_scammers.json`` (via ``kol_registry_seed``), so the posted message
links the original exposé — never a bare assertion.

Pipeline:
  1. ``collect_group_shill_calls`` reads the recorded KOL call store
     (``db.kol.list_kol_calls``), resolves each handle to its canonical
     identity (``db.kol.resolve_handle``), and keeps calls whose identity
     carries a ``scammer_group`` in the warning set.
  2. ``format_scammer_warning`` renders the HTML warning card: who shilled
     what, the group label + evidence-basis line, the "reason not to buy
     anything they post" doctrine, and the ZachXBT source link.
  3. ``run_scammer_warning_watch`` posts alerts (always) + main (high
     signal) + feed, deduped per handle+token per day so a campaign warns
     once, never spams.

Fail-open everywhere: a dead DB, unregistered handle, unresolved channel, or
failed send never crashes the tick — silence is a valid outcome.
"""

from __future__ import annotations

import html
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot.channels._story_cta import attach_story_cta
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.scammer_warning")

SCAMMER_WARNING_ENABLED = os.getenv("RMI_SCAMMER_WARNING_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
SCAMMER_WARNING_INTERVAL_MIN = int(os.getenv("RMI_SCAMMER_WARNING_INTERVAL_MIN", "90"))
#: Freshness window (hours) — only calls captured within this window warn.
SCAMMER_WARNING_WINDOW_HOURS = int(os.getenv("RMI_SCAMMER_WARNING_WINDOW_HOURS", "72"))
#: Max warnings posted per run (bounded, no spam).
SCAMMER_WARNING_MAX_ALERTS = int(os.getenv("RMI_SCAMMER_WARNING_MAX_ALERTS", "3"))
#: Scammer groups whose shills trigger warnings (comma-separated env override).
_DEFAULT_WARNING_GROUPS: tuple[str, ...] = (
    "solana_syndicate",
    "impersonation_syndicate",
)
SCAMMER_WARNING_GROUPS: tuple[str, ...] = tuple(
    g.strip()
    for g in os.getenv("RMI_SCAMMER_WARNING_GROUPS", ",".join(_DEFAULT_WARNING_GROUPS)).split(",")
    if g.strip()
)

#: Call types that count as a "high signal" shill for the main channel.
_HIGH_SIGNAL_TYPES: frozenset[str] = frozenset({"shill", "rug"})


def _esc(value: object) -> str:
    """HTML-escape for Telegram parse_mode=html."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _token_label(token_symbol: str, token_address: str) -> str:
    """Rendered token identity: ``$SYM`` when present, else a shortened addr."""
    sym = str(token_symbol or "").strip()
    if sym:
        return "$" + _esc(sym.lstrip("$"))
    addr = str(token_address or "").strip()
    if addr:
        short = addr[:8] + "…" + addr[-6:] if len(addr) > 16 else addr
        return f"<code>{_esc(short)}</code>"
    return "a token"


def _group_evidence(group: str) -> str:
    """Evidence-basis sentence + ZachXBT source URL for one group (fail-open)."""
    try:
        from rugmunchbot.kol_registry_seed import SCAMMER_GROUPS, group_label
    except Exception:  # fail-open by contract
        return ""
    label = group_label(group)
    info = SCAMMER_GROUPS.get(group) or {}
    desc = str(info.get("description") or "").strip()
    basis = f"<i>{_esc(label)}</i>" if label else _esc(group)
    if desc:
        basis += f" — {_esc(desc[:220])}"
    return basis


def format_scammer_warning(
    *,
    handle: str,
    token_symbol: str = "",
    token_address: str = "",
    call_type: str = "buy",
    tweet_text: str = "",
    source_url: str = "",
    group: str = "",
) -> str:
    """Build the HTML warning card for one flagged-account shill (pure).

    Doctrine: these accounts run coordinated panic → scam-traffic networks
    (fake giveaways + pump-and-dump promotion, per ZachXBT); a shill from
    them is a reason NOT to buy, not a call to ape. The message cites
    ZachXBT's own post as the source.
    """
    token = _token_label(token_symbol, token_address)
    lines = [
        f"⚠️ <b>SCAMMER WARNING</b> — @{_esc(handle)} shilled {token}",
        "",
        f"Type: {_esc(call_type or 'buy')} · Group: {_group_evidence(group) or _esc(group)}",
        "",
        "These accounts have run coordinated panic-to-scam networks — fake "
        "giveaways and pump-and-dump promotions (e.g. $ORAMAMA). Reason not "
        "to buy anything they post.",
        "",
    ]
    text = str(tweet_text or "").strip()
    if text:
        lines.append(f"Shill: {_esc(text[:200])}")
        lines.append("")
    if source_url:
        lines.append(f'🔗 <a href="{html.escape(source_url, quote=True)}">{_esc(source_url)}</a>')
    lines.append("")
    lines.append("Verify before ape: /scan @rugmunchbot · Source: ZachXBT")
    return "\n".join(lines)


def _captured_within(captured_at: str, hours: float) -> bool:
    """True when a call was captured within the last *hours* (fail-open True)."""
    try:
        ts = datetime.fromisoformat(str(captured_at or "").replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return datetime.now(UTC) - ts.astimezone(UTC) <= timedelta(hours=max(1, hours))
    except (ValueError, TypeError):
        return True  # undated rows stay in the window


def collect_group_shill_calls(
    *,
    window_hours: int = SCAMMER_WARNING_WINDOW_HOURS,
    groups: tuple[str, ...] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Calls from identities in a warning group (handle → identity resolved).

    Reads ``db.kol.list_kol_calls``, resolves each handle to its canonical
    identity (``db.kol.resolve_handle``), and keeps rows whose
    ``scammer_group`` is in the warning set and that fall inside the window.
    Returns call dicts enriched with ``_group`` and ``_group_handle``.
    Fail-open ``[]``.
    """
    active_groups = set(groups or SCAMMER_WARNING_GROUPS)
    if not active_groups:
        return []
    try:
        from rugmunchbot.db import list_kol_calls, resolve_handle
    except Exception as exc:  # fail-open by contract
        logger.warning("scammer_warning:db_import_failed err=%s", str(exc)[:120])
        return []
    out: list[dict[str, Any]] = []
    try:
        rows = list_kol_calls(limit=max(1, limit))
    except Exception as exc:  # fail-open by contract
        logger.warning("scammer_warning:list_calls_failed err=%s", str(exc)[:120])
        return []
    for row in rows or []:
        handle = str(row.get("handle") or "").strip().lstrip("@")
        if not handle:
            continue
        if not _captured_within(str(row.get("captured_at") or ""), window_hours):
            continue
        try:
            identity = resolve_handle(handle)
        except Exception:  # fail-open — unknown handle skips, never raises
            logger.debug("scammer_warning:resolve_failed handle=%s", handle, exc_info=True)
            continue
        if not identity:
            continue
        group = str(identity.get("scammer_group") or "")
        if group not in active_groups:
            continue
        item = dict(row)
        item["_group"] = group
        item["_group_handle"] = str(identity.get("kol_id") or handle)
        out.append(item)
    return out


def is_high_signal(call: dict[str, Any]) -> bool:
    """High-signal shill = classified shill/rug (clear pump/dump language)."""
    return str(call.get("call_type") or "").strip().lower() in _HIGH_SIGNAL_TYPES


async def post_scammer_warning(bot: Any, call: dict[str, Any]) -> dict[str, Any]:
    """Post one warning to alerts (+ main when high signal, mirrored to feed).

    Routing (matches exploit-alert doctrine):
      * alerts — every flagged-account shill (the warning always posts)
      * main   — high-signal only (shill/rug call type), mirrored to
        feed.rugmunch.io via ``mirror_post_to_feed``

    Returns ``{handle, token, routes: {alerts, main, feed}}``. Fail-open:
    unresolved channel ids and failed sends log and skip — a warning never
    crashes the tick.
    """
    from rugmunchbot.channels.scheduler._core import _safe_send
    from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed
    from rugmunchbot.config import _resolve_channel_id

    handle = str(call.get("handle") or "unknown")
    token_symbol = str(call.get("token_symbol") or "")
    token_address = str(call.get("token_address") or "")
    text = format_scammer_warning(
        handle=handle,
        token_symbol=token_symbol,
        token_address=token_address,
        call_type=str(call.get("call_type") or "buy"),
        tweet_text=str(call.get("tweet_text") or ""),
        source_url=str(call.get("source_url") or ""),
        group=str(call.get("_group") or ""),
    )
    # Canonical story CTA (scam section) — one link row, never a naked link.
    text = attach_story_cta(text, call, "scam")
    result: dict[str, Any] = {
        "handle": handle,
        "token": token_symbol or token_address,
        "routes": {"alerts": False, "main": False, "feed": False},
    }

    # 1) alerts — always (this is the rug-alerts lane).
    alerts_chat = _resolve_channel_id("alerts")
    if alerts_chat:
        result["routes"]["alerts"] = await _safe_send(bot, alerts_chat, text, content_type="alert")
    else:
        logger.warning("scammer_warning_skipped reason=no_chat_id channel=alerts")

    # 2) main — high-signal only, mirrored to feed.rugmunch.io.
    if is_high_signal(call):
        main_chat = _resolve_channel_id("main")
        if main_chat:
            from rugmunchbot.channels.formatters._primitives import (
                NewsPost,
                enforce_site_link,
            )
            from rugmunchbot.support.image_policy import resolve_post_image
            from rugmunchbot.support.quality_gate import pre_post_quality_gate

            main_text = enforce_site_link(text, site="feed")
            # HARD quality gate + photo-aware delivery (2026-08-31): the main
            # route is a flagship channel post — gate → autofix → photo
            # (branded placeholder when no og:image), never naked text.
            resolved_image = await resolve_post_image(
                "main",
                image_url=call.get("image_url") or call.get("image") or "",
                source_url=str(call.get("source_url") or ""),
                logger=logger,
            )
            gate = await pre_post_quality_gate(
                main_text,
                image_url=resolved_image,
                source=handle[:80],
                kind="scammer_warning:main",
                min_words=8,
                require_image=True,
                allow_placeholder_image=True,
                logger=logger,
            )
            if gate.ok:
                from rugmunchbot.channels.scheduler._core import _post_photo_aware

                post = NewsPost(gate.text)
                post.image_url = gate.image_url
                result["routes"]["main"] = await _post_photo_aware(
                    bot, main_chat, post, _safe_send, content_type="alert"
                )
            else:
                logger.warning(
                    "scammer_main_gated handle=%s violations=%d",
                    handle[:60],
                    len(gate.violations),
                )
        else:
            logger.warning("scammer_warning_skipped reason=no_chat_id channel=main")
        slug = mirror_post_to_feed(
            {
                "text": text,
                "story_ref": str(call.get("source_url") or ""),
                "source": "zachxbt",
                "kind": "scammer_warning",
            }
        )
        result["routes"]["feed"] = slug is not None
        if slug is None:
            logger.warning("scammer_warning_feed_mirror_failed handle=%s", handle)

    return result


async def run_scammer_warning_watch(ctx: Any = None) -> int:
    """Sweep recorded KOL calls → warn on flagged-group shills (deduped).

    Returns the number of warnings posted (0..``SCAMMER_WARNING_MAX_ALERTS``).
    Dedupe is per handle+token per calendar day (global ``_remember``
    namespace), so a campaign warns once a day, never every tick. Fail-open
    at every stage.
    """
    from rugmunchbot.channels.scheduler._core import _release, _remember

    if not SCAMMER_WARNING_ENABLED:
        return 0

    calls = collect_group_shill_calls()
    if not calls:
        logger.debug("scammer_warning:no_group_calls")
        return 0

    today = datetime.now(UTC).strftime("%Y%m%d")
    posted = 0
    for call in calls[:SCAMMER_WARNING_MAX_ALERTS]:
        handle = str(call.get("handle") or "")
        token = str(call.get("token_symbol") or call.get("token_address") or "")
        dedupe_key = f"scammer_warning:{handle}:{token}:{today}"
        if not _remember("scammer_warning", dedupe_key):
            continue
        if ctx is None:
            # No bot handle (manual/dry-run use) — release and skip.
            _release("scammer_warning", dedupe_key)
            continue
        result = await post_scammer_warning(ctx.bot, call)
        if result["routes"]["alerts"] or result["routes"]["main"]:
            posted += 1
            logger.info(
                "scammer_warning:alerted handle=%s token=%s routes=%s",
                handle,
                result["token"][:40],
                result["routes"],
            )
        else:
            _release("scammer_warning", dedupe_key)
    logger.info(
        "scammer_warning:run_done calls=%s posted=%s",
        len(calls),
        posted,
    )
    return posted


__all__ = [
    "SCAMMER_WARNING_ENABLED",
    "SCAMMER_WARNING_GROUPS",
    "SCAMMER_WARNING_INTERVAL_MIN",
    "SCAMMER_WARNING_MAX_ALERTS",
    "SCAMMER_WARNING_WINDOW_HOURS",
    "collect_group_shill_calls",
    "format_scammer_warning",
    "is_high_signal",
    "post_scammer_warning",
    "run_scammer_warning_watch",
]
