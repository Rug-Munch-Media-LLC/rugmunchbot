# methodology-methods baseline — why 16 findings are accepted debt (2026-10-25)

Recorded at the TRUE current count (magic-threshold 8, prompt-judgement 6,
signal-list 2). This is not a raise to turn CI green: the gate had no baseline
file at all (baseline 0), so every pre-existing finding blocked every commit.
Nothing in this triage is a published internal method or path.

## 6 findings — src/rugmunchbot/_vendor/ (cannot be edited here)
Frozen vendored copy of rmi-backend/app/rag ("VENDORED 2026-08-24 ... do not
edit", re-vendor via scripts/refresh-vendors.sh). The gate's skip_dirs covers
vendor, third_party and site-packages but not the _vendor convention. The
durable fix is a skip_dirs entry in
the shared gate pattern data in the standards repo (tracked separately),
not an edit to the vendored tree.

## 4 findings — editorial / user-facing prompts
channels/daily_rug, channels/evening_wrap, channels/midday_market_report
_prompt.py and tools/web.py. Public product copy: voice, banned-phrase list,
output shape. No weights, no calibration, no signal vocabulary, no scoring
cut-off.

## 6 findings — display banding on externally supplied rates
scam_alerts/_project_alert.py (high/medium/low label bands),
portfolio_risk.py (diversification warning), scanner/_ai_verdict.py (percentage
wording inside a prompt), _scan_report_riskreport/_extract.py (data-derived
rug_rate flag), wallet_intelligence.py (whale labelling constant). These band
numbers produced elsewhere; they do not calibrate a score.

Ratchet: shrink-only. Any NEW finding in these rules fails the gate.
