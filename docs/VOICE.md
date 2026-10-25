# VOICE — rugmunchbot

## Principles

1. Dry by default. The joke is in the understatement, never in an emoji.
2. Witty is the resting state; **serious is mandatory** on a loss, a rug, a hack, or a ban.
3. Structure is predictable: same situation, same shape, same wording on a re-run.
4. State the number, then the consequence. No hedging stacks ("could potentially").
5. One sentence of opinion, maximum. The data is the argument.
6. Never apologise for working, and never narrate your own process.
7. No hype vocabulary: no rockets, no moons, no LFG, no exclamation stacking.
8. An error says what failed and what did not change. Nothing else.
9. If it reads like a press release or a chatbot, it is wrong — rewrite, don't decorate.
10. We are the desk that tells you the trade is bad. That is the brand.

## Registers

| Situation | Register | What to never say |
|---|---|---|
| `command.ok` (watch saved, removed) | witty | exclamation marks; "successfully" |
| `command.progress` (loading, scanning) | neutral | "📊 Loading your portfolio...", "🧬 Sequencing token DNA..." |
| `command.empty` (no watches, no results) | witty | "Nothing to see here!" |
| `plan.already_active` | neutral | "Free tier is already active!" |
| `error.transient` (RPC down, timeout) | neutral | "I'm sorry", "Please try again in a moment." |
| `error.user_input` (bad number, bad id) | neutral | "Oops!", "Invalid input, please retry" |
| `scan.clean` | witty | "safe", "guaranteed", "you're good" |
| `scan.caution` | neutral | any joke; any softened verb |
| `scan.danger` | serious | emoji, jokes, "do your own research" |
| `alert.launch` | neutral | "🚀 LAUNCH CALL", "aping", "send it" |
| `alert.whale` | witty | "whale alert 🐋🐋🐋" |
| `alert.rug` | serious | exclamation, schadenfreude, "stay safe out there" |
| `alert.security` | serious | emoji, jokes, vague "suspicious activity" |
| `loss.disclosure` | serious | euphemism; hiding the stop level |
| `onboarding.welcome` | witty | "Welcome! 🚀 Let's dive in!" |
| `onboarding.help_hint` | neutral | wall of text; "please note" |
| `moderation.notice` / `.appeal` | serious | "oops", emoji, public shaming |
| `market.noop` | neutral | "No further action needed" |
| `market.recap` | witty | hype verbs, "the crypto space is buzzing" |

## Banned phrases

Two tiers. `support/content_lint.BANNED_PHRASES` (the post list: `"dyor"`, `"nfa"`, `"gm"`,
`"wagmi"`, `"mooning"`, `"hodl"`, `"delve into"`, `"ever-evolving landscape"`, `"robust"`,
`"revolutionary"`, `"it's worth noting"`, `"stay tuned"`, …) plus the chrome offenders
that actually shipped in this tree:

| Banned | Where it came from |
|---|---|
| `"Please try again in a moment."` | 8 handler branches, verbatim — the single most botlike string we ship |
| `"🚀"` | `channels/automation/render_call.py` — `🚀 <b>LAUNCH CALL · {symbol}</b>`; `formatters/_sentinel_helpers.py` pumpfun icon |
| `"🧬 Sequencing token DNA..."` | scan progress narration |
| `"📊 Loading your portfolio..."` | progress narration dressed as personality |
| `"Free tier is already active!"` | exclamation on a non-event |
| `"No further action needed"` | sterile filler in 5 channel publishers |
| `"As an AI"`, `"Per my data"`, `"In this chat"` | already banned in `prompts.py`; now also banned in chrome |
| `"I'm sorry"`, `"Please note"`, `"Great question"`, `"Let's dive in"`, `"I hope this helps"`, `"Feel free to"` | assistant-tell openers |
| `"LFG"`, `"to the moon"`, `"🔥"`, `"💎"`, `"🌙"` | hype emoji and slogans |
| `!!`, `!!!`, `lol`, `lmao` | exclamation stacking, text-speak |

Enforced by `tests/test_voice_lint.py`. Legacy occurrences are pinned in
`tests/voice_legacy_offenders.txt`; the test fails on any **new** hit *and* on any
**stale** exemption, so the list only ever shrinks.

## Before / after (10 real strings)

| # | Before (current) | After (through `voice.render`) |
|---|---|---|
| 1 | `❌ Could not load your watchlist ({exc})` | `err.action_failed` → "Watchlist didn't go through ({detail})." |
| 2 | `❌ Could not ban the user. Please try again in a moment.` | `err.action_failed` → "Ban failed ({detail}). Nothing was changed." |
| 3 | `Free tier is already active!` | `plan.already_active` → "Free tier is already on." |
| 4 | `📊 Loading your portfolio...` | `cmd.portfolio_loading` → "Loading portfolio." |
| 5 | `🧬 Sequencing token DNA...` | `scan.progress` → "Scanning." |
| 6 | `🚀 <b>LAUNCH CALL · {symbol}</b>` | `alert.launch` → "New pair: {symbol}. Launched {age}." |
| 7 | `No further action needed` | `market.noop` → "Nothing changed since the last scan." |
| 8 | `Rate limit reached — try again in a minute.` | `err.rate_limit` → "Rate limited. Next slot in about a minute." |
| 9 | `❌ No watchlist entry with that id.` | `err.no_such_watch` → "No watch entry with that id." |
| 10 | `Days must be between 1 and 3650.` | `err.bad_days` → "Days has to be a whole number from 1 to 3650." |

**Already correct, keep as the reference:** `"No KOLs on the wall this week. Clean slate — for now."`
(`channels/shill_watch/_runner.py:393`) — dry, one clause of opinion, no emoji, no hype.
That is the tone the table above is aiming at.

## Determinism

`render(key, ctx, message_id=…)` seeds a BLAKE2s digest with `message_id` + key and picks the
variant by the first byte. Same input, same words — across processes, restarts and deploys.
`hash()` is not used: it is salted per process and would rotate the voice on every restart.
SERIOUS keys hold exactly one variant, so a loss alert cannot rotate into a joke.
