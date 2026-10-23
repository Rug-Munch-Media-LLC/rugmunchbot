# Command Status Table

> Generated 2026-08-31 from `bot/_entry.py` + `register_*` helpers.
> **Menu = source of truth**: only commands in `_COMMAND_SECTIONS`
> (`bot/_commands.py`) are advertised via BotFather + /menu. The startup
> check `check_menu_coverage()` logs `MENU_DRIFT` / `HELP_DRIFT` loudly when
> the registry and menu drift apart (log-only, never crashes startup).

Counts: **97 registered** · **51 advertised in menu** · **46 unadvertised**
(admin/owner, aliases, auxiliary — deliberate).

Status legend: **working** = fully functional · **stub** = registered but
returns placeholder/limited output · **owner** = owner/admin-gated.

## Advertised (menu) commands — 51

| Command | Status | Note |
|---|---|---|
| /start | working | Welcome + quick start |
| /help | working | First-run explainer (v14) |
| /channels | working | Static channel directory |
| /feed | working | Static feed.rugmunch.io pointer |
| /site | working | Static rugmunch.io pointer |
| /scan | working | v2 backend scan + enrichment + prior-intel recall |
| /deepscan | working | Full forensic scan + prior-intel recall |
| /wallet | working | Wallet forensics |
| /price | working | Price by address |
| /chart | working | Chart + TA |
| /trace | working | Cross-chain fund trace |
| /market | working | Market overview |
| /news | working | News feed (internal → backend → CoinGecko fallback) |
| /tweet | working | Read X post via ApiPass |
| /tweets | working | Recent X posts from account |
| /alerts | working | Price alert list (active + recently triggered) |
| /chat | working | Freestyle AI chat |
| /volume | working | Wash-trading check |
| /exchangeaudit | working | Exchange wash-trading ranking |
| /liqmap | working | Cross-venue liquidation map |
| /hlwhales | working | Hyperliquid whale flow |
| /dislocation | working | Funding dislocation monitor |
| /dune | working | Curated Dune queries |
| /status | working | Data-source tier health |
| /trending | working | Hot tokens |
| /movers | working | 24h gainers/losers |
| /launches | working | New launch feed |
| /whales | working | Whale moves feed |
| /yield | working | DeFi yield opportunities |
| /defi | working | DeFi protocol analytics |
| /polymarket | working | Prediction market odds |
| /election | working | Election odds |
| /odds | working | Sports/event odds |
| /tokeninfo | working | Token DNA fingerprint |
| /cascadewatch | working | Liquidation cascade risk |
| /portfolio | working | Wallet holdings overview |
| /lpcheck | working | LP health & lock check |
| /rugcheck | working | Quick rug-pull check |
| /honeypot | working | Honeypot simulation |
| /deployer | working | Deployer fingerprint |
| /holders | working | Holder distribution |
| /smartmoney | working | Smart-money tracking |
| /sentinelscan | working | SENTINEL deep forensic scan |
| /investigate | working | Full investigation dossier |
| /whale | working | Single whale deep-dive |
| /mev | working | MEV/sandwich exposure |
| /compare | working | Side-by-side token compare |
| /simulate | working | Buy/sell simulation |
| /research | working | Deep research report |
| /sentiment | working | Social sentiment breakdown |
| /dex | working | DEX pair & liquidity lookup |

## Unadvertised (registered, deliberate) — 46

| Command | Status | Note |
|---|---|---|
| /menu | working | Capability atlas (onboarding surface) |
| /faq | working | FAQ card |
| /account | working | Tier, weekly usage, balances, all-time stats |
| /pricing | working | Stars pricing plans |
| /topup | working | Bonus-usage top-up menu |
| /buystars | working | Stars purchase info |
| /scamschool | working | Scam-education topic menu |
| /refer | working | Referral link + milestones |
| /hype | working | Hype score |
| /ta | working | Technical analysis |
| /quick | working | Quick scan variant |
| /rugmap | working | Cluster rugmap |
| /events | working | Token events feed |
| /impersonation | working | Impersonation check |
| /deathclock | working | Token death-clock estimate |
| /cluster | working | Wallet clustering |
| /laundry | working | Mixer/laundry exposure |
| /lphealth | working | Alias of /lpcheck (same backend route) |
| /osint | working | OSINT lookup (owner-gated surface) |
| /osintall | working | Full OSINT sweep |
| /track | working | OSINT tracking |
| /email | working | OSINT email lookup |
| /phone | working | OSINT phone lookup |
| /face | working | OSINT face search |
| /watchlist /watch /unwatch | working | Watchlist management |
| /monitor /monitors /unmonitor | working | Token monitors |
| /alert | working | Create/update an alert: `/alert ABOVE\|BELOW\|CHANGE_5\|ANY <address> [price]` |
| /alert delete | working | Delete your alert for one token: `/alert delete <address>` |
| /report | working | User report submission |
| /ask | working | One-shot AI question |
| /dumps | working | Recent dump tracing |
| /flow | working | Fund-flow tracing |
| /webhook | working | Webhook management |
| /api | working | API key info |
| /mygroup /groupinfo /voicechat /banuser | working | Group-management tools |
| /rpcstats | owner | RPC pool health |
| /admin | owner | Admin panel |
| /admin_stats /credits /users | owner | Fleet analytics |
| /admin_set_tier /admin_broadcast /admin_toggle /admin_toggle_chat | owner | User/tier admin |
| /admin_user /admin_ban /admin_unban /admin_logs | owner | User admin |

## Known gaps

- No registered command is currently a hard stub; the former "Premium plans
  coming soon" placeholder line in /account was removed (2026-08-31).
- /code appears in help/menu prose but is NOT registered — flagged by the
  HELP_DRIFT startup check; either register or reword the copy. [REVIEW]
- Menu drift is now machine-checked at startup (`check_menu_coverage` in
  `bot/_commands.py`, called from `setup_bot_profile`).
