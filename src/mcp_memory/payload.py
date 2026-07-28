"""Payload-size measurement as an explicit byte-count PROXY for token cost, not a real tokenizer.

Measures the serialized JSON size of MCP tool responses so context economy (compact savings,
per-tool cost) can be compared deterministically.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .database import DatabaseManager


@dataclass(frozen=True)
class SavingsRatio:
    """Byte-size delta between a full and a compact search response.

    ``ratio`` is the fraction of the full payload saved by the compact form.
    """

    full_bytes: int
    compact_bytes: int
    saved_bytes: int
    ratio: float


@dataclass(frozen=True)
class ToolPayload:
    """Serialized byte size of a single MCP tool's return value."""

    tool: str
    bytes: int


def _json_default(obj: object) -> object:
    """Serialize dataclass instances to dicts for ``json.dumps``, else raise ``TypeError``."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    msg = f"Cannot serialize {type(obj).__name__} to JSON"
    raise TypeError(msg)


def payload_size(payload: object) -> int:
    """Serialize an MCP-tool return value to canonical JSON and return its UTF-8 byte size.

    This is the proxy for token cost. ``ensure_ascii=False`` counts multibyte characters at
    their real UTF-8 length, and ``sort_keys=True`` makes the size independent of dict
    insertion order. Nested ``Entity``/``Relation`` dataclasses are handled structurally.
    """
    encoded = json.dumps(payload, default=_json_default, ensure_ascii=False, sort_keys=True)
    return len(encoded.encode("utf-8"))


def compact_savings(
    db: DatabaseManager, project: str, query: str, *, limit: int = 10
) -> SavingsRatio:
    """Measure the payload delta between the full and compact ``search_nodes`` responses.

    Runs the same query against the same database with ``compact=False`` and ``compact=True``,
    measures the serialized size of each result, and returns the byte savings and the fraction
    of the full payload they represent.
    """
    full = db.search_nodes(project, query, limit=limit, compact=False)
    compact = db.search_nodes(project, query, limit=limit, compact=True)
    full_bytes = payload_size(full)
    compact_bytes = payload_size(compact)
    saved_bytes = full_bytes - compact_bytes
    return SavingsRatio(
        full_bytes=full_bytes,
        compact_bytes=compact_bytes,
        saved_bytes=saved_bytes,
        ratio=saved_bytes / full_bytes if full_bytes else 0.0,
    )


def per_tool_payloads(calls: Mapping[str, object]) -> list[ToolPayload]:
    """Measure the serialized payload size of each tool's already-returned value.

    Takes a mapping of tool name to that tool's return value and returns one ``ToolPayload``
    per entry, sorted by tool name for deterministic ordering.
    """
    return [
        ToolPayload(tool=tool, bytes=payload_size(value)) for tool, value in sorted(calls.items())
    ]
