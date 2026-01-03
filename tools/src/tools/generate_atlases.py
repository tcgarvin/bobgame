#!/usr/bin/env python3
"""Generate Phaser-compatible texture atlas JSON files from DawnLike tileset.

This script processes the DawnLike 16x16 tileset and generates JSON atlas files
that can be loaded by Phaser 3 using load.atlas().

Usage:
    uv run python -m tools.generate_atlases

Output files are written to viewer/public/assets/atlases/
"""

import json
from pathlib import Path

from PIL import Image

TILE_SIZE = 16
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
TILESET_DIR = PROJECT_ROOT / "assets" / "dawnlike-tileset"
OUTPUT_DIR = PROJECT_ROOT / "viewer" / "public" / "assets" / "atlases"


def get_sprite_grid(image_path: Path) -> tuple[int, int]:
    """Get the number of columns and rows in a sprite sheet."""
    with Image.open(image_path) as img:
        width, height = img.size
        cols = width // TILE_SIZE
        rows = height // TILE_SIZE
        return cols, rows


def generate_atlas(
    image_path: Path,
    sprite_definitions: dict[str, int],
    output_name: str,
) -> dict:
    """Generate a Phaser atlas JSON for a sprite sheet.

    Args:
        image_path: Path to the PNG sprite sheet
        sprite_definitions: Dict mapping sprite names to frame indices
        output_name: Name for the output atlas (without extension)

    Returns:
        The atlas dictionary
    """
    cols, rows = get_sprite_grid(image_path)

    frames = {}
    for name, index in sprite_definitions.items():
        col = index % cols
        row = index // cols

        frames[name] = {
            "frame": {
                "x": col * TILE_SIZE,
                "y": row * TILE_SIZE,
                "w": TILE_SIZE,
                "h": TILE_SIZE,
            },
            "rotated": False,
            "trimmed": False,
            "spriteSourceSize": {
                "x": 0,
                "y": 0,
                "w": TILE_SIZE,
                "h": TILE_SIZE,
            },
            "sourceSize": {
                "w": TILE_SIZE,
                "h": TILE_SIZE,
            },
        }

    with Image.open(image_path) as img:
        width, height = img.size

    atlas = {
        "frames": frames,
        "meta": {
            "app": "bobgame-atlas-generator",
            "version": "1.0",
            "image": f"{output_name}.png",
            "format": "RGBA8888",
            "size": {"w": width, "h": height},
            "scale": 1,
        },
    }

    return atlas


def generate_floor_atlas() -> None:
    """Generate atlas for floor tiles."""
    image_path = TILESET_DIR / "Objects" / "Floor.png"
    cols, _ = get_sprite_grid(image_path)

    # Floor.png layout (approximate - based on DawnLike structure):
    # Rows 0-2: Stone variants
    # Rows 3-5: Wood variants
    # Rows 6-8: Grass variants
    # etc.

    sprites = {}

    # Stone floor tiles (first 3 rows)
    for i in range(cols * 3):
        sprites[f"floor-stone-{i}"] = i

    # Wood floor tiles (rows 3-5)
    for i in range(cols * 3):
        sprites[f"floor-wood-{i}"] = cols * 3 + i

    # Grass floor tiles (rows 6-8)
    for i in range(cols * 3):
        sprites[f"floor-grass-{i}"] = cols * 6 + i

    atlas = generate_atlas(image_path, sprites, "floor")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "floor.json"
    with open(output_path, "w") as f:
        json.dump(atlas, f, indent=2)

    print(f"Generated {output_path} ({len(sprites)} sprites)")


def generate_wall_atlas() -> None:
    """Generate atlas for wall tiles."""
    image_path = TILESET_DIR / "Objects" / "Wall.png"
    cols, _ = get_sprite_grid(image_path)

    # Wall.png uses an autotile format
    # Each wall type uses a 4x4 block (16 tiles) for different neighbor configs

    sprites = {}

    # First wall type (stone) - first 4x4 block
    for i in range(16):
        sprites[f"wall-stone-{i}"] = i

    # Second wall type (brick) - second 4x4 block
    for i in range(16):
        col = (i % 4) + 4  # Offset by 4 columns
        row = i // 4
        sprites[f"wall-brick-{i}"] = row * cols + col

    atlas = generate_atlas(image_path, sprites, "wall")

    output_path = OUTPUT_DIR / "wall.json"
    with open(output_path, "w") as f:
        json.dump(atlas, f, indent=2)

    print(f"Generated {output_path} ({len(sprites)} sprites)")


def generate_player_atlas() -> None:
    """Generate atlas for player character sprites."""
    # Player0.png and Player1.png are animation frames
    # We'll generate separate atlases and note the pairing

    for frame_num in range(2):
        image_path = TILESET_DIR / "Characters" / f"Player{frame_num}.png"
        cols, rows = get_sprite_grid(image_path)

        # Player sheet has different character classes/races
        # Layout varies, but typically organized in rows
        sprites = {}

        # Generate generic indexed sprites for now
        # Users can add semantic names later based on the tileset docs
        for row in range(rows):
            for col in range(cols):
                index = row * cols + col
                sprites[f"player-{row}-{col}"] = index

        atlas = generate_atlas(image_path, sprites, f"player{frame_num}")

        output_path = OUTPUT_DIR / f"player{frame_num}.json"
        with open(output_path, "w") as f:
            json.dump(atlas, f, indent=2)

        print(f"Generated {output_path} ({len(sprites)} sprites)")


def generate_humanoid_atlas() -> None:
    """Generate atlas for humanoid NPC sprites."""
    for frame_num in range(2):
        image_path = TILESET_DIR / "Characters" / f"Humanoid{frame_num}.png"
        cols, rows = get_sprite_grid(image_path)

        sprites = {}
        for row in range(rows):
            for col in range(cols):
                index = row * cols + col
                sprites[f"humanoid-{row}-{col}"] = index

        atlas = generate_atlas(image_path, sprites, f"humanoid{frame_num}")

        output_path = OUTPUT_DIR / f"humanoid{frame_num}.json"
        with open(output_path, "w") as f:
            json.dump(atlas, f, indent=2)

        print(f"Generated {output_path} ({len(sprites)} sprites)")


def copy_source_images() -> None:
    """Copy source PNG files to output directory."""
    import shutil

    files_to_copy = [
        ("Objects/Floor.png", "floor.png"),
        ("Objects/Wall.png", "wall.png"),
        ("Characters/Player0.png", "player0.png"),
        ("Characters/Player1.png", "player1.png"),
        ("Characters/Humanoid0.png", "humanoid0.png"),
        ("Characters/Humanoid1.png", "humanoid1.png"),
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for src_rel, dst_name in files_to_copy:
        src = TILESET_DIR / src_rel
        dst = OUTPUT_DIR / dst_name
        shutil.copy(src, dst)
        print(f"Copied {src_rel} -> {dst_name}")


def main() -> None:
    """Generate all atlases."""
    print(f"Generating atlases from {TILESET_DIR}")
    print(f"Output directory: {OUTPUT_DIR}")
    print()

    copy_source_images()
    print()

    generate_floor_atlas()
    generate_wall_atlas()
    generate_player_atlas()
    generate_humanoid_atlas()

    print()
    print("Done!")


if __name__ == "__main__":
    main()
