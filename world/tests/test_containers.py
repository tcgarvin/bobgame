"""Tests for item piles, chests, message boards, equip and place."""

from world.containers import (
    BOARD_SLOTS,
    add_items_to_ground,
    encode_contents,
    empty_notes_json,
    process_deposit_phase,
    process_drop_phase,
    process_equip_phase,
    process_pickup_phase,
    process_place_phase,
    process_withdraw_phase,
    process_write_note_phase,
    read_contents,
    read_notes,
)
from world.events import TickEvents
from world.state import Entity, Inventory, Tile, World, WorldObject
from world.types import (
    DepositIntent,
    Direction,
    DropIntent,
    EquipIntent,
    PickupIntent,
    PlaceIntent,
    Position,
    WithdrawIntent,
    WriteNoteIntent,
)


def _world(**items: int) -> World:
    world = World(width=10, height=10)
    inventory = Inventory()
    for kind, count in items.items():
        inventory = inventory.add(kind, count)
    world.add_entity(
        Entity(entity_id="bob", position=Position(x=5, y=5), inventory=inventory)
    )
    return world


def _chest(world: World, position: Position, **contents: int) -> WorldObject:
    chest = WorldObject(
        object_id="chest_1",
        position=position,
        object_type="chest",
        state=(("owner", "bob"), ("contents", encode_contents(contents))),
    )
    world.add_object(chest)
    return chest


def _board(world: World, position: Position) -> WorldObject:
    board = WorldObject(
        object_id="board_1",
        position=position,
        object_type="message_board",
        state=(("owner", "bob"), ("notes", empty_notes_json())),
    )
    world.add_object(board)
    return board


class TestDropAndPickup:
    def test_drop_creates_pile(self) -> None:
        world = _world(wood=3)
        events = TickEvents()

        process_drop_phase(
            world, {"bob": DropIntent(entity_id="bob", kind="wood", amount=2)}, events
        )

        piles = [
            o for o in world.all_objects().values() if o.object_type == "item_pile"
        ]
        assert read_contents(piles[0]) == {"wood": 2}
        assert world.get_entity("bob").inventory.count("wood") == 1
        assert len(events.objects_added) == 1

    def test_drop_merges_into_existing_pile(self) -> None:
        world = _world(wood=2)
        events = TickEvents()
        add_items_to_ground(world, Position(x=5, y=5), {"stone": 1}, events)

        process_drop_phase(
            world, {"bob": DropIntent(entity_id="bob", kind="wood", amount=2)}, events
        )

        piles = [
            o for o in world.all_objects().values() if o.object_type == "item_pile"
        ]
        assert len(piles) == 1
        assert read_contents(piles[0]) == {"stone": 1, "wood": 2}

    def test_drop_more_than_held_fails(self) -> None:
        world = _world(wood=1)
        events = TickEvents()

        process_drop_phase(
            world, {"bob": DropIntent(entity_id="bob", kind="wood", amount=5)}, events
        )

        assert not events.action_results[0].success
        assert world.object_count() == 0

    def test_drop_unequips_when_last_one_goes(self) -> None:
        world = _world(axe=1)
        world.set_entity(world.get_entity("bob").with_wielded("axe"))
        events = TickEvents()

        process_drop_phase(
            world, {"bob": DropIntent(entity_id="bob", kind="axe", amount=1)}, events
        )

        assert world.get_entity("bob").wielded == ""

    def test_pickup_removes_emptied_pile(self) -> None:
        world = _world()
        events = TickEvents()
        add_items_to_ground(world, Position(x=5, y=5), {"wood": 2}, events)

        process_pickup_phase(
            world,
            {"bob": PickupIntent(entity_id="bob", kind="wood", amount=5)},
            events,
        )

        assert world.get_entity("bob").inventory.count("wood") == 2
        assert world.object_count() == 0
        assert len(events.objects_removed) == 1

    def test_pickup_without_pile_fails(self) -> None:
        world = _world()
        events = TickEvents()

        process_pickup_phase(
            world,
            {"bob": PickupIntent(entity_id="bob", kind="wood", amount=1)},
            events,
        )

        assert not events.action_results[0].success

    def test_pickup_conflict_lexicographic_order(self) -> None:
        world = _world()
        events = TickEvents()
        add_items_to_ground(world, Position(x=5, y=5), {"wood": 1}, events)
        # alice shares the tile only conceptually; pickup uses each entity's own
        # tile, so place her on the same position via a second world entry.
        world.add_entity(Entity(entity_id="alice", position=Position(x=5, y=6)))
        add_items_to_ground(world, Position(x=5, y=6), {"wood": 1}, events)

        process_pickup_phase(
            world,
            {
                "bob": PickupIntent(entity_id="bob", kind="wood", amount=1),
                "alice": PickupIntent(entity_id="alice", kind="wood", amount=1),
            },
            events,
        )

        assert world.get_entity("alice").inventory.count("wood") == 1
        assert world.get_entity("bob").inventory.count("wood") == 1


class TestChest:
    def test_deposit_and_withdraw_adjacent(self) -> None:
        world = _world(wood=4)
        _chest(world, Position(x=6, y=5))
        events = TickEvents()

        process_deposit_phase(
            world,
            {
                "bob": DepositIntent(
                    entity_id="bob", object_id="chest_1", kind="wood", amount=3
                )
            },
            events,
        )

        assert read_contents(world.get_object("chest_1")) == {"wood": 3}
        assert world.get_entity("bob").inventory.count("wood") == 1

        process_withdraw_phase(
            world,
            {
                "bob": WithdrawIntent(
                    entity_id="bob", object_id="chest_1", kind="wood", amount=2
                )
            },
            events,
        )

        assert read_contents(world.get_object("chest_1")) == {"wood": 1}
        assert world.get_entity("bob").inventory.count("wood") == 3

    def test_emptied_chest_is_kept(self) -> None:
        world = _world()
        _chest(world, Position(x=5, y=5), wood=1)
        events = TickEvents()

        process_withdraw_phase(
            world,
            {
                "bob": WithdrawIntent(
                    entity_id="bob", object_id="chest_1", kind="wood", amount=1
                )
            },
            events,
        )

        assert "chest_1" in world.all_objects()
        assert read_contents(world.get_object("chest_1")) == {}

    def test_distant_chest_fails(self) -> None:
        world = _world(wood=1)
        _chest(world, Position(x=9, y=9))
        events = TickEvents()

        process_deposit_phase(
            world,
            {
                "bob": DepositIntent(
                    entity_id="bob", object_id="chest_1", kind="wood", amount=1
                )
            },
            events,
        )

        assert not events.action_results[0].success
        assert world.get_entity("bob").inventory.count("wood") == 1

    def test_withdraw_missing_kind_fails(self) -> None:
        world = _world()
        _chest(world, Position(x=5, y=5))
        events = TickEvents()

        process_withdraw_phase(
            world,
            {
                "bob": WithdrawIntent(
                    entity_id="bob", object_id="chest_1", kind="wood", amount=1
                )
            },
            events,
        )

        assert not events.action_results[0].success


class TestEquip:
    def test_equip_and_unequip(self) -> None:
        world = _world(axe=1)
        events = TickEvents()

        process_equip_phase(
            world, {"bob": EquipIntent(entity_id="bob", kind="axe")}, events
        )
        assert world.get_entity("bob").wielded == "axe"
        assert world.get_entity("bob").inventory.count("axe") == 1

        process_equip_phase(
            world, {"bob": EquipIntent(entity_id="bob", kind="")}, events
        )
        assert world.get_entity("bob").wielded == ""

    def test_equip_missing_item_fails(self) -> None:
        world = _world()
        events = TickEvents()

        process_equip_phase(
            world, {"bob": EquipIntent(entity_id="bob", kind="sword")}, events
        )

        assert not events.action_results[0].success
        assert world.get_entity("bob").wielded == ""


class TestPlace:
    def test_place_chest_creates_object(self) -> None:
        world = _world(chest=1)
        events = TickEvents()

        process_place_phase(
            world,
            {
                "bob": PlaceIntent(
                    entity_id="bob", kind="chest", direction=Direction.EAST
                )
            },
            events,
        )

        placed = world.get_objects_at(Position(x=6, y=5))
        assert len(placed) == 1
        assert placed[0].object_type == "chest"
        assert placed[0].get_state("owner") == "bob"
        assert read_contents(placed[0]) == {}
        assert world.get_entity("bob").inventory.count("chest") == 0
        assert len(events.objects_added) == 1

    def test_place_board_starts_empty(self) -> None:
        world = _world(message_board=1)
        events = TickEvents()

        process_place_phase(
            world,
            {
                "bob": PlaceIntent(
                    entity_id="bob", kind="message_board", direction=Direction.NORTH
                )
            },
            events,
        )

        board = world.get_objects_at(Position(x=5, y=4))[0]
        assert read_notes(board) == [None] * BOARD_SLOTS

    def test_place_requires_inventory(self) -> None:
        world = _world()
        events = TickEvents()

        process_place_phase(
            world,
            {
                "bob": PlaceIntent(
                    entity_id="bob", kind="chest", direction=Direction.EAST
                )
            },
            events,
        )

        assert not events.action_results[0].success

    def test_place_rejects_unwalkable_occupied_and_busy_tiles(self) -> None:
        world = _world(chest=3)
        world.set_tile(Tile(position=Position(x=6, y=5), walkable=False))
        world.add_entity(Entity(entity_id="alice", position=Position(x=5, y=4)))
        world.add_object(
            WorldObject(
                object_id="bush_1", position=Position(x=4, y=5), object_type="bush"
            )
        )
        events = TickEvents()

        for direction in (Direction.EAST, Direction.NORTH, Direction.WEST):
            process_place_phase(
                world,
                {
                    "bob": PlaceIntent(
                        entity_id="bob", kind="chest", direction=direction
                    )
                },
                events,
            )

        assert [r.success for r in events.action_results] == [False, False, False]
        assert world.get_entity("bob").inventory.count("chest") == 3

    def test_place_out_of_bounds_fails(self) -> None:
        world = _world(chest=1)
        world.update_entity_position("bob", Position(x=0, y=0))
        events = TickEvents()

        process_place_phase(
            world,
            {
                "bob": PlaceIntent(
                    entity_id="bob", kind="chest", direction=Direction.WEST
                )
            },
            events,
        )

        assert not events.action_results[0].success

    def test_non_placeable_kind_fails(self) -> None:
        world = _world(wood=1)
        events = TickEvents()

        process_place_phase(
            world,
            {
                "bob": PlaceIntent(
                    entity_id="bob", kind="wood", direction=Direction.EAST
                )
            },
            events,
        )

        assert not events.action_results[0].success


class TestMessageBoard:
    def test_write_and_clear_slot(self) -> None:
        world = _world()
        _board(world, Position(x=6, y=5))
        world.tick = 7
        events = TickEvents()

        process_write_note_phase(
            world,
            {
                "bob": WriteNoteIntent(
                    entity_id="bob",
                    object_id="board_1",
                    slot=3,
                    title="plan",
                    text="gather wood",
                )
            },
            events,
        )

        notes = read_notes(world.get_object("board_1"))
        assert notes[3] == {
            "title": "plan",
            "text": "gather wood",
            "author": "bob",
            "tick": 7,
        }
        assert events.object_changes[-1].field == "notes"

        process_write_note_phase(
            world,
            {
                "bob": WriteNoteIntent(
                    entity_id="bob", object_id="board_1", slot=3, title="", text=""
                )
            },
            events,
        )

        assert read_notes(world.get_object("board_1"))[3] is None

    def test_slot_out_of_range_fails(self) -> None:
        world = _world()
        _board(world, Position(x=5, y=5))
        events = TickEvents()

        process_write_note_phase(
            world,
            {
                "bob": WriteNoteIntent(
                    entity_id="bob", object_id="board_1", slot=20, title="x", text="y"
                )
            },
            events,
        )

        assert not events.action_results[0].success

    def test_too_long_title_or_text_fails(self) -> None:
        world = _world()
        _board(world, Position(x=5, y=5))
        events = TickEvents()

        process_write_note_phase(
            world,
            {
                "bob": WriteNoteIntent(
                    entity_id="bob",
                    object_id="board_1",
                    slot=0,
                    title="x" * 61,
                    text="y",
                )
            },
            events,
        )
        process_write_note_phase(
            world,
            {
                "bob": WriteNoteIntent(
                    entity_id="bob",
                    object_id="board_1",
                    slot=0,
                    title="ok",
                    text="y" * 501,
                )
            },
            events,
        )

        assert [r.success for r in events.action_results] == [False, False]
        assert read_notes(world.get_object("board_1"))[0] is None

    def test_distant_board_fails(self) -> None:
        world = _world()
        _board(world, Position(x=0, y=0))
        events = TickEvents()

        process_write_note_phase(
            world,
            {
                "bob": WriteNoteIntent(
                    entity_id="bob", object_id="board_1", slot=0, title="a", text="b"
                )
            },
            events,
        )

        assert not events.action_results[0].success
