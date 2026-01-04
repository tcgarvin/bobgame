"""Foraging action processing (collect, eat, regeneration)."""

from dataclasses import dataclass

import structlog

from .exceptions import EntityNotFoundError, ObjectNotFoundError
from .state import World
from .types import CollectIntent, EatIntent

logger = structlog.get_logger()


@dataclass
class CollectResult:
    """Result of a collect action."""

    entity_id: str
    success: bool
    object_id: str | None = None
    item_type: str | None = None
    amount: int = 0
    failure_reason: str | None = None


@dataclass
class EatResult:
    """Result of an eat action."""

    entity_id: str
    success: bool
    item_type: str | None = None
    amount: int = 0
    failure_reason: str | None = None


@dataclass
class ObjectChange:
    """Record of an object state change."""

    object_id: str
    field: str
    old_value: str
    new_value: str


def process_collect_phase(
    world: World,
    intents: dict[str, CollectIntent],
) -> tuple[list[CollectResult], list[ObjectChange]]:
    """
    Process collect intents for a tick.

    Returns:
        Tuple of (results, object_changes)
    """
    results: list[CollectResult] = []
    object_changes: list[ObjectChange] = []

    # Group intents by target object
    object_collectors: dict[str, list[CollectIntent]] = {}

    for entity_id, intent in intents.items():
        try:
            entity = world.get_entity(entity_id)
        except EntityNotFoundError:
            results.append(
                CollectResult(
                    entity_id=entity_id,
                    success=False,
                    failure_reason="entity_not_found",
                )
            )
            continue

        # Find target object
        if intent.object_id:
            try:
                obj = world.get_object(intent.object_id)
                if obj.position != entity.position:
                    results.append(
                        CollectResult(
                            entity_id=entity_id,
                            success=False,
                            object_id=intent.object_id,
                            failure_reason="object_not_at_position",
                        )
                    )
                    continue
                target_object_id = intent.object_id
            except ObjectNotFoundError:
                results.append(
                    CollectResult(
                        entity_id=entity_id,
                        success=False,
                        failure_reason="object_not_found",
                    )
                )
                continue
        else:
            # Find any collectible object at position
            objects = world.get_objects_at(entity.position)
            bush_objects = [o for o in objects if o.object_type == "bush"]
            if not bush_objects:
                results.append(
                    CollectResult(
                        entity_id=entity_id,
                        success=False,
                        failure_reason="no_collectible_object",
                    )
                )
                continue
            target_object_id = bush_objects[0].object_id

        object_collectors.setdefault(target_object_id, []).append(intent)

    # Resolve conflicts per object (lexicographic entity_id wins)
    for object_id, collectors in object_collectors.items():
        obj = world.get_object(object_id)
        berry_count = int(obj.get_state("berry_count", "0"))

        # Sort by entity_id for deterministic winner
        collectors.sort(key=lambda i: i.entity_id)

        for intent in collectors:
            if berry_count <= 0:
                results.append(
                    CollectResult(
                        entity_id=intent.entity_id,
                        success=False,
                        object_id=object_id,
                        failure_reason="no_berries",
                    )
                )
                continue

            # Collect berries (up to available)
            collect_amount = min(intent.amount, berry_count)
            old_count = berry_count
            berry_count -= collect_amount

            # Update entity inventory
            entity = world.get_entity(intent.entity_id)
            new_inventory = entity.inventory.add("berry", collect_amount)
            world._entities[intent.entity_id] = entity.with_inventory(new_inventory)

            results.append(
                CollectResult(
                    entity_id=intent.entity_id,
                    success=True,
                    object_id=object_id,
                    item_type="berry",
                    amount=collect_amount,
                )
            )

            object_changes.append(
                ObjectChange(
                    object_id=object_id,
                    field="berry_count",
                    old_value=str(old_count),
                    new_value=str(berry_count),
                )
            )

            logger.debug(
                "collect_success",
                entity_id=intent.entity_id,
                object_id=object_id,
                amount=collect_amount,
                remaining=berry_count,
            )

        # Update object state
        world.update_object(obj.with_state("berry_count", str(berry_count)))

    return results, object_changes


def process_eat_phase(
    world: World,
    intents: dict[str, EatIntent],
) -> list[EatResult]:
    """Process eat intents for a tick."""
    results: list[EatResult] = []

    for entity_id, intent in intents.items():
        try:
            entity = world.get_entity(entity_id)
        except EntityNotFoundError:
            results.append(
                EatResult(
                    entity_id=entity_id,
                    success=False,
                    item_type=intent.item_type,
                    failure_reason="entity_not_found",
                )
            )
            continue

        if not entity.inventory.has(intent.item_type, intent.amount):
            results.append(
                EatResult(
                    entity_id=entity_id,
                    success=False,
                    item_type=intent.item_type,
                    failure_reason="insufficient_items",
                )
            )
            continue

        # Remove from inventory
        new_inventory = entity.inventory.remove(intent.item_type, intent.amount)
        world._entities[entity_id] = entity.with_inventory(new_inventory)

        results.append(
            EatResult(
                entity_id=entity_id,
                success=True,
                item_type=intent.item_type,
                amount=intent.amount,
            )
        )

        logger.debug(
            "eat_success",
            entity_id=entity_id,
            item_type=intent.item_type,
            amount=intent.amount,
        )

    return results


def process_regeneration(
    world: World,
    regen_rate: int = 10,
) -> list[ObjectChange]:
    """
    Process bush regeneration.

    Args:
        world: World state
        regen_rate: Ticks between regeneration (e.g., 10 = +1 berry every 10 ticks)

    Returns:
        List of object changes
    """
    changes: list[ObjectChange] = []

    if world.tick % regen_rate != 0:
        return changes

    for obj in list(world.all_objects().values()):
        if obj.object_type != "bush":
            continue

        berry_count = int(obj.get_state("berry_count", "0"))
        max_berries = int(obj.get_state("max_berries", "5"))

        if berry_count < max_berries:
            old_count = berry_count
            new_count = berry_count + 1
            world.update_object(obj.with_state("berry_count", str(new_count)))
            changes.append(
                ObjectChange(
                    object_id=obj.object_id,
                    field="berry_count",
                    old_value=str(old_count),
                    new_value=str(new_count),
                )
            )
            logger.debug(
                "bush_regenerated",
                object_id=obj.object_id,
                old_count=old_count,
                new_count=new_count,
            )

    return changes
