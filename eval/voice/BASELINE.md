# Voice eval baseline

Measured 24 gold samples from real output paths with the rules scorer
(`eval/voice/score.py`). Gate: `--min 0.75`.

**Aggregate: 0.7761** (gate 0.75 — PASS)

| sample | category | score | failing rules / phrases |
|---|---|---|---|
| `error-scanner-timeout` | error | 0.000 | emoji=2/msg; structure=missing fact; register=joke in error: '😅' |
| `loss-liquidation` | loss | 0.000 | banned='ngmi'; spread=uniform (sd 1.7); emoji=2/msg |
| `content-llm-hype` | content | 0.610 | banned="we're so back"; emoji=3/msg; structure=missing headline |
| `content-generic-filler` | content | 0.700 | cliche="in today's fast-paced" (density 9.00); structure=missing headline; structure=missing fact |
| `help-menu` | onboarding | 0.763 | spread=uniform (sd 0.75); emoji=2/msg; structure=missing fact |
| `help-full` | onboarding | 0.789 | spread=uniform (sd 1.04); structure=missing fact; structure=missing cta |
| `alert-sell-restricted` | security | 0.803 | spread=uniform (sd 1.41); structure=missing fact; structure=missing cta |
| `mod-gban` | moderation | 0.806 | spread=uniform (sd 1.5); structure=missing fact; structure=missing cta |
| `mod-quarantine` | moderation | 0.819 | spread=uniform (sd 0.5); structure=missing fact |
| `alert-lp-pulled` | security | 0.831 | spread=uniform (sd 0.83); structure=missing cta |
| `error-rate-limit` | error | 0.838 | spread=uniform (sd 1.0); structure=missing fact |
| `error-bad-address` | error | 0.850 | structure=missing headline; structure=missing fact; structure=missing cta |
| `alert-whale-cex` | alert | 0.873 | spread=uniform (sd 1.94); structure=missing cta |
| `scan-critical` | security | 0.875 | structure=missing cta |
| `scan-clean` | scan | 0.875 | structure=missing cta |
| `scan-unknown` | scan | 0.875 | structure=missing cta |
| `scan-medium` | scan | 0.875 | structure=missing cta |
| `onboarding-start` | onboarding | 0.900 | structure=missing fact; structure=missing cta |
| `onboarding-returning` | onboarding | 0.900 | structure=missing fact; structure=missing cta |
| `mod-appeal-denied` | moderation | 0.900 | structure=missing fact; structure=missing cta |
| `content-daily-rug` | content | 0.905 | spread=uniform (sd 1.48) |
| `alert-exploit-formatted` | security | 0.940 | emoji=2/msg; register=emoji stack on serious message |
| `alert-sniper-bundle` | security | 0.950 | structure=missing cta |
| `content-shill-campaign` | content | 0.950 | structure=missing cta |

## Rules (weights sum to 1.0)
banned 0.20 · cliche 0.15 · spread 0.15 · structure 0.15 · register 0.15 ·
emoji 0.10 · hedge 0.10. A joke in a serious register (loss / security /
error / moderation) is a hard veto (score 0).

## Caveat
`current` texts reconstruct the shipped templates and lanes at commit c5bc812
(alert pipeline, risk verdict, exploit formatter, help/onboarding, error paths,
moderation, Daily Rug envelope, campaign post). They are reviewed fixtures, not
a live capture — re-capture after a template change.
