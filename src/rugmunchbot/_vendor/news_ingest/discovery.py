# VENDORED 2026-08-24 from rmi-backend/app/domains/news_ingest/discovery.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Telegram channel discovery for news ingestion.

``discover_followed_channels`` enumerates the dialogs of the logged-in user
account via Telethon MTProto (``client.get_dialogs()``). ``get_dialogs``
returns every conversation the account participates in — private chats,
groups, and broadcast channels (both channels it follows and groups it is a
member of). We filter down to **public** entities (those exposing a
``username``) that look like channels/groups (they carry a ``title``),
which is exactly the set the news sweep can read via
``read_channel_messages``.

Design rules:
  * Never raises — any failure (Telethon missing, no credentials, network
    error) degrades to ``[]`` so the sweep can fall back to the explicit
    channel list.
  * Duck-typed against the Telethon surface so unit tests can inject fakes
    without installing Telethon.
  * Accepts either the ``HeadlessTelegramClient`` wrapper (its underlying
    Telethon ``_client`` is unwrapped) or a raw Telethon client.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger("news_ingest.discovery")


async def discover_followed_channels(client: Any, limit: int = 500) -> list[tuple[str, str]]:
    """Enumerate public channels/groups the account is in.

    Args:
        client: The headless client wrapper or a raw Telethon client exposing
            ``get_dialogs``/``iter_dialogs``.
        limit: Max dialogs to scan (Telethon default 500; enough to cover
            news-channel follows without flooding the call).

    Returns:
        List of ``(username, title)`` tuples for public channels/groups the
        account follows or is a member of, ordered by dialog recency.
        ``[]`` on any failure (fail-open).
    """
    try:
        raw = getattr(client, "_client", None) or client
        if raw is None:
            return []

        get_dialogs = getattr(raw, "get_dialogs", None)
        if not callable(get_dialogs):
            logger.warning("news_ingest:discovery_unavailable no_get_dialogs")
            return []

        dialogs = await get_dialogs(limit=max(1, int(limit)))
        results: list[tuple[str, str]] = []
        seen: set[str] = set()

        # Best-effort account identity for the log — confirms the sweep is
        # reading the operator's session (cryptorugmunch) and not a stale one.
        # Fail-open: absent ``get_me``/errors only skip the identity field.
        account = ""
        get_me = getattr(raw, "get_me", None)
        if callable(get_me):
            try:
                me = await get_me()
                account = getattr(me, "username", None) or getattr(me, "first_name", None) or ""
            except Exception as exc:
                log.warning("news_ingest:discovery:get_me_failed error=%s", str(exc)[:100])
                account = ""

        # get_dialogs() returns a Dialogs container; iterate over its
        # ``dialogs`` attribute (each item is a Telethon Dialog).
        dialog_items = getattr(dialogs, "dialogs", None) or []
        for dialog in dialog_items:
            entity = getattr(dialog, "entity", None)
            if entity is None:
                continue
            username = getattr(entity, "username", None)
            if not username or not isinstance(username, str):
                continue  # private chat / no public handle
            title = getattr(dialog, "name", None) or getattr(entity, "title", None) or ""
            handle = username.strip().lstrip("@")
            if not handle or handle in seen:
                continue
            seen.add(handle)
            results.append((handle, str(title or handle)))

        logger.info(
            "news_ingest:discovered_channels account=%s count=%s",
            account or "unknown",
            len(results),
        )
        return results
    except Exception as exc:  # fail-open by contract
        logger.warning("news_ingest:discovery_failed err=%s", str(exc)[:200])
        return []


__all__ = ["discover_followed_channels"]
