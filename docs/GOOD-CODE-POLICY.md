# GOOD-CODE-POLICY.md — rugmunchbot canonical engineering policy

> Status: **canonical** · owner=crmuncher · 2026-09-18
> Scope: first-party code = `src/rugmunchbot/**` (excluding `src/rugmunchbot/_vendor/**`), `tests/**`.
> Enforcement: `make policy` (scripts/check-policy.sh) + the CI "Policy checks" step.
> Debt ledger (the ONLY one): `TECH-DEBT.md`. (TECH_DEBT.md is a pointer.)

## 0. The one rule

**Root cause or nothing.** If the cause cannot be fixed today, write it in
`TECH-DEBT.md` with a payoff order — never band-aid it. A workaround that
ships is a second bug with better manners.

## 1. Monkey code — banned in first-party code

Monkey code = code that patches around a problem instead of fixing it.
Banned:

- runtime **monkeypatching** (module/attr patching), `sys.path` hacks,
  `importlib`-based hot patches
- `HACK` / `WORKAROUND` / `BANDAID` / `TEMP` / `XXX` comments (repo count: **0** — keep it that way)
- silent swallows: `except …: pass` / `continue` with no log and no handled state
- "just in case" shims, duplicated logic kept "for safety", commented-out code
- `# type: ignore` / `# noqa` / `# pragma: no cover` without a same-line reason

Allowed exceptions:

- `pytest`'s `monkeypatch` fixture in `tests/**` — ONLY when no clean seam
  (dependency injection, parameterization) exists; prefer the seam.
- `_vendor/**` — vendored snapshots; tracked upstream, out of scope for style rewrites.

Detection: `scripts/check-policy.sh` (band-aid markers, runtime patching,
sys.path/importlib in src, god files, suppression ratchets).

## 2. Reduction — always-on

- **Delete dead code on sight.** No commented-out blocks, no `*_old`,
  no `.bak`/`.bak-*` files in the tree.
- **One source of truth per topic.** Docs: `TECH-DEBT.md` is the debt
  ledger; `TODO.md` / `TRIAGE.md` are historical only. Code: search before
  you write (`search_code` / RAG) — duplicated helpers are bugs waiting.
- **No god files:** first-party modules ≤ 800 lines; split by
  responsibility (see import-linter contracts for the layering).
- **No god functions:** ≤ 50 lines, ≤ 5 params; extract, use dataclasses/Pydantic.

## 3. Structure & layering

- `[tool.importlinter]` contracts are **BLOCKING in CI** — foundational
  packages stay leaf modules; presentation never reaches into orchestration.
- Dependencies flow down: router → service → repository → model.
- No cross-service imports — APIs or events only.

## 4. Typing & lint

- `ruff check` + `ruff format --check`: green. Each suppression needs a reason.
- `mypy` (`strict=false` today): every NEW or EDITED function fully typed.
- **Suppression ratchets** (scripts/policy-budgets.env): `# noqa`,
  `# type: ignore`, `# pragma: no cover` counts may go DOWN, never up.

## 5. Testing — the real gap

- Measured total coverage: **47%** (2026-09-18). CI floor: **34%**
  (`--cov-fail-under=34`, ratchet) + a per-diff gate via `diff-cover`.
- Fleet standard target: **≥ 70%** on first-party code. Ratchet up, never down.
- Every new function/branch ships with a test; tests mirror source structure.
- Bug fixes start with a failing test that reproduces the bug.
- `# pragma: no cover` needs a same-line justification ("structurally unreachable", "platform-specific", ...).

## 6. Review gate — non-negotiable

CI must pass all of: ruff, mypy, pytest + coverage floors, `lint-imports`,
secrets/security scans. Conventional commits. No force-push to `main`.
No `.env`/secrets in git — gopass only.

## 7. Enforcement

| Where | Command |
|---|---|
| Local | `make policy` |
| CI | step "Policy checks (BLOCKING)" → `bash scripts/check-policy.sh` |
| Review | any new suppression or god file = PR rejected by policy gate |
