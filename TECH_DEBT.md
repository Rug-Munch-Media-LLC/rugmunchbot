# Tech Debt

Tracked FIXME markers in `src/rugmunchbot/` (excluding `_vendor/`).

| # | Marker | Location | Description |
|---|--------|----------|-------------|
| 1 | `FIXME(issue)` | `src/rugmunchbot/channels/shill_watch/_evidence.py:256` | **Deleted-post detection.** The sweep is poll-based and never sees deletions. Register a Telethon `events.MessageDeleted` handler on the live client (`_build_headless_client` consumer) that, on deletion of a message from a `SHILL_WATCH_CHANNELS` channel captured in a prior sweep, re-persists the already-captured screenshot/OCR with `deleted=True` so the site can show a "deleted posts" surface. Until then, deletion evidence is limited to screenshots captured before the delete. |
| 2 | `FIXME(issue)` | `src/rugmunchbot/channels/personal_channel/_ghost.py:34` | **Ghost Admin API key.** Provision `GHOST_ADMIN_API_KEY` for the cross-post job. Secrets-store entry: `ghost-admin-api-key` (name only — never log the value). Content API key (`GHOST_CONTENT_API_KEY`) is read-only and cannot publish, hence the Admin key requirement. |
