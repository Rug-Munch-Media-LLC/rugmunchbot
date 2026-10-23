"""``/setup`` groups backed by key/value fields — moderation, voting, features, admins.

Values arrive as Telegram strings. Coercion happens here (one place), then the
models re-validate — so ``/setup voting quorum_pct 900`` is rejected by pydantic
rather than clamped or silently stored.
"""

from __future__ import annotations

import re
from typing import Any

from rugmunchbot.community.config_models import FEATURE_FIELDS
from rugmunchbot.community.store import load_settings
from rugmunchbot.community.validation import update_setting

_INT = re.compile(r"^-?\d+$")
_FLOAT = re.compile(r"^-?\d+\.\d+$")
_TRUTHY = {"on", "true", "yes", "1"}
_FALSY = {"off", "false", "no", "0"}

_USAGE = {
    "moderation": "usage: /setup moderation &lt;field&gt; &lt;value&gt;",
    "voting": "usage: /setup voting &lt;field&gt; &lt;value&gt;",
    "features": "usage: /setup features &lt;field&gt; on|off",
    "admins": "usage: /setup admins add|remove &lt;id&gt; | list",
}


async def run_config_command(
    chat_id: int,
    user_id: int,
    group: str,
    rest: list[str],
    client: Any = None,
) -> str:
    """Dispatch one settings group and return the reply text."""
    if group == "admins":
        return await _admins(chat_id, user_id, rest, client)
    field = rest[0].lower() if rest else ""
    if not field or len(rest) < 2:
        return _fail(_USAGE.get(group, "missing arguments"))
    raw = " ".join(rest[1:])
    value: object
    if group == "features":
        if field not in FEATURE_FIELDS:
            return _fail(f"unknown feature field: {field}")
        enabled = _to_bool(raw)
        if enabled is None:
            return _fail("feature value must be on or off")
        value = enabled
    else:
        value = _coerce(raw)
    ok, error = await update_setting(chat_id, user_id, group, {field: value}, client=client)
    if not ok:
        return _fail(error or f"invalid {group} value")
    return f"✅ {group}.{field} → {value}"


async def _admins(chat_id: int, user_id: int, rest: list[str], client: Any) -> str:
    if not rest or rest[0].lower() == "list":
        current = (await load_settings(chat_id, client)).admins
        return "👤 <b>Extra admins:</b> " + (", ".join(map(str, current)) or "—")
    action = rest[0].lower()
    if action not in {"add", "remove"} or len(rest) < 2:
        return _fail(_USAGE["admins"])
    try:
        target = int(rest[1])
    except ValueError:
        return _fail(f"not a user id: {rest[1]}")
    current = list((await load_settings(chat_id, client)).admins)
    if action == "add":
        if target in current:
            return _fail(f"{target} is already an admin")
        current.append(target)
    else:
        if target not in current:
            return _fail(f"{target} is not an extra admin")
        current.remove(target)
    ok, error = await update_setting(chat_id, user_id, "admins", current, client=client)
    if not ok:
        return _fail(error or "could not update admins")
    return f"✅ {action}ed <code>{target}</code>"


def _to_bool(raw: str) -> bool | None:
    token = raw.strip().lower()
    if token in _TRUTHY:
        return True
    if token in _FALSY:
        return False
    return None


def _coerce(raw: str) -> object:
    """String → int / float / bool / word list, leaving free text alone."""
    token = raw.strip()
    if _INT.match(token):
        return int(token)
    if _FLOAT.match(token):
        return float(token)
    boolean = _to_bool(token)
    if boolean is not None:
        return boolean
    if "," in token:
        return [part.strip() for part in token.split(",") if part.strip()]
    return token


def _fail(message: str) -> str:
    return f"⛔ {message}"
