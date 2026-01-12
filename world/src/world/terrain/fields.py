"""Field generation for terrain: elevation, moisture, and helper fields."""

import numpy as np
from numpy.typing import NDArray

from .config import ElevationConfig, ForestConfig, MoistureConfig
from .noise import (
    domain_warp,
    fbm_noise_vectorized,
    ridged_multifractal,
    smoothstep,
)


def make_elevation(
    width: int,
    height: int,
    seed: int,
    config: ElevationConfig,
) -> NDArray[np.float32]:
    """Generate elevation field.

    Combines fBm noise with ridged multifractal for mountains
    and applies domain warping for organic shapes.

    Args:
        width: World width in tiles.
        height: World height in tiles.
        seed: Random seed.
        config: Elevation generation parameters.

    Returns:
        2D elevation array.
    """
    # Base fBm elevation
    elevation = fbm_noise_vectorized(
        width,
        height,
        seed,
        config.noise.base_wavelength,
        octaves=config.noise.octaves,
        lacunarity=config.noise.lacunarity,
        gain=config.noise.gain,
    )

    # Add ridged multifractal for mountain candidates
    ridged = ridged_multifractal(
        width,
        height,
        seed + 100,
        config.ridged_wavelength,
        octaves=config.ridged_octaves,
    )

    # Combine: base elevation + weighted ridged contribution
    elevation = elevation + config.ridged_weight * ridged

    # Apply domain warping for organic coastlines
    elevation = domain_warp(
        elevation,
        seed + 200,
        config.warp.wavelength,
        config.warp.amplitude,
        config.warp.octaves,
    )

    return elevation


def make_moisture(
    width: int,
    height: int,
    seed: int,
    config: MoistureConfig,
    distance_to_water: NDArray[np.float32] | None = None,
) -> NDArray[np.float32]:
    """Generate moisture field.

    Combines noise with optional distance-to-water influence.

    Args:
        width: World width in tiles.
        height: World height in tiles.
        seed: Random seed.
        config: Moisture generation parameters.
        distance_to_water: Optional distance field (higher = further from water).

    Returns:
        2D moisture array in range [0, 1].
    """
    # Base fBm moisture noise
    moisture = fbm_noise_vectorized(
        width,
        height,
        seed + 300,
        config.noise.base_wavelength,
        octaves=config.noise.octaves,
        gain=config.noise.gain,
    )

    # Normalize to [0, 1]
    moisture = (moisture + 1.0) / 2.0

    # Blend with distance-to-water if provided
    if distance_to_water is not None:
        # Normalize distance to [0, 1] (closer to water = higher moisture)
        max_dist = np.max(distance_to_water)
        if max_dist > 0:
            water_influence = 1.0 - (distance_to_water / max_dist)
        else:
            water_influence = np.ones_like(distance_to_water)

        noise_weight = 1.0 - config.water_influence
        moisture = noise_weight * moisture + config.water_influence * water_influence

    return np.clip(moisture, 0.0, 1.0).astype(np.float32)


def make_forest_density(
    width: int,
    height: int,
    seed: int,
    config: ForestConfig,
) -> NDArray[np.float32]:
    """Generate forest density field.

    Creates clustered blob patterns for forest placement.

    Args:
        width: World width in tiles.
        height: World height in tiles.
        seed: Random seed.
        config: Forest generation parameters.

    Returns:
        2D forest density array in range [0, 1].
    """
    # Base noise
    noise = fbm_noise_vectorized(
        width,
        height,
        seed + 400,
        config.base_wavelength,
        octaves=config.octaves,
    )

    # Normalize to [0, 1]
    noise = (noise + 1.0) / 2.0

    # Apply smoothstep for clustered blobs (dense cores, soft edges)
    density = smoothstep(config.smoothstep_low, config.smoothstep_high, noise)

    return density


def make_beach_width_noise(
    width: int,
    height: int,
    seed: int,
    base_wavelength: float = 500,
    octaves: int = 3,
) -> NDArray[np.float32]:
    """Generate noise field for varying beach widths.

    Args:
        width: World width in tiles.
        height: World height in tiles.
        seed: Random seed.
        base_wavelength: Noise wavelength.
        octaves: Noise octaves.

    Returns:
        2D noise array in range [0, 1].
    """
    noise = fbm_noise_vectorized(
        width,
        height,
        seed + 500,
        base_wavelength,
        octaves=octaves,
    )

    # Normalize to [0, 1]
    return ((noise + 1.0) / 2.0).astype(np.float32)


def make_shallow_water_noise(
    width: int,
    height: int,
    seed: int,
    base_wavelength: float = 800,
    octaves: int = 3,
) -> NDArray[np.float32]:
    """Generate noise field for shallow water patches.

    Args:
        width: World width in tiles.
        height: World height in tiles.
        seed: Random seed.
        base_wavelength: Noise wavelength.
        octaves: Noise octaves.

    Returns:
        2D noise array in range [0, 1].
    """
    noise = fbm_noise_vectorized(
        width,
        height,
        seed + 600,
        base_wavelength,
        octaves=octaves,
    )

    # Normalize to [0, 1]
    return ((noise + 1.0) / 2.0).astype(np.float32)


def compute_slope(elevation: NDArray[np.float32]) -> NDArray[np.float32]:
    """Compute slope magnitude from elevation.

    Args:
        elevation: 2D elevation array.

    Returns:
        2D slope magnitude array (|gradient|).
    """
    # Compute gradients
    grad_y, grad_x = np.gradient(elevation)

    # Magnitude
    slope = np.sqrt(grad_x**2 + grad_y**2).astype(np.float32)

    return slope
