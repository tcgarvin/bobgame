"""Settlement site selection.

Picks, deterministically for a given map, a walkable grass or dirt tile that is
surrounded by enough trees, rocks, bushes, reeds, clay and at least one water
tile, with a clear area to build on. See `docs/08_building.md` ("Map and
settlement site") for the contract.

The search runs on the raw floor array with numpy so it stays fast on the
4000x4000 island map: integral images give exact box sums for every tile at
once, so every tile is tested and scored, not a sample of candidates.
"""

from collections.abc import Iterable, Mapping

import numpy as np
import structlog
from numpy.typing import NDArray

from .state import DEFAULT_ENTITY_TYPE, World, WorldObject
from .types import Position

logger = structlog.get_logger()

# Contract thresholds (docs/08_building.md, "Map and settlement site"). Counts
# are within SETTLEMENT_RADIUS of the centre, Chebyshev (a 61 x 61 box).
SETTLEMENT_RADIUS = 30
MIN_TREES = 120
MIN_ROCKS = 50
MIN_BUSHES = 12
MIN_REEDS = 25
MIN_CLAY = 12

# Ore (docs/10_metal_and_sleep.md, section 5). A site needs metal within reach
# but none on its doorstep, so ore stays a trip: the requirement counts veins in
# the ring between ORE_EXCLUSION_RADIUS and ORE_SEARCH_RADIUS of the centre
# (Chebyshev), and the generator deletes every vein inside the exclusion radius
# of the chosen site. Counting the ring rather than the whole disc is what makes
# that safe: deleting the inner veins cannot make the winning tile ineligible or
# change its score, so the site found on the pruned map is the site the pruning
# was built around.
ORE_SEARCH_RADIUS = 200
ORE_EXCLUSION_RADIUS = 60
MIN_COPPER_VEINS = 3
MIN_IRON_VEINS = 2

ORE_MINIMUMS: Mapping[str, int] = {
    "copper_vein": MIN_COPPER_VEINS,
    "iron_vein": MIN_IRON_VEINS,
}

MINIMUMS: Mapping[str, int] = {
    "tree": MIN_TREES,
    "rock": MIN_ROCKS,
    "bush": MIN_BUSHES,
    "reeds": MIN_REEDS,
    "clay": MIN_CLAY,
}

# The town needs room: a CLEARING_SIZE square around the centre in which at
# least CLEARING_MIN_FRACTION of the tiles are buildable (grass or dirt with no
# natural object on them).
CLEARING_SIZE = 15
CLEARING_MIN_FRACTION = 0.9
# A wider square whose openness is scored (not required), so that among
# qualifying sites the finder prefers the one with room to grow.
GROWTH_SIZE = 27
# At least this share of the settlement box must be dry, walkable land;
# a centre on a spit with water on three sides is a poor town.
MIN_LAND_FRACTION = 0.6
# Supply beyond this multiple of the minimum earns nothing more, so a site
# drowning in trees cannot outscore a balanced one.
SUPPLY_RATIO_CAP = 2.5

# Floor values as stored in the floor array (mirrors state._FLOOR_VALUE_PROPERTIES).
FLOOR_DEEP_WATER = 0
FLOOR_SHALLOW_WATER = 1
FLOOR_SAND = 2
FLOOR_GRASS = 3
FLOOR_DIRT = 4

SITE_FLOOR_VALUES = (FLOOR_GRASS, FLOOR_DIRT)
WATER_FLOOR_VALUES = (FLOOR_DEEP_WATER, FLOOR_SHALLOW_WATER)
DRY_LAND_FLOOR_VALUES = (FLOOR_SAND, FLOOR_GRASS, FLOOR_DIRT)

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
    """Map an object type to a settlement resource category, or "".

    Ore veins are their own categories: they are required near a site but,
    unlike the five gathering resources, they do not feed the score.
    """
    if object_type in ORE_MINIMUMS:
        return object_type
    if object_type == "tree":
        return "tree"
    if object_type == "bush":
        return "bush"
    if object_type in ROCK_TYPES:
        return "rock"
    if object_type == "reeds":
        return "reeds"
    if object_type == "clay_deposit":
        return "clay"
    return ""


def _box_sums(counts: NDArray[np.int32], half: int) -> NDArray[np.int32]:
    """For every tile, the sum over the (2*half+1) square centred on it.

    Tiles beyond the map edge count as zero. One integral image, so the cost
    is independent of the box size.
    """
    padded = np.pad(counts, half, mode="constant")
    integral = padded.cumsum(axis=0, dtype=np.int32).cumsum(axis=1, dtype=np.int32)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode="constant")
    size = 2 * half + 1
    rows, cols = counts.shape
    return (
        integral[size : size + rows, size : size + cols]
        - integral[0:rows, size : size + cols]
        - integral[size : size + rows, 0:cols]
        + integral[0:rows, 0:cols]
    )


def _category_grids(
    objects: Iterable[tuple[int, int, str]], height: int, width: int
) -> dict[str, NDArray[np.int32]]:
    """Per-tile counts for every resource category and both vein kinds.

    Args:
        objects: (x, y, object_type) triples.
        height: Map height in tiles.
        width: Map width in tiles.
    """
    grids = {
        category: np.zeros((height, width), dtype=np.int32)
        for category in (*MINIMUMS, *ORE_MINIMUMS)
    }
    for x, y, object_type in objects:
        category = _category_of(object_type)
        if category and 0 <= x < width and 0 <= y < height:
            grids[category][y, x] += 1
    return grids


def ore_in_reach(
    grids: Mapping[str, NDArray[np.int32]], vein_type: str
) -> NDArray[np.int32]:
    """Veins of one kind in the ring ORE_EXCLUSION_RADIUS..ORE_SEARCH_RADIUS.

    The inner box is subtracted so that the count does not depend on the veins
    the generator is about to delete around the chosen site.
    """
    return _box_sums(grids[vein_type], ORE_SEARCH_RADIUS) - _box_sums(
        grids[vein_type], ORE_EXCLUSION_RADIUS
    )


def resource_counts(
    world: World, objects: Iterable[WorldObject], centre: Position
) -> dict[str, int]:
    """Resource counts by category within the settlement radius of centre.

    Returns:
        {"tree", "rock", "bush", "reeds", "clay"} -> count. Ore veins are not
        counted here: they live far outside the settlement radius by design.
    """
    totals = {category: 0 for category in MINIMUMS}
    for obj in objects:
        category = _category_of(obj.object_type)
        if (
            category in totals
            and abs(obj.position.x - centre.x) <= SETTLEMENT_RADIUS
            and abs(obj.position.y - centre.y) <= SETTLEMENT_RADIUS
        ):
            totals[category] += 1
    return totals


def find_settlement_site(world: World, objects: Iterable[WorldObject]) -> Position:
    """Find the settlement centre for a map.

    Args:
        world: The world (its floor array drives terrain checks).
        objects: All world objects (trees, rocks, bushes, reeds, clay, veins).

    Returns:
        The settlement centre; see `select_site`.

    Raises:
        NoSettlementSiteError: If no tile satisfies the requirements.
    """
    return select_site(
        floor_array_of(world),
        ((obj.position.x, obj.position.y, obj.object_type) for obj in objects),
    )


def select_site(
    floor: NDArray[np.uint8],
    objects: Iterable[tuple[int, int, str]],
    require_ore: bool = True,
) -> Position:
    """Choose the settlement centre from a floor array and object triples.

    Every tile is tested exactly (box sums over integral images), so the
    result does not depend on a candidate grid. Among qualifying tiles the
    finder prefers a balanced supply of all five resources and open ground to
    grow into - in practice a clearing on a forest edge beside fresh water
    with an outcrop in reach. Ore only gates eligibility (see ORE_MINIMUMS);
    it never moves the score.

    Args:
        floor: Floor values, shape (height, width).
        objects: (x, y, object_type) triples for every object on the map.
        require_ore: Whether the ORE_MINIMUMS gate eligibility. The generator
            turns it off for the first pass, when it is still deciding where to
            put the ore (see `terrain.objects.fit_ore_to_settlement`); every
            other caller leaves it on.

    Returns:
        A grass or dirt Position that has, within SETTLEMENT_RADIUS, at least
        the MINIMUMS of every resource and one water tile, enough dry land, a
        mostly buildable CLEARING_SIZE square around it, and the ORE_MINIMUMS
        in the ore ring around it. Deterministic: the best score wins and ties
        go to the smallest (y, x).

    Raises:
        NoSettlementSiteError: If no tile satisfies the requirements.
    """
    height, width = floor.shape
    grids = _category_grids(objects, height, width)

    natural = np.zeros((height, width), dtype=np.bool_)
    for category in MINIMUMS:
        natural |= grids[category] > 0
    buildable = np.isin(floor, SITE_FLOOR_VALUES) & ~natural

    box_area = (2 * SETTLEMENT_RADIUS + 1) ** 2
    eligible = buildable.copy()
    ratios: list[NDArray[np.float32]] = []
    for category, minimum in MINIMUMS.items():
        supply = _box_sums(grids[category], SETTLEMENT_RADIUS)
        eligible &= supply >= minimum
        ratios.append(
            np.minimum(supply / np.float32(minimum), SUPPLY_RATIO_CAP).astype(
                np.float32
            )
        )

    if require_ore:
        for vein_type, minimum in ORE_MINIMUMS.items():
            eligible &= ore_in_reach(grids, vein_type) >= minimum

    water = _box_sums(
        np.isin(floor, WATER_FLOOR_VALUES).astype(np.int32), SETTLEMENT_RADIUS
    )
    dry_land = _box_sums(
        np.isin(floor, DRY_LAND_FLOOR_VALUES).astype(np.int32), SETTLEMENT_RADIUS
    )
    clearing = _box_sums(buildable.astype(np.int32), CLEARING_SIZE // 2)
    growth = _box_sums(buildable.astype(np.int32), GROWTH_SIZE // 2)

    eligible &= water > 0
    eligible &= dry_land >= MIN_LAND_FRACTION * box_area
    eligible &= clearing >= CLEARING_MIN_FRACTION * CLEARING_SIZE**2

    if not eligible.any():
        raise NoSettlementSiteError(
            "No tile satisfies the settlement requirements on this map"
        )

    stacked = np.stack(ratios)
    balance = stacked.min(axis=0)
    plenty = stacked.mean(axis=0)
    openness = growth.astype(np.float32) / np.float32(GROWTH_SIZE**2)
    score = np.where(eligible, balance + 0.3 * plenty + openness, -np.inf)

    # argmax returns the first maximum in row-major order: smallest (y, x).
    best_y, best_x = np.unravel_index(int(np.argmax(score)), score.shape)
    site = Position(x=int(best_x), y=int(best_y))
    logger.info(
        "settlement_site_selected",
        x=site.x,
        y=site.y,
        score=round(float(score[best_y, best_x]), 3),
        qualifying_tiles=int(eligible.sum()),
    )
    return site


def is_free_walkable(
    world: World, position: Position, entity_type: str = DEFAULT_ENTITY_TYPE
) -> bool:
    """Whether an entity of this type can stand on this tile right now.

    Combines terrain walkability, built blockers (walls for everyone, doors for
    wolves), entity occupancy and the natural objects nobody spawns on top of.
    """
    if not world.in_bounds(position):
        return False
    if not world.is_passable(position, entity_type):
        return False
    if world.is_position_occupied(position):
        return False
    return not any(
        obj.object_type in BLOCKING_OBJECT_TYPES
        for obj in world.get_objects_at(position)
    )


def nearest_free_walkable(
    world: World,
    centre: Position,
    max_radius: int = 64,
    entity_type: str = DEFAULT_ENTITY_TYPE,
) -> Position:
    """Find the nearest free walkable tile to centre (spiral / ring search).

    Tiles are scanned ring by ring in Chebyshev distance, and within a ring in
    a fixed order, so the result is deterministic.

    Raises:
        NoSettlementSiteError: If no free tile is found within max_radius.
    """
    if is_free_walkable(world, centre, entity_type):
        return centre

    for radius in range(1, max_radius + 1):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                candidate = Position(x=centre.x + dx, y=centre.y + dy)
                if is_free_walkable(world, candidate, entity_type):
                    return candidate

    raise NoSettlementSiteError(
        f"No free walkable tile within {max_radius} tiles of {centre}"
    )
