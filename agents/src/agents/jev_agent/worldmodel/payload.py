"""The snapshot codec: every remembered thing as JSON-safe data and back.

Tiles are the bulk of a settled model (10^5 of them), so they go in parallel
arrays with the floor types interned rather than as one dict per tile. The
shapes here are the on-disk snapshot format (docs/14 section 3).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..geometry import Coord
from .types import EntityInfo, HeardUtterance, ObjectInfo, TileInfo, WorldClock


_TILE_WALKABLE_BIT = 1
_TILE_OPAQUE_BIT = 2


def tiles_payload(tiles: Mapping[Coord, TileInfo]) -> dict[str, Any]:
    """Tiles as parallel arrays with the floor types interned."""
    floors: list[str] = []
    floor_index: dict[str, int] = {}
    xs: list[int] = []
    ys: list[int] = []
    floor_of: list[int] = []
    flags: list[int] = []
    last_seen: list[int] = []
    for (x, y), tile in tiles.items():
        index = floor_index.get(tile.floor_type, -1)
        if index < 0:
            index = len(floors)
            floor_index[tile.floor_type] = index
            floors.append(tile.floor_type)
        xs.append(x)
        ys.append(y)
        floor_of.append(index)
        flags.append(
            (_TILE_WALKABLE_BIT if tile.walkable else 0)
            | (_TILE_OPAQUE_BIT if tile.opaque else 0)
        )
        last_seen.append(tile.last_seen)
    return {
        "floors": floors,
        "floor_of": floor_of,
        "x": xs,
        "y": ys,
        "flags": flags,
        "last_seen": last_seen,
    }


def tiles_from_payload(payload: Mapping[str, Any]) -> dict[Coord, TileInfo]:
    """The inverse of `_tiles_payload`."""
    floors = [str(name) for name in payload["floors"]]
    tiles: dict[Coord, TileInfo] = {}
    for x, y, floor, flag, seen in zip(
        payload["x"],
        payload["y"],
        payload["floor_of"],
        payload["flags"],
        payload["last_seen"],
    ):
        position = (int(x), int(y))
        tiles[position] = TileInfo(
            position=position,
            walkable=bool(int(flag) & _TILE_WALKABLE_BIT),
            opaque=bool(int(flag) & _TILE_OPAQUE_BIT),
            floor_type=floors[int(floor)],
            last_seen=int(seen),
        )
    return tiles


def object_payload(obj: ObjectInfo) -> dict[str, Any]:
    return {
        "object_id": obj.object_id,
        "object_type": obj.object_type,
        "position": list(obj.position),
        "state": dict(obj.state),
        "last_seen": obj.last_seen,
    }


def object_from_payload(payload: Mapping[str, Any]) -> ObjectInfo:
    position = payload["position"]
    return ObjectInfo(
        object_id=str(payload["object_id"]),
        object_type=str(payload["object_type"]),
        position=(int(position[0]), int(position[1])),
        state={str(key): str(value) for key, value in payload["state"].items()},
        last_seen=int(payload["last_seen"]),
    )


def entity_payload(info: EntityInfo) -> dict[str, Any]:
    return {
        "entity_id": info.entity_id,
        "entity_type": info.entity_type,
        "position": list(info.position),
        "health": info.health,
        "max_health": info.max_health,
        "food": info.food,
        "max_food": info.max_food,
        "wielded": info.wielded,
        "alive": info.alive,
        "inventory": dict(info.inventory),
        "last_seen": info.last_seen,
        "fatigue": info.fatigue,
        "max_fatigue": info.max_fatigue,
        "asleep": info.asleep,
        "sleeping_on": info.sleeping_on,
        "collapsed": info.collapsed,
    }


def entity_from_payload(payload: Mapping[str, Any]) -> EntityInfo:
    position = payload["position"]
    return EntityInfo(
        entity_id=str(payload["entity_id"]),
        entity_type=str(payload["entity_type"]),
        position=(int(position[0]), int(position[1])),
        health=int(payload["health"]),
        max_health=int(payload["max_health"]),
        food=int(payload["food"]),
        max_food=int(payload["max_food"]),
        wielded=str(payload["wielded"]),
        alive=bool(payload["alive"]),
        inventory={
            str(kind): int(count) for kind, count in payload["inventory"].items()
        },
        last_seen=int(payload["last_seen"]),
        fatigue=int(payload["fatigue"]),
        max_fatigue=int(payload["max_fatigue"]),
        asleep=bool(payload["asleep"]),
        sleeping_on=str(payload["sleeping_on"]),
        collapsed=bool(payload["collapsed"]),
    )


def clock_payload(clock: WorldClock) -> dict[str, Any]:
    return {
        "day": clock.day,
        "tick_of_day": clock.tick_of_day,
        "day_length": clock.day_length,
        "night": clock.night,
        "new_moon_tonight": clock.new_moon_tonight,
        "next_new_moon_day": clock.next_new_moon_day,
        # Deliberately not carried: the save tick belongs to the tick the
        # snapshot was taken on, and the resumed world re-delivers it as 0.
    }


def clock_from_payload(payload: Mapping[str, Any]) -> WorldClock:
    return WorldClock(
        day=int(payload["day"]),
        tick_of_day=int(payload["tick_of_day"]),
        day_length=int(payload["day_length"]),
        night=bool(payload["night"]),
        new_moon_tonight=bool(payload["new_moon_tonight"]),
        next_new_moon_day=int(payload["next_new_moon_day"]),
    )


def heard_payload(heard: HeardUtterance) -> list[Any]:
    return [
        heard.tick,
        heard.speaker_id,
        heard.channel,
        heard.text,
        list(heard.position),
        heard.conversation_id,
    ]


def heard_from_payload(row: Sequence[Any]) -> HeardUtterance:
    tick, speaker, channel, text, position, conversation_id = row
    return HeardUtterance(
        tick=int(tick),
        speaker_id=str(speaker),
        channel=str(channel),
        text=str(text),
        position=(int(position[0]), int(position[1])),
        conversation_id=str(conversation_id),
    )


def board_payload(boards: Mapping[str, Mapping[int, int]]) -> dict[str, Any]:
    """Board read-tracking, with the integer slots as JSON's string keys."""
    return {
        board_id: {str(slot): tick for slot, tick in slots.items()}
        for board_id, slots in boards.items()
    }


def board_from_payload(payload: Mapping[str, Any]) -> dict[str, dict[int, int]]:
    return {
        str(board_id): {int(slot): int(tick) for slot, tick in slots.items()}
        for board_id, slots in payload.items()
    }
