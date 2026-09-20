#!/usr/bin/env python3
"""Generate a 1-pixel-per-tile image of the world with terrain stats.

Usage:
    python tools/visualize_world.py [MAP_PATH] [OUTPUT_PATH]
        [--crop X,Y,SIZE] [--scale N] [--mark X,Y]

Arguments:
    MAP_PATH: Path to the .npz map file (default: saves/island.npz)
    OUTPUT_PATH: Path for output image (default: world_map.png)
    --crop X,Y,SIZE: Render only the SIZE x SIZE window centred on (X, Y)
    --scale N: Pixels per tile (default 1; use 6-8 with --crop)
    --mark X,Y: Draw a red cross on a tile (e.g. the settlement centre)
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from bobgame_rules.terrain import FloorType

# Floor type codes (`bobgame_rules.terrain`, the one definition of them).
FLOOR_DEEP_WATER = FloorType.DEEP_WATER.code
FLOOR_SHALLOW_WATER = FloorType.SHALLOW_WATER.code
FLOOR_SAND = FloorType.SAND.code
FLOOR_GRASS = FloorType.GRASS.code
FLOOR_DIRT = FloorType.DIRT.code
FLOOR_MOUNTAIN = FloorType.MOUNTAIN.code
FLOOR_STONE = FloorType.STONE.code

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
    FLOOR_DEEP_WATER: (20, 60, 140),  # Dark blue
    FLOOR_SHALLOW_WATER: (60, 130, 180),  # Light blue
    FLOOR_SAND: (230, 210, 140),  # Sandy yellow
    FLOOR_GRASS: (60, 150, 60),  # Green
    FLOOR_DIRT: (140, 100, 60),  # Brown
    FLOOR_MOUNTAIN: (100, 100, 100),  # Gray
    FLOOR_STONE: (160, 160, 160),  # Light gray
}

# Object colors for overlay (optional)
OBJECT_COLORS = {
    "tree": (20, 100, 20),  # Dark green
    "bush": (100, 180, 100),  # Light green
    "rock_small": (80, 80, 80),  # Dark gray
    "rock_medium": (90, 90, 90),
    "rock_large": (100, 100, 100),
    "boulder": (110, 110, 110),
    "reeds": (90, 230, 190),  # Aqua
    "clay_deposit": (200, 90, 50),  # Terracotta
    "copper_vein": (255, 150, 0),  # Bright orange
    "iron_vein": (190, 70, 230),  # Violet
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

    palette = np.full((256, 3), (255, 0, 255), dtype=np.uint8)  # magenta = unknown
    for value, color in FLOOR_COLORS.items():
        palette[value] = color
    rgb = palette[floor]

    if show_objects and objects:
        for obj in objects:
            x, y = obj["x"], obj["y"]
            if 0 <= x < width and 0 <= y < height:
                rgb[y, x] = OBJECT_COLORS.get(obj.get("object_type", ""), (255, 0, 255))

    return Image.fromarray(rgb, "RGB")


def crop_view(
    floor: np.ndarray, objects: list[dict], centre: tuple[int, int], size: int
) -> tuple[np.ndarray, list[dict], tuple[int, int]]:
    """Cut a size x size window centred on `centre`, clamped to the map.

    Returns the floor window, the objects inside it re-based to window
    coordinates, and the window's (x0, y0) origin on the full map.
    """
    height, width = floor.shape
    x0 = int(np.clip(centre[0] - size // 2, 0, max(0, width - size)))
    y0 = int(np.clip(centre[1] - size // 2, 0, max(0, height - size)))
    window = floor[y0 : y0 + size, x0 : x0 + size]
    inside = [
        {**obj, "x": obj["x"] - x0, "y": obj["y"] - y0}
        for obj in objects
        if x0 <= obj["x"] < x0 + size and y0 <= obj["y"] < y0 + size
    ]
    return window, inside, (x0, y0)


def draw_mark(img: Image.Image, tile: tuple[int, int], scale: int) -> None:
    """Draw a red cross centred on `tile` (image-local tile coordinates)."""
    pixels = img.load()
    cx = tile[0] * scale + scale // 2
    cy = tile[1] * scale + scale // 2
    arm = max(3, 2 * scale)
    for d in range(-arm, arm + 1):
        for px, py in ((cx + d, cy), (cx, cy + d)):
            if 0 <= px < img.width and 0 <= py < img.height:
                pixels[px, py] = (255, 0, 0)


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
    print(
        f"  Land:  {summary['land_tiles']:>12,} tiles ({summary['land_percentage']:.1f}%)"
    )
    print(
        f"  Water: {summary['water_tiles']:>12,} tiles ({100 - summary['land_percentage']:.1f}%)"
    )

    # Terrain breakdown
    print(f"\nTerrain Breakdown:")
    for name, data in terrain_stats["terrain"].items():
        if data["count"] > 0:
            print(
                f"  {name:15} {data['count']:>12,} tiles ({data['percentage']:>5.1f}%)"
            )

    # Objects
    if object_stats["total_objects"] > 0:
        print(f"\nObjects ({object_stats['total_objects']:,} total):")
        for obj_type, count in object_stats["by_type"].items():
            print(f"  {obj_type:15} {count:>12,}")

    print(f"\n{'='*60}\n")


def _parse_ints(text: str, count: int) -> tuple[int, ...]:
    parts = text.split(",")
    if len(parts) != count:
        raise argparse.ArgumentTypeError(f"expected {count} comma-separated integers")
    return tuple(int(part) for part in parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("map_path", nargs="?", default="saves/island.npz")
    parser.add_argument("output_path", nargs="?", default="world_map.png")
    parser.add_argument("--crop", type=lambda t: _parse_ints(t, 3), default=None)
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--mark", type=lambda t: _parse_ints(t, 2), default=None)
    args = parser.parse_args()

    map_path = Path(args.map_path)
    output_path = Path(args.output_path)

    # Make map path absolute if relative
    if not map_path.is_absolute():
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

    print_stats(compute_terrain_stats(floor), compute_object_stats(objects), metadata)

    origin = (0, 0)
    if args.crop is not None:
        floor, objects, origin = crop_view(
            floor, objects, (args.crop[0], args.crop[1]), args.crop[2]
        )

    print("Generating image...")
    img = generate_terrain_image(floor, objects, show_objects=True)
    if args.scale > 1:
        img = img.resize(
            (img.width * args.scale, img.height * args.scale), Image.NEAREST
        )
    if args.mark is not None:
        draw_mark(img, (args.mark[0] - origin[0], args.mark[1] - origin[1]), args.scale)
    img.save(output_path)
    print(f"Saved terrain image to: {output_path}")

    # Also save a version without objects (whole-map renders only)
    if objects and args.crop is None:
        no_obj_path = output_path.with_stem(output_path.stem + "_terrain_only")
        img_no_obj = generate_terrain_image(floor, objects=None, show_objects=False)
        img_no_obj.save(no_obj_path)
        print(f"Saved terrain-only image to: {no_obj_path}")


if __name__ == "__main__":
    main()
