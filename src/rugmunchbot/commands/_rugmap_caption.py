"""RugMap caption — HTML caption for the /rugmap photo.

Renders the cluster summary produced by ``_rugmap_summary`` into one Telegram
photo caption. Every value is escaped, the AI sentence is the only optional
part, and the result is trimmed at newline boundaries so it stays under
Telegram's 1024-character caption limit. An absent metric prints ``n/a``
followed by the reason — never a guess.
"""

from __future__ import annotations

from html import escape
from typing import Any

#: Telegram rejects photo captions longer than this.
CAPTION_LIMIT = 1024

_RISK_ICON = {"high": "🔴", "medium": "🟠", "low": "🟢"}


def _clusters_block(clusters: list[dict[str, Any]], note: str | None) -> list[str]:
    """Return the 'Top clusters' lines (or the reason there are none)."""
    if not clusters:
        return [f"<b>Top clusters</b> — n/a ({escape(note or 'graph returned no links')})"]
    lines = ["<b>Top clusters</b>"]
    for i, row in enumerate(clusters[:3], 1):
        icon = _RISK_ICON.get(str(row.get("risk") or "unknown"), "⚪")
        share = row.get("share_pct")
        share_txt = f" · {share}% supply" if share else ""
        label = escape(str(row.get("label") or "Linked wallets"))
        lines.append(f"{i}. {icon} {label} · {row.get('wallets', 0)} wallets{share_txt}")
    return lines


def _metric_lines(summary: dict[str, Any], with_ai_summary: bool) -> list[str]:
    """Return the bundle / fresh-wallet / AI lines."""
    bundle = summary.get("bundle") or {}
    fresh = summary.get("fresh") or {}
    ai = summary.get("ai") or {}

    conf = bundle.get("confidence")
    note = escape(str(bundle.get("note") or "no metric"))
    bundle_txt = (
        f"<b>Bundle</b> {conf}% ({note})" if conf is not None else f"<b>Bundle</b> n/a ({note})"
    )

    pct = fresh.get("pct")
    if pct is None:
        fresh_txt = f"<b>Fresh wallets</b> n/a ({escape(str(fresh.get('note') or 'no metric'))})"
    else:
        fresh_txt = f"<b>Fresh wallets</b> {pct}%" + (" ⚠️" if pct > 50 else "")

    verdict = ai.get("verdict")
    if not verdict:
        return [
            bundle_txt,
            fresh_txt,
            f"<b>AI</b> n/a ({escape(str(ai.get('note') or 'no analysis'))})",
        ]

    ai_conf = ai.get("confidence")
    conf_txt = f" {ai_conf}%" if isinstance(ai_conf, (int, float)) else ""
    ai_txt = (
        f"<b>AI</b> {escape(str(verdict))}{conf_txt} · {escape(str(ai.get('source') or 'rmi'))}"
    )
    lines = [bundle_txt, fresh_txt, ai_txt]
    if with_ai_summary and ai.get("summary"):
        lines.append(f"<i>{escape(str(ai['summary'])[:140])}</i>")
    return lines


def build_caption(
    address: str,
    chain: str,
    name: str,
    symbol: str,
    liquidity: float,
    summary: dict[str, Any],
    web_url: str,
) -> str:
    """Build the escaped HTML caption for one /rugmap result."""
    head = [
        f"🕸️ <b>RugMap — {escape(name[:28])} ({escape(symbol[:12])})</b>",
        f"<code>{escape(address)}</code> · {escape(chain or 'unknown')}"
        + (f" · 💧 ${liquidity:,.0f}" if liquidity else ""),
        "",
    ]
    clusters = _clusters_block(summary.get("clusters") or [], summary.get("cluster_note"))
    source = (
        f"<b>Source</b> {escape(str(summary.get('source') or 'unknown'))}"
        f" · {summary.get('node_count', 0)} nodes / {summary.get('link_count', 0)} links"
    )
    tail = [f'🌐 <a href="{escape(web_url)}">Interactive map</a>']

    for with_ai in (True, False):
        caption = "\n".join(
            head + clusters + [""] + _metric_lines(summary, with_ai) + [source] + tail
        )
        if len(caption) <= CAPTION_LIMIT:
            return caption

    # Last resort: no AI sentence, no clusters, trim on a line boundary.
    short = "\n".join(head + _metric_lines(summary, False) + [source] + tail)
    return short[: CAPTION_LIMIT - 1].rsplit("\n", 1)[0]


__all__ = ["CAPTION_LIMIT", "build_caption"]
