"""Attacks, damage and death handling."""

from typing import Mapping

import structlog

from .containers import add_items_to_ground
from .events import (
    DamageEvent,
    DeathEvent,
    EntityDespawnedEvent,
    TickEvents,
)
from .exceptions import EntityNotFoundError
from .items import attack_damage
from .sleep import is_tired
from .state import WOLF_ENTITY_TYPE, World
from .types import AttackIntent, is_adjacent

logger = structlog.get_logger()

WOLF_TYPE = WOLF_ENTITY_TYPE

# Attack damage lost while tired (docs/10_metal_and_sleep.md, "Fatigue").
TIRED_DAMAGE_PENALTY = 1


def apply_damage(
    world: World,
    entity_id: str,
    amount: int,
    attacker_id: str,
    events: TickEvents,
) -> bool:
    """Apply `amount` damage to an entity, recording the event.

    Returns True when the entity died from this damage.
    """
    entity = world.get_entity(entity_id)
    if not entity.alive:
        return False

    damaged = entity.with_health(entity.health - amount)
    world.set_entity(damaged)
    events.damage_events.append(
        DamageEvent(
            entity_id=entity_id,
            attacker_id=attacker_id,
            amount=amount,
            remaining_health=damaged.health,
            position=damaged.position,
        )
    )
    if damaged.health > 0:
        return False

    kill_entity(world, entity_id, attacker_id, events)
    return True


def kill_entity(
    world: World,
    entity_id: str,
    killer_id: str,
    events: TickEvents,
) -> None:
    """Kill an entity: drop its inventory, detach it, schedule its respawn.

    Players stay in `all_entities()` marked dead and respawn later. Wolves are
    removed from the world entirely.
    """
    entity = world.get_entity(entity_id)
    if not entity.alive:
        return

    death_position = entity.position
    dropped = dict(entity.inventory.items)
    if dropped:
        add_items_to_ground(world, death_position, dropped, events)

    world.detach_entity(entity_id)
    events.deaths.append(
        DeathEvent(entity_id=entity_id, killer_id=killer_id, position=death_position)
    )
    logger.info("entity_died", entity_id=entity_id, killer_id=killer_id)

    if entity.entity_type == WOLF_TYPE:
        world.discard_entity(entity_id)
        world.clear_death(entity_id)
        events.entities_despawned.append(
            EntityDespawnedEvent(
                entity_id=entity_id, position=death_position, reason="killed"
            )
        )
        return

    world.set_entity(entity.as_dead())
    world.mark_death(entity_id, world.tick)


def process_attack_phase(
    world: World,
    intents: Mapping[str, AttackIntent],
    events: TickEvents,
) -> None:
    """Resolve all attacks simultaneously against post-movement positions."""
    if not intents:
        return

    # Validate first; all valid attacks are then applied against the same
    # pre-attack health values so simultaneous kills work.
    strikes: list[tuple[str, str, int]] = []  # attacker, target, damage

    for entity_id in sorted(intents):
        intent = intents[entity_id]
        attacker = world.get_entity(entity_id)

        if intent.target_entity_id == entity_id:
            events.acted(entity_id, "attack", False, "cannot attack yourself")
            continue
        try:
            target = world.get_entity(intent.target_entity_id)
        except EntityNotFoundError:
            events.acted(
                entity_id, "attack", False, f"no entity {intent.target_entity_id}"
            )
            continue
        if not target.alive:
            events.acted(
                entity_id, "attack", False, f"{target.entity_id} is already dead"
            )
            continue
        if not is_adjacent(attacker.position, target.position):
            events.acted(
                entity_id, "attack", False, f"{target.entity_id} is not adjacent"
            )
            continue

        damage = attack_damage(attacker.entity_type, attacker.wielded)
        if is_tired(attacker):
            # docs/10 "Fatigue": a tired settler hits one point softer.
            damage = max(1, damage - TIRED_DAMAGE_PENALTY)
        strikes.append((entity_id, target.entity_id, damage))

    # Apply damage simultaneously: accumulate per target, then resolve deaths.
    totals: dict[str, int] = {}
    for attacker_id, target_id, damage in strikes:
        totals[target_id] = totals.get(target_id, 0) + damage

    pre_health = {target_id: world.get_entity(target_id).health for target_id in totals}

    for attacker_id, target_id, damage in strikes:
        target = world.get_entity(target_id)
        remaining = max(0, pre_health[target_id] - totals[target_id])
        events.damage_events.append(
            DamageEvent(
                entity_id=target_id,
                attacker_id=attacker_id,
                amount=damage,
                remaining_health=remaining,
                position=target.position,
            )
        )
        events.acted(
            attacker_id,
            "attack",
            True,
            f"hit {target_id} for {damage} ({remaining} hp left)",
        )

    for target_id, total in sorted(totals.items()):
        target = world.get_entity(target_id)
        world.set_entity(target.with_health(pre_health[target_id] - total))

    for target_id, total in sorted(totals.items()):
        if pre_health[target_id] - total > 0:
            continue
        killer = next(a for a, t, _ in strikes if t == target_id)
        kill_entity(world, target_id, killer, events)
