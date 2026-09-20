"""Tests for signs: crafting, placing, writing, blocking and dismantling.

Contract: docs/08_building.md, "Signs".
"""

from world.containers import process_place_phase, process_write_note_phase
from world.crafting import process_craft_phase
from world.events import TickEvents
from world.foraging import process_extract_phase
from world.items import DISMANTLE_WORK, SIGN, SIGN_TEXT_MAX
from world.movement import process_movement_phase
from world.state import Entity, Inventory, World, WorldObject
from world.types import (
    CraftIntent,
    Direction,
    ExtractIntent,
    PlaceIntent,
    Position,
    WriteNoteIntent,
)

WOLF = "wolf"


def _world(**items: int) -> World:
    """10x10 world with `bob` at (5,5) holding `items`."""
    world = World(width=10, height=10)
    inventory = Inventory()
    for kind, count in items.items():
        inventory = inventory.add(kind, count)
    world.add_entity(
        Entity(entity_id="bob", position=Position(x=5, y=5), inventory=inventory)
    )
    return world


def _place(world: World, direction: Direction | None = Direction.EAST) -> TickEvents:
    events = TickEvents()
    process_place_phase(
        world,
        {"bob": PlaceIntent(entity_id="bob", kind=SIGN, direction=direction)},
        events,
    )
    return events


def _write(
    world: World,
    entity_id: str,
    object_id: str,
    text: str,
    slot: int = 0,
    title: str = "",
) -> TickEvents:
    events = TickEvents()
    process_write_note_phase(
        world,
        {
            entity_id: WriteNoteIntent(
                entity_id=entity_id,
                object_id=object_id,
                slot=slot,
                title=title,
                text=text,
            )
        },
        events,
    )
    return events


def _placed_sign(world: World) -> WorldObject:
    signs = [obj for obj in world.all_objects().values() if obj.object_type == SIGN]
    assert len(signs) == 1
    return signs[0]


class TestCraftingASign:
    def test_two_wood_makes_a_sign_by_hand(self) -> None:
        world = _world(wood=2)
        events = TickEvents()

        process_craft_phase(
            world, {"bob": CraftIntent(entity_id="bob", recipe=SIGN)}, events
        )

        assert events.action_results[0].success
        assert world.get_entity("bob").inventory.count(SIGN) == 1
        assert world.get_entity("bob").inventory.count("wood") == 0

    def test_one_wood_is_not_enough(self) -> None:
        world = _world(wood=1)
        events = TickEvents()

        process_craft_phase(
            world, {"bob": CraftIntent(entity_id="bob", recipe=SIGN)}, events
        )

        assert not events.action_results[0].success


class TestPlacingASign:
    def test_a_sign_is_a_structure_and_starts_blank(self) -> None:
        world = _world(sign=1)

        events = _place(world)

        assert events.action_results[0].success
        sign = _placed_sign(world)
        assert sign.position == Position(x=6, y=5)
        assert sign.get_state("owner") == "bob"
        assert sign.get_state("text") == ""
        assert sign.get_state("author") == ""

    def test_a_sign_needs_a_direction(self) -> None:
        world = _world(sign=1)

        events = _place(world, direction=None)

        assert not events.action_results[0].success
        assert "needs a direction" in events.action_results[0].details

    def test_a_road_may_be_laid_under_a_sign(self) -> None:
        world = _world(sign=1, road=1)
        _place(world)
        world.update_entity_position("bob", Position(x=6, y=5))

        events = TickEvents()
        process_place_phase(
            world,
            {"bob": PlaceIntent(entity_id="bob", kind="road", direction=None)},
            events,
        )

        assert events.action_results[0].success

    def test_a_sign_blocks_neither_settlers_nor_wolves(self) -> None:
        world = _world(sign=1)
        _place(world)
        world.add_entity(
            Entity(
                entity_id="wolf_1",
                position=Position(x=7, y=5),
                entity_type=WOLF,
            )
        )

        assert world.is_passable(Position(x=6, y=5), "player")
        assert world.is_passable(Position(x=6, y=5), WOLF)

        results = process_movement_phase(world, {"bob": Direction.EAST})

        assert results[0].success
        assert world.get_entity("bob").position == Position(x=6, y=5)


class TestWritingASign:
    def _standing_sign(self) -> World:
        world = _world(sign=1)
        _place(world)
        return world

    def test_writing_from_an_adjacent_tile_records_text_author_and_tick(self) -> None:
        world = self._standing_sign()
        world.tick = 42

        events = _write(world, "bob", _placed_sign(world).object_id, "Wolves to the N")

        assert events.action_results[0].success
        sign = _placed_sign(world)
        assert sign.get_state("text") == "Wolves to the N"
        assert sign.get_state("author") == "bob"
        assert sign.get_state("tick") == "42"
        assert {change.field for change in events.object_changes} == {
            "text",
            "author",
            "tick",
        }

    def test_writing_from_the_signs_own_tile_works(self) -> None:
        world = self._standing_sign()
        world.update_entity_position("bob", Position(x=6, y=5))

        events = _write(world, "bob", _placed_sign(world).object_id, "here")

        assert events.action_results[0].success

    def test_anyone_may_rewrite_a_sign(self) -> None:
        world = self._standing_sign()
        sign_id = _placed_sign(world).object_id
        _write(world, "bob", sign_id, "first")
        world.add_entity(Entity(entity_id="ada", position=Position(x=7, y=5)))

        events = _write(world, "ada", sign_id, "second")

        assert events.action_results[0].success
        assert _placed_sign(world).get_state("text") == "second"
        assert _placed_sign(world).get_state("author") == "ada"

    def test_empty_text_blanks_the_sign(self) -> None:
        world = self._standing_sign()
        sign_id = _placed_sign(world).object_id
        _write(world, "bob", sign_id, "first")

        events = _write(world, "bob", sign_id, "")

        assert events.action_results[0].success
        assert events.action_results[0].details == f"cleared {sign_id}"
        sign = _placed_sign(world)
        assert sign.get_state("text") == ""
        assert sign.get_state("author") == ""
        assert sign.get_state("tick") == ""

    def test_text_over_the_limit_is_refused_not_truncated(self) -> None:
        world = self._standing_sign()
        sign_id = _placed_sign(world).object_id

        events = _write(world, "bob", sign_id, "x" * (SIGN_TEXT_MAX + 1))

        assert not events.action_results[0].success
        assert f"at most {SIGN_TEXT_MAX} characters" in events.action_results[0].details
        assert _placed_sign(world).get_state("text") == ""

    def test_text_at_the_limit_is_accepted(self) -> None:
        world = self._standing_sign()

        events = _write(
            world, "bob", _placed_sign(world).object_id, "x" * SIGN_TEXT_MAX
        )

        assert events.action_results[0].success

    def test_a_slot_other_than_zero_is_refused(self) -> None:
        world = self._standing_sign()

        events = _write(world, "bob", _placed_sign(world).object_id, "hi", slot=3)

        assert not events.action_results[0].success
        assert "one slot" in events.action_results[0].details

    def test_the_title_is_ignored(self) -> None:
        world = self._standing_sign()

        events = _write(
            world, "bob", _placed_sign(world).object_id, "hi", title="a title"
        )

        assert events.action_results[0].success
        assert _placed_sign(world).get_state("text") == "hi"

    def test_writing_from_too_far_away_fails(self) -> None:
        world = self._standing_sign()
        sign_id = _placed_sign(world).object_id
        world.update_entity_position("bob", Position(x=1, y=1))

        events = _write(world, "bob", sign_id, "hi")

        assert not events.action_results[0].success
        assert "not adjacent" in events.action_results[0].details


class TestDismantlingASign:
    def test_three_extract_actions_return_the_item(self) -> None:
        world = _world(sign=1)
        _place(world)
        sign_id = _placed_sign(world).object_id

        for _ in range(DISMANTLE_WORK):
            events = TickEvents()
            process_extract_phase(
                world,
                {"bob": ExtractIntent(entity_id="bob", object_id=sign_id)},
                events,
            )
            assert events.action_results[0].success

        assert sign_id not in world.all_objects()
        assert world.get_entity("bob").inventory.count(SIGN) == 1
