"""Object placement: trees, bushes, rocks, reeds, clay and ore veins.

Every placement pass is a vectorised Bernoulli draw against a probability
field. The fields multiply low-frequency "where does this kind of thing live"
masks into a base density so the landscape reads as places - forests with
clearings and soft edges, rock outcrops at mountain feet, reed beds and clay
banks along the rivers - rather than uniform speckle. Ore veins are the one
exception: they are placed as whole clusters around chosen seed tiles, because
a vein cluster has a size (3-8) and a per-map target count, not a density.
See docs/08_building.md and docs/10_metal_and_sleep.md, section 5.
"""

import logging
from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray
from scipy import special

from ..settlement import (
    ORE_EXCLUSION_RADIUS,
    ORE_MINIMUMS,
    ORE_SEARCH_RADIUS,
    NoSettlementSiteError,
    select_site,
)
from ..terrain_types import FloorType
from ..types import Position
from .config import ObjectPlacementConfig
from .noise import fbm_noise_vectorized, smoothstep

logger = logging.getLogger(__name__)

# Seed offsets for the placement noise masks (kept apart from the field seeds
# in fields.py, which use +100..+600).
_SEED_CANOPY = 1100
_SEED_OUTCROP = 1200
_SEED_REEDS = 1300
_SEED_CLAY = 1400
_SEED_BUSH = 1500
_SEED_VEIN = 1600

# Trees stop growing where the ground gets this steep.
_TREE_SLOPE_MAX = 0.5
# Share of the tree probability an outcrop's heart takes away.
_OUTCROP_TREE_SUPPRESSION = 0.85

# Vein cluster seeds need this much outcrop x highland under them; the rest of
# a cluster settles for less, so a cluster spills down the flanks of its outcrop.
_VEIN_SEED_SUITABILITY = 0.45
_VEIN_MEMBER_SUITABILITY = 0.18
# Vein densities in the config are per this many tiles of map area.
_VEIN_DENSITY_AREA = 1_000_000


class ObjectType(str, Enum):
    """Types of objects that can be placed."""

    TREE = "tree"
    BUSH = "bush"
    ROCK_SMALL = "rock_small"
    ROCK_MEDIUM = "rock_medium"
    ROCK_LARGE = "rock_large"
    BOULDER = "boulder"
    REEDS = "reeds"
    CLAY_DEPOSIT = "clay_deposit"
    COPPER_VEIN = "copper_vein"
    IRON_VEIN = "iron_vein"


@dataclass
class PlacedObject:
    """A placed object with position and type."""

    x: int
    y: int
    object_type: ObjectType
    object_id: str


@dataclass(frozen=True)
class PlacementFields:
    """The continuous fields object placement reads. All share one shape.

    Attributes:
        floor: Floor type values.
        forest_density: Broad forest regions in [0, 1].
        ridged_noise: Ridged noise in [0, 1], high along ridge lines.
        slope: Slope magnitude.
        dist_to_water: Distance to the nearest water of any kind.
        dist_to_coast: Distance to the ocean only.
        dist_to_fresh: Distance to the nearest river tile (fords included).
        dist_to_mountain: Distance to the nearest mountain tile.
    """

    floor: NDArray[np.uint8]
    forest_density: NDArray[np.float32]
    ridged_noise: NDArray[np.float32]
    slope: NDArray[np.float32]
    dist_to_water: NDArray[np.float32]
    dist_to_coast: NDArray[np.float32]
    dist_to_fresh: NDArray[np.float32]
    dist_to_mountain: NDArray[np.float32]


def uniformise(field: NDArray[np.float32]) -> NDArray[np.float32]:
    """Map a roughly Gaussian field to a roughly uniform one on [0, 1].

    Smoothed noise bunches up around its mean, which makes thresholds touchy.
    Pushing the z-score through the normal CDF spreads it out so a threshold
    of t leaves about (1 - t) of the map above it, whatever the noise scale.
    """
    z = (field - float(field.mean())) / (float(field.std()) + 1e-9)
    return (0.5 * (1.0 + special.erf(z / np.sqrt(2.0)))).astype(np.float32)


def _unit_noise(
    shape: tuple[int, int], seed: int, wavelength: float, octaves: int = 3
) -> NDArray[np.float32]:
    """fBm noise spread uniformly over [0, 1]."""
    height, width = shape
    return uniformise(
        fbm_noise_vectorized(width, height, seed, wavelength, octaves=octaves)
    )


def _floor_mask(floor: NDArray[np.uint8], *types: FloorType) -> NDArray[np.bool_]:
    return np.isin(floor, [floor_type.code for floor_type in types])


def _draw(
    probability: NDArray[np.float32],
    free: NDArray[np.bool_],
    rng: np.random.Generator,
) -> tuple[NDArray[np.intp], NDArray[np.intp]]:
    """Bernoulli-sample tiles; returns (ys, xs) in row-major order."""
    hits = (rng.random(probability.shape, dtype=np.float32) < probability) & free
    ys, xs = np.nonzero(hits)
    return ys, xs


def _to_objects(
    ys: NDArray[np.intp],
    xs: NDArray[np.intp],
    object_type: ObjectType,
    prefix: str,
) -> list[PlacedObject]:
    return [
        PlacedObject(
            x=int(x), y=int(y), object_type=object_type, object_id=f"{prefix}_{i}"
        )
        for i, (y, x) in enumerate(zip(ys, xs))
    ]


def canopy_field(
    fields: PlacementFields, seed: int, config: ObjectPlacementConfig
) -> NDArray[np.float32]:
    """Where tree cover is, in [0, 1]: forests with clearings, plus lone groves.

    The broad forest field decides regions; a grove-scale noise breaks the
    regions into stands and glades and scatters copses across open country.
    A narrow smoothstep gives dense interiors with a soft, short edge.
    """
    shape = fields.floor.shape
    grove = _unit_noise(shape, seed + _SEED_CANOPY, config.grove_wavelength)
    combined = 0.55 * uniformise(fields.forest_density) + 0.45 * grove
    edge = config.canopy_edge_softness
    canopy = smoothstep(
        config.canopy_threshold - edge, config.canopy_threshold + edge, combined
    )
    return canopy.astype(np.float32)


def place_trees(
    fields: PlacementFields,
    canopy: NDArray[np.float32],
    outcrop: NDArray[np.float32],
    rng: np.random.Generator,
    config: ObjectPlacementConfig,
) -> list[PlacedObject]:
    """Place trees: dense under the canopy, rare strays in the open.

    Trees keep off the ocean coast (wind, salt) over `tree_coast_distance` but
    come right down to river banks, leaving only a short towpath. Stony
    ground grows few trees, so an outcrop opens a glade in the forest.
    """
    valid = _floor_mask(fields.floor, FloorType.GRASS, FloorType.DIRT)
    probability = config.tree_base_density * canopy + config.tree_stray_density
    probability = probability * np.clip(
        fields.dist_to_coast / config.tree_coast_distance, 0, 1
    )
    probability = probability * smoothstep(1.0, 4.0, fields.dist_to_water)
    probability = probability * np.clip(1 - fields.slope / _TREE_SLOPE_MAX, 0, 1)
    probability = probability * (1.0 - _OUTCROP_TREE_SUPPRESSION * outcrop)
    ys, xs = _draw(probability.astype(np.float32), valid, rng)
    return _to_objects(ys, xs, ObjectType.TREE, "tree")


def place_bushes(
    fields: PlacementFields,
    canopy: NDArray[np.float32],
    occupied: NDArray[np.bool_],
    rng: np.random.Generator,
    seed: int,
    config: ObjectPlacementConfig,
) -> list[PlacedObject]:
    """Place berry bushes in thickets along forest edges and near water.

    The canopy edge (where canopy is neither 0 nor 1) is where bushes thrive;
    a patch noise gathers them into thickets rather than an even sprinkle.
    """
    valid = _floor_mask(fields.floor, FloorType.GRASS, FloorType.DIRT) & ~occupied
    edge = 4.0 * canopy * (1.0 - canopy)  # 1 at the forest edge, 0 deep in or out
    patch = _unit_noise(fields.floor.shape, seed + _SEED_BUSH, 40.0)
    thicket = smoothstep(0.5, 0.7, patch)
    water_band = smoothstep(
        float(config.bush_water_min_distance),
        float(config.bush_water_min_distance + 8),
        fields.dist_to_water,
    ) * (
        1.0
        - 0.7
        * smoothstep(
            float(config.bush_water_max_distance),
            float(config.bush_water_max_distance * 2),
            fields.dist_to_water,
        )
    )
    open_ground = 1.0 - canopy
    probability = (
        config.bush_base_density
        * (0.15 * open_ground + 0.85 * np.maximum(edge, thicket * open_ground))
        * water_band
    )
    ys, xs = _draw(probability.astype(np.float32), valid, rng)
    return _to_objects(ys, xs, ObjectType.BUSH, "bush")


def outcrop_field(
    fields: PlacementFields, seed: int, config: ObjectPlacementConfig
) -> NDArray[np.float32]:
    """Where rock gathers, in [0, 1]: stony patches along ridges, mountain-foot scree."""
    shape = fields.floor.shape
    patch = _unit_noise(shape, seed + _SEED_OUTCROP, config.outcrop_wavelength)
    foot = 1.0 - smoothstep(
        2.0, float(config.rock_mountain_reach), fields.dist_to_mountain
    )
    rockiness = 0.6 * patch + 0.4 * uniformise(fields.ridged_noise)
    outcrop = smoothstep(
        config.outcrop_threshold - 0.04, config.outcrop_threshold + 0.06, rockiness
    )
    # Scree at mountain feet is patchy too, not a uniform collar.
    scree = foot * smoothstep(0.35, 0.6, patch)
    return np.maximum(outcrop, scree).astype(np.float32)


def place_rocks(
    fields: PlacementFields,
    outcrop: NDArray[np.float32],
    occupied: NDArray[np.bool_],
    rng: np.random.Generator,
    config: ObjectPlacementConfig,
) -> list[PlacedObject]:
    """Place rocks: packed into outcrops, a thin scatter of strays elsewhere.

    Size follows the outcrop field, so an outcrop has boulders and large rocks
    at its heart and small stones round its rim; strays are small.
    """
    valid = (
        _floor_mask(fields.floor, FloorType.GRASS, FloorType.DIRT, FloorType.SAND)
        & ~occupied
    )
    probability = config.rock_base_density * outcrop + config.rock_stray_density
    ys, xs = _draw(probability.astype(np.float32), valid, rng)

    heart = outcrop[ys, xs] * rng.random(len(ys), dtype=np.float32)
    rocks: list[PlacedObject] = []
    for i, (y, x, h) in enumerate(zip(ys, xs, heart)):
        if h > 0.92:
            object_type = ObjectType.BOULDER
        elif h > 0.8:
            object_type = ObjectType.ROCK_LARGE
        elif h > 0.55:
            object_type = ObjectType.ROCK_MEDIUM
        else:
            object_type = ObjectType.ROCK_SMALL
        rocks.append(
            PlacedObject(
                x=int(x), y=int(y), object_type=object_type, object_id=f"rock_{i}"
            )
        )
    return rocks


def highland_field(
    fields: PlacementFields, config: ObjectPlacementConfig
) -> NDArray[np.float32]:
    """Where the ground rides high, in [0, 1]: ridges, hillsides, mountain feet.

    The generator has no elevation field at placement time, so high ground is
    read off what it does have: the ridged noise that drove the uplift, the
    slope, and the distance to the mountains. Any one of the three is enough,
    which is why they are combined with a maximum rather than a product.
    """
    ridge = smoothstep(0.55, 0.85, uniformise(fields.ridged_noise))
    foot = 1.0 - smoothstep(
        2.0, float(config.vein_mountain_reach), fields.dist_to_mountain
    )
    steep = smoothstep(config.vein_slope_min, 3.0 * config.vein_slope_min, fields.slope)
    return np.maximum(np.maximum(ridge, foot), steep).astype(np.float32)


def _vein_target(density: float, shape: tuple[int, int]) -> int:
    """How many veins of one metal a map of this size should carry."""
    area = shape[0] * shape[1]
    return int(round(density * area / _VEIN_DENSITY_AREA))


def _cluster_seeds(
    suitability: NDArray[np.float32],
    available: NDArray[np.bool_],
    rng: np.random.Generator,
    count: int,
    spacing: int,
) -> list[tuple[int, int]]:
    """Pick up to `count` cluster centres, no two within `spacing` tiles.

    Candidates are the best outcrop-on-high-ground tiles; they are walked in a
    random order and kept when far enough from every centre already kept.
    """
    ys, xs = np.nonzero(available & (suitability >= _VEIN_SEED_SUITABILITY))
    if len(ys) == 0:
        return []
    seeds: list[tuple[int, int]] = []
    for index in rng.permutation(len(ys)):
        x, y = int(xs[index]), int(ys[index])
        if all(max(abs(x - sx), abs(y - sy)) >= spacing for sx, sy in seeds):
            seeds.append((x, y))
            if len(seeds) == count:
                break
    return seeds


def _cluster_members(
    seed_xy: tuple[int, int],
    suitability: NDArray[np.float32],
    available: NDArray[np.bool_],
    rng: np.random.Generator,
    radius: int,
    size: int,
) -> list[tuple[int, int]]:
    """Choose up to `size` tiles for one cluster around its seed tile.

    The best ore-bearing tiles in the seed's window win, with a random jitter
    so that two clusters on similar ground do not take the same shape.
    """
    seed_x, seed_y = seed_xy
    height, width = suitability.shape
    y0, y1 = max(0, seed_y - radius), min(height, seed_y + radius + 1)
    x0, x1 = max(0, seed_x - radius), min(width, seed_x + radius + 1)
    window = available[y0:y1, x0:x1] & (
        suitability[y0:y1, x0:x1] >= _VEIN_MEMBER_SUITABILITY
    )
    ys, xs = np.nonzero(window)
    if len(ys) == 0:
        return []
    scores = suitability[y0:y1, x0:x1][ys, xs] + 0.05 * rng.random(
        len(ys), dtype=np.float32
    )
    best = np.argsort(-scores, kind="stable")[:size]
    return [(int(xs[i]) + x0, int(ys[i]) + y0) for i in best]


def _place_one_metal(
    suitability: NDArray[np.float32],
    available: NDArray[np.bool_],
    rng: np.random.Generator,
    config: ObjectPlacementConfig,
    target: int,
    object_type: ObjectType,
) -> list[PlacedObject]:
    """Place clusters of one vein kind until the target count is reached.

    `available` is mutated: every tile taken is struck off, so the two metals
    and the rocks that follow never share a tile.
    """
    if target <= 0:
        return []
    max_clusters = max(1, -(-target // config.vein_cluster_min))
    seeds = _cluster_seeds(
        suitability, available, rng, max_clusters, config.vein_cluster_spacing
    )
    placed: list[tuple[int, int]] = []
    for seed_xy in seeds:
        if len(placed) >= target:
            break
        size = int(rng.integers(config.vein_cluster_min, config.vein_cluster_max + 1))
        members = _cluster_members(
            seed_xy, suitability, available, rng, config.vein_cluster_radius, size
        )
        for x, y in members:
            available[y, x] = False
        placed.extend(members)
    prefix = object_type.value
    return [
        PlacedObject(x=x, y=y, object_type=object_type, object_id=f"{prefix}_{index}")
        for index, (x, y) in enumerate(placed)
    ]


def vein_suitability(
    fields: PlacementFields,
    outcrop: NDArray[np.float32],
    seed: int,
    config: ObjectPlacementConfig,
) -> NDArray[np.float32]:
    """How ore-bearing every tile is, in [0, 1].

    Ore needs a strong outcrop and high ground under it, and a slow "ore
    bearing" noise leaves most outcrops barren, so the metal sits in a few
    inland districts rather than everywhere there is rock.
    """
    ore_bearing = smoothstep(
        0.35,
        0.6,
        _unit_noise(fields.floor.shape, seed + _SEED_VEIN, config.vein_wavelength),
    )
    return (outcrop * highland_field(fields, config) * ore_bearing).astype(np.float32)


def vein_ground(
    floor: NDArray[np.uint8], occupied: NDArray[np.bool_]
) -> NDArray[np.bool_]:
    """Tiles a vein may be placed on: walkable stony ground, nothing on it."""
    return (
        _floor_mask(floor, FloorType.GRASS, FloorType.DIRT, FloorType.STONE) & ~occupied
    )


def place_ore_veins(
    fields: PlacementFields,
    outcrop: NDArray[np.float32],
    occupied: NDArray[np.bool_],
    rng: np.random.Generator,
    seed: int,
    config: ObjectPlacementConfig,
) -> list[PlacedObject]:
    """Place copper and iron veins in clusters inside high-ground outcrops.

    Veins gather on ridges and at mountain feet and thin out towards the coast,
    where the land is low and the outcrops are scree at best.
    """
    suitability = vein_suitability(fields, outcrop, seed, config)
    available = vein_ground(fields.floor, occupied)
    veins: list[PlacedObject] = []
    for object_type, density in (
        (ObjectType.COPPER_VEIN, config.copper_vein_density),
        (ObjectType.IRON_VEIN, config.iron_vein_density),
    ):
        veins.extend(
            _place_one_metal(
                suitability,
                available,
                rng,
                config,
                _vein_target(density, fields.floor.shape),
                object_type,
            )
        )
    return veins


def place_reeds(
    fields: PlacementFields,
    occupied: NDArray[np.bool_],
    rng: np.random.Generator,
    seed: int,
    config: ObjectPlacementConfig,
) -> list[PlacedObject]:
    """Place reed beds hugging fresh water.

    Reeds grow on the bank tiles within `reed_bank_width` of a river and in
    the shallows of fords, in stands set by a patch noise, never on the ocean
    shore (distance to fresh water is what counts, and sea-side tiles where
    the river meets the coast are excluded).
    """
    bank = _floor_mask(
        fields.floor, FloorType.GRASS, FloorType.DIRT, FloorType.SAND
    ) & (fields.dist_to_fresh <= config.reed_bank_width)
    shallows = _floor_mask(fields.floor, FloorType.SHALLOW_WATER) & (
        fields.dist_to_fresh <= 1.0
    )
    inland = fields.dist_to_coast > config.reed_coast_exclusion
    valid = (bank | shallows) & inland & ~occupied

    patch = _unit_noise(fields.floor.shape, seed + _SEED_REEDS, config.reed_wavelength)
    stand = smoothstep(0.45, 0.6, patch)
    probability = config.reed_base_density * stand * np.where(shallows, 0.5, 1.0)
    ys, xs = _draw(probability.astype(np.float32), valid, rng)
    return _to_objects(ys, xs, ObjectType.REEDS, "reeds")


def place_clay(
    fields: PlacementFields,
    occupied: NDArray[np.bool_],
    rng: np.random.Generator,
    seed: int,
    config: ObjectPlacementConfig,
) -> list[PlacedObject]:
    """Place clay deposits in tight clusters a few tiles back from a river bank."""
    valid = (
        _floor_mask(fields.floor, FloorType.GRASS, FloorType.DIRT)
        & (fields.dist_to_fresh >= config.clay_min_distance)
        & (fields.dist_to_fresh <= config.clay_max_distance)
        & ~occupied
    )
    patch = _unit_noise(fields.floor.shape, seed + _SEED_CLAY, config.clay_wavelength)
    pit = smoothstep(0.58, 0.68, patch)
    probability = config.clay_base_density * pit
    ys, xs = _draw(probability.astype(np.float32), valid, rng)
    return _to_objects(ys, xs, ObjectType.CLAY_DEPOSIT, "clay")


def _mark(occupied: NDArray[np.bool_], objects: list[PlacedObject]) -> None:
    """Record placed objects in the shared occupancy mask (mutates `occupied`)."""
    if objects:
        occupied[[o.y for o in objects], [o.x for o in objects]] = True


def place_objects(
    fields: PlacementFields,
    rng: np.random.Generator,
    seed: int,
    config: ObjectPlacementConfig,
) -> list[PlacedObject]:
    """Place all natural objects, at most one per tile.

    Order matters: waterside resources claim the banks first, then trees, then
    ore veins take the best outcrop tiles, and rocks and bushes fill what is
    left. A last pass fits the ore to the settlement site
    (`fit_ore_to_settlement`).

    Args:
        fields: Continuous fields describing the terrain.
        rng: Random number generator (drives the per-tile draws).
        seed: Terrain seed (drives the placement noise masks).
        config: Object placement configuration.

    Returns:
        All placed objects: trees, bushes, rocks, reeds, clay deposits and
        ore veins.
    """
    occupied = np.zeros(fields.floor.shape, dtype=np.bool_)

    reeds = place_reeds(fields, occupied, rng, seed, config)
    _mark(occupied, reeds)
    clay = place_clay(fields, occupied, rng, seed, config)
    _mark(occupied, clay)

    canopy = canopy_field(fields, seed, config)
    outcrop = outcrop_field(fields, seed, config)
    trees = [
        tree
        for tree in place_trees(fields, canopy, outcrop, rng, config)
        if not occupied[tree.y, tree.x]
    ]
    _mark(occupied, trees)

    veins = place_ore_veins(fields, outcrop, occupied, rng, seed, config)
    _mark(occupied, veins)

    rocks = place_rocks(fields, outcrop, occupied, rng, config)
    _mark(occupied, rocks)

    bushes = place_bushes(fields, canopy, occupied, rng, seed, config)
    _mark(occupied, bushes)

    placed = trees + bushes + rocks + reeds + clay + veins
    return fit_ore_to_settlement(fields, outcrop, occupied, placed, rng, seed, config)


ORE_TYPES = (ObjectType.COPPER_VEIN, ObjectType.IRON_VEIN)
# The guaranteed home district sits this far out: a real walk, well inside the
# site finder's search radius.
_HOME_DISTRICT_MIN = ORE_EXCLUSION_RADIUS + 20
_HOME_DISTRICT_MAX = 150


def _chebyshev(obj: PlacedObject, site: Position) -> int:
    return max(abs(obj.x - site.x), abs(obj.y - site.y))


def _ring_mask(
    shape: tuple[int, int], site: Position, inner: int, outer: int
) -> NDArray[np.bool_]:
    """Tiles whose Chebyshev distance from the site is in [inner, outer]."""
    ys = np.abs(np.arange(shape[0]) - site.y)[:, None]
    xs = np.abs(np.arange(shape[1]) - site.x)[None, :]
    distance = np.maximum(ys, xs)
    return (distance >= inner) & (distance <= outer)


def _home_district(
    suitability: NDArray[np.float32],
    available: NDArray[np.bool_],
    site: Position,
    rng: np.random.Generator,
    config: ObjectPlacementConfig,
    object_type: ObjectType,
    wanted: int,
    id_offset: int,
) -> list[PlacedObject]:
    """Put one cluster of `object_type` in the ring around the settlement.

    The ring's best ore-bearing tile is the seed even when nothing there passes
    the usual seed threshold: every settlement must have metal within reach,
    and the site is chosen before the district is placed.
    """
    ring = available & _ring_mask(
        suitability.shape, site, _HOME_DISTRICT_MIN, _HOME_DISTRICT_MAX
    )
    if not ring.any():
        raise NoSettlementSiteError(
            f"No ground for a {object_type.value} district around {site}"
        )
    masked = np.where(ring, suitability, -1.0)
    seed_y, seed_x = np.unravel_index(int(np.argmax(masked)), masked.shape)
    size = max(
        wanted, int(rng.integers(config.vein_cluster_min, config.vein_cluster_max + 1))
    )
    # Inside the ring the member threshold is waived: poor ground still beats
    # a settlement with no metal within reach.
    members = _cluster_members(
        (int(seed_x), int(seed_y)),
        np.where(ring, np.maximum(suitability, _VEIN_MEMBER_SUITABILITY), 0.0).astype(
            np.float32
        ),
        ring,
        rng,
        config.vein_cluster_radius,
        size,
    )
    for x, y in members:
        available[y, x] = False
    prefix = object_type.value
    return [
        PlacedObject(
            x=x,
            y=y,
            object_type=object_type,
            object_id=f"{prefix}_{id_offset + index}",
        )
        for index, (x, y) in enumerate(members)
    ]


def prune_veins_near_settlement(
    objects: list[PlacedObject], site: Position
) -> list[PlacedObject]:
    """Drop every vein within ORE_EXCLUSION_RADIUS (Chebyshev) of the site."""
    return [
        obj
        for obj in objects
        if obj.object_type not in ORE_TYPES
        or _chebyshev(obj, site) > ORE_EXCLUSION_RADIUS
    ]


def fit_ore_to_settlement(
    fields: PlacementFields,
    outcrop: NDArray[np.float32],
    occupied: NDArray[np.bool_],
    objects: list[PlacedObject],
    rng: np.random.Generator,
    seed: int,
    config: ObjectPlacementConfig,
) -> list[PlacedObject]:
    """Make the map's ore fit the settlement: a district in reach, none on top.

    The settlement site is chosen from the finished map, and the ore rules are
    written against that site, so the generator does the two in order:

    1. choose the site with the ordinary resource requirements only;
    2. add a copper and an iron cluster in the ring around it if the random
       placement left it short, so that the site meets the ore requirement;
    3. delete every vein within ORE_EXCLUSION_RADIUS of the site.

    That order is safe because the ore requirement counts veins in the ring
    between the exclusion and search radii, and because veins feed neither the
    buildable mask nor the site score: adding ore in the ring and deleting ore
    inside the exclusion radius leave the winning tile the winning tile, so the
    site finder picks the same site again when the world is loaded. The last
    step checks exactly that.

    A map with no settlement site at all - a test island too small or too poor
    to hold one - is returned untouched; there is no site to arrange ore around.

    Args:
        fields: The placement fields (floor and the distance fields).
        outcrop: The outcrop field.
        occupied: Tiles already taken by another object.
        objects: Every object placed so far, veins included.
        rng: Random number generator.
        seed: Terrain seed.
        config: Object placement configuration.

    Returns:
        The objects to save.

    Raises:
        NoSettlementSiteError: If the settlement site moves once the ore is in
            place, or if there is no ground for a home district.
    """
    triples = [(obj.x, obj.y, obj.object_type.value) for obj in objects]
    try:
        site = select_site(fields.floor, triples, require_ore=False)
    except NoSettlementSiteError:
        logger.info("No settlement site on this map; ore left as placed")
        return objects

    suitability = vein_suitability(fields, outcrop, seed, config)
    available = vein_ground(fields.floor, occupied)
    added: list[PlacedObject] = []
    for object_type in ORE_TYPES:
        minimum = ORE_MINIMUMS[object_type.value]
        in_reach = sum(
            1
            for obj in objects
            if obj.object_type == object_type
            and ORE_EXCLUSION_RADIUS < _chebyshev(obj, site) <= ORE_SEARCH_RADIUS
        )
        if in_reach >= minimum:
            continue
        district = _home_district(
            suitability,
            available,
            site,
            rng,
            config,
            object_type,
            minimum,
            id_offset=sum(1 for obj in objects if obj.object_type == object_type),
        )
        logger.info(
            f"Added a {object_type.value} district of {len(district)} "
            f"near the settlement at ({site.x}, {site.y})"
        )
        added.extend(district)

    kept = prune_veins_near_settlement(objects + added, site)
    logger.info(
        f"Settlement site ({site.x}, {site.y}): "
        f"{len(objects) + len(added) - len(kept)} veins removed within "
        f"{ORE_EXCLUSION_RADIUS} tiles"
    )

    settled = select_site(
        fields.floor, [(obj.x, obj.y, obj.object_type.value) for obj in kept]
    )
    if settled != site:
        raise NoSettlementSiteError(
            f"Settlement site moved from {site} to {settled} when the ore was "
            "fitted; the ore rules and the site finder disagree"
        )
    return kept
