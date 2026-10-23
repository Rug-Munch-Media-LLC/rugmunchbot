"""Pure ``/setup`` command logic — no Telegram objects, fully unit-testable.

``handlers.community_admin`` owns authorization and I/O; this module owns the
command grammar and returns the reply text. Keeping it Telegram-free is what
lets the tests exercise every subcommand without a network or a Bot instance.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.community.audit import AUDIT_PAGE, clear_audit, get_audit_log
from rugmunchbot.community.commands_config import run_config_command
from rugmunchbot.community.render import audit_text, summary_text
from rugmunchbot.community.store import delete_settings, load_settings
from rugmunchbot.community.validation import update_setting

SETUP_HELP = (
    "⚙️ <b>Channel settings</b> — admin only\n"
    "────────────────────\n"
    "<b>Token</b>\n"
    "  /setup token &lt;chain&gt; &lt;address&gt; &lt;symbol&gt; &lt;name&gt;\n"
    "<b>Buttons</b>\n"
    "  /setup amounts buy|sell 0.1,0.5,1\n"
    "  /setup emoji buy|sell 🟢 Buy\n"
    "<b>Moderation</b>\n"
    "  /setup moderation max_links_per_message 3\n"
    "  /setup moderation banned_words spam,scam\n"
    "<b>Voting</b>\n"
    "  /setup voting enabled on\n"
    "<b>Features</b>\n"
    "  /setup features trading off\n"
    "<b>Admins</b>\n"
    "  /setup admins add|remove &lt;user_id&gt; · /setup admins list\n"
    "<b>History</b>\n"
    "  /setup show · /setup audit · /setup reset confirm"
)

_SETTINGS_GROUP = frozenset({"moderation", "voting", "features", "admins"})


async def run_setup_command(
    chat_id: int,
    user_id: int,
    args: list[str],
    client: Any = None,
) -> str:
    """Dispatch ``/setup`` arguments and return the reply text."""
    if not args or args[0].lower() in {"help", "-h", "?"}:
        return SETUP_HELP
    sub = args[0].lower()
    rest = args[1:]
    if sub in {"show", "status"}:
        return summary_text(await load_settings(chat_id, client))
    if sub == "token":
        return await _set_token(chat_id, user_id, rest, client)
    if sub == "amounts":
        return await _set_amounts(chat_id, user_id, rest, client)
    if sub in {"emoji", "emojis"}:
        return await _set_emoji(chat_id, user_id, rest, client)
    if sub == "audit":
        return audit_text(await get_audit_log(chat_id, AUDIT_PAGE, client))
    if sub == "reset":
        return await _reset(chat_id, rest, client)
    if sub in _SETTINGS_GROUP:
        return await run_config_command(chat_id, user_id, sub, rest, client)
    return _fail(f"unknown subcommand <code>{sub}</code> — try /setup help")


async def _set_token(chat_id: int, user_id: int, rest: list[str], client: Any) -> str:
    if len(rest) < 4:
        return _fail(
            "usage: /setup token &lt;chain&gt; &lt;address&gt; &lt;symbol&gt; &lt;name&gt;"
        )
    chain, address, symbol = rest[0].lower(), rest[1], rest[2]
    value = {"chain": chain, "address": address, "symbol": symbol, "name": " ".join(rest[3:])}
    ok, error = await update_setting(chat_id, user_id, "token", value, client=client)
    if not ok:
        return _fail(error or "invalid token config")
    return _ok(f"token set to <b>{symbol.upper()}</b> on <code>{chain}</code>")


async def _set_amounts(chat_id: int, user_id: int, rest: list[str], client: Any) -> str:
    if len(rest) < 2 or rest[0].lower() not in {"buy", "sell"}:
        return _fail("usage: /setup amounts buy|sell 0.1,0.5,1")
    side = rest[0].lower()
    ok, error = await update_setting(
        chat_id, user_id, "amounts", ",".join(rest[1:]), sub_key=side, client=client
    )
    if not ok:
        return _fail(error or "invalid amount presets")
    return _ok(f"{side} presets updated")


async def _set_emoji(chat_id: int, user_id: int, rest: list[str], client: Any) -> str:
    if len(rest) < 2 or rest[0].lower() not in {"buy", "sell"}:
        return _fail("usage: /setup emoji buy|sell 🟢 Buy")
    side = rest[0].lower()
    value: dict[str, str] = {"emoji": rest[1]}
    if len(rest) > 2:
        value["label"] = " ".join(rest[2:])
    ok, error = await update_setting(chat_id, user_id, "emojis", value, sub_key=side, client=client)
    if not ok:
        return _fail(error or "invalid button config")
    return _ok(f"{side} button updated")


async def _reset(chat_id: int, rest: list[str], client: Any) -> str:
    if not rest or rest[0].lower() != "confirm":
        return _fail("this wipes every setting for this chat — send /setup reset confirm")
    if not await delete_settings(chat_id, client):
        return _fail("could not reset (storage unavailable)")
    await clear_audit(chat_id, client)
    return _ok("all settings reset to defaults")


def _ok(message: str) -> str:
    return f"✅ {message}"


def _fail(message: str) -> str:
    return f"⛔ {message}"
