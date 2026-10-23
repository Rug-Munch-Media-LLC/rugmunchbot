# Rugmunchbot — what to build to beat this set (2026-10-23)

Derived from `docs/competitors/feature-matrix.md`. Ordered by leverage per unit of work.
Each spec has: why, surface, data/config, acceptance criteria (AC), and what we do NOT do.
Canon applies: new files <=200 lines, one primary function per module, no monkeypatching,
shrink-only ratchets, gate self-tests must pass.

## P0-1 — Group moderation v3: shared scam graph + zero-setup defaults

**Why.** Heimdall is the only real moderation product in the market and its whole pitch is
"add as admin, configure nothing, a scammer banned in one group is banned in all" [15].
We already have the pieces — `moderation/gate.py`, `captcha.py`, `decide.py`, `actions.py`,
`propagate.py`, `audit.py`, per-chat `configs/moderation.json` (schema v2) — but everything
is per-chat and default `dry_run: true`, so a new group gets no protection until an operator
edits JSON. That is the gap.

**Surface.** Existing module only: no new bot entry point, no new command name required.
`propagate.py` gains a signed fleet digest; `decide.py` gains one rule (`global_ban`).

**Data/config (schema v3, additive).**
```
enabled: true
preset: "guard"                  # guard | balanced | off  (guard = new default for new chats)
global_list: true                # consult fleet ban digest
global_list_min_sources: 2       # require 2 distinct chats before we trust an entry
ca_flag_action: "warn"           # suspicious-CA flag on messages containing a mint
digest_ttl_s: 900
```
Schema v3 must be readable by the v2 loader (missing keys -> documented defaults) so no
migration step is needed.

**AC1** `configs/moderation.json` with `preset: "guard"` loads on a chat with no other keys
and `enabled=true`, `dry_run=false`, links/promo/velocity/CA rules active.
**AC2** A ban recorded in chat A with `propagate_ban: true` blocks the same user in chat B
when `global_list: true` and >=2 source chats agree; single-source entries never block.
**AC3** Every global-list action writes an `audit.py` line with `rule`, `action`, `sources`,
`evidence`, `dry_run` — verified by the existing dry harness (`harness_cases.py`), which
grows by >=4 cases and stays zero-Telegram-write.
**AC4** `exempt_admins: true` is honoured before the global rule (admin actions never blocked).
**AC5** Latency: decision path adds no network call on the hot path (digest is cached in
process, refreshed off-path); measured in the harness with a fake clock.

## P0-2 — Admin-configurable buy/sell cards for any token (`/buybot`)

**Why.** Maestro's group buy-bot is still "coming soon" [14]; GMGN pushes alerts into groups
but never renders a trade card [18]; PIRBView/Trench are DM-only [6][8]. A channel owner who
wants "every buy of MY token, as a card, with buttons" has no documented product. This is the
single biggest open niche in the set.

**Surface.** New commands (menu-advertised, owner/admin-gated): `/buybot`, `/buybot_on`,
`/buybot_off`, `/buybot_preview`, `/buybot_template`. New module package
`src/rugmunchbot/buybot/` with one primary function per file:
`config.py` (load/validate per-chat config), `watch.py` (topic poller), `render.py`
(template -> HTML card), `handlers.py` (command routing), `store.py` (dedupe + counter).

**Data/config** `configs/buybot.json`, keyed by `chat_id`:
```
{ "enabled": true,
  "chain": "solana",                  # solana | ethereum | base | bsc
  "token": "<mint>",
  "emoji_tiers": true,
  "amount_presets": ["0.1", "0.5", "1", "5"],
  "min_buy_usd": 25,
  "fields": ["amount","usd","mcap","holders","buyer_pnl","verdict"],
  "buttons": ["chart","scan","holders"],
  "dedupe_window_s": 20,
  "max_cards_per_min": 10 }
```
Any token, not just ours: the watcher resolves the mint, pulls trades and holder data through
the existing data resolver, and renders our verdict from the same path `/scan` uses.

**AC1** `/buybot` on a chat with no config replies with a setup card (chain -> token -> presets)
and writes nothing until a token is confirmed.
**AC2** With a valid config, a simulated buy event renders exactly one card containing the
configured `fields` in fixed order; rendered length <4096 chars; all dynamic values HTML-escaped
(reuse `moderation/messages.py` escaping helpers, no new escaper).
**AC3** Two events for the same `(tx, buyer)` inside `dedupe_window_s` produce **one** card
(`store.py` unit test with a fake clock).
**AC4** Rate cap enforced: 11th card in a minute is dropped and increments a drop counter in
the audit log; the bot never raises.
**AC5** Buttons are `callback_data`-scoped to `(chat_id, mint)` and re-validated against current
config before acting, so a stale button cannot scan the wrong token.
**AC6** `/buybot_off` stops posting within one poll interval and leaves the stored counter intact.
**AC7** Tier test: card renders at $25 / $250 / $2.5K / $25K buy sizes with the correct emoji tier.

## P1-3 — Emoji and amount presets as a shared primitive

**Why.** PIRBView's WhaleMap (whale >$100K, shark 50-100K, dolphin 10-50K, fish 1-10K,
shrimp <1K, ordered by holder rank) is the most legible holder UI in the market [6];
Trench marks pre-bond pump.fun buyers with a pill emoji [11]. We have `/holders` and
`/hmap` but the scales are ad hoc.

**Surface.** One module `formatters/tiers.py` exporting two pure functions
(`holder_tier(usd)`, `buy_tier(usd)`) consumed by `/holders`, `/hmap`, `/whale`, `/buybot`.
No handler changes beyond calls.

**AC1** Boundaries are exact and tested at 999 / 1000 / 9999 / 10000 / 49999 / 50000 /
99999 / 100000 / above — table-driven, no float rounding drift (integer cents internally).
**AC2** Tier legend is appended once per message, not per row.
**AC3** Same token rendered through `/holders` and `/buybot` yields identical tier assignments
(shared fixture test, one source of truth).

## P1-4 — Voting and anti-racket

**Why.** No competitor in the set has voting at all [matrix: every row ✗]. The risk that kills
naive voting is raiding, so the feature is only worth building with the anti-racket half.

**Surface.** `/nominate <ca>`, `/vote <id>`, `/tally`, `/leaderboard` (owner/admin-gated to
enable; voter-gated by membership age). One module `src/rugmunchbot/community/votes.py`.

**Anti-racket rules (all must be on):**
`min_account_age_days: 7`, `min_chat_messages: 20`, one vote per user per round (idempotent),
round window, and a **raid freeze**: if joins exceed `joins_per_10min` (default 25) or the
cluster score of new joiners exceeds threshold, the round freezes and admins are alerted
instead of accepting votes.

**AC1** Replaying the same `/vote` from one user in a round changes nothing (idempotent,
store-level unique key).
**AC2** A user below age/message thresholds gets a single polite refusal and no tally change.
**AC3** Raid freeze triggers at the 26th join in 10 minutes in the harness, freezes the round,
and emits one admin alert (not one per joiner).
**AC4** `/tally` output is deterministic given the store state, and includes voter count,
quorum, and freeze status.
**AC5** Abuse path: nominating the same CA twice in a round is collapsed to the original entry.

## P2-5 — Curation with a real scoring contract (Trench's economics, free)

**Why.** Trench's scoring is the most defensible curation model published: whale weight by
count x size x entry mcap, micro-cap bias, nothing above $10M mcap, stacked signals multiply,
low-winrate whales subtract, and **each call raises the next-call threshold by 30%** [10].
Ours is currently "hot tokens" [COMMANDS.md `/trending`] with no published contract.

**Surface.** `src/rugmunchbot/curation/score.py` (pure scoring), `threshold.py` (escalation),
`select.py` (top-N pick). Feeds `/trending` and the curated channel post.

**AC1** Scoring is a pure function of a documented feature dict; golden-fixture test pins
three scenarios (single whale, whale+insider, low-winrate whale) with expected scores.
**AC2** Escalation: calling token X requires `score >= base * 1.3^n` where n = prior calls
for X in the window; tested with a fake clock.
**AC3** Hard cap: any token above the configured mcap ceiling is never called, even with a
perfect score.
**AC4** Selection is idempotent per window and never re-posts the same token twice.

## P2-6 — Cluster risk gate (BRI-equivalent, documented weights)

**Why.** Trench's Bundle Risk Index publishes its weights (temporal sync /40, shared funding
/50, funding timing /30, composition /25, behavioral /20 -> 0-100%) [12]; our `/cluster` and
`/rugmap` produce clusters but no single number and no gate on promotion.

**Surface.** `src/rugmunchbot/scanners/cluster_risk.py`, consumed by `/cluster`, `/scan`
(forensics block) and P2-5 selection.

**AC1** Output is an integer 0-100 with the five named components exposed (not just the total),
so the card can show why.
**AC2** Weights are config-driven and the sum of component maxima is asserted in a test.
**AC3** P2-5 refuses to call any token whose risk index exceeds the configured ceiling.

## P3-7 — Monetization: Stars, not token gating

**Why.** Our `/pricing`, `/topup`, `/buystars` already run on Telegram Stars [COMMANDS.md].
PIRBView gates PRO on holding 347,100 PIRB [7] — that only works because they have a token.
Wire the new surfaces (`/buybot` per-chat slots, extra baskets, vote rounds) to Stars.

**AC1** Every new paid surface maps to an existing tier in `/account` (no second billing path).
**AC2** A lapsed subscription degrades to free behaviour, never to a broken command.

## Do not build

- Copy-trading, custody, or key import. It is the legal and security blast radius of every
  competitor here and none of it is our edge.
- Wallet-connect gating. Proven by five competitors to be a funnel leak (Phanes redirects,
  GMGN asks for a wallet before showing anything).
- Per-chain bot cloning (GMGN's @GMGN_02..04 pattern [3]). One bot, chain routing internally.

## Top gaps this closes

| Gap | Spec | Why it wins |
|---|---|---|
| G1 moderation + scan in one bot | P0-1 | Heimdall has no scan; scanners have no moderation |
| G2 channel-owner buy/sell cards | P0-2 | nobody has shipped one (Maestro: coming soon) |
| G3 emoji data as a product | P1-3 | PIRBView proves legibility sells; ours adds a verdict |
| G5 voting/raids | P1-4 | zero competition, but only with anti-racket |
| G4 paywalled curation | P2-5 | free curated channel = distribution |
| G6 Sybil gate on promotion | P2-6 | makes curation defensible, not just loud |
