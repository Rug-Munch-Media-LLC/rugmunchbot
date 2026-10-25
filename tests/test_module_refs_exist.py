"""Every ``rugmunchbot`` module a test imports must exist.

A lane once deleted ``premium/watch_cycle.py`` while its tests stayed behind;
collection failed with ``ModuleNotFoundError`` and nothing named the culprit.
This guard reads the test tree, resolves every ``rugmunchbot`` module a test
references, and fails listing the missing ones — one file, no network, no
import side effects for the modules themselves (``find_spec`` only locates).
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

TESTS_ROOT = Path(__file__).parent


def _modules_in(path: Path) -> set[str]:
    """Every ``rugmunchbot`` module path this test file references."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("rugmunchbot"))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if not node.module.startswith("rugmunchbot"):
                continue
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names if a.name != "*")
    return found


def _exists(module: str) -> bool:
    """A module is present when it has a spec, or its parent exposes the name.

    ``from pkg import name`` may name a submodule *or* an attribute re-exported
    by ``__init__``; both are legitimate, a missing one is not.
    """
    try:
        if importlib.util.find_spec(module) is not None:
            return True
    except ModuleNotFoundError:
        pass  # parent is a plain module, not a package: fall through to the attr check
    parent, _, attr = module.rpartition(".")
    if not parent:
        return False
    try:
        return hasattr(importlib.import_module(parent), attr)
    except ModuleNotFoundError:
        return False


def test_every_imported_rugmunchbot_module_exists() -> None:
    referenced: set[str] = set()
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        referenced |= _modules_in(path)

    missing = sorted(module for module in referenced if not _exists(module))

    assert not missing, "tests import modules that do not exist: " + ", ".join(missing)
