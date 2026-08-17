"""
RugMunchBot — scheduled channel content (news / alerts / alpha / scans / personal).

Wire-up for the bot-owned Telegram channels (``config.CHANNELS``): each
channel gets its own content formatter and a JobQueue-based poster that runs
on an env-configurable cadence (``channels/scheduler.py``).

Channels
--------
* ``news``   — curated headlines from our backend ``/api/v1/news/trending``.
* ``alerts`` — scanner risk-alert digest (high-risk launches / honeypot+rug
               flags) from ``/api/v1/launches/alerts``. Event-driven scanner
               alerts already reach this channel via ``alert_hooks``; this
               job adds a REGULAR digest so the channel stays active.
* ``alpha``  — whale movements (helius WhaleWatcher via
               ``app.domains.alerts.whale_watch`` when populated) + smart
               money wallets (backend ``/api/v1/databus/premium/smart-money``)
               + new launches (``/api/v1/launches/feed``).
* ``scans``  — @rmiscans "scans of note" ~10/day: the day's most notable
               scanned tokens (launch-alert ≥70 rail + scan history),
               reranker-gated (skip <0.4), one compact card per token
               (verdict + risk score + token + chain + scan link).
* ``personal`` — the operator's @cryptorugmuncher channel: reranker-gated
               "bigger stories" (threshold 0.7) posted as operator-voice
               captions + cross-posted to the Ghost blog (Admin API) — see
               ``channels/personal_channel.py`` (opt-in via
               ``RMI_PERSONAL_CHANNEL_ENABLED``).

Every job fails open: a missing channel id, backend outage, or malformed
item logs and skips — it never crashes the bot's JobQueue.
"""

from app.domains.telegram.rugmunchbot.channels.personal_channel import (
    PERSONAL_CHANNEL_ENABLED,
    PERSONAL_CHANNEL_INTERVAL_MINUTES,
    PERSONAL_CHANNEL_THRESHOLD,
    post_personal_channel,
)
from app.domains.telegram.rugmunchbot.channels.scheduler import (
    post_alert_update,
    post_alpha_update,
    post_news_update,
    post_scan_update,
    register_channel_jobs,
)

__all__ = [
    "PERSONAL_CHANNEL_ENABLED",
    "PERSONAL_CHANNEL_INTERVAL_MINUTES",
    "PERSONAL_CHANNEL_THRESHOLD",
    "post_alert_update",
    "post_alpha_update",
    "post_news_update",
    "post_personal_channel",
    "post_scan_update",
    "register_channel_jobs",
]
