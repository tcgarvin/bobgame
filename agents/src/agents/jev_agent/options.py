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

import re
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
from .enclosure import blocks_movement, would_seal
from .pathfinding import find_path, legal_directions
from .worldmodel import (
    EXTRACTABLE_TYPES,
    VIEW_RADIUS,
    EntityInfo,
    ObjectInfo,
    WorldModel,
)

MAX_OPTIONS = 40
WAIT = "wait"

# Walking. Every walk option is a single A* step that also sets the travel
# state, so following a target is "pick the same key again" or `KEEP_GOING`.
STEP_KEY_PREFIX = "step_towards:"
KEEP_GOING = "keep_going"
STOP_GOING = "stop_going"

# How many walk targets each object group may contribute. A per-group quota
# replaces the old shared top-6: in a grove the six nearest objects were all
# trees, so the berry bush eight tiles away and the rock the brief named were
# never offered and Jev could only flip move_N/move_S.
STEP_TARGETS_PER_GROUP = 2
# Other settlers worth a walk option; every living wolf in view gets one.
STEP_SETTLER_LIMIT = 3

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
# A sign is only worth anything with a line on it, and Jev has no way to write
# one: it would plant blank posts. Placing signs is the planner's `place_sign`.
JEV_PLACEABLE_KINDS: frozenset[str] = PLACEABLE_KINDS - frozenset({items.SIGN})

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
    items.SIGN,
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
HEARD_SHOUT_KEY_PREFIX = f"{STEP_KEY_PREFIX}shout:"

# Conversations. Joining one is a seat at a turn-taking table; the walk to a
# free tile next to the anchor is code-owned, like the heard-shout walk.
JOIN_CONVERSATION_KEY_PREFIX = "join_conversation:"
JOIN_CONVERSATION_OPTION_LIMIT = 2

# Hails (docs/09 section 9): addressing a settler next to you starts a
# conversation with the two of you, without either of you having asked first.
# The planner grants them one at a time, in the brief; Jev never invents one.
HAIL_KEY_PREFIX = "hail:"
MAX_BRIEF_HAILS = 3
# How many times the world may refuse one brief hail before the stint stops
# offering it: two refusals are enough to show the reason is not going away.
HAIL_REFUSAL_LIMIT = 2


@dataclass(frozen=True)
class BriefHail:
    """One settler the planner told Jev it may address, and what to say."""

    settler: str
    line: str
    # What the actor wants out of the conversation the hail starts, shown only
    # to it once seated (docs/09 section 10, item 4). Empty when the planner
    # gave none.
    purpose: str = ""

    def as_payload(self) -> dict[str, str]:
        """The pair (and purpose, when given) as the brief payload and trace."""
        payload = {"settler": self.settler, "line": self.line}
        if self.purpose:
            payload["purpose"] = self.purpose
        return payload


# Object groups worth walking across the map for, each with its own quota, in
# the order they are offered. Grouping is what stops one dense resource from
# owning every walk option: a grove of trees now spends two slots, not six.
STEP_GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    ("berry bush", frozenset({items.BUSH})),
    ("tree", frozenset({items.TREE})),
    ("rock", items.ROCK_TYPES),
    ("reeds", frozenset({items.REEDS})),
    ("clay", frozenset({items.CLAY_DEPOSIT})),
    ("copper vein", frozenset({items.COPPER_VEIN})),
    ("iron vein", frozenset({items.IRON_VEIN})),
    ("chest", frozenset({items.CHEST})),
    ("item pile", frozenset({items.ITEM_PILE})),
    ("message board", frozenset({items.MESSAGE_BOARD})),
    ("sign", frozenset({items.SIGN})),
    ("workshop table", frozenset({items.WORKSHOP_TABLE})),
    ("furnace", frozenset({items.FURNACE})),
    ("anvil", frozenset({items.ANVIL})),
    ("bed", frozenset({items.BED})),
)

# Every object type any group covers; used to look candidates up in one pass.
STEP_TARGET_TYPES: frozenset[str] = frozenset(
    object_type for _, types in STEP_GROUPS for object_type in types
)

# The sections an option list is made of, in the order they are offered.
# `MAX_OPTIONS` truncates the tail, so the quota walk targets come last: they
# are the only section that may be cut. A target the brief named, a survival
# action and an interaction with something underfoot always survive.
OPTION_SECTIONS: tuple[str, ...] = (
    "wait",
    "brief_steps",
    "survival",
    "hail",
    "conversation",
    "travel_control",
    "interaction",
    "craft",
    "move",
    "quota_steps",
)

# World object ids look like `bush_17247`; this is how one is spotted in the
# free text of a brief so the named object always gets a walk option.
OBJECT_ID_PATTERN = re.compile(r"\b[a-z][a-z_]*_\d+\b")

# Place names the planner may attach to a brief (docs/05, "named places").
PLACE_NAME_PATTERN = re.compile(r"^[a-z0-9_]{1,24}$")
MAX_BRIEF_PLACES = 6

EMPTY_PLACES: Mapping[str, Coord] = {}


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
    hails: Sequence[BriefHail] = (),
    places: Mapping[str, Coord] = EMPTY_PLACES,
    brief_text: str = "",
    max_options: int = MAX_OPTIONS,
) -> list[Option]:
    """Every action that is legal for this actor on this tick, best-first.

    `shouts` are the phrases the planner put in the brief; Jev may shout those
    and nothing else. `hails` are the settlers the brief lets Jev address, with
    the line to say, the same way. `places` are the brief's named destinations,
    and `brief_text` is the instruction and notes, scanned for object ids so
    that whatever the brief names is always walkable-to.
    """
    position = model.position
    inventory = dict(model.self_info.inventory)

    sections: dict[str, list[Option]] = {
        "wait": [_wait_option()],
        "brief_steps": _brief_step_options(model, position, places, brief_text),
        "survival": _survival_options(model, inventory, position, travel, shouts),
        "hail": _hail_options(model, position, hails),
        "conversation": _conversation_options(model, position, travel),
        "travel_control": _travel_control_options(model, travel),
        "interaction": _interaction_options(model, inventory, position),
        "craft": _crafting_options(model, inventory),
        "move": _move_options(model, position),
        "quota_steps": _quota_step_options(model, position, travel),
    }

    seen: set[str] = set()
    unique: list[Option] = []
    for section in OPTION_SECTIONS:
        for option in sections[section]:
            if option.key in seen:
                continue
            seen.add(option.key)
            unique.append(option)
    return unique[:max_options]


def brief_object_ids(model: WorldModel, brief_text: str) -> list[str]:
    """Object ids the brief's own words name, in the order they appear.

    The planner writes ids like `bush_17247` into an instruction all the time,
    and a target Jev cannot walk to is a brief it cannot carry out.
    """
    found: list[str] = []
    for token in OBJECT_ID_PATTERN.findall(brief_text):
        if token in model.objects and token not in found:
            found.append(token)
    return found


def step_target_ids(options: Sequence[Option]) -> frozenset[str]:
    """The object or place names every `step_towards` option in `options` aims at."""
    return frozenset(
        option.key[len(STEP_KEY_PREFIX) :]
        for option in options
        if option.key.startswith(STEP_KEY_PREFIX)
    )


def _survival_options(
    model: WorldModel,
    inventory: Mapping[str, int],
    position: Coord,
    travel: TravelState | None,
    shouts: Sequence[str],
) -> list[Option]:
    options: list[Option] = []
    if inventory.get("berry", 0) > 0:
        food = model.self_info.food
        options.append(
            Option(
                key="eat:berry",
                description=f"eat a berry to restore 20 food (food now {food})",
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


def _hail_options(
    model: WorldModel,
    position: Coord,
    hails: Sequence[BriefHail],
) -> list[Option]:
    """Addressing a settler the brief named, or walking over to do it.

    Not offered while this actor holds a seat, nor for a settler that is dead,
    asleep or already in a conversation: the world would refuse every one of
    those, and Jev would spend the stint being told no.
    """
    if model.my_conversation() is not None:
        return []
    seated = {
        participant
        for conversation in model.conversations()
        for participant in conversation.participants
    }
    options: list[Option] = []
    for hail in hails[:MAX_BRIEF_HAILS]:
        target = model.entities.get(hail.settler)
        if target is None or not target.alive or target.asleep:
            continue
        if target.entity_id in seated:
            continue
        key = f"{HAIL_KEY_PREFIX}{hail.settler}"
        distance = chebyshev(target.position, position)
        if distance == 1:
            options.append(
                Option(
                    key=key,
                    description=(
                        f'say "{hail.line}" to {hail.settler}, next to you: a '
                        "conversation with the two of you starts and "
                        f"{hail.settler} answers first"
                    ),
                    intent=pb.Intent(
                        converse=pb.ConverseIntent(
                            action=items.ACTION_HAIL,
                            target_entity_id=hail.settler,
                            text=hail.line,
                        )
                    ),
                    clears_travel=True,
                )
            )
            continue
        path = find_path(model, position, target.position, stop_adjacent=True)
        if not path:
            continue
        options.append(
            Option(
                key=key,
                description=(
                    f"walk to {hail.settler}, {distance} tiles away, to say "
                    f'"{hail.line}" and start a conversation with them'
                ),
                intent=_move(direction_between(position, path[0])),
                travel_target=TravelState(
                    target=target.position,
                    label=f"{hail.settler}, to say your line to them",
                    stop_adjacent=True,
                ),
            )
        )
    return options


def _rest_options(model: WorldModel) -> list[Option]:
    """Resting on a bed, offered only to a wounded actor standing by one."""
    info = model.self_info
    if info.health >= info.max_health or info.food <= 0:
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
                    f"fatigue 0, on damage, at food {items.HUNGRY_WAKE_FOOD}, "
                    "or on a wake action"
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
                f"fatigue 0, on damage, at food {items.HUNGRY_WAKE_FOOD}, "
                "or on a wake action"
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
        options.append(
            Option(
                key=KEEP_GOING,
                description=(
                    f"keep going toward {travel.label} (next step "
                    f"{direction_name(direction)}, {len(path)} steps left)"
                ),
                intent=_move(direction),
            )
        )
    options.append(
        Option(
            key=STOP_GOING,
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
    for kind in sorted(JEV_PLACEABLE_KINDS & set(inventory)):
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
            # A settler once walled the six free neighbours of her own tile one
            # by one and starved in the cell. A door lets settlers through, so
            # placing one is never a seal.
            if blocks_movement(kind) and would_seal(model, position, target):
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


def _step_option_for_object(
    model: WorldModel, obj: ObjectInfo, position: Coord
) -> Option | None:
    """One A* step toward `obj`, or None when it is underfoot or unreachable."""
    distance = chebyshev(obj.position, position)
    if distance <= 1:
        return None
    state = travel_state_for(obj)
    path = find_path(model, position, state.target, stop_adjacent=state.stop_adjacent)
    if not path:
        return None
    dx = obj.position[0] - position[0]
    dy = obj.position[1] - position[1]
    return Option(
        key=f"{STEP_KEY_PREFIX}{obj.object_id}",
        description=(
            f"one step toward {obj.object_id} ({obj.object_type}) at "
            f"dx {dx} dy {dy}, {distance} tiles away"
        ),
        intent=_move(direction_between(position, path[0])),
        travel_target=state,
    )


def _step_option_for_entity(
    model: WorldModel, entity: EntityInfo, position: Coord
) -> Option | None:
    """One A* step toward another entity, stopping on the tile beside it."""
    distance = chebyshev(entity.position, position)
    if distance <= 1:
        return None
    path = find_path(model, position, entity.position, stop_adjacent=True)
    if not path:
        return None
    dx = entity.position[0] - position[0]
    dy = entity.position[1] - position[1]
    return Option(
        key=f"{STEP_KEY_PREFIX}{entity.entity_id}",
        description=(
            f"one step toward {entity.entity_id} ({entity.entity_type}) at "
            f"dx {dx} dy {dy}, {distance} tiles away"
        ),
        intent=_move(direction_between(position, path[0])),
        travel_target=TravelState(
            target=entity.position,
            label=f"{entity.entity_id} ({entity.entity_type})",
            stop_adjacent=True,
        ),
    )


def _greedy_step(model: WorldModel, position: Coord, target: Coord) -> pb.Direction:
    """The legal step that gets closest to `target`, or `NO_DIRECTION`.

    Used only when A* has failed: the actor may be standing at the edge of what
    it remembers, and walking hopefully into unknown ground is what turns a
    coordinate the planner named into a place the actor can reach.
    """
    current = chebyshev(position, target)
    best = NO_DIRECTION
    best_gap = current
    for direction in legal_directions(model, position):
        gap = chebyshev(offset(position, direction), target)
        if gap < best_gap:
            best_gap = gap
            best = direction
    return best


def _step_option_for_place(
    model: WorldModel, name: str, target: Coord, position: Coord
) -> Option | None:
    """One step toward a named place, by path if there is one and by nose if not."""
    distance = chebyshev(target, position)
    if distance == 0:
        return None
    dx = target[0] - position[0]
    dy = target[1] - position[1]
    where = f"at dx {dx} dy {dy}, {distance} tiles away"
    state = TravelState(target=target, label=name)
    path = find_path(model, position, target)
    if path:
        direction = direction_between(position, path[0])
        description = f"one step toward {name} {where}"
    else:
        # A* has failed. Walking hopefully is only walking toward a tile the
        # actor has never seen; when it *has* seen the tile and still has no
        # route, there is no route, and offering a hopeful step is what kept a
        # settler stepping north and south inside her own walls until she
        # starved. Saying nothing here is what makes `lost` and `no_path` fire.
        if model.is_known(target):
            return None
        direction = _greedy_step(model, position, target)
        if direction == NO_DIRECTION:
            return None
        # Said with confidence on purpose: a named place beyond the view is
        # ordinary walking, and Jev must not read "unknown" as "lost".
        description = (
            f"one step toward {name} {where}; it is beyond what you can see, "
            "so keep stepping and it comes into view"
        )
    return Option(
        key=f"{STEP_KEY_PREFIX}{name}",
        description=description,
        intent=_move(direction),
        travel_target=state,
    )


def _brief_step_options(
    model: WorldModel,
    position: Coord,
    places: Mapping[str, Coord],
    brief_text: str,
) -> list[Option]:
    """Walk options for everything the brief itself names.

    These come before the quota list and are never truncated: a brief that
    names a target Jev is not offered is a brief that cannot be carried out.
    """
    options: list[Option] = []
    for name, target in places.items():
        option = _step_option_for_place(model, name, target, position)
        if option is not None:
            options.append(option)
    for object_id in brief_object_ids(model, brief_text):
        obj = model.objects.get(object_id)
        if obj is None:
            continue
        option = _step_option_for_object(model, obj, position)
        if option is not None:
            options.append(option)
    return options


def _quota_step_options(
    model: WorldModel, position: Coord, travel: TravelState | None
) -> list[Option]:
    """Walk options for the nearest few of each kind of thing worth walking to.

    Each object group gets `STEP_TARGETS_PER_GROUP` slots, every living wolf in
    view gets one, and the nearest `STEP_SETTLER_LIMIT` settlers get one each.
    """
    by_group: dict[str, list[ObjectInfo]] = {name: [] for name, _ in STEP_GROUPS}
    group_of = {
        object_type: name for name, types in STEP_GROUPS for object_type in types
    }
    for obj in model.objects_by_type(STEP_TARGET_TYPES):
        if obj.object_type == items.BUSH and not obj.has_berry:
            continue
        if travel is not None and obj.position == travel.target:
            continue
        bucket = by_group[group_of[obj.object_type]]
        if len(bucket) < STEP_TARGETS_PER_GROUP:
            bucket.append(obj)

    options: list[Option] = []
    for name, _ in STEP_GROUPS:
        for obj in by_group[name]:
            option = _step_option_for_object(model, obj, position)
            if option is not None:
                options.append(option)

    settlers = 0
    for entity in model.entities_near(VIEW_RADIUS):
        if not entity.alive:
            continue
        is_wolf = entity.entity_type == "wolf"
        if not is_wolf:
            if settlers >= STEP_SETTLER_LIMIT:
                continue
            settlers += 1
        option = _step_option_for_entity(model, entity, position)
        if option is not None:
            options.append(option)
    return options


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
