"""Storage layer for the MCP memory server.

Modules are grouped by layer, and imports only ever point down: `pure/` holds helpers with no
connection, `connection.py` owns the sqlite handle and its ports, `services/` are connection-taking
helpers shared by the repositories, `repositories/` hold one subject each and never import one
another, `operations/` compose several subjects, and `bootstrap.py` wires it all together.

`Storage`, `open_writable`, `open_readonly`, `GraphResult`, `NodeList` and `ImportCounts` are
this package's entire public surface. Everything else is deep-imported by whoever needs it.
"""

from __future__ import annotations

from .bootstrap import Storage, open_readonly, open_writable
from .operations.reads import GraphResult, NodeList
from .operations.transfer import ImportCounts

__all__ = [
    "GraphResult",
    "ImportCounts",
    "NodeList",
    "Storage",
    "open_readonly",
    "open_writable",
]
