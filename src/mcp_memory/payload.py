"""Payload-size measurement as an explicit byte-count PROXY for token cost, not a real tokenizer.

Measures the serialized JSON size of MCP tool responses as a deterministic byte-count proxy
for token cost.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass


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
