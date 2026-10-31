"""Lazy package __init__ — cheap import for backend consumers, heavy attrs lazy."""

import importlib
import os
import subprocess
import sys

import pytest

#: The heavy stack the lazy ``__init__`` must NOT drag in at package import.
HEAVY_MODULES = (
    "telegram",
    "phonenumbers",
    "rugmunchbot.bot",
    "rugmunchbot.formatters",
    "rugmunchbot.scanner",
)


def _package_root() -> str:
    """Directory that must be on PYTHONPATH for a fresh ``import rugmunchbot``."""
    pkg = importlib.import_module("rugmunchbot")
    return os.path.dirname(os.path.dirname(os.path.abspath(pkg.__file__)))


def _modules_after_light_import() -> set[str]:
    """Import rugmunchbot in a FRESH interpreter and return its ``sys.modules``.

    A subprocess is used so the verdict cannot depend on what other tests in
    this worker already imported. That is what makes the assertion
    deterministic — the previous ``elapsed < 5.0`` bound measured CI load, not
    the package: a loaded runner can exceed any import-time threshold.
    """
    prog = (
        "import importlib, sys;"
        "importlib.import_module('rugmunchbot');"
        "print('\\n'.join(sys.modules))"
    )
    env = {**os.environ, "PYTHONPATH": _package_root()}
    done = subprocess.run(  # noqa: S603 - fixed argv, sys.executable + literal program
        [sys.executable, "-c", prog], capture_output=True, text=True, check=True, env=env
    )
    return set(done.stdout.split())


def test_light_import_is_cheap():
    """Importing rugmunchbot must NOT drag the full bot (telegram/osint deps)."""
    loaded = _modules_after_light_import()
    dragged = sorted(
        mod
        for mod in loaded
        if any(mod == heavy or mod.startswith(f"{heavy}.") for heavy in HEAVY_MODULES)
    )
    assert dragged == [], f"package import dragged heavy modules: {dragged}"
    mod = importlib.import_module("rugmunchbot")
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
