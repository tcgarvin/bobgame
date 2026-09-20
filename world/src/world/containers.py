"""Objects that hold things: item piles, chests and message boards.

Also hosts the inventory-versus-world actions that operate on them
(pickup, withdraw, drop, deposit, place, write_note), the equip action and
`give`, which moves items straight from one inventory to another.
"""

from typing import Any, Mapping

import structlog

from .conversations import share_conversation
from .events import (
    ObjectAddedEvent,
    ObjectRemovedEvent,
    TickEvents,
    commit_object,
)
from .exceptions import (
    EntityNotFoundError,
    InvalidObjectStateError,
    ObjectNotFoundError,
)
from .items import (
    CHEST_OBJECT,
    GROUND_LAYER_KINDS,
    ITEM_KINDS,
    ITEM_PILE,
    MESSAGE_BOARD_OBJECT,
    NATURAL_OBJECT_TYPES,
    PLACEABLE_KINDS,
    PLACED_OBJECT_TYPE,
    SIGN_AUTHOR_KEY,
    SIGN_OBJECT,
    SIGN_SLOT,
    SIGN_TEXT_KEY,
    SIGN_TEXT_MAX,
    SIGN_TICK_KEY,
)
from .objstate import (
    encode_json,
    read_json_list,
    read_json_object,
    with_updates,
)
from .state import WOLF_ENTITY_TYPE, World, WorldObject
from .types import (
    DepositIntent,
    DropIntent,
    EquipIntent,
    GiveIntent,
    PickupIntent,
    PlaceIntent,
    Position,
    WithdrawIntent,
    WriteNoteIntent,
    is_adjacent,
    is_same_or_adjacent,
)

logger = structlog.get_logger()

CONTENTS_KEY = "contents"
NOTES_KEY = "notes"
OWNER_KEY = "owner"

BOARD_SLOTS = 20
NOTE_TITLE_MAX = 60
NOTE_TEXT_MAX = 500

Note = dict[str, Any]


# --- Contents helpers -----------------------------------------------------


def read_contents(obj: WorldObject) -> dict[str, int]:
    """Parse an object's `contents` JSON into a {kind: count} dict.

    Raises:
        InvalidObjectStateError: If the value is not a JSON object of counts.
    """
    parsed = read_json_object(obj, CONTENTS_KEY, "a JSON object")
    contents: dict[str, int] = {}
    for kind, count in parsed.items():
        if not isinstance(kind, str) or not isinstance(count, int):
            raise InvalidObjectStateError(
                f"Object {obj.object_id} contents entry {kind!r} is not a count"
            )
        if count > 0:
            contents[kind] = count
    return contents


def encode_contents(contents: Mapping[str, int]) -> str:
    """Serialise a {kind: count} mapping to the stored JSON form."""
    return encode_json({k: v for k, v in sorted(contents.items()) if v > 0})


def with_contents(obj: WorldObject, contents: Mapping[str, int]) -> WorldObject:
    """Return a copy of `obj` with its contents replaced."""
    return obj.with_state(CONTENTS_KEY, encode_contents(contents))


def read_notes(obj: WorldObject) -> list[Note | None]:
    """Parse a message board's `notes` JSON into a list of 20 slots.

    Raises:
        InvalidObjectStateError: If the value is not a JSON list.
    """
    parsed = read_json_list(obj, NOTES_KEY, "a list")
    notes: list[Note | None] = [None] * BOARD_SLOTS
    for index, entry in enumerate(parsed[:BOARD_SLOTS]):
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise InvalidObjectStateError(
                f"Object {obj.object_id} note slot {index} is not an object"
            )
        notes[index] = entry
    return notes


def encode_notes(notes: list[Note | None]) -> str:
    """Serialise 20 note slots to the stored JSON form."""
    return encode_json(notes)


def empty_notes_json() -> str:
    """JSON for a fresh, empty message board."""
    return encode_notes([None] * BOARD_SLOTS)


# --- Item piles -----------------------------------------------------------


def find_pile(world: World, position: Position) -> WorldObject | None:
    """Return the item_pile at `position`, if any."""
    for obj in world.get_objects_at(position):
        if obj.object_type == ITEM_PILE:
            return obj
    return None


def add_items_to_ground(
    world: World,
    position: Position,
    items: Mapping[str, int],
    events: TickEvents,
) -> WorldObject | None:
    """Drop `items` on `position`, merging into an existing pile.

    Returns the resulting pile, or None when `items` is empty.
    """
    additions = {k: v for k, v in items.items() if v > 0}
    if not additions:
        return None

    pile = find_pile(world, position)
    if pile is None:
        pile = WorldObject(
            object_id=world.generate_object_id(ITEM_PILE),
            position=position,
            object_type=ITEM_PILE,
            state=((CONTENTS_KEY, encode_contents(additions)),),
        )
        world.add_object(pile)
        events.objects_added.append(ObjectAddedEvent(obj=pile))
        return pile

    contents = read_contents(pile)
    for kind, count in additions.items():
        contents[kind] = contents.get(kind, 0) + count
    return commit_object(world, pile, with_contents(pile, contents), events)


def _store_contents(
    world: World,
    obj: WorldObject,
    contents: Mapping[str, int],
    events: TickEvents,
) -> None:
    """Write contents back to an object, removing an emptied item pile.

    An emptied pile is litter and goes; a chest that has been emptied stays
    standing. That is a fact about the object type, not about the caller.
    """
    if obj.object_type == ITEM_PILE and not any(v > 0 for v in contents.values()):
        world.remove_object(obj.object_id)
        events.objects_removed.append(
            ObjectRemovedEvent(object_id=obj.object_id, position=obj.position)
        )
        return
    commit_object(world, obj, with_contents(obj, contents), events)


# --- Phases ---------------------------------------------------------------


def _take_from_container(
    world: World,
    entity_id: str,
    obj: WorldObject,
    kind: str,
    amount: int,
    action_type: str,
    events: TickEvents,
) -> None:
    """Move up to `amount` of `kind` from a container into an inventory."""
    contents = read_contents(obj)
    available = contents.get(kind, 0)
    if available <= 0:
        events.acted(entity_id, action_type, False, f"{obj.object_id} has no {kind}")
        return

    taken = min(available, amount)
    contents[kind] = available - taken
    entity = world.get_entity(entity_id)
    world.set_entity(entity.with_inventory(entity.inventory.add(kind, taken)))
    _store_contents(world, obj, contents, events)
    events.acted(
        entity_id,
        action_type,
        True,
        f"took {taken} {kind} from {obj.object_id}",
    )


def process_pickup_phase(
    world: World,
    intents: Mapping[str, PickupIntent],
    events: TickEvents,
) -> None:
    """Take items from an item_pile on the entity's own tile."""
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        entity = world.get_entity(entity_id)
        if intent.amount < 1:
            events.acted(entity_id, "pickup", False, "amount must be positive")
            continue
        pile = find_pile(world, entity.position)
        if pile is None:
            events.acted(entity_id, "pickup", False, "no item pile here")
            continue
        _take_from_container(
            world, entity_id, pile, intent.kind, intent.amount, "pickup", events
        )


def process_withdraw_phase(
    world: World,
    intents: Mapping[str, WithdrawIntent],
    events: TickEvents,
) -> None:
    """Take items out of a chest on the same or an adjacent tile."""
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        entity = world.get_entity(entity_id)
        if intent.amount < 1:
            events.acted(entity_id, "withdraw", False, "amount must be positive")
            continue
        try:
            chest = world.get_object(intent.object_id)
        except ObjectNotFoundError:
            events.acted(entity_id, "withdraw", False, f"no object {intent.object_id}")
            continue
        if chest.object_type != "chest":
            events.acted(
                entity_id, "withdraw", False, f"{chest.object_id} is not a chest"
            )
            continue
        if not is_same_or_adjacent(entity.position, chest.position):
            events.acted(entity_id, "withdraw", False, "chest is not adjacent")
            continue
        _take_from_container(
            world, entity_id, chest, intent.kind, intent.amount, "withdraw", events
        )


def process_drop_phase(
    world: World,
    intents: Mapping[str, DropIntent],
    events: TickEvents,
) -> None:
    """Drop items from inventory onto the entity's own tile."""
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        entity = world.get_entity(entity_id)
        if intent.amount < 1:
            events.acted(entity_id, "drop", False, "amount must be positive")
            continue
        if not entity.inventory.has(intent.kind, intent.amount):
            events.acted(entity_id, "drop", False, f"not enough {intent.kind} to drop")
            continue
        updated = entity.with_inventory(
            entity.inventory.remove(intent.kind, intent.amount)
        )
        if updated.wielded == intent.kind and not updated.inventory.has(intent.kind):
            updated = updated.with_wielded("")
        world.set_entity(updated)
        add_items_to_ground(
            world, entity.position, {intent.kind: intent.amount}, events
        )
        events.acted(entity_id, "drop", True, f"dropped {intent.amount} {intent.kind}")


def process_deposit_phase(
    world: World,
    intents: Mapping[str, DepositIntent],
    events: TickEvents,
) -> None:
    """Put items from inventory into a chest on the same or an adjacent tile."""
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        entity = world.get_entity(entity_id)
        if intent.amount < 1:
            events.acted(entity_id, "deposit", False, "amount must be positive")
            continue
        try:
            chest = world.get_object(intent.object_id)
        except ObjectNotFoundError:
            events.acted(entity_id, "deposit", False, f"no object {intent.object_id}")
            continue
        if chest.object_type != "chest":
            events.acted(
                entity_id, "deposit", False, f"{chest.object_id} is not a chest"
            )
            continue
        if not is_same_or_adjacent(entity.position, chest.position):
            events.acted(entity_id, "deposit", False, "chest is not adjacent")
            continue
        if not entity.inventory.has(intent.kind, intent.amount):
            events.acted(
                entity_id, "deposit", False, f"not enough {intent.kind} to deposit"
            )
            continue

        updated = entity.with_inventory(
            entity.inventory.remove(intent.kind, intent.amount)
        )
        if updated.wielded == intent.kind and not updated.inventory.has(intent.kind):
            updated = updated.with_wielded("")
        world.set_entity(updated)

        contents = read_contents(chest)
        contents[intent.kind] = contents.get(intent.kind, 0) + intent.amount
        _store_contents(world, chest, contents, events)
        events.acted(
            entity_id,
            "deposit",
            True,
            f"put {intent.amount} {intent.kind} in {chest.object_id}",
        )


def process_equip_phase(
    world: World,
    intents: Mapping[str, EquipIntent],
    events: TickEvents,
) -> None:
    """Wield an inventory item (or unequip with an empty kind)."""
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        entity = world.get_entity(entity_id)
        if intent.kind == "":
            world.set_entity(entity.with_wielded(""))
            events.acted(entity_id, "equip", True, "unequipped")
            continue
        if not entity.inventory.has(intent.kind):
            events.acted(entity_id, "equip", False, f"no {intent.kind} in inventory")
            continue
        world.set_entity(entity.with_wielded(intent.kind))
        events.acted(entity_id, "equip", True, f"wielding {intent.kind}")


def _place_target(
    entity_position: Position, intent: PlaceIntent, ground: bool
) -> Position | None:
    """Tile a place intent aims at, or None when it lacks a direction.

    Ground-layer kinds may be laid on the placer's own tile by leaving the
    direction unspecified; structures always need one.
    """
    if intent.direction is not None:
        return entity_position.offset(intent.direction)
    return entity_position if ground else None


def process_place_phase(
    world: World,
    intents: Mapping[str, PlaceIntent],
    events: TickEvents,
) -> None:
    """Place a held item as a world object (docs/08_building.md, "Placement").

    A tile holds at most one ground-layer object (road, floors) and at most one
    structure-layer object; a structure may stand on a ground object.
    """
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        entity = world.get_entity(entity_id)

        if intent.kind not in PLACEABLE_KINDS:
            events.acted(entity_id, "place", False, f"{intent.kind} is not placeable")
            continue
        if not entity.inventory.has(intent.kind):
            events.acted(entity_id, "place", False, f"no {intent.kind} in inventory")
            continue

        ground = intent.kind in GROUND_LAYER_KINDS
        target = _place_target(entity.position, intent, ground)
        if target is None:
            events.acted(entity_id, "place", False, f"{intent.kind} needs a direction")
            continue
        if not world.in_bounds(target):
            events.acted(entity_id, "place", False, "target out of bounds")
            continue
        if not world.is_walkable(target):
            events.acted(entity_id, "place", False, "target not walkable")
            continue

        objects_at = world.get_objects_at(target)
        if ground:
            if any(obj.object_type in NATURAL_OBJECT_TYPES for obj in objects_at):
                events.acted(entity_id, "place", False, "target holds a natural object")
                continue
            if any(obj.object_type in GROUND_LAYER_KINDS for obj in objects_at):
                events.acted(
                    entity_id, "place", False, "target already holds a ground object"
                )
                continue
        else:
            # Structures need standing room: no entity, nothing else on the
            # structure layer.
            if world.is_position_occupied(target):
                events.acted(entity_id, "place", False, "target occupied by an entity")
                continue
            if any(obj.object_type not in GROUND_LAYER_KINDS for obj in objects_at):
                events.acted(
                    entity_id, "place", False, "target already holds an object"
                )
                continue

        object_type = PLACED_OBJECT_TYPE[intent.kind]
        state: list[tuple[str, str]] = [(OWNER_KEY, entity_id)]
        if object_type == CHEST_OBJECT:
            state.append((CONTENTS_KEY, encode_contents({})))
        elif object_type == MESSAGE_BOARD_OBJECT:
            state.append((NOTES_KEY, empty_notes_json()))
        elif object_type == SIGN_OBJECT:
            # A sign is born blank; `write_note` fills the three keys in.
            state.append((SIGN_TEXT_KEY, ""))
            state.append((SIGN_AUTHOR_KEY, ""))
            state.append((SIGN_TICK_KEY, ""))

        obj = WorldObject(
            object_id=world.generate_object_id(object_type),
            position=target,
            object_type=object_type,
            state=tuple(state),
        )
        world.add_object(obj)
        events.objects_added.append(ObjectAddedEvent(obj=obj))
        world.set_entity(entity.with_inventory(entity.inventory.remove(intent.kind, 1)))
        events.acted(entity_id, "place", True, f"placed {obj.object_id} at {target}")


def process_give_phase(
    world: World,
    intents: Mapping[str, GiveIntent],
    events: TickEvents,
) -> None:
    """Hand items to another living player (docs/09, section 3).

    The target must be adjacent (Chebyshev 1) or sit in the same conversation,
    which lets participants trade across the anchor tile.
    """
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        giver = world.get_entity(entity_id)

        if intent.amount < 1:
            events.acted(entity_id, "give", False, "amount must be positive")
            continue
        if intent.target_entity_id == entity_id:
            events.acted(entity_id, "give", False, "cannot give to yourself")
            continue
        try:
            target = world.get_entity(intent.target_entity_id)
        except EntityNotFoundError:
            events.acted(
                entity_id, "give", False, f"no entity {intent.target_entity_id}"
            )
            continue
        if not target.alive:
            events.acted(entity_id, "give", False, f"{target.entity_id} is dead")
            continue
        if target.entity_type == WOLF_ENTITY_TYPE:
            events.acted(entity_id, "give", False, "cannot give to a wolf")
            continue
        if not is_adjacent(giver.position, target.position) and not share_conversation(
            world, entity_id, target.entity_id
        ):
            events.acted(
                entity_id, "give", False, f"{target.entity_id} is not adjacent"
            )
            continue
        if not giver.inventory.has(intent.kind, intent.amount):
            events.acted(entity_id, "give", False, f"not enough {intent.kind} to give")
            continue

        updated_giver = giver.with_inventory(
            giver.inventory.remove(intent.kind, intent.amount)
        )
        # A wielded item that runs out can no longer be held.
        if updated_giver.wielded == intent.kind and not updated_giver.inventory.has(
            intent.kind
        ):
            updated_giver = updated_giver.with_wielded("")
        world.set_entity(updated_giver)
        world.set_entity(
            target.with_inventory(target.inventory.add(intent.kind, intent.amount))
        )
        events.acted(
            entity_id,
            "give",
            True,
            f"gave {intent.amount} {intent.kind} to {target.entity_id}",
        )


def _write_sign(
    world: World,
    entity_id: str,
    sign: WorldObject,
    intent: WriteNoteIntent,
    events: TickEvents,
) -> None:
    """Write, rewrite or blank the one line a sign carries.

    A sign has a single slot and no title: the text comes from `intent.text`
    and anything longer than `SIGN_TEXT_MAX` is refused rather than cut, so the
    writer knows the line they meant never went up (docs/08_building.md).
    """
    if intent.slot != SIGN_SLOT:
        events.acted(
            entity_id, "write_note", False, f"a sign has one slot: use {SIGN_SLOT}"
        )
        return
    text = intent.text
    if len(text) > SIGN_TEXT_MAX:
        events.acted(
            entity_id,
            "write_note",
            False,
            f"sign text is at most {SIGN_TEXT_MAX} characters, this one is "
            f"{len(text)}",
        )
        return

    if text:
        updates = {
            SIGN_TEXT_KEY: text,
            SIGN_AUTHOR_KEY: entity_id,
            SIGN_TICK_KEY: str(world.tick),
        }
        detail = f'wrote {sign.object_id}: "{text}"'
    else:
        updates = {SIGN_TEXT_KEY: "", SIGN_AUTHOR_KEY: "", SIGN_TICK_KEY: ""}
        detail = f"cleared {sign.object_id}"

    commit_object(world, sign, with_updates(sign, updates), events)
    events.acted(entity_id, "write_note", True, detail)


def process_write_note_phase(
    world: World,
    intents: Mapping[str, WriteNoteIntent],
    events: TickEvents,
) -> None:
    """Write or clear one slot of a message board, or the one line of a sign."""
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        entity = world.get_entity(entity_id)

        try:
            board = world.get_object(intent.object_id)
        except ObjectNotFoundError:
            events.acted(
                entity_id, "write_note", False, f"no object {intent.object_id}"
            )
            continue
        if board.object_type not in (MESSAGE_BOARD_OBJECT, SIGN_OBJECT):
            events.acted(
                entity_id,
                "write_note",
                False,
                f"{board.object_id} is not a message board or a sign",
            )
            continue
        if not is_same_or_adjacent(entity.position, board.position):
            events.acted(
                entity_id,
                "write_note",
                False,
                f"{board.object_id} is not adjacent",
            )
            continue
        if board.object_type == SIGN_OBJECT:
            _write_sign(world, entity_id, board, intent, events)
            continue
        if not 0 <= intent.slot < BOARD_SLOTS:
            events.acted(
                entity_id, "write_note", False, f"slot must be 0..{BOARD_SLOTS - 1}"
            )
            continue
        if len(intent.title) > NOTE_TITLE_MAX:
            events.acted(entity_id, "write_note", False, "title too long")
            continue
        if len(intent.text) > NOTE_TEXT_MAX:
            events.acted(entity_id, "write_note", False, "text too long")
            continue

        notes = read_notes(board)
        if intent.title == "" and intent.text == "":
            notes[intent.slot] = None
            detail = f"cleared slot {intent.slot} on {board.object_id}"
        else:
            notes[intent.slot] = {
                "title": intent.title,
                "text": intent.text,
                "author": entity_id,
                "tick": world.tick,
            }
            detail = f"wrote slot {intent.slot} on {board.object_id}"

        commit_object(
            world, board, board.with_state(NOTES_KEY, encode_notes(notes)), events
        )
        events.acted(entity_id, "write_note", True, detail)


__all__ = [
    "BOARD_SLOTS",
    "CONTENTS_KEY",
    "ITEM_KINDS",
    "NOTES_KEY",
    "NOTE_TEXT_MAX",
    "NOTE_TITLE_MAX",
    "OWNER_KEY",
    "add_items_to_ground",
    "empty_notes_json",
    "encode_contents",
    "encode_notes",
    "find_pile",
    "process_deposit_phase",
    "process_drop_phase",
    "process_equip_phase",
    "process_give_phase",
    "process_pickup_phase",
    "process_place_phase",
    "process_withdraw_phase",
    "process_write_note_phase",
    "read_contents",
    "read_notes",
    "with_contents",
]
