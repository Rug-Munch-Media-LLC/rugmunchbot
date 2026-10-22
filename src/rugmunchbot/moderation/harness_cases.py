"""Synthetic updates for the dry harness — real handlers, fake transport.

Every case runs the production code path (captcha handlers, ``decide``,
``execute``) with ``dry_run`` on, so the table printed by the harness is the
behaviour an operator will get before flipping a chat live.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from rugmunchbot.moderation import (
    actions,
    audit,
    captcha,
    captcha_verify,
    decide as decide_mod,
    nsfw,
    state,
    timeout,
)
from rugmunchbot.moderation.harness_fakes import (
    CHAT_ID,
    FakeChat,
    FakeMessage,
    FakeQuery,
    FakeUpdate,
    FakeUser,
    ctx,
    policy,
    rig_context,
)
from rugmunchbot.moderation.models import AddressVerdict

ALLOW = ("allow", "none", "no rule fired")


def _last(chat_id: int = CHAT_ID) -> tuple[str, str, str]:
    rows = audit.tail(chat_id, 1)
    if not rows:
        return ALLOW
    row = rows[0]
    return (str(row.get("rule")), str(row.get("action")), str(row.get("reason")))


async def _message(
    rig: Any, pol: Any, text: str = "", *, repeats: int = 1, verdicts: Any = None, **kw: Any
) -> tuple[str, str, str]:
    action = None
    message_ctx = ctx(text, **kw)
    for _ in range(repeats):
        message_ctx = ctx(text, **kw)
        action = decide_mod.decide(message_ctx, pol, verdicts or {})
    if action is None:
        return ALLOW
    await actions.execute(rig.bot, message_ctx, pol, action)
    return (action.rule, action.action, action.reason)


async def _join(rig: Any) -> tuple[str, str, str]:
    user = FakeUser(555, "newbie", "Newbie")
    message = FakeMessage(new_chat_members=[user], message_id=10)
    update = FakeUpdate(message=message, chat=rig.chat, user=user)
    await captcha.handle_new_members(update, rig_context(rig.bot, rig.queue))
    return _last()


async def _captcha_pass(rig: Any) -> tuple[str, str, str]:
    user = FakeUser(555, "newbie", "Newbie")
    query = FakeQuery(captcha.callback_data(CHAT_ID, user.id), user)
    update = FakeUpdate(chat=rig.chat, user=user, callback_query=query)
    await captcha_verify.handle_captcha_callback(update, rig_context(rig.bot, rig.queue))
    return _last()


async def _captcha_fail(rig: Any) -> tuple[str, str, str]:
    from rugmunchbot.moderation.harness_fakes import MATH_CHAT_ID

    user = FakeUser(556, "bot", "Bot")
    query = FakeQuery(captcha.callback_data(MATH_CHAT_ID, user.id, 999), user)
    update = FakeUpdate(
        chat=FakeChat(MATH_CHAT_ID, "RugMunch Math"), user=user, callback_query=query
    )
    await captcha_verify.handle_captcha_callback(update, rig_context(rig.bot, rig.queue))
    return _last(MATH_CHAT_ID)


async def _captcha_timeout(rig: Any) -> tuple[str, str, str]:
    pol = policy(captcha_timeout_s=60, captcha_kick=False)
    timeout.schedule(rig_context(rig.bot, rig.queue), CHAT_ID, 777, pol)
    job = rig.queue.jobs[-1]
    await job.callback(SimpleNamespace(bot=rig.bot))
    return _last()


async def _scam_link(rig: Any) -> tuple[str, str, str]:
    url = "https://claim-airdrop.xyz/x"
    return await _message(rig, policy(), f"claim now {url}", urls=(url,))


async def _invite_link(rig: Any) -> tuple[str, str, str]:
    url = "https://t.me/+AbCdEf123"
    return await _message(rig, policy(), f"join {url}", urls=(url,))


async def _nsfw(rig: Any) -> tuple[str, str, str]:
    pol = policy(nsfw_hashes=[nsfw.digest("BAD-MEDIA")])
    return await _message(rig, pol, "look", media="photo", media_id="BAD-MEDIA")


async def _promo(rig: Any) -> tuple[str, str, str]:
    return await _message(rig, policy(), "100x guaranteed, dm me for signals")


async def _unlisted(rig: Any) -> tuple[str, str, str]:
    url = "https://random-shill.xyz/a"
    return await _message(rig, policy(), f"check {url}", urls=(url,))


async def _repeat(rig: Any) -> tuple[str, str, str]:
    return await _message(rig, policy(), "same text", repeats=3)


async def _velocity(rig: Any) -> tuple[str, str, str]:
    pol = policy()
    action = None
    for i in range(pol.user_msgs):
        action = decide_mod.decide(ctx(f"rapid {i}", user_id=42), pol)
    assert action is not None
    await actions.execute(rig.bot, ctx("rapid", user_id=42), pol, action)
    return (action.rule, action.action, action.reason)


async def _raid(rig: Any) -> tuple[str, str, str]:
    pol = policy()
    action = None
    for i in range(pol.chat_msgs):
        action = decide_mod.decide(ctx(f"raid {i}", user_id=5000 + i), pol)
    assert action is not None
    await actions.execute(rig.bot, ctx("raid", user_id=5000), pol, action)
    return (action.rule, action.action, action.reason)


async def _address_avoid(rig: Any) -> tuple[str, str, str]:
    bad = AddressVerdict(
        address="0xbad",
        risk_level="critical",
        safety_score=3,
        verdict="avoid",
        reason="critical: honeypot, mintable",
    )
    return await _message(rig, policy(), "buy 0xbad", verdicts={"0xbad": bad})


async def _address_high(rig: Any) -> tuple[str, str, str]:
    risk = AddressVerdict(
        address="0xrisk",
        risk_level="high",
        safety_score=30,
        verdict="caution",
        reason="high: top10 91%",
    )
    return await _message(rig, policy(), "chart 0xrisk", verdicts={"0xrisk": risk})


async def _media_burst(rig: Any) -> tuple[str, str, str]:
    state.mark_member(CHAT_ID, 43, now=time.time())
    return await _message(rig, policy(), "pic", media="photo", media_id="m1", user_id=43, repeats=2)


async def _impersonation(rig: Any) -> tuple[str, str, str]:
    return await _message(rig, policy(), "hello", display_name="RugMunch Support")


async def _clean(rig: Any) -> tuple[str, str, str]:
    return await _message(rig, policy(), "gm, sol looking strong today")


CASES: list[tuple[str, Any, str]] = [
    ("join", _join, "new_member_gate"),
    ("captcha_pass", _captcha_pass, "captcha_pass"),
    ("captcha_fail", _captcha_fail, "captcha_fail"),
    ("captcha_timeout", _captcha_timeout, "captcha_timeout"),
    ("scam_link", _scam_link, "scam_link"),
    ("invite_link", _invite_link, "invite_link"),
    ("nsfw_hash", _nsfw, "nsfw_media"),
    ("promo", _promo, "promo"),
    ("unlisted_link", _unlisted, "unlisted_link"),
    ("repeat_message", _repeat, "repeat_message"),
    ("user_velocity", _velocity, "user_velocity"),
    ("chat_raid", _raid, "chat_raid"),
    ("address_avoid", _address_avoid, "address_risk"),
    ("address_high", _address_high, "address_risk"),
    ("media_burst", _media_burst, "media_burst"),
    ("impersonation", _impersonation, "impersonation"),
    ("clean_message", _clean, "allow"),
]
