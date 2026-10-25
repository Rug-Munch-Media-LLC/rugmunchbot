"""Extract bot-facing string *values* from Python source for the voice lint.

Parsing beats line-grepping: a line scan sees ``if a != b`` and comments, so a
banned ``!`` rule would drown in false pins. The AST gives us the literal
values the bot actually emits. Docstrings are skipped — they quote banned
phrases on purpose.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rugmunchbot.voice import banned_hits

REPO: Path = Path(__file__).resolve().parents[1]
#: Chrome surfaces scanned. Guides/prompts quote banned phrases deliberately.
SCAN_TARGETS: tuple[str, ...] = (
    "src/rugmunchbot/handlers",
    "src/rugmunchbot/channels/automation",
    "src/rugmunchbot/formatters",
)


def _docstrings(tree: ast.AST) -> set[int]:
    """Ids of constant nodes that are module/class/function docstrings."""
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                skipped.add(id(body[0].value))
    return skipped


def scan_file(path: Path) -> list[str]:
    """Return ``lineno:phrase`` hits for one file, sorted by line then phrase."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    skipped = _docstrings(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skipped:
                continue
            for phrase in banned_hits(node.value):
                hits.append(f"{node.lineno}:{phrase}")
    return sorted(set(hits))


def scan_all() -> dict[str, list[str]]:
    """Map repo-relative path -> hits, over every file in :data:`SCAN_TARGETS`."""
    found: dict[str, list[str]] = {}
    for rel in SCAN_TARGETS:
        for path in sorted((REPO / rel).rglob("*.py")):
            hits = scan_file(path)
            if hits:
                found[path.relative_to(REPO).as_posix()] = hits
    return found
