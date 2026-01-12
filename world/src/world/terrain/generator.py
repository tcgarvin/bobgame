"""Main terrain generation orchestration."""

import logging
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..state import World, WorldObject
from ..terrain_types import FloorType
from ..types import Position
from .classification import classify_terrain
from .coastal import (
    compute_distance_to_land,
    compute_distance_to_water,
    keep_largest_component,
    majority_smooth,
)
from .config import TerrainConfig
from .fields import (
    compute_slope,
    make_beach_width_noise,
    make_elevation,
    make_forest_density,
    make_moisture,
    make_shallow_water_noise,
)
from .hydrology import (
    River,
    carve_rivers,
    compute_d8_flow_direction,
    compute_flow_accumulation,
    priority_flood_fill,
)
from .island import (
    apply_radial_falloff,
    compute_sea_level,
    create_land_mask,
    enforce_border_ocean,
)
from .noise import ridged_multifractal
from .objects import ObjectType, PlacedObject, place_objects
from .persistence import load_map, save_map

logger = logging.getLogger(__name__)


class GenerationResult:
    """Result of terrain generation with all intermediate data."""

    def __init__(
        self,
        floor: NDArray[np.uint8],
        objects: list[PlacedObject],
        config: TerrainConfig,
        elevation: NDArray[np.float32],
        moisture: NDArray[np.float32],
        rivers: list[River],
    ):
        self.floor = floor
        self.objects = objects
        self.config = config
        self.elevation = elevation
        self.moisture = moisture
        self.rivers = rivers


def generate_terrain(config: TerrainConfig) -> GenerationResult:
    """Generate complete terrain from configuration.

    Args:
        config: Terrain generation configuration.

    Returns:
        GenerationResult with floor array and placed objects.
    """
    rng = np.random.default_rng(config.seed)
    width, height = config.width, config.height

    logger.info(f"Generating terrain {width}x{height} with seed {config.seed}")

    # Stage A: Base continuous fields
    logger.info("Stage A: Generating elevation field...")
    elevation = make_elevation(width, height, config.seed, config.elevation)

    ridged = ridged_multifractal(
        width,
        height,
        config.seed + 100,
        config.elevation.ridged_wavelength,
        octaves=config.elevation.ridged_octaves,
    )

    # Stage B: Island shaping
    logger.info("Stage B: Shaping island...")
    elevation = apply_radial_falloff(elevation, config.island)
    elevation = enforce_border_ocean(elevation, config.island.border_width)
    sea_level = compute_sea_level(elevation, config.island.land_fraction)
    land_mask = create_land_mask(elevation, sea_level)

    logger.info(
        f"Sea level: {sea_level:.3f}, land fraction: {np.mean(land_mask):.2%}"
    )

    # Stage C: Coastal refinement
    logger.info("Stage C: Refining coastline...")
    land_mask = keep_largest_component(land_mask)
    land_mask = majority_smooth(land_mask, iterations=2)

    logger.info(f"Land fraction after refinement: {np.mean(land_mask):.2%}")

    # Stage D: Hydrology
    logger.info("Stage D: Computing hydrology...")
    filled_elevation = priority_flood_fill(elevation, ~land_mask)
    flow_dir = compute_d8_flow_direction(filled_elevation)
    flow_acc = compute_flow_accumulation(flow_dir)

    # Distance to coast for river source selection
    dist_to_coast = compute_distance_to_water(land_mask)

    river_mask, ford_mask, rivers = carve_rivers(
        land_mask,
        elevation,
        filled_elevation,
        flow_dir,
        flow_acc,
        dist_to_coast,
        rng,
        config.hydrology,
    )

    logger.info(f"Carved {len(rivers)} rivers")

    # Stage E: Distance fields
    logger.info("Stage E: Computing distance fields...")
    dist_to_water = compute_distance_to_water(land_mask, river_mask)
    dist_to_land = compute_distance_to_land(land_mask)

    # Generate moisture field with water influence
    moisture = make_moisture(
        width, height, config.seed, config.moisture, dist_to_water
    )

    # Stage F: Terrain classification
    logger.info("Stage F: Classifying terrain...")
    slope = compute_slope(filled_elevation)
    beach_noise = make_beach_width_noise(width, height, config.seed)
    shallow_noise = make_shallow_water_noise(width, height, config.seed)

    floor = classify_terrain(
        land_mask,
        river_mask,
        ford_mask,
        elevation,
        moisture,
        slope,
        dist_to_water,
        dist_to_land,
        beach_noise,
        shallow_noise,
        ridged,
        config.classification,
    )

    # Stage G: Object placement
    logger.info("Stage G: Placing objects...")
    forest_density = make_forest_density(
        width, height, config.seed, config.objects.forest
    )

    objects = place_objects(
        floor,
        forest_density,
        ridged,
        slope,
        dist_to_water,
        rng,
        config.objects,
    )

    logger.info(f"Placed {len(objects)} objects")

    # Stage H: Validation (in separate module)
    # For now, just log terrain stats
    _log_terrain_stats(floor, land_mask)

    # Debug output if enabled
    if config.debug_output_dir:
        _dump_debug_images(
            Path(config.debug_output_dir),
            elevation=elevation,
            moisture=moisture,
            floor=floor,
            river_mask=river_mask,
            dist_to_water=dist_to_water,
        )

    return GenerationResult(
        floor=floor,
        objects=objects,
        config=config,
        elevation=elevation,
        moisture=moisture,
        rivers=rivers,
    )


def generate_world(config: TerrainConfig) -> tuple[World, list[WorldObject]]:
    """Generate a World instance with terrain and objects.

    Args:
        config: Terrain generation configuration.

    Returns:
        Tuple of (World, list of WorldObject).
    """
    result = generate_terrain(config)

    # Convert floor array to World with sparse tiles
    world = floor_array_to_world(result.floor, config.width, config.height)

    # Convert placed objects to WorldObjects
    world_objects = objects_to_world_objects(result.objects)

    return world, world_objects


def floor_array_to_world(
    floor: NDArray[np.uint8],
    width: int,
    height: int,
) -> World:
    """Convert floor array to World with efficient array-backed storage.

    Stores the floor array directly on the World for O(1) lookups without
    creating millions of Tile objects. Tiles are generated on-demand.

    Args:
        floor: 2D array of floor type values, shape (height, width).
        width: World width.
        height: World height.

    Returns:
        World instance with floor array set.
    """
    world = World(width=width, height=height)
    world.set_floor_array(floor)
    return world


def objects_to_world_objects(objects: list[PlacedObject]) -> list[WorldObject]:
    """Convert placed objects to WorldObject instances.

    Args:
        objects: List of PlacedObject from generation.

    Returns:
        List of WorldObject instances.
    """
    world_objects: list[WorldObject] = []

    for obj in objects:
        if obj.object_type == ObjectType.BUSH:
            # Bushes start with berries
            world_objects.append(
                WorldObject(
                    object_id=obj.object_id,
                    position=Position(x=obj.x, y=obj.y),
                    object_type="bush",
                    state=(("berry_count", "1"),),
                )
            )
        elif obj.object_type == ObjectType.TREE:
            world_objects.append(
                WorldObject(
                    object_id=obj.object_id,
                    position=Position(x=obj.x, y=obj.y),
                    object_type="tree",
                )
            )
        else:
            # Rock variants
            world_objects.append(
                WorldObject(
                    object_id=obj.object_id,
                    position=Position(x=obj.x, y=obj.y),
                    object_type=obj.object_type.value,
                )
            )

    return world_objects


def _log_terrain_stats(floor: NDArray[np.uint8], land_mask: NDArray[np.bool_]) -> None:
    """Log terrain generation statistics."""
    total = floor.size
    land_count = np.sum(land_mask)

    # Count each floor type
    counts = {
        "deep_water": np.sum(floor == 0),
        "shallow_water": np.sum(floor == 1),
        "sand": np.sum(floor == 2),
        "grass": np.sum(floor == 3),
        "dirt": np.sum(floor == 4),
        "mountain": np.sum(floor == 5),
    }

    logger.info(f"Terrain stats ({total:,} tiles):")
    for name, count in counts.items():
        pct = count / total * 100
        logger.info(f"  {name}: {count:,} ({pct:.1f}%)")

    if land_count > 0:
        mountain_pct = counts["mountain"] / land_count * 100
        logger.info(f"  Mountain fraction of land: {mountain_pct:.1f}%")


def _dump_debug_images(output_dir: Path, **arrays: NDArray) -> None:
    """Save arrays as images for debugging.

    Args:
        output_dir: Directory to save images.
        **arrays: Named arrays to save.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available, skipping debug images")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    for name, arr in arrays.items():
        fig, ax = plt.subplots(figsize=(10, 10))

        if arr.dtype == bool:
            ax.imshow(arr, cmap="binary")
        elif arr.dtype == np.uint8:
            ax.imshow(arr, cmap="tab10")
        else:
            ax.imshow(arr, cmap="terrain")

        ax.set_title(name)
        ax.axis("off")

        fig.savefig(output_dir / f"{name}.png", dpi=150, bbox_inches="tight")
        plt.close(fig)

    logger.info(f"Debug images saved to {output_dir}")


def load_world(path: Path) -> tuple[World, list[WorldObject]]:
    """Load a World instance from a saved map file.

    Args:
        path: Path to .npz map file.

    Returns:
        Tuple of (World, list of WorldObject).

    Raises:
        FileNotFoundError: If file doesn't exist.
        ValueError: If file format is invalid.
    """
    floor, objects, metadata = load_map(path)
    height, width = floor.shape

    logger.info(f"Loading world from {path}: {width}x{height}")

    # Convert floor array to World
    world = floor_array_to_world(floor, width, height)

    # Convert objects to WorldObjects
    world_objects = objects_to_world_objects(objects)

    logger.info(f"Loaded {len(world_objects)} objects")

    return world, world_objects


def generate_and_save_world(
    config: TerrainConfig,
    save_path: Path,
) -> tuple[World, list[WorldObject]]:
    """Generate terrain and save it, then return the World.

    Args:
        config: Terrain generation configuration.
        save_path: Path to save the generated map.

    Returns:
        Tuple of (World, list of WorldObject).
    """
    result = generate_terrain(config)

    # Save to disk
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_map(save_path, result.floor, result.objects, config)

    # Convert to World
    world = floor_array_to_world(result.floor, config.width, config.height)
    world_objects = objects_to_world_objects(result.objects)

    return world, world_objects
