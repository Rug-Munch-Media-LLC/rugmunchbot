"""Import smoke for the ``python -m rugmunchbot.bot`` entry point.

importlib (not runpy) so coverage instruments the module — the body is a
single import + a ``__main__`` guard that must NOT fire here.
"""

import importlib


def test_main_module_imports_without_running():
    mod = importlib.import_module("rugmunchbot.bot.__main__")
    assert mod is not None
    assert mod.__name__ == "rugmunchbot.bot.__main__"
