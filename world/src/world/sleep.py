"""Fatigue, sleep, collapse and waking (docs/10_metal_and_sleep.md, section 4).

Two phases run each tick:

- `process_wake_phase` then `process_sleep_phase` turn `WakeIntent` and
  `SleepIntent` into state changes. Both run after the movement and action
  phases; waking first, so someone who woke this tick cannot lie down within
  it.
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

# Fatigue recovered per sleeping tick, as (points, ticks): "`points` fatigue
# every `ticks` ticks". A bed is strictly faster than the ground in both
# periods, and every period is fast enough that a settler need not lie down
# for most of the daylight: before the 2026-09-20 retunes the ground was 1 per
# 2 at night and 1 per 4 by day, and 31-45% of all settler-ticks were asleep.
BED_NIGHT_RECOVERY = (1, 1)
BED_DAY_RECOVERY = (2, 3)
GROUND_NIGHT_RECOVERY = (2, 3)
GROUND_DAY_RECOVERY = (1, 2)

# `Entity.sleeping_on` when the sleeper lies on the bare ground.
GROUND = ""

# --- Hunger and sleep ------------------------------------------------------

# One rule, read both ways: a settler will not lie down at or below this much
# food, and a sleeper that falls to it wakes. Waking ends the sleep, so it
# happens at most once per sleep. Before 2026-09-20 the line was food 0, and a
# settler slept from food 39 through the night and died four ticks after waking.
HUNGRY_WAKE_FOOD = 20

# --- How tired you have to be to lie down --------------------------------

# A settler will not fall asleep below this much fatigue. Before 2026-09-20 the
# floor was 1, and one settler took ten sleeps of one or two ticks at fatigue
# 1. Collapse is unaffected: it happens at max fatigue, whatever this says.
MIN_SLEEP_FATIGUE = 20

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


def recovery_rate(on_bed: bool, night: bool) -> tuple[int, int]:
    """Fatigue a sleeper sheds here, as (points, every this many ticks)."""
    if on_bed:
        return BED_NIGHT_RECOVERY if night else BED_DAY_RECOVERY
    return GROUND_NIGHT_RECOVERY if night else GROUND_DAY_RECOVERY


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


def process_wake_phase(
    world: World,
    intents: Mapping[str, WakeIntent],
    events: TickEvents,
) -> None:
    """Wake voluntary sleepers that asked to get up.

    Runs before `process_sleep_phase`, and on the raw intents: the tick's
    `_living_subset` drops every intent from a sleeper, which is everyone this
    phase is for.

    Missing and dead entities are filtered here rather than by the tick's
    `_living_subset`, which drops every intent from a sleeper. Inside a
    new-moon still window every wake is refused (docs/14).

    `moon` is imported here rather than at module level: it imports this module
    for `GROUND`, and a top-level import both ways would be circular.
    """
    from .moon import NEW_MOON_WAKE_REFUSAL, is_still

    still = is_still(world)

    for entity_id in sorted(intents):
        entity = world.all_entities().get(entity_id)
        if entity is None:
            events.acted(entity_id, "wake", False, "entity not found")
            continue
        if still:
            events.acted(entity_id, "wake", False, NEW_MOON_WAKE_REFUSAL)
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


def process_sleep_phase(
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
        if entity.food <= HUNGRY_WAKE_FOOD:
            events.acted(
                entity_id,
                "sleep",
                False,
                f"too hungry to sleep: food {entity.food}, and a sleeper wakes "
                f"at food {HUNGRY_WAKE_FOOD}",
            )
            continue
        if entity.fatigue < MIN_SLEEP_FATIGUE:
            events.acted(
                entity_id,
                "sleep",
                False,
                f"not tired enough to sleep: fatigue {entity.fatigue}, "
                f"and sleep needs fatigue {MIN_SLEEP_FATIGUE}",
            )
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
        points, interval = recovery_rate(entity.sleeping_on != GROUND, night)
        if world.tick % interval != 0:
            continue
        world.set_entity(entity.with_fatigue(entity.fatigue - points))


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
    """Wake every sleeper whose reason to stay asleep has gone.

    Nothing wakes anyone inside a new-moon still window: not rest, damage,
    hunger or a dismantled bed (docs/14, section 1).
    """
    from .moon import is_still

    if is_still(world):
        return

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
    if entity.food <= HUNGRY_WAKE_FOOD:
        return WAKE_HUNGRY
    if entity.sleeping_on != GROUND and not _bed_exists(world, entity.sleeping_on):
        return WAKE_BED_REMOVED
    return ""


def _bed_exists(world: World, object_id: str) -> bool:
    """Whether `object_id` is still a bed in the world."""
    bed = world.all_objects().get(object_id)
    return bed is not None and bed.object_type == BED
