# Tileset Preparation Guide

The DawnLike tileset needs processing before use with Phaser 3. This document covers the preparation workflow.

## Source Tileset Structure

Location: `assets/dawnlike-tileset/`

```
dawnlike-tileset/
├── Characters/           # Entity sprites (2-frame animation)
│   ├── Player0.png      # Frame 0
│   ├── Player1.png      # Frame 1
│   ├── Humanoid0.png
│   ├── Humanoid1.png
│   └── ... (16 categories)
├── Objects/              # World objects
│   ├── Floor.png        # Floor tiles
│   ├── Wall.png         # Wall tiles
│   ├── Door0.png        # Doors (animated)
│   ├── Door1.png
│   ├── Tree0.png
│   ├── Decor0.png
│   └── ...
├── Items/                # Inventory items
│   ├── Potion.png
│   ├── Armor.png
│   └── ... (24 categories)
├── GUI/                  # UI elements
│   ├── GUI0.png
│   └── GUI1.png
└── Examples/             # Reference maps
```

### Tile Dimensions
- All tiles are **16x16 pixels**
- Sprite sheets vary in size
- Floor.png: 336x624 (21x39 tiles)
- Wall.png: 320x816 (20x51 tiles)
- Player0.png: 128x240 (8x15 tiles)

## Phaser 3 Integration Options

### Option A: Sprite Sheet (Simple)
Load each PNG directly as a spritesheet:

```typescript
// In preload
this.load.spritesheet('player0', 'assets/Characters/Player0.png', {
  frameWidth: 16,
  frameHeight: 16
});

// In create - access by frame index
this.add.sprite(x, y, 'player0', 42);
```

**Pros**: Simple, no preprocessing
**Cons**: Frame indices are magic numbers, no semantic names

### Option B: Texture Atlas (Recommended)
Create JSON atlas files mapping names to coordinates:

```json
{
  "frames": {
    "player-warrior-0": { "frame": { "x": 0, "y": 0, "w": 16, "h": 16 } },
    "player-warrior-1": { "frame": { "x": 16, "y": 0, "w": 16, "h": 16 } },
    "player-mage-0": { "frame": { "x": 32, "y": 0, "w": 16, "h": 16 } }
  },
  "meta": {
    "image": "Player0.png",
    "size": { "w": 128, "h": 240 },
    "scale": 1
  }
}
```

**Pros**: Semantic sprite names, easier to use
**Cons**: Requires manifest creation

### Option C: Combined Texture Pack
Use TexturePacker or similar to combine all sheets:

**Pros**: Single load, optimized
**Cons**: Large file, complex tooling

## Recommended Approach: Option B with Python Generator

### Step 1: Create Sprite Manifest

Create `assets/sprite_manifest.yaml` documenting the tileset:

```yaml
# Sprite naming convention: category-type-variant
# Index is (row * columns) + column

characters:
  Player0:
    columns: 8
    sprites:
      # Row 0
      - { name: "player-warrior", index: 0 }
      - { name: "player-mage", index: 1 }
      - { name: "player-rogue", index: 2 }
      # ... more sprites

objects:
  Floor:
    columns: 21
    sprites:
      # Stone floor variants
      - { name: "floor-stone-1", index: 0 }
      - { name: "floor-stone-2", index: 1 }
      # Grass floor variants
      - { name: "floor-grass-1", index: 21 }  # Row 1
      # ... etc

  Wall:
    columns: 20
    tiles:
      # Autotile format: each tile type has 16 variants
      # for different neighbor configurations
      stone:
        base_index: 0  # Top-left of autotile block
      brick:
        base_index: 16
```

### Step 2: Atlas Generator Script

Create `tools/generate_atlases.py`:

```python
#!/usr/bin/env python3
"""Generate Phaser texture atlas JSON from sprite manifest."""

import json
from pathlib import Path

import yaml
from PIL import Image


def generate_atlas(image_path: Path, manifest: dict) -> dict:
    """Generate Phaser-compatible atlas JSON."""
    img = Image.open(image_path)
    width, height = img.size
    columns = manifest["columns"]

    frames = {}
    for sprite in manifest["sprites"]:
        name = sprite["name"]
        idx = sprite["index"]
        col = idx % columns
        row = idx // columns

        frames[name] = {
            "frame": {
                "x": col * 16,
                "y": row * 16,
                "w": 16,
                "h": 16
            },
            "sourceSize": {"w": 16, "h": 16},
            "spriteSourceSize": {"x": 0, "y": 0, "w": 16, "h": 16}
        }

    return {
        "frames": frames,
        "meta": {
            "image": image_path.name,
            "size": {"w": width, "h": height},
            "format": "RGBA8888",
            "scale": 1
        }
    }


def main():
    manifest_path = Path("assets/sprite_manifest.yaml")
    output_dir = Path("viewer/public/assets/atlases")
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)

    for category, sheets in manifest.items():
        for sheet_name, sheet_data in sheets.items():
            image_path = Path(f"assets/dawnlike-tileset/{category}/{sheet_name}.png")
            atlas = generate_atlas(image_path, sheet_data)

            output_path = output_dir / f"{sheet_name.lower()}.json"
            with open(output_path, "w") as f:
                json.dump(atlas, f, indent=2)

            print(f"Generated {output_path}")


if __name__ == "__main__":
    main()
```

### Step 3: Viewer Asset Loading

```typescript
// src/scenes/PreloadScene.ts
export class PreloadScene extends Phaser.Scene {
  preload() {
    // Load atlases
    this.load.atlas(
      'player0',
      'assets/atlases/player0.png',
      'assets/atlases/player0.json'
    );
    this.load.atlas(
      'floor',
      'assets/atlases/floor.png',
      'assets/atlases/floor.json'
    );
    // ... more atlases
  }
}

// Usage in game scene
const player = this.add.sprite(x, y, 'player0', 'player-warrior');
```

## Animation Setup

Characters use 2-frame animation (0 and 1 variants):

```typescript
// Create animation from two atlases
this.anims.create({
  key: 'player-warrior-idle',
  frames: [
    { key: 'player0', frame: 'player-warrior' },
    { key: 'player1', frame: 'player-warrior' }
  ],
  frameRate: 2,
  repeat: -1
});

// Apply to sprite
sprite.play('player-warrior-idle');
```

## Autotiling for Walls

DawnLike uses a 4x4 autotile format for walls. Each tile type has 16 variants:

```
┌───┬───┬───┬───┐
│ 0 │ 1 │ 2 │ 3 │  Row 0: corners and edges
├───┼───┼───┼───┤
│ 4 │ 5 │ 6 │ 7 │  Row 1: more edges
├───┼───┼───┼───┤
│ 8 │ 9 │10 │11 │  Row 2: inner corners
├───┼───┼───┼───┤
│12 │13 │14 │15 │  Row 3: special cases
└───┴───┴───┴───┘
```

Map neighbor bitmask to tile index:

```typescript
function getWallTileIndex(neighbors: number): number {
  // neighbors is a bitmask: N=1, E=2, S=4, W=8
  const mapping: Record<number, number> = {
    0: 0,   // isolated
    1: 1,   // N only
    2: 2,   // E only
    // ... full mapping for 16 combinations
  };
  return mapping[neighbors] ?? 0;
}
```

## File Copying for Viewer

Copy required PNGs to viewer assets:

```bash
#!/bin/bash
# tools/copy_assets.sh

mkdir -p viewer/public/assets/tiles

# Copy character sheets
cp assets/dawnlike-tileset/Characters/Player*.png viewer/public/assets/tiles/
cp assets/dawnlike-tileset/Characters/Humanoid*.png viewer/public/assets/tiles/

# Copy object sheets
cp assets/dawnlike-tileset/Objects/Floor.png viewer/public/assets/tiles/
cp assets/dawnlike-tileset/Objects/Wall.png viewer/public/assets/tiles/
cp assets/dawnlike-tileset/Objects/Door*.png viewer/public/assets/tiles/

# Copy item sheets
cp assets/dawnlike-tileset/Items/*.png viewer/public/assets/tiles/
```

## Priority Sprites for MVP

For Milestone 1, we need:

1. **Floor tiles** (Floor.png)
   - Stone floor (row 0)
   - Grass floor (row 1)

2. **Wall tiles** (Wall.png)
   - Basic stone wall autotile block

3. **Player character** (Player0.png, Player1.png)
   - One character class

4. **Door** (Door0.png, Door1.png)
   - Open/closed states

## Attribution Requirement

Per the DawnLike license (CC-BY 4.0):

1. Credit DawnBringer for the color palette
2. Hide the Platino sprite (in Reptile.png) somewhere in the game
3. Credit DragonDePlatino for the tileset

Add to game credits:
```
Tileset: DawnLike by DragonDePlatino
Color Palette: DawnBringer
Licensed under CC-BY 4.0
```
