"""Smoke test: verify bot module imports without errors.

This test ensures all critical P0 bugs are fixed:
  1. No duplicate handlers (double /pricing)
  2. No SyntaxError in imports (fundflow.py removed from live code)
  3. No broken ParseMode imports (voice.py removed from live code)
  4. No broken import paths (cmd_buy SUBSCRIPTION_PLANS replaced inline)
"""

import re
import sys
from pathlib import Path


def _add_bot_to_path():
    """Add the bot package dir to sys.path and purge cached 'config'.

    The config namespace package can shadow
    the bot's config.py when running tests in batch. We must clear the
    import cache and put the bot dir first so from config import ...
    resolves to the correct module.
    """
    import importlib

    bot_dir = Path(__file__).parent.parent / "bot" / "app" / "telegram_bot"
    if str(bot_dir) not in sys.path:
        sys.path.insert(0, str(bot_dir))

    # Purge cached 'config' module so bot.py's from config import
    # picks up the real config.py from the bot dir.
    if "config" in sys.modules:
        del sys.modules["config"]
    importlib.invalidate_caches()


def _load_bot_module():
    """Load bot.py directly to avoid namespace collision with bot/ directory."""
    import importlib.util as iu

    bot_path = str(
        Path(__file__).parent.parent / "bot" / "app" / "telegram_bot" / "bot.py"
    )
    spec = iu.spec_from_file_location("bot_module", bot_path)
    mod = iu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bot_module_imports_cleanly():
    """bot.py should import without errors (no SyntaxError, no ImportError)."""
    _add_bot_to_path()

    # Import modules that bot.py depends on
    import config

    assert config.BOT_TOKEN is not None

    # Verify the main bot module parses and imports cleanly
    bot_mod = _load_bot_module()

    assert hasattr(bot_mod, "main")

    # Verify the live command file
    from commands.investigate import register

    assert callable(register)


def test_no_duplicate_pricing_handler():
    """Verify /pricing is registered exactly once, pointing to cmd_pricing."""
    bot_path = Path(__file__).parent.parent / "bot" / "app" / "telegram_bot" / "bot.py"
    if bot_path.exists():
        source = bot_path.read_text()
        matches = re.findall(r'CommandHandler\("pricing",\s*(cmd_\w+)\)', source)
        assert len(matches) == 1, (
            f"Expected 1 /pricing handler, found {len(matches)}: {matches}"
        )
        assert matches[0] == "cmd_pricing", (
            f"/pricing should point to cmd_pricing, not {matches[0]}"
        )


def test_commands_dir_has_no_dead_files():
    """Verify dead command files have been removed from live commands/."""
    commands_dir = (
        Path(__file__).parent.parent / "bot" / "app" / "telegram_bot" / "commands"
    )
    live_files = {f.name for f in commands_dir.iterdir() if f.suffix == ".py"}
    dead_files = {
        "fundflow.py",
        "voice.py",
        "win_card.py",
        "aichart.py",
        "chart.py",
        "history.py",
        "history_risk.py",
        "scan.py",
        "scan_v2.py",
        "simulate.py",
        "smartmoney.py",
        "specialists.py",
        "trending.py",
        "rugmap.py",
        "ai.py",
        "portfolio.py",
        "watch.py",
    }
    # Verify none of the dead files are in live commands/
    assert not (live_files & dead_files), (
        f"Dead files still in live: {live_files & dead_files}"
    )
    # Archive directory is optional — dead files may be removed entirely
    archive_dir = commands_dir / "_archive"
    if archive_dir.is_dir():
        archived = {f.name for f in archive_dir.iterdir() if f.suffix == ".py"}
        assert dead_files.issubset(archived), (
            f"Missing archived files: {dead_files - archived}"
        )


def test_investigate_is_live():
    """Verify investigate.py is the only command file remaining."""
    commands_dir = (
        Path(__file__).parent.parent / "bot" / "app" / "telegram_bot" / "commands"
    )
    live_files = {f.name for f in commands_dir.iterdir() if f.suffix == ".py"}
    assert "investigate.py" in live_files
    assert len(live_files) == 1, f"Expected only investigate.py, found: {live_files}"
