"""Object placement: trees, bushes, rocks, reeds and clay.

Every placement pass is a vectorised Bernoulli draw against a probability
field. The fields multiply low-frequency "where does this kind of thing live"
masks into a base density so the landscape reads as places - forests with
clearings and soft edges, rock outcrops at mountain feet, reed beds and clay
banks along the rivers - rather than uniform speckle. See docs/08_building.md.
"""

from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray
from scipy import special

from ..terrain_types import FloorType
from .config import ObjectPlacementConfig
from .noise import fbm_noise_vectorized, smoothstep

# Seed offsets for the placement noise masks (kept apart from the field seeds
# in fields.py, which use +100..+600).
_SEED_CANOPY = 1100
_SEED_OUTCROP = 1200
_SEED_REEDS = 1300
_SEED_CLAY = 1400
_SEED_BUSH = 1500

# Trees stop growing where the ground gets this steep.
_TREE_SLOPE_MAX = 0.5
# Share of the tree probability an outcrop's heart takes away.
_OUTCROP_TREE_SUPPRESSION = 0.85


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
    return np.isin(floor, [_floor_value(floor_type) for floor_type in types])


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
    rocks and bushes fill what is left.

    Args:
        fields: Continuous fields describing the terrain.
        rng: Random number generator (drives the per-tile draws).
        seed: Terrain seed (drives the placement noise masks).
        config: Object placement configuration.

    Returns:
        All placed objects: trees, bushes, rocks, reeds, clay deposits.
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

    rocks = place_rocks(fields, outcrop, occupied, rng, config)
    _mark(occupied, rocks)

    bushes = place_bushes(fields, canopy, occupied, rng, seed, config)

    return trees + bushes + rocks + reeds + clay


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
