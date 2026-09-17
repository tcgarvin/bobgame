"""Tests for natural object placement (trees, rocks, bushes, reeds, clay)."""

from collections import Counter

import numpy as np
import pytest
from scipy import ndimage

from world.terrain.config import ObjectPlacementConfig
from world.terrain.generator import split_ocean_and_lakes
from world.terrain.objects import (
    ObjectType,
    PlacedObject,
    PlacementFields,
    canopy_field,
    outcrop_field,
    place_objects,
    uniformise,
)

DEEP, SHALLOW, SAND, GRASS, DIRT, MOUNTAIN = 0, 1, 2, 3, 4, 5
SIZE = 240
SEED = 7
ROCKS = {
    ObjectType.ROCK_SMALL,
    ObjectType.ROCK_MEDIUM,
    ObjectType.ROCK_LARGE,
    ObjectType.BOULDER,
}


def _distance_to(mask: np.ndarray) -> np.ndarray:
    return ndimage.distance_transform_edt(~mask).astype(np.float32)


def _fields() -> PlacementFields:
    """A grass map: ocean strip on the west, a river down x=150 with a ford,
    and a mountain block in the north-east corner."""
    floor = np.full((SIZE, SIZE), GRASS, dtype=np.uint8)
    floor[:, :20] = DEEP  # ocean
    floor[:, 20:22] = SAND  # beach
    floor[:, 150:153] = DEEP  # river
    floor[100:108, 150:153] = SHALLOW  # ford
    floor[:40, 200:] = MOUNTAIN

    ocean = np.zeros((SIZE, SIZE), dtype=bool)
    ocean[:, :20] = True
    fresh = np.zeros((SIZE, SIZE), dtype=bool)
    fresh[:, 150:153] = True
    rng = np.random.default_rng(1)
    return PlacementFields(
        floor=floor,
        forest_density=rng.random((SIZE, SIZE), dtype=np.float32),
        ridged_noise=rng.random((SIZE, SIZE), dtype=np.float32),
        slope=np.zeros((SIZE, SIZE), dtype=np.float32),
        dist_to_water=_distance_to(ocean | fresh),
        dist_to_coast=_distance_to(ocean),
        dist_to_fresh=_distance_to(fresh),
        dist_to_mountain=_distance_to(floor == MOUNTAIN),
    )


@pytest.fixture(scope="module")
def fields() -> PlacementFields:
    return _fields()


@pytest.fixture(scope="module")
def placed(fields: PlacementFields) -> list[PlacedObject]:
    return place_objects(
        fields, np.random.default_rng(SEED), SEED, ObjectPlacementConfig()
    )


def _of(placed: list[PlacedObject], *types: ObjectType) -> list[PlacedObject]:
    return [obj for obj in placed if obj.object_type in types]


class TestPlaceObjects:
    def test_places_every_kind(self, placed: list[PlacedObject]) -> None:
        kinds = Counter(obj.object_type for obj in placed)
        assert kinds[ObjectType.TREE] > 0
        assert kinds[ObjectType.BUSH] > 0
        assert kinds[ObjectType.REEDS] > 0
        assert kinds[ObjectType.CLAY_DEPOSIT] > 0
        assert sum(kinds[rock] for rock in ROCKS) > 0

    def test_at_most_one_object_per_tile(self, placed: list[PlacedObject]) -> None:
        tiles = [(obj.x, obj.y) for obj in placed]
        assert len(tiles) == len(set(tiles))

    def test_object_ids_are_unique(self, placed: list[PlacedObject]) -> None:
        ids = [obj.object_id for obj in placed]
        assert len(ids) == len(set(ids))

    def test_same_seed_gives_the_same_objects(
        self, fields: PlacementFields, placed: list[PlacedObject]
    ) -> None:
        again = place_objects(
            fields, np.random.default_rng(SEED), SEED, ObjectPlacementConfig()
        )
        assert again == placed

    def test_nothing_on_deep_water_or_mountain(
        self, fields: PlacementFields, placed: list[PlacedObject]
    ) -> None:
        for obj in placed:
            assert fields.floor[obj.y, obj.x] not in (DEEP, MOUNTAIN)


class TestReeds:
    def test_reeds_hug_fresh_water(
        self, fields: PlacementFields, placed: list[PlacedObject]
    ) -> None:
        width = ObjectPlacementConfig().reed_bank_width
        for reed in _of(placed, ObjectType.REEDS):
            assert fields.dist_to_fresh[reed.y, reed.x] <= width

    def test_no_reeds_on_the_ocean_shore(
        self, fields: PlacementFields, placed: list[PlacedObject]
    ) -> None:
        exclusion = ObjectPlacementConfig().reed_coast_exclusion
        for reed in _of(placed, ObjectType.REEDS):
            assert fields.dist_to_coast[reed.y, reed.x] > exclusion

    def test_reeds_only_stand_in_water_at_fords(
        self, fields: PlacementFields, placed: list[PlacedObject]
    ) -> None:
        for reed in _of(placed, ObjectType.REEDS):
            assert fields.floor[reed.y, reed.x] in (SHALLOW, SAND, GRASS, DIRT)


class TestClay:
    def test_clay_sits_a_little_back_from_the_bank(
        self, fields: PlacementFields, placed: list[PlacedObject]
    ) -> None:
        config = ObjectPlacementConfig()
        for clay in _of(placed, ObjectType.CLAY_DEPOSIT):
            distance = fields.dist_to_fresh[clay.y, clay.x]
            assert config.clay_min_distance <= distance <= config.clay_max_distance
            assert fields.floor[clay.y, clay.x] in (GRASS, DIRT)


class TestTreesAndRocks:
    def test_trees_only_on_grass_or_dirt_off_the_waterline(
        self, fields: PlacementFields, placed: list[PlacedObject]
    ) -> None:
        for tree in _of(placed, ObjectType.TREE):
            assert fields.floor[tree.y, tree.x] in (GRASS, DIRT)
            assert fields.dist_to_water[tree.y, tree.x] > 1.0

    def test_trees_gather_under_the_canopy(self, fields: PlacementFields) -> None:
        config = ObjectPlacementConfig()
        canopy = canopy_field(fields, SEED, config)
        placed = place_objects(fields, np.random.default_rng(SEED), SEED, config)
        trees = _of(placed, ObjectType.TREE)
        under = sum(1 for tree in trees if canopy[tree.y, tree.x] > 0.5)
        assert under / len(trees) > 0.8
        # ... and the canopy leaves real open country, not wall-to-wall forest.
        assert 0.15 < float((canopy > 0.5).mean()) < 0.7

    def test_rocks_gather_in_outcrops(self, fields: PlacementFields) -> None:
        config = ObjectPlacementConfig()
        outcrop = outcrop_field(fields, SEED, config)
        placed = place_objects(fields, np.random.default_rng(SEED), SEED, config)
        rocks = _of(placed, *ROCKS)
        inside = sum(1 for rock in rocks if outcrop[rock.y, rock.x] > 0.5)
        assert inside / len(rocks) > 0.6
        assert float((outcrop > 0.5).mean()) < 0.35

    def test_big_rocks_only_inside_outcrops(self, fields: PlacementFields) -> None:
        config = ObjectPlacementConfig()
        outcrop = outcrop_field(fields, SEED, config)
        placed = place_objects(fields, np.random.default_rng(SEED), SEED, config)
        for rock in _of(placed, ObjectType.BOULDER, ObjectType.ROCK_LARGE):
            assert outcrop[rock.y, rock.x] > 0.5


class TestHelpers:
    def test_uniformise_spreads_values_over_the_unit_interval(self) -> None:
        bunched = np.random.default_rng(0).normal(0.5, 0.01, (200, 200))
        spread = uniformise(bunched.astype(np.float32))
        assert 0.0 <= float(spread.min()) and float(spread.max()) <= 1.0
        assert abs(float((spread > 0.7).mean()) - 0.3) < 0.02

    def test_split_ocean_and_lakes(self) -> None:
        land = np.ones((30, 30), dtype=bool)
        land[:, :3] = False  # ocean touches the border
        land[10:15, 10:15] = False  # enclosed lake
        ocean, lakes = split_ocean_and_lakes(land)
        assert ocean[:, :3].all() and not ocean[12, 12]
        assert lakes[10:15, 10:15].all() and int(lakes.sum()) == 25
