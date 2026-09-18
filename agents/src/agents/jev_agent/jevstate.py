"""The compact per-tick state handed to Jev.

Jev is fast, cheap, and literal, and it pays for every token, so the state is
deliberately small: a 17x17 ASCII map, the two dozen nearest objects, whoever is
in view, the actor's own last few ticks, and the planner's brief. The shape is
fixed by docs/05_jev_agents_design.md.
"""

from __future__ import annotations

from typing import Any, Mapping

from . import items
from .geometry import Coord, direction_name
from .options import TravelState
from .pathfinding import NO_PATH, next_step, path_length
from .worldmodel import EntityInfo, HeardUtterance, ObjectInfo, WorldModel

VIEW_RADIUS = 8
MAP_SIZE = VIEW_RADIUS * 2 + 1
NEARBY_LIMIT = 25
ENTITY_LIMIT = 8
HISTORY_LINES = 8

MAP_LEGEND = (
    ". walkable, # blocked or wall, ~ water, T tree, o rock, b bush, r reeds, "
    "y clay, v ore vein, C chest, B board, X workshop table, F furnace, "
    "A anvil, + door, z bed, f furniture, , road or floor, i item pile, "
    "@ self, P player, W wolf, ? unknown"
)

# Structure-layer glyphs. A tile shows its structure, not the road under it.
_OBJECT_GLYPHS: Mapping[str, str] = {
    items.TREE: "T",
    "rock_small": "o",
    "rock_medium": "o",
    "rock_large": "o",
    "boulder": "o",
    items.BUSH: "b",
    items.REEDS: "r",
    items.CLAY_DEPOSIT: "y",
    items.COPPER_VEIN: "v",
    items.IRON_VEIN: "v",
    "chest": "C",
    "message_board": "B",
    "item_pile": "i",
    items.WORKSHOP_TABLE: "X",
    items.FURNACE: "F",
    items.ANVIL: "A",
    items.WOOD_WALL: "#",
    items.STONE_WALL: "#",
    items.DOOR: "+",
    items.BED: "z",
    items.CHAIR: "f",
    items.TABLE: "f",
}

_GROUND_GLYPH = ","

_WATER_FLOORS = frozenset({"deep_water", "shallow_water"})


def render_map(model: WorldModel, size: int = MAP_SIZE) -> str:
    """A `size` x `size` ASCII picture of what the actor remembers around itself."""
    radius = size // 2
    centre_x, centre_y = model.position

    glyphs: dict[Coord, str] = {}
    # Ground first, so a bed on a floor still reads as a bed.
    for obj in model.objects.values():
        if obj.object_type in items.GROUND_LAYER_KINDS:
            glyphs[obj.position] = _GROUND_GLYPH
    for obj in model.objects.values():
        if obj.object_type in items.GROUND_LAYER_KINDS:
            continue
        glyphs[obj.position] = _OBJECT_GLYPHS.get(obj.object_type, "?")
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
    if obj.object_type in items.EXTRACTABLE_TYPES:
        entry["remaining"] = obj.remaining
        yields = items.EXTRACT_YIELD.get(obj.object_type, "")
        if yields:
            entry["yields"] = yields
    elif obj.object_type == "bush":
        entry["berry"] = obj.has_berry
    elif obj.object_type in ("chest", "item_pile"):
        contents = obj.contents()
        if contents:
            entry["contents"] = contents
    return entry


def _entity_entry(entity: EntityInfo, origin: Coord) -> dict[str, Any]:
    """One line of the `entities` list. `asleep` only appears when it is true."""
    entry: dict[str, Any] = {
        "id": entity.entity_id,
        "type": entity.entity_type,
        "dx": entity.position[0] - origin[0],
        "dy": entity.position[1] - origin[1],
        "health": f"{entity.health}/{entity.max_health}",
        "wielded": entity.wielded,
    }
    if entity.asleep:
        entry["asleep"] = True
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
            "name": model.entity_id,
            "position": [origin[0], origin[1]],
            "health": f"{self_info.health}/{self_info.max_health}",
            "hunger": f"{self_info.hunger}/{self_info.max_hunger}",
            "fatigue": (
                f"{self_info.fatigue}/{self_info.max_fatigue} "
                f"({self_info.fatigue_word})"
            ),
            "asleep": self_info.asleep,
            "wielded": self_info.wielded,
            "inventory": dict(self_info.inventory),
            # Station recipes only work here, so say so plainly.
            "at_workshop_table": model.station_near(items.WORKSHOP_TABLE) is not None,
            "at_furnace": model.station_near(items.FURNACE) is not None,
            "at_anvil": model.station_near(items.ANVIL) is not None,
        },
        "fatigue_facts": FATIGUE_FACTS,
        "clock": _clock_entry(model),
        "settlement": {"dx": settlement_dx, "dy": settlement_dy},
        "travel": _travel_entry(model, travel),
        "nearby": [
            _object_entry(obj, origin)
            for obj in model.objects_near(VIEW_RADIUS)[:NEARBY_LIMIT]
        ],
        "entities": [
            _entity_entry(entity, origin)
            for entity in model.entities_near(VIEW_RADIUS)[:ENTITY_LIMIT]
            if entity.alive
        ],
        "map": render_map(model),
        "map_legend": MAP_LEGEND,
        "recent": model.recent_history(HISTORY_LINES),
    }
    invitations = _invitation_lines(model)
    if invitations:
        state["invitations"] = invitations
    threat = _threat_entry(model)
    if threat:
        state["threat"] = threat
    heard = model.recent_utterances(3)
    if heard:
        state["heard"] = [_heard_line(u, origin, model.tick) for u in heard]
    if notes:
        state["notes"] = notes
    return state


# Physics only: what to do about a wolf is the planner's brief to decide.
WOLF_FACTS = (
    f"A wolf has {items.WOLF_HEALTH} health and bites an adjacent settler for "
    f"{items.WOLF_DAMAGE} every tick. Every settler attacking the same wolf "
    "hits it on the same tick."
)


# Physics only: when to sleep is the planner's brief to decide.
FATIGUE_FACTS = (
    f"Fatigue rises 1 every {items.FATIGUE_INTERVAL_DAY} ticks by day and every "
    f"{items.FATIGUE_INTERVAL_NIGHT} ticks at night. From {items.TIRED_FATIGUE} "
    "you are tired: the work a tool adds per extract action is halved and "
    f"your attacks hit for 1 less. At {items.MAX_FATIGUE} you collapse where you "
    f"stand and sleep until fatigue falls to {items.COLLAPSE_WAKE_FATIGUE}. "
    f"Sleeping on a bed recovers {items.sleep_recovery_text(True, True)} at "
    f"night and {items.sleep_recovery_text(True, False)} by day; on the ground "
    f"{items.sleep_recovery_text(False, True)} at night and "
    f"{items.sleep_recovery_text(False, False)} by day."
)

DAY_FACTS = (
    f"A day is {items.DEFAULT_DAY_LENGTH} ticks: the first two thirds are light "
    "and the rest is night."
)


def _clock_entry(model: WorldModel) -> dict[str, Any]:
    """The world clock: which day it is, how far into it, and whether it is night."""
    clock = model.clock
    return {
        "day": clock.day,
        "tick_of_day": f"{clock.tick_of_day}/{clock.day_length}",
        "night": clock.night,
        "facts": DAY_FACTS,
    }


def _invitation_lines(model: WorldModel) -> list[str]:
    """Who is open to talk, and whether this actor is (docs/09 section 8.3).

    One line per other settler with an open invitation, plus a line for the
    actor's own while it stands.
    """
    origin = model.position
    lines = [
        f"{invitation.entity_id} at dx {invitation.position[0] - origin[0]} "
        f"dy {invitation.position[1] - origin[1]} is open to talk: "
        f'"{invitation.text}"'
        for invitation in model.open_invitations()
    ]
    if model.my_invitation_live():
        lines.append(
            f"you are open to talk ({model.my_invitation_ticks_left()} ticks left)"
        )
    return lines


def _threat_entry(model: WorldModel) -> dict[str, Any]:
    """The wolves Jev can see and who is near them; empty while none is in view."""
    wolves = model.wolves_near(VIEW_RADIUS)
    if not wolves:
        return {}
    origin = model.position
    nearest = wolves[0]
    return {
        "wolves_in_view": len(wolves),
        "nearest_wolf": {
            "id": nearest.entity_id,
            "dx": nearest.position[0] - origin[0],
            "dy": nearest.position[1] - origin[1],
            "health": f"{nearest.health}/{nearest.max_health}",
            "settlers_next_to_it": len(model.allies_near(nearest.position, 1)),
        },
        "settlers_within_3_of_you": len(model.allies_near(origin, 3)),
        "facts": WOLF_FACTS,
    }


def _heard_line(utterance: HeardUtterance, origin: Coord, tick: int) -> str:
    """One heard line; a shout says where it came from so Jev can go there."""
    if utterance.channel != items.SHOUT_CHANNEL:
        return f"{utterance.speaker_id}: {utterance.text}"
    dx = utterance.position[0] - origin[0]
    dy = utterance.position[1] - origin[1]
    age = tick - utterance.tick
    return (
        f"{utterance.speaker_id} shouted from dx {dx} dy {dy}, {age} ticks ago: "
        f"{utterance.text}"
    )


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
