# Voice eval baseline

Measured 24 gold samples from real output paths with the scorer
(`eval/voice/score.py`). Gate: `--min 0.75`.

## Calibration seam (public shape, private calibration)

The sub-scores are public; the weights are not. A public checkout scores on the
uniform shape-only default (`policy.example_weights()`, uniform 1/7). The fleet
calibration is injected with `RMB_VOICE_WEIGHTS_FILE=<path.json>` — a JSON
object keyed by the seven sub-score names, positive, summing to 1.0 (see
`eval/voice/policy.py`). The table below records *which rules failed* per
sample — numeric per-sample scores under the fleet calibration are fleet data
and live outside this repo.

| sample | category | failing rules / phrases |
|---|---|---|
| `error-scanner-timeout` | error | emoji=2/msg; structure=missing fact; register=joke in error: '😅' |
| `loss-liquidation` | loss | banned='ngmi'; spread=uniform (sd 1.7); emoji=2/msg |
| `content-llm-hype` | content | banned="we're so back"; emoji=3/msg; structure=missing headline |
| `content-generic-filler` | content | cliche="in today's fast-paced" (density 9.00); structure=missing headline; structure=missing fact |
| `help-menu` | onboarding | spread=uniform (sd 0.75); emoji=2/msg; structure=missing fact |
| `help-full` | onboarding | spread=uniform (sd 1.04); structure=missing fact; structure=missing cta |
| `alert-sell-restricted` | security | spread=uniform (sd 1.41); structure=missing fact; structure=missing cta |
| `mod-gban` | moderation | spread=uniform (sd 1.5); structure=missing fact; structure=missing cta |
| `mod-quarantine` | moderation | spread=uniform (sd 0.5); structure=missing fact |
| `alert-lp-pulled` | security | spread=uniform (sd 0.83); structure=missing cta |
| `error-rate-limit` | error | spread=uniform (sd 1.0); structure=missing fact |
| `error-bad-address` | error | structure=missing headline; structure=missing fact; structure=missing cta |
| `alert-whale-cex` | alert | spread=uniform (sd 1.94); structure=missing cta |
| `scan-critical` | security | structure=missing cta |
| `scan-clean` | scan | structure=missing cta |
| `scan-unknown` | scan | structure=missing cta |
| `scan-medium` | scan | structure=missing cta |
| `onboarding-start` | onboarding | structure=missing fact; structure=missing cta |
| `onboarding-returning` | onboarding | structure=missing fact; structure=missing cta |
| `mod-appeal-denied` | moderation | structure=missing fact; structure=missing cta |
| `content-daily-rug` | content | spread=uniform (sd 1.48) |
| `alert-exploit-formatted` | security | emoji=2/msg; register=emoji stack on serious message |
| `alert-sniper-bundle` | security | structure=missing cta |
| `content-shill-campaign` | content | structure=missing cta |

## Scoring shape (public)

Seven sub-scores — banned, cliche, spread, emoji, hedge, structure, register —
each scored 0-1 by deterministic rules in `score.py`, combined by the
calibration (weights sum to 1.0, resolved by `policy.load_weights()`). A joke
in a serious register (loss / security / error / moderation) is a hard veto
(score 0). The CI gate is the aggregate versus `TARGET_MIN` (0.75).

## Caveat

`current` texts reconstruct the shipped templates and lanes at commit c5bc812
(alert pipeline, risk verdict, exploit formatter, help/onboarding, error paths,
moderation, Daily Rug envelope, campaign post). They are reviewed fixtures, not
a live capture — re-capture after a template change.
