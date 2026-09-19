"""Fatigue, sleep, collapse and waking (docs/10_metal_and_sleep.md, section 4).

Two phases run each tick:

- `process_sleep_phase` turns `SleepIntent` and `WakeIntent` into state changes.
  It runs after the movement and action phases.
- `process_fatigue_phase` runs beside the food phase: it accumulates fatigue
  for awake players, recovers it for sleepers, heals bed sleepers, collapses
  the exhausted and wakes sleepers whose reason to sleep has gone.

Wolves have no body clock: they never accumulate fatigue and never sleep.
"""

from typing import Mapping

import structlog

from .events import TickEvents
from .exceptions import ObjectNotFoundError
from .items import BED
from .state import WOLF_ENTITY_TYPE as WOLF_TYPE, Entity, World
from .types import SleepIntent, WakeIntent, is_same_or_adjacent

logger = structlog.get_logger()

# --- Fatigue ---------------------------------------------------------------

# One point of fatigue every N ticks awake. Nights are tiring.
FATIGUE_PER_STEP = 1
FATIGUE_INTERVAL_DAY = 4
FATIGUE_INTERVAL_NIGHT = 3

# At or above TIRED_FATIGUE a settler works slower and stops regenerating.
TIRED_FATIGUE = 60
# A respawned settler comes back part-rested.
RESPAWN_FATIGUE = 30
# A collapsed sleeper wakes once fatigue has fallen back to this.
COLLAPSE_WAKE_FATIGUE = 70

# --- Recovery --------------------------------------------------------------

# Ticks of sleep per point of fatigue recovered, by place and time of day.
BED_NIGHT_INTERVAL = 1
BED_DAY_INTERVAL = 2
GROUND_NIGHT_INTERVAL = 2
GROUND_DAY_INTERVAL = 4
RECOVERY_PER_STEP = 1

# `Entity.sleeping_on` when the sleeper lies on the bare ground.
GROUND = ""

# --- Wake reasons ----------------------------------------------------------

WAKE_RESTED = "rested"
WAKE_DAMAGED = "damaged"
WAKE_HUNGRY = "hungry"
WAKE_BED_REMOVED = "bed removed"
WAKE_ASKED = "asked"


def is_tired(entity: Entity) -> bool:
    """Whether fatigue has reached the level that slows work (docs/10)."""
    return entity.fatigue >= TIRED_FATIGUE


def fatigue_interval(night: bool) -> int:
    """Ticks an awake player takes to gain one point of fatigue."""
    return FATIGUE_INTERVAL_NIGHT if night else FATIGUE_INTERVAL_DAY


def recovery_interval(on_bed: bool, night: bool) -> int:
    """Ticks a sleeper takes to shed one point of fatigue."""
    if on_bed:
        return BED_NIGHT_INTERVAL if night else BED_DAY_INTERVAL
    return GROUND_NIGHT_INTERVAL if night else GROUND_DAY_INTERVAL


def _has_body_clock(entity: Entity) -> bool:
    """Whether this entity accumulates fatigue and can sleep."""
    return entity.entity_type != WOLF_TYPE


def _occupied_beds(world: World) -> set[str]:
    """Object ids of the beds someone is currently asleep on."""
    return {
        entity.sleeping_on
        for entity in world.living_entities()
        if entity.asleep and entity.sleeping_on != GROUND
    }


# --- Sleep and wake intents ------------------------------------------------


def process_sleep_phase(
    world: World,
    sleep_intents: Mapping[str, SleepIntent],
    wake_intents: Mapping[str, WakeIntent],
    events: TickEvents,
) -> None:
    """Lay sleepers down and wake the ones that asked to get up.

    Wakes are applied first so an entity that woke this tick could in
    principle lie down again next tick, never within the same one.
    """
    _apply_wake_intents(world, wake_intents, events)
    _apply_sleep_intents(world, sleep_intents, events)


def _apply_wake_intents(
    world: World,
    intents: Mapping[str, WakeIntent],
    events: TickEvents,
) -> None:
    """Wake voluntary sleepers that asked to get up.

    Missing and dead entities are filtered here rather than by the tick's
    `_living_subset`, which drops every intent from a sleeper.
    """
    for entity_id in sorted(intents):
        entity = world.all_entities().get(entity_id)
        if entity is None:
            events.acted(entity_id, "wake", False, "entity not found")
            continue
        if not entity.alive:
            events.acted(entity_id, "wake", False, "dead")
            continue
        if not entity.asleep:
            events.acted(entity_id, "wake", False, "not asleep")
            continue
        if entity.collapsed:
            events.acted(entity_id, "wake", False, "collapsed")
            continue
        _wake(world, entity, WAKE_ASKED, events)


def _apply_sleep_intents(
    world: World,
    intents: Mapping[str, SleepIntent],
    events: TickEvents,
) -> None:
    """Put settlers to sleep on a named bed or on the ground.

    One sleeper per bed: the lexicographically smallest entity id wins, as
    everywhere else in the world.
    """
    taken = _occupied_beds(world)

    for entity_id in sorted(intents):
        entity = world.get_entity(entity_id)
        place = _sleep_place(world, entity, intents[entity_id].object_id, taken, events)
        if place is None:
            continue
        if entity.food <= 0:
            events.acted(entity_id, "sleep", False, "too hungry to sleep")
            continue
        if entity.fatigue <= 0:
            events.acted(entity_id, "sleep", False, "not tired")
            continue

        if place != GROUND:
            taken.add(place)
        world.set_entity(entity.as_asleep(place))
        where = place if place != GROUND else "the ground"
        events.acted(entity_id, "sleep", True, f"asleep on {where}")
        logger.debug("sleep_started", entity_id=entity_id, place=place)


def _sleep_place(
    world: World,
    entity: Entity,
    object_id: str,
    taken: set[str],
    events: TickEvents,
) -> str | None:
    """Validate where `entity` wants to sleep; None when it cannot sleep there.

    Returns the bed's object id, or GROUND for an empty `object_id`.
    """
    if not object_id:
        return GROUND

    try:
        bed = world.get_object(object_id)
    except ObjectNotFoundError:
        events.acted(entity.entity_id, "sleep", False, f"no object {object_id}")
        return None

    if bed.object_type != BED:
        events.acted(entity.entity_id, "sleep", False, f"{object_id} is not a bed")
        return None
    if not is_same_or_adjacent(entity.position, bed.position):
        events.acted(entity.entity_id, "sleep", False, f"{object_id} is not adjacent")
        return None
    if object_id in taken:
        events.acted(entity.entity_id, "sleep", False, f"{object_id} is taken")
        return None
    return object_id


def _wake(world: World, entity: Entity, reason: str, events: TickEvents) -> None:
    """Wake `entity` and report the reason."""
    world.set_entity(entity.as_awake())
    events.acted(entity.entity_id, "wake", True, f"woke up: {reason}")
    logger.debug("wake", entity_id=entity.entity_id, reason=reason)


# --- The fatigue phase -----------------------------------------------------


def process_fatigue_phase(world: World, events: TickEvents) -> None:
    """Accumulate, recover, collapse and wake, in that order.

    `events` is read as well as written: the damage recorded earlier this tick
    is what wakes a voluntary sleeper.
    """
    night = world.clock.night
    damaged = {damage.entity_id for damage in events.damage_events}

    _accumulate_fatigue(world, night, events)
    _recover_fatigue(world, night)
    _heal_bed_sleepers(world)
    _wake_sleepers(world, damaged, events)


def _accumulate_fatigue(world: World, night: bool, events: TickEvents) -> None:
    """Tire out awake players, collapsing the ones that hit max fatigue."""
    if world.tick % fatigue_interval(night) != 0:
        return

    for entity in sorted(world.living_entities(), key=lambda e: e.entity_id):
        if entity.asleep or not _has_body_clock(entity):
            continue
        tired = entity.with_fatigue(entity.fatigue + FATIGUE_PER_STEP)
        if tired.fatigue < tired.max_fatigue:
            world.set_entity(tired)
            continue
        world.set_entity(tired.as_asleep(GROUND, collapsed=True))
        events.acted(entity.entity_id, "collapse", True, "collapsed from exhaustion")
        logger.info("entity_collapsed", entity_id=entity.entity_id)


def _recover_fatigue(world: World, night: bool) -> None:
    """Shed fatigue for everyone asleep, at the rate for their place."""
    for entity in sorted(world.living_entities(), key=lambda e: e.entity_id):
        if not entity.asleep:
            continue
        interval = recovery_interval(entity.sleeping_on != GROUND, night)
        if world.tick % interval != 0:
            continue
        world.set_entity(entity.with_fatigue(entity.fatigue - RECOVERY_PER_STEP))


def _heal_bed_sleepers(world: World) -> None:
    """Heal bed sleepers regardless of food and fatigue (docs/10).

    The regen constants are imported here rather than at module level:
    `stats` imports this module for `is_tired` and `RESPAWN_FATIGUE`, and a
    top-level import in both directions would be circular.
    """
    from .stats import REGEN_AMOUNT, REGEN_INTERVAL_TICKS

    if world.tick % REGEN_INTERVAL_TICKS != 0:
        return

    for entity in sorted(world.living_entities(), key=lambda e: e.entity_id):
        if not entity.asleep or entity.sleeping_on == GROUND:
            continue
        if entity.health >= entity.max_health:
            continue
        world.set_entity(entity.with_health(entity.health + REGEN_AMOUNT))


def _wake_sleepers(world: World, damaged: set[str], events: TickEvents) -> None:
    """Wake every sleeper whose reason to stay asleep has gone."""
    for entity in sorted(world.living_entities(), key=lambda e: e.entity_id):
        if not entity.asleep:
            continue
        reason = _wake_reason(world, entity, damaged)
        if reason:
            _wake(world, entity, reason, events)


def _wake_reason(world: World, entity: Entity, damaged: set[str]) -> str:
    """Why this sleeper should wake, or "" to sleep on.

    A collapsed sleeper ignores damage and starvation; it sleeps until its fatigue
    has fallen to COLLAPSE_WAKE_FATIGUE.
    """
    if entity.collapsed:
        if entity.fatigue <= COLLAPSE_WAKE_FATIGUE:
            return WAKE_RESTED
        return ""

    if entity.fatigue <= 0:
        return WAKE_RESTED
    if entity.entity_id in damaged:
        return WAKE_DAMAGED
    if entity.food <= 0:
        return WAKE_HUNGRY
    if entity.sleeping_on != GROUND and not _bed_exists(world, entity.sleeping_on):
        return WAKE_BED_REMOVED
    return ""


def _bed_exists(world: World, object_id: str) -> bool:
    """Whether `object_id` is still a bed in the world."""
    bed = world.all_objects().get(object_id)
    return bed is not None and bed.object_type == BED
