"""The new moon: the night everyone sleeps (docs/14_new_moon_and_saves.md).

Every `new_moon_every_days` days the world has a **new-moon night**. At the
first tick of that night every living, awake, non-wolf entity is put to sleep
where it stands, every open conversation closes, nothing wakes a sleeper for
`NEW_MOON_STILL_TICKS` ticks, and the wolves lie low until dawn.

The mechanic is pure physics: it happens whether or not a save is written, so
taking a save never changes what a run does. The save itself lives in
`snapshot.py` and `save_coordinator.py`.

Everything here is a small function of the clock, so the hooks in `tick.py`,
`sleep.py`, `wolves.py` and `stats.py` stay one line each.
"""

from .conversations import close_all_conversations
from .events import TickEvents
from .items import BED
from .sleep import GROUND
from .state import WOLF_ENTITY_TYPE, Entity, World, night_start_tick
from .types import Position

__all__ = [
    "NEW_MOON_SAVE_OFFSET",
    "NEW_MOON_STILL_TICKS",
    "NEW_MOON_SLEEP_REASON",
    "NEW_MOON_WAKE_REFUSAL",
    "apply_new_moon",
    "in_still_window",
    "is_forced_sleep_tick",
    "is_new_moon_day",
    "is_still",
    "next_new_moon_day",
    "save_tick_of_day",
    "wolves_lie_low",
]

# Ticks from the forced sleep during which nothing wakes anyone.
NEW_MOON_STILL_TICKS = 6

# The save is taken this many ticks into the still window.
NEW_MOON_SAVE_OFFSET = 3

# What the forced sleep and a refused wake are called in events.
NEW_MOON_SLEEP_REASON = "new moon"
NEW_MOON_WAKE_REFUSAL = "new moon"

# End reason recorded when a conversation is closed by the new moon.
NEW_MOON_CONVERSATION_REASON = "new_moon"


# --- Pure helpers ----------------------------------------------------------


def is_new_moon_day(day: int, every_days: int) -> bool:
    """Whether the night of 0-based `day` is a new moon.

    `every_days <= 0` means the world has no new moon at all.
    """
    if every_days <= 0:
        return False
    return (day + 1) % every_days == 0


def next_new_moon_day(day: int, every_days: int) -> int:
    """The first day at or after `day` with a new-moon night; -1 when never."""
    if every_days <= 0:
        return -1
    remaining = (every_days - 1 - day % every_days) % every_days
    return day + remaining


def save_tick_of_day(day_length: int) -> int:
    """Tick of day at which a new-moon night's save is taken."""
    return night_start_tick(day_length) + NEW_MOON_SAVE_OFFSET


def in_still_window(tick_of_day: int, day_length: int) -> bool:
    """Whether `tick_of_day` falls in the still window of a new-moon night."""
    start = night_start_tick(day_length)
    return start <= tick_of_day < start + NEW_MOON_STILL_TICKS


# --- World-level predicates ------------------------------------------------


def is_forced_sleep_tick(world: World) -> bool:
    """Whether this tick is the one on which everyone falls asleep."""
    clock = world.clock
    return is_new_moon_day(
        clock.day, world.new_moon_every_days
    ) and clock.tick_of_day == night_start_tick(clock.day_length)


def is_still(world: World) -> bool:
    """Whether the world is inside a new-moon still window right now."""
    clock = world.clock
    if not is_new_moon_day(clock.day, world.new_moon_every_days):
        return False
    return in_still_window(clock.tick_of_day, clock.day_length)


def wolves_lie_low(world: World) -> bool:
    """Whether wolves neither hunt nor arrive this tick (new-moon night)."""
    clock = world.clock
    if not is_new_moon_day(clock.day, world.new_moon_every_days):
        return False
    return clock.tick_of_day >= night_start_tick(clock.day_length)


# --- The forced sleep ------------------------------------------------------


def apply_new_moon(world: World, events: TickEvents) -> None:
    """Put everyone to sleep and close every conversation, once per new moon.

    Called from the tick pipeline right after the ordinary sleep phase. Does
    nothing on any tick but the first of a new-moon night.
    """
    if not is_forced_sleep_tick(world):
        return

    taken = {
        entity.sleeping_on
        for entity in world.living_entities()
        if entity.asleep and entity.sleeping_on != GROUND
    }

    for entity in sorted(world.living_entities(), key=lambda e: e.entity_id):
        if entity.entity_type == WOLF_ENTITY_TYPE or entity.asleep:
            continue
        place = _free_bed(world, entity, taken)
        if place != GROUND:
            taken.add(place)
        world.set_entity(entity.as_asleep(place))
        events.acted(entity.entity_id, "sleep", True, NEW_MOON_SLEEP_REASON)

    close_all_conversations(world, NEW_MOON_CONVERSATION_REASON, events)


def _free_bed(world: World, entity: Entity, taken: set[str]) -> str:
    """The lowest-id free bed on or next to `entity`, or GROUND for none.

    Only the nine neighbouring tiles are scanned, never the whole object
    registry. Beds are compared by object id, so the choice never depends on
    dictionary order.
    """
    candidates = [
        obj.object_id
        for position in _own_and_neighbours(entity)
        for obj in world.get_objects_at(position)
        if obj.object_type == BED and obj.object_id not in taken
    ]
    if not candidates:
        return GROUND
    return min(candidates)


def _own_and_neighbours(entity: Entity) -> list[Position]:
    """The entity's tile and the eight around it."""
    origin = entity.position
    return [
        Position(x=origin.x + dx, y=origin.y + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
    ]
