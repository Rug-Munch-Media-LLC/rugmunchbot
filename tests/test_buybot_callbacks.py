"""Callback router for the popup buttons — offline, fake swap."""

from __future__ import annotations

from buybot_fakes import CHAT, MINT, TOKEN_DOC, FakeQuery, FakeRedis, FakeSwap, context, state, tap

from rugmunchbot.buybot.execution import BuyExecutor
from rugmunchbot.buybot.render import mint_ref


def _redis(**overrides) -> FakeRedis:
    return FakeRedis(dict(TOKEN_DOC, **overrides))


def test_tap_without_swap_service_returns_a_preview_and_never_settles() -> None:
    query = FakeQuery(f"buy|{CHAT}|{mint_ref(MINT)}|0.5")
    tap(query, context(redis=_redis(), state=state()))
    assert "Preview only" in query.answers[-1]
    assert "not configured" in query.answers[-1]


def test_tap_is_idempotent_and_submits_once() -> None:
    swap = FakeSwap()
    bot_state = state()
    bot_state.executor = BuyExecutor(swap)
    data = f"buy|{CHAT}|{mint_ref(MINT)}|0.5"
    first, second = FakeQuery(data), FakeQuery(data)
    tap(first, context(redis=_redis(), state=bot_state))
    tap(second, context(redis=_redis(), state=bot_state))
    assert "Submitted" in first.answers[-1]
    assert "Already submitted" in second.answers[-1]
    assert swap.calls == 1


def test_stale_token_is_refused() -> None:
    query = FakeQuery(f"buy|{CHAT}|deadbeefdead|0.5")
    tap(query, context(redis=_redis(), state=state()))
    assert "token changed" in query.answers[-1].lower()


def test_closed_panel_is_refused() -> None:
    query = FakeQuery(f"buy|{CHAT}|{mint_ref(MINT)}|0.5")
    tap(query, context(redis=_redis(enabled=False), state=state()))
    assert "closed" in query.answers[-1].lower()


def test_amount_outside_presets_is_refused() -> None:
    query = FakeQuery(f"buy|{CHAT}|{mint_ref(MINT)}|99")
    tap(query, context(redis=_redis(), state=state()))
    assert "no longer offered" in query.answers[-1]


def test_foreign_chat_payload_is_refused() -> None:
    query = FakeQuery(f"buy|-100999|{mint_ref(MINT)}|0.5")
    tap(query, context(redis=_redis(), state=state()))
    assert "stale" in query.answers[-1].lower()


def test_malformed_payload_is_refused() -> None:
    query = FakeQuery("buy|only-two-parts")
    tap(query, context(redis=_redis(), state=state()))
    assert "stale" in query.answers[-1].lower()


def test_action_button_points_at_the_configured_token() -> None:
    query = FakeQuery(f"tok|{CHAT}|{mint_ref(MINT)}|chart")
    tap(query, context(redis=_redis(), state=state()))
    assert query.answers[-1] == f"/chart {MINT}"


def test_unknown_action_says_so() -> None:
    query = FakeQuery(f"tok|{CHAT}|{mint_ref(MINT)}|teleport")
    tap(query, context(redis=_redis(), state=state()))
    assert "Unknown action" in query.answers[-1]
