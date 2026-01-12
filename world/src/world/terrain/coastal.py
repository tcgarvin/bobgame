"""Coastal refinement: connected components, morphological smoothing."""

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage


def keep_largest_component(
    land_mask: NDArray[np.bool_],
    connectivity: int = 2,
) -> NDArray[np.bool_]:
    """Keep only the largest connected land component.

    Removes secondary islands and isolated land patches.

    Args:
        land_mask: Boolean mask where True = land.
        connectivity: 1 for 4-connected, 2 for 8-connected.

    Returns:
        Land mask with only largest component.
    """
    # Label connected components
    structure = ndimage.generate_binary_structure(2, connectivity)
    labeled, num_features = ndimage.label(land_mask, structure=structure)

    if num_features == 0:
        return land_mask

    # Find the largest component
    component_sizes = ndimage.sum(land_mask, labeled, range(1, num_features + 1))
    largest_label = np.argmax(component_sizes) + 1  # Labels start at 1

    # Keep only the largest
    return labeled == largest_label


def majority_smooth(
    land_mask: NDArray[np.bool_],
    iterations: int = 2,
    land_threshold: int = 2,
    water_threshold: int = 6,
) -> NDArray[np.bool_]:
    """Apply majority filter smoothing to coastline.

    Removes thin spikes and fills small holes.

    Args:
        land_mask: Boolean mask where True = land.
        iterations: Number of smoothing passes.
        land_threshold: Land with <= this many land neighbors becomes water.
        water_threshold: Water with >= this many land neighbors becomes land.

    Returns:
        Smoothed land mask.
    """
    result = land_mask.copy()

    # 3x3 kernel for counting neighbors
    kernel = np.array(
        [[1, 1, 1], [1, 0, 1], [1, 1, 1]], dtype=np.int32
    )  # 8-connected, exclude center

    for _ in range(iterations):
        # Count land neighbors for each cell
        neighbor_count = ndimage.convolve(
            result.astype(np.int32), kernel, mode="constant", cval=0
        )

        # Apply rules
        # Land with too few neighbors -> water (thin spikes)
        thin_spikes = result & (neighbor_count <= land_threshold)
        result = result & ~thin_spikes

        # Water with too many land neighbors -> land (pinholes)
        pinholes = ~result & (neighbor_count >= water_threshold)
        result = result | pinholes

    return result


def compute_distance_to_water(
    land_mask: NDArray[np.bool_],
    river_mask: NDArray[np.bool_] | None = None,
) -> NDArray[np.float32]:
    """Compute distance from each land cell to nearest water.

    Args:
        land_mask: Boolean mask where True = land.
        river_mask: Optional river mask to treat as water.

    Returns:
        Distance field (0 at water edge, increasing inland).
    """
    # Combined water mask: ocean + rivers
    water_mask = ~land_mask
    if river_mask is not None:
        water_mask = water_mask | river_mask

    # Distance transform from water
    distance = ndimage.distance_transform_edt(~water_mask).astype(np.float32)

    return distance


def compute_distance_to_land(
    land_mask: NDArray[np.bool_],
) -> NDArray[np.float32]:
    """Compute distance from each water cell to nearest land.

    Args:
        land_mask: Boolean mask where True = land.

    Returns:
        Distance field (0 at land edge, increasing into water).
    """
    # Distance transform from land
    distance = ndimage.distance_transform_edt(~land_mask).astype(np.float32)

    return distance
