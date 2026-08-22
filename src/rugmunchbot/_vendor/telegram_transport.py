# VENDORED 2026-08-17 from rmi-backend/app/domains/alerts/telegram_transport.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Telegram transport for platform alerts + news.

Executes the routing plan produced by ``app.domains.alerts.publisher.route_alerts``
against the headless Telegram client (``HeadlessTelegramClient.send_message``).
The transport is fail-open: a missing channel id or a single failing send never
aborts the batch — the error is logged and the remaining messages continue.

Channel resolution
------------------
Logical channel names (the keys of ``publisher.DEFAULT_CHANNELS``) map to
Telegram chat ids via the shared channel registry
(``app.core.channels_registry``) — the SAME registry the RugMunchBot uses, so
the two sides can never drift. Resolution order is gopass → env → fallback;
registry-backed channels deliberately do NOT use the registry's documented
fallback (they fail open and are skipped when unconfigured).

==============  ================  =============================  =============================================
Logical channel  Registry role    Env var                        Notes
==============  ================  =============================  =============================================
alerts_channel  alerts (handle)   ``RMI_ALERTS_CHANNEL``         Public @rmialerts channel
                                                                 (https://t.me/rmialerts). Handle
                                                                 variant of the ``alerts`` role; defaults to
                                                                 the ``@rmialerts`` handle; may be a
                                                                 numeric chat id instead.
rmialerts        alerts           ``RMI_ALERTS_CHANNEL_ID``      Internal RMI alerts channel (high+).
rmialpha         alpha            ``RMI_ALPHA_CHANNEL_ID``       RMI Alpha channel — highest-signal early
                                                                 data (launch/syndicate/whale, high+).
rmicryptonews    news             ``RMI_NEWS_CHANNEL_ID``        RMI Crypto News channel (news-worthy
                                                                 alerts). Legacy env alias
                                                                 ``RMI_CRYPTONEWS_CHANNEL_ID`` still read.
main_channel     main             ``RMI_MAIN_CHANNEL_ID``        Main channel (critical only).
site             (none)           (none)                         No Telegram surface — always skipped.
==============  ================  =============================  =============================================

Message formatting uses Telegram HTML parse mode (``parse_mode="html"``).
All user/detector-supplied text is HTML-escaped before embedding.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.alerts_models import PlatformAlert
from rugmunchbot._vendor.alerts_publisher import route_alerts
from rugmunchbot._vendor.channels_registry import CHANNEL_ROLES, resolve_channel_id
from rugmunchbot._vendor.headless import get_headless_client
from rugmunchbot._vendor.logging_shim import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger("alerts.telegram_transport")

# Telegram soft limit is 4096 chars; keep bodies/news comfortably under it.
_MAX_BODY_CHARS = 1000
_MAX_NEWS_SUMMARY_CHARS = 280

SEVERITY_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}

_INT_RE = re.compile(r"^-?\d+$")


@dataclass(frozen=True)
class ChannelBinding:
    """How one logical channel resolves to a Telegram chat id.

    A binding is either registry-backed (``role`` set — resolves through
    ``app.core.channels_registry`` with gopass → env → fallback) or
    env-direct (``env_var`` + optional ``default``, used for the public
    @-handle surface of the alerts role).
    """

    logical: str
    description: str
    role: str | None = None
    env_var: str | None = None
    default: int | str | None = None


CHANNEL_BINDINGS: tuple[ChannelBinding, ...] = (
    ChannelBinding(
        logical="alerts_channel",
        description="Public @rmialerts channel (t.me/rmialerts) — handle surface of the alerts role",
        env_var="RMI_ALERTS_CHANNEL",
        default="@rmialerts",
    ),
    ChannelBinding(
        logical="rmialerts",
        role="alerts",
        description="Internal RMI alerts channel (high+)",
    ),
    ChannelBinding(
        logical="rmialpha",
        role="alpha",
        description="RMI Alpha channel (highest-signal early data: launch/syndicate/whale, high+)",
    ),
    ChannelBinding(
        logical="rmicryptonews",
        role="news",
        description="RMI Crypto News channel (news-worthy alerts)",
    ),
    ChannelBinding(
        logical="main_channel",
        role="main",
        description="Main channel (critical only)",
    ),
)


def _binding_env_display(binding: ChannelBinding) -> str | None:
    """Canonical env var name for a binding (for logs / docs)."""
    if binding.env_var:
        return binding.env_var
    if binding.role:
        definition = CHANNEL_ROLES.get(binding.role)
        if definition:
            return definition.env_var
    return None


def _parse_chat_id(raw: str | None) -> int | str | None:
    """Parse an env var into a chat id: int when numeric, else a @-handle."""
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if _INT_RE.fullmatch(value):
        try:
            return int(value)
        except ValueError:
            return None
    return value  # Telegram username handle, e.g. "@rmialerts"


def channel_ids_from_env(
    env: Mapping[str, str] | None = None,
    *,
    use_gopass: bool = True,
) -> dict[str, int | str | None]:
    """Resolve logical channel name → Telegram chat id.

    Registry-backed channels resolve through the shared channel registry
    (``app.core.channels_registry``) with the gopass → env → fallback order;
    they deliberately ignore the registry's documented fallback so an
    unconfigured channel stays ``None`` and is skipped at publish time.
    ``alerts_channel`` resolves directly from its env var and falls back to
    the ``@rmialerts`` handle. Pass ``use_gopass=False`` for
    deterministic env-only resolution (tests / sandbox).
    """
    source = env if env is not None else os.environ
    ids: dict[str, int | str | None] = {}
    for binding in CHANNEL_BINDINGS:
        raw: str | None
        if binding.role is not None:
            raw = resolve_channel_id(
                binding.role,
                env=source,
                use_gopass=use_gopass,
                include_fallback=False,
            )
        else:
            raw = source.get(binding.env_var) if binding.env_var else None
            if not raw and binding.default is not None:
                raw = str(binding.default)
        parsed = _parse_chat_id(raw)
        if parsed is None:
            logger.warning(
                "telegram_channel_not_configured",
                channel=binding.logical,
                env_var=_binding_env_display(binding),
            )
        ids[binding.logical] = parsed
    return ids


#: Logical channel name → Telegram chat id (int or @-handle). Read once at
#: import; override in tests / orchestrators before publishing if needed.
CHANNEL_IDS: dict[str, int | str | None] = channel_ids_from_env()


def _esc(value: object) -> str:
    """HTML-escape text for Telegram parse_mode=html (quotes left intact)."""
    return html.escape(str(value), quote=False)


def _esc_attr(value: object) -> str:
    """HTML-escape an attribute value (href) — quotes escaped too."""
    return html.escape(str(value), quote=True)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def format_alert_message(alert: PlatformAlert) -> str:
    """Format a PlatformAlert as an HTML Telegram message.

    Layout: bold title with severity emoji, body, then a meta line with
    severity/category/token/chain/source, then any links.
    """
    severity = str(alert.severity)
    emoji = SEVERITY_EMOJI.get(severity, "⚪")
    lines = [f"<b>{emoji} {_esc(alert.title)}</b>"]

    if alert.body:
        lines.append(_truncate(_esc(alert.body), _MAX_BODY_CHARS))

    meta: list[str] = [f"Severity: {_esc(severity.upper())}"]
    meta.append(f"Category: {_esc(alert.category)}")
    if alert.token_address:
        meta.append(f"Token: <code>{_esc(alert.token_address)}</code>")
    if alert.chain:
        meta.append(f"Chain: {_esc(alert.chain)}")
    if alert.source:
        meta.append(f"Source: {_esc(alert.source)}")
    lines.append(" • ".join(meta))

    for url in alert.urls:
        lines.append(f'🔗 <a href="{_esc_attr(url)}">{_esc(url)}</a>')

    return "\n".join(lines)


def format_news_message(article: Mapping[str, Any]) -> str:
    """Format a news article dict as an HTML Telegram message.

    Accepts dicts with ``title``/``headline``, ``summary``/``body``/
    ``description``, ``link``/``url``/``source_url`` and ``source``/
    ``source_channel`` keys (NewsArticle model shape or JSON).
    """
    title = article.get("title") or article.get("headline") or "Untitled"
    summary = article.get("summary") or article.get("body") or article.get("description") or ""
    link = article.get("link") or article.get("url") or article.get("source_url") or ""
    source = article.get("source") or article.get("source_channel") or ""

    lines = [f"<b>{_esc(title)}</b>"]
    if summary:
        one_line = " ".join(str(summary).split())
        lines.append(_truncate(_esc(one_line), _MAX_NEWS_SUMMARY_CHARS))

    meta: list[str] = []
    if source:
        meta.append(f"Source: {_esc(source)}")
    if link:
        meta.append(f'🔗 <a href="{_esc_attr(link)}">{_esc(link)}</a>')
    if meta:
        lines.append(" • ".join(meta))

    return "\n".join(lines)


def _article_key(article: Mapping[str, Any]) -> str:
    """Dedup key: ``content_hash`` when present, else sha256(title|link)."""
    content_hash = article.get("content_hash") or article.get("hash")
    if content_hash:
        return f"hash:{content_hash}"
    title = article.get("title") or article.get("headline") or ""
    link = article.get("link") or article.get("url") or article.get("source_url") or ""
    digest = hashlib.sha256(f"{title}|{link}".encode()).hexdigest()
    return f"derived:{digest}"


async def _resolve_client(client: Any | None) -> Any:
    """Use the injected client, or the singleton headless client."""
    return client if client is not None else await get_headless_client()


async def publish_routed(route_plan: list[dict[str, Any]], client: Any | None = None) -> dict[str, Any]:
    """Execute a ``route_alerts`` plan against Telegram.

    Each plan entry's channel resolves through :data:`CHANNEL_IDS`; entries
    with no configured id are skipped. Sends use ``parse_mode="html"``.
    Fail-open: a raising client or a ``False`` return is recorded in
    ``errors`` and the batch continues.

    Returns ``{"sent": int, "skipped": int, "errors": list[str]}``.
    """
    transport = await _resolve_client(client)
    sent = 0
    skipped = 0
    errors: list[str] = []

    for entry in route_plan:
        channel = entry.get("channel", "?")
        chat_id = CHANNEL_IDS.get(channel)
        alerts = entry.get("alerts", [])
        if chat_id is None:
            skipped += len(alerts)
            logger.warning("telegram_channel_skipped", channel=channel, reason="no_chat_id")
            continue

        for alert_dict in alerts:
            try:
                alert = (
                    alert_dict if isinstance(alert_dict, PlatformAlert) else PlatformAlert.model_validate(alert_dict)
                )
                text = format_alert_message(alert)
                ok = await transport.send_message(chat_id, text, parse_mode="html")
            except Exception as exc:
                errors.append(f"{channel}: {exc}")
                logger.error("telegram_send_failed", channel=channel, err=str(exc))
                continue

            if ok:
                sent += 1
            else:
                errors.append(f"{channel}: send_message returned False")
                logger.warning("telegram_send_returned_false", channel=channel)

    return {"sent": sent, "skipped": skipped, "errors": errors}


async def publish_alert(alert: PlatformAlert, client: Any | None = None) -> dict[str, Any]:
    """Route a single alert through the default policies and publish it."""
    plan = route_alerts([alert])
    return await publish_routed(plan, client=client)


async def publish_news_to_channel(articles: list[Mapping[str, Any]], client: Any | None = None) -> dict[str, Any]:
    """Post news articles to the ``rmicryptonews`` channel, deduped.

    Duplicates are detected by ``content_hash`` (falling back to a hash of
    title+link). Returns ``{"sent": int, "skipped": int, "errors": list[str]}``.
    """
    news_chat_id = CHANNEL_IDS.get("rmicryptonews")
    if news_chat_id is None:
        logger.warning("telegram_news_channel_skipped", channel="rmicryptonews", reason="no_chat_id")
        return {"sent": 0, "skipped": len(articles), "errors": []}

    transport = await _resolve_client(client)
    seen: set[str] = set()
    sent = 0
    skipped = 0
    errors: list[str] = []

    for article in articles:
        key = _article_key(article)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)

        try:
            text = format_news_message(article)
            ok = await transport.send_message(news_chat_id, text, parse_mode="html")
        except Exception as exc:
            errors.append(f"rmicryptonews: {exc}")
            logger.error("telegram_news_send_failed", err=str(exc))
            continue

        if ok:
            sent += 1
        else:
            errors.append("rmicryptonews: send_message returned False")
            logger.warning("telegram_news_send_returned_false")

    return {"sent": sent, "skipped": skipped, "errors": errors}
