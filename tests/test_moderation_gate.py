"""Gate ordering + entrance protection (captcha, names, nsfw hash, timeout)."""

from __future__ import annotations

import asyncio
import json

import pytest
from telegram.ext import ApplicationHandlerStop

from rugmunchbot.moderation import (
    captcha,
    captcha_verify,
    challenge,
    config,
    names,
    nsfw,
    register,
    rules,
    state,
    timeout,
)
from rugmunchbot.moderation.gate import moderation_gate
from rugmunchbot.moderation.harness_fakes import (
    CHAT_ID,
    MATH_CHAT_ID,
    FakeBot,
    FakeChat,
    FakeMessage,
    FakeQuery,
    FakeQueue,
    FakeUpdate,
    FakeUser,
    ctx,
    policy,
    rig_context,
)


@pytest.fixture(autouse=True)
def _clean_state():
    state.reset()
    yield
    state.reset()


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "moderation.json"
    path.write_text(
        json.dumps(
            {
                "chats": {
                    str(CHAT_ID): {"dry_run": True, "captcha_type": "button"},
                    str(MATH_CHAT_ID): {"dry_run": True, "captcha_type": "math"},
                }
            }
        )
    )
    original = config.CONFIG_PATH
    config.use_config_path(path)
    yield path
    config.use_config_path(original)


class RecordingApp:
    def __init__(self):
        self.handlers = []

    def add_handler(self, handler, group=0):
        self.handlers.append((group, getattr(handler, "callback", None), handler))


def test_gate_is_registered_before_everything_else():
    app = RecordingApp()
    register.register_moderation(app)
    group, callback, _ = app.handlers[0]
    assert group == -1
    assert callback is moderation_gate
    names_ = {type(h).__name__ for _, _, h in app.handlers[1:]}
    assert names_ == {"CommandHandler"}


def test_join_is_consumed_by_the_gate(cfg):
    user = FakeUser(555, "newbie", "Newbie")
    message = FakeMessage(new_chat_members=[user])
    update = FakeUpdate(message=message, chat=FakeChat(CHAT_ID), user=user)
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(moderation_gate(update, rig_context(FakeBot(), FakeQueue())))


def test_commands_are_not_moderated_by_the_message_path(cfg):
    from rugmunchbot.moderation.gate import handle_message

    user = FakeUser(7)
    message = FakeMessage(text="/scan 0x1")
    update = FakeUpdate(message=message, chat=FakeChat(CHAT_ID), user=user)
    assert asyncio.run(handle_message(update, rig_context(FakeBot(), FakeQueue()))) is False


def test_captcha_button_is_not_yours(cfg):
    query = FakeQuery(captcha.callback_data(CHAT_ID, 555), FakeUser(999))
    update = FakeUpdate(chat=FakeChat(CHAT_ID), user=FakeUser(999), callback_query=query)
    assert asyncio.run(
        captcha_verify.handle_captcha_callback(update, rig_context(FakeBot(), FakeQueue()))
    )
    assert query.answers == ["This button isn't yours."]


def test_math_captcha_wrong_answer_fails_and_right_answer_passes(cfg, tmp_path):
    from rugmunchbot.moderation import audit

    original = audit.AUDIT_PATH
    audit.use_audit_path(tmp_path / "a.jsonl")
    user = FakeUser(556)
    right = challenge.challenge(MATH_CHAT_ID, 556)[2]
    prev = challenge.challenge(MATH_CHAT_ID, 556, now=challenge.time.time() - challenge.BUCKET_S)[2]
    bad = max(right, prev) + 7
    assert not challenge.accepts(MATH_CHAT_ID, 556, bad)

    wrong = FakeQuery(captcha.callback_data(MATH_CHAT_ID, 556, bad), user)
    asyncio.run(
        captcha_verify.handle_captcha_callback(
            FakeUpdate(chat=FakeChat(MATH_CHAT_ID), user=user, callback_query=wrong),
            rig_context(FakeBot(), FakeQueue()),
        )
    )
    assert audit.tail(MATH_CHAT_ID, 1)[0]["rule"] == "captcha_fail"

    good = FakeQuery(captcha.callback_data(MATH_CHAT_ID, 556, right), user)
    asyncio.run(
        captcha_verify.handle_captcha_callback(
            FakeUpdate(chat=FakeChat(MATH_CHAT_ID), user=user, callback_query=good),
            rig_context(FakeBot(), FakeQueue()),
        )
    )
    assert audit.tail(MATH_CHAT_ID, 1)[0]["rule"] == "captcha_pass"
    audit.use_audit_path(original)


def test_timeout_is_scheduled_and_dry_run_audits_without_writes():
    bot, queue = FakeBot(), FakeQueue()
    pol = policy(captcha_timeout_s=60)
    assert timeout.schedule(rig_context(bot, queue), CHAT_ID, 777, pol) is True
    job = queue.jobs[0]
    asyncio.run(job.callback(rig_context(bot, queue)))
    assert bot.calls == []
    assert state.is_verified(CHAT_ID, 777) is False


def test_name_policy_flags_homoglyph_and_spares_partial_matches():
    cyrillic = ctx("hi", display_name="\u0430dmin RugMunch")
    assert names.impersonation_reason(cyrillic, policy(impersonation_strict=True))
    assert names.impersonation_reason(ctx("hi", display_name="devon"), policy()) is None
    assert names.impersonation_reason(ctx("hi", display_name="dev"), policy()) is None
    assert names.impersonation_reason(
        ctx("hi", display_name="dev"), policy(impersonation_strict=True)
    )


def test_nsfw_hash_hit_and_clean_media():
    pol = policy(nsfw_hashes=[nsfw.digest("BAD")])
    assert rules.nsfw.hit("BAD", pol)
    assert rules.nsfw.hit("GOOD", pol) is None
