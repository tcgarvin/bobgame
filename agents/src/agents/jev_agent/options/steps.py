"""Walk options: one A* step toward a target, and the travel controls.

Everything here turns "somewhere worth going" into a single move intent plus
the `TravelState` that makes the next tick's "keep going" mean the same thing.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from ... import world_pb2 as pb
from .. import items
from ..briefs import OBJECT_ID_PATTERN, Option, TravelState
from ..geometry import Coord, chebyshev, direction_name, offset, NO_DIRECTION
from ..pathfinding import legal_directions
from ..walk import greedy_step, plan_step
from ..worldmodel import VIEW_RADIUS, EntityInfo, ObjectInfo, WorldModel
from .common import (
    KEEP_GOING,
    STEP_KEY_PREFIX,
    STEP_GROUPS,
    STEP_SETTLER_LIMIT,
    STEP_TARGETS_PER_GROUP,
    STEP_TARGET_TYPES,
    STOP_GOING,
    _move,
    travel_state_for,
)


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


def _travel_control_options(
    model: WorldModel, travel: TravelState | None
) -> list[Option]:
    if travel is None:
        return []
    # Somebody may have stepped onto the destination mid-walk, or it was never
    # a tile one can stand on: finish beside it rather than lose the option.
    step = plan_step(
        model,
        model.position,
        travel.target,
        stop_adjacent=travel.stop_adjacent,
        retry_adjacent=True,
    )
    options: list[Option] = []
    if step.found:
        options.append(
            Option(
                key=KEEP_GOING,
                description=(
                    f"keep going toward {travel.label} (next step "
                    f"{direction_name(step.direction)}, {step.steps_left} steps left)"
                ),
                intent=_move(step.direction),
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
    step = plan_step(model, position, state.target, stop_adjacent=state.stop_adjacent)
    if not step.found:
        return None
    dx = obj.position[0] - position[0]
    dy = obj.position[1] - position[1]
    return Option(
        key=f"{STEP_KEY_PREFIX}{obj.object_id}",
        description=(
            f"one step toward {obj.object_id} ({obj.object_type}) at "
            f"dx {dx} dy {dy}, {distance} tiles away"
        ),
        intent=_move(step.direction),
        travel_target=state,
    )


def _step_option_for_entity(
    model: WorldModel, entity: EntityInfo, position: Coord
) -> Option | None:
    """One A* step toward another entity, stopping on the tile beside it."""
    distance = chebyshev(entity.position, position)
    if distance <= 1:
        return None
    step = plan_step(model, position, entity.position, stop_adjacent=True)
    if not step.found:
        return None
    dx = entity.position[0] - position[0]
    dy = entity.position[1] - position[1]
    return Option(
        key=f"{STEP_KEY_PREFIX}{entity.entity_id}",
        description=(
            f"one step toward {entity.entity_id} ({entity.entity_type}) at "
            f"dx {dx} dy {dy}, {distance} tiles away"
        ),
        intent=_move(step.direction),
        travel_target=TravelState(
            target=entity.position,
            label=f"{entity.entity_id} ({entity.entity_type})",
            stop_adjacent=True,
        ),
    )


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
    step = plan_step(model, position, target, retry_adjacent=model.is_known(target))
    state = TravelState(target=target, label=name, stop_adjacent=step.stop_adjacent)
    if step.found:
        direction = step.direction
        description = f"one step toward {name} {where}"
        if step.stop_adjacent:
            description += "; you cannot stand on it, so the walk ends beside it"
    else:
        # A* has failed. Walking hopefully is only walking toward a tile the
        # actor has never seen; when it *has* seen the tile and still has no
        # route, there is no route, and offering a hopeful step is what kept a
        # settler stepping north and south inside her own walls until she
        # starved. Saying nothing here is what makes `lost` and `no_path` fire.
        if model.is_known(target):
            return None
        direction = greedy_step(model, position, target)
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
