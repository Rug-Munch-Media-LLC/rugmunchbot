"""Per-channel HTML content formatters for the bot-owned Telegram channels.

Modularized from the former single-module ``formatters.py`` (2026-08-21).
This ``__init__`` is a pure re-export shim: every public name and signature
is unchanged, and callers keep importing ``channels.formatters`` exactly as
before.

News stays neutral-curated, alpha is high-signal, alerts read like an urgent
warning. Every story carries its canonical source link and attribution;
summary hooks are flattened of raw HTML/markdown before display so no
unrendered markup leaks into a post. Messages stay under Telegram's
4096-char soft limit and always carry the bot footer.

Layout:
  * ``_primitives.py`` — escaping, markup stripping, hooks, stamps, NewsPost
  * ``_news.py``       — news/breaking/feature/hybrid/themed post formatters
  * ``_alerts.py``     — alert verdicts + alerts/escalation posts
  * ``_alpha.py``      — scan cards + alpha posts
  * ``_format_law.py`` — universal format law: the mandatory pre-send pass
"""

from __future__ import annotations

from rugmunchbot.channels.formatters._alerts import (
    _ALERT_VERDICT_FALLBACK as _ALERT_VERDICT_FALLBACK,
    _ALERT_VERDICT_REASONS as _ALERT_VERDICT_REASONS,
    _SCAN_REASON_BITS as _SCAN_REASON_BITS,
    _explorer_url as _explorer_url,
    _item_links as _item_links,
    _scan_reason_bits as _scan_reason_bits,
    _scanner_url as _scanner_url,
    alert_verdict as alert_verdict,
    format_alerts_post as format_alerts_post,
    format_main_escalation as format_main_escalation,
)
from rugmunchbot.channels.formatters._alpha import (
    _fmt_age_hours as _fmt_age_hours,
    _fmt_usd as _fmt_usd,
    _fmt_wallet_short as _fmt_wallet_short,
    _launch_verdict as _launch_verdict,
    format_alpha_post as format_alpha_post,
    format_scan_card as format_scan_card,
)
from rugmunchbot.channels.formatters._format_law import (
    MAX_HEADLINE_CHARS as MAX_HEADLINE_CHARS,
    MAX_PARA_SENTENCES as MAX_PARA_SENTENCES,
    MAX_SAME_EMOJI as MAX_SAME_EMOJI,
    enforce_format_law as enforce_format_law,
)
from rugmunchbot.channels.formatters._news import (
    _story_line as _story_line,
    format_breaking_post as format_breaking_post,
    format_feature_post as format_feature_post,
    format_hybrid_post as format_hybrid_post,
    format_news_post as format_news_post,
    format_scam_digest as format_scam_digest,
    format_themed_post as format_themed_post,
)
from rugmunchbot.channels.formatters._primitives import (
    _BREAKING_SUMMARY_CHARS as _BREAKING_SUMMARY_CHARS,
    _COMMENTARY_HOOK_CHARS as _COMMENTARY_HOOK_CHARS,
    _DIGEST_TAKEAWAY_CHARS as _DIGEST_TAKEAWAY_CHARS,
    _FEATURE_HOOK_CHARS as _FEATURE_HOOK_CHARS,
    _FEATURE_SUMMARY_CHARS as _FEATURE_SUMMARY_CHARS,
    _MARKDOWN_BULLET_RE as _MARKDOWN_BULLET_RE,
    _MARKDOWN_EMPH_RE as _MARKDOWN_EMPH_RE,
    _MARKDOWN_LINK_RE as _MARKDOWN_LINK_RE,
    _MARKUP_TAGS_RE as _MARKUP_TAGS_RE,
    _MAX_BODY_CHARS as _MAX_BODY_CHARS,
    _WHITESPACE_RE as _WHITESPACE_RE,
    NewsPost as NewsPost,
    _clip as _clip,
    _esc as _esc,
    _first_sentence as _first_sentence,
    _is_placeholder as _is_placeholder,
    _lede_after_hook as _lede_after_hook,
    _now_stamp as _now_stamp,
    _pick_image_url as _pick_image_url,
    _risk_emoji as _risk_emoji,
    _scan_channel_shill as _scan_channel_shill,
    _strip_markup as _strip_markup,
    _takeaway as _takeaway,
)
from rugmunchbot.channels.formatters._scam_alert import (
    SEVERITY_FRAMING as SEVERITY_FRAMING,
    format_scam_alert as format_scam_alert,
    is_postable_scam_alert as is_postable_scam_alert,
)

__all__ = [
    "MAX_HEADLINE_CHARS",
    "MAX_PARA_SENTENCES",
    "MAX_SAME_EMOJI",
    "SEVERITY_FRAMING",
    "NewsPost",
    "alert_verdict",
    "enforce_format_law",
    "format_alerts_post",
    "format_alpha_post",
    "format_breaking_post",
    "format_feature_post",
    "format_hybrid_post",
    "format_main_escalation",
    "format_news_post",
    "format_scam_alert",
    "format_scam_digest",
    "format_scan_card",
    "format_themed_post",
    "is_postable_scam_alert",
]
