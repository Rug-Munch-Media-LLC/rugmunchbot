"""Scan cards + alpha posts for the bot-owned Telegram channels.

Modularized from the former single-module ``_alpha.py`` (2026-10-23). This
``__init__`` is a pure re-export shim: every public name and signature is
unchanged, and callers keep importing ``channels.formatters._alpha`` (and
``channels.formatters``) exactly as before.

Layout:
  * ``_lines.py``       — small value formatters (usd/age/wallet/tx/time/verdict)
  * ``_move.py``        — whale-move rendering
  * ``_sections.py``    — smart-money / launches / KOL section rendering
  * ``_scan_signal.py`` — the honest signal lines on a scan card
  * ``_scan_card.py``   — ``format_scan_card``
  * ``_post.py``        — ``format_alpha_post``
"""

from __future__ import annotations

from rugmunchbot.channels.formatters._alpha._lines import (
    _TX_EXPLORER as _TX_EXPLORER,
    _fmt_age_hours as _fmt_age_hours,
    _fmt_move_time as _fmt_move_time,
    _fmt_usd as _fmt_usd,
    _fmt_wallet_short as _fmt_wallet_short,
    _launch_verdict as _launch_verdict,
    _tx_url as _tx_url,
)
from rugmunchbot.channels.formatters._alpha._move import (
    _whale_move_lines as _whale_move_lines,
    render_whale_moves as render_whale_moves,
)
from rugmunchbot.channels.formatters._alpha._post import format_alpha_post as format_alpha_post
from rugmunchbot.channels.formatters._alpha._scan_card import format_scan_card as format_scan_card
from rugmunchbot.channels.formatters._alpha._scan_signal import (
    _scan_signal_lines as _scan_signal_lines,
)
from rugmunchbot.channels.formatters._alpha._sections import (
    render_kol_trades as render_kol_trades,
    render_launches as render_launches,
    render_smart_money as render_smart_money,
)

__all__ = [
    "_TX_EXPLORER",
    "_fmt_age_hours",
    "_fmt_move_time",
    "_fmt_usd",
    "_fmt_wallet_short",
    "_launch_verdict",
    "_scan_signal_lines",
    "_tx_url",
    "_whale_move_lines",
    "format_alpha_post",
    "format_scan_card",
    "render_kol_trades",
    "render_launches",
    "render_smart_money",
    "render_whale_moves",
]
