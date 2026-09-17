"""Settlement site selection.

Picks, deterministically for a given map, a walkable grass or dirt tile that is
surrounded by enough trees, rocks, bushes and at least one water tile. See
`docs/05_jev_agents_design.md` for the contract.

The search runs on the raw floor array with numpy so it stays fast on the
4000x4000 island map: a coarse grid of candidate centres is scored with integral
images, then the best candidates are verified exactly.
"""

from collections.abc import Iterable, Sequence

import numpy as np
import structlog
from numpy.typing import NDArray

from .state import World, WorldObject
from .types import Position

logger = structlog.get_logger()

# Contract thresholds (docs/05_jev_agents_design.md, "Settlement site").
SETTLEMENT_RADIUS = 30
MIN_TREES = 15
MIN_ROCKS = 15
MIN_BUSHES = 8

# Coarse cell size for the candidate grid and the integral images.
CELL_SIZE = 16
# How many coarse candidates get an exact re-count.
REFINE_CANDIDATES = 400

# Floor values as stored in the floor array (mirrors state._FLOOR_VALUE_PROPERTIES).
FLOOR_DEEP_WATER = 0
FLOOR_SHALLOW_WATER = 1
FLOOR_GRASS = 3
FLOOR_DIRT = 4

SITE_FLOOR_VALUES = (FLOOR_GRASS, FLOOR_DIRT)
WATER_FLOOR_VALUES = (FLOOR_DEEP_WATER, FLOOR_SHALLOW_WATER)

ROCK_TYPES = frozenset({"rock_small", "rock_medium", "rock_large", "boulder"})

# Object types that occupy a tile so an entity cannot spawn on it.
# Local to this module: the mechanics track owns the authoritative rules.
BLOCKING_OBJECT_TYPES = frozenset({"tree"} | ROCK_TYPES)

_FLOOR_TYPE_TO_VALUE: dict[str, int] = {
    "deep_water": 0,
    "shallow_water": 1,
    "sand": 2,
    "grass": 3,
    "dirt": 4,
    "mountain": 5,
    "stone": 6,
}

# Above this many tiles we refuse to rebuild a floor array tile by tile.
_MAX_TILES_WITHOUT_FLOOR_ARRAY = 512 * 512


class NoSettlementSiteError(RuntimeError):
    """Raised when no tile on the map satisfies the settlement requirements."""


def floor_array_of(world: World) -> NDArray[np.uint8]:
    """Return the world's floor values as a (height, width) uint8 array.

    Worlds built from terrain generation already carry one; small synthetic
    worlds are rebuilt tile by tile.
    """
    array = world._floor_array  # noqa: SLF001 - no public accessor exists yet
    if array is not None:
        return array

    tile_count = world.width * world.height
    if tile_count > _MAX_TILES_WITHOUT_FLOOR_ARRAY:
        raise ValueError(
            f"World of {tile_count} tiles has no floor array; "
            "cannot rebuild it tile by tile"
        )

    rebuilt = np.zeros((world.height, world.width), dtype=np.uint8)
    for y in range(world.height):
        for x in range(world.width):
            tile = world.get_tile(Position(x=x, y=y))
            rebuilt[y, x] = _FLOOR_TYPE_TO_VALUE.get(tile.floor_type, 6)
    return rebuilt


def _category_of(object_type: str) -> str:
    """Map an object type to a settlement resource category, or ""."""
    if object_type == "tree":
        return "tree"
    if object_type == "bush":
        return "bush"
    if object_type in ROCK_TYPES:
        return "rock"
    return ""


def _coarse_counts(
    positions: Sequence[tuple[int, int]], cells_y: int, cells_x: int
) -> NDArray[np.int32]:
    """Count positions per coarse cell."""
    counts = np.zeros((cells_y, cells_x), dtype=np.int32)
    if not positions:
        return counts
    arr = np.asarray(positions, dtype=np.int64)
    cy = arr[:, 1] // CELL_SIZE
    cx = arr[:, 0] // CELL_SIZE
    np.add.at(counts, (cy, cx), 1)
    return counts


def _box_sums(counts: NDArray[np.int32], half_cells: int) -> NDArray[np.int32]:
    """For every cell, the sum over the square of cells within `half_cells`."""
    padded = np.pad(counts, half_cells, mode="constant")
    integral = padded.cumsum(axis=0).cumsum(axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode="constant")
    size = 2 * half_cells + 1
    rows, cols = counts.shape
    return (
        integral[size : size + rows, size : size + cols]
        - integral[0:rows, size : size + cols]
        - integral[size : size + rows, 0:cols]
        + integral[0:rows, 0:cols]
    )


def _tile_mask_to_cells(
    mask: NDArray[np.bool_], cells_y: int, cells_x: int
) -> NDArray[np.int32]:
    """Sum a per-tile boolean mask into per-cell counts."""
    height, width = mask.shape
    padded = np.zeros((cells_y * CELL_SIZE, cells_x * CELL_SIZE), dtype=np.int32)
    padded[:height, :width] = mask
    blocks = padded.reshape(cells_y, CELL_SIZE, cells_x, CELL_SIZE)
    return blocks.sum(axis=(1, 3), dtype=np.int32)


def _exact_counts(
    by_cell: dict[tuple[int, int], list[tuple[int, int, str]]],
    centre: Position,
) -> dict[str, int]:
    """Exact resource counts within SETTLEMENT_RADIUS (Chebyshev) of centre."""
    totals = {"tree": 0, "rock": 0, "bush": 0}
    reach = SETTLEMENT_RADIUS // CELL_SIZE + 1
    cx0 = centre.x // CELL_SIZE
    cy0 = centre.y // CELL_SIZE
    for cy in range(cy0 - reach, cy0 + reach + 1):
        for cx in range(cx0 - reach, cx0 + reach + 1):
            for x, y, category in by_cell.get((cx, cy), ()):
                if (
                    abs(x - centre.x) <= SETTLEMENT_RADIUS
                    and abs(y - centre.y) <= SETTLEMENT_RADIUS
                ):
                    totals[category] += 1
    return totals


def _has_water_near(floor: NDArray[np.uint8], centre: Position) -> bool:
    """Whether a water tile lies within SETTLEMENT_RADIUS of centre."""
    height, width = floor.shape
    y0 = max(0, centre.y - SETTLEMENT_RADIUS)
    y1 = min(height, centre.y + SETTLEMENT_RADIUS + 1)
    x0 = max(0, centre.x - SETTLEMENT_RADIUS)
    x1 = min(width, centre.x + SETTLEMENT_RADIUS + 1)
    window = floor[y0:y1, x0:x1]
    return bool(np.isin(window, WATER_FLOOR_VALUES).any())


def _balance_score(counts: dict[str, int]) -> tuple[float, float]:
    """Score a site: balanced supply first, then total supply.

    Both components are ratios against the minimum requirement, so a site with
    30 trees / 30 rocks / 16 bushes beats one with 100 trees and 15 rocks.
    """
    ratios = (
        counts["tree"] / MIN_TREES,
        counts["rock"] / MIN_ROCKS,
        counts["bush"] / MIN_BUSHES,
    )
    return (min(ratios), sum(ratios))


def resource_counts(
    world: World, objects: Iterable[WorldObject], centre: Position
) -> dict[str, int]:
    """Resource counts (tree/rock/bush) within the settlement radius of centre."""
    by_cell = _index_objects(objects)
    return _exact_counts(by_cell, centre)


def _index_objects(
    objects: Iterable[WorldObject],
) -> dict[tuple[int, int], list[tuple[int, int, str]]]:
    """Bucket relevant objects into coarse cells."""
    by_cell: dict[tuple[int, int], list[tuple[int, int, str]]] = {}
    for obj in objects:
        category = _category_of(obj.object_type)
        if not category:
            continue
        key = (obj.position.x // CELL_SIZE, obj.position.y // CELL_SIZE)
        by_cell.setdefault(key, []).append((obj.position.x, obj.position.y, category))
    return by_cell


def find_settlement_site(world: World, objects: Iterable[WorldObject]) -> Position:
    """Find the settlement centre for a map.

    Args:
        world: The world (its floor array drives terrain checks).
        objects: All world objects (trees, rocks, bushes, ...).

    Returns:
        A walkable grass or dirt Position with at least MIN_TREES trees,
        MIN_ROCKS rocks, MIN_BUSHES bushes and one water tile within
        SETTLEMENT_RADIUS, preferring the most balanced supply.

    Raises:
        NoSettlementSiteError: If no tile satisfies the requirements.
    """
    objects = list(objects)
    floor = floor_array_of(world)
    height, width = floor.shape

    positions: dict[str, list[tuple[int, int]]] = {
        "tree": [],
        "rock": [],
        "bush": [],
    }
    for obj in objects:
        category = _category_of(obj.object_type)
        if category:
            positions[category].append((obj.position.x, obj.position.y))

    cells_y = (height + CELL_SIZE - 1) // CELL_SIZE
    cells_x = (width + CELL_SIZE - 1) // CELL_SIZE
    # A cell box of this half-width covers every tile within SETTLEMENT_RADIUS,
    # so the coarse counts are an upper bound and the filter never drops a
    # qualifying site.
    half_cells = SETTLEMENT_RADIUS // CELL_SIZE + 1

    coarse = {
        category: _box_sums(
            _coarse_counts(positions[category], cells_y, cells_x), half_cells
        )
        for category in positions
    }

    water_mask = np.isin(floor, WATER_FLOOR_VALUES)
    site_mask = np.isin(floor, SITE_FLOOR_VALUES)
    water_cells = _tile_mask_to_cells(water_mask, cells_y, cells_x)
    water_box = _box_sums(water_cells, half_cells)

    # Candidate centres: the tile at the middle of each coarse cell.
    centre_y = np.minimum(np.arange(cells_y) * CELL_SIZE + CELL_SIZE // 2, height - 1)
    centre_x = np.minimum(np.arange(cells_x) * CELL_SIZE + CELL_SIZE // 2, width - 1)
    centre_site = site_mask[np.ix_(centre_y, centre_x)]

    eligible = (
        centre_site
        & (coarse["tree"] >= MIN_TREES)
        & (coarse["rock"] >= MIN_ROCKS)
        & (coarse["bush"] >= MIN_BUSHES)
        & (water_box > 0)
    )

    rough_score = np.minimum(
        np.minimum(coarse["tree"] / MIN_TREES, coarse["rock"] / MIN_ROCKS),
        coarse["bush"] / MIN_BUSHES,
    ) + 0.001 * (
        coarse["tree"] / MIN_TREES
        + coarse["rock"] / MIN_ROCKS
        + coarse["bush"] / MIN_BUSHES
    )
    rough_score = np.where(eligible, rough_score, -1.0)

    cell_indices = np.argwhere(eligible)
    if cell_indices.size == 0:
        raise NoSettlementSiteError(
            "No candidate cell meets the settlement resource requirements"
        )

    scores = rough_score[cell_indices[:, 0], cell_indices[:, 1]]
    # Deterministic ordering: score descending, then (cy, cx) ascending.
    order = sorted(
        range(len(cell_indices)),
        key=lambda i: (
            -float(scores[i]),
            int(cell_indices[i][0]),
            int(cell_indices[i][1]),
        ),
    )

    by_cell = _index_objects(objects)
    best: tuple[tuple[float, float], Position] | None = None
    checked = 0
    for index in order:
        if checked >= REFINE_CANDIDATES:
            break
        cy, cx = int(cell_indices[index][0]), int(cell_indices[index][1])
        centre = _pick_site_tile(site_mask, cx, cy, width, height)
        if centre is None:
            continue
        checked += 1
        counts = _exact_counts(by_cell, centre)
        if (
            counts["tree"] < MIN_TREES
            or counts["rock"] < MIN_ROCKS
            or counts["bush"] < MIN_BUSHES
        ):
            continue
        if not _has_water_near(floor, centre):
            continue
        score = _balance_score(counts)
        if best is None or score > best[0]:
            best = (score, centre)

    if best is None:
        raise NoSettlementSiteError(
            "No tile satisfies the settlement requirements on this map"
        )

    logger.info(
        "settlement_site_selected",
        x=best[1].x,
        y=best[1].y,
        balance=round(best[0][0], 3),
    )
    return best[1]


def _pick_site_tile(
    site_mask: NDArray[np.bool_], cx: int, cy: int, width: int, height: int
) -> Position | None:
    """Pick the first grass/dirt tile inside a coarse cell, scanning row-major."""
    y0 = cy * CELL_SIZE
    y1 = min(height, y0 + CELL_SIZE)
    x0 = cx * CELL_SIZE
    x1 = min(width, x0 + CELL_SIZE)
    block = site_mask[y0:y1, x0:x1]
    hits = np.argwhere(block)
    if hits.size == 0:
        return None
    # Prefer the tile nearest the cell centre for a tidy settlement centre.
    mid = CELL_SIZE // 2
    best = min(
        (tuple(int(v) for v in hit) for hit in hits),
        key=lambda h: (abs(h[0] - mid) + abs(h[1] - mid), h[0], h[1]),
    )
    return Position(x=x0 + best[1], y=y0 + best[0])


def is_free_walkable(world: World, position: Position) -> bool:
    """Whether an entity can be spawned on this tile right now."""
    if not world.in_bounds(position):
        return False
    if not world.is_walkable(position):
        return False
    if world.is_position_occupied(position):
        return False
    return not any(
        obj.object_type in BLOCKING_OBJECT_TYPES
        for obj in world.get_objects_at(position)
    )


def nearest_free_walkable(
    world: World, centre: Position, max_radius: int = 64
) -> Position:
    """Find the nearest free walkable tile to centre (spiral / ring search).

    Tiles are scanned ring by ring in Chebyshev distance, and within a ring in
    a fixed order, so the result is deterministic.

    Raises:
        NoSettlementSiteError: If no free tile is found within max_radius.
    """
    if is_free_walkable(world, centre):
        return centre

    for radius in range(1, max_radius + 1):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                candidate = Position(x=centre.x + dx, y=centre.y + dy)
                if is_free_walkable(world, candidate):
                    return candidate

    raise NoSettlementSiteError(
        f"No free walkable tile within {max_radius} tiles of {centre}"
    )
