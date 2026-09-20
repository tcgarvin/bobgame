"""Foraging action processing (collect, eat, extract, regeneration)."""

from typing import Mapping

import structlog

from .events import ObjectRemovedEvent, TickEvents, commit_object
from .exceptions import EntityNotFoundError, ObjectNotFoundError
from .items import (
    BUILDING_KINDS,
    DISMANTLE_WORK,
    EXTRACT_THRESHOLD,
    EXTRACT_TOOLS,
    EXTRACT_WORK_BARE,
    EXTRACT_WORK_BY_TOOL,
    EXTRACT_YIELD,
    EXTRACTABLE_TYPES,
    VEIN_REQUIRED_TOOLS,
    default_remaining,
)
from .state import World, WorldObject
from .sleep import is_tired
from .stats import food_restored
from .types import CollectIntent, EatIntent, ExtractIntent, is_same_or_adjacent

logger = structlog.get_logger()

# Object types an ExtractIntent may work: natural resources plus placed
# buildings, which come apart instead of yielding material per unit.
WORKABLE_OBJECT_TYPES = EXTRACTABLE_TYPES | BUILDING_KINDS


def process_collect_phase(
    world: World,
    intents: Mapping[str, CollectIntent],
    events: TickEvents,
) -> None:
    """Pick the single berry off a bush each collector is standing on.

    A bush holds at most one berry, so at most one collector per bush and per
    tick succeeds; the smallest entity id wins, as everywhere else.
    """
    # Target object per entity, in submission order, so the failures below are
    # reported before any bush is resolved.
    object_collectors: dict[str, list[str]] = {}

    for entity_id, intent in intents.items():
        try:
            entity = world.get_entity(entity_id)
        except EntityNotFoundError:
            events.acted(entity_id, "collect", False, "entity_not_found")
            continue

        if intent.object_id:
            try:
                obj = world.get_object(intent.object_id)
            except ObjectNotFoundError:
                events.acted(entity_id, "collect", False, "object_not_found")
                continue
            if obj.position != entity.position:
                events.acted(entity_id, "collect", False, "object_not_at_position")
                continue
            target_object_id = intent.object_id
        else:
            bushes = [
                o
                for o in world.get_objects_at(entity.position)
                if o.object_type == "bush"
            ]
            if not bushes:
                events.acted(entity_id, "collect", False, "no_collectible_object")
                continue
            target_object_id = bushes[0].object_id

        object_collectors.setdefault(target_object_id, []).append(entity_id)

    for object_id, collectors in object_collectors.items():
        obj = world.get_object(object_id)
        has_berry = obj.get_state("berry_count", "0") == "1"

        berry_taken = False
        for entity_id in sorted(collectors):
            if not has_berry or berry_taken:
                events.acted(entity_id, "collect", False, "no_berries")
                continue

            berry_taken = True
            entity = world.get_entity(entity_id)
            world.set_entity(entity.with_inventory(entity.inventory.add("berry", 1)))
            events.acted(
                entity_id, "collect", True, f"collected berry from {object_id}"
            )
            logger.debug("collect_success", entity_id=entity_id, object_id=object_id)

        if berry_taken:
            commit_object(world, obj, obj.with_state("berry_count", "0"), events)


def process_eat_phase(
    world: World,
    intents: Mapping[str, EatIntent],
    events: TickEvents,
) -> None:
    """Eat items out of the eater's own pack, restoring food."""
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        try:
            entity = world.get_entity(entity_id)
        except EntityNotFoundError:
            events.acted(entity_id, "eat", False, "entity_not_found")
            continue

        if intent.amount < 1:
            events.acted(entity_id, "eat", False, "invalid_amount")
            continue

        restored = food_restored(intent.item_type, intent.amount)
        if restored <= 0:
            events.acted(entity_id, "eat", False, "not_edible")
            continue

        if not entity.inventory.has(intent.item_type, intent.amount):
            events.acted(entity_id, "eat", False, "insufficient_items")
            continue

        updated = entity.with_inventory(
            entity.inventory.remove(intent.item_type, intent.amount)
        )
        updated = updated.with_food(updated.food + restored)
        if updated.wielded == intent.item_type and not updated.inventory.has(
            intent.item_type
        ):
            updated = updated.with_wielded("")
        world.set_entity(updated)

        events.acted(entity_id, "eat", True, f"ate {intent.amount} {intent.item_type}")
        logger.debug(
            "eat_success",
            entity_id=entity_id,
            item_type=intent.item_type,
            amount=intent.amount,
            food=updated.food,
        )


def object_remaining(obj_type: str, raw_remaining: str) -> int:
    """Units left in an extractable object, defaulting lazily by type."""
    if raw_remaining == "":
        return default_remaining(obj_type)
    try:
        return int(raw_remaining)
    except ValueError:
        return default_remaining(obj_type)


def extract_work(wielded: str, object_type: str) -> int:
    """Work units one extract action contributes when fresh.

    A tool only helps on the objects it suits (axes on trees, pickaxes on rock,
    clay and veins); the tier decides how much (docs/10, "Tools and tiers").
    """
    if wielded and wielded in EXTRACT_TOOLS.get(object_type, frozenset()):
        return EXTRACT_WORK_BY_TOOL.get(wielded, EXTRACT_WORK_BARE)
    return EXTRACT_WORK_BARE


def tired_work(work: int) -> int:
    """Work units a tired settler gets out of `work` (docs/10, "Fatigue")."""
    return max(1, work // 2)


def missing_vein_tool(wielded: str, object_type: str) -> str:
    """Failure detail when `wielded` cannot work this object, else "".

    Ore veins need a pickaxe of a high enough tier; everything else in
    EXTRACTABLE_TYPES can be worked bare-handed.
    """
    required = VEIN_REQUIRED_TOOLS.get(object_type, frozenset())
    if not required or wielded in required:
        return ""
    return " or ".join(sorted(required))


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
        # One unit per action; the tired penalty (half, minimum 1) is a no-op.
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

    # `obj` may carry no `progress` key at all; spell out the default it reads
    # as, so an untouched default is not reported as a change.
    before = obj.with_state("progress", old_progress)
    commit_object(world, before, obj.with_state("progress", str(progress)), events)


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
        tools = missing_vein_tool(entity.wielded, obj.object_type)
        if tools:
            events.acted(entity_id, "extract", False, f"{object_id} needs a {tools}")
            continue
        work = extract_work(entity.wielded, obj.object_type)
        progress += tired_work(work) if is_tired(entity) else work
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

    # Spell out the defaults the keys read as, so an untouched default is not
    # reported as a change (the object may carry neither key).
    before = obj.with_state("progress", old_progress).with_state(
        "remaining", old_remaining_raw
    )
    after = before.with_state("progress", str(progress)).with_state(
        "remaining", str(remaining)
    )
    commit_object(world, before, after, events)


def process_regeneration(
    world: World,
    events: TickEvents,
    regen_rate: int = 10,
) -> None:
    """Grow a berry back on every empty bush, every `regen_rate` ticks.

    A berry bush's state is binary: it has a berry (1) or it does not (0).
    """
    if world.tick % regen_rate != 0:
        return

    for obj in list(world.all_objects().values()):
        if obj.object_type != "bush":
            continue
        # Spell out the default, so a bush with no `berry_count` key yet
        # reports the same change as one storing "0".
        before = obj.with_state("berry_count", obj.get_state("berry_count", "0"))
        if before.get_state("berry_count") == "1":
            continue
        commit_object(world, before, before.with_state("berry_count", "1"), events)
        logger.debug("bush_regenerated", object_id=obj.object_id)
