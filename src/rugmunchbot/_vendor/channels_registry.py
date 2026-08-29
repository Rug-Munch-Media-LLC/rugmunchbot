# VENDORED 2026-08-24 from rmi-backend/app/core/channels_registry.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Canonical Telegram push-channel registry — single source of truth.

Both the RugMunchBot (``rugmunchbot.config``, its own repo) and the
backend alert transport (``app.domains.alerts.telegram_transport``) resolve
their Telegram push channels from THIS module, so the two sides can never
drift on which channel a role maps to.

Channel inventory (role → public handle → gopass entry → env var)
-----------------------------------------------------------------
Public handles only — numeric chat ids are deliberately NOT listed here.
See ``ChannelRole.fallback`` in code for the documented offline defaults
(public channel ids, not credentials), and ``docs/CHANNELS.md`` for the
full human-readable inventory.

=========  ==================  ==================================  =========================
Role       Public handle       Gopass entry (path)                 Env var (canonical)
=========  ==================  ==================================  =========================
news       @rmicryptonews      (env-var is canonical)       RMI_NEWS_CHANNEL_ID
alerts     @rmialerts          (env-var is canonical)  RMI_ALERTS_CHANNEL_ID
alpha      @rmialpha           (env-var is canonical)   RMI_ALPHA_CHANNEL_ID
scans      @rmiscans           (env-var is canonical) RMI_SCANS_CHANNEL_ID
main       @cryptorugmuncher   (env-var is canonical) RMI_MAIN_CHANNEL_ID
updates    @rmiupdates         (env-var is canonical)    RMI_UPDATES_CHANNEL_ID
=========  ==================  ==================================  =========================

Legacy env aliases (read after the canonical var, before the fallback):
    news:    ``RMI_CRYPTONEWS_CHANNEL_ID`` — the pre-registry name used by
             ``app.domains.alerts.telegram_transport``. Kept so existing
             deployments keep working until they migrate to
             ``RMI_NEWS_CHANNEL_ID``.

Resolution order is always **gopass → env → fallback**:
    1. ``gopass show <store_path>`` (skipped when ``use_gopass=False`` or
       gopass is unavailable on the host, e.g. inside the backend container);
    2. ``os.environ[env_var]`` (canonical, then legacy aliases);
    3. ``ChannelRole.fallback`` (documented public channel id; only when the
       caller opts in via ``include_fallback=True`` — the bot always does,
       the alert transport deliberately does NOT so unconfigured channels
       fail open and are skipped at publish time).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# ══════════════════════════════════════════════════════════════════════════
# ROLE DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class ChannelRole:
    """One logical push-channel role and how to resolve its Telegram chat id.

    ``fallback`` is a documented *public* channel id used only when gopass and
    env are both unavailable. It is not a credential; it is still kept out of
    documentation (``docs/CHANNELS.md`` / docstrings) by policy.
    """

    role: str
    store_path: str
    env_var: str
    legacy_env_vars: tuple[str, ...] = ()
    public_handle: str | None = None
    fallback: str | None = None
    description: str = ""


CHANNEL_ROLES: dict[str, ChannelRole] = {
    "news": ChannelRole(
        role="news",
        store_path="",  # supplied by deployment (env-var is canonical)
        env_var="RMI_NEWS_CHANNEL_ID",
        legacy_env_vars=("RMI_CRYPTONEWS_CHANNEL_ID",),
        public_handle="@rmicryptonews",
        fallback="-1003982391578",
        description="RMI Crypto News 24/7 — news-worthy alerts and news digests",
    ),
    "alerts": ChannelRole(
        role="alerts",
        store_path="",  # supplied by deployment (env-var is canonical)
        env_var="RMI_ALERTS_CHANNEL_ID",
        public_handle="@rmialerts",
        fallback="-1003818352164",
        description="RMI Security Alerts — rug pulls, exploits & high-severity threats (auto-escalated to main)",
    ),
    "alpha": ChannelRole(
        role="alpha",
        store_path="",  # supplied by deployment (env-var is canonical)
        env_var="RMI_ALPHA_CHANNEL_ID",
        public_handle="@rmialpha",
        fallback="-1003762675055",
        description="RMI Whale & Alpha Signals — highest-signal early data (launch/syndicate/whale)",
    ),
    "scans": ChannelRole(
        role="scans",
        store_path="",  # supplied by deployment (env-var is canonical)
        env_var="RMI_SCANS_CHANNEL_ID",
        public_handle="@rmiscans",
        fallback="-1003937506770",
        description="RMI Scanner — deep security reports on suspicious tokens (verdict + metrics + links)",
    ),
    "main": ChannelRole(
        role="main",
        store_path="",  # supplied by deployment (env-var is canonical)
        env_var="RMI_MAIN_CHANNEL_ID",
        public_handle="@cryptorugmuncher",
        fallback="-1002056885429",
        description="Main channel @cryptorugmuncher — top curated stories + high-importance escalations from news/alerts/alpha",
    ),
    "updates": ChannelRole(
        role="updates",
        store_path="",  # supplied by deployment (env-var is canonical)
        env_var="RMI_UPDATES_CHANNEL_ID",
        public_handle="@rmiupdates",
        fallback=None,
        description="RMI Changelog — product/feature announcements (mapped, no consumer yet)",
    ),
    "shills": ChannelRole(
        role="shills",
        store_path="",  # supplied by deployment (env-var is canonical)
        env_var="RMI_SHILLS_CHANNEL_ID",
        public_handle="@rmishills",
        fallback=None,
        description="RMI Shill Watch — coordinated shill campaigns and pump group tracking",
    ),
}

# ══════════════════════════════════════════════════════════════════════════
# RESOLUTION (gopass → env → fallback)
# ══════════════════════════════════════════════════════════════════════════


def _gopass_read(path: str, *, timeout: float = 5.0) -> str:
    """Read one gopass secret. Returns "" when gopass is unavailable/empty."""
    try:
        return (
            subprocess.check_output(  # noqa: S603 — gopass is the canonical secret store
                ["gopass", "show", path],  # noqa: S607 — fixed tool name, arg is a secret name
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                shell=False,
            )
            .decode()
            .strip()
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def resolve_channel_id(
    role: str,
    *,
    env: Mapping[str, str] | None = None,
    use_gopass: bool = True,
    include_fallback: bool = True,
) -> str | None:
    """Resolve one channel role to a chat id: gopass → env → fallback.

    ``env`` overrides ``os.environ`` for deterministic resolution (tests).
    Returns ``None`` when the role is unknown or nothing resolved.
    """
    definition = CHANNEL_ROLES.get(role)
    if definition is None:
        return None
    source = env if env is not None else os.environ

    if use_gopass:
        gopass_value = _gopass_read(definition.store_path)
        if gopass_value:
            return gopass_value

    for key in (definition.env_var, *definition.legacy_env_vars):
        value = source.get(key)
        if value:
            return value

    if include_fallback and definition.fallback:
        return definition.fallback
    return None


# ══════════════════════════════════════════════════════════════════════════
# INVENTORY (value-free, for docs/tooling/reporting)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class ChannelInventoryRow:
    """One registry row with no secret material — safe to log/dump."""

    role: str
    public_handle: str | None
    store_path: str
    env_var: str
    legacy_env_vars: tuple[str, ...]
    description: str


def channel_inventory() -> tuple[ChannelInventoryRow, ...]:
    """Return the full registry as value-free rows (names only, no chat ids)."""
    return tuple(
        ChannelInventoryRow(
            role=definition.role,
            public_handle=definition.public_handle,
            store_path=definition.store_path,
            env_var=definition.env_var,
            legacy_env_vars=definition.legacy_env_vars,
            description=definition.description,
        )
        for definition in CHANNEL_ROLES.values()
    )
