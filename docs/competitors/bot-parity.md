# Competitive parity — five Telegram bots

Scope: `@SkeletonPriceBot` (+ Skeleton Scan Bot), `@PIRBViewBot` / `@PIRBViewPROBot`,
`@MissRose_bot`, `@heimdall_odintools_bot`, `@the_mugetsu_bot`.
Baseline: our bot, `rugmunchbot` (`COMMANDS.md`, `docs/MODERATION.md`).

Method: vendor documentation only (docs sites, published guide channels, bot
profile pages), read 2026-10-24. No claim is inferred from a screenshot caption
alone, no feature is attributed from reputation. `UNVERIFIED` = not stated in any
source we read; it is **not** a claim of absence. `✗` means documented as absent
or contradicted by the vendor's own docs.

Sources are listed at the end; every row carries refs.

## 1. What each bot actually is

| Bot | Category | Documented surfaces |
|---|---|---|
| SkeletonPriceBot | Group buybot + market tracker + raid/community tooling | 4 chains, launchpads, contract analyzer (separate `@skeletonscanbot`), KOL/whale tracking, trending alerts [4][5][6][10] |
| PIRBViewBot | On-chain scanner, DM-first, with a PRO tier | Holder maps, PnL, floor, overlap, taxes, TA chart buttons [11]-[19] |
| MissRose_bot | Group administration suite | Moderation, greetings, captcha, notes, filters, locks, antiflood/antiraid, federations [20]-[25] |
| heimdall_odintools_bot | Zero-setup anti-scam guard | Global scammer list, message scanning, CA flags; explicitly "no commands, no configuration" [26] |
| the_mugetsu_bot | On-chain research command set (TG + Discord) | 16 commands; holders, bundles, funding graph, wallet analysis [1][2][3] |

## 2. Axis A — scanning, holders, bundles

| Capability | Skeleton | PIRBView | Mugetsu | Heimdall | MissRose | **rugmunchbot** |
|---|---|---|---|---|---|---|
| Paste-CA contract scan | ✓ scan bot, SOL+EVM [7] | ✓ [11] | ✗ (command-per-question) [1] | ~ flags CAs only [26] | ✗ | ✓ `/scan` `/deepscan` |
| Safety flags (mint/freeze/hidden owner/proxy/tax) | ✓ EVM deep set [7] | ~ taxes only documented [11] | ~ via `/check_dex` [1] | ~ "suspicious/unverified" flag [26] | ✗ | ✓ `/rugcheck` `/honeypot` `/lpcheck` |
| Top holders + wallet wealth tier | ✓ top 20 / top 30 [7] | ✓ top 15 (45 PRO) + emoji tiers [11] | ✓ `/top_holders` [1] | ✗ | ✗ | ✓ `/holders` |
| Holder behaviour map (bought/sold/transferred) | ✗ | ✓ HoldersMap [13] | ✗ | ✗ | ✗ | ✗ |
| Fresh-wallet detection in top holders | ✗ | ✓ [11] | ~ `/fresh` [1] | ✗ | ✗ | UNVERIFIED |
| Airdrop / insider distribution map | ✓ airdrop report (EVM) [7] | ✓ AirdropMap [14] | ✗ | ✗ | ✗ | ✗ |
| **Bundle detection** | ✗ | UNVERIFIED | ✓ `/bundle_check` (BETA, named tx) [2] | ✗ | ✗ | ✗ (internal risk signals only) |
| Snipers + first 20 buyers w/ PnL | ✓ [7] | ✗ | ✗ | ✗ | ✗ | UNVERIFIED |
| Top traders of a token | ✓ top 10 [7] | ✓ top-50 holder PnL (PRO) [16][17] | ✓ `/common_top_traders` [1] | ✗ | ✗ | ~ `/smartmoney` |
| Deployer past projects | ✓ [7] | ✗ | ✗ | ✗ | ✗ | ✓ `/deployer` |
| Wallet PnL by address | ✗ | ✓ `/pnl addr chain` [15] | ✓ `/wallet_analyzer` [1] | ✗ | ✗ | ✓ `/wallet` `/portfolio` |
| Two-token holder overlap | ✗ | ✓ `/o` OverlapMap, top 80 [19] | ✗ | ✗ | ✗ | ✗ |
| Floor analysis (top-10 holder floor) | ✗ | ✓ `/floor` [18] | ~ `/floor` [1] | ✗ | ✗ | ✗ |
| Caller leaderboard per group | ✓ `/lb` [7] | ✓ `/lb` [16] | ✗ | ✗ | ✗ | ✗ |
| Holder-count history | ✓ chart [7] | ✗ | ✓ `/holderscan` (holderscan.org scrape) [3] | ✗ | ✗ | ✗ |

## 3. Axis B — price, market and push alerts

| Capability | Skeleton | PIRBView | Mugetsu | Heimdall | MissRose | **rugmunchbot** |
|---|---|---|---|---|---|---|
| User-set price alert (above/below) | ✗ | UNVERIFIED | ✗ | ✗ | ✗ | ✓ `/alert`, `/alerts` — **tier-gated ≥ Scout** [28] |
| % move / volatility alert | ✗ | UNVERIFIED | ✗ | ✗ | ✗ | UNVERIFIED |
| Whale / LP / tax-change alert subscription | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ backend subs [28] |
| Buy popups in group (buybot) | ✓ threshold, emoji scaling, media, bulk 24, pinned stream [6] | ✗ | ✗ | ✗ | ✗ | ✗ — not registered; specified as P0-2 in `docs/competitors/rugmunch-better.md` |
| KOL buy alerts | ✓ 800+ wallet DB, $ threshold [6][8] | ✗ | ✗ | ✗ | ✗ | ✗ |
| Whale buy notifications | ✓ [6] | ✗ | ✗ | ✗ | ✗ | ✓ `/whales` feed (global, not per-token) |
| Trending / boost alerts opt-in | ✓ boosted + organic [9] | ✗ | ✗ | ✗ | ✗ | ✗ |
| Periodic auto stats popup (3h) | ✓ `/stats`, togglable [5] | ✗ | ✗ | ✗ | ✗ | ✗ |
| Market overview / top coins | ✓ `/market` top 15/100 [7] | ✓ `/market` heatmap, `/fear`, `/gas` [16] | ✗ | ✗ | ✗ | ✓ `/market` `/movers` `/trending` |
| Chart + TA buttons | ~ dex minichart [5] | ✓ TA/MA/EMA/BB/SMI [19] | ✗ | ✗ | ✗ | ✓ `/chart` |
| Raid / community engagement | ✓ raids, queue, leaderboards, buy contests [4] | ✓ calling card `/pl` [16] | ✗ | ✗ | ✗ | ✗ |

## 4. Axis C — moderation (the largest gap)

| Capability | MissRose | Heimdall | Skeleton | Mugetsu | PIRBView | **rugmunchbot** |
|---|---|---|---|---|---|---|
| Custom welcome / goodbye | ✓ `/setwelcome`, fillings, cleanwelcome [20][21] | ✗ | ✗ | ✗ | ✗ | ~ static `welcome` string in per-chat JSON |
| CAPTCHA | ✓ button/text/math/text2, join-request DM, captchakick [21] | ✗ | ✗ | ✗ | ✗ | ✓ button + math, timeout mute/kick [29] |
| Notes / saved replies | ✓ `/save` `/get` `#note` `/notes` `/clear`, attachments, private notes [22] | ✗ | ✗ | ✗ | ✗ | ✗ |
| Filters (auto-reply triggers) | ✓ single/phrase/multi, prefix/exact, `{admin}` `{user}` fillings, `{command}` [23] | ✗ | ✗ | ✗ | ✗ | ✗ |
| Blocklist / blacklist + modes | ✓ words, `blocklistmode` ban/mute/tban [20] | ✗ | ✗ | ✗ | ✗ | ~ scam domains/substrings only |
| Locks (per-media/per-type) | ✓ [20] | ✗ | ✗ | ✗ | ✗ | ✗ |
| Antiflood | ✓ [20] | ✗ | ✗ | ✗ | ✗ | ✓ velocity + dup windows [29] |
| Antiraid | ✓ [20] | ✗ | ✗ | ✗ | ✗ | ✓ `chat_raid` rule |
| Warnings / warn-escalation | ✓ [20] | ✗ | ✗ | ✗ | ✗ | ✗ |
| User reports | ✓ [20] | ✗ | ✗ | ✗ | ✗ | ~ `/appeal` only |
| Purge / clean commands | ✓ `/purge`, clean service/bot msgs [20] | ✗ | ✗ | ✗ | ✗ | ✗ |
| Admin log channel | ✓ [20] | ✗ | ✗ | ✗ | ✗ | ~ append-only JSONL audit, no channel |
| **Global / federated bans** | ✓ federations + fedbans shared across chats [20][25] | ✓ **automatic global scammer list, ban once → blocked everywhere** [26] | ✗ | ✗ | ✗ | ✗ (`propagate_ban` per-chat, default off, `dry_run: true`) |
| Zero-setup protection | ✗ (all opt-in) | ✓ "add as admin, done" [26] | ✗ | ✗ | ✗ | ✗ (new chat = `{"chats": {}}`, untouched) |
| Approvals / join gating | ✓ approvals, join requests [20] | ✓ | ✗ | ✗ | ✗ | ✗ |
| Command disabling | ✓ [20] | ✗ | ✗ | ✗ | ✗ | ✗ |
| Multi-language UI | ✓ [20] | ✗ | ✗ | ✗ | ✗ | ✗ |
| Settings export / import | ✓ [20] | ✗ | ✗ | ✗ | ✗ | ✗ |
| Impersonation / homoglyph names | ✗ | ✓ "fake admins removed" [26] | ✗ | ✗ | ✗ | ✓ `names.py` |

## 5. Axis D — content and intelligence, Axis E — AI chat

| Capability | Skeleton | PIRBView | Mugetsu | MissRose | Heimdall | **rugmunchbot** |
|---|---|---|---|---|---|---|
| Token call / alpha feed | ✓ trending, KolSkull [4] | ✓ `/top10` most-scanned, `/pl` cards [16] | ✗ | ✗ | ✗ | ✓ `/launches` `/trending` `/smartmoney` |
| Scam / exploit alert stream | ~ scanner warnings only [7] | ✗ | ✗ | ✗ | ✓ in-group scam msg deletion [26] | ✓ `channels/scam_alerts`, `exploit_alerts` |
| News | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ `/news` |
| AI chat / free-text assistant | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ `/chat` |
| Deep research / dossier | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ `/research` `/investigate` `/sentinelscan` |
| Sentiment | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ `/sentiment` `/hype` |
| Cross-chain fund trace | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ `/trace` |
| DeFi / prediction markets / yield | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ `/defi` `/polymarket` `/yield` `/odds` |
| Monetization | free bot; PRO/paid tooling UNVERIFIED | token-gate 34,710 PIRB **or** 0.1 ETH/mo [17] | free [1] | free, custom instances [20] | free forever [26] | Stars tiers + top-ups [27] (PIRBView token-gate is the model to study, [17]) |

## 6. Reading of the matrix

1. **We already win axes D and E outright.** None of the five ships AI chat, research
   dossiers, news, cross-chain tracing, or DeFi/prediction-market coverage. That is
   our moat and it needs no parity work.
2. **Axis C is a structural loss, not a feature loss.** MissRose has ~20 moderations
   surfaces we lack entirely (notes, filters, warns, locks, purge, approvals,
   command disabling, export/import, languages) and Heimdall has the one thing
   neither of us can fake per-chat: a **network-effect global ban list that works
   with zero configuration**. Our moderation is stronger per-message
   (impersonation folding, address verdicts, NSFW hashes) but ships **inert**
   (`configs/moderation.json` = `{"chats": {}}`, `dry_run: true`) [29].
3. **Axis A: we are close, but three specific artifacts are missing** — bundle
   detection surfaced as a command, holder behaviour/fresh-wallet maps, and
   overlap/floor analysis. These are cheap for us because the signals already
   exist in `_vendor/rug_risk_*` and `holder_mega_sampler`.
4. **Axis B: our alerting is deeper but narrower.** We have real price alerts and
   backend LP/whale/tax subscriptions; they have group-native buy popups with
   bulk batching, KOL alerts and a 3-hour stats pulse. Both matter for retention
   inside a token's own group.

## Sources

| Ref | URL | Used for |
|---|---|---|
| [1] | https://mugetsu.gitbook.io/mugetsu/llms.txt | Mugetsu command inventory |
| [2] | https://mugetsu.gitbook.io/mugetsu/mugetsu/bundle_check-beta.md | bundle detection, BETA, tx-level output |
| [3] | https://mugetsu.gitbook.io/mugetsu/mugetsu/holderscan.md | holder-growth source + coverage limit |
| [4] | https://docs.skeletonecosystem.com/skeleton-ecosystem/features/commands | SkeletonPriceBot command origins (DM/group/admin) |
| [5] | .../features/multi-purpose-telegram-bot/price-market-tracker | `/stats`, 3h auto-popup, fields |
| [6] | .../features/multi-purpose-telegram-bot/advanced-buybot-tools | buy popups, bulk 24, pinned stream, KOL/whale alerts |
| [7] | .../features/multi-purpose-telegram-bot/contract-scanner.md | scan report, holders, snipers, first-20, airdrop, EVM flags, `/lb` |
| [8] | .../features/multi-purpose-telegram-bot/kol-tracking | KOL threshold alerts via `/settings` |
| [9] | .../features/multi-purpose-telegram-bot/trendig-alerts-feature | boosted/organic trending alerts opt-in |
| [10] | https://t.me/SkeletonPriceBot | profile: multichain buybot, 17,613 monthly users |
| [11] | https://t.me/s/PIRBViewGuide/7 | Top-15/Top-45 holders, fresh-wallet indicator |
| [12] | https://t.me/s/PIRBViewGuide/8 | WhaleMap emoji net-worth tiers |
| [13] | https://t.me/s/PIRBViewGuide/9 | HoldersMap buy/sell/transfer states |
| [14] | https://t.me/s/PIRBViewGuide/10 | AirdropMap |
| [15] | https://t.me/s/PIRBViewGuide/13 | `/pnl addr chain`, 5 chains |
| [16] | https://t.me/s/PIRBViewGuide/14 | `/gas` `/fear` `/market` `/top10` `/lb` `/pl` |
| [17] | https://t.me/s/PIRBViewGuide/15 | PRO: 0.05% PIRB or 0.1 ETH/month |
| [18] | https://t.me/s/PIRBViewGuide/29 | `/floor` Solana top-10 |
| [19] | https://t.me/s/PIRBViewGuide/30 | `/o` OverlapMap top-80 |
| [20] | https://missrose.org/docs/ | full feature index (moderation, greetings, anti-spam, federations, filters, notes, exports, topics) |
| [21] | https://missrose.org/docs/anti-spam/captchas/ | captcha modes, join requests, captchakick |
| [22] | https://missrose.org/docs/notes/ | notes incl. attachments, private notes |
| [23] | https://missrose.org/docs/filters/ | filter syntax, fillings, prefix/exact |
| [24] | https://missrose.org/docs/moderation/restrictions/ | ban/tban/dban/sban families |
| [25] | https://missrose.org/docs/federations/ (nav: fedinfo, creating, managing, joining, fedbans) | federated ban network |
| [26] | https://docs.odin.tools/tools/heimdall-protection-bot | global scammer list, message scanning, CA flags, zero setup, free forever |
| [27] | repo `COMMANDS.md` (2026-08-31) | our advertised command set |
| [28] | `src/rugmunchbot/handlers/alerts/_commands.py` | `/alert` + `/alerts`, Scout-gate, backend subs |
| [29] | `docs/MODERATION.md`, `configs/moderation.json` | our moderation modules, defaults, dry_run |
