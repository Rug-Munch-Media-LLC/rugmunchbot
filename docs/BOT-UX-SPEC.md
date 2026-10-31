# BOT-UX-SPEC — menu, layout, structure, setup

Status: design spec, not yet implemented. Owner: bot UX.
Scope: our Telegram surface (`rugmunchbot`) vs four public competitors.
Method: public vendor material only — Phanes docs (`docs.phanes.bot/phanes/commands`),
Skeleton vendor material (`docs/competitors/bot-parity.md` refs [4]-[10]),
Heimdall/Odin (`@heimdall_odintools_bot`, ref [26]), MajorBuyBot (`t.me/MajorBuyBot`
profile only — no docs site found 2026-10-28). No competitor text, code or asset is
reproduced; structures below are reconstructions from documented commands and
published keyboard behaviour. `UNVERIFIED` = not stated in any source we read, which
is **not** a claim of absence.

---

## 1. Their menus, as trees

### 1.1 Phanes — flat command namespace, no navigation tree

```
/start
├── /help .................... one in-chat help card (flat list, ~50 rows)
├── [typed query] $sol / CA / dexscreener link
│    └── price card
│         ├── 💎 holders ...
│         ├── quick-task buttons (user picks up to 6 via /qt)
│         └── chart buttons
├── /c [flags] ............... `/c help` = second, inline help surface
├── /th /tw /tt /ws /dev /nar /thesis /soc /s /x /do /gh /dp /tldr /tr /v /p /i
├── /hm /hmc /hms /rekt /chains /runners /tren /ctos /markets /cal /earnings /today
├── /poly  /fix (add|del|list + "Clear all")  /lang  /ctheme
└── group, admin-only (whisper reply)
     ├── /settings → panel: 18 toggles in 2 sections (General 17, Alerts 7)
     │                (Discord: 10 toggles)
     ├── /alerts /xret /xfeed /yt /pnlbg /dd
     └── /lb /calls /pnl /gpnl /fc /elb /dapp
```
Depth 1 everywhere. No back button anywhere — a screen is a message, not a place.
Only in-place state editors are the `/settings` toggles and `/fix` (which does ship a
`Clear all` button).

### 1.2 Heimdall / Odin — deliberately zero menu

```
(no navigation at all)
Add as group admin  →  done
├── passively scans every message
├── deletes/flags known scammer accounts, CAs, fake admins
└── global ban list is the product (ban once → blocked everywhere)
Vendor position: "no commands, no configuration".
```

### 1.3 Skeleton — buybot config by typed subcommand, scanner in a second bot

```
@SkeletonPriceBot (group)
├── buy popups (auto) → card + ~3-4 action buttons
│    ├── threshold / emoji scaling / media / bulk 24 buys / pinned stream
│    └── buy batching: 24 buys collapse to one card
├── /stats ............. 3-hour auto stats pulse (togglable)
├── /market ............ top 15 / 100
├── /lb ................ group caller leaderboard
├── [buybot setup] ..... typed subcommands, no panel
└── /chart (dex minichart)

@Skeletonscanbot (separate product)
├── paste CA → scan (SOL + EVM)
├── holders top 20/30, snipers, first 20 buyers, airdrop map
└── deployer history
```

### 1.4 MajorBuyBot — undocumented

```
t.me/MajorBuyBot  (profile page only, 2026-10-28)
├── group buy-popup product
└── menu / keyboard / setup flow: UNVERIFIED — no docs site, no published command list
```
Scored below only where observable; the unobservable axes are marked `U` and the total
is `n/a`. It is not a navigation benchmark, it is an absence of one.

---

## 2. Scoring

Scale 0-5 per axis, 5 = best. `first value` = taps/clicks from opening the bot to a
useful answer. `buttons/screen` rewards ≤6. `state` = can the user see what is armed,
watched, or last-fired without running a command.

| Axis | Phanes | Heimdall/Odin | Skeleton | MajorBuyBot | **OURS (target)** |
|---|---:|---:|---:|---:|---:|
| Steps to first value | 3 | 5 | 3 | U | **5** |
| Menu depth (≤2) | 2 | 5 | 3 | U | **5** |
| Buttons per screen (≤6) | 4 | 5 | 4 | U | **5** |
| Label clarity | 3 | 3 | 3 | U | **5** |
| Back / cancel consistency | 2 | 3 | 2 | U | **5** |
| State visibility | 3 | 1 | 3 | U | **5** |
| Error recovery | 3 | 1 | 2 | U | **4** |
| **Total /35** | **20** | **23** | **20** | **n/a** | **34** |

Why Heimdall scores high despite feeling empty: "zero configuration" legitimately
minimises depth, buttons and steps. It loses hard on the two things a guard must have —
you cannot see what it is protecting, and you cannot see why it deleted your message.
Cheap to beat, and we should beat it without giving up zero-setup defaults.

Phanes loses on the same axis it looks strongest: 50 verbs at depth 1 is not a shallow
menu, it is an unindexed one. `/th /tw /ws /dp /soc /fc /elb /gpnl /xret` all require the
docs tab to decode.

Skeleton loses on split identity (scanner is a different bot) and on typed setup.

---

## 3. Our structure (the design)

Four rules, then the concrete screens.

1. **≤2 taps to anything.** Root screen holds 6 job buttons; every other screen is one
   tap from root; every action is one tap from its screen. Typeable commands stay as a
   power path and are never the only path.
2. **One screen = one job.** No screen mixes "configure" and "answer". A scan screen
   scans; a settings screen toggles. Mixed screens are where competitors' panels rot.
3. **Persistent context line.** Line 1 of every screen, same slot, same shape. It is the
   answer to "what am I watching?" without a command.
4. **Nothing destructive on a single tap.** Every destructive callback routes through a
   nonce-bound confirm with a 60 s expiry; a stale or replayed tap is refused, not obeyed.

### 3.1 Root — `/start` (6 buttons, 2 jobs never share a row)

```
╭─ 🐋 RugMunch · Free · 3 alerts · watching $WIF · 12m
│
│  What do you want to do?
│
│  [ 🔍 Scan a token ]  [ 🧠 Run intel ]
│  [ 🔔 Alerts ]        [ 💼 Wallet ]
│  [ ⚙️ Settings ]      [ ❓ Help ]
╰─ tap a job — everything else is 2 taps away
```

Job framing, not feature framing: "Scan a token" tells the user what will happen.
"Scan" (current v11) and "Intelligence" are nouns; they name our org chart, not the job.

### 3.2 Scan — one job, one screen

```
╭─ 🐋 Free · 3 alerts · watching $WIF
│
│  🔍 Scan a token
│  Send a contract address, or paste a DexScreener link.
│
│  [ 📋 Paste CA ]      [ 🖼 Send screenshot ]
│  [ ‹ Back ]           [ 🏠 Home ]
╰─
        ↓ address received
╭─ 🐋 Scanning $WIF · sol · 8h old
│
│  VERDICT  ⚠️ CAUTION — 2 high, 3 medium
│  Mint renounced ✓   Freeze n/a   LP 71% locked ⚠️
│  Top10 38% ⚠️       Dev sold 100% 🚨
│
│  [ 🔬 Deep scan ]  [ 👥 Holders ]  [ 📈 Chart ]
│  [ 🔔 Alert me ]   [ ↗ Share ]
│  [ ‹ Back ]        [ 🏠 Home ]
╰─ verdict first, evidence second — never a wall of flags
```

### 3.3 Alerts — the state screen (what am I watching?)

```
╭─ 🐋 Free · 3 alerts armed · last fired 2h ago ($WIF +31%)
│
│  🔔 Alerts
│  1. $WIF  above $0.42   armed   fired 2h ago
│  2. $BONK below $0.018  armed   never
│  3. $POPCAT +30% / 1h   paused  —
│
│  [ ➕ New alert ]  [ ⏸ Pause all ]
│  [ 🗑 Remove ]     [ ⚙️ Limits ]
│  [ ‹ Back ]        [ 🏠 Home ]
╰─ the list IS the state; no /alerts round-trip to find out
```

`[ 🗑 Remove ]` opens one row per alert, and each row's tap is `q:` (confirm), not `a:`.

```
╭─ 🐋 3 alerts · removing $BONK below $0.018
│  Remove this alert? It cannot be restored.
│  [ ✅ Yes, remove ]   [ ✕ Keep it ]
╰─ nonce-bound · 60 s · a stale tap is refused
```

### 3.4 Settings — grouped, ≤6 per screen, no 18-toggle wall

```
╭─ 🐋 Settings · group: RugMunchers · admin
│
│  [ 🔍 Scanning ]  [ 🔔 Alerts ]
│  [ 🛡 Moderation ] [ 💳 Plan ]
│  [ ‹ Back ]       [ 🏠 Home ]
╰─
        ↓ Scanning
╭─ 🐋 Settings › Scanning
│  ✅ Auto-scan contract addresses posted here
│  ✅ Show holder summary on scan cards
│  ⬜ Deep-scan by default (slower)
│  [ ‹ Back ]  [ 🏠 Home ]
╰─ 3 toggles, current values visible, one tap each, no Save button
```

### 3.5 `callback_data` naming (concrete, 64-byte safe)

Telegram caps `callback_data` at 64 bytes. Long subjects (mint addresses) are **never**
inlined — a short reference is stored server-side and resolved per tap, the same
`mint_ref` discipline the buybot already uses.

| Prefix | Meaning | Shape | Example |
|---|---|---|---|
| `m:` | open a screen | `m:<screen>[:<ref>]` | `m:scan`, `m:alerts` |
| `a:` | run an action, no confirm | `a:<screen>:<verb>[:<ref>]` | `a:scan:run:7f3a91c2` |
| `q:` | destructive → confirm | `q:<screen>:<verb>:<ref>:<nonce>` | `q:alerts:rm:2:9c1e` |
| `s:` | toggle state in place | `s:<screen>:<key>` | `s:set.scan:deep` |
| `n:` | navigate | `n:back:<screen>` / `n:home` | `n:back:m:alerts` |
| `x:` | cancel / dismiss | `x:<screen>` | `x:confirm` |

Rules that make this safe:

- `ref` is **opaque and short** (8 hex chars). Any handler that receives a `ref` re-reads
  the subject from the store and re-validates ownership — a tap can never assert state
  the server does not hold.
- `nonce` is single-use, 60 s, bound to `(user_id, screen, verb, ref)`. Replays are
  refused with the "already handled" card, never re-executed.
- Compat: the current `menu_cat_*` / `menu_main` names stay valid as aliases for one
  release; `menu_cat_<x>` → `m:<x>`, `menu_main` → `n:home`.
- Existing buybot callbacks (`buy|<chat>|<mint_ref>|<amount>`) are unchanged — they are
  already ref-based and claim-gated.

### 3.6 Error recovery (every failure card has the same four parts)

```
╭─ 🐋 Scan failed
│  Could not read that address — it is 41 characters, Solana needs 32-44 base58.
│  Last good screen: Scan a token
│  [ 🔁 Retry with what I sent ]  [ ‹ Back ]  [ 🏠 Home ]
╰─ what happened · what to do · where I came from · how to leave
```

No `[ OK ]`. No dead-end card anywhere. A card that cannot explain itself in one sentence
is a bug, not a copy problem.

---

## 4. Delta — theirs vs ours

```
THEIRS (Phanes, typical)                 OURS
─────────────────────────────────────    ─────────────────────────────────────
50 typed verbs at depth 1                6 job buttons at depth 1
memory is the menu                       the menu is the menu
no state line                            context line on every screen
message = terminal                       message = place with ‹ Back / 🏠 Home
settings = 18 toggles in one panel       settings = 3 per screen, value visible
no confirm on config changes             q: nonce confirm on every destructive tap
error = "invalid input"                  error = what/why/where/retry
```

Against Heimdall specifically: we keep their zero-setup win (safe defaults on install,
no configuration required to be protected) and add the two things they cannot show —
**what is armed** and **why something was removed**, plus an appeal path.

Against Skeleton specifically: their scanner lives in a second bot. Ours is one bot; scan
is a job on the root screen, not a separate product to discover. Their buybot is still
the retention benchmark inside a token's own group — this spec does not touch buy popups
except to note that buy cards must carry the same context line.

---

## 5. The three UX failures every competitor repeats

1. **The command list is the menu.** Discovery is a docs tab, not a tap. Phanes ships
   ~50 flat verbs (`/th /tw /ws /dp /soc /fc /elb /xret`) whose meaning lives in
   documentation; Skeleton encodes setup in typed subcommands; Heimdall removes the menu
   entirely and calls it a feature; MajorBuyBot publishes none. All four put the recall
   burden on the user.
   **Ours:** 6 buttons at root, every job ≤2 taps, job-labelled ("Scan a token", not
   "Scan"), 20-command BotFather ceiling already enforced by `MAX_ADVERTISED`, everything
   else under `/more` with grouped help. Typing stays available, never required.

2. **No state, anywhere.** No competitor answers "what am I watching / what is armed /
   when did it last fire" without re-deriving it from a command, and none shows why an
   action was taken. Heimdall deletes a message and says nothing; Phanes' alert config is
   only visible by re-opening `/settings`; Skeleton's buybot threshold is invisible until
   the next buy.
   **Ours:** a persistent context line on every screen (`3 alerts · watching $WIF · last
   fired 2h ago`) and a state-first Alerts screen where the list *is* the state. Group
   actions log to the same place the user can read.

3. **No back, no undo, no exit.** Screens are terminals: once a panel opens, the only
   recovery is retyping a command. Nobody ships a consistent `Back`; nobody binds a
   confirm to a destructive tap; every error card dead-ends.
   **Ours:** `n:back:<screen>` and `n:home` on every screen in a fixed position; every
   destructive callback is `q:` with a single-use 60 s nonce so one tap cannot destroy
   anything; every failure card carries what/why/where/retry, and no card ends in `[OK]`.

Honourable mention (not universal, but worth not copying): **one job, two products** —
Skeleton's scanner is a separate bot, Phanes pushes group admin to a DApp. We keep one
identity: if a job exists, it is a screen in this bot.

---

## 6. Implementation notes / open items

- Root keyboard replaces `_main_menu_rows()` in `keyboards/_menus.py`; the current
  `menu_cat_*` callbacks are aliased, not renamed, for one release.
- Context line is one function `context_line(user, chat) -> str`, rendered into every
  screen's first block — no screen assembles its own header.
- `mint_ref` store already exists in the buybot path; reuse it rather than inventing a
  second one (single canonical truth).
- [REVIEW] MajorBuyBot's menu is unverified. If it publishes docs later, re-score its row
  and re-run the totals; the recommended structure does not depend on that row.
- [REVIEW] The 34/35 is a design target, not a measurement. It should be re-scored after
  implementation by tapping the shipped bot, not by reading this file.
