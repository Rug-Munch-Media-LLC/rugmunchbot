"""Handler wiring for /buybot — offline, fake bot / redis / swap."""

from __future__ import annotations

from buybot_fakes import (
    CHAT,
    MINT,
    TOKEN_DOC,
    FakeBot,
    FakeRedis,
    call,
    context,
    registered,
    state,
    update,
)


def test_registers_five_commands_and_a_callback_router() -> None:
    commands, callbacks = registered()
    assert set(commands) == {
        "buybot",
        "buybot_on",
        "buybot_off",
        "buybot_preview",
        "buybot_template",
    }
    assert len(callbacks) == 1


def test_template_lists_the_canonical_rows() -> None:
    update_obj, context_obj = update(), context()
    call("buybot_template", update_obj, context_obj)
    reply = update_obj.effective_message.replies[-1]
    assert "amount, usd, price, mcap, liquidity, risk" in reply


def test_non_admin_is_refused_and_nothing_is_written() -> None:
    redis = FakeRedis()
    update_obj = update(user_id=7)
    call("buybot", update_obj, context(bot=FakeBot(admins=()), redis=redis))
    assert "Admin only" in update_obj.effective_message.replies[0]
    assert redis.document is None


def test_setup_card_appears_before_any_token() -> None:
    update_obj, context_obj = update(), context()
    call("buybot", update_obj, context_obj)
    assert "not configured yet" in update_obj.effective_message.replies[0]


def test_token_and_presets_are_validated_and_persisted() -> None:
    redis = FakeRedis()
    update_obj = update()
    call("buybot", update_obj, context(redis=redis, args=["token", "solana", MINT, "bonk"]))
    assert redis.document["token"]["address"] == MINT
    assert "BONK" in update_obj.effective_message.replies[-1]

    update_obj = update()
    call("buybot", update_obj, context(redis=redis, args=["presets", "0.1,0.5"]))
    assert redis.document["amounts"]["buy"] == [0.1, 0.5]

    update_obj = update()
    call("buybot", update_obj, context(redis=redis, args=["token", "solana", "nope"]))
    assert "⚠️" in update_obj.effective_message.replies[-1]
    assert redis.document["token"]["address"] == MINT


def test_unknown_option_is_explained_not_crashed() -> None:
    update_obj = update()
    call("buybot", update_obj, context(redis=FakeRedis(), args=["teleport", "x"]))
    assert "unknown option" in update_obj.effective_message.replies[-1]


def test_on_requires_a_token_then_enables() -> None:
    redis = FakeRedis()
    update_obj = update()
    call("buybot_on", update_obj, context(redis=redis))
    assert "not configured yet" in update_obj.effective_message.replies[0]

    redis.document = dict(TOKEN_DOC, enabled=False)
    update_obj = update()
    call("buybot_on", update_obj, context(redis=redis))
    assert redis.document["enabled"] is True
    assert "ON" in update_obj.effective_message.replies[-1]


def test_off_disables_but_keeps_the_drop_counter() -> None:
    redis = FakeRedis(dict(TOKEN_DOC))
    bot_state = state()
    bot_state.ledger.allow_card(CHAT, 1)
    bot_state.ledger.allow_card(CHAT, 1)
    assert bot_state.ledger.drops(CHAT) == 1
    update_obj = update()
    call("buybot_off", update_obj, context(redis=redis, state=bot_state))
    assert redis.document["enabled"] is False
    assert bot_state.ledger.drops(CHAT) == 1


def test_preview_renders_escaped_card_with_amount_buttons() -> None:
    redis = FakeRedis(dict(TOKEN_DOC))
    update_obj = update()
    call("buybot_preview", update_obj, context(redis=redis, state=state(), args=["1"]))
    reply = update_obj.effective_message.replies[-1]
    assert "preview — no buy happened" in reply
    assert "BONK" in reply
    keyboard = update_obj.effective_message.keyboards[-1]
    assert keyboard is not None
    assert keyboard.inline_keyboard
    first_button = keyboard.inline_keyboard[0][0]
    assert first_button.callback_data.startswith(f"buy|{CHAT}|")


def test_preview_without_token_shows_setup() -> None:
    update_obj = update()
    call("buybot_preview", update_obj, context(redis=FakeRedis(), args=["1"]))
    assert "not configured yet" in update_obj.effective_message.replies[-1]


def test_status_card_reports_off_for_a_disabled_chat() -> None:
    redis = FakeRedis(dict(TOKEN_DOC, enabled=False))
    update_obj = update()
    call("buybot", update_obj, context(redis=redis, args=["token", "solana", MINT, "bonk"]))
    assert "OFF" in update_obj.effective_message.replies[-1]
