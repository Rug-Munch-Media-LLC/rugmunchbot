# Moderation — Heimdall parity matrix (2026-10-24)

Scope: `rugmunchbot` group-admin capability vs **Heimdall** (Odin Tools' free
anti-scam group bot, the only documented moderation competitor in
`docs/competitors/feature-matrix.md` §3 [15]).

Engine audited: `src/rugmunchbot/moderation/` (the documented engine, wired by
`moderation/register.py` → `bot/_entry.py:321`). `docs/MODERATION.md` is its
contract; this file records the delta against Heimdall and what closed it.

## 1. Evidence

| Ref | Source | What it proves |
|---|---|---|
| [15] | https://docs.odin.tools/tools/heimdall-protection-bot | Heimdall: global scammer list shared across groups, phishing/drainer/fake-CA/impersonation deletion, auto ban/mute/report **with admin alerts**, suspicious Solana CA flag, zero setup, free |
| — | `src/rugmunchbot/moderation/*` | our engine: `gate.py`, `captcha.py`, `rules.py`, `links.py`, `names.py`, `nsfw.py`, `address_rule.py`, `actions.py`, `audit.py`, `gban.py`, `propagate.py` |
| — | `configs/moderation.json` | shipped state is `{"chats": {}}` — inert until an operator opts a chat in |

## 2. Matrix

`✓` present · `+` stronger than Heimdall · `—` was missing, now closed · `✗` still absent

| Capability | Heimdall | rugmunchbot | Notes |
|---|---|---|---|
| Global scammer list across groups | ✓ | ✓ `gban.py` | store-backed `NS:global:gbans`, `/gban /ungban /gbans` |
| Phishing / drainer link deletion | ✓ | ✓ `links.py` | `scam_domains` + `scam_substrings` + invite links |
| Fake / suspicious CA flag | ✓ (Solana) | + `address_rule.py` | EVM **and** Solana, real scanner verdict + risk score, per-policy action |
| Impersonation removal | ✓ | ✓ `names.py` | homoglyph/leet folding, brand+role, chat-name lookalikes |
| Auto ban / mute | ✓ | ✓ `actions.py` | duration clamps, per-op error capture |
| **Admin alerts / modlog** | ✓ | **— now** | `modlog.py` + `community.modlog_enabled` / `modlog_chat_id` / `modlog_linked`; mirrors `alert`/`mute`/`ban` and any applied action |
| **Report a message to admins** | ✓ (auto) | **— now** | `/report` as a reply in a group → audit row `user_report` + modlog line; throttled 3/10 min; never punishes |
| **Per-chat rules text** | ✗ | **— now** | `/rules` (member) + `/setrules` / `/clearrules` (admin, needs `community.rules_enabled`) |
| Logs / audit trail | ~ | + `audit.py` | append-only JSONL with `action_id`, `dry_run`, `applied`, `error`; `/modactions` |
| Zero-setup protection | ✓ | ~ by design | requires an operator chat block **and** `dry_run:false` — see §4 |
| Captcha / verification | ✗ | + `captcha.py`, `challenge.py` | button or math, mute-then-kick deadline |
| Anti-flood / duplicate | ~ | + `rules.py` + `state.py` | per-user and per-chat velocity, duplicate digests, media burst |
| Welcome / goodbye | ✗ | + `welcome.py`, `settings_cmd.py` | `/setwelcome`, `/setgoodbye` |
| Federated ban propagation | ✓ | ✓ `propagate.py` | opt-in per chat (`propagate_ban` + `propagate_chats`) |
| Content locks (media/sticker/link-only) | ✗ | ✗ | not implemented; `media_burst` + `nsfw_hashes` cover the abuse shapes we see |

## 3. What shipped in this change

| File | Purpose |
|---|---|
| `moderation/modlog.py` | resolve a log destination (explicit id → linked channel), format one HTML line per decision, deliver best-effort |
| `moderation/report.py` | group `/report` — evidence, not a verdict; audit + modlog; throttled; honest when unconfigured |
| `moderation/rules_cmd.py` | `/rules`, `/setrules`, `/clearrules`; per-chat override in the community store, JSON stays the ceiling |
| `moderation/actions.py` | `_mirror()` — mirrors `alert`/`mute`/`ban` and applied actions into the modlog |
| `moderation/chatconf.py` | `modlog_enabled`, `modlog_chat_id`, `modlog_linked`, `rules_enabled`, `rules_text` (all default off/empty) |
| `handlers/report.py` | branches to the group shape; DM scam-address intake unchanged |

## 4. Safety properties (fail-closed)

1. **Nothing is on by default.** `configs/moderation.json` ships `{"chats": {}}`; every new
   community switch (`modlog_enabled`, `modlog_linked`, `rules_enabled`) is `False`.
   A chat that is not in the file is touched by nothing — the gate returns early.
2. **Dry-run is the default for every configured chat** (`policy.dry_run: true`). No delete,
   mute, ban, kick or reply happens until an operator flips it per chat.
3. **Logging never blocks moderation.** `modlog.deliver` swallows every Telegram error and
   returns `False`; `_mirror` wraps it again. A broken log channel cannot stop a ban.
4. **A report is evidence, not a verdict.** `/report` performs no action against the reported
   member; the only API call is the modlog message. Verified in
   `tests/test_moderation_report.py::test_no_report_action_against_the_target`.
5. **Member-facing text leaks no ops detail.** `/rules` on an unguarded chat shows generic
   house rules, never the config path or "enforcement is off".
6. **Bounded memory.** Report throttle prunes per (chat, user); state windows are capped at
   `MAX_KEYS` / `MAX_EVENTS`.
7. **Escaping.** Every dynamic value in a modlog line goes through `html.escape`
   (`test_explicit_chat_receives_escaped_line` proves `<b>evil</b>` cannot inject markup).

## 5. Residual gaps `[REVIEW]`

- **Content locks** (media-only / link-only chats) — Heimdall does not document them either;
  not implemented. Would belong in `rules.py` as another ordered rule.
- **Zero setup** — Heimdall auto-protects on add with no config. We deliberately do not:
  auto-enabling anything on join would be a destructive default. An operator block is the
  price of fail-closed behaviour; the harness + `/modstatus` make onboarding one line of JSON.
- **Second engine** — `handlers/moderation.py` + `community/moderation*.py` still registers
  `/modstatus /modrules /modactions /warn /mute /ban /trust /untrust /modpardon` in the same
  handler group as this engine. That is a pre-existing duplicate surface (`/modstatus`,
  `/modactions`, `/modpardon` are served by both). Consolidating it is a separate change:
  it needs the buybot's `community.models` dependency untangled first.
