"""Payload-size measurement as an explicit byte-count PROXY for token cost, not a real tokenizer.

Measures the serialized JSON size of MCP tool responses as a deterministic byte-count proxy
for token cost.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json


def _json_default(obj: object) -> object:
    """Serialize dataclass instances to dicts for ``json.dumps``, else raise ``TypeError``."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    msg = f"Cannot serialize {type(obj).__name__} to JSON"
    raise TypeError(msg)


def compact_json(payload: object) -> str:
    """Serialize an MCP-tool return value to compact JSON, the form sent on the wire.

    Nested ``Entity``/``Relation`` dataclasses are handled structurally.
    """
    return json.dumps(payload, default=_json_default, ensure_ascii=False, separators=(",", ":"))


def payload_size(payload: object) -> int:
    """Return the UTF-8 byte size of an MCP-tool return value's compact JSON.

    This is the proxy for token cost. ``ensure_ascii=False`` counts multibyte characters at
    their real UTF-8 length.
    """
    return len(compact_json(payload).encode("utf-8"))
