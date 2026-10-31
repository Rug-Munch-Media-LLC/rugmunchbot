# Live recon digest — 16 competitor bots (2026-10-31)

Method: our user session (Telethon), per bot: profile + command scope + `/start` +
SAFE-labeled inline-button clicks (≤6/pass) + `/help`. Never: trades, wallet ops,
ToS accept, payments, joins. FloodWait honored; 3–8 s pacing. Raw JSON:
`bot-recon/ (fleet knowledge store):<handle>.json`; scan outputs: `scan-<handle>.json`;
Phanes deep pass: `phanes-captures.md`. The bot = the label; `id` = Telegram bot id.

## Trading / hub bots

### ThorSolana_bot (id 7572873387) — Odin "Thor"
/start: ToS → wallet-protection choice (SIP 🔒 vs no protection ⚡️), language EN.
Buttons: ✅ Accept / ❌ Decline / 🌐 Language (EN) — Accept NOT clicked (no ToS
agreement on the user's behalf). Full capability set documented at docs.odin.tools
(see gaps-2026-10-31.md): 6 chains, spot/perps/PolyMarket, USD Cash, copy trade,
trenches autotrade, PnL cards, $100/30d 0%-fee plan, 5-level referral.

### odin_tools_bot (id 7245707183) — the ecosystem hub
/start: "⛓ Choose a chain — Your wallets, balances and bots are separate per chain":
⛓ Solana·SOL / Ethereum·ETH / BNB·BNB / Base·ETH / Robinhood·ETH / Arc·USDC +
🔥 Support / 🛡️ Heimdall / ⚡️ THOR (URL buttons). /help markets the BOOST suite:
🔥 Trending (DEX Screener, DEXTools, GMGN, Phantom & more) · 📈 Chart Maker ("draw a
pattern with real holder wallets") · ⚡ Fast Volume · 🏆 Rank (unique buyers + maker
count) · 👨‍👩‍👧‍👦 Holders · 😱 Reactions (DexScreener emoji reactions) · 🎁 Earn (10%).
→ odin_tools_bot is the marketing/market-manipulation suite; Thor is the trader app.

### SafeguardRobot (id 5434266369) — V5.5
/start: "The ultimate bot for crypto groups! Telegram's best protection & token buy
tracker" — /setup (create portal) · /config (group config) · /add (add token to buy
bot) · /trend (boost token trending). Buttons: 🔈 Announcements / 🔥 Trending / Set
as group admin (all URL). 206,480 monthly users; powers @Trending channel.
= captcha/security portal + buybot + trending marketplace in one bot.

### MajorBuyBot (id 7098195052)
/start: "Enjoy the fastest buy notifications on SOL | ETH | BSC | BASE | HOOD…";
buttons: Buy Bot on Telegram (url) / Buy Stream on X & Pump.fun (callback, skipped).
/help tutorial: /add → ➡️ Choose Chain → send Token Address → select Pair → default
tracking on → /settings to customize. Full feature set: docs.majorbots.io
(whale detection with custom wallet-balance threshold, auto-pin whales, milestone
hype events, welcome DM with project links, voting, DexScreener event hooks, buy
competitions: time mode / spot mode ≤72h, dev-funded escrow, random winner).

### MajorBoostBot (id 6988268550) — the monetization bot
/start: Trending packages incl. Button AD + Mass DM to all BuyBot users; free bumps/
market-maker/holders with every SOL trending ticket. Buttons: Trending / Volume /
Exclusive Ads / Voting Boost / Free Tools / Feedback / Support.

## Scanner bots (one-shot cards)

### Phanes_bot (id 7178305557)
50+ commands in 6 groups (Token Data / Markets & Discovery / Wallets & Holders /
Tools / Personalize / Group). Paste CA or $ticker. Trust copy: "We don't have a
coin / airdrop! … The Phanes Team will never DM you." See scan-methodology doc for
the card anatomy + badge lines.

### the_mugetsu_bot (id 7069767637)
/start: language gate 🇬🇧/🇨🇳. 30+ commands in /help. 2026-10-31: `/top_holders`
now paywalled ("purchase/renew your subscription using /renew") — update to the
2026-10-25 "free" verdict.

### SkeletonPriceBot (id 5386442585)
/start menu buttons: Add Skeleton to your Group / Info & Docs / Features / Setup
Trending Alerts / Premium Subscription / Fasttrack Trending Boost / Create a Buy
Contest / Skeletontrend / Lounge / Multi Events / KolSkull / Promotion Options.

### Skeletonscanbot (id 8113048569)
"Contract & Data Analyzer Bot… add to your lounge or use in DM"; commands: /holders
/top /snipers /first /wd /ad /market; group popup + minichart + per-group /lb.

### PirbViewBot (id 6362041475) / PIRBViewPROBot (id 6371821039)
"Your Unfair Advantage in DeFi… 8 chains — whale movements, insider wallets, bundler
detection, holder analysis; bubble maps, holder overlap & jeet detection; wallet PnL;
PumpFun graduation tracker & DEX trending." Buttons: ➕ Add to Group / 📚 Guide /
🐦 Twitter / 💬 Support Group. Scan card anatomy + full emoji grammar:
see scan-methodology-2026-10-31.md §2.1.

### TrenchRadarBot (id 7079505887)
"unlock your edge in the Solana meme trenches"; /track /details /alerts (filter
loadouts) /predict /guide; premium via @TrenchSubBot. /details is paywalled.

### TrenchScannerBot (id 7615161364) — TrenchRadar's public scanner (same vendor)
Profile links x.com/TrenchRadar + trench.bot. `/clusters [ca]` (cluster risk
analysis, all Solana tokens) + `/ob [ca]` (legacy pump.fun bundle analysis).
Free, no paywall on the one-shot path.

## Moderation bots

### MissRose_bot (id 609517172)
"Rose — group management: flood control, warning system, note keeping, keyword
replies"; buttons: Add me to your chat! / ⭐ Get your own Rose. Full surface per
missrose.org docs (captchas incl. button/text/math/text2 + join-request DM +
captchakick, notes, filters, locks, warns, antiflood/antiraid, federations).

### DevsNightmareProbot (id 7941368212)
"Advanced Solana token analysis assistant": token analysis, team tracking, first
buyers analysis; button: View Tracked Tokens. Pro = REKT suite.

## The former-dev bot

### MarcusRugIntelBot — Marcus Rug Intel (org: github.com/Marcus-Rug-Intel)
README states: "This project was formerly known as CryptoRugMunch." Products:
Telegram bot (/scan on SOL/ETH/Base), browser extension (DexScreener, Pump.fun,
Jupiter, Raydium, BullX, GMGN, Birdeye, Photon), x402 Agent API
(`https://cryptorugmunch.app/api/agent/v1/check-risk`), PyPI `rug-munch-mcp`
(19 tools), `rug-agent-kit` (Coinbase AgentKit plugin), "Marcus Aurelius" LLM
analyst (X sentiment, Polymarket, web search, graph memory, KOL/shill detection,
"Marcus Index" daily risk score), "You Dodged This" DM when a scanned token rugs.
**Status check 2026-10-31:** `cryptorugmunch.app` DNS = dns-parking.com
(Hostinger parking) → API POST returns an ngrok error page → the advertised API is
not live. PyPI tool pricing ($2.50–$12.50 per call via x402) against a dead base.
Live bot capture: `MarcusRugIntelBot.json`.

## Safety record
No trades executed, no wallets created/connected, no ToS accepted, no payments,
no channel joins; only SAFE-labeled navigation callbacks clicked; all captures
read-only. Evidence: per-bot JSON files listed above.
