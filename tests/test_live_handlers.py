"""Tests for live handler command routing."""

from __future__ import annotations


class TestLiveHandlerImports:
    """Verify all live handler modules import without errors."""

    def test_import_handlers(self):
        from rugmunchbot import handlers

        assert handlers is not None

    def test_import_scan_handlers(self):
        from rugmunchbot.handlers import _scan_handlers

        assert _scan_handlers is not None

    def test_import_alerts(self):
        from rugmunchbot.handlers import alerts

        assert alerts is not None

    def test_import_payment_handlers(self):
        from rugmunchbot.handlers import payments

        assert payments is not None

    def test_import_callback(self):
        from rugmunchbot.handlers import callback

        assert callback is not None

    def test_import_inline(self):
        from rugmunchbot.handlers import inline

        assert inline is not None

    def test_import_chat_ai(self):
        from rugmunchbot.handlers import chat_ai

        assert chat_ai is not None

    def test_import_ta_handlers(self):
        from rugmunchbot.handlers import _ta_handlers

        assert _ta_handlers is not None

    def test_import_scanning(self):
        from rugmunchbot.handlers import scanning

        assert scanning is not None

    def test_import_deepscan(self):
        from rugmunchbot.handlers import deepscan

        assert deepscan is not None

    def test_import_watchlist(self):
        from rugmunchbot.handlers import watchlist

        assert watchlist is not None

    def test_import_portfolio(self):
        from rugmunchbot.handlers import portfolio

        assert portfolio is not None

    def test_import_market(self):
        from rugmunchbot.handlers import market

        assert market is not None

    def test_import_price(self):
        from rugmunchbot.handlers import price

        assert price is not None

    def test_import_status(self):
        from rugmunchbot.handlers import status

        assert status is not None

    def test_import_trace(self):
        from rugmunchbot.handlers import trace

        assert trace is not None

    def test_import_osint(self):
        from rugmunchbot.handlers import osint

        assert osint is not None

    def test_import_research(self):
        from rugmunchbot.handlers import research

        assert research is not None

    def test_import_report(self):
        from rugmunchbot.handlers import report

        assert report is not None

    def test_import_sentiment(self):
        from rugmunchbot.handlers import sentiment

        assert sentiment is not None

    def test_import_holders(self):
        from rugmunchbot.handlers import holders

        assert holders is not None

    def test_import_hlwhales(self):
        from rugmunchbot.handlers import hlwhales

        assert hlwhales is not None

    def test_import_liqmap(self):
        from rugmunchbot.handlers import liqmap

        assert liqmap is not None

    def test_import_prediction(self):
        from rugmunchbot.handlers import prediction

        assert prediction is not None

    def test_import_dislocation(self):
        from rugmunchbot.handlers import dislocation

        assert dislocation is not None

    def test_import_defi(self):
        from rugmunchbot.handlers import defi

        assert defi is not None

    def test_import_cascade_token(self):
        from rugmunchbot.handlers import cascade_token

        assert cascade_token is not None

    def test_import_account(self):
        from rugmunchbot.handlers import account

        assert account is not None

    def test_import_ask(self):
        from rugmunchbot.handlers import ask

        assert ask is not None

    def test_import_chat(self):
        from rugmunchbot.handlers import chat

        assert chat is not None

    def test_import_onboarding(self):
        from rugmunchbot.handlers import onboarding

        assert onboarding is not None

    def test_import_groups(self):
        from rugmunchbot.handlers import groups

        assert groups is not None

    def test_import_integrity(self):
        from rugmunchbot.handlers import integrity_handlers

        assert integrity_handlers is not None

    def test_import_error(self):
        from rugmunchbot.handlers import error

        assert error is not None

    def test_import_messages(self):
        from rugmunchbot.handlers import _messages

        assert _messages is not None

    def test_import_help(self):
        from rugmunchbot.handlers import _help_text

        assert _help_text is not None

    def test_import_image(self):
        from rugmunchbot.handlers import _image

        assert _image is not None

    def test_import_charts(self):
        from rugmunchbot.handlers import _charts

        assert _charts is not None

    def test_import_trending(self):
        from rugmunchbot.handlers import _trending

        assert _trending is not None

    def test_import_engagement(self):
        from rugmunchbot.handlers import _engagement

        assert _engagement is not None

    def test_import_market_helpers(self):
        from rugmunchbot.handlers import _market_helpers

        assert _market_helpers is not None

    def test_import_scanning_helpers(self):
        from rugmunchbot.handlers import _scanning_helpers

        assert _scanning_helpers is not None

    def test_import_core_handlers(self):
        from rugmunchbot.handlers import _core_handlers

        assert _core_handlers is not None

    def test_import_api_access(self):
        from rugmunchbot.handlers import api_access

        assert api_access is not None

    def test_import_webhook(self):
        from rugmunchbot.handlers import webhook

        assert webhook is not None

    def test_import_owner(self):
        from rugmunchbot.handlers import owner

        assert owner is not None


class TestHandlerRegistration:
    """Verify handlers are properly registered."""

    def test_scan_handler_exists(self):
        from rugmunchbot.handlers._scan_handlers._scan import cmd_scan

        assert callable(cmd_scan)

    def test_alert_handler_exists(self):
        from rugmunchbot.handlers.alerts import cmd_alerts

        assert callable(cmd_alerts)


class TestBotConfig:
    """Verify bot configuration is valid."""

    def test_config_import(self):
        from rugmunchbot import config

        assert config is not None

    def test_owner_ids_resolvable(self):
        from rugmunchbot.config import _resolve_owner_ids

        assert callable(_resolve_owner_ids)

    def test_tiers_configurable(self):
        from rugmunchbot.config import TIERS

        assert isinstance(TIERS, dict)
        assert len(TIERS) > 0
