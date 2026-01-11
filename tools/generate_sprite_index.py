#!/usr/bin/env python3
"""
Parse Tiled TSX files and generate a sprite index.

Looks for tiles with custom properties:
  - key: string - The sprite name (e.g., "bush.with_berry")
  - two-frame-animation: bool - If true, same tile ID in File1.png is frame 2

Fails if duplicate keys are found across all TSX files.

Output: viewer/public/assets/sprite-index.json
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def compute_animation_pair(image_path: str) -> str | None:
    """Convert File0.png to File1.png for two-frame animations."""
    # Match patterns like "Player0.png", "Floor0.png", etc.
    match = re.search(r"^(.*)0(\.[^.]+)$", image_path)
    if match:
        return f"{match.group(1)}1{match.group(2)}"
    return None


def parse_tsx_file(tsx_path: Path, assets_base: Path) -> list[dict]:
    """Parse a single TSX file and extract tiles with 'key' properties."""
    tree = ET.parse(tsx_path)
    root = tree.getroot()

    tileset_name = root.get("name", tsx_path.stem)
    image_elem = root.find("image")
    if image_elem is None:
        return []

    # Get image source and resolve relative to TSX file location
    image_source = image_elem.get("source", "")
    image_abs = (tsx_path.parent / image_source).resolve()
    image_relative = image_abs.relative_to(assets_base)

    # Get tileset dimensions for frame calculations
    columns = int(root.get("columns", "1"))

    entries = []

    for tile in root.findall("tile"):
        tile_id = tile.get("id")
        if tile_id is None:
            continue

        properties = tile.find("properties")
        if properties is None:
            continue

        key = None
        two_frame_animation = False

        for prop in properties.findall("property"):
            prop_name = prop.get("name")
            prop_value = prop.get("value")

            if prop_name == "key":
                key = prop_value
            elif prop_name == "two-frame-animation":
                two_frame_animation = prop_value.lower() == "true"

        if key:
            frame = int(tile_id)
            entry = {
                "key": key,
                "source_file": str(tsx_path),
                "tileset": tileset_name,
                "spritesheet": str(image_relative),
                "frame": frame,
                "columns": columns,
            }

            if two_frame_animation:
                pair = compute_animation_pair(str(image_relative))
                if pair:
                    entry["animationFrames"] = [
                        {"spritesheet": str(image_relative), "frame": frame},
                        {"spritesheet": pair, "frame": frame},
                    ]
                else:
                    print(
                        f"Warning: {key} has two-frame-animation but "
                        f"no File1 pair found for {image_relative}",
                        file=sys.stderr,
                    )

            entries.append(entry)

    return entries


def find_tsx_files(base_path: Path) -> list[Path]:
    """Find all TSX files in the assets directory."""
    return list(base_path.rglob("*.tsx"))


def check_duplicates(entries: list[dict]) -> list[str]:
    """Check for duplicate keys. Returns list of error messages."""
    seen: dict[str, list[dict]] = {}

    for entry in entries:
        key = entry["key"]
        if key not in seen:
            seen[key] = []
        seen[key].append(entry)

    errors = []
    for key, occurrences in seen.items():
        if len(occurrences) > 1:
            locations = [
                f"  - {e['source_file']} (tile {e['frame']})"
                for e in occurrences
            ]
            errors.append(f"Duplicate key '{key}' found in:\n" + "\n".join(locations))

    return errors


def build_index(entries: list[dict]) -> dict:
    """Build the sprite index from parsed entries."""
    index = {}
    for entry in entries:
        key = entry["key"]
        sprite_data = {
            "spritesheet": entry["spritesheet"],
            "frame": entry["frame"],
            "columns": entry["columns"],
        }
        if "animationFrames" in entry:
            sprite_data["animationFrames"] = entry["animationFrames"]

        index[key] = sprite_data

    return index


def main() -> int:
    project_root = Path(__file__).parent.parent
    assets_path = project_root / "assets" / "dawnlike-tileset"
    output_path = project_root / "viewer" / "public" / "assets" / "sprite-index.json"

    if not assets_path.exists():
        print(f"Error: Assets path not found: {assets_path}", file=sys.stderr)
        return 1

    tsx_files = find_tsx_files(assets_path)
    if not tsx_files:
        print(f"No TSX files found in {assets_path}", file=sys.stderr)
        return 1

    print(f"Scanning {len(tsx_files)} TSX files...")

    all_entries = []
    for tsx_file in tsx_files:
        entries = parse_tsx_file(tsx_file, assets_path)
        if entries:
            print(f"  {tsx_file.name}: {len(entries)} keyed tiles")
        all_entries.extend(entries)

    print(f"\nTotal keyed tiles: {len(all_entries)}")

    errors = check_duplicates(all_entries)
    if errors:
        print(f"\nError: Found {len(errors)} duplicate key(s):\n", file=sys.stderr)
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("\nNo duplicate keys found.")

    # Build and write the index
    index = build_index(all_entries)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(index, f, indent=2, sort_keys=True)

    print(f"\nGenerated sprite index: {output_path}")
    print(f"  {len(index)} sprites indexed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
