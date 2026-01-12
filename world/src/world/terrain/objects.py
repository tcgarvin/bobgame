"""Object placement: trees, bushes, rocks with natural clustering."""

from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray

from ..terrain_types import FloorType
from .classification import floor_value_to_type
from .config import ObjectPlacementConfig


class ObjectType(str, Enum):
    """Types of objects that can be placed."""

    TREE = "tree"
    BUSH = "bush"
    ROCK_SMALL = "rock_small"
    ROCK_MEDIUM = "rock_medium"
    ROCK_LARGE = "rock_large"
    BOULDER = "boulder"


@dataclass
class PlacedObject:
    """A placed object with position and type."""

    x: int
    y: int
    object_type: ObjectType
    object_id: str


def place_trees(
    floor: NDArray[np.uint8],
    forest_density: NDArray[np.float32],
    dist_to_water: NDArray[np.float32],
    slope: NDArray[np.float32],
    rng: np.random.Generator,
    base_density: float = 0.15,
    coast_distance: int = 80,
) -> list[PlacedObject]:
    """Place trees using clustered probability.

    Trees are placed on grass/dirt, denser in forest areas,
    sparse near coasts and on slopes.

    Args:
        floor: Floor type array.
        forest_density: Forest density field [0, 1].
        dist_to_water: Distance to water.
        slope: Slope magnitude.
        rng: Random number generator.
        base_density: Base tree probability.
        coast_distance: Distance from water for full density.

    Returns:
        List of placed tree objects.
    """
    height, width = floor.shape
    trees: list[PlacedObject] = []
    tree_id = 0

    valid_floors = {
        _floor_value(FloorType.GRASS),
        _floor_value(FloorType.DIRT),
    }
    slope_max = 0.5

    for y in range(height):
        for x in range(width):
            if floor[y, x] not in valid_floors:
                continue

            # Probability based on forest density, distance from water, slope
            p = base_density * (forest_density[y, x] ** 2)
            p *= np.clip(dist_to_water[y, x] / coast_distance, 0, 1)
            p *= np.clip(1 - slope[y, x] / slope_max, 0, 1)

            if rng.random() < p:
                trees.append(
                    PlacedObject(
                        x=x,
                        y=y,
                        object_type=ObjectType.TREE,
                        object_id=f"tree_{tree_id}",
                    )
                )
                tree_id += 1

    return trees


def place_bushes(
    floor: NDArray[np.uint8],
    forest_density: NDArray[np.float32],
    dist_to_water: NDArray[np.float32],
    occupied: set[tuple[int, int]],
    rng: np.random.Generator,
    base_density: float = 0.08,
    water_min_distance: int = 10,
    water_max_distance: int = 60,
) -> list[PlacedObject]:
    """Place bushes near forest edges and water.

    Bushes appear on grass/dirt, avoiding trees.

    Args:
        floor: Floor type array.
        forest_density: Forest density field [0, 1].
        dist_to_water: Distance to water.
        occupied: Set of (x, y) positions already occupied.
        rng: Random number generator.
        base_density: Base bush probability.
        water_min_distance: Min distance from water for bushes.
        water_max_distance: Max distance from water for bushes.

    Returns:
        List of placed bush objects.
    """
    height, width = floor.shape
    bushes: list[PlacedObject] = []
    bush_id = 0

    valid_floors = {
        _floor_value(FloorType.GRASS),
        _floor_value(FloorType.DIRT),
    }

    for y in range(height):
        for x in range(width):
            if floor[y, x] not in valid_floors:
                continue
            if (x, y) in occupied:
                continue

            # Higher probability near forest edges (inverse of forest density)
            # and in moderate distance from water
            dist = dist_to_water[y, x]
            if dist < water_min_distance or dist > water_max_distance:
                continue

            p = base_density * (1 - forest_density[y, x])
            # Smoothstep for water distance preference
            water_factor = _smoothstep(water_min_distance, water_max_distance, dist)
            p *= water_factor

            if rng.random() < p:
                bushes.append(
                    PlacedObject(
                        x=x,
                        y=y,
                        object_type=ObjectType.BUSH,
                        object_id=f"bush_{bush_id}",
                    )
                )
                occupied.add((x, y))
                bush_id += 1

    return bushes


def place_rocks(
    floor: NDArray[np.uint8],
    ridged_noise: NDArray[np.float32],
    slope: NDArray[np.float32],
    dist_to_water: NDArray[np.float32],
    occupied: set[tuple[int, int]],
    rng: np.random.Generator,
    base_density: float = 0.05,
) -> list[PlacedObject]:
    """Place rocks of varying sizes.

    Rocks appear on grass/dirt/sand, more common on slopes
    and near mountains.

    Args:
        floor: Floor type array.
        ridged_noise: Ridged noise for rockiness.
        slope: Slope magnitude.
        dist_to_water: Distance to water.
        occupied: Set of (x, y) positions already occupied.
        rng: Random number generator.
        base_density: Base rock probability.

    Returns:
        List of placed rock objects.
    """
    height, width = floor.shape
    rocks: list[PlacedObject] = []
    rock_id = 0

    valid_floors = {
        _floor_value(FloorType.GRASS),
        _floor_value(FloorType.DIRT),
        _floor_value(FloorType.SAND),
    }

    # Calculate rockiness field
    slope_norm = slope / (np.max(slope) + 1e-6)
    rockiness = 0.6 * ridged_noise + 0.4 * slope_norm

    # Collect rock candidates with their rockiness
    candidates: list[tuple[int, int, float]] = []

    for y in range(height):
        for x in range(width):
            if floor[y, x] not in valid_floors:
                continue
            if (x, y) in occupied:
                continue

            r = rockiness[y, x]
            p = base_density * r

            if rng.random() < p:
                candidates.append((x, y, r))

    # Sort by rockiness to assign sizes
    candidates.sort(key=lambda c: c[2], reverse=True)

    # Assign sizes based on quantiles
    total = len(candidates)
    for i, (x, y, r) in enumerate(candidates):
        frac = i / max(total, 1)

        if frac < 0.02:
            obj_type = ObjectType.BOULDER
        elif frac < 0.07:
            obj_type = ObjectType.ROCK_LARGE
        elif frac < 0.17:
            obj_type = ObjectType.ROCK_MEDIUM
        else:
            obj_type = ObjectType.ROCK_SMALL

        rocks.append(
            PlacedObject(
                x=x,
                y=y,
                object_type=obj_type,
                object_id=f"rock_{rock_id}",
            )
        )
        occupied.add((x, y))
        rock_id += 1

    return rocks


def place_objects(
    floor: NDArray[np.uint8],
    forest_density: NDArray[np.float32],
    ridged_noise: NDArray[np.float32],
    slope: NDArray[np.float32],
    dist_to_water: NDArray[np.float32],
    rng: np.random.Generator,
    config: ObjectPlacementConfig,
) -> list[PlacedObject]:
    """Place all natural objects.

    Args:
        floor: Floor type array.
        forest_density: Forest density field.
        ridged_noise: Ridged noise for rockiness.
        slope: Slope magnitude.
        dist_to_water: Distance to water.
        rng: Random number generator.
        config: Object placement configuration.

    Returns:
        List of all placed objects.
    """
    occupied: set[tuple[int, int]] = set()

    # Place trees first (they block other objects)
    trees = place_trees(
        floor,
        forest_density,
        dist_to_water,
        slope,
        rng,
        base_density=config.tree_base_density,
        coast_distance=config.tree_coast_distance,
    )
    for tree in trees:
        occupied.add((tree.x, tree.y))

    # Place bushes
    bushes = place_bushes(
        floor,
        forest_density,
        dist_to_water,
        occupied,
        rng,
        base_density=config.bush_base_density,
        water_min_distance=config.bush_water_min_distance,
        water_max_distance=config.bush_water_max_distance,
    )

    # Place rocks
    rocks = place_rocks(
        floor,
        ridged_noise,
        slope,
        dist_to_water,
        occupied,
        rng,
        base_density=config.rock_base_density,
    )

    return trees + bushes + rocks


def _floor_value(floor_type: FloorType) -> int:
    """Convert FloorType to uint8 value."""
    mapping = {
        FloorType.DEEP_WATER: 0,
        FloorType.SHALLOW_WATER: 1,
        FloorType.SAND: 2,
        FloorType.GRASS: 3,
        FloorType.DIRT: 4,
        FloorType.MOUNTAIN: 5,
        FloorType.STONE: 6,
    }
    return mapping[floor_type]


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    """Scalar smoothstep function."""
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)
