# Channel guard / moderation

One layer inside `rugmunchbot` (no second bot, no second token). Registered in handler
group **-1**, so a deleted spam message never reaches the AI chat pipeline. Ships inert:
`configs/moderation.json` is `{"chats": {}}` — unlisted chats are untouched.

## Module map

| Module | Responsibility |
|---|---|
| `gate.py` | The only entry point (group -1): routes joins / captcha taps / messages, builds `MsgContext`, raises `ApplicationHandlerStop` when a decision blocks |
| `captcha.py` | New-member gate: mute, button **or** short-math challenge, welcome, hands the deadline to `timeout` |
| `captcha_verify.py` | Tap verification: ownership check, math answer, pass (unmute+greet) / fail (mute or kick) |
| `challenge.py` | Stateless math challenge, seeded by `(chat, user, 5-min bucket)` |
| `timeout.py` | Deadline: mute or kick a newcomer who never solved it (dry-run audits only) |
| `names.py` | Impersonation: homoglyph/leet folding, brand+role, chat-name lookalikes, optional strict role words |
| `links.py` | URL extraction (incl. hidden `text_link`), allowlist match, scam-domain needles |
| `messages.py` | User-facing HTML cards, every dynamic value escaped |
| `rules.py` | Ordered text/media rules (first hit wins) |
| `nsfw.py` | NSFW hash hook: sha256(`file_unique_id`) blocklist + optional classifier seam |
| `ocr.py` | Optional media OCR seam — **no OCR engine installed**, inert until hooked |
| `addresses.py` | Solana/EVM address extraction + scanner verdict mapping |
| `address_rule.py` | Address verdict → warn/delete per policy |
| `decide.py` | Single decision point: state + text rules + address verdicts → one `Action` |
| `actions.py` | Executes delete/warn/mute/ban with durations; dry-run calls nothing |
| `propagate.py` | Optional cross-chat ban propagation |
| `audit.py` | Append-only JSONL: `actor / rule / action / reason / matched / evidence / dry_run / applied` |
| `commands.py` | `/modstatus`, `/modactions`, `/modpardon`, `/appeal` |
| `config.py` | Per-chat policy loader, hot-reloaded on file mtime, fail-open |
| `harness.py` + `harness_fakes.py` + `harness_cases.py` | Dry harness: 17 synthetic updates, fake transport |

## Gate order

`join → captcha callback → command skip → context/OCR → state windows → text/media rules → address rule → worst(action) → execute → audit`

Text/media order inside `rules.py`: `scam_link → invite_link → nsfw_media → scam_phrase → promo → unlisted_link → media_burst → repeat_message → user_velocity → chat_raid → impersonation`.

Exempt (`exempt_user_ids`, and chat admins unless `exempt_admins: false`) are still counted in the
windows but never actioned. Bots ignored. Scan failures → `unknown` → no action (fail-open).

## Per-chat config (`configs/moderation.json`, schema v2)

| Group | Keys (defaults) |
|---|---|
| Mode | `enabled` true · `dry_run` **true** · `exempt_user_ids` [] · `exempt_admins` true |
| Links | `links_allow` (our domains + docs + exchanges) · `link_action` warn · `scam_domains` · `scam_substrings` · `invite_action` delete |
| Promo | `promo_patterns` (regex list) · `promo_action` warn |
| Velocity | `dup_count` 3 / `dup_window_s` 120 / `dup_action` delete · `user_msgs` 5 · `chat_msgs` 20 · `velocity_window_s` 10 · `user_velocity_action` mute · `chat_velocity_action` alert · `mute_minutes` 10 |
| Ban | `ban_minutes` 0 (=permanent) · `propagate_ban` false · `propagate_chats` [] |
| Address | `address_action` warn (caution) · `address_avoid_action` delete · `address_score` 40 (caution escalates below it) · `address_delete` false |
| Entrance | `new_member_captcha` true · `captcha_type` `button`\|`math` · `captcha_timeout_s` 90 (clamped 30–600) · `captcha_mute_minutes` 10 · `captcha_kick` false · `welcome` "" |
| Media | `media_burst` 2 (per 60 s, new accounts) · `nsfw_action` delete · `nsfw_hashes` [] · `ocr_scan` false |
| Names | `impersonation_action` warn · `impersonation_strict` false · `impersonation_roles` [admin, owner, dev, moderator, support, official, …] |
| Text | `appeal_note` |

Unknown keys are dropped; a missing key can never make a chat live. Config is re-read when
the file's mtime changes — **no restart**.

## Proof: dry harness (zero Telegram writes)

```bash
PYTHONPATH=src python3 -m rugmunchbot.moderation.harness   # run from the repo root
```

Writes a throwaway config + audit file, runs every scenario through the production code path
with `dry_run` on, and fails if a scenario fires the wrong rule or the fake bot is touched.

| # | case | rule | action | reason |
|---|---|---|---|---|
| 1 | join | `new_member_gate` | mute | button captcha, 90s |
| 2 | captcha_pass | `captcha_pass` | unmute | captcha solved |
| 3 | captcha_fail | `captcha_fail` | kick | wrong answer |
| 4 | captcha_timeout | `captcha_timeout` | mute | captcha not solved within 60s |
| 5 | scam_link | `scam_link` | delete | blocklisted scam domain (claim-airdrop) |
| 6 | invite_link | `invite_link` | delete | chat invite link |
| 7 | nsfw_hash | `nsfw_media` | delete | media hash on the NSFW blocklist |
| 8 | promo | `promo` | warn | promotional pitch |
| 9 | unlisted_link | `unlisted_link` | warn | link outside the allowlist |
| 10 | repeat_message | `repeat_message` | delete | same message x3 within 120s |
| 11 | user_velocity | `user_velocity` | mute | 5 msgs within 10s |
| 12 | chat_raid | `chat_raid` | alert | 20 msgs in chat within 10s |
| 13 | address_avoid | `address_risk` | delete | avoid address (critical: honeypot, mintable) |
| 14 | address_high | `address_risk` | warn | caution address (high: top10 91%) |
| 15 | media_burst | `media_burst` | delete | 2 media posts within 60s from a new account |
| 16 | impersonation | `impersonation` | warn | brand + role in name |
| 17 | clean_message | `allow` | none | no rule fired |

`cases=17 audit_rows=16 telegram_api_calls=0`.

## Enable exactly one chat (and revert)

```bash
# live for ONE chat, no restart (hot reload on mtime)
jq '.chats["-1001234567890"].dry_run = false' configs/moderation.json > /tmp/m.json && mv /tmp/m.json configs/moderation.json && jq '.chats["-1001234567890"].dry_run' configs/moderation.json

# revert to dry-run
jq '.chats["-1001234567890"].dry_run = true' configs/moderation.json > /tmp/m.json && mv /tmp/m.json configs/moderation.json
```

Requires the bot to be a chat admin with *delete messages* + *restrict members* (and *ban users*
when `captcha_kick` or `propagate_ban` is on). `job_queue` must be installed for the entrance
timeout — it is; if it ever is not, `timeout.schedule` logs a warning and the gate degrades to
"muted until manually released".

## Live test status

**No safe test chat was available on the build host**, so validation stopped at the dry harness. To smoke
it live, create a private test supergroup, add the bot as admin, put its id in
`configs/moderation.json` with `dry_run: false`, then: join with a second account (captcha prompt),
post `t.me/+abc` (delete), post an allowlisted link (allowed), run `/modstatus` and `/modactions`.

## Limits (honest)

- [REVIEW] NSFW detection is hash-blocklist + media-burst heuristic; the classifier seam is empty.
- [REVIEW] OCR is a seam only — no OCR dependency exists in this repo.
- Impersonation checks in-chat names only (bots cannot see private DMs between members); admin
  display-name lookalikes are not enumerated — only brand, role words and chat-title lookalikes.
- Default `impersonation_action` is `warn`; set `impersonation_strict: true` to flag bare
  `admin/owner/dev` names and raise the action to `ban` per chat if that is the policy.
- Velocity/dedup/verified state is in-process: a restart forgets windows (no rule fires on stale data).
- `chat_raid` alerts, it never auto-actions — a raid must not silence the room.
