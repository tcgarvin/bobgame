"""The compact per-tick state handed to Jev.

Jev is fast, cheap, and literal, and it pays for every token, so the state is
deliberately small: a 17x17 ASCII map, the two dozen nearest objects, whoever is
in view, the actor's own last few ticks, and the planner's brief. The shape is
fixed by docs/05_jev_agents_design.md.
"""

from __future__ import annotations

from typing import Any, Mapping

from .geometry import Coord, direction_name
from .options import TravelState
from .pathfinding import NO_PATH, next_step, path_length
from .worldmodel import ObjectInfo, WorldModel

VIEW_RADIUS = 8
MAP_SIZE = VIEW_RADIUS * 2 + 1
NEARBY_LIMIT = 25
ENTITY_LIMIT = 8
HISTORY_LINES = 8

MAP_LEGEND = (
    ". walkable, # blocked, ~ water, T tree, o rock, b bush, C chest, "
    "B board, i item pile, @ self, P player, W wolf, ? unknown"
)

_OBJECT_GLYPHS: Mapping[str, str] = {
    "tree": "T",
    "rock_small": "o",
    "rock_medium": "o",
    "rock_large": "o",
    "boulder": "o",
    "bush": "b",
    "chest": "C",
    "message_board": "B",
    "item_pile": "i",
}

_WATER_FLOORS = frozenset({"deep_water", "shallow_water"})


def render_map(model: WorldModel, size: int = MAP_SIZE) -> str:
    """A `size` x `size` ASCII picture of what the actor remembers around itself."""
    radius = size // 2
    centre_x, centre_y = model.position

    glyphs: dict[Coord, str] = {}
    for obj in model.objects.values():
        glyph = _OBJECT_GLYPHS.get(obj.object_type, "?")
        glyphs[obj.position] = glyph
    for entity in model.entities.values():
        if entity.entity_id == model.entity_id or not entity.alive:
            continue
        glyphs[entity.position] = "W" if entity.entity_type == "wolf" else "P"
    glyphs[(centre_x, centre_y)] = "@"

    rows: list[str] = []
    for dy in range(-radius, radius + 1):
        row: list[str] = []
        for dx in range(-radius, radius + 1):
            position = (centre_x + dx, centre_y + dy)
            marker = glyphs.get(position)
            if marker is not None:
                row.append(marker)
                continue
            tile = model.tiles.get(position)
            if tile is None:
                row.append("?")
            elif tile.floor_type in _WATER_FLOORS:
                row.append("~")
            elif tile.walkable:
                row.append(".")
            else:
                row.append("#")
        rows.append("".join(row))
    return "\n".join(rows)


def _object_entry(obj: ObjectInfo, origin: Coord) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": obj.object_id,
        "type": obj.object_type,
        "dx": obj.position[0] - origin[0],
        "dy": obj.position[1] - origin[1],
    }
    if obj.object_type in _OBJECT_GLYPHS and obj.object_type in (
        "tree",
        "rock_small",
        "rock_medium",
        "rock_large",
        "boulder",
    ):
        entry["remaining"] = obj.remaining
    elif obj.object_type == "bush":
        entry["berry"] = obj.has_berry
    elif obj.object_type in ("chest", "item_pile"):
        contents = obj.contents()
        if contents:
            entry["contents"] = contents
    return entry


def build_state(
    model: WorldModel,
    *,
    instruction: str,
    success_condition: str,
    ticks_left: int,
    notes: str = "",
    travel: TravelState | None = None,
) -> dict[str, Any]:
    """Assemble the JSON state object for one Jev request."""
    self_info = model.self_info
    origin = self_info.position
    settlement_dx, settlement_dy = model.settlement_offset()

    state: dict[str, Any] = {
        "brief": {
            "instruction": instruction,
            "success_condition": success_condition,
            "ticks_left": ticks_left,
        },
        "self": {
            "position": [origin[0], origin[1]],
            "health": f"{self_info.health}/{self_info.max_health}",
            "hunger": f"{self_info.hunger}/{self_info.max_hunger}",
            "wielded": self_info.wielded,
            "inventory": dict(self_info.inventory),
        },
        "settlement": {"dx": settlement_dx, "dy": settlement_dy},
        "travel": _travel_entry(model, travel),
        "nearby": [
            _object_entry(obj, origin)
            for obj in model.objects_near(VIEW_RADIUS)[:NEARBY_LIMIT]
        ],
        "entities": [
            {
                "id": entity.entity_id,
                "type": entity.entity_type,
                "dx": entity.position[0] - origin[0],
                "dy": entity.position[1] - origin[1],
                "health": f"{entity.health}/{entity.max_health}",
            }
            for entity in model.entities_near(VIEW_RADIUS)[:ENTITY_LIMIT]
            if entity.alive
        ],
        "map": render_map(model),
        "map_legend": MAP_LEGEND,
        "recent": model.recent_history(HISTORY_LINES),
    }
    heard = model.recent_utterances(3)
    if heard:
        state["heard"] = [f"{u.speaker_id}: {u.text}" for u in heard]
    if notes:
        state["notes"] = notes
    return state


def _travel_entry(model: WorldModel, travel: TravelState | None) -> Any:
    if travel is None:
        return None
    origin = model.position
    direction = next_step(
        model, origin, travel.target, stop_adjacent=travel.stop_adjacent
    )
    steps = path_length(
        model, origin, travel.target, stop_adjacent=travel.stop_adjacent
    )
    dx = travel.target[0] - origin[0]
    dy = travel.target[1] - origin[1]
    return {
        "target": f"{travel.label} at dx {dx} dy {dy}",
        "next_step": direction_name(direction) if direction else "blocked",
        "steps_left": None if steps == NO_PATH else steps,
    }
