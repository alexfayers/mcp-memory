"""Find the workspace files a shell command writes."""

from __future__ import annotations

import re
import shlex

_QUOTED = re.compile(r"""'[^']*'|"(?:\\.|[^"\\])*\"""")
_HEREDOC_BODY = re.compile(r"(<<-?\s*['\"]?(\w+)['\"]?[^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.DOTALL)
_REDIRECT = re.compile(r"(?<![-=<>])>>?\|?\s*(?![&(=]|$)(\S+)")
_TEE = re.compile(r"(?:^|[|;&(\n])\s*(?:sudo\s+)?tee\s+(?:-\S+\s+)*(\S+)")
_SED_IN_PLACE = re.compile(r"(?:^|[|;&(\n])\s*(?:sudo\s+)?(sed\s[^|;&\n]*)")
_COPY_MOVE = re.compile(r"(?:^|[|;&(\n])\s*(?:sudo\s+)?((?:cp|mv|install)\s[^|;&\n]*)")
_PYTHON_WRITE = re.compile(
    r"""open\(\s*['"]([^'"]+)['"]\s*,\s*['"][wax]|Path\(\s*['"]([^'"]+)['"]\s*\)\.write_(?:text|bytes)"""
)
_PYTHON_OPEN_VARIABLE = re.compile(r"""open\(\s*(?!['"])[^\n]*?,\s*['"][wax]""")
_PYTHON_PATH_WRITE = re.compile(r"\.write_(?:text|bytes)\(")
_PYTHON_LITERAL_PATH_WRITE = re.compile(r"""Path\(\s*['"][^'"]+['"]\s*\)\.write_(?:text|bytes)""")
_UNNAMED_TARGET = ""
_OUTSIDE_WORKSPACE_PREFIXES = ("/dev/", "~")


def _unquote(command: str) -> str:
    """Drop heredoc bodies and keep quoted text as inert word characters."""
    without_bodies = _HEREDOC_BODY.sub(r"\1", command)
    return _QUOTED.sub(lambda match: re.sub(r"[\s<>|;&]", "_", match.group(0)[1:-1]), without_bodies)


def _targets_workspace(target: str, workspace_roots: list[str]) -> bool:
    """Return True if a write target is relative or sits under a workspace root."""
    if target.startswith(_OUTSIDE_WORKSPACE_PREFIXES):
        return False
    if not target.startswith("/"):
        return True
    return any(target == root or target.startswith(root.rstrip("/") + "/") for root in workspace_roots)


def _sed_in_place_targets(command: str) -> list[str]:
    """Return the files `sed -i` edits in place."""
    targets: list[str] = []
    for match in _SED_IN_PLACE.finditer(command):
        try:
            words = shlex.split(match.group(1))[1:]
        except ValueError:
            continue
        flags = [word for word in words if word.startswith("-")]
        positional = [word for word in words if not word.startswith("-")]
        if any(flag.startswith("--in-place") or re.fullmatch(r"-[a-zA-Z]*i\S*", flag) for flag in flags):
            scripted = any(flag in {"-e", "-f"} for flag in flags)
            targets.extend(positional if scripted else positional[1:])
    return targets


def _copy_move_targets(command: str) -> list[str]:
    """Return the destinations of `cp`, `mv` and `install`."""
    targets: list[str] = []
    for match in _COPY_MOVE.finditer(command):
        try:
            words = shlex.split(match.group(1))[1:]
        except ValueError:
            continue
        positional: list[str] = []
        directory = ""
        for index, word in enumerate(words):
            if word in {"-t", "--target-directory"}:
                directory = words[index + 1] if index + 1 < len(words) else ""
            elif word.startswith("--target-directory="):
                directory = word.split("=", 1)[1]
            elif not word.startswith("-") and words[index - 1 : index] not in (["-t"], ["--target-directory"]):
                positional.append(word)
        targets.extend([directory] if directory else positional[1:][-1:])
    return targets


def shell_write_targets(command: str, workspace_roots: list[str]) -> list[str]:
    """Return the workspace files the shell command writes by redirect, `tee`, `sed -i` or a Python write.

    A Python write to a path held in a variable is an empty, unnamed target.
    """
    text = _unquote(command)
    targets = [match.group(1) for pattern in (_REDIRECT, _TEE) for match in pattern.finditer(text)]
    targets += _sed_in_place_targets(text)
    targets += _copy_move_targets(text)
    if "python" in command:
        targets += [group for match in _PYTHON_WRITE.finditer(command) for group in match.groups() if group]
        unnamed_writes = len(_PYTHON_OPEN_VARIABLE.findall(command)) + len(_PYTHON_PATH_WRITE.findall(command))
        targets += [_UNNAMED_TARGET] * (unnamed_writes - len(_PYTHON_LITERAL_PATH_WRITE.findall(command)))
    return [target for target in targets if _targets_workspace(target, workspace_roots)]
