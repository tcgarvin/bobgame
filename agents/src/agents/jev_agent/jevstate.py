"""The compact per-tick state handed to Jev.

Jev is fast, cheap, and literal, and it pays for every token, so the state is
deliberately small: a 17x17 ASCII map, the two dozen nearest objects, whoever is
in view, the actor's own last few ticks, and the planner's brief. The shape is
fixed by docs/05_jev_agents_design.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Collection, Mapping, Sequence

from . import items
from .enclosure import enclosed_fact
from .geometry import Coord, chebyshev, direction_name
from .briefs import EMPTY_PLACES, BriefHail, TravelState
from .pathfinding import NO_PATH, next_step, path_length
from .worldmodel import EntityInfo, HeardUtterance, ObjectInfo, WorldModel

VIEW_RADIUS = items.VIEW_RADIUS
MAP_SIZE = VIEW_RADIUS * 2 + 1
NEARBY_LIMIT = 25
ENTITY_LIMIT = 8
HISTORY_LINES = 8

MAP_LEGEND = (
    ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a "
    "berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, "
    "M message board, S sign, X workshop table, F furnace, A anvil, + door, "
    "z bed, "
    "f furniture, , road or floor, i item pile, @ self, P player, W wolf, "
    "? unknown"
)

# Structure-layer glyphs. A tile shows its structure, not the road under it.
_OBJECT_GLYPHS: Mapping[str, str] = {
    items.TREE: "T",
    "rock_small": "o",
    "rock_medium": "o",
    "rock_large": "o",
    "boulder": "o",
    items.BUSH: "b",  # overridden to "B" when the bush carries a berry
    items.REEDS: "r",
    items.CLAY_DEPOSIT: "y",
    items.COPPER_VEIN: "v",
    items.IRON_VEIN: "v",
    "chest": "C",
    "message_board": "M",
    items.SIGN: "S",
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
        if obj.object_type == items.BUSH:
            # The one glyph Jev acts on directly, so berries get their own.
            glyphs[obj.position] = "B" if obj.has_berry else "b"
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
    elif obj.object_type == items.SIGN:
        # The whole point of a sign is its line, so it rides in the state.
        entry["text"] = obj.sign_text
        entry["written_by"] = obj.sign_author
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


@dataclass(frozen=True)
class StintProgress:
    """What the stint has done so far, measured by code rather than by Jev.

    Jev has no memory between ticks, so without this it cannot tell a stint on
    its first tick from one that has been chopping the same tree for forty.
    """

    ticks_used: int = 0
    ticks_left: int = 0
    inventory_change: Mapping[str, int] = field(default_factory=dict)
    actions: Mapping[str, int] = field(default_factory=dict)
    moved_from_start: Coord = (0, 0)
    net_tiles_moved: int = 0

    def as_entry(self) -> dict[str, Any]:
        """The `so_far` block of the state."""
        dx, dy = self.moved_from_start
        return {
            "ticks_used": self.ticks_used,
            "ticks_left": self.ticks_left,
            "inventory_change": dict(self.inventory_change),
            "actions": dict(self.actions),
            "moved_from_start": f"dx {dx} dy {dy}",
            "net_tiles_moved": self.net_tiles_moved,
        }


# The stint has not started yet: every counter is zero.
NO_PROGRESS = StintProgress()


def collapse_action(key: str) -> str:
    """The name an option key is counted under in `so_far.actions`.

    The eight compass moves and every walk target are one thing each as far as
    "what have I been doing" goes.
    """
    if key.startswith("move_"):
        return "move"
    head, _, _ = key.partition(":")
    return head


def build_state(
    model: WorldModel,
    *,
    instruction: str,
    success_condition: str,
    progress: StintProgress = NO_PROGRESS,
    notes: str = "",
    travel: TravelState | None = None,
    places: Mapping[str, Coord] = EMPTY_PLACES,
    hails: Sequence[BriefHail] = (),
    highlight_ids: Collection[str] = (),
) -> dict[str, Any]:
    """Assemble the JSON state object for one Jev request.

    `places` are the brief's named destinations, shown relative so Jev never
    reasons about absolute coordinates. `hails` are the settlers the brief lets
    Jev address and the line to say to each. `highlight_ids` are the objects the
    brief names or offers a walk to, which stay in `nearby` however far off.
    """
    self_info = model.self_info
    origin = self_info.position
    settlement_dx, settlement_dy = model.settlement_offset()

    brief: dict[str, Any] = {
        "instruction": instruction,
        "success_condition": success_condition,
    }
    if hails:
        brief["hails"] = [f'say to {h.settler}: "{h.line}"' for h in hails]
    if places:
        brief["places"] = {
            name: f"dx {target[0] - origin[0]} dy {target[1] - origin[1]}"
            for name, target in places.items()
        }

    state: dict[str, Any] = {
        "brief": brief,
        "self": {
            "name": model.entity_id,
            "position": [origin[0], origin[1]],
            "health": f"{self_info.health}/{self_info.max_health}",
            "food": f"{self_info.food}/{self_info.max_food}",
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
        "so_far": progress.as_entry(),
        "facts": _facts(model),
        "clock": _clock_entry(model),
        "settlement": {"dx": settlement_dx, "dy": settlement_dy},
        "travel": _travel_entry(model, travel),
        "nearby": [
            _object_entry(obj, origin)
            for obj in _nearby_objects(model, highlight_ids)[:NEARBY_LIMIT]
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
    threat = _threat_entry(model)
    if threat:
        state["threat"] = threat
    heard = model.recent_utterances(3)
    if heard:
        state["heard"] = [_heard_line(u, origin, model.tick) for u in heard]
    if notes:
        state["notes"] = notes
    return state


# Built things the map's glyphs cannot describe: each carries contents, notes,
# a seat or a use that only a line of JSON can say. They stay in `nearby`
# wherever they are. Walls, roads, floors and furniture are not here: the map
# shows them and there is nothing more to know.
NOTEWORTHY_OBJECT_TYPES: frozenset[str] = (
    frozenset(
        {
            items.CHEST,
            items.ITEM_PILE,
            items.MESSAGE_BOARD,
            items.SIGN,
            items.BED,
            items.DOOR,
            items.CONVERSATION,
        }
    )
    | items.STATION_KINDS
)


def _nearby_objects(
    model: WorldModel, highlight_ids: Collection[str]
) -> list[ObjectInfo]:
    """The objects worth spelling out, nearest first.

    A grove of trees used to fill all 25 slots with lines the map already
    drew, pushing the one bush and the one chest out of the state entirely.
    So `nearby` now carries only what the map cannot express: what is underfoot,
    every built thing, whatever the brief named, and one example of each
    material in reach.
    """
    wanted = frozenset(highlight_ids)
    seen_types: set[str] = set()
    chosen: list[ObjectInfo] = []
    for obj in model.objects_near(VIEW_RADIUS):
        keep = (
            chebyshev(obj.position, model.position) <= 1
            or obj.object_type in NOTEWORTHY_OBJECT_TYPES
            or obj.object_id in wanted
        )
        if not keep and obj.object_type in items.NATURAL_OBJECT_TYPES:
            keep = obj.object_type not in seen_types
        if not keep:
            continue
        seen_types.add(obj.object_type)
        chosen.append(obj)
    return chosen


# Physics only: what to do about a wolf is the planner's brief to decide.
WOLF_FACTS = (
    f"A wolf has {items.WOLF_MAX_HEALTH} health and bites an adjacent settler for "
    f"{items.WOLF_ATTACK_DAMAGE} every tick. Every settler attacking the same wolf "
    "hits it on the same tick."
)


# Physics only: when to sleep is the planner's brief to decide.
FATIGUE_FACTS = (
    f"Fatigue rises 1 every {items.FATIGUE_INTERVAL_DAY} ticks by day and every "
    f"{items.FATIGUE_INTERVAL_NIGHT} ticks at night. From {items.TIRED_FATIGUE} "
    "you are tired: the work a tool adds per extract action is halved and "
    f"your attacks hit for 1 less. At {items.PLAYER_MAX_FATIGUE} you collapse where you "
    f"stand and sleep until fatigue falls to {items.COLLAPSE_WAKE_FATIGUE}. "
    f"Sleeping on a bed recovers {items.sleep_recovery_text(True, True)} at "
    f"night and {items.sleep_recovery_text(True, False)} by day; on the ground "
    f"{items.sleep_recovery_text(False, True)} at night and "
    f"{items.sleep_recovery_text(False, False)} by day. "
    f"You cannot fall asleep below fatigue {items.MIN_SLEEP_FATIGUE}."
)

DAY_FACTS = (
    f"A day is {items.DEFAULT_DAY_LENGTH_TICKS} ticks: the first two thirds are light "
    "and the rest is night."
)

# Physics only: when to eat is the planner's brief to decide.
FOOD_FACTS = (
    f"Food falls 1 every {items.FOOD_INTERVAL_TICKS} ticks. At food 0 you "
    f"lose {items.STARVATION_DAMAGE} health every "
    f"{items.STARVATION_INTERVAL_TICKS} ticks until you eat. Eating a berry "
    f"restores {items.BERRY_FOOD_RESTORE} food; berries come from bushes "
    "marked B on the map (b is a bush with no berry). Health regenerates 1 per "
    f"{items.REGEN_INTERVAL_TICKS} ticks only while food is above "
    f"{items.REGEN_FOOD_THRESHOLD} and you are not tired."
)

# One list, always present: three separate fact fields buried in three
# different blocks meant Jev read the wolf physics only when a wolf was already
# in view, and never read the food physics at all.
FACTS: tuple[str, ...] = (FOOD_FACTS, WOLF_FACTS, FATIGUE_FACTS, DAY_FACTS)


def _facts(model: WorldModel) -> list[str]:
    """The always-present physics, plus anything true of this body right now.

    Being shut into a pocket is the one situation the map alone does not make
    plain: the glyphs are all there, but "there is no way out of these six
    tiles" is a fact about reachability, not about a glyph.
    """
    facts = list(FACTS)
    enclosed = enclosed_fact(model)
    if enclosed:
        facts.append(enclosed)
    # Only on the day itself: what the clock says about a new moon weeks away
    # is nothing Jev can act on this tick (docs/14 section 1).
    if model.clock.new_moon_tonight:
        facts.append(model.clock.moon_text())
    return facts


def _clock_entry(model: WorldModel) -> dict[str, Any]:
    """The world clock: which day it is, how far into it, and whether it is night."""
    clock = model.clock
    return {
        "day": clock.day,
        "tick_of_day": f"{clock.tick_of_day}/{clock.day_length}",
        "night": clock.night,
    }


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
    if steps == NO_PATH:
        # Truly unreachable as far as the remembered map goes.
        return {
            "target": f"{travel.label} at dx {dx} dy {dy}",
            "next_step": "blocked",
            "steps_left": None,
        }
    if steps == 0:
        # An empty path means the goal is satisfied, not that the way is shut;
        # saying "blocked" here told Jev the opposite of the truth.
        return {
            "target": f"{travel.label} at dx {dx} dy {dy}",
            "next_step": "arrived",
            "steps_left": 0,
        }
    return {
        "target": f"{travel.label} at dx {dx} dy {dy}",
        "next_step": direction_name(direction),
        "steps_left": steps,
    }
