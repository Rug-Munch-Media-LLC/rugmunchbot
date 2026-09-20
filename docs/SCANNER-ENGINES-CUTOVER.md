# Scanner Engines — vendored → canonical cutover

**Status:** partial. Canonical engine package: `rugmunch-scanner` (declared in
`pyproject.toml` under the `scanner` extra and in `requirements.txt`).

## Why

`src/rugmunchbot/_vendor/scanner_engines/` is a frozen 2026-08-24 copy of
rmi-backend `app/domains/scanner/engines/`. The canonical scanner is now
**rugmunch-scanner**, whose engines live under
`rugmunch_scanner.unified.engines.*`. Keeping the frozen copy duplicates
detection logic and drifts from canonical.

## Cut over (done)

| Bot consumer | Old (vendored) | New (canonical) |
|---|---|---|
| `/trace` fund flow — `handlers/trace/_flow.py` | `_vendor.scanner_engines.wallet_flow.build_flow_summary` | `handlers/trace/_flow_summary.py` → `rugmunch_scanner.unified.engines.wallet_flow.build_flow_graph` |
| Flow-summary tests — `tests/test_wave11_scanner_engines.py` | vendored `_summarize_flow` | bot `handlers/trace/_flow_summary._summarize_flow` |

`_vendor/scanner_engines/wallet_flow.py` is deleted; `scripts/vendor-from-backend.sh`
removes it after a refresh so the frozen copy cannot be resurrected.

## Decisions on the two blocked cutovers (2026-09-10)

### 1. `rpc_router` — freemium tier logic is scanner-owned; PORT into canonical

**Decision:** the freemium tier machinery (`_split_tiers`, quota ledger,
escalation telemetry, free-first pool ordering) is an **RPC-layer concern** and
belongs in `rugmunch_scanner.unified.engines.rpc_router` — not in the bot. The
canonical router already owns health tracking + endpoint fallback, so the port
is **additive, not a rebuild**.

**Status:** blocked until the port lands. The bot keeps the vendored
`rpc_router` in the interim (no behavior change).

**Owner:** scanner maintainers (`@crmuncher`); bot integration by the
rugmunchbot maintainer.

**Acceptance criteria for the cutover:**

- `RpcRouter._split_tiers(chain)` free/freemium pool split plus
  `_record_quota` / `_quota_usage` / `_record_escalation` /
  `freemium_escalations` ported.
- Free-first ordering inside `EVM_RPC_ENDPOINTS` preserved (asserted by
  `test_wave35`).
- `test_wave31` / `test_wave33` / `test_wave5` / `test_wave35` pass against the
  canonical router.
- Swap the four consumers: `tools/scanner/_solana_onchain.py`,
  `tools/scanner/_chain_pools.py`, `handlers/owner/_rpcstats.py`,
  `_vendor/holder_mega_sampler.py`.

### 2. `own_wallet_trust` — DEPRECATED in favor of the label graph; DO NOT PORT

**Decision:** `own_wallet_trust` (0-100 score derived from raw
`wallet_memory.evm_transfers` / `deployer_history` reads) is **superseded by the
label graph**. Wallet trust is answered by **entity resolution** against the
indexer's `GET /api/v1/labels/{address}` (canonical labels) plus the scanner's
risk scoring. Porting a second trust scorer into the canonical package would
duplicate detection methodology — rejected.

**Status:** blocked by design (won't-port). The bot's consumer migrates to the
label API; the vendored `own_wallet_trust.py` is then deleted.

**Owner:** rugmunchbot maintainer. The indexer label API is already live, so no
scanner change is required.

**Follow-up:** `handlers/_scan_handlers/_wallet.py` trust block queries
`GET /api/v1/labels/{address}` (or the scanner `/api/v1/wallet/*` API) and
renders the label-graph verdict; remove the `get_wallet_trust` import and the
vendored file. See `WALLET-MEMORY-DECOUPLE.md` for the label-API contract.

## Remaining vendored engines

`own_cluster_analysis`, `own_cross_token_link`, `own_decentralization`,
`own_deployer_timeline`, `own_wallet_map` (and the rest of the tree) have no
canonical equivalent yet. `_vendor/scanner_engines/__init__.py` imports all of
them, so the package stays until each engine is ported upstream — or the
package `__init__` is trimmed to the remaining engines.

## Direct scanner-service client (batch-4, 2026-09-20)

`src/rugmunchbot/tools/scanner/_scanner_direct.py` is the feature-flagged seam
that lets the bot call the **standalone** `rugmunch-scanner` service directly
instead of routing every scan through rmi-backend. It is inert by default —
zero behaviour change until an operator opts in — and fails open to the
existing backend/local chain on any failure (never raises, never blocks a
user scan).

Two env vars, read at call time (no restart needed to flip):

| Env var | Default | Meaning |
|---|---|---|
| `SCANNER_DIRECT` | `0` | `1`/`true`/`yes`/`on` enables the direct client |
| `SCANNER_SERVICE_URL` | unset | Base URL of the scanner service, e.g. `http://127.0.0.1:8098` |

`SCANNER_DIRECT_URL` is accepted as a legacy alias (`SCANNER_SERVICE_URL` wins).

Contract: readiness-gate on `GET {base}/readyz` (and `GET {base}/healthz` via
`scanner_service_ready()`), then `POST {base}/api/v2/scanner/token/scan` and
`POST {base}/api/v2/scanner/wallet/scan` with `{address, chain}` — the same v2
shape `tools/scanner/_backend.py` uses. **The standalone runtime currently
serves only the two probes**; the v2 scan routes land with the batch-3 scan
rail (`rugmunch-scanner/docs/STANDALONE.md` §TODO), so a flag-on call
health-gates, receives `404` on the scan route, and falls open. Wiring the
`_scans.py` call-site legs is the follow-up batch.

## Verification

`ruff check`, `mypy`, and `pytest tests/` must pass after each swap.
