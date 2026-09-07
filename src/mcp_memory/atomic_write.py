"""Atomic JSON writes, shared by the status marker files."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


def write_json_atomic(path: Path, data: object) -> None:
    """Write ``data`` as JSON to ``path`` atomically (temp file + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        tmp_path.replace(path)
    except OSError:
        tmp_path.unlink()
        raise
