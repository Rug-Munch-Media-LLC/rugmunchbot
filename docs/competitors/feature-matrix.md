# Competitor Feature Matrix — crypto Telegram bots (teardown, 2026-10-23)

Scope: bots that either (a) scan/curate tokens inside Telegram, or (b) post buy/sell
cards or moderate crypto groups. Every cell carries a ref `[n]`; `UNVERIFIED` means we
did not reach a primary source. Collected 2026-10-23.

## 1. Evidence table

| Ref | Source | What it proves |
|---|---|---|
| [1] | https://mugetsu.gitbook.io/mugetsu | Mugetsu command set, chains, absence of trading |
| [2] | https://docs.phanes.bot/phanes/commands | Phanes command set, charts, copy-trade redirect, 19 languages |
| [3] | https://docs.gmgn.ai/index/gmgn-telegram-bot-list | GMGN bot roster per chain (SOL/ETH/Base/BSC/Tron), alert bots |
| [4] | https://docs.gmgn.ai/index/gmgn-tg-sniper-bot-sol.md | GMGN sniper-bot buttons: Buy/Sell, Limit orders, Assets, Wallet, Copy Signal, Copy Trade, Pump FOMO |
| [5] | https://docs.gmgn.ai/index/trending.md | GMGN trending fields, intervals, NextBC blue-chip index |
| [6] | t.me/PIRBViewGuide/2 (index), /7 (Top-15 holders), /8 (WhaleMap) | PIRBView scan modules, PRO split, emoji tiers |
| [7] | https://pirb.tech/ | PIRBView / PRO / PIRBX trading bot, fee cashback, chain claim |
| [8] | https://docs.trench.bot/ + /sitemap-pages.xml | Trench Radar doc map (bot = @TrenchSubBot) |
| [9] | https://docs.trench.bot/filters/filter-categories | Filter categories: algorithm scores (Overseer model), whales, insiders, fresh wallets |
| [10] | https://docs.trench.bot/curated-radar/scoring-system | Score mechanics: whale weight, micro-cap bias, no calls above $10M, +30% threshold escalation |
| [11] | https://docs.trench.bot/curated-radar/call-breakdown | Call message anatomy: token info, whales (SOL size, mcap, WR), twitter section |
| [12] | https://trench.bot/ pricing block + /bundles | Subscription tiers; BRI weights (temporal sync 40, shared funding 50, funding timing 30, composition 25, behavioral 20) |
| [13] | https://docs.maestrobots.com/ + /llms.txt | Maestro: buy/sell settings, pasted-contract Token Report, multi-buy/multi-sell, limit orders, signals, copytrade, auto-snipe, premium |
| [14] | https://docs.maestrobots.com/buy | "Maestro Buy is coming soon" — group buy-bot not documented |
| [15] | https://docs.odin.tools/tools/heimdall-protection-bot | Heimdall = Odin Tools FREE anti-scam/moderation bot (global scammer list, CA flags, zero setup) |
| [16] | https://docs.odin.tools/ + /docs/trading/buy | Odin Tools trading bot is **Thor** (buy/sell/TP-SL/trailing/limit/copytrade/trenches) |
| [17] | https://t.me/s/skeleton_bot | `@skeleton_bot` = "SkeleTON_Bot", official bot of the SkeleTON project on TON |

## 2. Matrix (✓ = documented, ~ = partial/indirect, ✗ = absent, ? = UNVERIFIED)

| Capability | Mugetsu | Phanes | GMGN | PIRBView | Trench | Maestro | Thor | Heimdall |
|---|---|---|---|---|---|---|---|---|
| Public command list | ✓ 16 cmds [1] | ✓ 50+ [2] | ~ button UI [4] | ✗ button UI [6] | ✗ button UI [8] | ~ settings menus [13] | ~ menus [16] | ✗ none by design [15] |
| Scan fields | holders, bundle, dex, hmap, fresh, funded [1] | holders, top traders, deployer, trenches, heatmaps [2] | trending fields + CA audit [5] | JeetMap, EarlyBuyers, Bubble, Whale, Holders, Airdrop, Overlap, Top15/45, tax, PnL [6] | filter engine + bundle/cluster [9][12] | Token Report (pasted CA) [13] | token security + tax [16] | CA suspicion flag [15] |
| Trending selection | ✗ | ~ trenches/heatmaps [2] | ✓ sortable, 1m/5m/30m/1h, fires, CTO/AD badges, blue-chip index [5] | ✗ | ✓ curated score + user filters [9][10] | ✗ | ✓ trenches + filters [16] | ✗ |
| Buy/sell execution in-bot | ✗ [1] | ✗ redirect only [2] | ✓ SOL/ETH/Base/BSC/Tron [3][4] | ✓ via PIRBX bot (separate) [7] | ~ "automated filter buying (soon)" [12] | ✓ [13] | ✓ [16] | ✗ |
| Emoji-coded data | ~ "hmap" [1] | ✓ heatmaps [2] | ~ icon system [5] | ✓ 5-tier holder emoji (>$100K / 50-100K / 10-50K / 1-10K / <1K) [6] | ~ pill emoji marks pre-bond buyers [11] | ✗ | ✗ | ✗ |
| Amount presets | ? | ? | ~ buy settings [4] | ? | ✗ (filter thresholds only) | ✓ buy amounts, slippage, limits [13] | ✓ [16] | n/a |
| Channel-owner / group setup | ? | ? | ✓ invite @Alert_GMGNBOT to group [3] | ✗ (DM bot) | ✗ (DM bot) | ~ group buy-bot "coming soon" [14] | ✗ | ✓ add-as-admin, zero config [15] |
| Moderation | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ global scammer list, phishing/drainer delete, impersonation, auto ban/mute, admin alerts [15] |
| Voting / raids | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Anti-Sybil | ~ bundle_check [1] | ~ deployer/top traders [2] | ~ insider/sniper pages [3] | ~ fresh-wallet indicator, OverlapMap [6] | ✓ clusters + BRI (funding/temporal/behavioral) [12] | ✗ | ✗ | ~ cross-group ban graph [15] |
| Monetization | ? | ? | 1% trade fee + referral [3] | token-gated PRO (347,100 PIRB -> 75% fee cashback) [7] | SOL subscriptions 1.5-2.95 SOL/mo, 0% fees on top tier [12] | premium subscription, cashback, referrals [13] | referral + USD cash [16] | free forever [15] |
| Languages | ? | ✓ 19 [2] | ~ EN/CN docs [3] | ? | ? | ? | ? | ? |

## 3. Per-competitor notes (what is actually reusable)

### Mugetsu — scanner, no execution [1]
16 commands; the differentiating ones are `/top_holders`, `/wallet_analyzer`,
`/bundle_check`, `/holderscan`, `/check_dex`, `/hmap`, `/reverse_image_search`,
`/fresh`, `/funded`, `/early-pf-wallets`. Solana + Ethereum. No buy/sell, no copy-trade.
Gap we exploit: **no trending/curation layer and no group surface** — every command is a
one-shot DM answer.

### Phanes — breadth play [2]
50+ commands: charts, holders, top traders, deployer, trenches, heatmaps, alerts,
leaderboard; copy-trade is a *redirect* (no execution), prediction markets included,
19 languages. Gap: redirect-only execution leaks the conversion funnel; no moderation
and no group-management product.

### GMGN — a terminal that ships bots [3][4][5]
Bot roster is per-chain and mirrored (@GMGN, @GMGN_02.. per chain) — capacity scaling by
bot cloning. Sniper bot exposes Buy/Sell, Limit orders, Assets, Wallet, Copy Signal,
Copy Trade, Pump FOMO [4]. Trending is a real product: Age, creation time, Liq/MC,
holders, 1h TXs, 1h volume, price, 1m/5m/1h percent change, Degen Audit, selectable
1m/5m/30m/1h window, fire count, CTO/AD badges, **Blue Chip Index** (share of holders who
also hold verified blue chips) [5]. Group surface: `@Alert_GMGNBOT` pushes smart-money
alerts into a group [3]. Gap for us: message formats are utilitarian and there is
**no community/moderation product at all**.

### PIRBView — the best scan-message design in the market [6][7]
Free bot + PRO bot. Modules: JeetMap, EarlyBuyersMap, BubbleMap, WhaleMap, HoldersMap,
AirdropMap, OverlapMap, Top-15 holders (Top-45 on PRO), tax analysis, wallet PnL,
charts with AI analysis, floor analysis. Two details worth stealing outright:
1. **WhaleMap is an emoji scale over the holder list** — whale >$100K, shark $50-100K,
   dolphin $10-50K, fish $1-10K, shrimp <$1K, ordered by holder rank [6].
2. **Fresh-wallet share of top holders is presented as a risk signal**, alongside bag %
   and USD value [6].
PRO monetization = token-gated (347,100 PIRB holdings -> 75% trading-fee cashback on
PIRBX) [7]. Chain coverage: pirb.tech claims "almost all EVM chains including BNB Chain";
the SOL/TON/Base claim in our brief is **UNVERIFIED** [7].

### Trench Radar — curation economics [9][10][11][12]
Filter categories: algorithm scores (Overseer model, sensitivity presets), whale tracking,
insider detection (pre-bond pump.fun buyers), fresh-wallet CEX-withdrawal tracking,
backtesting (3d/7d/14d by tier), filter management. Scoring is explicit and worth copying:
whales add score by count x size x entry mcap; **micro-cap bias**; **nothing above $10M
market cap is called**; stacked signals multiply (whale + insider); low-winrate whales
subtract; **each call raises the next-call threshold by 30%** to suppress duplicate calls.
Call message = 5 sections in one message: token info -> whales (SOL size, mcap, age, top
holding, winrate, pre-bond prefix, clickable Solscan links) -> twitter activity -> etc.
Bundle Risk Index (BRI) is a weighted cluster score: temporal sync /40, shared funding /50,
funding timing /30, composition /25, behavioral /20, output 0-100% [12].
Pricing: Starter 1.5 SOL/mo, Premium 2.95 SOL/mo, Premium Plus 2.5 SOL/mo (promo) with
unlimited filters/backtests and 0% trading fees [12]. Gaps: DM-only, premium-priced,
no group product, no moderation, no voting.

### Maestro — settings depth benchmark, no group buy-bot yet [13][14]
Documented: global settings (anti-MEV, degen mode, gas), buy settings (auto-buy, amounts,
slippage, limits), sell settings (auto-sell, trailing sell, preset sell limits), pasted
contracts -> Token Report with **multi-buy / multi-sell / limit orders from the menu**,
signals, copytrade, auto-snipe, bridge, referral, cashback, premium subscription.
`docs.maestrobots.com/buy` literally says "Maestro Buy is coming soon" [14] — the
channel-owner buy-bot is the least-defended niche in this set.

### Thor (Odin Tools) + Heimdall [15][16]
Odin Tools ships **two** products: Thor (trading: buy/sell, cross-chain swaps, TP/SL,
trailing stop, limit buy, copy trade, trenches, portfolio, USD cash, wallet protection)
and **Heimdall — a free anti-scam bot for crypto groups**: global scammer list shared
across groups, phishing/drainer/fake-CA/impersonation deletion in milliseconds,
auto ban/mute/report with admin alerts, suspicious Solana CA flagging,
**zero setup (no commands, no config)**, free forever [15].
This is the only real moderation competitor in the set and the direct counter-position
to our `moderation/` module.

### Skeleton (`@skeleton_bot`) — misclassified in our brief [17]
The handle is **SkeleTON_Bot**, "official bot of the SkeleTON project on the TON
blockchain". It is a project/community bot, not a trading or scanning competitor.
Feature set: **UNVERIFIED** (no Start pressed, no docs).

### Heimdall — corrected classification
Our brief listed Heimdall as a candidate moderation bot; it **exists** and is Odin Tools'
free anti-scam bot (`@heimdall_odintools_bot`) [15]. Other products using the name
(generic group managers) are **UNVERIFIED** and out of scope.

### Trojan / Axiom / Terminal / Padre / Banana Gun — UNVERIFIED
No primary documentation was reachable in this pass (SERP returned unrelated pages).
Treated as trading terminals with no documented group/curation product until proven.
Do not cite them in external content.

## 4. Cross-cutting gaps (ranked by our leverage)

| # | Gap in the market | Evidence | Our leverage |
|---|---|---|---|
| G1 | Nobody pairs **moderation** with **scan/curation** in one bot | only Heimdall moderates [15]; only we run both `moderation/` and the scan stack | one bot, one admin, one config |
| G2 | Buy/sell **cards for channel owners** are undocumented or coming-soon | Maestro Buy "coming soon" [14]; GMGN et al. are DM terminals [4] | ship admin-configurable cards now |
| G3 | Emoji-coded holder data exists but stays data-only | PIRBView tiers [6], Trench pre-bond marker [11] | emoji tiers + drill-down + our verdict |
| G4 | Curation is paywalled and DM-only | Trench 1.5-2.95 SOL/mo [12] | free curated channel = distribution |
| G5 | No voting/raid mechanic anywhere in the set | every row ✗ | community curation + anti-racket |
| G6 | Anti-Sybil is cluster scoring, never a gate on promotion | BRI [12], fresh wallets [6] | make cluster score a *filter* on trending |
| G7 | Alert bots push into groups but never score what they push | @Alert_GMGNBOT [3] | verdict-first cards (already our /scan win) |

## 5. Method and limitations

- Collected with Bright Data `scrape_as_markdown` / `search_engine`, Pry scrape, SearXNG.
  SearXNG returned off-topic results for several crypto queries; Google SERP via Bright
  Data was the reliable discovery path.
- GitBook / Mintlify / Docusaurus sites expose `.md` and `llms.txt`; used where available,
  which is why GMGN, Maestro and Trench have page-level citations and others do not.
- Bot **command lists** are public only where the operator documents them. GMGN, Trench,
  PIRBView, Maestro and Thor are button-driven: their "commands" are inline-keyboard
  labels, not slash commands. Treat slash-command claims about them as UNVERIFIED.
- No Telegram bot interaction was performed (no Start, no `getUpdates` probing, no
  synthetic accounts). Anything requiring bot interaction is UNVERIFIED.

## 5. Scan-card parity actions (2026-10-24, rugmunchbot `scan-parity`)

What the six named competitors (Phanes, Trench Radar, GMGN, PreviewBot,
DevsNightmare, Mugetsu) put in front of a reader, mapped to the rows our
verdict card already renders or now renders. Every cell is grounded in the
refs above or in the card's own module.

| Competitor capability | Ref | Our row | Source of truth |
|---|---|---|---|
| Risk word + score up front | [1][2][10] | `VERDICT word · score/100 · $SYMBOL` (new: 2026-10) | `formatters/_vcard.py` |
| Hard-fail enumeration | [2][9] | why line lists honeypot / mint / freeze / sell-tax fails | `_vcard_verdict.py` |
| Holder count + fresh-wallet share | [6][9] | `Holders 3,241 · fresh 18%` (new) | `_vcard_intel.py` |
| Bundle verdict | [5][9][12] | `Bundle 🔴 12 wallets · 78% conf` / `none detected` / reason (new) | `_vcard_intel.py` |
| Sniper / insider / team cohorts | [1][9][12] | `Clusters Team/Insiders/Snipers: bought → current · realised SOL` | `_vcard_clusters.py` |
| Dev history (rug rate) | [2][6][9] | `Dev rug-rate · (rugs/launches) · funder` | `_vcard_signals.py` |
| LP / tax / authority row | [2][6][16] | one safety row, `—` when unread | `_vcard_safety.py` |
| Holder concentration | [6] | `Top-10` row, omitted when unmeasured | `_vcard_signals.py` |
| Plain-language read | [6][11] | AI analyst paragraph, numeric-grounded (new) | `tools/scanner/_ai_grounding.py` |
| Launch-cohort economics | [9][12] | parity block: cohort % + realised SOL | `_scan_report_parity.py` |
| Honest null with a reason | none of them | parity sections render `—` + the engine's reason; card rows do the same (new) | `_scan_report_parity.py`, `_vcard_intel.py` |

Deliberate non-goals (unchanged): in-bot buy/sell execution (GMGN [4], Thor
[16]) is a trading-terminal feature, not a scan feature; monetisation and
language coverage are out of scope for this card.

## 6. DevsNightmare and PreviewBot (added 2026-10-24)

Not folded into the 8-bot matrix above (that table is a fixed set); recorded
separately with the same evidence rule.

| Capability | DevsNightmare [18] | PreviewBot [19] |
|---|---|---|
| Product | `@DevsNightmareProbot` token checker/tracker (channel `@soldevnightmarebot`) | UNVERIFIED |
| Scan fields | token history + alerts; exact field list **UNVERIFIED** | UNVERIFIED |
| Holder / bundle / sniper presentation | UNVERIFIED; channel posts document alert deltas and a "token tracking count" | UNVERIFIED |
| Dev history | no documented deployer rug-rate surface | UNVERIFIED |
| Alerts | ✓ alerts that show what changed since the last report (balance/SOL shifts) | UNVERIFIED |
| Chains | Solana; posts document PumpSwap, Raydium CPMM/CLMM and Token-2022 coverage | UNVERIFIED |
| Reliability claim | node-dependent: "no info found" while its node was down; 3-month retention after a disk upgrade | UNVERIFIED |

**Do not cite** DevsNightmare scan fields or any PreviewBot capability in
external content until a primary source is reached. Our parity work therefore
uses Phanes/GMGN/PIRBView/Trench/Mugetsu ([1]-[12]) as the design benchmark and
treats [18]/[19] as watch items only.
