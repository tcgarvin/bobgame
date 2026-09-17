"""Tests for settlement site selection."""

import numpy as np
import pytest

from world.settlement import (
    CLEARING_SIZE,
    MINIMUMS,
    NoSettlementSiteError,
    find_settlement_site,
    nearest_free_walkable,
    resource_counts,
)
from world.state import Entity, World, WorldObject
from world.types import Position

GRASS = 3
SAND = 2
SHALLOW_WATER = 1
MOUNTAIN = 5

SIZE = 200

# Where each resource block sits relative to the site centre: all inside the
# settlement radius, all outside the central clearing.
_BLOCK_OFFSETS = {
    "tree": (0, -20),
    "rock_medium": (0, 20),
    "bush": (20, 0),
    "reeds": (-20, -8),
    "clay_deposit": (-20, 8),
}
_DEFAULT_SUPPLY = {
    "tree": 150,
    "rock_medium": 70,
    "bush": 20,
    "reeds": 35,
    "clay_deposit": 20,
}


def _make_world(floor_value: int = GRASS) -> World:
    world = World(width=SIZE, height=SIZE)
    world.set_floor_array(np.full((SIZE, SIZE), floor_value, dtype=np.uint8))
    return world


def _block(
    centre: Position, object_type: str, count: int, prefix: str
) -> list[WorldObject]:
    """Place `count` objects on distinct tiles in a 15-wide block around centre."""
    objects: list[WorldObject] = []
    for index in range(count):
        dx, dy = index % 15 - 7, index // 15 - 5
        objects.append(
            WorldObject(
                object_id=f"{prefix}_{object_type}_{index}",
                position=Position(x=centre.x + dx, y=centre.y + dy),
                object_type=object_type,
            )
        )
    return objects


def _supplied_site(world: World, centre: Position, **supply: int) -> list[WorldObject]:
    """Surround centre with qualifying resources and one water tile."""
    floor = world._floor_array
    assert floor is not None
    floor[centre.y, centre.x - 28] = SHALLOW_WATER

    objects: list[WorldObject] = []
    for object_type, (dx, dy) in _BLOCK_OFFSETS.items():
        count = supply.get(object_type, _DEFAULT_SUPPLY[object_type])
        block_centre = Position(x=centre.x + dx, y=centre.y + dy)
        objects += _block(block_centre, object_type, count, f"s{centre.x}")
    return objects


class TestFindSettlementSite:
    def test_finds_a_site_with_every_minimum_met(self) -> None:
        world = _make_world()
        centre = Position(x=100, y=100)
        objects = _supplied_site(world, centre)

        site = find_settlement_site(world, objects)

        counts = resource_counts(world, objects, site)
        for category, minimum in MINIMUMS.items():
            assert counts[category] >= minimum, category

    @pytest.mark.parametrize("missing", sorted(_BLOCK_OFFSETS))
    def test_every_resource_is_required(self, missing: str) -> None:
        world = _make_world()
        objects = _supplied_site(world, Position(x=100, y=100), **{missing: 3})

        with pytest.raises(NoSettlementSiteError):
            find_settlement_site(world, objects)

    def test_site_is_walkable_grass_or_dirt(self) -> None:
        world = _make_world(SAND)
        floor = world._floor_array
        assert floor is not None
        # Only a patch of grass, everything else sand: the site must land there.
        floor[90:111, 90:111] = GRASS
        objects = _supplied_site(world, Position(x=100, y=100))

        site = find_settlement_site(world, objects)

        tile = world.get_tile(site)
        assert tile.floor_type in ("grass", "dirt")
        assert 90 <= site.x <= 110 and 90 <= site.y <= 110

    def test_site_has_a_clear_building_area(self) -> None:
        world = _make_world()
        objects = _supplied_site(world, Position(x=100, y=100))

        site = find_settlement_site(world, objects)

        half = CLEARING_SIZE // 2
        cluttered = sum(
            1
            for obj in objects
            if abs(obj.position.x - site.x) <= half
            and abs(obj.position.y - site.y) <= half
        )
        assert cluttered <= 0.1 * CLEARING_SIZE**2

    def test_rejects_a_site_with_no_room_to_build(self) -> None:
        world = _make_world()
        centre = Position(x=100, y=100)
        objects = _supplied_site(world, centre)
        # Fill every gap between the resource blocks with stray trees, so no
        # 15 x 15 square anywhere near the supply is clear.
        taken = {(obj.position.x, obj.position.y) for obj in objects}
        index = 0
        for y in range(60, 141, 2):
            for x in range(60, 141, 2):
                if (x, y) not in taken:
                    objects.append(
                        WorldObject(
                            object_id=f"stray_{index}",
                            position=Position(x=x, y=y),
                            object_type="tree",
                        )
                    )
                    index += 1

        with pytest.raises(NoSettlementSiteError):
            find_settlement_site(world, objects)

    def test_rejects_a_site_that_is_mostly_water(self) -> None:
        world = _make_world()
        centre = Position(x=100, y=100)
        objects = _supplied_site(world, centre)
        floor = world._floor_array
        assert floor is not None
        floor[:, : centre.x - 9] = SHALLOW_WATER
        floor[: centre.y - 9, :] = SHALLOW_WATER

        with pytest.raises(NoSettlementSiteError):
            find_settlement_site(world, objects)

    def test_deterministic_for_the_same_map(self) -> None:
        world = _make_world()
        objects = _supplied_site(world, Position(x=50, y=50))
        objects += _supplied_site(world, Position(x=150, y=150))

        first = find_settlement_site(world, objects)
        second = find_settlement_site(world, objects)

        assert first == second

    def test_prefers_the_balanced_site(self) -> None:
        world = _make_world()
        lean = Position(x=50, y=50)
        balanced = Position(x=150, y=150)
        # Drowning in trees but only just enough of everything else.
        objects = _supplied_site(
            world, lean, tree=165, rock_medium=50, bush=12, reeds=25, clay_deposit=12
        )
        objects += _supplied_site(
            world, balanced, rock_medium=100, bush=24, reeds=50, clay_deposit=24
        )

        site = find_settlement_site(world, objects)

        assert abs(site.x - balanced.x) <= 20
        assert abs(site.y - balanced.y) <= 20

    def test_requires_water_nearby(self) -> None:
        world = _make_world()
        centre = Position(x=100, y=100)
        objects = _supplied_site(world, centre)
        # Remove the water tile that _supplied_site added.
        floor = world._floor_array
        assert floor is not None
        floor[centre.y, centre.x - 28] = GRASS

        with pytest.raises(NoSettlementSiteError):
            find_settlement_site(world, objects)

    def test_raises_when_resources_are_missing(self) -> None:
        world = _make_world()
        with pytest.raises(NoSettlementSiteError):
            find_settlement_site(world, [])


class TestNearestFreeWalkable:
    def test_returns_the_centre_when_free(self) -> None:
        world = _make_world()
        centre = Position(x=10, y=10)
        assert nearest_free_walkable(world, centre) == centre

    def test_skips_occupied_tiles(self) -> None:
        world = _make_world()
        centre = Position(x=10, y=10)
        world.add_entity(Entity(entity_id="a", position=centre))

        spot = nearest_free_walkable(world, centre)

        assert spot != centre
        assert max(abs(spot.x - centre.x), abs(spot.y - centre.y)) == 1

    def test_skips_blocking_objects(self) -> None:
        world = _make_world()
        centre = Position(x=10, y=10)
        world.add_object(
            WorldObject(object_id="t1", position=centre, object_type="tree")
        )

        assert nearest_free_walkable(world, centre) != centre

    def test_allows_non_blocking_objects(self) -> None:
        world = _make_world()
        centre = Position(x=10, y=10)
        world.add_object(
            WorldObject(object_id="b1", position=centre, object_type="bush")
        )

        assert nearest_free_walkable(world, centre) == centre

    def test_skips_unwalkable_terrain(self) -> None:
        world = _make_world()
        floor = world._floor_array
        assert floor is not None
        floor[10, 10] = MOUNTAIN

        assert nearest_free_walkable(world, Position(x=10, y=10)) != Position(
            x=10, y=10
        )

    def test_spawns_twelve_entities_without_overlap(self) -> None:
        world = _make_world()
        centre = Position(x=100, y=100)
        placed = []
        for i in range(12):
            position = nearest_free_walkable(world, centre)
            world.add_entity(Entity(entity_id=f"e{i}", position=position))
            placed.append(position)

        assert len(set(placed)) == 12
        for position in placed:
            assert max(abs(position.x - centre.x), abs(position.y - centre.y)) <= 2

    def test_raises_when_nothing_is_free(self) -> None:
        world = World(width=3, height=3)
        world.set_floor_array(np.full((3, 3), MOUNTAIN, dtype=np.uint8))

        with pytest.raises(NoSettlementSiteError):
            nearest_free_walkable(world, Position(x=1, y=1), max_radius=2)
