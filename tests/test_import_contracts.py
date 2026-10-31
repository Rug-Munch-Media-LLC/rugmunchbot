"""Layer contracts that hold today, checked against the AST (not just at import).

``lint-imports`` (pyproject ``[tool.importlinter]``) is the CI gate; this test is
the fast local twin and it also sees **function-level** imports, which is exactly
how the last boundary break got in (``premium.service`` imported
``tools.scanner`` inside a coroutine — invisible to a module-level grep and to a
top-of-file review).

The checker is proven to fail on a deliberate violation, so a green run means
something.
"""

from __future__ import annotations

import ast
import pathlib

import rugmunchbot

PACKAGE = pathlib.Path(rugmunchbot.__file__).parent

RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # premium is a domain layer: it talks to ports, never to the bot, the
    # presentation packages, or the integration/scanner packages.
    (
        "premium",
        (
            "rugmunchbot.handlers",
            "rugmunchbot.bot",
            "rugmunchbot.commands",
            "rugmunchbot.tools",
            "rugmunchbot.channels",
        ),
    ),
    # the command registry is data: importable before Telegram exists.
    (
        "command_registry",
        ("telegram", "rugmunchbot.handlers", "rugmunchbot.bot", "rugmunchbot.commands"),
    ),
)


def imported_modules(tree: ast.AST) -> list[str]:
    """Every module named by an import, at any depth (lazy imports included)."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.append(node.module)
    return found


def violations(source: str, forbidden: tuple[str, ...]) -> list[str]:
    """Modules in *source* that match *forbidden* (exact package or submodule)."""
    bad: list[str] = []
    for module in imported_modules(ast.parse(source)):
        for rule in forbidden:
            if module == rule or module.startswith(rule + "."):
                bad.append(module)
    return sorted(set(bad))


def test_checker_catches_a_module_level_violation() -> None:
    src = "from rugmunchbot.handlers.alerts import cmd_alerts\n"
    assert violations(src, ("rugmunchbot.handlers",)) == ["rugmunchbot.handlers.alerts"]


def test_checker_catches_a_lazy_in_function_violation() -> None:
    src = "def f():\n    from rugmunchbot.tools.scanner import scan_token_v2\n"
    assert violations(src, ("rugmunchbot.tools",)) == ["rugmunchbot.tools.scanner"]


def test_checker_passes_a_clean_module() -> None:
    assert violations("from rugmunchbot.premium import scan_port\n", ("rugmunchbot.tools",)) == []


def test_checker_ignores_relative_imports() -> None:
    assert violations("from . import scan_port\n", ("rugmunchbot", "telegram")) == []


def files_for(root: str) -> list[pathlib.Path]:
    path = PACKAGE / root
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def scan(root: str, forbidden: tuple[str, ...]) -> dict[str, list[str]]:
    broken = {}
    for path in files_for(root):
        bad = violations(path.read_text(), forbidden)
        if bad:
            broken[str(path.relative_to(PACKAGE))] = bad
    return broken


def test_premium_never_imports_presentation_bot_or_integration() -> None:
    forbidden = dict(RULES)["premium"]
    assert scan("premium", forbidden) == {}


def test_command_registry_is_data_only() -> None:
    forbidden = dict(RULES)["command_registry"]
    broken = {
        **scan("command_registry.py", forbidden),
        **scan("_command_registry_data.py", forbidden),
    }
    assert broken == {}
