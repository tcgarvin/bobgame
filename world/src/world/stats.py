"""Per-tick entity bookkeeping: hunger, starvation, regeneration, respawn."""

import structlog

from typing import Mapping

from .combat import WOLF_TYPE, apply_damage
from .events import RespawnEvent, TickEvents
from .exceptions import ObjectNotFoundError
from .items import BED, FOOD_HUNGER_RESTORE, REST_HEAL
from .settlement import NoSettlementSiteError, nearest_free_walkable
from .state import World
from .types import (
    Position,
    RestIntent,
    chebyshev_distance,
    is_same_or_adjacent,
)

logger = structlog.get_logger()

# Hunger drops one point every HUNGER_INTERVAL_TICKS ticks. At a 2 s tick a
# full stomach (100) lasts ~13 minutes before starvation damage begins.
HUNGER_PER_TICK = 1
HUNGER_INTERVAL_TICKS = 4
STARVATION_INTERVAL_TICKS = 4
STARVATION_DAMAGE = 1
REGEN_INTERVAL_TICKS = 5
REGEN_AMOUNT = 1
REGEN_HUNGER_THRESHOLD = 50
RESPAWN_DELAY_TICKS = 10
RESPAWN_HUNGER = 50

# Maximum ring radius searched by find_free_tile before giving up.
FREE_TILE_SEARCH_RADIUS = 64
# Respawning settlers keep this far from every living wolf when they can. It is
# wider than the wolves' chase radius (8), so a wolf camping the settlement
# does not notice them arrive.
RESPAWN_SAFE_DISTANCE = 10
RESPAWN_RING_DISTANCES = (12, 24)
RESPAWN_RING_DIRECTIONS = (
    (0, -1),
    (1, 0),
    (0, 1),
    (-1, 0),
    (1, -1),
    (1, 1),
    (-1, 1),
    (-1, -1),
)


def hunger_restored(kind: str, amount: int) -> int:
    """Hunger restored by eating `amount` units of `kind` (0 if inedible)."""
    return FOOD_HUNGER_RESTORE.get(kind, 0) * amount


def process_hunger_phase(world: World, events: TickEvents) -> None:
    """Drop hunger, then apply starvation damage to entities at hunger 0.

    Wolves never starve; their hunger stays at max.
    """
    if world.tick % HUNGER_INTERVAL_TICKS == 0:
        for entity in sorted(world.living_entities(), key=lambda e: e.entity_id):
            if entity.entity_type == WOLF_TYPE:
                continue
            world.set_entity(entity.with_hunger(entity.hunger - HUNGER_PER_TICK))

    if world.tick % STARVATION_INTERVAL_TICKS != 0:
        return

    for entity in sorted(world.living_entities(), key=lambda e: e.entity_id):
        if entity.entity_type == WOLF_TYPE or entity.hunger > 0:
            continue
        apply_damage(world, entity.entity_id, STARVATION_DAMAGE, "", events)


def process_health_regen(world: World) -> None:
    """Regenerate health for well-fed, wounded entities."""
    if world.tick % REGEN_INTERVAL_TICKS != 0:
        return

    for entity in sorted(world.living_entities(), key=lambda e: e.entity_id):
        if entity.health >= entity.max_health:
            continue
        if entity.hunger <= REGEN_HUNGER_THRESHOLD:
            continue
        world.set_entity(entity.with_health(entity.health + REGEN_AMOUNT))


def process_rest_phase(
    world: World,
    intents: Mapping[str, RestIntent],
    events: TickEvents,
) -> None:
    """Rest on a bed to heal REST_HEAL health (docs/08_building.md, "Resting").

    One rester per bed per tick: the lexicographically smallest entity id wins,
    the others are told the bed is taken. Resting needs hunger above zero.
    """
    taken: set[str] = set()

    for entity_id in sorted(intents):
        intent = intents[entity_id]
        entity = world.get_entity(entity_id)
        try:
            bed = world.get_object(intent.object_id)
        except ObjectNotFoundError:
            events.acted(entity_id, "rest", False, f"no object {intent.object_id}")
            continue

        if bed.object_type != BED:
            events.acted(entity_id, "rest", False, f"{bed.object_id} is not a bed")
            continue
        if not is_same_or_adjacent(entity.position, bed.position):
            events.acted(entity_id, "rest", False, f"{bed.object_id} is not adjacent")
            continue
        if bed.object_id in taken:
            events.acted(entity_id, "rest", False, f"{bed.object_id} is taken")
            continue
        if entity.hunger <= 0:
            events.acted(entity_id, "rest", False, "too hungry to rest")
            continue

        taken.add(bed.object_id)
        rested = entity.with_health(entity.health + REST_HEAL)
        world.set_entity(rested)
        healed = rested.health - entity.health
        events.acted(
            entity_id,
            "rest",
            True,
            f"rested at {bed.object_id} (+{healed} health)",
        )
        logger.debug("rest_success", entity_id=entity_id, object_id=bed.object_id)


def find_free_tile(world: World, center: Position) -> Position | None:
    """Nearest in-bounds, walkable, unoccupied, object-free tile to `center`.

    Thin wrapper around `settlement.nearest_free_walkable` that returns None
    instead of raising when nothing is free within FREE_TILE_SEARCH_RADIUS.
    """
    try:
        return nearest_free_walkable(world, center, max_radius=FREE_TILE_SEARCH_RADIUS)
    except NoSettlementSiteError:
        return None


def _living_wolf_positions(world: World) -> list[Position]:
    return [
        entity.position
        for entity in world.all_entities().values()
        if entity.entity_type == WOLF_TYPE and entity.alive
    ]


def _respawn_centers(world: World, center: Position) -> list[Position]:
    """Places to try, nearest first: the centre, then rings of eight around it."""
    centers = [center]
    for distance in RESPAWN_RING_DISTANCES:
        for dx, dy in RESPAWN_RING_DIRECTIONS:
            x = min(max(center.x + dx * distance, 0), world.width - 1)
            y = min(max(center.y + dy * distance, 0), world.height - 1)
            centers.append(Position(x=x, y=y))
    return centers


def respawn_position(world: World, death_position: Position) -> Position | None:
    """Where a player respawns: near the settlement, else near where it died.

    A wolf standing at the settlement would otherwise kill each settler the
    moment it came back, so the first candidate that is out of every wolf's
    chase range wins. With wolves everywhere the settlement is used anyway.
    """
    center = world.settlement if world.settlement is not None else death_position
    wolves = _living_wolf_positions(world)
    for candidate in _respawn_centers(world, center):
        tile = find_free_tile(world, candidate)
        if tile is None:
            continue
        if all(
            chebyshev_distance(tile, wolf) >= RESPAWN_SAFE_DISTANCE for wolf in wolves
        ):
            return tile
    return find_free_tile(world, center)


def process_respawns(world: World, events: TickEvents) -> None:
    """Bring back players that died at least RESPAWN_DELAY_TICKS ago."""
    due = [
        entity_id
        for entity_id, death_tick in sorted(world.pending_respawns().items())
        if world.tick - death_tick >= RESPAWN_DELAY_TICKS
    ]

    for entity_id in due:
        entity = world.get_entity(entity_id)
        position = respawn_position(world, entity.position)
        if position is None:
            logger.warning("respawn_blocked", entity_id=entity_id)
            continue

        world.set_entity(entity.as_respawned(entity.position, RESPAWN_HUNGER))
        world.attach_entity(entity_id, position)
        world.clear_death(entity_id)
        events.respawns.append(RespawnEvent(entity_id=entity_id, position=position))
        logger.info("entity_respawned", entity_id=entity_id, position=str(position))
