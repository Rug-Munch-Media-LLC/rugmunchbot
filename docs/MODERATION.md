# Channel guard / moderation

Layer inside `rugmunchbot` (not a second bot). Registered in handler group **-1**, so a
deleted spam message never reaches the AI chat pipeline.

## Enable (one line per chat)

1. Add the chat to `configs/moderation.json` (copy a block from `moderation.example.json`,
   replace `-1001234567890` with the real chat id).
2. Watch `🧪 dry-run` rows in `data/moderation_actions.jsonl` (or `/modactions` as chat admin).
3. Flip **that chat's** `"dry_run": true` → `false`, restart the bot.

Unlisted chats are untouched. `{"chats": {}}` (shipped) = moderation fully inert.

## Rules and default action

| Rule | Signal | Default action |
|---|---|---|
| `new_member_gate` | join → captcha button, muted until tapped | mute + prompt |
| `scam_link` | URL host/path matches `scam_domains` | delete + warning card |
| `scam_phrase` | `scam_substrings` in text | warn |
| `invite_link` | `t.me/+` or `t.me/joinchat/` | delete |
| `promo` | `promo_patterns` (100x, presale, airdrop live, DM me…) | warn |
| `unlisted_link` | URL outside `links_allow` (incl. hidden `text_link` targets) | warn |
| `impersonation` | display name folds to brand + role ("RugMunch Support") | warn |
| `address_risk` | posted Solana/EVM address scans `avoid`/`high` | warn (delete when `avoid`) |
| `repeat_message` | same normalised text ≥ `dup_count` in `dup_window_s` | delete |
| `user_velocity` | ≥ `user_msgs` in `velocity_window_s` | mute `velocity_window_s` min |
| `chat_raid` | ≥ `chat_msgs` in the chat in `velocity_window_s` | alert admins (no auto-action) |
| `media_burst` | ≥ `media_burst` media posts/60s from an account <10 min old | delete |

Exempt: `exempt_user_ids` and (unless `exempt_admins: false`) chat admins/owner. Bots ignored.

## Safety properties

- **Dry-run calls zero Telegram write APIs** (`scripts/moderation_dryrun.py` asserts this).
- Every decision is appended to `data/moderation_actions.jsonl` (JSONL, one line, no secrets).
- Scan failures → verdict `unknown` → no action (fail-open, never a false delete).
- Per-op error isolation: a missing admin right is audited, never raised into the dispatcher.
- Address scans are budgeted to 6/minute per chat so a shill raid cannot hammer the scanner.

## Admin / appeal path

- `/modstatus` — effective policy + DRY-RUN/LIVE for this chat.
- `/modactions [n]` — last n decisions (flag: 🧪 dry-run, ✅ applied, ⚠️ failed).
- `/modpardon <user_id> [reason]` — lift mute/ban, audited.
- `/appeal <text>` in a **private DM** — logged against the user's last action id.

## Limits (honest)

- [REVIEW] NSFW detection is a media-burst heuristic on new accounts, not a classifier.
- Impersonation DMs between users are invisible to a bot; only in-chat names are checked.
- Velocity/dedup state is in-process: a restart forgets the windows (no rule fires on stale data).
- `captcha_kick` is off by default; the bot must be admin with `restrict_members` to mute.
