"""Tests for the building update: layers, blocking, dismantling and resting.

Contract: docs/08_building.md.
"""

import pytest

from world.containers import process_place_phase
from world.events import TickEvents
from world.foraging import process_extract_phase
from world.items import DISMANTLE_WORK, REST_HEAL
from world.movement import process_movement_phase
from world.settlement import is_free_walkable
from world.lease import LeaseManager
from world.services.observation_service import ObservationServiceServicer
from world.state import Entity, Inventory, Tile, World, WorldObject
from world.stats import process_rest_phase
from world.tick import TickContext, TickLoop, process_tick
from world.types import Direction, ExtractIntent, PlaceIntent, Position, RestIntent
from world.viewer_payload import object_from_state, object_state


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


def _place(world: World, kind: str, direction: Direction | None = None) -> TickEvents:
    events = TickEvents()
    process_place_phase(
        world,
        {"bob": PlaceIntent(entity_id="bob", kind=kind, direction=direction)},
        events,
    )
    return events


def _add_object(world: World, object_id: str, object_type: str, x: int, y: int) -> None:
    world.add_object(
        WorldObject(
            object_id=object_id,
            position=Position(x=x, y=y),
            object_type=object_type,
        )
    )


class TestPlacementLayers:
    def test_ground_kind_goes_on_the_placers_own_tile_without_a_direction(
        self,
    ) -> None:
        world = _world(road=1)

        events = _place(world, "road")

        assert events.action_results[0].success
        placed = world.get_objects_at(Position(x=5, y=5))
        assert [obj.object_type for obj in placed] == ["road"]
        assert placed[0].get_state("owner") == "bob"

    def test_structure_kind_without_a_direction_fails(self) -> None:
        world = _world(bed=1)

        events = _place(world, "bed")

        assert not events.action_results[0].success
        assert events.action_results[0].details == "bed needs a direction"
        assert world.get_entity("bob").inventory.count("bed") == 1

    def test_structure_may_stand_on_a_ground_object(self) -> None:
        world = _world(bed=1)
        _add_object(world, "road_1", "road", 6, 5)

        events = _place(world, "bed", Direction.EAST)

        assert events.action_results[0].success
        types = {obj.object_type for obj in world.get_objects_at(Position(x=6, y=5))}
        assert types == {"road", "bed"}

    def test_ground_object_may_go_under_a_structure(self) -> None:
        world = _world(road=1)
        _add_object(world, "chest_1", "chest", 6, 5)

        events = _place(world, "road", Direction.EAST)

        assert events.action_results[0].success

    def test_second_ground_object_on_a_tile_fails(self) -> None:
        world = _world(road=1)
        _add_object(world, "floor_1", "wood_floor", 5, 5)

        events = _place(world, "road")

        assert not events.action_results[0].success
        assert "ground object" in events.action_results[0].details

    def test_second_structure_on_a_tile_fails(self) -> None:
        world = _world(bed=1)
        _add_object(world, "chest_1", "chest", 6, 5)

        events = _place(world, "bed", Direction.EAST)

        assert not events.action_results[0].success

    @pytest.mark.parametrize(
        "natural_type", ["tree", "rock_small", "bush", "reeds", "clay_deposit"]
    )
    def test_ground_kind_cannot_cover_a_natural_object(self, natural_type: str) -> None:
        world = _world(road=1)
        _add_object(world, "natural_1", natural_type, 5, 5)

        events = _place(world, "road")

        assert not events.action_results[0].success
        assert "natural object" in events.action_results[0].details

    def test_structure_needs_a_tile_free_of_entities(self) -> None:
        world = _world(bed=1)
        world.add_entity(Entity(entity_id="alice", position=Position(x=6, y=5)))

        events = _place(world, "bed", Direction.EAST)

        assert not events.action_results[0].success
        assert "entity" in events.action_results[0].details

    def test_ground_kind_may_be_laid_under_a_standing_entity(self) -> None:
        world = _world(road=1)
        world.add_entity(Entity(entity_id="alice", position=Position(x=6, y=5)))

        events = _place(world, "road", Direction.EAST)

        assert events.action_results[0].success

    def test_building_objects_carry_an_owner_but_no_notes(self) -> None:
        world = _world(wood_wall=1)

        _place(world, "wood_wall", Direction.EAST)

        wall = world.get_objects_at(Position(x=6, y=5))[0]
        assert wall.get_state("owner") == "bob"
        assert dict(wall.state) == {"owner": "bob"}

    def test_unwalkable_terrain_rejects_a_placement(self) -> None:
        world = _world(road=1)
        world.set_tile(Tile(position=Position(x=6, y=5), walkable=False))

        events = _place(world, "road", Direction.EAST)

        assert not events.action_results[0].success


class TestBlockingIndex:
    def test_walls_block_everyone(self) -> None:
        world = World(width=10, height=10)
        _add_object(world, "wall_1", "wood_wall", 3, 3)

        assert world.is_blocked(Position(x=3, y=3))
        assert world.is_blocked(Position(x=3, y=3), "wolf")
        assert not world.is_passable(Position(x=3, y=3))

    def test_doors_block_wolves_only(self) -> None:
        world = World(width=10, height=10)
        _add_object(world, "door_1", "door", 3, 3)

        assert not world.is_blocked(Position(x=3, y=3))
        assert world.is_blocked(Position(x=3, y=3), "wolf")
        assert world.is_passable(Position(x=3, y=3))
        assert not world.is_passable(Position(x=3, y=3), "wolf")

    @pytest.mark.parametrize("object_type", ["tree", "bush", "chest", "bed", "road"])
    def test_ordinary_objects_never_block(self, object_type: str) -> None:
        world = World(width=10, height=10)
        _add_object(world, "obj_1", object_type, 3, 3)

        assert not world.is_blocked(Position(x=3, y=3))
        assert not world.is_blocked(Position(x=3, y=3), "wolf")

    def test_removing_a_wall_unblocks_its_tile(self) -> None:
        world = World(width=10, height=10)
        _add_object(world, "wall_1", "wood_wall", 3, 3)

        world.remove_object("wall_1")

        assert not world.is_blocked(Position(x=3, y=3))

    def test_two_blockers_on_a_tile_survive_one_removal(self) -> None:
        world = World(width=10, height=10)
        _add_object(world, "wall_1", "wood_wall", 3, 3)
        _add_object(world, "door_1", "door", 3, 3)

        world.remove_object("door_1")

        assert world.is_blocked(Position(x=3, y=3))

    def test_placing_a_wall_updates_the_index(self) -> None:
        world = _world(wood_wall=1)

        _place(world, "wood_wall", Direction.EAST)

        assert world.is_blocked(Position(x=6, y=5))


class TestBlockingMovement:
    def test_entity_cannot_walk_into_a_wall(self) -> None:
        world = _world()
        _add_object(world, "wall_1", "wood_wall", 6, 5)

        results = process_movement_phase(world, {"bob": Direction.EAST})

        assert results == []
        assert world.get_entity("bob").position == Position(x=5, y=5)

    def test_entity_walks_through_a_door(self) -> None:
        world = _world()
        _add_object(world, "door_1", "door", 6, 5)

        results = process_movement_phase(world, {"bob": Direction.EAST})

        assert results[0].success
        assert world.get_entity("bob").position == Position(x=6, y=5)

    def test_wolf_cannot_walk_through_a_door(self) -> None:
        world = World(width=10, height=10)
        world.add_entity(
            Entity(entity_id="wolf_1", position=Position(x=5, y=5), entity_type="wolf")
        )
        _add_object(world, "door_1", "door", 6, 5)

        results = process_movement_phase(world, {"wolf_1": Direction.EAST})

        assert results == []
        assert world.get_entity("wolf_1").position == Position(x=5, y=5)

    def test_wall_corner_blocks_the_diagonal_step(self) -> None:
        world = _world()
        _add_object(world, "wall_1", "wood_wall", 5, 4)
        _add_object(world, "wall_2", "wood_wall", 6, 5)

        results = process_movement_phase(world, {"bob": Direction.NORTHEAST})

        assert results == []

    def test_door_corner_blocks_only_the_wolfs_diagonal_step(self) -> None:
        world = World(width=10, height=10)
        world.add_entity(
            Entity(entity_id="wolf_1", position=Position(x=5, y=5), entity_type="wolf")
        )
        world.add_entity(Entity(entity_id="bob", position=Position(x=1, y=1)))
        _add_object(world, "door_1", "door", 5, 4)
        _add_object(world, "door_2", "door", 6, 5)
        _add_object(world, "door_3", "door", 1, 0)
        _add_object(world, "door_4", "door", 2, 1)

        results = {
            result.entity_id: result
            for result in process_movement_phase(
                world, {"wolf_1": Direction.NORTHEAST, "bob": Direction.NORTHEAST}
            )
        }

        assert "wolf_1" not in results
        assert results["bob"].success


class TestBlockingPlacement:
    def test_free_walkable_rejects_a_wall_tile(self) -> None:
        world = World(width=10, height=10)
        _add_object(world, "wall_1", "wood_wall", 3, 3)

        assert not is_free_walkable(world, Position(x=3, y=3))

    def test_free_walkable_allows_a_door_tile_for_settlers_only(self) -> None:
        world = World(width=10, height=10)
        _add_object(world, "door_1", "door", 3, 3)

        assert is_free_walkable(world, Position(x=3, y=3))
        assert not is_free_walkable(world, Position(x=3, y=3), "wolf")


class TestDismantling:
    def _dismantle(self, world: World, object_id: str, *entity_ids: str) -> TickEvents:
        events = TickEvents()
        process_extract_phase(
            world,
            {
                entity_id: ExtractIntent(entity_id=entity_id, object_id=object_id)
                for entity_id in entity_ids
            },
            events,
        )
        return events

    def test_dismantling_takes_three_actions_and_returns_the_item(self) -> None:
        world = _world()
        _add_object(world, "bed_1", "bed", 6, 5)

        for _ in range(DISMANTLE_WORK - 1):
            events = self._dismantle(world, "bed_1", "bob")
            assert events.action_results[0].success
            assert "bed_1" in world.all_objects()

        events = self._dismantle(world, "bed_1", "bob")

        assert "bed_1" not in world.all_objects()
        assert world.get_entity("bob").inventory.count("bed") == 1
        assert [e.object_id for e in events.objects_removed] == ["bed_1"]
        assert events.action_results[0].details == "dismantled bed_1 (+1 bed)"

    def test_partial_dismantle_records_progress_state(self) -> None:
        world = _world()
        _add_object(world, "wall_1", "wood_wall", 6, 5)

        events = self._dismantle(world, "wall_1", "bob")

        assert world.get_object("wall_1").get_state("progress") == "1"
        assert events.action_results[0].details == (
            f"dismantling wall_1 (1/{DISMANTLE_WORK})"
        )
        assert events.object_changes[0].field == "progress"

    def test_dismantling_a_wall_unblocks_its_tile(self) -> None:
        world = _world()
        _add_object(world, "wall_1", "wood_wall", 6, 5)

        for _ in range(DISMANTLE_WORK):
            self._dismantle(world, "wall_1", "bob")

        assert not world.is_blocked(Position(x=6, y=5))

    def test_tools_do_not_speed_dismantling_up(self) -> None:
        world = _world(axe=1)
        world.set_entity(world.get_entity("bob").with_wielded("axe"))
        _add_object(world, "wall_1", "wood_wall", 6, 5)

        self._dismantle(world, "wall_1", "bob")

        assert world.get_object("wall_1").get_state("progress") == "1"

    def test_anyone_may_dismantle_and_the_finisher_gets_the_item(self) -> None:
        world = _world()
        world.add_entity(Entity(entity_id="alice", position=Position(x=7, y=5)))
        world.add_entity(Entity(entity_id="carol", position=Position(x=6, y=6)))
        _add_object(world, "chair_1", "chair", 6, 5)

        events = self._dismantle(world, "chair_1", "bob", "alice", "carol")

        assert "chair_1" not in world.all_objects()
        # Work lands in lexicographic id order, so carol swings third.
        assert world.get_entity("carol").inventory.count("chair") == 1
        assert [r.entity_id for r in events.action_results] == [
            "alice",
            "bob",
            "carol",
        ]

    def test_dismantling_a_distant_object_fails(self) -> None:
        world = _world()
        _add_object(world, "bed_1", "bed", 9, 9)

        events = self._dismantle(world, "bed_1", "bob")

        assert not events.action_results[0].success
        assert "not adjacent" in events.action_results[0].details

    def test_chests_and_boards_cannot_be_dismantled(self) -> None:
        world = _world()
        _add_object(world, "chest_1", "chest", 6, 5)

        events = self._dismantle(world, "chest_1", "bob")

        assert not events.action_results[0].success
        assert "cannot be extracted" in events.action_results[0].details
        assert "chest_1" in world.all_objects()


class TestResting:
    def _rest(self, world: World, object_id: str, *entity_ids: str) -> TickEvents:
        events = TickEvents()
        process_rest_phase(
            world,
            {
                entity_id: RestIntent(entity_id=entity_id, object_id=object_id)
                for entity_id in entity_ids
            },
            events,
        )
        return events

    def test_resting_on_an_adjacent_bed_heals(self) -> None:
        world = _world()
        world.set_entity(world.get_entity("bob").with_health(10))
        _add_object(world, "bed_1", "bed", 6, 5)

        events = self._rest(world, "bed_1", "bob")

        assert events.action_results[0].success
        assert world.get_entity("bob").health == 10 + REST_HEAL
        assert events.action_results[0].details == (
            f"rested at bed_1 (+{REST_HEAL} health)"
        )

    def test_resting_on_ones_own_tile_works(self) -> None:
        world = _world()
        world.set_entity(world.get_entity("bob").with_health(10))
        _add_object(world, "bed_1", "bed", 5, 5)

        events = self._rest(world, "bed_1", "bob")

        assert events.action_results[0].success

    def test_healing_is_capped_at_max_health(self) -> None:
        world = _world()
        bob = world.get_entity("bob")
        world.set_entity(bob.with_health(bob.max_health - 1))
        _add_object(world, "bed_1", "bed", 6, 5)

        events = self._rest(world, "bed_1", "bob")

        assert world.get_entity("bob").health == bob.max_health
        assert events.action_results[0].details == "rested at bed_1 (+1 health)"

    def test_only_the_smallest_entity_id_gets_the_bed(self) -> None:
        world = _world()
        world.add_entity(Entity(entity_id="alice", position=Position(x=7, y=5)))
        world.set_entity(world.get_entity("bob").with_health(5))
        world.set_entity(world.get_entity("alice").with_health(5))
        _add_object(world, "bed_1", "bed", 6, 5)

        events = self._rest(world, "bed_1", "alice", "bob")

        outcomes = {r.entity_id: r for r in events.action_results}
        assert outcomes["alice"].success
        assert not outcomes["bob"].success
        assert outcomes["bob"].details == "bed_1 is taken"
        assert world.get_entity("bob").health == 5

    def test_resting_far_from_the_bed_fails(self) -> None:
        world = _world()
        _add_object(world, "bed_1", "bed", 9, 9)

        events = self._rest(world, "bed_1", "bob")

        assert not events.action_results[0].success
        assert "not adjacent" in events.action_results[0].details

    def test_resting_on_a_non_bed_fails(self) -> None:
        world = _world()
        _add_object(world, "chair_1", "chair", 6, 5)

        events = self._rest(world, "chair_1", "bob")

        assert not events.action_results[0].success
        assert events.action_results[0].details == "chair_1 is not a bed"

    def test_resting_with_no_hunger_left_fails(self) -> None:
        world = _world()
        world.set_entity(world.get_entity("bob").with_hunger(0).with_health(5))
        _add_object(world, "bed_1", "bed", 6, 5)

        events = self._rest(world, "bed_1", "bob")

        assert not events.action_results[0].success
        assert events.action_results[0].details == "too hungry to rest"
        assert world.get_entity("bob").health == 5

    def test_resting_on_a_missing_object_fails(self) -> None:
        world = _world()

        events = self._rest(world, "bed_9", "bob")

        assert not events.action_results[0].success
        assert events.action_results[0].details == "no object bed_9"


class TestObservedTiles:
    def _tiles(self, world: World) -> dict[tuple[int, int], bool]:
        service = ObservationServiceServicer(world, TickLoop(world), LeaseManager())
        observation = service._generate_observation(
            "bob", TickContext(tick_id=1, start_time_ms=0, deadline_ms=1000)
        )
        assert observation is not None
        return {
            (tile.position.x, tile.position.y): tile.walkable
            for tile in observation.visible_tiles
        }

    def test_wall_tiles_are_reported_unwalkable(self) -> None:
        world = _world()
        _add_object(world, "wall_1", "wood_wall", 6, 5)

        assert self._tiles(world)[(6, 5)] is False

    def test_door_tiles_stay_walkable(self) -> None:
        world = _world()
        _add_object(world, "door_1", "door", 6, 5)

        assert self._tiles(world)[(6, 5)] is True

    def test_plain_tiles_are_unaffected(self) -> None:
        world = _world()

        assert self._tiles(world)[(6, 5)] is True


class TestRestPhaseWiring:
    def test_rest_intent_heals_through_the_tick_pipeline(self) -> None:
        world = _world()
        world.set_entity(world.get_entity("bob").with_health(10))
        _add_object(world, "bed_1", "bed", 6, 5)
        # Tick 1 runs neither hunger nor health regeneration, so the health
        # change below is the rest alone.
        world.tick = 1
        ctx = TickContext(
            tick_id=world.tick, start_time_ms=0, deadline_ms=0, world=world
        )
        ctx.submit_rest_intent(
            RestIntent(entity_id="bob", object_id="bed_1"), enforce_deadline=False
        )

        result = process_tick(world, ctx)

        assert ("bob", "rest", True) in {
            (r.entity_id, r.action_type, r.success) for r in result.action_results
        }
        assert world.get_entity("bob").health == 10 + REST_HEAL


class TestViewerPayload:
    @pytest.mark.parametrize(
        "object_type", ["wood_wall", "door", "road", "bed", "reeds", "clay_deposit"]
    )
    def test_new_object_types_round_trip_unchanged(self, object_type: str) -> None:
        obj = WorldObject(
            object_id="x_1",
            position=Position(x=2, y=3),
            object_type=object_type,
            state=(("owner", "bob"),),
        )

        assert object_from_state(object_state(obj)) == obj
