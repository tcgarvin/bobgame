"""Tests for settlement site selection."""

import numpy as np
import pytest

from world.settlement import (
    CLEARING_SIZE,
    MINIMUMS,
    MIN_COPPER_VEINS,
    MIN_IRON_VEINS,
    ORE_EXCLUSION_RADIUS,
    ORE_SEARCH_RADIUS,
    NoSettlementSiteError,
    find_settlement_site,
    nearest_free_walkable,
    resource_counts,
)
from world.state import Entity, World, WorldObject
from world.terrain.objects import (
    ObjectType,
    PlacedObject,
    PlacementFields,
    fit_ore_to_settlement,
    prune_veins_near_settlement,
)
from world.terrain.config import ObjectPlacementConfig
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


# Ore sits outside the exclusion radius and inside the search radius.
_ORE_DISTANCE = 70
_DEFAULT_ORE = {"copper_vein": MIN_COPPER_VEINS + 1, "iron_vein": MIN_IRON_VEINS + 1}


def _ore_veins(
    centre: Position, distance: int = _ORE_DISTANCE, **counts: int
) -> list[WorldObject]:
    """Put veins `distance` tiles away from centre, in a direction that fits."""
    step_x = 1 if centre.x + distance < SIZE else -1
    step_y = 1 if centre.y + distance < SIZE else -1
    objects: list[WorldObject] = []
    for vein_type in ("copper_vein", "iron_vein"):
        count = counts.get(vein_type, _DEFAULT_ORE[vein_type])
        for index in range(count):
            objects.append(
                WorldObject(
                    object_id=f"o{centre.x}_{vein_type}_{index}_{distance}",
                    position=Position(
                        x=centre.x + step_x * distance,
                        y=centre.y + step_y * (distance - 2 * index),
                    ),
                    object_type=vein_type,
                )
            )
    return objects


def _supplied_site(world: World, centre: Position, **supply: int) -> list[WorldObject]:
    """Surround centre with qualifying resources, ore in reach and water."""
    floor = world._floor_array
    assert floor is not None
    floor[centre.y, centre.x - 28] = SHALLOW_WATER

    objects: list[WorldObject] = []
    for object_type, (dx, dy) in _BLOCK_OFFSETS.items():
        count = supply.get(object_type, _DEFAULT_SUPPLY[object_type])
        block_centre = Position(x=centre.x + dx, y=centre.y + dy)
        objects += _block(block_centre, object_type, count, f"s{centre.x}")
    objects += _ore_veins(
        centre,
        copper_vein=supply.get("copper_vein", _DEFAULT_ORE["copper_vein"]),
        iron_vein=supply.get("iron_vein", _DEFAULT_ORE["iron_vein"]),
    )
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

    @pytest.mark.parametrize(
        "vein_type,kept", [("copper_vein", MIN_COPPER_VEINS - 1), ("iron_vein", 1)]
    )
    def test_requires_ore_in_reach(self, vein_type: str, kept: int) -> None:
        world = _make_world()
        objects = _supplied_site(world, Position(x=100, y=100), **{vein_type: kept})

        with pytest.raises(NoSettlementSiteError):
            find_settlement_site(world, objects)

    def test_ore_on_the_doorstep_does_not_count(self) -> None:
        world = _make_world()
        centre = Position(x=100, y=100)
        objects = _supplied_site(world, centre, copper_vein=0, iron_vein=0)
        objects += _ore_veins(centre, distance=ORE_EXCLUSION_RADIUS // 2)

        with pytest.raises(NoSettlementSiteError):
            find_settlement_site(world, objects)

    def test_ore_in_reach_is_beyond_the_exclusion_radius(self) -> None:
        world = _make_world()
        objects = _supplied_site(world, Position(x=100, y=100))

        site = find_settlement_site(world, objects)

        veins = [obj for obj in objects if obj.object_type.endswith("_vein")]
        in_reach = [
            obj
            for obj in veins
            if ORE_EXCLUSION_RADIUS
            < max(abs(obj.position.x - site.x), abs(obj.position.y - site.y))
            <= ORE_SEARCH_RADIUS
        ]
        assert len(in_reach) >= MIN_COPPER_VEINS + MIN_IRON_VEINS

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


def _as_placed(objects: list[WorldObject]) -> list[PlacedObject]:
    """Convert world objects to the generator's placed-object form."""
    return [
        PlacedObject(
            x=obj.position.x,
            y=obj.position.y,
            object_type=ObjectType(obj.object_type),
            object_id=obj.object_id,
        )
        for obj in objects
    ]


class TestPruneVeinsNearSettlement:
    """The generator keeps ore off the settlement's doorstep."""

    def _map(self) -> tuple[World, list[WorldObject]]:
        world = _make_world()
        centre = Position(x=100, y=100)
        objects = _supplied_site(world, centre)
        # Ore right on the doorstep, which the generator must delete.
        objects += _ore_veins(centre, distance=ORE_EXCLUSION_RADIUS // 2)
        return world, objects

    def test_removes_only_the_veins_inside_the_exclusion_radius(self) -> None:
        world, objects = self._map()
        site = find_settlement_site(world, objects)

        kept = prune_veins_near_settlement(_as_placed(objects), site)

        assert len(kept) < len(objects)
        for obj in kept:
            if obj.object_type in (ObjectType.COPPER_VEIN, ObjectType.IRON_VEIN):
                distance = max(abs(obj.x - site.x), abs(obj.y - site.y))
                assert distance > ORE_EXCLUSION_RADIUS
        kept_ids = {obj.object_id for obj in kept}
        for obj in objects:
            if not obj.object_type.endswith("_vein"):
                assert obj.object_id in kept_ids

    def test_the_site_is_the_same_after_pruning(self) -> None:
        world, objects = self._map()
        before = find_settlement_site(world, objects)

        kept = prune_veins_near_settlement(_as_placed(objects), before)
        after = find_settlement_site(
            world,
            [
                WorldObject(
                    object_id=obj.object_id,
                    position=Position(x=obj.x, y=obj.y),
                    object_type=obj.object_type.value,
                )
                for obj in kept
            ],
        )

        assert after == before

    def test_keeps_the_veins_that_are_already_out_of_reach(self) -> None:
        site = Position(x=100, y=100)
        objects = _as_placed(_ore_veins(site, distance=ORE_EXCLUSION_RADIUS + 5))

        assert prune_veins_near_settlement(objects, site) == objects


def _flat_fields(floor: np.ndarray) -> PlacementFields:
    """Placement fields for a flat synthetic map: only the floor matters here."""
    zeros = np.zeros(floor.shape, dtype=np.float32)
    far = np.full(floor.shape, 1000.0, dtype=np.float32)
    return PlacementFields(
        floor=floor,
        forest_density=zeros,
        ridged_noise=zeros,
        slope=zeros,
        dist_to_water=far,
        dist_to_coast=far,
        dist_to_fresh=far,
        dist_to_mountain=far,
    )


class TestFitOreToSettlement:
    """The generator guarantees ore in reach of the site it picked."""

    def test_adds_a_district_when_the_random_placement_left_none(self) -> None:
        world = _make_world()
        centre = Position(x=100, y=100)
        objects = _supplied_site(world, centre, copper_vein=0, iron_vein=0)
        floor = world._floor_array
        assert floor is not None
        outcrop = np.zeros(floor.shape, dtype=np.float32)

        kept = fit_ore_to_settlement(
            _flat_fields(floor),
            outcrop,
            np.zeros(floor.shape, dtype=np.bool_),
            _as_placed(objects),
            np.random.default_rng(3),
            seed=1,
            config=ObjectPlacementConfig(),
        )

        site = find_settlement_site(
            world,
            [
                WorldObject(
                    object_id=obj.object_id,
                    position=Position(x=obj.x, y=obj.y),
                    object_type=obj.object_type.value,
                )
                for obj in kept
            ],
        )
        veins = [
            obj
            for obj in kept
            if obj.object_type
            in (
                ObjectType.COPPER_VEIN,
                ObjectType.IRON_VEIN,
            )
        ]
        assert veins
        for vein in veins:
            assert (
                max(abs(vein.x - site.x), abs(vein.y - site.y)) > ORE_EXCLUSION_RADIUS
            )

    def test_a_map_without_a_site_is_left_alone(self) -> None:
        world = _make_world()
        floor = world._floor_array
        assert floor is not None
        objects = _as_placed(_ore_veins(Position(x=100, y=100), distance=10))

        kept = fit_ore_to_settlement(
            _flat_fields(floor),
            np.zeros(floor.shape, dtype=np.float32),
            np.zeros(floor.shape, dtype=np.bool_),
            objects,
            np.random.default_rng(3),
            seed=1,
            config=ObjectPlacementConfig(),
        )

        assert kept == objects
