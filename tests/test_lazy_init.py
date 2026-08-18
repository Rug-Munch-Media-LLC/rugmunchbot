"""Lazy package __init__ — cheap import for backend consumers, heavy attrs lazy."""

import importlib
import time

import pytest


def test_light_import_is_cheap():
    """Importing rugmunchbot must NOT drag the full bot (telegram/osint deps)."""

    t0 = time.monotonic()
    mod = importlib.import_module("rugmunchbot")
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0  # generous CI bound; heavy import would take much longer
    assert mod.db is not None
    assert mod.config is not None


def test_unknown_attribute_raises():
    mod = importlib.import_module("rugmunchbot")
    with pytest.raises(AttributeError):
        _ = mod.definitely_not_a_symbol


def test_heavy_reexport_lazy_loads():
    """accessing main via __getattr__ resolves the real bot entry (heavy but works)."""
    mod = importlib.import_module("rugmunchbot")
    main = mod.main
    assert callable(main)
