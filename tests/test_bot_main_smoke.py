"""Import smoke for the ``python -m rugmunchbot.bot`` entry point.

importlib (not runpy) so coverage instruments the module — the body is a
single import + a ``__main__`` guard that must NOT fire here.
"""

import importlib

import pytest


# The entry chain pulls phonenumbers.geocoder (a ~5k-line data22 parse) —
# known-expensive under heavy host load; the suite-wide 120s floor is for
# logic tests, not a one-shot data-module parse. Budget, not a bypass.
@pytest.mark.timeout(600)
def test_main_module_imports_without_running():
    mod = importlib.import_module("rugmunchbot.bot.__main__")
    assert mod is not None
    assert mod.__name__ == "rugmunchbot.bot.__main__"
