"""Post-generation validation and repair."""

import logging

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from ..terrain_types import FloorType
from .config import TerrainConfig
from .objects import PlacedObject

logger = logging.getLogger(__name__)


class ValidationResult:
    """Result of terrain validation."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.passed = True

    def add_error(self, message: str) -> None:
        """Add validation error."""
        self.errors.append(message)
        self.passed = False

    def add_warning(self, message: str) -> None:
        """Add validation warning."""
        self.warnings.append(message)


def validate_terrain(
    floor: NDArray[np.uint8],
    objects: list[PlacedObject],
    config: TerrainConfig,
) -> ValidationResult:
    """Validate generated terrain against constraints.

    Args:
        floor: Floor type array.
        objects: List of placed objects.
        config: Generation configuration.

    Returns:
        ValidationResult with any errors/warnings.
    """
    result = ValidationResult()
    height, width = floor.shape

    # Check 1: Border is water
    _check_border_water(floor, config.island.border_width, result)

    # Check 2: Single island (largest land component)
    _check_single_island(floor, result)

    # Check 3: Land fraction in reasonable range
    _check_land_fraction(floor, config.island.land_fraction, result)

    # Check 4: Mountain fraction cap
    _check_mountain_cap(floor, config.classification.mountain_fraction_max, result)

    # Check 5: Objects on valid floor types
    _check_object_placement(floor, objects, result)

    # Log results
    if result.passed:
        logger.info("Terrain validation passed")
    else:
        logger.warning(f"Terrain validation failed with {len(result.errors)} errors")
        for error in result.errors:
            logger.error(f"  - {error}")

    for warning in result.warnings:
        logger.warning(f"  - {warning}")

    return result


def _check_border_water(
    floor: NDArray[np.uint8],
    border_width: int,
    result: ValidationResult,
) -> None:
    """Check that border is water."""
    height, width = floor.shape
    water_values = {0, 1}  # DEEP_WATER, SHALLOW_WATER

    # Check all border cells
    non_water = 0

    for y in range(border_width):
        for x in range(width):
            if floor[y, x] not in water_values:
                non_water += 1
            if floor[height - 1 - y, x] not in water_values:
                non_water += 1

    for x in range(border_width):
        for y in range(border_width, height - border_width):
            if floor[y, x] not in water_values:
                non_water += 1
            if floor[y, width - 1 - x] not in water_values:
                non_water += 1

    if non_water > 0:
        result.add_error(f"Border has {non_water} non-water cells")


def _check_single_island(
    floor: NDArray[np.uint8],
    result: ValidationResult,
) -> None:
    """Check that there's a single land mass."""
    # Land is anything not water
    land_mask = (floor != 0) & (floor != 1)

    # Count connected components
    structure = ndimage.generate_binary_structure(2, 2)  # 8-connected
    labeled, num_features = ndimage.label(land_mask, structure=structure)

    if num_features == 0:
        result.add_error("No land found")
    elif num_features > 1:
        # Find component sizes
        sizes = ndimage.sum(land_mask, labeled, range(1, num_features + 1))
        total_land = np.sum(land_mask)
        largest = np.max(sizes)
        largest_frac = largest / total_land if total_land > 0 else 0

        if largest_frac < 0.99:
            result.add_warning(
                f"Multiple land masses: {num_features} components, "
                f"largest is {largest_frac:.1%} of land"
            )


def _check_land_fraction(
    floor: NDArray[np.uint8],
    target_fraction: float,
    result: ValidationResult,
) -> None:
    """Check land fraction is reasonable."""
    total = floor.size
    land = np.sum((floor != 0) & (floor != 1))
    actual_fraction = land / total

    tolerance = 0.08
    if abs(actual_fraction - target_fraction) > tolerance:
        result.add_warning(
            f"Land fraction {actual_fraction:.1%} differs from target {target_fraction:.1%}"
        )


def _check_mountain_cap(
    floor: NDArray[np.uint8],
    max_fraction: float,
    result: ValidationResult,
) -> None:
    """Check mountain fraction is within cap."""
    land = np.sum((floor != 0) & (floor != 1))
    mountains = np.sum(floor == 5)  # MOUNTAIN

    if land > 0:
        mountain_fraction = mountains / land
        if mountain_fraction > max_fraction + 0.01:
            result.add_warning(
                f"Mountain fraction {mountain_fraction:.1%} exceeds cap {max_fraction:.1%}"
            )


def _check_object_placement(
    floor: NDArray[np.uint8],
    objects: list[PlacedObject],
    result: ValidationResult,
) -> None:
    """Check objects are on valid floor types."""
    height, width = floor.shape

    # Valid floors for each object type
    valid_floors = {
        "tree": {3, 4},  # GRASS, DIRT
        "bush": {3, 4},  # GRASS, DIRT
        "rock_small": {2, 3, 4},  # SAND, GRASS, DIRT
        "rock_medium": {2, 3, 4},
        "rock_large": {2, 3, 4},
        "boulder": {2, 3, 4},
    }

    invalid_count = 0
    for obj in objects:
        if not (0 <= obj.y < height and 0 <= obj.x < width):
            invalid_count += 1
            continue

        floor_val = floor[obj.y, obj.x]
        allowed = valid_floors.get(obj.object_type.value, set())

        if floor_val not in allowed:
            invalid_count += 1

    if invalid_count > 0:
        result.add_warning(f"{invalid_count} objects on invalid floor types")
