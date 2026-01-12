"""Island shaping: radial falloff, border enforcement, sea level calculation."""

import numpy as np
from numpy.typing import NDArray

from .config import IslandConfig
from .noise import smoothstep


def apply_radial_falloff(
    elevation: NDArray[np.float32],
    config: IslandConfig,
) -> NDArray[np.float32]:
    """Apply centered radial falloff to create island shape.

    Uses a smooth falloff from center to edges, ensuring
    the terrain naturally tapers to ocean.

    Args:
        elevation: Input elevation field.
        config: Island shaping parameters.

    Returns:
        Elevation with radial falloff applied.
    """
    height, width = elevation.shape
    result = elevation.copy()

    # Center coordinates
    cx, cy = width / 2, height / 2

    # Maximum distance (to corner)
    max_dist = np.sqrt(cx**2 + cy**2)

    # Generate distance field
    y_coords = np.arange(height, dtype=np.float32)
    x_coords = np.arange(width, dtype=np.float32)
    xx, yy = np.meshgrid(x_coords, y_coords)

    # Normalized distance from center [0, ~1.4]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max_dist

    # Smooth falloff function
    falloff = smoothstep(config.falloff_start, config.falloff_end, dist)

    # Apply falloff (subtract from elevation)
    result = result - falloff * config.coast_drop

    return result


def enforce_border_ocean(
    elevation: NDArray[np.float32],
    border_width: int,
) -> NDArray[np.float32]:
    """Force a water border around the edges.

    Sets elevation very low at borders to guarantee water.

    Args:
        elevation: Input elevation field.
        border_width: Width of guaranteed water border.

    Returns:
        Elevation with forced water border.
    """
    height, width = elevation.shape
    result = elevation.copy()

    # Very low value that will always be below sea level
    very_low = np.min(elevation) - 10.0

    # Top and bottom borders
    result[:border_width, :] = very_low
    result[-border_width:, :] = very_low

    # Left and right borders
    result[:, :border_width] = very_low
    result[:, -border_width:] = very_low

    return result


def compute_sea_level(
    elevation: NDArray[np.float32],
    target_land_fraction: float,
) -> float:
    """Compute sea level to achieve target land fraction.

    Args:
        elevation: Elevation field.
        target_land_fraction: Desired fraction of cells above water (0-1).

    Returns:
        Sea level threshold.
    """
    # Water fraction is 1 - land_fraction
    water_fraction = 1.0 - target_land_fraction

    # Sea level is the quantile at water_fraction
    sea_level = float(np.quantile(elevation, water_fraction))

    return sea_level


def create_land_mask(
    elevation: NDArray[np.float32],
    sea_level: float,
) -> NDArray[np.bool_]:
    """Create binary land mask from elevation.

    Args:
        elevation: Elevation field.
        sea_level: Threshold for land vs water.

    Returns:
        Boolean mask where True = land.
    """
    return elevation >= sea_level
