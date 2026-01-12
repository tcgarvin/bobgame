"""Hydrology: Priority-Flood fill, D8 flow direction, river carving, fords.

Based on Barnes et al. (2014) Priority-Flood algorithm for depression filling.
"""

import heapq
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .config import HydrologyConfig

# D8 directions: N, NE, E, SE, S, SW, W, NW (clockwise from north)
D8_DY = np.array([-1, -1, 0, 1, 1, 1, 0, -1], dtype=np.int32)
D8_DX = np.array([0, 1, 1, 1, 0, -1, -1, -1], dtype=np.int32)

# No-data value for flow direction
FLOW_NODATA = 255


def priority_flood_fill(
    elevation: NDArray[np.float32],
    ocean_mask: NDArray[np.bool_],
) -> NDArray[np.float32]:
    """Fill depressions using Priority-Flood algorithm.

    Ensures all land cells can drain to ocean by filling pits.
    Based on Barnes et al. (2014) "Priority-Flood" algorithm.

    Args:
        elevation: Raw elevation field.
        ocean_mask: Boolean mask where True = ocean.

    Returns:
        Filled elevation where all cells drain to ocean.
    """
    height, width = elevation.shape
    filled = elevation.copy()
    closed = np.zeros((height, width), dtype=bool)

    # Priority queue: (elevation, y, x)
    pq: list[tuple[float, int, int]] = []

    # Initialize with ocean boundary cells' land neighbors
    for y in range(height):
        for x in range(width):
            if ocean_mask[y, x]:
                closed[y, x] = True
                # Add land neighbors to queue
                for d in range(8):
                    ny = y + D8_DY[d]
                    nx = x + D8_DX[d]
                    if 0 <= ny < height and 0 <= nx < width:
                        if not closed[ny, nx] and not ocean_mask[ny, nx]:
                            heapq.heappush(pq, (float(elevation[ny, nx]), ny, nx))
                            closed[ny, nx] = True

    # Process queue
    while pq:
        elev, y, x = heapq.heappop(pq)

        # Update filled elevation if needed (pit filling)
        if filled[y, x] < elev:
            filled[y, x] = elev

        for d in range(8):
            ny = y + D8_DY[d]
            nx = x + D8_DX[d]
            if 0 <= ny < height and 0 <= nx < width and not closed[ny, nx]:
                closed[ny, nx] = True
                # Push with max of neighbor elevation and current
                neighbor_elev = max(float(filled[ny, nx]), float(filled[y, x]))
                heapq.heappush(pq, (neighbor_elev, ny, nx))
                filled[ny, nx] = neighbor_elev

    return filled


def compute_d8_flow_direction(
    elevation: NDArray[np.float32],
) -> NDArray[np.uint8]:
    """Compute D8 flow direction for each cell (vectorized).

    Each cell points to its steepest downslope neighbor (0-7).
    255 indicates no outflow (flat or pit).

    Args:
        elevation: Filled elevation field.

    Returns:
        Flow direction array (0-7 for D8 directions, 255 for no flow).
    """
    height, width = elevation.shape

    # Diagonal distance factor
    DIAG_DIST = 1.414

    # Compute drop (gradient) to each of 8 neighbors
    # Using np.roll to shift the elevation array
    drops = np.zeros((8, height, width), dtype=np.float32)

    for d in range(8):
        dy, dx = D8_DY[d], D8_DX[d]
        # Roll elevation to get neighbor values
        neighbor = np.roll(np.roll(elevation, -dy, axis=0), -dx, axis=1)

        # Compute drop (positive = downhill)
        drop = elevation - neighbor

        # Apply distance factor for diagonals
        if d % 2 == 1:  # Diagonal
            drop /= DIAG_DIST

        drops[d] = drop

    # Handle edges: set drop to -inf for out-of-bounds neighbors
    # Top edge (y=0): directions 0, 1, 7 (N, NE, NW) are invalid
    drops[0, 0, :] = -np.inf
    drops[1, 0, :] = -np.inf
    drops[7, 0, :] = -np.inf

    # Bottom edge (y=H-1): directions 3, 4, 5 (SE, S, SW) are invalid
    drops[3, -1, :] = -np.inf
    drops[4, -1, :] = -np.inf
    drops[5, -1, :] = -np.inf

    # Left edge (x=0): directions 5, 6, 7 (SW, W, NW) are invalid
    drops[5, :, 0] = -np.inf
    drops[6, :, 0] = -np.inf
    drops[7, :, 0] = -np.inf

    # Right edge (x=W-1): directions 1, 2, 3 (NE, E, SE) are invalid
    drops[1, :, -1] = -np.inf
    drops[2, :, -1] = -np.inf
    drops[3, :, -1] = -np.inf

    # Find steepest direction
    max_drop = np.max(drops, axis=0)
    flow_dir = np.argmax(drops, axis=0).astype(np.uint8)

    # Mark cells with no downhill neighbor
    flow_dir[max_drop <= 0] = FLOW_NODATA

    return flow_dir


def compute_flow_accumulation(
    flow_dir: NDArray[np.uint8],
) -> NDArray[np.uint32]:
    """Compute flow accumulation from flow directions (optimized).

    Each cell's value is the count of upstream cells that flow through it.

    Args:
        flow_dir: D8 flow direction array.

    Returns:
        Flow accumulation array.
    """
    from collections import deque

    height, width = flow_dir.shape
    accumulation = np.ones((height, width), dtype=np.uint32)

    # Count inflows using vectorized approach
    inflow_count = np.zeros((height, width), dtype=np.int32)

    # For each direction, count cells that flow in that direction
    for d in range(8):
        dy, dx = D8_DY[d], D8_DX[d]
        # Mask of cells flowing in direction d
        mask = flow_dir == d

        # Target cells are offset by (dy, dx) from source cells
        # We need to increment inflow_count at target positions
        # Using np.add.at for scattered addition
        source_ys, source_xs = np.where(mask)
        target_ys = source_ys + dy
        target_xs = source_xs + dx

        # Filter valid targets
        valid = (
            (target_ys >= 0)
            & (target_ys < height)
            & (target_xs >= 0)
            & (target_xs < width)
        )
        target_ys = target_ys[valid]
        target_xs = target_xs[valid]

        np.add.at(inflow_count, (target_ys, target_xs), 1)

    # Process cells with no inflow first (topological sort)
    # Use deque for O(1) popleft instead of O(n) list.pop(0)
    zero_inflow_ys, zero_inflow_xs = np.where(inflow_count == 0)
    queue: deque[tuple[int, int]] = deque(zip(zero_inflow_ys, zero_inflow_xs))

    while queue:
        y, x = queue.popleft()
        d = flow_dir[y, x]

        if d != FLOW_NODATA:
            ny = y + D8_DY[d]
            nx = x + D8_DX[d]
            if 0 <= ny < height and 0 <= nx < width:
                accumulation[ny, nx] += accumulation[y, x]
                inflow_count[ny, nx] -= 1
                if inflow_count[ny, nx] == 0:
                    queue.append((ny, nx))

    return accumulation


@dataclass
class River:
    """Represents a river path with its properties."""

    path: list[tuple[int, int]]  # (y, x) coordinates
    width: int
    fords: list[tuple[int, int]]  # Start indices of ford segments


def select_river_sources(
    land_mask: NDArray[np.bool_],
    elevation: NDArray[np.float32],
    distance_to_coast: NDArray[np.float32],
    rng: np.random.Generator,
    config: HydrologyConfig,
) -> list[tuple[int, int]]:
    """Select river source locations.

    Sources are inland, high elevation, and well-spaced.

    Args:
        land_mask: Boolean mask where True = land.
        elevation: Filled elevation field.
        distance_to_coast: Distance from each cell to coast.
        rng: Random number generator.
        config: Hydrology configuration.

    Returns:
        List of (y, x) source coordinates.
    """
    height, width = land_mask.shape

    # Find candidate cells: inland, land, high elevation
    min_dist = config.source_min_distance_from_coast
    land_elevations = elevation[land_mask]
    elev_threshold = np.quantile(land_elevations, 0.80) if len(land_elevations) > 0 else 0

    candidates = []
    for y in range(height):
        for x in range(width):
            if (
                land_mask[y, x]
                and distance_to_coast[y, x] >= min_dist
                and elevation[y, x] >= elev_threshold
            ):
                candidates.append((y, x))

    if not candidates:
        # Fallback: any land cell far enough from coast
        for y in range(height):
            for x in range(width):
                if land_mask[y, x] and distance_to_coast[y, x] >= min_dist / 2:
                    candidates.append((y, x))

    if not candidates:
        return []

    # Select sources with minimum spacing
    num_rivers = rng.integers(config.river_count_min, config.river_count_max + 1)
    selected: list[tuple[int, int]] = []
    min_spacing = config.source_min_spacing

    rng.shuffle(candidates)

    for cy, cx in candidates:
        if len(selected) >= num_rivers:
            break

        # Check spacing from existing sources
        too_close = False
        for sy, sx in selected:
            dist = np.sqrt((cy - sy) ** 2 + (cx - sx) ** 2)
            if dist < min_spacing:
                too_close = True
                break

        if not too_close:
            selected.append((cy, cx))

    return selected


def trace_river(
    source: tuple[int, int],
    flow_dir: NDArray[np.uint8],
    ocean_mask: NDArray[np.bool_],
    rng: np.random.Generator,
    temperature: float = 0.1,
    max_length: int = 10000,
) -> list[tuple[int, int]]:
    """Trace a river from source to ocean.

    Uses stochastic downhill choice for natural meander.

    Args:
        source: (y, x) source coordinates.
        flow_dir: D8 flow direction array.
        ocean_mask: Boolean mask where True = ocean.
        rng: Random number generator.
        temperature: Meander temperature (higher = more random).
        max_length: Maximum path length.

    Returns:
        List of (y, x) coordinates from source to ocean.
    """
    height, width = flow_dir.shape
    path = [source]
    y, x = source

    for _ in range(max_length):
        # Check if we reached ocean
        if ocean_mask[y, x]:
            break

        d = flow_dir[y, x]
        if d == FLOW_NODATA:
            break

        # Follow flow direction (with small random perturbation for meander)
        if rng.random() < temperature:
            # Try a random adjacent downhill direction
            possible_dirs = []
            for test_d in range(8):
                ny = y + D8_DY[test_d]
                nx = x + D8_DX[test_d]
                if 0 <= ny < height and 0 <= nx < width:
                    possible_dirs.append(test_d)
            if possible_dirs:
                d = rng.choice(possible_dirs)

        ny = y + D8_DY[d]
        nx = x + D8_DX[d]

        if not (0 <= ny < height and 0 <= nx < width):
            break

        y, x = ny, nx
        path.append((y, x))

    return path


def widen_river_path(
    path: list[tuple[int, int]],
    flow_acc: NDArray[np.uint32],
    config: HydrologyConfig,
) -> set[tuple[int, int]]:
    """Widen a river path based on flow accumulation.

    Args:
        path: River centerline path.
        flow_acc: Flow accumulation array.
        config: Hydrology configuration.

    Returns:
        Set of (y, x) coordinates for widened river.
    """
    height, width = flow_acc.shape
    river_cells: set[tuple[int, int]] = set()

    # Determine threshold for wider sections
    max_acc = np.max(flow_acc)
    wide_threshold = max_acc * 0.3 if max_acc > 0 else 1

    for y, x in path:
        acc = flow_acc[y, x]
        river_width = config.river_width_max if acc > wide_threshold else config.river_width_min

        # Add cells within radius
        radius = river_width // 2
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                ny, nx = y + dy, x + dx
                if 0 <= ny < height and 0 <= nx < width:
                    if dy * dy + dx * dx <= radius * radius:
                        river_cells.add((ny, nx))

    return river_cells


def place_fords(
    path: list[tuple[int, int]],
    elevation: NDArray[np.float32],
    rng: np.random.Generator,
    config: HydrologyConfig,
) -> list[int]:
    """Select ford locations along a river.

    Fords are placed in relatively flat sections, not near source or mouth.

    Args:
        path: River path.
        elevation: Elevation field.
        rng: Random number generator.
        config: Hydrology configuration.

    Returns:
        List of path indices where fords start.
    """
    if len(path) < config.ford_length * 3:
        return []

    # Find flat sections (low slope)
    candidates = []
    margin = len(path) // 5  # Avoid source and mouth

    for i in range(margin, len(path) - margin - config.ford_length):
        y1, x1 = path[i]
        y2, x2 = path[i + config.ford_length]
        slope = abs(elevation[y1, x1] - elevation[y2, x2]) / config.ford_length

        if slope < 0.01:  # Relatively flat
            candidates.append(i)

    if not candidates:
        # Fallback: random positions
        candidates = list(range(margin, len(path) - margin - config.ford_length))

    if not candidates:
        return []

    # Select ford positions with spacing
    num_fords = rng.integers(config.fords_per_river_min, config.fords_per_river_max + 1)
    min_spacing = len(path) // (num_fords + 1)

    selected: list[int] = []
    rng.shuffle(candidates)

    for idx in candidates:
        if len(selected) >= num_fords:
            break

        # Check spacing
        too_close = False
        for other in selected:
            if abs(idx - other) < min_spacing:
                too_close = True
                break

        if not too_close:
            selected.append(idx)

    return sorted(selected)


def carve_rivers(
    land_mask: NDArray[np.bool_],
    elevation: NDArray[np.float32],
    filled_elevation: NDArray[np.float32],
    flow_dir: NDArray[np.uint8],
    flow_acc: NDArray[np.uint32],
    distance_to_coast: NDArray[np.float32],
    rng: np.random.Generator,
    config: HydrologyConfig,
) -> tuple[NDArray[np.bool_], NDArray[np.bool_], list[River]]:
    """Carve rivers from sources to ocean.

    Args:
        land_mask: Boolean mask where True = land.
        elevation: Original elevation.
        filled_elevation: Pit-filled elevation.
        flow_dir: D8 flow direction.
        flow_acc: Flow accumulation.
        distance_to_coast: Distance from each cell to coast.
        rng: Random number generator.
        config: Hydrology configuration.

    Returns:
        Tuple of (river_mask, ford_mask, list of River objects).
    """
    height, width = land_mask.shape
    river_mask = np.zeros((height, width), dtype=bool)
    ford_mask = np.zeros((height, width), dtype=bool)
    ocean_mask = ~land_mask

    # Select river sources
    sources = select_river_sources(
        land_mask, filled_elevation, distance_to_coast, rng, config
    )

    rivers: list[River] = []

    for source in sources:
        # Trace river path
        path = trace_river(
            source,
            flow_dir,
            ocean_mask,
            rng,
            temperature=config.meander_temperature,
        )

        if len(path) < 10:  # Too short, skip
            continue

        # Widen river
        river_cells = widen_river_path(path, flow_acc, config)
        for y, x in river_cells:
            river_mask[y, x] = True

        # Place fords
        ford_indices = place_fords(path, elevation, rng, config)

        for ford_start in ford_indices:
            for i in range(ford_start, min(ford_start + config.ford_length, len(path))):
                y, x = path[i]
                # Mark ford cells (within river width)
                radius = config.river_width_max // 2
                for dy in range(-radius, radius + 1):
                    for dx in range(-radius, radius + 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < height and 0 <= nx < width:
                            if dy * dy + dx * dx <= radius * radius:
                                ford_mask[ny, nx] = True

        rivers.append(River(path=path, width=config.river_width_min, fords=ford_indices))

    return river_mask, ford_mask, rivers
