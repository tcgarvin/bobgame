"""Tests for settlement site selection."""

import numpy as np
import pytest

from world.settlement import (
    MIN_BUSHES,
    MIN_ROCKS,
    MIN_TREES,
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


def _make_world(floor_value: int = GRASS) -> World:
    world = World(width=SIZE, height=SIZE)
    world.set_floor_array(np.full((SIZE, SIZE), floor_value, dtype=np.uint8))
    return world


def _cluster(
    centre: Position,
    object_type: str,
    count: int,
    prefix: str,
    spread: int = 10,
) -> list[WorldObject]:
    """Place `count` objects on distinct tiles around `centre`."""
    objects: list[WorldObject] = []
    index = 0
    for dy in range(-spread, spread + 1):
        for dx in range(-spread, spread + 1):
            if len(objects) >= count:
                return objects
            # Skip the centre tile so it stays free for spawning.
            if dx == 0 and dy == 0:
                continue
            objects.append(
                WorldObject(
                    object_id=f"{prefix}_{index}",
                    position=Position(x=centre.x + dx, y=centre.y + dy),
                    object_type=object_type,
                )
            )
            index += 1
    return objects


def _supplied_site(
    world: World,
    centre: Position,
    trees: int = 20,
    rocks: int = 20,
    bushes: int = 12,
) -> list[WorldObject]:
    """Put a qualifying resource cluster and a water tile around centre."""
    floor = world._floor_array
    assert floor is not None
    floor[centre.y, centre.x - 20] = SHALLOW_WATER

    objects = []
    objects += _cluster(
        Position(x=centre.x, y=centre.y - 12), "tree", trees, f"tree{centre.x}"
    )
    objects += _cluster(
        Position(x=centre.x, y=centre.y + 12), "rock_medium", rocks, f"rock{centre.x}"
    )
    objects += _cluster(
        Position(x=centre.x + 12, y=centre.y), "bush", bushes, f"bush{centre.x}"
    )
    return objects


class TestFindSettlementSite:
    def test_finds_the_supplied_site(self) -> None:
        world = _make_world()
        centre = Position(x=100, y=100)
        objects = _supplied_site(world, centre)

        site = find_settlement_site(world, objects)

        # The site has to sit within reach of the one supplied cluster.
        assert abs(site.x - centre.x) <= 45
        assert abs(site.y - centre.y) <= 45
        counts = resource_counts(world, objects, site)
        assert counts["tree"] >= MIN_TREES
        assert counts["rock"] >= MIN_ROCKS
        assert counts["bush"] >= MIN_BUSHES

    def test_site_is_walkable_grass_or_dirt(self) -> None:
        world = _make_world(SAND)
        floor = world._floor_array
        assert floor is not None
        # Only a patch of grass, everything else sand: the site must land there.
        floor[90:120, 90:120] = GRASS
        objects = _supplied_site(world, Position(x=104, y=104))

        site = find_settlement_site(world, objects)

        tile = world.get_tile(site)
        assert tile.floor_type in ("grass", "dirt")
        assert tile.walkable

    def test_deterministic_for_the_same_map(self) -> None:
        world = _make_world()
        objects = _supplied_site(world, Position(x=60, y=60))
        objects += _supplied_site(world, Position(x=150, y=150))

        first = find_settlement_site(world, objects)
        second = find_settlement_site(world, objects)

        assert first == second

    def test_prefers_the_balanced_site(self) -> None:
        world = _make_world()
        lean = Position(x=50, y=50)
        balanced = Position(x=150, y=150)
        # Plenty of trees but barely enough rocks/bushes.
        objects = _supplied_site(world, lean, trees=200, rocks=15, bushes=8)
        objects += _supplied_site(world, balanced, trees=40, rocks=40, bushes=24)

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
        floor[centre.y, centre.x - 20] = GRASS

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
