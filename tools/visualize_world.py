#!/usr/bin/env python3
"""Generate a 1-pixel-per-tile image of the world with terrain stats.

Usage:
    python tools/visualize_world.py [MAP_PATH] [OUTPUT_PATH]

Arguments:
    MAP_PATH: Path to the .npz map file (default: world/saves/island.npz)
    OUTPUT_PATH: Path for output image (default: world_map.png)
"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

# Floor type values (from terrain_types.py)
FLOOR_DEEP_WATER = 0
FLOOR_SHALLOW_WATER = 1
FLOOR_SAND = 2
FLOOR_GRASS = 3
FLOOR_DIRT = 4
FLOOR_MOUNTAIN = 5
FLOOR_STONE = 6

FLOOR_NAMES = {
    FLOOR_DEEP_WATER: "Deep Water",
    FLOOR_SHALLOW_WATER: "Shallow Water",
    FLOOR_SAND: "Sand",
    FLOOR_GRASS: "Grass",
    FLOOR_DIRT: "Dirt",
    FLOOR_MOUNTAIN: "Mountain",
    FLOOR_STONE: "Stone",
}

# Colors for each terrain type (RGB)
FLOOR_COLORS = {
    FLOOR_DEEP_WATER: (20, 60, 140),      # Dark blue
    FLOOR_SHALLOW_WATER: (60, 130, 180),  # Light blue
    FLOOR_SAND: (230, 210, 140),          # Sandy yellow
    FLOOR_GRASS: (60, 150, 60),           # Green
    FLOOR_DIRT: (140, 100, 60),           # Brown
    FLOOR_MOUNTAIN: (100, 100, 100),      # Gray
    FLOOR_STONE: (160, 160, 160),         # Light gray
}

# Object colors for overlay (optional)
OBJECT_COLORS = {
    "tree": (20, 100, 20),         # Dark green
    "bush": (100, 180, 100),       # Light green
    "rock_small": (80, 80, 80),    # Dark gray
    "rock_medium": (90, 90, 90),
    "rock_large": (100, 100, 100),
    "boulder": (110, 110, 110),
}


def load_map(path: Path) -> tuple[np.ndarray, list[dict], dict]:
    """Load map from .npz file.

    Returns:
        Tuple of (floor array, objects list, metadata dict).
    """
    if not path.exists():
        raise FileNotFoundError(f"Map file not found: {path}")

    data = np.load(path)

    if "floor" not in data:
        raise ValueError("Invalid map file: missing 'floor' array")
    floor = data["floor"]

    objects = []
    if "objects" in data:
        objects_json = data["objects"].tobytes().decode("utf-8")
        objects = json.loads(objects_json)

    metadata = {}
    if "metadata" in data:
        metadata_json = data["metadata"].tobytes().decode("utf-8")
        metadata = json.loads(metadata_json)

    return floor, objects, metadata


def generate_terrain_image(
    floor: np.ndarray,
    objects: list[dict] | None = None,
    show_objects: bool = True,
) -> Image.Image:
    """Generate a 1-pixel-per-tile image of the terrain.

    Args:
        floor: Floor type array (height x width, uint8).
        objects: Optional list of object dicts with x, y, object_type.
        show_objects: Whether to overlay objects on the image.

    Returns:
        PIL Image with terrain visualization.
    """
    height, width = floor.shape

    # Create RGB image
    img = Image.new("RGB", (width, height))
    pixels = img.load()

    # Fill terrain
    for y in range(height):
        for x in range(width):
            floor_type = floor[y, x]
            color = FLOOR_COLORS.get(floor_type, (255, 0, 255))  # Magenta for unknown
            pixels[x, y] = color

    # Overlay objects
    if show_objects and objects:
        for obj in objects:
            x, y = obj["x"], obj["y"]
            obj_type = obj.get("object_type", "")
            if 0 <= x < width and 0 <= y < height:
                color = OBJECT_COLORS.get(obj_type, (255, 0, 255))
                pixels[x, y] = color

    return img


def compute_terrain_stats(floor: np.ndarray) -> dict:
    """Compute statistics about terrain types.

    Returns:
        Dict with terrain type counts and percentages.
    """
    height, width = floor.shape
    total = height * width

    counts = Counter(floor.flatten())

    stats = {
        "dimensions": {"width": width, "height": height, "total_tiles": total},
        "terrain": {},
    }

    for floor_type in sorted(FLOOR_NAMES.keys()):
        count = counts.get(floor_type, 0)
        name = FLOOR_NAMES[floor_type]
        stats["terrain"][name] = {
            "count": count,
            "percentage": round(100 * count / total, 2),
        }

    # Derived stats
    water_count = counts.get(FLOOR_DEEP_WATER, 0) + counts.get(FLOOR_SHALLOW_WATER, 0)
    land_count = total - water_count
    stats["summary"] = {
        "water_tiles": water_count,
        "land_tiles": land_count,
        "land_percentage": round(100 * land_count / total, 2),
    }

    return stats


def compute_object_stats(objects: list[dict]) -> dict:
    """Compute statistics about placed objects."""
    counts = Counter(obj.get("object_type", "unknown") for obj in objects)

    return {
        "total_objects": len(objects),
        "by_type": dict(sorted(counts.items())),
    }


def print_stats(terrain_stats: dict, object_stats: dict, metadata: dict) -> None:
    """Print formatted statistics."""
    dims = terrain_stats["dimensions"]
    print(f"\n{'='*60}")
    print("WORLD MAP STATISTICS")
    print(f"{'='*60}")

    # Metadata
    if metadata:
        print(f"\nMetadata:")
        print(f"  Seed: {metadata.get('seed', 'unknown')}")
        print(f"  Generated: {metadata.get('generated_at', 'unknown')}")

    # Dimensions
    print(f"\nDimensions:")
    print(f"  Size: {dims['width']} x {dims['height']} ({dims['total_tiles']:,} tiles)")

    # Summary
    summary = terrain_stats["summary"]
    print(f"\nLand/Water:")
    print(f"  Land:  {summary['land_tiles']:>12,} tiles ({summary['land_percentage']:.1f}%)")
    print(f"  Water: {summary['water_tiles']:>12,} tiles ({100 - summary['land_percentage']:.1f}%)")

    # Terrain breakdown
    print(f"\nTerrain Breakdown:")
    for name, data in terrain_stats["terrain"].items():
        if data["count"] > 0:
            print(f"  {name:15} {data['count']:>12,} tiles ({data['percentage']:>5.1f}%)")

    # Objects
    if object_stats["total_objects"] > 0:
        print(f"\nObjects ({object_stats['total_objects']:,} total):")
        for obj_type, count in object_stats["by_type"].items():
            print(f"  {obj_type:15} {count:>12,}")

    print(f"\n{'='*60}\n")


def main():
    # Parse arguments
    map_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("world/saves/island.npz")
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("world_map.png")

    # Make map path absolute if relative
    if not map_path.is_absolute():
        # Try relative to script location first
        script_dir = Path(__file__).parent.parent
        candidate = script_dir / map_path
        if candidate.exists():
            map_path = candidate

    print(f"Loading map from: {map_path}")

    try:
        floor, objects, metadata = load_map(map_path)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("\nHint: Run the world server with terrain generation first:")
        print("  cd world && uv run python -m world.server --config island")
        sys.exit(1)

    # Compute stats
    terrain_stats = compute_terrain_stats(floor)
    object_stats = compute_object_stats(objects)

    # Print stats
    print_stats(terrain_stats, object_stats, metadata)

    # Generate and save image
    print(f"Generating image...")
    img = generate_terrain_image(floor, objects, show_objects=True)
    img.save(output_path)
    print(f"Saved terrain image to: {output_path}")

    # Also save a version without objects
    if objects:
        no_obj_path = output_path.with_stem(output_path.stem + "_terrain_only")
        img_no_obj = generate_terrain_image(floor, objects=None, show_objects=False)
        img_no_obj.save(no_obj_path)
        print(f"Saved terrain-only image to: {no_obj_path}")


if __name__ == "__main__":
    main()
