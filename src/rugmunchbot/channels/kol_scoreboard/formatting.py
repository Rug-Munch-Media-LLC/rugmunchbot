"""KOL Scoreboard — Telegram HTML formatting/rendering.

Split from the former monolithic ``kol_scoreboard.py``: the report-card
block, the scoreboard post, and the Hall-of-Shame subset post.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rugmunchbot.channels.kol_scoreboard.data import KolReportCard


def format_report_card(card: KolReportCard) -> str:
    """One KOL's report card as an HTML block (the scoreboard render unit)."""
    handle = card.handle or "unknown"
    name = card.name if card.name and card.name != handle else ""
    head_name = f" {name}" if name else ""
    badge = " 🚨 <b>REPEAT SCAMMER</b>" if card.is_repeat_scammer else ""
    group_tag = ""
    if card.scammer_group:
        from rugmunchbot.kol_registry_seed import group_label

        group_tag = f" [<i>{group_label(card.scammer_group)}</i>]"
    head = f"🧾 <b>KOL report card: @{handle}</b>{head_name} — grade {card.grade}{badge}{group_tag}"
    lines = [
        head,
        f"• calls: {card.total_calls}  (wins {card.wins} / losses {card.losses}, win-rate {card.win_rate:.0%})",
        f"• deleted calls: {card.deleted_calls}",
        f"• shill/rug calls: {card.shill_calls}",
        f"• tokens on scam denylist: {card.scam_token_calls}",
        f"• tokens mentioned: {card.tokens_mentioned}",
    ]
    if card.category:
        lines.append(f"• category: {card.category}")
    if card.name_changes:
        lines.append(f"• screen-name changes: {card.name_changes}")
    if card.wallets:
        shown = card.wallets[:3]
        rendered = []
        for w in shown:
            short = w["address"][:8] + "…" + w["address"][-6:]
            meta = "/".join(x for x in (w.get("chain"), w.get("confidence")) if x)
            rendered.append(f"<code>{short}</code>{' (' + meta + ')' if meta else ''}")
        more = f" +{len(card.wallets) - len(shown)} more" if len(card.wallets) > len(shown) else ""
        lines.append(f"• traced wallets ({len(card.wallets)}): {', '.join(rendered)}{more}")
    elif card.wallet_address:
        short = card.wallet_address[:8] + "…" + card.wallet_address[-6:]
        lines.append(f"• wallet: <code>{short}</code>")
    lines.append(f"• score: <b>{card.score:.1f}</b>")
    return "\n".join(lines)


def format_scoreboard_post(cards: list[KolReportCard]) -> str:
    """HTML scoreboard post (worst-first; the main product render)."""
    if not cards:
        return ""
    lines = ["🏆 <b>KOL SCOREBOARD</b> — worst KOL report cards right now", ""]
    for card in cards[:5]:
        entry = f"@{card.handle} — grade <b>{card.grade}</b> (score {card.score:.0f})"
        if card.scammer_group:
            from rugmunchbot.kol_registry_seed import group_label

            entry += f" [{group_label(card.scammer_group)}]"
        detail = []
        if card.deleted_calls:
            detail.append(f"{card.deleted_calls} deleted")
        if card.wins or card.losses:
            detail.append(f"{card.wins}W/{card.losses}L")
        if card.shill_calls:
            detail.append(f"{card.shill_calls} shills")
        if card.scam_token_calls:
            detail.append(f"{card.scam_token_calls} scam-token calls")
        if card.wallets:
            n = len(card.wallets)
            detail.append(f"{n} wallet{'s' if n != 1 else ''} traced")
        if card.is_repeat_scammer:
            detail.append("repeat scammer")
        if detail:
            entry += f" — {', '.join(detail)}"
        lines.append(entry)
    lines.append("")
    lines.append("Hall of Shame = repeat scammers only. Verify before ape: /scan @rugmunchbot")
    return "\n".join(lines)


def format_hall_of_shame_post(cards: list[KolReportCard]) -> str:
    """HTML post for the Hall of Shame SUBSET (repeat scammers only).

    Group-attributed KOLs show their syndicate — the Hall is where the
    scammer-group is surfaced to the channel.
    """
    if not cards:
        return ""
    lines = ["🪦 <b>KOL HALL OF SHAME</b> — repeat scammers only", ""]
    for card in cards[:5]:
        entry = f"@{card.handle} — grade <b>{card.grade}</b> (score {card.score:.0f})"
        if card.scammer_group:
            from rugmunchbot.kol_registry_seed import group_label

            entry += f" [<i>{group_label(card.scammer_group)}</i>]"
        detail = []
        if card.deleted_calls:
            detail.append(f"{card.deleted_calls} deleted")
        if card.scam_token_calls:
            detail.append(f"{card.scam_token_calls} scam-token calls")
        if card.shill_calls:
            detail.append(f"{card.shill_calls} shills")
        if card.wallets:
            n = len(card.wallets)
            detail.append(f"{n} wallet{'s' if n != 1 else ''} traced")
        if detail:
            entry += f" — {', '.join(detail)}"
        lines.append(entry)
    lines.append("")
    lines.append(
        "Repeat deletions + scam-token calls = bad-faith pattern. Verify before ape: /scan @rugmunchbot"
    )
    return "\n".join(lines)
