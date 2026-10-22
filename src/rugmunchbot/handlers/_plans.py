"""/plans — free vs Pro, stated honestly (surface only, no gating).

The actual tier gating lives in ``db`` / ``config.TIERS`` (another lane). This
command only tells the truth about it:

* While ``config.BETA_MODE`` is on, NOTHING is blocked — saying "Pro required"
  would be a fake paywall, so we say the opposite and mean it.
* When billing goes live the same text switches to the real quotas from
  ``config.TIERS`` (one source of truth for numbers — never retyped here).

Purchase paths stay in ``/pricing`` (Stars packs); ``/plans`` never sells.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.config import BETA_MODE, TIERS
from rugmunchbot.formatters import sep, thin_sep

#: Order plans the way a user evaluates them (free first, then ladder).
PLAN_ORDER = ("free", "scout", "hunter", "pro")


def plans_text() -> str:
    """Render the plan card (HTML): what is free now, what changes later."""
    free = TIERS["free"]
    lines = [
        f"\U0001f510 <b>Plans \u2014 the honest version</b>\n{sep()}",
    ]
    if BETA_MODE:
        lines.append(
            "\U0001f680 <b>Free public beta</b> \u2014 every feature is unlocked "
            "right now. No paywall, no upsell wall, nothing held back while we "
            "test.\n"
        )
    else:
        lines.append(
            "<b>Billing is live.</b> The free tier below is what you get "
            "without paying; nothing is hidden behind a fake limit.\n"
        )

    lines.append(f"\u2705 <b>Free \u2014 always</b>\n{thin_sep()}")
    lines.append(
        f"\u2022 {free['scans_per_week']} scans/week\n"
        f"\u2022 {free['ai_msgs_per_week']} AI questions/week\n"
        "\u2022 Risk score, contract safety, liquidity, top holders\n"
        "\u2022 Price, charts, news, /desk, /more \u2014 the whole command surface\n"
        "\u2022 Alerts and watchlists"
    )

    paid = [key for key in PLAN_ORDER if key != "free" and key in TIERS]
    if paid:
        lines.append(f"\n\u23f3 <b>Paid \u2014 when billing turns on</b>\n{thin_sep()}")
        for key in paid:
            tier = TIERS[key]
            price = tier.get("price_monthly")
            price_txt = f"${price:,.2f}/mo" if isinstance(price, (int, float)) else "TBA"
            extras = [
                feature
                for feature in tier.get("features", [])
                if "scans/week" not in feature and "questions/week" not in feature
            ][:3]
            lines.append(
                f"{tier['emoji']} <b>{tier['name']}</b> \u2014 {price_txt} "
                f"\u00b7 {tier['scans_per_week']} scans/week "
                f"\u00b7 {tier['ai_msgs_per_week']} AI/week"
            )
            for feature in extras:
                lines.append(f"   \u2022 {feature}")
        lines.append(
            "\n<i>No fake countdown, no locked button you can see but cannot "
            "press. If a limit ever applies, the bot tells you before it "
            "blocks you.</i>"
        )

    lines.append(
        f"\n{sep()}\n\U0001f4b3 Buy Stars / top up: /pricing \u00b7 /topup\n"
        "\U0001f4ca Your usage and tier: /account"
    )
    return "\n".join(lines)


async def cmd_plans(update: Any, _ctx: Any) -> None:
    """/plans — free vs Pro with no invented paywall."""
    message = getattr(update, "effective_message", None)
    if message is None:
        return
    await message.reply_text(plans_text(), parse_mode="HTML", disable_web_page_preview=True)


__all__ = ["PLAN_ORDER", "cmd_plans", "plans_text"]
