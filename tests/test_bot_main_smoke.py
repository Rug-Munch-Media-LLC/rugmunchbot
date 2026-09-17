"""Import smoke for the ``python -m rugmunchbot.bot`` entry point."""

import runpy


def test_main_module_imports_without_running():
    # run_name != "__main__" → the module body imports (covering the shim)
    # without launching the bot.
    runpy.run_module("rugmunchbot.bot.__main__", run_name="__smoke__")
