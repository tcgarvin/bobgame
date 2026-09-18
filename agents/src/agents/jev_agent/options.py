"""Enumeration of the actions Jev may choose from on a given tick.

Jev can only pick an option that code put in front of it, so this module is the
agent's real rulebook: every option here is legal *right now* according to the
world model, and each carries the proto Intent it will turn into. Options are
generated in priority order and truncated at `MAX_OPTIONS`, so the tail of the
list is what gets dropped when a tick is unusually rich.

Dismantling is deliberately absent. `ExtractIntent` on a placed building takes
it apart, and Jev reads "extract" as "gather materials", so offering it would
let a settler quietly eat the town wall it just built. Dismantling stays a
planner tool, where a coordinate and a reason are available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .. import world_pb2 as pb
from . import items
from .geometry import (
    direction_between,
    NO_DIRECTION,
    ORDERED_DIRECTIONS,
    Coord,
    chebyshev,
    direction_name,
    offset,
    same_or_adjacent,
)
from .pathfinding import find_path, legal_directions
from .worldmodel import EXTRACTABLE_TYPES, EntityInfo, ObjectInfo, WorldModel

MAX_OPTIONS = 40
TRAVEL_CANDIDATES = 6
WAIT = "wait"

# Jev sees a capped list, so the rich categories get their own budget: without
# these caps a settler carrying planks and stone would drown the move options
# in twelve craft lines and eight place lines.
CRAFT_OPTION_LIMIT = 6
PLACE_OPTION_LIMIT = 4

RECIPES = items.RECIPES
# recipe -> {item kind: units consumed}; kept for callers that only want inputs.
CRAFT_RECIPES: Mapping[str, Mapping[str, int]] = items.CRAFT_RECIPES

WIELDABLE_KINDS: frozenset[str] = items.WIELDABLE_KINDS
PLACEABLE_KINDS: frozenset[str] = items.PLACEABLE_KINDS

# Craft options are offered in this order and then truncated, so the things a
# settler usually needs first survive the cap.
CRAFT_PRIORITY: tuple[str, ...] = (
    items.AXE,
    items.PICKAXE,
    items.SWORD,
    items.IRON_SWORD,
    items.COPPER_AXE,
    items.COPPER_PICKAXE,
    items.IRON_AXE,
    items.IRON_PICKAXE,
    items.CHARCOAL,
    items.COPPER_INGOT,
    items.IRON_INGOT,
    items.PLANK,
    items.WORKSHOP_TABLE,
    items.FURNACE,
    items.ANVIL,
    items.ROPE,
    items.WOOD_WALL,
    items.DOOR,
    items.BED,
    items.ROAD,
    items.WOOD_FLOOR,
    items.STONE_WALL,
    items.STONE_FLOOR,
    items.CHEST,
    items.MESSAGE_BOARD,
    items.TABLE,
    items.CHAIR,
)

# One of each of these in the pack is plenty; a second is never urgent enough
# to spend an option slot on.
CRAFT_ONCE_KINDS: frozenset[str] = (
    items.WIELDABLE_KINDS
    | items.STATION_KINDS
    | frozenset({items.CHEST, items.MESSAGE_BOARD})
)

# Canned phrases. Jev gets a closed set; free-text speech is the planner's job.
SAY_PHRASES: Mapping[str, str] = {
    "come_here": "Come to me.",
    "all_good": "All good here.",
}

# How far away a wolf counts as "here" for the planner's alert.
WOLF_ALERT_RADIUS = 8

# Shouts. The planner writes the phrases into the brief; Jev only picks among
# them. The cooldown keeps twelve settlers from filling every ear, and a heard
# shout stays worth walking toward for a limited time.
SHOUT_COOLDOWN_TICKS = 8
MAX_BRIEF_SHOUTS = 4
MAX_SHOUT_LENGTH = 120
SHOUT_KEY_PREFIX = "shout:"
HEARD_SHOUT_MAX_AGE_TICKS = 20
HEARD_SHOUT_KEY_PREFIX = "travel_to:shout:"

# Conversations. Joining one is a seat at a turn-taking table; the walk to a
# free tile next to the anchor is code-owned, like the heard-shout walk.
JOIN_CONVERSATION_KEY_PREFIX = "join_conversation:"
JOIN_CONVERSATION_OPTION_LIMIT = 2

# Object types worth walking across the map for.
TRAVEL_TARGET_TYPES: frozenset[str] = (
    frozenset(
        {
            "bush",
            "chest",
            "message_board",
            "item_pile",
        }
    )
    | items.STATION_KINDS
    | EXTRACTABLE_TYPES
)


@dataclass(frozen=True)
class TravelState:
    """A code-owned journey: a destination and how to finish it."""

    target: Coord
    label: str
    stop_adjacent: bool = False


@dataclass(frozen=True)
class Option:
    """One choosable action, with the intent it becomes."""

    key: str
    description: str
    intent: pb.Intent
    travel_target: TravelState | None = None
    clears_travel: bool = False


def _move(direction: pb.Direction) -> pb.Intent:
    return pb.Intent(move=pb.MoveIntent(direction=direction))


def _wait_option() -> Option:
    return Option(
        key=WAIT,
        description="do nothing this tick and hold position",
        intent=pb.Intent(wait=pb.WaitIntent()),
    )


def _object_label(obj: ObjectInfo, origin: Coord) -> str:
    dx = obj.position[0] - origin[0]
    dy = obj.position[1] - origin[1]
    return f"{obj.object_type} at dx {dx} dy {dy}"


def travel_state_for(obj: ObjectInfo) -> TravelState:
    """The journey that walks the actor to (or up against) `obj`."""
    from .worldmodel import BLOCKING_OBJECT_TYPES

    return TravelState(
        target=obj.position,
        label=f"{obj.object_id} ({obj.object_type})",
        stop_adjacent=obj.object_type in BLOCKING_OBJECT_TYPES,
    )


def enumerate_options(
    model: WorldModel,
    travel: TravelState | None = None,
    *,
    shouts: Sequence[str] = (),
    max_options: int = MAX_OPTIONS,
) -> list[Option]:
    """Every action that is legal for this actor on this tick, best-first.

    `shouts` are the phrases the planner put in the brief; Jev may shout those
    and nothing else.
    """
    options: list[Option] = [_wait_option()]
    position = model.position
    inventory = dict(model.self_info.inventory)

    options.extend(_survival_options(model, inventory, position, travel, shouts))
    options.extend(_conversation_options(model, position, travel))
    options.extend(_travel_control_options(model, travel))
    options.extend(_interaction_options(model, inventory, position))
    options.extend(_crafting_options(model, inventory))
    options.extend(_move_options(model, position))
    options.extend(_travel_to_options(model, position, travel))
    options.extend(_say_options())

    seen: set[str] = set()
    unique: list[Option] = []
    for option in options:
        if option.key in seen:
            continue
        seen.add(option.key)
        unique.append(option)
    return unique[:max_options]


def _survival_options(
    model: WorldModel,
    inventory: Mapping[str, int],
    position: Coord,
    travel: TravelState | None,
    shouts: Sequence[str],
) -> list[Option]:
    options: list[Option] = []
    if inventory.get("berry", 0) > 0:
        hunger = model.self_info.hunger
        options.append(
            Option(
                key="eat:berry",
                description=f"eat a berry to restore 20 hunger (hunger now {hunger})",
                intent=pb.Intent(eat=pb.EatIntent(item_type="berry", amount=1)),
            )
        )
    options.extend(_rest_options(model))
    options.extend(_sleep_options(model))
    options.extend(_shout_options(model, shouts))
    options.extend(_heard_shout_options(model, position, travel))
    for entity in model.entities_near(1):
        if not entity.alive or entity.entity_id == model.entity_id:
            continue
        if chebyshev(entity.position, position) != 1:
            continue
        options.append(
            Option(
                key=f"attack:{entity.entity_id}",
                description=(
                    f"attack the adjacent {entity.entity_type} {entity.entity_id} "
                    f"(health {entity.health}/{entity.max_health}"
                    f"{_allies_in_the_fight(model, entity)})"
                ),
                intent=pb.Intent(
                    attack=pb.AttackIntent(target_entity_id=entity.entity_id)
                ),
            )
        )
    return options


def _allies_in_the_fight(model: WorldModel, target: EntityInfo) -> str:
    """For a wolf, how many other settlers are already next to it."""
    if target.entity_type != "wolf":
        return ""
    allies = len(model.allies_near(target.position, 1))
    return f", {allies} other settlers next to it"


def _shout_options(model: WorldModel, shouts: Sequence[str]) -> list[Option]:
    """One option per shout phrase the planner wrote into the brief.

    When to shout is Jev's call under the brief; code only enforces a cooldown.
    """
    last_shout = model.last_own_shout_tick()
    if last_shout >= 0 and model.tick - last_shout < SHOUT_COOLDOWN_TICKS:
        return []
    return [
        Option(
            key=f"{SHOUT_KEY_PREFIX}{index}",
            description=(
                f'shout "{phrase}" - every settler within {items.SHOUT_RADIUS} '
                "tiles hears it and where it came from"
            ),
            intent=pb.Intent(
                say=pb.SayIntent(text=phrase, channel=items.SHOUT_CHANNEL)
            ),
        )
        for index, phrase in enumerate(shouts[:MAX_BRIEF_SHOUTS])
    ]


def _heard_shout_options(
    model: WorldModel, position: Coord, travel: TravelState | None
) -> list[Option]:
    """Walking to where the most recent shout came from, whatever it was about.

    Not offered again while the actor is already on its way to that spot.
    """
    for shout in model.recent_shouts(HEARD_SHOUT_MAX_AGE_TICKS):
        target = shout.position
        distance = chebyshev(target, position)
        if distance <= 1:
            continue
        label = f"where {shout.speaker_id} shouted"
        already_going = (
            travel is not None and travel.label == label and travel.target == target
        )
        if already_going:
            continue
        path = find_path(model, position, target, stop_adjacent=True)
        if not path:
            continue
        age = model.tick - shout.tick
        return [
            Option(
                key=f"{HEARD_SHOUT_KEY_PREFIX}{shout.speaker_id}",
                description=(
                    f'go to where {shout.speaker_id} shouted "{shout.text}" '
                    f"{age} ticks ago, {distance} tiles away"
                ),
                intent=_move(direction_between(position, path[0])),
                travel_target=TravelState(
                    target=target,
                    label=label,
                    stop_adjacent=True,
                ),
            )
        ]
    return []


def _conversation_options(
    model: WorldModel, position: Coord, travel: TravelState | None
) -> list[Option]:
    """Joining a conversation in view that still has a free seat.

    Next to the anchor the option is the join intent itself; further away it is
    a code-owned walk to the anchor, exactly like the heard-shout option.
    """
    if model.my_conversation() is not None:
        return []
    options: list[Option] = []
    for conversation in model.conversations():
        if len(options) >= JOIN_CONVERSATION_OPTION_LIMIT:
            break
        if conversation.free_seats <= 0:
            continue
        distance = chebyshev(conversation.anchor, position)
        seated = ", ".join(conversation.participants) or "nobody"
        key = f"{JOIN_CONVERSATION_KEY_PREFIX}{conversation.conversation_id}"
        if distance == 1:
            options.append(
                Option(
                    key=key,
                    description=(
                        f"join the conversation {conversation.conversation_id} "
                        f"with {seated}; in it you speak when your turn comes "
                        f"round, and {conversation.free_seats} seats are free"
                    ),
                    intent=pb.Intent(
                        converse=pb.ConverseIntent(
                            action="join",
                            conversation_id=conversation.conversation_id,
                        )
                    ),
                    clears_travel=True,
                )
            )
            continue
        if distance == 0:
            # Standing on the anchor is not a seat; a move option gets off it.
            continue
        path = find_path(model, position, conversation.anchor, stop_adjacent=True)
        if not path:
            continue
        options.append(
            Option(
                key=key,
                description=(
                    f"walk to the conversation {conversation.conversation_id} "
                    f"with {seated}, {distance} tiles away, and take one of its "
                    f"{conversation.free_seats} free seats"
                ),
                intent=_move(direction_between(position, path[0])),
                travel_target=TravelState(
                    target=conversation.anchor,
                    label=f"the conversation {conversation.conversation_id}",
                    stop_adjacent=True,
                ),
            )
        )
    return options


def _rest_options(model: WorldModel) -> list[Option]:
    """Resting on a bed, offered only to a wounded actor standing by one."""
    info = model.self_info
    if info.health >= info.max_health or info.hunger <= 0:
        return []
    for obj in model.objects_near(1):
        if obj.object_type != items.BED:
            continue
        return [
            Option(
                key=f"rest:{obj.object_id}",
                description=(
                    f"rest on the bed {obj.object_id} to heal {items.REST_HEAL} "
                    f"health (health now {info.health}/{info.max_health})"
                ),
                intent=pb.Intent(rest=pb.RestIntent(object_id=obj.object_id)),
            )
        ]
    return []


def _sleep_options(model: WorldModel) -> list[Option]:
    """Sleeping on an adjacent bed or on the ground, and waking again.

    While asleep the only legal action is waking, so the two sets never appear
    together.
    """
    info = model.self_info
    night = model.clock.night
    if info.asleep:
        return [
            Option(
                key="wake",
                description=(
                    f"stop sleeping and stand up (fatigue "
                    f"{info.fatigue}/{info.max_fatigue})"
                ),
                intent=pb.Intent(wake=pb.WakeIntent()),
            )
        ]
    if info.fatigue <= 0:
        return []
    options: list[Option] = []
    for obj in model.objects_near(1):
        if obj.object_type != items.BED:
            continue
        options.append(
            Option(
                key=f"sleep:{obj.object_id}",
                description=(
                    f"sleep on the bed {obj.object_id}: it recovers "
                    f"{items.sleep_recovery_text(True, night)} and heals 1 health "
                    f"every {items.REGEN_INTERVAL_TICKS} ticks while you sleep "
                    f"(fatigue {info.fatigue}/{info.max_fatigue}). You wake at "
                    "fatigue 0, on damage, at hunger 0, or on a wake action"
                ),
                intent=pb.Intent(sleep=pb.SleepIntent(object_id=obj.object_id)),
            )
        )
        break
    options.append(
        Option(
            key="sleep:ground",
            description=(
                f"sleep on the ground where you stand: it recovers "
                f"{items.sleep_recovery_text(False, night)} while you sleep "
                f"(fatigue {info.fatigue}/{info.max_fatigue}). You wake at "
                "fatigue 0, on damage, at hunger 0, or on a wake action"
            ),
            intent=pb.Intent(sleep=pb.SleepIntent()),
        )
    )
    return options


def _travel_control_options(
    model: WorldModel, travel: TravelState | None
) -> list[Option]:
    if travel is None:
        return []
    path = find_path(
        model, model.position, travel.target, stop_adjacent=travel.stop_adjacent
    )
    options: list[Option] = []
    if path:
        direction = direction_between(model.position, path[0])
        steps_text = str(len(path))
        options.append(
            Option(
                key="follow_travel",
                description=(
                    f"take the next step ({direction_name(direction)}) toward "
                    f"{travel.label}, {steps_text} steps left"
                ),
                intent=_move(direction),
            )
        )
    options.append(
        Option(
            key="stop_travel",
            description=f"abandon the journey to {travel.label} and stand still",
            intent=pb.Intent(wait=pb.WaitIntent()),
            clears_travel=True,
        )
    )
    return options


def _interaction_options(
    model: WorldModel, inventory: Mapping[str, int], position: Coord
) -> list[Option]:
    options: list[Option] = []
    wielded = model.self_info.wielded

    for obj in model.objects_near(1):
        if obj.object_type in EXTRACTABLE_TYPES and same_or_adjacent(
            position, obj.position
        ):
            option = _extract_option(obj, position, wielded)
            if option is not None:
                options.append(option)
        elif obj.object_type == "bush" and obj.position == position and obj.has_berry:
            options.append(
                Option(
                    key=f"collect:{obj.object_id}",
                    description="pick the berry off the bush on this tile",
                    intent=pb.Intent(
                        collect=pb.CollectIntent(
                            object_id=obj.object_id, item_type="berry", amount=1
                        )
                    ),
                )
            )
        elif obj.object_type == "item_pile" and obj.position == position:
            for kind, count in sorted(obj.contents().items()):
                options.append(
                    Option(
                        key=f"pickup:{kind}",
                        description=f"pick up {kind} from the pile here ({count} available)",
                        intent=pb.Intent(
                            pickup=pb.PickupIntent(kind=kind, amount=min(count, 5))
                        ),
                    )
                )
        elif obj.object_type == "chest" and same_or_adjacent(position, obj.position):
            options.extend(_chest_options(obj, inventory))

    return options


def _chest_options(obj: ObjectInfo, inventory: Mapping[str, int]) -> list[Option]:
    options: list[Option] = []
    contents = obj.contents()
    for kind in sorted(inventory):
        if inventory[kind] <= 0:
            continue
        options.append(
            Option(
                key=f"deposit:{obj.object_id}:{kind}",
                description=f"put {kind} from your pack into chest {obj.object_id}",
                intent=pb.Intent(
                    deposit=pb.DepositIntent(
                        object_id=obj.object_id, kind=kind, amount=inventory[kind]
                    )
                ),
            )
        )
    for kind, count in sorted(contents.items()):
        options.append(
            Option(
                key=f"withdraw:{obj.object_id}:{kind}",
                description=f"take {kind} out of chest {obj.object_id} ({count} inside)",
                intent=pb.Intent(
                    withdraw=pb.WithdrawIntent(
                        object_id=obj.object_id, kind=kind, amount=min(count, 5)
                    )
                ),
            )
        )
    return options


def _extract_option(obj: ObjectInfo, position: Coord, wielded: str) -> Option | None:
    """Harvesting one object, or None when what is in hand cannot work it.

    A vein needs a pickaxe of the right tier; everything else yields to bare
    hands, only slower.
    """
    if not items.can_extract(obj.object_type, wielded):
        return None
    yields = items.EXTRACT_YIELD.get(obj.object_type, "materials")
    work = items.extract_work_per_action(obj.object_type, wielded)
    holding = f"with the {wielded}" if wielded else "bare-handed"
    return Option(
        key=f"extract:{obj.object_id}",
        description=(
            f"harvest {yields} from the {_object_label(obj, position)}, "
            f"{obj.remaining} units left ({work} work per action {holding}, "
            f"{items.EXTRACT_THRESHOLD} work per unit)"
        ),
        intent=pb.Intent(extract=pb.ExtractIntent(object_id=obj.object_id)),
    )


def craftable_now(model: WorldModel, inventory: Mapping[str, int]) -> list[str]:
    """Recipe names that would succeed on this tick, in priority order.

    A recipe is craftable when the inputs are in the pack and, for a station
    recipe, a placed station of that type is on or next to the actor's tile.
    """
    ready: list[str] = []
    for name, recipe in RECIPES.items():
        if recipe.station and model.station_near(recipe.station) is None:
            continue
        if any(
            inventory.get(kind, 0) < amount for kind, amount in recipe.inputs.items()
        ):
            continue
        if name in CRAFT_ONCE_KINDS and inventory.get(name, 0) > 0:
            continue
        if name in WIELDABLE_KINDS and model.self_info.wielded == name:
            continue
        ready.append(name)
    ready.sort(key=_craft_rank)
    return ready


def _craft_rank(recipe: str) -> tuple[int, str]:
    if recipe in CRAFT_PRIORITY:
        return (CRAFT_PRIORITY.index(recipe), recipe)
    return (len(CRAFT_PRIORITY), recipe)


def _craft_description(model: WorldModel, recipe_name: str) -> str:
    """One craft option's text: cost, yield, station, and work still to do."""
    recipe = RECIPES[recipe_name]
    yields = "" if recipe.output_count == 1 else f", {recipe.output_count} of them"
    text = f"craft {recipe_name} using {recipe.cost_text()}{yields}"
    if not recipe.station:
        return text
    text += f" at the {recipe.station} within reach"
    if recipe.work <= 1:
        return text
    station = model.station_near(recipe.station)
    done = 0
    if station is not None:
        started, actions = station.craft_progress(model.entity_id)
        if started == recipe_name:
            done = actions
    return f"{text}; {recipe.work} craft actions, {done} done so far"


def _crafting_options(model: WorldModel, inventory: Mapping[str, int]) -> list[Option]:
    options: list[Option] = []
    for recipe_name in craftable_now(model, inventory)[:CRAFT_OPTION_LIMIT]:
        options.append(
            Option(
                key=f"craft:{recipe_name}",
                description=_craft_description(model, recipe_name),
                intent=pb.Intent(craft=pb.CraftIntent(recipe=recipe_name)),
            )
        )

    wielded = model.self_info.wielded
    for kind in sorted(WIELDABLE_KINDS & set(inventory)):
        if kind == wielded:
            continue
        options.append(
            Option(
                key=f"equip:{kind}",
                description=f"wield the {kind} you are carrying",
                intent=pb.Intent(equip=pb.EquipIntent(kind=kind)),
            )
        )

    options.extend(_place_options(model, inventory))
    return options


def can_place_ground(model: WorldModel, position: Coord) -> bool:
    """Whether a road or floor may go on `position`.

    Ground objects never block, but they may not cover a natural object and
    only one may lie on a tile.
    """
    if not model.is_known(position):
        return False
    tile = model.tiles.get(position)
    if tile is not None and not tile.walkable:
        return False
    for obj in model.object_at(position):
        if obj.object_type in items.NATURAL_OBJECT_TYPES:
            return False
        if obj.object_type in items.GROUND_LAYER_KINDS:
            return False
    return True


def can_place_structure(model: WorldModel, position: Coord) -> bool:
    """Whether a wall, door, bed or other structure may go on `position`."""
    if not model.is_known(position) or not model.is_walkable(position):
        return False
    return not model.structure_objects_at(position)


def _place_options(model: WorldModel, inventory: Mapping[str, int]) -> list[Option]:
    """One placement per carried building item: own tile for ground, one free
    neighbour for structures."""
    options: list[Option] = []
    position = model.position
    for kind in sorted(PLACEABLE_KINDS & set(inventory)):
        if len(options) >= PLACE_OPTION_LIMIT:
            break
        if items.is_ground_kind(kind):
            if not can_place_ground(model, position):
                continue
            options.append(
                Option(
                    key=f"place:{kind}:here",
                    description=f"lay {kind} down on the tile you are standing on",
                    intent=pb.Intent(
                        place=pb.PlaceIntent(kind=kind, direction=NO_DIRECTION)
                    ),
                )
            )
            continue
        for direction in ORDERED_DIRECTIONS:
            target = offset(position, direction)
            if not can_place_structure(model, target):
                continue
            if any(entity.position == target for entity in model.entities_near(2)):
                continue
            name = direction_name(direction)
            options.append(
                Option(
                    key=f"place:{kind}:{name}",
                    description=f"set down the {kind} on the empty tile to the {name}",
                    intent=pb.Intent(
                        place=pb.PlaceIntent(kind=kind, direction=direction)
                    ),
                )
            )
            break  # one placement direction per kind is enough choice for Jev
    return options


def _move_options(model: WorldModel, position: Coord) -> list[Option]:
    options: list[Option] = []
    for direction in legal_directions(model, position):
        name = direction_name(direction)
        target = offset(position, direction)
        terrain = "unexplored" if not model.is_known(target) else "known ground"
        options.append(
            Option(
                key=f"move_{name}",
                description=f"step one tile {name} onto {terrain}",
                intent=_move(direction),
            )
        )
    return options


def _travel_to_options(
    model: WorldModel, position: Coord, travel: TravelState | None
) -> list[Option]:
    candidates: list[ObjectInfo] = []
    for obj in model.objects_by_type(TRAVEL_TARGET_TYPES):
        if chebyshev(obj.position, position) <= 1:
            continue
        if travel is not None and obj.position == travel.target:
            continue
        if obj.object_type == "bush" and not obj.has_berry:
            continue
        candidates.append(obj)
        if len(candidates) >= TRAVEL_CANDIDATES:
            break

    options: list[Option] = []
    for obj in candidates:
        state = travel_state_for(obj)
        path = find_path(
            model, position, state.target, stop_adjacent=state.stop_adjacent
        )
        if not path:
            continue
        direction = direction_between(position, path[0])
        distance = chebyshev(obj.position, position)
        options.append(
            Option(
                key=f"travel_to:{obj.object_id}",
                description=(
                    f"start walking to the {_object_label(obj, position)}, "
                    f"{distance} tiles away"
                ),
                intent=_move(direction),
                travel_target=state,
            )
        )
    return options


def _say_options() -> list[Option]:
    return [
        Option(
            key=f"say:{name}",
            description=f'say "{phrase}" out loud to anyone nearby',
            intent=pb.Intent(say=pb.SayIntent(text=phrase, channel="local")),
        )
        for name, phrase in SAY_PHRASES.items()
    ]


def options_to_criteria(options: Sequence[Option]) -> dict[str, str]:
    """The `criteria` mapping for a Jev `Choice` question."""
    return {option.key: option.description for option in options}


def retreat_option(model: WorldModel, options: Sequence[Option]) -> Option | None:
    """The move that best backs away from the nearest wolf, toward the settlement.

    Used by the hard danger rule, which overrides Jev when the actor is about to
    die. Returns None when no movement option improves the situation.
    """
    wolf = model.nearest_wolf()
    if wolf is None:
        return None
    position = model.position
    settlement = model.settlement
    best: Option | None = None
    best_score = float("-inf")
    current_gap = chebyshev(position, wolf.position)
    for option in options:
        if not option.key.startswith("move_"):
            continue
        direction = option.intent.move.direction
        target = offset(position, direction)
        gap = chebyshev(target, wolf.position)
        if gap <= current_gap:
            continue
        score = gap * 2.0 - chebyshev(target, settlement) * 0.1
        if score > best_score:
            best_score = score
            best = option
    return best
