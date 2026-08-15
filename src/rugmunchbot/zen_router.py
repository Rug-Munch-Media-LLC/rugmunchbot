"""Multi-account OpenCode Zen router — rotating residential proxies + failover.

The chat_ai Zen tier (``handlers/chat_ai/_llm.py``) normally talks to ONE
OpenCode Zen DeepSeek V4 Flash Free account through a fleet egress proxy
(``http://127.0.0.1:18081``) and burns the ~10 free chats/day budget in one
afternoon. This module multiplies the free tier by routing each request
through one of several accounts, each behind its OWN rotating Webshare
residential proxy IP, with automatic failover when an account+IP gets
rate-limited.

Config (mutually exclusive — ``ZEN_ACCOUNTS`` wins when set):

1. ``ZEN_ACCOUNTS`` — JSON array. Each entry has ``base_url`` (the FULL
   OpenAI-compatible ``chat/completions`` URL), ``api_key``, an optional
   ``proxy`` (``http://user:pass@host:port``) and an optional ``name`` used
   only in logs. Example::

       ZEN_ACCOUNTS=[
         {"name":"zen1","base_url":"https://zen1.example/v1/chat/completions",
          "api_key":"sk-...","proxy":"http://user:pass@proxy-ip-1:port"},
         {"name":"zen2","base_url":"https://zen2.example/v1/chat/completions",
          "api_key":"sk-...","proxy":"http://user:pass@proxy-ip-2:port"}
       ]

2. Flat numbered vars (when ``ZEN_ACCOUNTS`` is unset): ``ZEN1_URL`` +
   ``ZEN1_KEY`` + ``ZEN1_PROXY``, ``ZEN2_URL`` ..., up to ``ZEN10_*``.
   ``ZEN_PROXY_URL`` is the shared proxy fallback for accounts without
   their own ``ZEN<n>_PROXY``.

Behavior:

* Accounts are picked least-recently-used, so consecutive requests rotate
  round-robin across healthy accounts.
* Each attempt uses a fresh ``httpx.AsyncClient`` bound to that account's
  proxy, so every account egresses from a distinct residential IP.
* On 429 / 403 / 5xx / connect-timeout the account+IP is marked COOLING for
  ``cooldown_s`` (default 60s) and the request rotates to the next account.
  At most ``max_retries`` retries are made per request (default 1).
* When every attempt fails, :class:`ZenRouterExhausted` is raised so the
  caller falls back to the local llama.cpp tier.
* ``chat_completion`` returns the raw OpenAI ``chat/completions`` JSON dict
  so ``_llm.py`` parses it with its existing response-shape code.

No secrets are logged. ``api_key`` values are only ever sent as an
``Authorization: Bearer`` header to their own account's ``base_url``.

Import-time safety: importing this module builds NO router state and reads NO
env — :func:`build_router` is the lazy constructor and is only invoked on
first use (``_llm.py``'s ``_get_zen_router()``); the module logger is created
on demand too.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import httpx

from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from structlog.stdlib import BoundLogger

__all__ = [
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_MAX_RETRIES",
    "ZEN_ACCOUNTS_ENV",
    "ZenAccount",
    "ZenAccountConfigError",
    "ZenRouter",
    "ZenRouterError",
    "ZenRouterExhausted",
    "accounts_from_env",
    "build_router",
]

ZEN_ACCOUNTS_ENV = "ZEN_ACCOUNTS"
ZEN_COOLDOWN_S_ENV = "ZEN_COOLDOWN_S"
ZEN_MAX_RETRIES_ENV = "ZEN_MAX_RETRIES"

DEFAULT_COOLDOWN_S = 60.0
DEFAULT_MAX_RETRIES = 1
_MAX_NUMBERED_ACCOUNTS = 10


@lru_cache
def _get_logger() -> BoundLogger:
    """Lazily create the module logger — importing this module has no side
    effects (no structlog config / logging setup runs at import)."""
    return get_logger("rmi_bot.zen_router")


class ZenRouterError(Exception):
    """Base error for the Zen router — caller falls back to the next LLM tier."""


class ZenRouterExhausted(ZenRouterError):
    """Every configured account failed or is still cooling."""


class ZenAccountConfigError(ZenRouterError):
    """``ZEN_ACCOUNTS`` / ``ZEN<n>_*`` env is malformed (misconfiguration)."""


@dataclass(frozen=True, slots=True)
class ZenAccount:
    """One OpenCode Zen account + its dedicated residential proxy."""

    base_url: str
    api_key: str | None = None
    proxy: str | None = None
    name: str = ""


@dataclass(slots=True)
class _AccountState:
    """Runtime bookkeeping for one account (mutable, unlike ``ZenAccount``)."""

    account: ZenAccount
    last_used: float = 0.0
    cooling_until: float = 0.0


class _AccountUnavailable(Exception):
    """Internal: this account+IP failed transiently — cooldown + rotate."""


class ZenRouter:
    """OpenAI-compatible async client that fails over across Zen accounts.

    Each ``chat_completion`` call picks the least-recently-used account that
    is not cooling, POSTs the OpenAI ``chat/completions`` payload through that
    account's proxy, and on 429 / 403 / 5xx / timeout marks the account+IP
    cooling before rotating to the next account. When all attempts fail it
    raises :class:`ZenRouterExhausted` so the caller can fall back to the
    local tier.
    """

    def __init__(
        self,
        accounts: list[ZenAccount],
        *,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        clock: Callable[[], float] = time.monotonic,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Build a router over *accounts*.

        ``cooldown_s`` — how long a failed account+IP stays out of rotation
        (default 60s). ``max_retries`` — how many retries are allowed per
        request after the initial attempt (default 1 → at most 2 accounts
        tried per request). ``clock`` — injectable monotonic clock for tests.
        ``transport`` — optional httpx transport override (tests); when set,
        it replaces per-account proxies.
        """
        if not accounts:
            raise ZenAccountConfigError("ZenRouter requires at least one account")
        self._states = [_AccountState(account=a) for a in accounts]
        self._cooldown_s = max(0.0, cooldown_s)
        self._max_retries = max(0, max_retries)
        self._clock = clock
        self._transport = transport

    @property
    def account_count(self) -> int:
        """Number of configured accounts."""
        return len(self._states)

    def cooling_accounts(self) -> list[str]:
        """Names of accounts currently in cooldown (health/logging)."""
        now = self._clock()
        return [s.account.name or s.account.base_url for s in self._states if now < s.cooling_until]

    # ── internal helpers ────────────────────────────────────────────────

    def _pick(self) -> _AccountState | None:
        """Least-recently-used account that is not cooling (``None`` if all cooling)."""
        now = self._clock()
        candidates = [s for s in self._states if now >= s.cooling_until]
        if not candidates:
            return None
        chosen = min(candidates, key=lambda s: s.last_used)
        chosen.last_used = now
        return chosen

    def _mark_cooling(self, state: _AccountState) -> None:
        state.cooling_until = self._clock() + self._cooldown_s

    async def _post(self, account: ZenAccount, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """POST *payload* to *account* through its proxy; return the JSON dict.

        Raises :class:`_AccountUnavailable` on transient failures (429 / 403 /
        5xx / connect-timeout) so the caller rotates; raises
        :class:`ZenRouterError` on non-retryable failures (bad request shape,
        unparseable response).
        """
        label = account.name or account.base_url
        client_kwargs: dict[str, Any] = {}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        elif account.proxy:
            client_kwargs["proxy"] = account.proxy
        headers: dict[str, str] | None = None
        if account.api_key:
            headers = {"Authorization": f"Bearer {account.api_key}"}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=5.0, read=timeout, write=10.0),
                **client_kwargs,
            ) as client:
                r = await client.post(account.base_url, headers=headers, json=payload)
        except (httpx.HTTPError, OSError, TimeoutError) as e:
            _get_logger().warning("zen_account_unavailable (account=%s): %s", label, e)
            raise _AccountUnavailable(f"connection error: {e}") from e
        if r.status_code in (429, 403) or r.status_code >= 500:
            _get_logger().warning("zen_account_rate_limited (account=%s): http %d", label, r.status_code)
            raise _AccountUnavailable(f"http {r.status_code}")
        if r.status_code != 200:
            # 4xx that is not 403 — malformed request / wrong path. Retrying
            # other accounts with the same payload won't help, so bail.
            raise ZenRouterError(f"{label}: unexpected HTTP {r.status_code}")
        try:
            raw = r.json()
        except ValueError as e:
            raise ZenRouterError(f"{label}: invalid JSON response") from e
        if not isinstance(raw, dict):
            raise ZenRouterError(f"{label}: non-object JSON response")
        return raw

    # ── public API ──────────────────────────────────────────────────────

    async def chat_completion(self, payload: dict[str, Any], *, timeout: float = 60.0) -> dict[str, Any]:
        """POST an OpenAI-style ``chat/completions`` *payload* through the router.

        Attempts accounts in least-recently-used order, skipping accounts in
        cooldown. On 429 / 403 / 5xx / timeout the account+IP is marked
        cooling and the request rotates to the next account; at most
        ``max_retries`` retries are made per request. When every attempt
        fails, raises :class:`ZenRouterExhausted` so the caller can fall back
        to the local tier.

        Returns the raw OpenAI ``chat/completions`` response dict.
        """
        last_error: _AccountUnavailable | None = None
        attempts = 0
        max_attempts = self._max_retries + 1
        while attempts < max_attempts:
            state = self._pick()
            if state is None:
                break
            attempts += 1
            try:
                return await self._post(state.account, payload, timeout=timeout)
            except _AccountUnavailable as e:
                last_error = e
                self._mark_cooling(state)
                continue
        if last_error is not None:
            raise ZenRouterExhausted(
                f"all zen attempts failed ({attempts} tried, {self.account_count} accounts): {last_error}"
            ) from last_error
        raise ZenRouterExhausted(
            f"all {self.account_count} zen accounts are cooling: {', '.join(self.cooling_accounts())}"
        )


# ── env parsing ──────────────────────────────────────────────────────────


def _parse_accounts_json(raw: str) -> list[ZenAccount]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ZenAccountConfigError(f"{ZEN_ACCOUNTS_ENV} is not valid JSON: {e}") from e
    if not isinstance(data, list) or not data:
        raise ZenAccountConfigError(f"{ZEN_ACCOUNTS_ENV} must be a non-empty JSON array")
    accounts: list[ZenAccount] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ZenAccountConfigError(f"{ZEN_ACCOUNTS_ENV}[{i}] must be an object")
        base_url = str(item.get("base_url") or item.get("url") or "").strip()
        if not base_url:
            raise ZenAccountConfigError(f"{ZEN_ACCOUNTS_ENV}[{i}] missing base_url")
        accounts.append(
            ZenAccount(
                base_url=base_url,
                api_key=str(item.get("api_key") or "").strip() or None,
                proxy=str(item.get("proxy") or "").strip() or None,
                name=str(item.get("name") or f"zen-{i + 1}"),
            )
        )
    return accounts


def _parse_numbered_env() -> list[ZenAccount]:
    """Parse ``ZEN1_URL``/``ZEN1_KEY``/``ZEN1_PROXY`` … up to ``ZEN10_*``."""
    accounts: list[ZenAccount] = []
    shared_proxy = (os.getenv("ZEN_PROXY_URL") or "").strip() or None
    for i in range(1, _MAX_NUMBERED_ACCOUNTS + 1):
        base_url = os.getenv(f"ZEN{i}_URL")
        if not base_url:
            continue
        accounts.append(
            ZenAccount(
                base_url=base_url.strip(),
                api_key=(os.getenv(f"ZEN{i}_KEY") or "").strip() or None,
                proxy=(os.getenv(f"ZEN{i}_PROXY") or "").strip() or shared_proxy,
                name=f"zen-{i}",
            )
        )
    return accounts


def accounts_from_env() -> list[ZenAccount] | None:
    """Parse Zen accounts from the environment; ``None`` when none configured.

    ``ZEN_ACCOUNTS`` (JSON) takes precedence; otherwise numbered ``ZEN<n>_*``
    vars are scanned. An empty/whitespace ``ZEN_ACCOUNTS`` counts as unset so
    the single-account legacy path stays enabled by default.
    """
    raw = os.getenv(ZEN_ACCOUNTS_ENV)
    if raw is not None and raw.strip():
        return _parse_accounts_json(raw)
    numbered = _parse_numbered_env()
    return numbered or None


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def build_router() -> ZenRouter | None:
    """Build a :class:`ZenRouter` from the environment; ``None`` when unset.

    Returns ``None`` (no multi-account config) so ``_llm.py`` keeps the
    legacy single-account egress-proxy path unless ``ZEN_ACCOUNTS`` (or
    ``ZEN<n>_*``) is actually configured.
    """
    accounts = accounts_from_env()
    if not accounts:
        return None
    return ZenRouter(
        accounts,
        cooldown_s=_env_float(ZEN_COOLDOWN_S_ENV, DEFAULT_COOLDOWN_S),
        max_retries=_env_int(ZEN_MAX_RETRIES_ENV, DEFAULT_MAX_RETRIES),
    )
