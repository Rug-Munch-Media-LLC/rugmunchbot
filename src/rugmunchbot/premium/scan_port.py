"""Scanner port — premium asks for scan facts, it does not import the scanner.

``premium.service.scan_payload`` used to do a function-level
``from rugmunchbot.tools.scanner import scan_token_v2``: a lazy import that (a)
broke the layer rule (domain -> integration package) and (b) named a module that
does not exist — the real scanner is ``rugmunchbot.scanner``. A call would have
raised ``ImportError`` long after boot.

The seam: the composition root (the layer that knows both the bot and the
scanner) calls :func:`register` once at startup; premium code only sees an
async callable. Until something registers, :class:`ScanUnavailable` says so
loudly instead of failing in a stack trace five frames deep.
"""

from __future__ import annotations

from typing import Any, Protocol


class ScanProvider(Protocol):
    """Narrow protocol: exactly what the premium derivations need."""

    async def __call__(self, token: str, chain: str) -> dict[str, Any] | None: ...


class ScanUnavailable(RuntimeError):
    """Raised when no scanner provider was registered at the composition root."""


_provider: ScanProvider | None = None


def register(provider: ScanProvider) -> None:
    """Install the scanner callable (called once, from the composition root)."""
    global _provider
    _provider = provider


def provider() -> ScanProvider | None:
    """The registered scanner callable, or ``None``."""
    return _provider


def clear() -> None:
    """Drop the provider (tests, and a clean shutdown in the worker)."""
    global _provider
    _provider = None


def resolve(explicit: ScanProvider | None = None) -> ScanProvider:
    """Return *explicit* or the registered provider, else raise."""
    chosen = explicit or _provider
    if chosen is None:
        raise ScanUnavailable(
            "no scan provider registered — the composition root must call "
            "rugmunchbot.premium.scan_port.register(scanner)"
        )
    return chosen


__all__ = ["ScanProvider", "ScanUnavailable", "clear", "provider", "register", "resolve"]
