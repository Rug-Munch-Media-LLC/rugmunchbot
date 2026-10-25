# Orphan recovery triage — voting / settings / prediction cards

Source: `/tmp/wt-orphan/orphan-unique/` (abandoned worktrees).
Triage date: 2026-10-24. Branch: `bot-archive`.

**Result: 0 files recovered, 5 rejected.** Every candidate is either superseded
by code already on `main`, duplicative of an existing pipeline, or broken and
unwireable. Nothing was ported because nothing passed the duplicate test.

## Verdicts

| Archived file | Lines | Verdict | Reason |
|---|---:|:---:|---|
| `bot-admin/.../community/settings.py` | 648 | Obsolete | Superseded by the 7-module split already on `main`. |
| `bot-prediction/.../smart_money/prediction_cards.py` | 293 | Duplicate | Repo already ships a Polymarket+Kalshi+Manifold pipeline, wired. |
| `bot-voting/.../db/voting.py` | 603 | Broken | DDL is a SQLite syntax error; also bypasses `db._base`. |
| `bot-voting/.../db/__init__.py` | 250 | Obsolete | Superset of ours by exactly the voting re-exports; those have no target. |
| `bot-voting/.../community/voting.py` | 377 | Incomplete | Business logic only — no `/vote` command, no callback, anywhere. |

## Evidence

### 1. `community/settings.py` — obsolete (monolith already split)

The archived docstring advertises one module owning models **and** Redis
persistence. `main` already carries that split, so re-adding the monolith would
fork a single source of truth:

- `community/config_models.py` — `ModerationConfig`, `VotingConfig`,
  `FeaturesConfig`, `CommunitySettings` (+ field validators, `MODERATION_FIELDS`,
  `VOTING_FIELDS`, `FEATURE_FIELDS`)
- `community/store.py` — `load_settings` / `save_settings` / `delete_settings` /
  `settings_key` (Redis)
- `community/api.py` — `get_voting_config` and the stable read API
- `community/render.py`, `audit.py`, `commands_config.py`, `validation.py`

Rejected: duplicate, and 648 lines would breach the 200-line new-file cap.

### 2. `smart_money/prediction_cards.py` — duplicate, and unwired

The module fetches Polymarket / Manifold / Kalshi and renders standings cards.
`main` already covers all three venues and the publishing path:

- `tools/prediction_markets.py` — Polymarket (backend route *and* Gamma API) plus
  `kalshi:trade-api/v2/markets`; `_MIN_VOLUME_USD = 100_000`
- `smart_money/predictions.py` (`fetch_predictions`) +
  `smart_money/render_prediction.py` (`render_prediction`)
- wired via `channels/automation/runner.py` (`"odds": (fetch_predictions,
  select_odds)`) and `handlers/smart_money.py`

`prediction_cards` is imported by nothing in the archive either — it was never
wired, and its thresholds ($5k Polymarket / $1k Manifold / $10k Kalshi) are a
parallel policy, not an improvement on the $100k venue filter.

### 3. `db/voting.py` — broken, cannot run

The DDL emits unquoted defaults, so `executescript` raises on **every**
connection attempt:

```
description TEXT DEFAULT ,
status TEXT DEFAULT open,
flag_reason TEXT DEFAULT ,
cluster_id TEXT DEFAULT ,
risk_factors_json TEXT DEFAULT {},
```

Reproduced:

```
$ python3 -c 'sqlite3.connect(":memory:").executescript(ddl)'
SCHEMA FAILS: OperationalError near ",": syntax error
```

Same defect in the query layer: `WHERE chat_id = ? AND status = open`.
Compounding problems: `_conn()` opens its own `sqlite3.connect(DB_PATH)`
instead of `db._base.get_db`, so it skips the package-level path monkeypatching
that the test suite depends on; and 603 lines is 3x the new-file cap.

Rejected: non-functional. Recovering it means rewriting the layer, which is a
new feature, not a port.

### 4. `db/__init__.py` — obsolete

Diff against ours is the voting import block and 20 `__all__` entries; the other
~180 lines are identical. The current 210-line shim is canonical. Re-adding this
file without `db.voting` produces an ImportError at package import — it would
break the whole bot, not just voting.

### 5. `community/voting.py` — incomplete, so not wired

This is the deciding file for the wiring question. It contains poll CRUD
helpers, a cluster detector and an export path — and **no Telegram surface**:

- no `CommandHandler`, no `CallbackQueryHandler`, no `add_handler` — zero matches
  for vote/poll registration anywhere in the entire orphan tree
- no entry in `command_catalog.py` (so `/poll` and `/vote` are not even in the
  help surface)

It also depends on the broken `db.voting` layer and runs 377 lines.
Per the recovery contract — wire commands/callbacks **only if complete** —
voting is **rejected**.

## What a real voting port would need

Not attempted here; recorded so the next attempt starts from facts:

1. Rewrite the DDL with quoted defaults (`DEFAULT ''`, `DEFAULT 'open'`,
   `DEFAULT '{}'`) and route through `db._base.get_db` instead of a private
   connection.
2. Split to satisfy the caps: DB access, cluster detection and rendering are
   three modules, none over 200 lines.
3. Build the `command_catalog.py` entry plus `/poll` and `/vote` handler
   modules, with `chat_admin` checks and the `VotingConfig` values from
   `community/config_models.py` as the source of truth.
4. Only then evaluate the cluster detector on its own merits.
