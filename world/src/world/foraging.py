"""Foraging action processing (collect, eat, extract, regeneration)."""

from dataclasses import dataclass
from typing import Mapping

import structlog

from .events import ObjectChange, ObjectRemovedEvent, TickEvents
from .exceptions import EntityNotFoundError, ObjectNotFoundError
from .items import (
    BUILDING_KINDS,
    DISMANTLE_WORK,
    EXTRACT_THRESHOLD,
    EXTRACT_TOOL,
    EXTRACT_WORK_BARE,
    EXTRACT_WORK_WITH_TOOL,
    EXTRACT_YIELD,
    EXTRACTABLE_TYPES,
    default_remaining,
)
from .state import World, WorldObject
from .stats import hunger_restored
from .types import CollectIntent, EatIntent, ExtractIntent, is_same_or_adjacent

logger = structlog.get_logger()

# Object types an ExtractIntent may work: natural resources plus placed
# buildings, which come apart instead of yielding material per unit.
WORKABLE_OBJECT_TYPES = EXTRACTABLE_TYPES | BUILDING_KINDS


@dataclass
class CollectResult:
    """Result of a collect action.

    Berry bushes have binary state: either has a berry or doesn't.
    A successful collect always yields exactly 1 berry.
    """

    entity_id: str
    success: bool
    object_id: str | None = None
    item_type: str | None = None
    failure_reason: str | None = None


@dataclass
class EatResult:
    """Result of an eat action."""

    entity_id: str
    success: bool
    item_type: str | None = None
    amount: int = 0
    failure_reason: str | None = None


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
    # With binary berry state, only the first collector (sorted by entity_id) succeeds
    for object_id, collectors in object_collectors.items():
        obj = world.get_object(object_id)
        has_berry = obj.get_state("berry_count", "0") == "1"

        # Sort by entity_id for deterministic winner
        collectors.sort(key=lambda i: i.entity_id)

        berry_taken = False
        for intent in collectors:
            if not has_berry or berry_taken:
                results.append(
                    CollectResult(
                        entity_id=intent.entity_id,
                        success=False,
                        object_id=object_id,
                        failure_reason="no_berries",
                    )
                )
                continue

            # Collect the single berry
            berry_taken = True

            # Update entity inventory
            entity = world.get_entity(intent.entity_id)
            new_inventory = entity.inventory.add("berry", 1)
            world.set_entity(entity.with_inventory(new_inventory))

            results.append(
                CollectResult(
                    entity_id=intent.entity_id,
                    success=True,
                    object_id=object_id,
                    item_type="berry",
                )
            )

            object_changes.append(
                ObjectChange(
                    object_id=object_id,
                    field="berry_count",
                    old_value="1",
                    new_value="0",
                )
            )

            logger.debug(
                "collect_success",
                entity_id=intent.entity_id,
                object_id=object_id,
            )

        # Update object state if berry was taken
        if berry_taken:
            world.update_object(obj.with_state("berry_count", "0"))

    return results, object_changes


def process_eat_phase(
    world: World,
    intents: Mapping[str, EatIntent],
) -> list[EatResult]:
    """Process eat intents for a tick, restoring hunger."""
    results: list[EatResult] = []

    for entity_id in sorted(intents):
        intent = intents[entity_id]
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

        if intent.amount < 1:
            results.append(
                EatResult(
                    entity_id=entity_id,
                    success=False,
                    item_type=intent.item_type,
                    failure_reason="invalid_amount",
                )
            )
            continue

        restored = hunger_restored(intent.item_type, intent.amount)
        if restored <= 0:
            results.append(
                EatResult(
                    entity_id=entity_id,
                    success=False,
                    item_type=intent.item_type,
                    failure_reason="not_edible",
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

        updated = entity.with_inventory(
            entity.inventory.remove(intent.item_type, intent.amount)
        )
        updated = updated.with_hunger(updated.hunger + restored)
        if updated.wielded == intent.item_type and not updated.inventory.has(
            intent.item_type
        ):
            updated = updated.with_wielded("")
        world.set_entity(updated)

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
            hunger=updated.hunger,
        )

    return results


def object_remaining(obj_type: str, raw_remaining: str) -> int:
    """Units left in an extractable object, defaulting lazily by type."""
    if raw_remaining == "":
        return default_remaining(obj_type)
    try:
        return int(raw_remaining)
    except ValueError:
        return default_remaining(obj_type)


def extract_work(wielded: str, object_type: str) -> int:
    """Work units one extract action contributes."""
    if wielded and wielded == EXTRACT_TOOL.get(object_type, ""):
        return EXTRACT_WORK_WITH_TOOL
    return EXTRACT_WORK_BARE


def process_extract_phase(
    world: World,
    intents: Mapping[str, ExtractIntent],
    events: TickEvents,
) -> None:
    """Chop trees, mine rocks, cut reeds, dig clay and dismantle buildings.

    Several entities may work the same object in one tick; work is applied in
    lexicographic entity_id order, so that id wins a contested threshold.
    """
    by_object: dict[str, list[str]] = {}

    for entity_id in sorted(intents):
        intent = intents[entity_id]
        entity = world.get_entity(entity_id)
        try:
            obj = world.get_object(intent.object_id)
        except ObjectNotFoundError:
            events.acted(entity_id, "extract", False, f"no object {intent.object_id}")
            continue
        if obj.object_type not in WORKABLE_OBJECT_TYPES:
            events.acted(
                entity_id,
                "extract",
                False,
                f"{obj.object_id} cannot be extracted",
            )
            continue
        if not is_same_or_adjacent(entity.position, obj.position):
            events.acted(
                entity_id, "extract", False, f"{obj.object_id} is not adjacent"
            )
            continue
        by_object.setdefault(obj.object_id, []).append(entity_id)

    for object_id in sorted(by_object):
        obj = world.get_object(object_id)
        workers = sorted(by_object[object_id])
        if obj.object_type in BUILDING_KINDS:
            _dismantle_object(world, obj, workers, events)
        else:
            _extract_from_object(world, obj, workers, events)


def _dismantle_object(
    world: World,
    obj: WorldObject,
    workers: list[str],
    events: TickEvents,
) -> None:
    """Take a placed building apart, returning one item to whoever finishes it.

    DISMANTLE_WORK work units, one per action and no tool bonus
    (docs/08_building.md, "Dismantling").
    """
    old_progress = obj.get_state("progress", "0")
    progress = int(old_progress or "0")
    object_id = obj.object_id

    for entity_id in workers:
        progress += 1
        if progress < DISMANTLE_WORK:
            events.acted(
                entity_id,
                "extract",
                True,
                f"dismantling {object_id} ({progress}/{DISMANTLE_WORK})",
            )
            continue

        entity = world.get_entity(entity_id)
        world.set_entity(
            entity.with_inventory(entity.inventory.add(obj.object_type, 1))
        )
        world.remove_object(object_id)
        events.objects_removed.append(
            ObjectRemovedEvent(object_id=object_id, position=obj.position)
        )
        events.acted(
            entity_id,
            "extract",
            True,
            f"dismantled {object_id} (+1 {obj.object_type})",
        )
        logger.debug("object_dismantled", object_id=object_id, entity_id=entity_id)
        # The object is gone; anyone else who swung at it this tick missed.
        for latecomer in workers[workers.index(entity_id) + 1 :]:
            events.acted(latecomer, "extract", False, f"no object {object_id}")
        return

    updated = obj.with_state("progress", str(progress))
    world.update_object(updated)
    events.object_changes.append(
        ObjectChange(
            object_id=object_id,
            field="progress",
            old_value=old_progress,
            new_value=updated.get_state("progress"),
        )
    )


def _extract_from_object(
    world: World,
    obj: WorldObject,
    workers: list[str],
    events: TickEvents,
) -> None:
    """Work a natural object, yielding one item per EXTRACT_THRESHOLD units."""
    object_id = obj.object_id
    yielded = EXTRACT_YIELD[obj.object_type]
    old_progress = obj.get_state("progress", "0")
    old_remaining_raw = obj.get_state("remaining", "")
    progress = int(old_progress or "0")
    remaining = object_remaining(obj.object_type, old_remaining_raw)

    for entity_id in workers:
        if remaining <= 0:
            events.acted(entity_id, "extract", False, f"{object_id} is depleted")
            continue
        entity = world.get_entity(entity_id)
        progress += extract_work(entity.wielded, obj.object_type)
        if progress >= EXTRACT_THRESHOLD:
            progress -= EXTRACT_THRESHOLD
            remaining -= 1
            world.set_entity(entity.with_inventory(entity.inventory.add(yielded, 1)))
            events.acted(
                entity_id,
                "extract",
                True,
                f"worked {object_id} (+1 {yielded})",
            )
        else:
            events.acted(
                entity_id,
                "extract",
                True,
                f"worked {object_id} ({progress}/{EXTRACT_THRESHOLD})",
            )

    if remaining <= 0:
        world.remove_object(object_id)
        events.objects_removed.append(
            ObjectRemovedEvent(object_id=object_id, position=obj.position)
        )
        logger.debug("object_depleted", object_id=object_id)
        return

    updated = obj.with_state("progress", str(progress)).with_state(
        "remaining", str(remaining)
    )
    world.update_object(updated)
    if updated.get_state("progress") != old_progress:
        events.object_changes.append(
            ObjectChange(
                object_id=object_id,
                field="progress",
                old_value=old_progress,
                new_value=updated.get_state("progress"),
            )
        )
    if updated.get_state("remaining") != old_remaining_raw:
        events.object_changes.append(
            ObjectChange(
                object_id=object_id,
                field="remaining",
                old_value=old_remaining_raw,
                new_value=updated.get_state("remaining"),
            )
        )


def process_regeneration(
    world: World,
    regen_rate: int = 10,
) -> list[ObjectChange]:
    """
    Process bush regeneration.

    Berry bushes have binary state: either has a berry (1) or doesn't (0).
    At regeneration ticks, empty bushes grow a new berry.

    Args:
        world: World state
        regen_rate: Ticks between regeneration (e.g., 10 = regrow every 10 ticks)

    Returns:
        List of object changes
    """
    changes: list[ObjectChange] = []

    if world.tick % regen_rate != 0:
        return changes

    for obj in list(world.all_objects().values()):
        if obj.object_type != "bush":
            continue

        has_berry = obj.get_state("berry_count", "0") == "1"

        if not has_berry:
            world.update_object(obj.with_state("berry_count", "1"))
            changes.append(
                ObjectChange(
                    object_id=obj.object_id,
                    field="berry_count",
                    old_value="0",
                    new_value="1",
                )
            )
            logger.debug(
                "bush_regenerated",
                object_id=obj.object_id,
            )

    return changes
