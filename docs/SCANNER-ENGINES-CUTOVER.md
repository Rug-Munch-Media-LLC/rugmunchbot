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

## Blocked cutovers

### 1. `rpc_router` — canonical is missing the bot's freemium API

Consumers: `tools/scanner/_solana_onchain.py`, `tools/scanner/_chain_pools.py`,
`handlers/owner/_rpcstats.py`, `_vendor/holder_mega_sampler.py`.

A direct swap to `rugmunch_scanner.unified.engines.rpc_router` breaks 14 tests
because the canonical router lacks the bot's freemium tier machinery:

- `RpcRouter._split_tiers(chain)` — free/freemium pool split
- `_record_quota` / `_quota_usage` / `_record_escalation` + `freemium_escalations`
- free-first ordering inside `EVM_RPC_ENDPOINTS` (asserted by `test_wave35`)

**Cutover steps:** port the freemium tier machinery and free-first pool ordering
from the vendored copy into `rugmunch-scanner`, re-run
`test_wave31`/`test_wave33`/`test_wave5`/`test_wave35`, then swap the four
imports.

### 2. `own_wallet_trust` — no canonical equivalent

Consumer: `handlers/_scan_handlers/_wallet.py`.

`rugmunch_scanner` has no wallet-trust scorer (no `get_wallet_trust` /
`trust_score` anywhere in the package).

**Cutover steps:** port `own_wallet_trust.py` into
`rugmunch_scanner.unified.engines.own_wallet_trust`, then swap the import.

## Remaining vendored engines

`own_cluster_analysis`, `own_cross_token_link`, `own_decentralization`,
`own_deployer_timeline`, `own_wallet_map` (and the rest of the tree) have no
canonical equivalent yet. `_vendor/scanner_engines/__init__.py` imports all of
them, so the package stays until each engine is ported upstream — or the
package `__init__` is trimmed to the remaining engines.

## Verification

`ruff check`, `mypy`, and `pytest tests/` must pass after each swap.
