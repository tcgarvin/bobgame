"""Enumeration of the actions Jev may choose from on a given tick.

Jev can only pick an option that code put in front of it, so this module is the
agent's real rulebook: every option here is legal *right now* according to the
world model, and each carries the proto Intent it will turn into. Options are
generated in priority order and truncated at `MAX_OPTIONS`, so the tail of the
list is what gets dropped when a tick is unusually rich.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .. import world_pb2 as pb
from .geometry import (
    direction_between,
    ORDERED_DIRECTIONS,
    Coord,
    chebyshev,
    direction_name,
    offset,
    same_or_adjacent,
)
from .pathfinding import find_path, legal_directions
from .worldmodel import EXTRACTABLE_TYPES, ObjectInfo, WorldModel

MAX_OPTIONS = 40
TRAVEL_CANDIDATES = 6
WAIT = "wait"

# recipe -> {item kind: units consumed}
CRAFT_RECIPES: Mapping[str, Mapping[str, int]] = {
    "axe": {"wood": 2, "stone": 1},
    "pickaxe": {"wood": 2, "stone": 2},
    "sword": {"wood": 1, "stone": 3},
    "chest": {"wood": 6},
    "message_board": {"wood": 4, "stone": 1},
}

WIELDABLE_KINDS: frozenset[str] = frozenset({"axe", "pickaxe", "sword"})
PLACEABLE_KINDS: frozenset[str] = frozenset({"chest", "message_board"})

# Canned phrases. Jev gets a closed set; free-text speech is the planner's job.
SAY_PHRASES: Mapping[str, str] = {
    "help": "I need help!",
    "wolf_here": "There is a wolf here!",
    "come_here": "Come to me.",
    "all_good": "All good here.",
}

# Object types worth walking across the map for.
TRAVEL_TARGET_TYPES: frozenset[str] = (
    frozenset({"tree", "bush", "chest", "message_board", "item_pile"})
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
    max_options: int = MAX_OPTIONS,
) -> list[Option]:
    """Every action that is legal for this actor on this tick, best-first."""
    options: list[Option] = [_wait_option()]
    position = model.position
    inventory = dict(model.self_info.inventory)

    options.extend(_survival_options(model, inventory, position))
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
    model: WorldModel, inventory: Mapping[str, int], position: Coord
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
                    f"(health {entity.health}/{entity.max_health})"
                ),
                intent=pb.Intent(
                    attack=pb.AttackIntent(target_entity_id=entity.entity_id)
                ),
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
            tool = "axe" if obj.object_type == "tree" else "pickaxe"
            speed = "fast" if wielded == tool else f"slow, wield {tool} to speed up"
            options.append(
                Option(
                    key=f"extract:{obj.object_id}",
                    description=(
                        f"chop/mine the {_object_label(obj, position)}, "
                        f"{obj.remaining} units left ({speed})"
                    ),
                    intent=pb.Intent(extract=pb.ExtractIntent(object_id=obj.object_id)),
                )
            )
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


def _crafting_options(model: WorldModel, inventory: Mapping[str, int]) -> list[Option]:
    options: list[Option] = []
    for recipe, cost in CRAFT_RECIPES.items():
        if any(inventory.get(kind, 0) < amount for kind, amount in cost.items()):
            continue
        cost_text = ", ".join(f"{amount} {kind}" for kind, amount in cost.items())
        options.append(
            Option(
                key=f"craft:{recipe}",
                description=f"craft a {recipe} using {cost_text}",
                intent=pb.Intent(craft=pb.CraftIntent(recipe=recipe)),
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


def _place_options(model: WorldModel, inventory: Mapping[str, int]) -> list[Option]:
    options: list[Option] = []
    position = model.position
    occupied = {obj.position for obj in model.objects_near(2)}
    occupied |= {entity.position for entity in model.entities_near(2)}
    for kind in sorted(PLACEABLE_KINDS & set(inventory)):
        for direction in ORDERED_DIRECTIONS:
            target = offset(position, direction)
            if target in occupied or not model.is_walkable(target):
                continue
            if not model.is_known(target):
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
