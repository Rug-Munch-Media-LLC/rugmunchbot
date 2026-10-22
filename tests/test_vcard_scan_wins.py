"""Verdict-first scan card + bare-address auto-detect (PirbView wins).

Covers the five behaviours the card ships with:

  1. ``address_only`` fires on an address-only message (0x optional, one label
     allowed) and never on a sentence containing an address;
  2. group suppression — un-@mentioned group chatter is not addressed;
  3. card rules — hard fail forces AVOID, an unmeasured Top-10 is omitted,
     >100% cohort "bought" is labelled wash, thin evidence is not a high score;
  4. 4096 pruning drops optional blocks and keeps verdict + market;
  5. Refresh re-runs the scan and edits the SAME message (never a second one).

No ``mock.patch`` / ``monkeypatch`` fixtures: every seam is swapped by direct
assignment and restored in ``finally`` (the repo's monkeypatch ratchet is
decrease-only).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from telegram import MessageEntity

from rugmunchbot.formatters._vcard import TELEGRAM_LIMIT, format_verdict_card
from rugmunchbot.handlers.chat_ai._routing._address import address_only
from rugmunchbot.handlers.chat_ai._routing._handler import _is_bot_addressed

SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"


def _result(**over: object) -> dict:
    base = {
        "name": "Test Token",
        "symbol": "TEST",
        "address": SOL,
        "chain": "solana",
        "risk_score": 22,
        "liquidity_usd": 1_200_000,
        "mcap": 4_500_000,
        "volume_24h": 820_000,
        "price_change_24h": -3.5,
        "holders": 3241,
        "honeypot": False,
        "mintable": False,
        "freezable": False,
        "buy_tax": 0.05,
        "sell_tax": 0.05,
        "holder_concentration": {"top10_pct": 42.1},
        "cohorts": {
            "team": {"count": 3, "initial_buy_pct": 2.1, "current_pct": 0.4, "realized_sol": 12.5},
            "insiders": {"count": 2, "initial_buy_pct": 8.0, "current_pct": 0.0, "realized_sol": 0},
            "snipers": {
                "count": 14,
                "initial_buy_pct": 1.2,
                "current_pct": 0.9,
                "realized_sol": 31.2,
            },
        },
    }
    base.update(over)
    return base


class TestAddressOnly:
    """Rule: paste-to-scan, without hijacking normal chat."""

    def test_bare_solana(self) -> None:
        assert address_only(SOL) == SOL

    def test_bare_evm_with_prefix(self) -> None:
        assert address_only(EVM) == EVM

    def test_evm_without_prefix_is_canonicalised(self) -> None:
        assert address_only(EVM[2:]) == EVM

    def test_address_with_one_label(self) -> None:
        assert address_only(f"{EVM} pepe") == EVM

    def test_bot_mention_is_not_a_label(self) -> None:
        assert address_only(f"@RugMunchBot {SOL}") == SOL

    def test_sentence_is_not_a_scan(self) -> None:
        assert address_only(f"is this {EVM} a rug?") is None

    def test_two_addresses_are_ambiguous(self) -> None:
        assert address_only(f"{SOL} {EVM}") is None

    def test_empty_and_plain_text(self) -> None:
        assert address_only("") is None
        assert address_only("gm frens") is None


def _group_update(text: str, entities: list | None = None, reply_to=None) -> SimpleNamespace:
    message = SimpleNamespace(
        text=text,
        entities=entities or [],
        reply_to_message=reply_to,
        reply_text=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no reply in groups")),
    )
    chat = SimpleNamespace(type="group")
    return SimpleNamespace(
        effective_chat=chat,
        effective_user=SimpleNamespace(id=999_001, is_bot=False),
        message=message,
        get_bot=lambda: SimpleNamespace(username="RugMunchBot"),
    )


class TestGroupSuppression:
    """Un-@mentioned group chatter must never auto-scan."""

    def test_unmentioned_group_message_is_not_addressed(self) -> None:
        update = _group_update(SOL)
        assert _is_bot_addressed(update, update.message, "RugMunchBot") is False

    def test_mention_is_addressed(self) -> None:
        text = f"@RugMunchBot {SOL}"
        ent = SimpleNamespace(type=MessageEntity.MENTION, offset=0, length=len("@RugMunchBot"))
        update = _group_update(text, [ent])
        assert _is_bot_addressed(update, update.message, "RugMunchBot") is True

    def test_private_chat_always_addressed(self) -> None:
        update = _group_update(SOL)
        update.effective_chat = SimpleNamespace(type="private")
        assert _is_bot_addressed(update, update.message, "RugMunchBot") is True

    def test_handler_returns_before_any_db_work(self) -> None:
        from rugmunchbot.handlers.chat_ai._routing import _handler

        original = _handler.run_db
        _handler.run_db = lambda *a, **k: (_ for _ in ()).throw(AssertionError("db touched"))
        try:
            update = _group_update(SOL)
            asyncio.run(_handler.ai_chat_handler(update, SimpleNamespace(args=[], bot=None)))
        finally:
            _handler.run_db = original


class TestCardRules:
    """Rules 1-2-6-10: worst-of verdict, no fake zero, wash label, honesty."""

    def test_hard_fail_forces_avoid(self) -> None:
        card = format_verdict_card(_result(honeypot=True, risk_score=5))
        assert card.splitlines()[0].find("AVOID") > 0

    def test_unmeasured_top10_is_omitted(self) -> None:
        card = format_verdict_card(_result(holder_concentration={"top10_pct": 0.0}))
        assert "Top-10" not in card

    def test_wash_recycling_is_labelled(self) -> None:
        cohorts = {
            "team": {"count": 3, "initial_buy_pct": 340.0, "current_pct": 1.0, "realized_sol": 9},
            "insiders": {"count": 2, "initial_buy_pct": 1.0, "current_pct": 1.0, "realized_sol": 0},
            "snipers": {"count": 4, "initial_buy_pct": 1.0, "current_pct": 1.0, "realized_sol": 0},
        }
        card = format_verdict_card(_result(cohorts=cohorts))
        assert "recycled, not demand" in card
        assert "strong demand" not in card

    def test_thin_evidence_is_not_a_high_score(self) -> None:
        card = format_verdict_card(
            _result(
                risk_score=88,
                honeypot=None,
                mintable=None,
                freezable=None,
                cohorts=None,
                holder_concentration=None,
                destroyer_profile=None,
            )
        )
        assert "INSUFFICIENT EVIDENCE" in card

    def test_cluster_line_has_bought_current_realised(self) -> None:
        card = format_verdict_card(_result())
        assert "Team 3: 2.1% \u2192 0.4% \u00b7 12.50 SOL" in card

    def test_every_number_has_source_and_stamp(self) -> None:
        card = format_verdict_card(_result())
        assert "dexscreener" in card and "onchain" in card
        assert "Updated " in card


class TestPruning:
    """Rule: 4096 cap drops optional blocks first, never the verdict/market."""

    def test_optional_blocks_dropped_verdict_kept(self) -> None:
        # Three holders whose tags are 1.5 KB each: the optional block alone
        # blows past 4096, so pruning must drop it and keep the core card.
        holders = [{"address": "x" * 80, "pct": 1.5, "tag": "y" * 1500} for _ in range(3)]
        card = format_verdict_card(_result(top_holders=holders))
        assert len(card) <= TELEGRAM_LIMIT
        assert "AVOID" in card or "RISK" in card
        assert "Market" in card
        assert "Top holders" not in card


class TestRefresh:
    """Rule 9: one message — Refresh edits it, never sends another."""

    def test_refresh_edits_same_message(self) -> None:
        from rugmunchbot.handlers.callback import _vcard

        class FakeQuery:
            def __init__(self) -> None:
                self.edits: list[str] = []
                self.answers: list[str] = []

            async def edit_message_text(self, text: str, **kwargs: object) -> None:
                self.edits.append(text)

            async def answer(self, text: str = "", **kwargs: object) -> None:
                self.answers.append(text)

        query = FakeQuery()
        original_load, original_owner = _vcard.load_scan, _vcard.is_owner

        async def fake_load(address: str, chain: str | None = None, *, fresh: bool = False) -> dict:
            assert fresh is True, "Refresh must bypass the 30s scan cache"
            return _result()

        _vcard.load_scan = fake_load
        _vcard.is_owner = lambda uid: True
        try:
            handled = asyncio.run(
                _vcard.handle(SimpleNamespace(), SimpleNamespace(), query, f"vcard_refresh_{SOL}")
            )
        finally:
            _vcard.load_scan, _vcard.is_owner = original_load, original_owner

        assert handled is True
        assert len(query.edits) == 2, "skeleton + card, one message"
        assert (
            ("Scanning" not in query.edits[-1]
            and "AVOID" in query.edits[-1])
            or "RISK" in query.edits[-1]
        )
