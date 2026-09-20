"""Terrain type classification: water, sand, grass, dirt, mountain."""

import numpy as np
from numpy.typing import NDArray

from ..terrain_types import FLOOR_TYPE_BY_CODE, FloorType
from .config import ClassificationConfig
from .noise import smoothstep


def classify_terrain(
    land_mask: NDArray[np.bool_],
    river_mask: NDArray[np.bool_],
    ford_mask: NDArray[np.bool_],
    elevation: NDArray[np.float32],
    moisture: NDArray[np.float32],
    slope: NDArray[np.float32],
    dist_to_water: NDArray[np.float32],
    dist_to_land: NDArray[np.float32],
    beach_noise: NDArray[np.float32],
    shallow_noise: NDArray[np.float32],
    ridged_noise: NDArray[np.float32],
    config: ClassificationConfig,
) -> NDArray[np.uint8]:
    """Classify each cell into a terrain type.

    Args:
        land_mask: Boolean mask where True = land.
        river_mask: Boolean mask where True = river.
        ford_mask: Boolean mask where True = ford (walkable river crossing).
        elevation: Elevation field.
        moisture: Moisture field [0, 1].
        slope: Slope magnitude field.
        dist_to_water: Distance from land cells to water.
        dist_to_land: Distance from water cells to land.
        beach_noise: Noise for varying beach width [0, 1].
        shallow_noise: Noise for shallow water patches [0, 1].
        ridged_noise: Ridged noise for mountain placement.
        config: Classification configuration.

    Returns:
        2D array of FloorType enum values as uint8.
    """
    height, width = land_mask.shape
    floor = np.full((height, width), FloorType.GRASS.code, dtype=np.uint8)

    # Start with all ocean as deep water
    floor[~land_mask] = FloorType.DEEP_WATER.code

    # Shallow water near coast
    shallow = (
        ~land_mask
        & (dist_to_land <= config.shallow_water_max_depth)
        & (shallow_noise > config.shallow_water_noise_threshold)
    )
    floor[shallow] = FloorType.SHALLOW_WATER.code

    # Rivers are deep water
    floor[river_mask] = FloorType.DEEP_WATER.code

    # Fords are shallow water (walkable)
    floor[ford_mask] = FloorType.SHALLOW_WATER.code

    # Beaches: variable width based on noise
    beach_width = np.floor(
        config.beach_max_width * smoothstep(0.2, 0.8, beach_noise)
    ).astype(np.int32)

    floor[land_mask & (dist_to_water <= beach_width)] = FloorType.SAND.code

    # Mountains: inland, high elevation or ridged noise
    land_elevations = elevation[land_mask]
    if len(land_elevations) > 0:
        elev_threshold = np.quantile(
            land_elevations, config.mountain_elevation_quantile
        )
    else:
        elev_threshold = float("inf")

    mountain_candidate = (
        land_mask
        & (dist_to_water > config.mountain_distance_from_water)
        & ((ridged_noise > 0.6) | (elevation > elev_threshold))
    )

    # Cap mountains at max fraction
    # Rank candidates by how mountainous they are so that, when the cap bites,
    # the highest ground survives as contiguous massifs.
    elev_span = float(elevation.max() - elevation.min()) + 1e-9
    mountain_score = (elevation - elevation.min()) / elev_span + 0.5 * ridged_noise
    floor = _apply_mountain_cap(
        floor,
        mountain_candidate,
        mountain_score,
        land_mask,
        config.mountain_fraction_max,
    )

    # Dirt: low moisture or high slope (on remaining grass)
    slope_thresh = 0.1  # Slope threshold for dirt
    dirt = (
        land_mask
        & (floor == FloorType.GRASS.code)
        & ((moisture < config.moisture_dirt_threshold) | (slope > slope_thresh))
    )
    floor[dirt] = FloorType.DIRT.code

    return floor


def floor_value_to_type(value: int) -> FloorType:
    """Convert a uint8 floor code back to its FloorType, grass when unknown."""
    return FLOOR_TYPE_BY_CODE.get(value, FloorType.GRASS)


def _apply_mountain_cap(
    floor: NDArray[np.uint8],
    mountain_candidate: NDArray[np.bool_],
    mountain_score: NDArray[np.float32],
    land_mask: NDArray[np.bool_],
    max_fraction: float,
) -> NDArray[np.uint8]:
    """Turn mountain candidates into mountains, at most `max_fraction` of land.

    When there are more candidates than the cap allows, the highest-scoring
    ones win. The score is a smooth field, so the survivors form contiguous
    massifs (a random subset would pepper the map with one-tile mountains).

    Args:
        floor: Current floor array (modified in place and returned).
        mountain_candidate: Boolean mask of mountain candidates.
        mountain_score: Smooth "how mountainous" field; higher wins.
        land_mask: Boolean mask of land.
        max_fraction: Maximum fraction of land as mountains.

    Returns:
        Updated floor array.
    """
    max_mountains = int(np.sum(land_mask) * max_fraction)
    candidate_count = int(np.sum(mountain_candidate))

    if candidate_count <= max_mountains:
        floor[mountain_candidate] = FloorType.MOUNTAIN.code
        return floor
    if max_mountains == 0:
        return floor

    scores = mountain_score[mountain_candidate]
    cutoff = np.partition(scores, candidate_count - max_mountains)[
        candidate_count - max_mountains
    ]
    floor[mountain_candidate & (mountain_score >= cutoff)] = FloorType.MOUNTAIN.code
    return floor
