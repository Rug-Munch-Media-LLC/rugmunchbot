"""
Tests for config.py — env var loading, TIERS validation, formatting helpers.
"""

import os
from unittest import mock


# Import the config module — it runs at import time so we need to
# set env vars before importing. We'll use fresh copies of the module.
def _import_config():
    """Import config.py, clearing any cached version."""
    from bot.app.telegram_bot import config

    return config


class TestEnvVarLoading:
    """Verify config reads environment variables with correct defaults."""

    def test_default_bot_token_empty(self):
        """Without env var, BOT_TOKEN should be empty string."""
        with mock.patch.dict(os.environ, {}, clear=True):
            import importlib

            from bot.app.telegram_bot import config

            importlib.reload(config)
            assert config.BOT_TOKEN == ""

    def test_bot_token_from_env(self):
        with mock.patch.dict(os.environ, {"RUGMUNCH_BOT_TOKEN": "test_token_123"}):
            import importlib

            from bot.app.telegram_bot import config

            importlib.reload(config)
            assert config.BOT_TOKEN == "test_token_123"

    def test_default_owner_ids(self):
        """Without env var, OWNER_IDS should contain the hardcoded default."""
        with mock.patch.dict(os.environ, {}, clear=True):
            import importlib

            from bot.app.telegram_bot import config

            importlib.reload(config)
            assert 7075336557 in config.OWNER_IDS

    def test_owner_ids_from_env(self):
        """OWNER_IDS should be parsed from comma-separated env var."""
        with mock.patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "111,222,333"}):
            import importlib

            from bot.app.telegram_bot import config

            importlib.reload(config)
            assert {111, 222, 333} == config.OWNER_IDS

    def test_backend_url_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            import importlib

            from bot.app.telegram_bot import config

            importlib.reload(config)
            assert config.BACKEND_URL == "http://localhost:8000"

    def test_backend_url_from_env(self):
        with mock.patch.dict(
            os.environ, {"RMI_BACKEND_URL": "https://api.rugmunch.io"}
        ):
            import importlib

            from bot.app.telegram_bot import config

            importlib.reload(config)
            assert config.BACKEND_URL == "https://api.rugmunch.io"
            assert config.DATABUS_URL == "https://api.rugmunch.io/api/v1/databus"

    def test_rmi_auth_token_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            import importlib

            from bot.app.telegram_bot import config

            importlib.reload(config)
            assert config.RMI_AUTH_TOKEN == ""

    def test_rmi_auth_token_from_env(self):
        with mock.patch.dict(os.environ, {"RMI_AUTH_TOKEN": "super_secret_key"}):
            import importlib

            from bot.app.telegram_bot import config

            importlib.reload(config)
            assert config.RMI_AUTH_TOKEN == "super_secret_key"


class TestTIERSValidation:
    """Validate the TIERS configuration data is well-formed."""

    def test_all_tiers_have_required_keys(self):
        config = _import_config()
        for tier_name, tier in config.TIERS.items():
            assert "name" in tier, f"{tier_name} missing name"
            assert "emoji" in tier, f"{tier_name} missing emoji"
            assert "scans_per_week" in tier, f"{tier_name} missing scans_per_week"
            assert "ai_msgs_per_week" in tier, f"{tier_name} missing ai_msgs_per_week"
            assert "price_monthly" in tier, f"{tier_name} missing price_monthly"
            assert "features" in tier, f"{tier_name} missing features"

    def test_free_tier_has_zero_cost(self):
        config = _import_config()
        assert config.TIERS["free"]["price_monthly"] == 0

    def test_pro_tier_highest_limits(self):
        config = _import_config()
        assert (
            config.TIERS["pro"]["scans_per_week"]
            >= config.TIERS["hunter"]["scans_per_week"]
        )
        assert (
            config.TIERS["pro"]["ai_msgs_per_week"]
            >= config.TIERS["hunter"]["ai_msgs_per_week"]
        )

    def test_tier_prices_increase_with_tier(self):
        config = _import_config()
        prices = [
            config.TIERS[t]["price_monthly"] for t in ["free", "scout", "hunter", "pro"]
        ]
        for i in range(1, len(prices)):
            assert prices[i] > prices[i - 1], (
                f"Price not increasing at index {i}: {prices}"
            )

    def test_tier_features_are_lists(self):
        config = _import_config()
        for tier_name, tier in config.TIERS.items():
            assert isinstance(tier["features"], list), (
                f"{tier_name} features must be list"
            )

    def test_all_top_up_packs_have_required_fields(self):
        config = _import_config()
        for pack_name, pack in config.TOP_UP_PACKS.items():
            assert "name" in pack, f"{pack_name} missing name"
            assert "type" in pack, f"{pack_name} missing type"
            assert "amount" in pack, f"{pack_name} missing amount"
            assert "stars" in pack, f"{pack_name} missing stars"
            assert pack["type"] in ("scans", "ai_msgs"), f"{pack_name} invalid type"

    def test_top_up_pack_stars_cost_sensible(self):
        """Verify star costs are positive integers."""
        config = _import_config()
        for pack_name, pack in config.TOP_UP_PACKS.items():
            assert isinstance(pack["stars"], int), f"{pack_name} stars not int"
            assert pack["stars"] > 0, f"{pack_name} stars must be positive"

    def test_scan_pack_stars_positive(self):
        config = _import_config()
        for pack_name, pack in config.SCAN_PACKS.items():
            assert isinstance(pack["stars"], int), f"{pack_name} stars not int"
            assert pack["stars"] > 0, f"{pack_name} stars must be positive"
            assert "name" in pack, f"{pack_name} missing name"

    def test_dex_ref_links_have_urls(self):
        config = _import_config()
        for name, link in config.DEX_REF_LINKS.items():
            assert "url" in link, f"{name} missing url"
            assert "chains" in link, f"{name} missing chains"
            assert isinstance(link["chains"], list), f"{name} chains must be list"

    def test_social_links_have_keys(self):
        config = _import_config()
        for name, link in config.SOCIAL_LINKS.items():
            assert "url" in link, f"{name} missing url"
            assert "emoji" in link, f"{name} missing emoji"


class TestFormattingHelpers:
    def test_risk_bar_safe(self):
        config = _import_config()
        result = config.risk_bar(10, length=5)
        assert "10%" in result

    def test_risk_bar_critical(self):
        config = _import_config()
        result = config.risk_bar(90, length=5)
        assert "90%" in result
        assert "DANGER" in result

    def test_risk_bar_boundaries(self):
        config = _import_config()
        r1 = config.risk_bar(0, length=10)
        assert "SAFE" in r1
        r2 = config.risk_bar(100, length=10)
        assert "DANGER" in r2

    def test_threat_indicator_found(self):
        config = _import_config()
        assert "Honeypot" in config.threat_indicator("Honeypot", True)

    def test_threat_indicator_not_found(self):
        config = _import_config()
        assert "Renounced" in config.threat_indicator("Renounced", False)

    def test_fmt_number_large(self):
        config = _import_config()
        assert "1.50M" in config.fmt_number(1_500_000)

    def test_fmt_number_billion(self):
        config = _import_config()
        assert "2.50B" in config.fmt_number(2_500_000_000)

    def test_fmt_number_small(self):
        config = _import_config()
        result = config.fmt_number(0.001234)
        assert result.startswith("$")

    def test_fmt_number_none(self):
        config = _import_config()
        assert config.fmt_number(None) == "N/A"

    def test_fmt_pct_normal(self):
        config = _import_config()
        assert config.fmt_pct(15.5) == "15.50%"

    def test_fmt_pct_none(self):
        config = _import_config()
        assert config.fmt_pct(None) == "N/A"

    def test_fmt_chain_solana(self):
        config = _import_config()
        result = config.fmt_chain("solana")
        assert "Solana" in result

    def test_fmt_chain_unknown(self):
        config = _import_config()
        result = config.fmt_chain("unknown_chain")
        assert (
            "Unknown_chain" in result
            or "Unknown chain" in result
            or "Unknown" in result
        )
