"""Validated single-key updates for community settings.

The only write path used by ``/setup``. A change is applied to an in-memory
copy, re-validated by the models, persisted, and only then audited — so Redis
never holds a value the models reject and the audit trail never records a
change that did not happen.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError

from rugmunchbot.community.audit import append_audit
from rugmunchbot.community.config_models import (
    FEATURE_FIELDS,
    MODERATION_FIELDS,
    VOTING_FIELDS,
    CommunitySettings,
    FeaturesConfig,
    ModerationConfig,
    VotingConfig,
)
from rugmunchbot.community.models import AmountPresets, EmojiConfig, TokenConfig
from rugmunchbot.community.store import load_settings, save_settings

#: Top-level keys a caller may write.
SETTING_KEYS: frozenset[str] = frozenset(
    {"token", "amounts", "emojis", "moderation", "voting", "admins", "features"}
)

_ADMIN_IDS = TypeAdapter(list[int])
_MODELS = {"moderation": ModerationConfig, "voting": VotingConfig, "features": FeaturesConfig}


async def update_setting(
    chat_id: int,
    user_id: int,
    key: str,
    value: Any,
    *,
    sub_key: str | None = None,
    client: Any = None,
) -> tuple[bool, str | None]:
    """Validate + persist one change.

    Returns ``(True, None)`` on success or ``(False, reason)`` when the value
    is rejected or the store refused the write. The reason is safe to show an
    admin — never a traceback or a secret.
    """
    settings = await load_settings(chat_id, client)
    previous = _snapshot(settings, key, sub_key)
    try:
        _apply(settings, key, value, sub_key)
    except (ValidationError, ValueError) as exc:
        return False, _reason(exc)

    settings.updated_by = user_id
    if not await save_settings(settings, client):
        return False, "could not save settings (storage unavailable)"
    await append_audit(chat_id, user_id, _audit_name(key, sub_key), previous, value, client=client)
    return True, None


def _snapshot(settings: CommunitySettings, key: str, sub_key: str | None) -> Any:
    """Current value of the field about to change (for the audit trail)."""
    if key not in SETTING_KEYS:
        return None
    current = settings.model_dump(mode="json").get(key)
    if sub_key and isinstance(current, dict):
        return current.get(sub_key)
    return current


def _audit_name(key: str, sub_key: str | None) -> str:
    return f"{key}.{sub_key}" if sub_key else key


def _reason(exc: Exception) -> str:
    """First human-readable line from a pydantic or domain error."""
    if isinstance(exc, ValidationError):
        error = exc.errors()[0]
        return str(error.get("msg", "invalid value")).removeprefix("Value error, ")
    return str(exc)


def _apply(
    settings: CommunitySettings,
    key: str,
    value: Any,
    sub_key: str | None,
) -> None:
    """Validate *value* against the model and assign it in place."""
    if key == "token":
        if not isinstance(value, dict):
            raise ValueError("token needs chain, address, symbol and name")
        settings.token = TokenConfig.model_validate(value)
        return

    if key == "amounts":
        if sub_key not in {"buy", "sell"}:
            raise ValueError("amounts takes a buy or sell preset list")
        merged = settings.amounts.model_dump()
        merged[sub_key] = value
        settings.amounts = AmountPresets.model_validate(merged)
        return

    if key == "emojis":
        if sub_key not in {"buy", "sell"}:
            raise ValueError("emoji takes a buy or sell button")
        if not isinstance(value, dict):
            raise ValueError("emoji needs an emoji and/or label")
        current = settings.emojis.model_dump()
        current[sub_key] = {**current[sub_key], **value}
        settings.emojis = EmojiConfig.model_validate(current)
        return

    if key == "admins":
        settings.admins = _parse_admins(value)
        return

    if key in _MODELS:
        if not isinstance(value, dict):
            raise ValueError(f"{key} needs a field/value object")
        _reject_unknown(key, value)
        merged = {**getattr(settings, key).model_dump(), **value}
        setattr(settings, key, _MODELS[key].model_validate(merged))
        return

    raise ValueError(f"unknown setting key: {key}")


def _reject_unknown(key: str, value: dict[str, Any]) -> None:
    """A typo must fail loudly, never silently do nothing."""
    allowed = {
        "moderation": MODERATION_FIELDS,
        "voting": VOTING_FIELDS,
        "features": FEATURE_FIELDS,
    }[key]
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"unknown {key} field(s): {', '.join(unknown)}")


def _parse_admins(value: Any) -> list[int]:
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    return _ADMIN_IDS.validate_python(value)
