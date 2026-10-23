# Beyond parity — what we must ship to beat them

Derived from `docs/competitors/bot-parity.md` (evidence and refs live there).
Sequenced so that every item is shippable alone and leaves the bot better if we stop.

Rules for all specs below:
- No new bot, no second token, no second database. One bot, one config file family.
- Every group-facing behaviour ships **off** for existing chats and **on** for new
  chats only where the spec says so, and must be reversible from chat settings.
- Acceptance criteria are executable against the existing dry harness
  (`moderation/harness.py`, 17 synthetic updates) or a unit test; "looks right" is not an AC.

## P0-1 — Zero-setup guard preset + fleet ban digest (beats Heimdall)

**Why.** Heimdall's entire proposition is "add as admin, protection starts, no
commands" plus bans that follow a scammer across every group [26]. We have deeper
per-message detection (`names.py` homoglyph folding, `addresses.py` verdicts,
`nsfw.py` hashes) but a new chat receives `{"chats": {}}` and `dry_run: true`, so
our precision is invisible until an operator edits JSON [29].

**Surface.** `configs/moderation.json` preset key; `decide.py` gains `global_ban`;
`propagate.py` gains a signed digest of ban entries (no PII beyond user id + reason hash).

**Spec.**
```json
{ "preset": "guard", "dry_run": false,
  "global_list": true, "global_list_min_sources": 2, "digest_ttl_s": 900 }
```
`preset: "guard"` expands to: links + invite + scam-domain + velocity + CA-verdict
rules on; captcha `button` on with 90 s window; ban propagation **advisory only**
until `global_list` is set.

**Acceptance criteria.**
1. A chat with only `{"preset":"guard"}` loads, `dry_run` resolves to false, and the
   harness shows delete/warn actions — one new case per enabled rule.
2. A ban in chat A with `propagate_ban: true` blocks the same user in chat B **only**
   when ≥2 distinct source chats carry the entry; a single source never blocks (harness case).
3. Every global decision writes `audit.py` fields `rule, action, sources, evidence, dry_run`.
4. `exempt_admins: true` is evaluated before the global rule (harness case: admin spam passes).
5. No network call on the message hot path — digest read is cached, TTL-bounded.

## P0-2 — `/bundle`, `/holdermap`, `/fresh` (beats Mugetsu beta and PIRBView maps)

**Why.** Mugetsu ships `/bundle_check` explicitly labelled **BETA** and returns only
"highly probable" transactions [2]. PIRBView ships HoldersMap + fresh-wallet flags but
as emoji strings inside one long scan message [11][13]. We already compute the raw
material — `_vendor/rug_risk_rules.py`, `rug_risk_signals.py`, `holder_mega_sampler.py`,
`scan_enrich.py`, `own_wallet_map*.py` — and surface none of it as a command.

**Surface.** Three commands, DM + group, no tier gate (they are our wedge):
`/bundle <ca>` · `/holdermap <ca>` · `/fresh <ca>`.

**Spec.**
- `/bundle`: funding-graph clusters among first-N buyers, each cluster with shared
  funder, buy timestamps, % of supply, and a **confidence label** (`high|medium|low`)
  plus the evidence rows that produced it. Never a bare "BUNDLED" verdict.
- `/holdermap`: table of top holders → bought / sold / transferred-in / still-holding,
  reusing HoldersMap semantics [13] but as a table with links, not emoji.
- `/fresh`: holders whose wallet first-funding is inside the launch window, with
  funding source and age. State the data source and block height.

**Acceptance criteria.**
1. `/bundle` on a known-clustered fixture returns ≥2 clusters with funder addresses
   and a confidence label; on a negative fixture returns "no cluster found", not an empty card.
2. Every numeric claim carries `source` + `as_of` block/height in the card footer.
3. `/holdermap` reconciles to `/holders` totals within 1% on the same snapshot.
4. `/fresh` returns `unknown` (not "clean") when funding data is missing — no false negative.
5. All three are pure read paths: no write, no mute, no ban.

## P0-3 — Group-native alert layer: buy popups, 3-hour pulse, per-token subs

**Why.** Skeleton owns the "token's own group" surface: buy popups with thresholds,
emoji scaling, 24-buy batching, a pinned stream, and `/stats` auto-popping every 3
hours [5][6]. Our `/alert` is DM-shaped and tier-gated at Scout [28]; inside a token
group we are absent. This is a retention surface, not an intelligence surface.

**Surface.** `/buybot on|off|settings` (admin-only), `/stats` (anyone), batching in the
existing alert worker.

**Spec.** Admin picks: min buy USD (5–2500), emoji per $X, show holders on/off,
media, batch window (default 10 s, cap 24 buys/popup), pinned stream on/off.
`/stats` posts market cap, volume, holder count, biggest 24h buy, socials, and the
block height the numbers came from.

**Acceptance criteria.**
1. 30 buys in 10 s produce ≤2 messages and 0 dropped buys (batch accounting test).
2. Popups stop within one job tick of `/buybot off`; no queued message survives.
3. `/stats` payload fields match a fixture snapshot exactly; missing field renders
   `n/a` with source, never a zero.
4. Admin-only enforcement: non-admin `/buybot` gets a refusal card, no state change.
5. Rate budget: popups respect the group's flood control (`flood_control.py`).

## P1-1 — Notes and filters, but scam-aware (table stakes vs MissRose)

**Why.** Notes and filters are the two most-used MissRose features and we have
neither [22][23]. Without them an admin keeps a second moderation bot in the group,
which is exactly how we lose the seat.

**Surface.** `/save <key> [reply]` · `/get <key>` · `#key` · `/notes` · `/clear <key>` ·
`/filter <trigger> <reply>` · `/stop <trigger>` · `/filters`. Admin-gated writes.

**Spec.** Single-word keys (MissRose's constraint is fine). Attachments supported.
`{admin}`/`{user}`/`{private}` fillings. **Our addition:** a note or filter whose reply
contains a contract address is scanned once at save time; if the CA is `avoid`, the
save is rejected with the verdict and the evidence link. Filters cannot be created for
scam domains.

**Acceptance criteria.**
1. `/save faq <reply-to-media>` then `#faq` returns the attachment (round-trip test).
2. `/filter "rug check" <reply>` matches phrase; `/stop` removes; `/filters` lists.
3. Saving a note whose reply contains an `avoid`-verdict CA is refused with a reason
   code and does not persist (unit test with a fake scanner verdict).
4. Writes require admin; reads do not. `{private}` sends in DM and falls back to chat
   with a visible notice when DM is closed.
5. No note/filter can exceed the message limit; oversize is refused, not truncated silently.

## P1-2 — `/overlap`, `/floor`, `/lb` (beat PIRBView on its own turf)

**Why.** PIRBView's differentiators in the guide channel are OverlapMap (two-token
holder intersection, top 80) [19], Floor analysis [18] and the per-group caller
leaderboard [16]. Each is a small join over data we already store.

**Surface.** `/overlap <ca1> <ca2>` · `/floor <ca>` · `/lb [1d|1w|1m|all]`.

**Spec.** `/overlap` returns intersection of top-N holders with both-token USD values
and a shared-funder flag. `/floor` returns the top-10 holder cost basis and current
floor in USD. `/lb` scores *first scanner per token in this chat* and marks outcome
(mcap at first scan → mcap now), so the board measures calls, not volume of calls.

**Acceptance criteria.**
1. `/overlap` on two fixtures with one shared holder returns exactly that holder plus counts.
2. `/floor` labels the price source and timestamp; unresolvable cost basis renders `n/a`.
3. `/lb` is per-chat, resets are admin-only, and each row shows first-scan mcap, current
   mcap and the delta — no row without both numbers.
4. Multi-chain: ETH, BSC, Base, SOL, ARB minimum (matches or exceeds PIRBView's `/pnl` set [15]).

## P2-1 — Moderation ops surface (warns, purge, locks, admin log, approvals)

**Why.** These are the everyday tools that make MissRose sticky [20][24]. Our gap here
is the reason an admin keeps Rose alongside us even after P0-1.

**Surface.** `/warn` `/warns` `/unwarn` (escalation to mute/ban configurable) ·
`/purge <n>` · `/lock <type>` `/unlock` · log channel binding · join-request approvals.

**Acceptance criteria.**
1. 3 warns → configured action fires once; `/warns` shows history with reasons.
2. `/purge 50` deletes ≤50 of our-or-user messages and reports the count, never deletes
   another bot's messages.
3. `/lock links|media|stickers` blocks that type for non-admins; `/unlock` restores.
4. Log channel receives one line per action with `rule, action, target, evidence`.
5. Approvals: join-request path shares the captcha state machine (no second flow).

## P2-2 — Portability: export/import, languages, command disabling

**Why.** MissRose lets an admin move a configured group between instances and hide
commands [20]. That is switching-cost defence; without it our migration story is manual.

**Acceptance criteria.**
1. `/export` emits a versioned JSON of that chat's moderation + notes + filters; `/import`
   validates schema version and refuses unknown keys **loudly**.
2. Import is atomic: a bad key leaves the previous config intact (test with a corrupt file).
3. Two locales minimum (en, +1) for user-facing cards; unknown key falls back to en.
4. `/disable <command>` hides a command from the menu and refuses execution.

## What we must NOT copy

| Not this | Why |
|---|---|
| **PIRBView's token-gated PRO** (hold 0.05% of their own supply for access) [17] | Paywall coupled to our own token price; a drawdown silently churns paying users and invites "hold to use" speculation. Keep Stars/USD tiers. |
| **Heimdall's opaque global list** — "banned in one group, blocked in all", no published appeal or quorum [26] | Unaccountable cross-group power. We require 2-source quorum, an appeal path (`/appeal` exists), and audit lines. A wrong ban is a support incident, not a feature. |
| **Skeleton's blacklist-by-operator** ("our admins will remove/blacklist the token") [6] | Vendor discretion over a customer's group listing. We never blacklist a token for not pairing with us. |
| **Raid / engagement farming and buy contests** [4] | Manufactured volume conflicts with a scam-detection brand; it also creates a financial incentive for us to inflate activity. |
| **KOL alert databases sold as alpha** [6][8] | Unverifiable wallet→person mapping. We can alert on *tracked profitable wallets* with disclosed methodology, never on "KOL" identity claims. |
| **Emoji-only maps and bare verdicts** [2][11][13] | A "BUNDLED" or a row of 🐳 with no evidence rows is unfalsifiable. Every claim carries source + confidence. |
| **Scraping a third-party site as a primary data source** (Mugetsu `/holderscan` → holderscan.org, partial coverage) [3] | We own indexers; a scrape gives us someone else's gaps and their downtime. |
| **Buybot emoji spam by default** | Batching exists to *reduce* messages. Defaults: batch on, max 1 popup/10 s, cap 24 buys. |
| **100+ command surface with per-feature config sprawl** (MissRose scale) [20] | Our menu is 51 advertised commands with drift checks; growth goes into presets, not new top-level commands. |

## Success measure

Not "we have the command". Per axis: **A** `/bundle` used ≥1×/day per active group;
**B** ≥1 group enables buy popups and keeps them 7 days; **C** new groups reach
`preset: guard` without an operator edit, and global-list false positives stay under
1 per 10k decisions (audit lines, sampled weekly); **D/E** unchanged — already ahead.
