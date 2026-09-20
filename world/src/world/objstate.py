"""Typed reads and writes for the JSON values kept in `WorldObject.state`.

`WorldObject.state` is a flat tuple of string pairs, because that is what the
proto, the recording and the viewer payload carry. Mechanics that need
structure (a chest's contents, a board's notes, a conversation's participants)
store compact JSON in one key. This module is the only place that encodes and
decodes it, so every mechanic fails the same way on a corrupt value.
"""

import json
from typing import Any, Mapping

from .exceptions import InvalidObjectStateError
from .state import WorldObject

__all__ = [
    "encode_json",
    "read_int",
    "read_json_list",
    "read_json_object",
    "with_updates",
]


def encode_json(value: Any) -> str:
    """Compact JSON for a value stored in one state key."""
    return json.dumps(value, separators=(",", ":"))


def _parse(obj: WorldObject, key: str, raw: str) -> Any:
    """Parse one JSON state value.

    Raises:
        InvalidObjectStateError: If the value is not valid JSON.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidObjectStateError(
            f"Object {obj.object_id} has unparsable {key}: {exc}"
        ) from exc


def read_json_list(
    obj: WorldObject, key: str, description: str, item_type: type = object
) -> list[Any]:
    """Read a JSON list from `key`, empty when the key is unset.

    `description` names the expected shape in the error message (for example
    "a list of entity ids"); `item_type` is what every element must be.

    Raises:
        InvalidObjectStateError: If the value is not a list of `item_type`.
    """
    raw = obj.get_state(key, "")
    if not raw:
        return []
    parsed = _parse(obj, key, raw)
    if not isinstance(parsed, list) or not all(
        isinstance(entry, item_type) for entry in parsed
    ):
        raise InvalidObjectStateError(
            f"Object {obj.object_id} {key} is not {description}"
        )
    return list(parsed)


def read_json_object(obj: WorldObject, key: str, description: str) -> dict[str, Any]:
    """Read a JSON object from `key`, empty when the key is unset.

    Raises:
        InvalidObjectStateError: If the value is not a JSON object.
    """
    raw = obj.get_state(key, "")
    if not raw:
        return {}
    parsed = _parse(obj, key, raw)
    if not isinstance(parsed, dict):
        raise InvalidObjectStateError(
            f"Object {obj.object_id} {key} is not {description}"
        )
    return parsed


def read_int(obj: WorldObject, key: str) -> int:
    """Read an integer state value, 0 when unset.

    Raises:
        InvalidObjectStateError: If the stored value is not an integer.
    """
    raw = obj.get_state(key, "")
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError as exc:
        raise InvalidObjectStateError(
            f"Object {obj.object_id} {key} is not an integer: {raw!r}"
        ) from exc


def with_updates(obj: WorldObject, updates: Mapping[str, str]) -> WorldObject:
    """Return a copy of `obj` with several state keys replaced."""
    updated = obj
    for key, value in updates.items():
        updated = updated.with_state(key, value)
    return updated
