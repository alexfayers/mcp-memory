"""Helpers for reading JSONC-like files used by local tool configs."""

from __future__ import annotations

import json
import re
from pathlib import Path


def _strip_jsonc_comments(text: str) -> str:
    """Remove // and /* */ comments from JSONC text while preserving strings."""
    out: list[str] = []
    in_string = False
    in_line_comment = False
    in_block_comment = False
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                out.append(ch)
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _remove_trailing_commas(text: str) -> str:
    """Remove trailing commas before object/array close tokens."""
    return re.sub(r",\s*([}\]])", r"\1", text)


def load_jsonc_object(path: Path) -> dict[str, object]:
    """Load JSON or JSONC from disk and return a mapping."""
    raw = path.read_text(encoding="utf-8")
    cleaned = _remove_trailing_commas(_strip_jsonc_comments(raw))
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        msg = f"error: {path} must contain a top-level object"
        raise ValueError(msg)
    return data
