# rugmunchbot (archived)

> **⚠️ This bot is now embedded in rmi-backend.**
>
> The live bot runs from `rmi-backend/app/domains/telegram/rugmunchbot/` via systemd
> (`/etc/systemd/system/rugmunchbot.service`).
>
> The standalone code at `bot/app/telegram_bot/` has been **archived** to
> `bot/app/telegram_bot/_ARCHIVED/`. See `ARCHIVED.md` for details.

## What remains

- **`tests/`** — test fixtures and smoke tests (valuable for backward compatibility)
- **`ARCHIVED.md`** — full archive documentation
- **`bot/app/telegram_bot/_ARCHIVED/`** — the original standalone source, preserved for reference

## Running tests

```bash
make test
```
