"""Per-context-window dedupe of observations returned by memory reads."""

from typing import Any

from .config import get_dedupe_turns

_MAX_WINDOWS = 256
SEEN_NOTE = "seen: hashes of observations returned earlier in this context. Pass include_seen=true for full text."
_windows: dict[str, dict[str, tuple[int, str | None]]] = {}


def _dedupe_entity(entity: dict[str, Any], sent: dict[str, tuple[int, str | None]], turn: int, ttl: int) -> bool:
    """Replace the entity's observations sent within ttl turns by their hashes, recording the rest as sent.

    Each hash keeps the provenance last sent, so a provenance change is sent again in full.
    """
    observations = entity["observations"]
    keys = [(o.get("content_hash"), o.get("from")) for o in observations]
    seen = {h for h, src in keys if h in sent and sent[h][1] == src and turn - sent[h][0] < ttl}
    sent.update({h: (turn, src) for h, src in keys if h and h not in seen})
    if not seen:
        return False
    kept = [o for o, (h, _) in zip(observations, keys, strict=True) if h not in seen]
    entity.update(seen=[h for h, _ in keys if h in seen], observations=kept)
    if not kept:
        del entity["observations"]
    return True


def dedupe_read(
    result: dict[str, Any],
    window_id: str | None,
    turn: int | None,
    *,
    include_seen: bool = False,
    full_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Send observations already returned in this window as hashes in `seen`, except under `full_keys`."""
    ttl = get_dedupe_turns()
    if window_id is None or turn is None or "error" in result or ttl <= 0:
        return result
    ttl = 0 if include_seen else ttl
    sent = _windows[window_id] = _windows.pop(window_id, {})
    if len(_windows) > _MAX_WINDOWS:
        del _windows[next(iter(_windows))]

    def walk(node: Any, key: str = "") -> int:
        if isinstance(node, list):
            return sum(walk(item, key) for item in node)
        if not isinstance(node, dict):
            return 0
        own = _dedupe_entity(node, sent, turn, 0 if key in full_keys else ttl) if "observations" in node else False
        return own + sum(walk(value, name) for name, value in node.items())

    if walk(result):
        result["seen_note"] = SEEN_NOTE
    return result
