# TECH-DEBT.md — rugmunchbot

> Living ledger. Items are ordered by payoff. Updated 2026-08-21 after the
> platform audit (see rmi-backend `AUDIT-2026-Q3.md` for platform context).

## Done
- [x] Silent `except Exception: pass` in `_vendor/unified_scanner/_finalize_helpers.py`
      → warning-tier logging (mirrors rmi-backend fix, commit series 2026-08-21)
- [x] Production Dockerfile + docker-compose.yml (non-root UID 10002,
      restart policy, named volume) — previously unshippable as a container
- [x] Gitleaks sweep clean; hooks verified

## God files — split plan (all >800 lines)
| File | Lines | Split target |
|---|---|---|
| `handlers/chat_ai/_llm/_router.py` | 1166 | route table → `_router_tables.py`; provider fallback chain → `_fallback.py` |
| `config.py` | 1069 | per-domain sections → `config/` package (`telegram.py`, `llm.py`, `channels.py`, `integrations.py`) |
| `formatters/_scan_report_deepscan.py` | 992 | section renderers → `_deepscan_sections.py` |
| `channels/telegram_news/_sources.py` | 984 | one module per source family; registry stays |
| `handlers/trace.py` | 965 | address/tx/wallet trace handlers → `handlers/trace/` pkg |
| `formatters/_scan_report_common.py` | 965 | shared blocks → `_blocks.py`; layout → `_layout.py` |
| `channels/daily_market_summary.py` | 952 | data assembly vs rendering split |
| `channels/formatters.py` | 923 | per-channel formatter modules |

## Vendored code (`_vendor/`)
- `osint/exploit_db.py` (1094), `osint/exploit_sources.py` (1000),
  `unified_scanner/*` — vendored copies of backend modules.
- **Decision needed:** keep vendored (frozen, upstream-drifts) vs depend on
  rmi-backend as a library. Vendoring was chosen for deploy independence;
  revisit when rmi-common extraction lands (platform win 15).

## Known contracts / follow-ups
- `TODO(ghost-admin-key)` in `channels/personal_channel.py` — provision
  `GHOST_ADMIN_API_KEY` via gopass to enable cross-posting.
- Long-polling bot has no HTTP listener → compose uses restart policy only.
  Follow-up: internal health thread so Docker HEALTHCHECK can attach.
- Test suite: 16 files, green. Coverage gates not yet wired (platform win 29).
