"""Guard: only the server process and the database-file tools may open the database."""

from __future__ import annotations

import ast
from pathlib import Path

_SOURCE = Path(__file__).parent.parent / "src" / "mcp_memory"
_DB_OPENERS = ("storage/", "server.py", "eval.py", "relocate.py")
_OPENER_NAMES = {"open_readonly", "open_writable"}


def _opens_database(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        return any(
            alias.name in _OPENER_NAMES or (node.module == "sqlite3" and alias.name == "connect")
            for alias in node.names
        )
    return isinstance(node, ast.Attribute) and (
        node.attr in _OPENER_NAMES
        or (node.attr == "connect" and isinstance(node.value, ast.Name) and node.value.id == "sqlite3")
    )


def test_only_the_server_and_file_tools_open_the_database() -> None:
    offenders = [
        f"{path.relative_to(_SOURCE)}:{node.lineno}"
        for path in sorted(_SOURCE.rglob("*.py"))
        if not path.relative_to(_SOURCE).as_posix().startswith(_DB_OPENERS)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if _opens_database(node)
    ]
    assert not offenders, f"database opened outside {_DB_OPENERS}: {offenders}"
