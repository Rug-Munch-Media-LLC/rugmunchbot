# RugMunchBot — FAQ

**What is RugMunchBot?** The Telegram bot for the Rug Munch Intelligence platform: /scan, /portfolio, price/security alerts, inline queries.

**How do I run it?** Set the bot token via your secret store, `docker compose up -d`. Systemd hardening units ship under deploy/.

**What commands exist?** /start /help /scan /portfolio /chart /alerts + inline queries. Command reference: docs/.

**Where do alerts come from?** The backend alert pipeline; the bot subscribes and fans out to chats. Configure thresholds per chat.

**Can I self-host with my own backend?** Yes — point it at any RMI backend instance via env.

**Rate limits?** Flood control is built in (per-chat queues); respect Telegram Bot API limits.
