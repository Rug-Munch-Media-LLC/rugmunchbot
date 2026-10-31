# Competitor scan methodology — how they scan, and how we do it better (2026-10-31)

Inputs: vendor docs (`data/competitors/sources.json`), live session captures
(`bot-recon/ (fleet knowledge store):scan-*.json`, `<handle>.json`, `phanes-captures.*`),
collected 2026-10-24 → 2026-10-31. Facts only; inferred steps are marked inferred.

## 1. Three different products hide behind the word "scan"

| Type | Who | What it really is |
|---|---|---|
| One-shot card | PIRBView, Phanes, Skeleton scanner, Mugetsu, Thor | Query in → formatted card out; no state |
| Continuous filter engine | TrenchRadar (+@TrenchScannerBot for one-shot) | Saved filter loadouts + backtesting + alert stream |
| Team/allocation audit | DevsNightmare (+REKT suite) | Deployer/team behavior over time, diffs between scans |

Our `/scan` is type 1 with `/watch`+`/alerts` as a lite type 2. That is the right
product; the parity gap is *presentation* (emoji grammar) and *methodology depth*
(bundle/cluster, fresh wallets, team audit), not the model.

## 2. Per-competitor methodology (observed output anatomy → inferred pipeline)

### 2.1 PIRBView — the emoji-card standard (verbatim from live capture, BONK/PEPE)

```text
🦜 Pepe (PEPE) | ETH
⏳ 1287d 👥 1377 👀 1509 🦷 0
🌍 🔍 🌐 🐦 ❎ ♻️
✅ Renounced
0x6982508145454Ce325dDbE47a25d4ec3d2311933
📈 GM ⋅ DS
🦜🦜🪙🦜🦜🦜🦜
🧠 2x 1% ✅ Rug 1%
🧢 MC:  $1.85B ⇨ $11.83B [682d]
💧 Liq: $15.20M (1%) ⋅ 🔒 99%
🏷 USD: $0.000004389 (-1%)
🎚 Vol: $1.30M (🅑804/Ⓢ763)
🏧 B: 0.00% | S: 0.00% | T: 0.00%
⚖️ No limits
🪠 Clog: 0.01%
🦅 Dex Paid: ✅ ⋅ CTO: ❌
🦜🦜🦜🦜🦜🦜🦜
5️⃣ 6.1⋅4.5⋅3.2⋅3.0⋅2.0 [27%]|597.2K
🎯 0.0 % 📦 1.5 % 🕵️ 0.5 %
🐳 7.8 % 🦐 5.4 %
🌱 0.0 % 🌿 0.0 % 🧠 0.0 %
❌ Blacklist: 1 | Siphon: 0
⚪️ Explanation of the Emojis
🔥 $McRib⋅$McRib⋅$ROOTS
🥇 First scan @ MC 1.85B
🎯 PIRBX ⋅ Maestro (Pro)
❇️ Best Trending Channel
Updated at 08:00:49 PM UTC
```

Emoji grammar (their guide + card):

| Emoji | Meaning |
|---|---|
| 🦜 / 🪙 | top-7 holder wealth tier (5 tiers: >$100K / 50–100K / 10–50K / 1–10K / <1K) |
| 🧠 | smart-wallet count (with x-multiple) |
| 🧢 / 💧 / 🏷 / 🎚 / 🏧 / ⚖️ / 🪠 / 🦅 | MC (now⇨peak[age]) / liq+% / price Δ / volume with 🅑buy Ⓢsell counts / buy-sell-transfer tax / limits / LP "clog" / Dex Paid + CTO |
| 5️⃣ | top-5 holder stack `a⋅b⋅c⋅d⋅e [sum%]|supply` |
| 🎯 📦 🕵️ | snipers / bundles / insiders (%) |
| 🐳 🦐 | whale % / shrimp % of holders |
| 🌱 🌿 🧠 | fresh / mid / smart % of holders |
| ❌ | blacklist count, siphon count |
| ⚪️ | the legend button — "Explanation of the Emojis" (self-documenting card) |

Method inferred: chain RPC for taxes/limits/renounce; holder snapshot + transfer
graph for the tier rows and behaviour %; GMGN/DexScreener deep links (📈 GM ⋅ DS);
paid-DEX listing state (Dex Paid); first-scan tracking (🥇 first scan @ MC).
Gaps: no evidence provenance, emoji-only without inline legend (the legend is a
separate message), PRO gates top-50 PnL, no confidence labels.

### 2.2 Phanes — breadth + call tracking (verbatim from live capture, $PILL)

```text
🪙 Pillpad ($PILL/PUMP)
 └ 🟣 Pump @ 46% | 🌱19m | 👁️80
📊 Stats
 ├ USD   $0.0₅7084 (+128%)
 ├ MC    $7.1K
 ├ Vol   $24.8K
 ├ LP    $1.7K
 ├ Sup   1B/1B
 ├ 1H    +128%  🅑96 Ⓢ75
 └ ATH   $15.4K (-54% / 5m)
🔗 Socials [3h]
 └ 𝕏 [♽] • Web [ⓘ] • [lore]
🔒 Security
 ├ Fresh     4.9% 1D | 29% 7D
 ├ Top 10    28% | 33 (total)
 ├ TH        3.3|3.0|3.0|3.0|3.0
 ├ Sniper    7 (14%)
 ├ Insider   2 (3.5%)
 └ DEX Paid  🟢 [info] • [lens]
DEF•DS•GT•EXP•𝕏s
GM•AXI•TRO•TRT•FMO•OKX
BSD•MAE•TRM•BAN•COV•BLO
```

Notables: 🟣 Pump @ 46% (bonding-curve progress), 👁️ watcher count, `0.0₅` subscript
notation, socials freshness `[3h]` + ♻︎ recycled-X marker, Fresh% with 1D/7D split,
Sniper/Insider as count+share, three badge lines = paid/promo coverage (DEF•DS•GT•…)
and launchpad coverage (GMGN•Axiom•Trojan•…). Search results (`/s BONK`) use mascot
emoji (🐶/🟣/💊) + 👁️ watchers + age•MC|Vol|Hldr|Tx. 50+ commands incl. group
leaderboards (`/lb /calls /fc /elb`), PnL cards (`/pnl /gpnl`), admin alert tools
(`/xfeed /xret /dd`). No risk verdict anywhere — pure data breadth.

### 2.3 Mugetsu — command zoo, now paywalled in part

30+ commands (`/holders_map /whale_map /early_map /distro /bundle_check /fresh
/funded /dev /scam /first /notable_followers /domain /site_check /twitter_reuse
/reverse_image_search …`). Live capture 2026-10-31: `/top_holders <CA>` now returns
**"Unauthorized: Please purchase/renew your subscription using /renew"** — the
2026-10-25 "free" verdict must be updated: core holder analytics are
subscription-gated today. `holderscan.org` is their holder-growth source (scraped).
Bundle-check labels itself BETA. Language gate EN/CN on /start.

### 2.4 Skeleton — lounge-native analyzer (separate bot)

`@Skeletonscanbot`: add-as-admin for "lounges", group popup with minichart, per-group
leaderboard; DM single-user scans on SOL/ETH/BSC/BASE. Commands: `/holders /top
/snipers /first /wd /ad /market`. Latency disclosure: "May take up to 30 sec".
Deep EVM flag set (mint/freeze/hidden owner/proxy/tax) per their docs.

### 2.5 TrenchRadar — filter engine + score model (paywalled)

`/track /details /alerts /predict /guide`; `/details` on a free account returns
❌ "You need to be subscribed to our Curated bot, or have the full package".
Docs: filter categories with algorithm scores ("Overseer"), whale/insider/fresh-wallet
filters, backtesting; BRI bundle-risk weights: shared funding 50, temporal sync 40,
funding timing 30, composition 25, behavioral 20; no calls above $10M mcap.
Public one-shot variant `@TrenchScannerBot`: `/clusters [ca]` and `/ob [ca]`
(legacy pump.fun bundles) — box-drawn card, 🟢/🟡/🔴 bar-gauge risk score, cluster
table with `💰 Sort: Bought`, `👁️ Show Empty`, `🔄 Refresh` buttons.

### 2.6 DevsNightmare — team-allocation audit + REKT suite

Token/team allocation analysis, "how many users watch this token" (watch-count),
alert diffs since last report, Raydium CPMM/CLMM + Token2022 coverage. The only
vendor that treats a scan as *stateful* (diff vs your last scan).

### 2.7 Thor (Odin) — execution-side safety (do not clone the execution, clone the UX)

Token security + tax on every quote, price-impact guard, EVM honeypot checks,
trenches scored 0–100. The best pattern in the set is Thor's *skip reasons*: when a
copy trade is skipped it explains WHY ("Flagged as a honeypot", "Buy and sell tax
too high", "No healthy way to sell this token") — safety messaging as first-class UX.

## 3. Cross-vendor emoji/wording table (the scan vocabulary)

| Field | PIRBView | Phanes | TrenchScanner | Recommendation for our card |
|---|---|---|---|---|
| Age | ⏳ 1287d | 🌱19m | — | `⏳ 19m` + chain tag |
| Holders | 👥 1377 | Hldr: | — | 👥 with source + block |
| Watchers | 👀 1509 | 👁️80 | — | 👀 = our own scan-count (free to compute) |
| Curve progress | — | 🟣 Pump @ 46% | — | 🟣 for pump.fun tokens |
| Buy/sell counts | 🅑804/Ⓢ763 | 🅑96 Ⓢ75 | — | keep 🅑/Ⓢ (market-standard) |
| Taxes | 🏧 B/S/T | — | — | 🏧 + "no limits" line |
| Liquidity | 💧 … 🔒 99% | LP $1.7K | — | 💧 locked-% |
| Top-5 stack | 5️⃣ 6.1⋅4.5… | TH 3.3|3.0|… | — | keep 5️⃣ stack, cite block |
| Whales/shrimp | 🐳 7.8% 🦐 5.4% | — | 🐋 Whales | 🐳/🦐 |
| Fresh | 🌱 0.0% | Fresh 4.9% 1D/7D | 🌱 Fresh | 🌱 with 1D/7D (adopt Phanes split) |
| Snipers | 🎯 0.0% | Sniper 7 (14%) | — | 🎯 count (share) |
| Bundles | 📦 1.5% | — | cluster table | 📦 + /bundle command (P0-2) |
| Smart money | 🧠 0.0% | Insider 2 (3.5%) | — | 🧠 count |
| Legend | ⚪️ Explanation button | — | — | one-line inline legend on every card |
| Freshness | Updated at … UTC | Socials [3h] | — | per-section `as_of` (ours already) |

## 4. How we do it better — concrete plan (extends `bot-beyond.md`)

1. **Evidence-first cards** (already our doctrine): every numeric carries
   `source` + `as_of` block/height. None of the five competitors does this — it is
   the differentiator their vendors cannot copy without changing their product story.
2. **Self-documenting card**: one-line emoji legend inline (PIRBView hides theirs
   behind a button; we put it under the card). Kills the "what does 🪠 mean" problem.
3. **Confidence labels on derived claims**: `/bundle` ships high|medium|low + evidence
   rows (already AC'd in bot-beyond P0-2); never a bare "BUNDLED".
4. **Honest degradation**: `unknown` when data is missing (P0-2 AC4). Never
   "clean" on missing data — Mugetsu/Trench both stay silent on their gaps.
5. **Ship the missing trio** `/bundle` `/holdermap` `/fresh` (P0-2) with these
   formats as reference: PIRBView HoldersMap semantics (table not emoji), Mugetsu
   fresh-wallet + funding source, TrenchScanner cluster UI (Sort/Show Empty/Refresh).
6. **Watch-count**: DevsNightmare's per-token watch-count is trivial for us — count
   `/scan` invocations per token; display as 👀. Competitive parity in one day of work.
7. **Scan diffs**: store last scan per (chat, token); `/scan` appends a delta section
   (holders Δ, LP Δ, dev sells since last scan). DevsNightmare charges for this.
8. **Latency honesty**: show "scanning…" placeholder and edit in place; budget <10 s
   from our DataBus cache (Skeleton discloses up to 30 s).
9. **Skip-reason UX (Thor's best idea)**: every negative/absent answer explains why
   ("no holder data: token < 24h old"), turning silence into trust.
10. **Never clone**: TrenchRadar's paid filter/backtest engine and any volume-wash
    products (Major "VolumeBot") — intelligence and safety only.

## 5. Sources

See `data/competitors/sources.json` (appended 2026-10-31: docs.majorbots.io,
docs.odin.tools, t.me profile pages, web3.okx.com plugin page, Marcus-Rug-Intel
GitHub org). Live captures: `bot-recon/ (fleet knowledge store):*.json` (16 bots + Phanes
deep pass), session method: read-only navigation, safe-click policy, FloodWait
honored, no trades/wallet ops/ToS accept/joins.
