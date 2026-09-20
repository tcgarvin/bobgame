"""Folding events into memory: the deaths the actor witnessed, and move names.

Hunting a wolf that died 1000 ticks ago cost three settlers most of a day in
the 2026-09-20 hamlet run, so an observed death is remembered until the body is
seen alive again (a respawned settler); a wolf id is never reused and stays
dead. The bookkeeping is a deque plus the entity-type map, so it lives here as
functions over both.
"""

from __future__ import annotations

from collections import deque
from typing import Mapping

from ... import world_pb2 as pb
from ..geometry import DELTA_TO_DIRECTION, Coord, direction_name
from .types import DeathSeen, EntityInfo


def delta_name(delta: Coord) -> str:
    """The compass name of a one-tile move, or `?` for anything else."""
    direction = DELTA_TO_DIRECTION.get(delta)
    if direction is None:
        return "?"
    return direction_name(direction)


def record_death(
    deaths: deque[DeathSeen],
    entity_types: Mapping[str, str],
    died: pb.EntityDied,
    tick: int,
) -> None:
    """Remember a death this actor observed, replacing any earlier one."""
    forget_death(deaths, died.entity_id)
    deaths.append(
        DeathSeen(
            entity_id=died.entity_id,
            entity_type=entity_types.get(died.entity_id, ""),
            tick=tick,
            killer_id=died.killer_id,
        )
    )


def forget_death(deaths: deque[DeathSeen], entity_id: str) -> None:
    """Drop any remembered death of `entity_id`, for a body seen alive again."""
    remaining = [death for death in deaths if death.entity_id != entity_id]
    if len(remaining) == len(deaths):
        return
    deaths.clear()
    deaths.extend(remaining)


def death_of(
    deaths: deque[DeathSeen],
    entities: Mapping[str, EntityInfo],
    entity_id: str,
) -> DeathSeen | None:
    """The remembered death of `entity_id`, or None if it may still be alive.

    A settler that has been observed alive since it died has respawned, so its
    death is forgotten; a wolf id is never reused and stays dead.
    """
    # Snapshot: the loop body can call `forget_death`, which rewrites the
    # deque, and a live deque iterator refuses to be mutated under it.
    for death in reversed(tuple(deaths)):
        if death.entity_id != entity_id:
            continue
        seen = entities.get(entity_id)
        if seen is not None and seen.alive and seen.last_seen > death.tick:
            forget_death(deaths, entity_id)
            return None
        return death
    return None


def recent_deaths(
    deaths: deque[DeathSeen],
    entities: Mapping[str, EntityInfo],
    limit: int,
) -> list[DeathSeen]:
    """The last `limit` deaths this actor saw that are still deaths."""
    # `death_of` forgets the death of a body seen alive again, which rewrites
    # `deaths`; iterate a snapshot so that is allowed.
    found = [
        death
        for death in reversed(tuple(deaths))
        if death_of(deaths, entities, death.entity_id) is not None
    ]
    return list(reversed(found[:limit]))
