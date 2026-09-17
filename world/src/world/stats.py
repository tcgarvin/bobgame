"""Per-tick entity bookkeeping: hunger, starvation, regeneration, respawn."""

import structlog

from .combat import WOLF_TYPE, apply_damage
from .events import RespawnEvent, TickEvents
from .items import FOOD_HUNGER_RESTORE
from .settlement import NoSettlementSiteError, nearest_free_walkable
from .state import World
from .types import Position

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


def find_free_tile(world: World, center: Position) -> Position | None:
    """Nearest in-bounds, walkable, unoccupied, object-free tile to `center`.

    Thin wrapper around `settlement.nearest_free_walkable` that returns None
    instead of raising when nothing is free within FREE_TILE_SEARCH_RADIUS.
    """
    try:
        return nearest_free_walkable(world, center, max_radius=FREE_TILE_SEARCH_RADIUS)
    except NoSettlementSiteError:
        return None


def respawn_position(world: World, death_position: Position) -> Position | None:
    """Where a player respawns: near the settlement, else near where it died."""
    center = world.settlement if world.settlement is not None else death_position
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
