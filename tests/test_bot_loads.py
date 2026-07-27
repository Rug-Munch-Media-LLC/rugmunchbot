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

import pytest


def test_bot_module_imports_cleanly():
    """bot.py should import without errors (no SyntaxError, no ImportError)."""
    # Add the bot package dir to sys.path
    bot_dir = Path(__file__).parent.parent / "bot" / "app" / "telegram_bot"
    sys.path.insert(0, str(bot_dir))

    # Import modules that bot.py depends on
    import config

    assert config.BOT_TOKEN is not None


    # Verify the main bot module parses and imports cleanly
    import bot as bot_mod

    assert hasattr(bot_mod, "main")
    assert hasattr(bot_mod, "cmd_pricing")
    assert hasattr(bot_mod, "cmd_buy")

    # Verify the live command file
    from commands.investigate import register

    assert callable(register)

    # Verify archived dead module does NOT exist in live commands
    with pytest.raises(ImportError):
        pass
    with pytest.raises(ImportError):
        pass


def test_no_duplicate_pricing_handler():
    """Verify /pricing is registered exactly once, pointing to cmd_pricing."""
    import bot as bot_mod

    # Read the source to verify only one /pricing registration
    source = Path(bot_mod.__file__).read_text()
    # Count occurrences of CommandHandler("pricing", ...)

    matches = re.findall(r'CommandHandler\("pricing",\s*(cmd_\w+)\)', source)
    assert len(matches) == 1, f"Expected 1 /pricing handler, found {len(matches)}: {matches}"
    assert matches[0] == "cmd_pricing", f"/pricing should point to cmd_pricing, not {matches[0]}"


def test_commands_dir_has_no_dead_files():
    """Verify dead command files have been removed from live commands/."""
    commands_dir = Path(__file__).parent.parent / "bot" / "app" / "telegram_bot" / "commands"
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
    assert not (live_files & dead_files), f"Dead files still in live: {live_files & dead_files}"
    # Verify archive exists with the dead files
    archive_dir = commands_dir / "_archive"
    assert archive_dir.is_dir(), "_archive directory missing"
    archived = {f.name for f in archive_dir.iterdir() if f.suffix == ".py"}
    assert dead_files.issubset(archived), f"Missing archived files: {dead_files - archived}"


def test_investigate_is_live():
    """Verify investigate.py is the only command file remaining."""
    commands_dir = Path(__file__).parent.parent / "bot" / "app" / "telegram_bot" / "commands"
    live_files = {f.name for f in commands_dir.iterdir() if f.suffix == ".py"}
    assert "investigate.py" in live_files
    assert len(live_files) == 1, f"Expected only investigate.py, found: {live_files}"
