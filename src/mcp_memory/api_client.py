"""HTTP client for the memory server's /api/call route, used by the hooks and the CLI."""

from __future__ import annotations

import json
from typing import Any
import urllib.error
import urllib.request

from .config import get_memory_url

TIMEOUT_SECONDS = 60.0


class ApiError(Exception):
    """The server could not be reached, or it refused the call."""


def call(op: str, args: dict[str, Any] | None = None, *, timeout: float = TIMEOUT_SECONDS) -> Any:
    """Run the server operation ``op`` with JSON ``args`` and return its decoded JSON result."""
    base = get_memory_url().rstrip("/").removesuffix("/mcp")
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
        f"{base}/api/call/{op}", data=json.dumps(args or {}).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # ruff: ignore[suspicious-url-open-usage]
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read())["error"]
        except (ValueError, KeyError, TypeError):
            message = str(exc.reason)
        raise ApiError(message) from exc
    except (OSError, ValueError) as exc:
        raise ApiError(f"mcp-memory server is not reachable at {base}") from exc
