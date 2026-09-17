/**
 * Terrain configuration: maps floor type values to sprite keys.
 *
 * Floor type values come from the backend (world/src/world/terrain_types.py).
 * Sprite keys are defined in TSX files (assets/dawnlike-tileset/Objects/*.tsx).
 *
 * This is the single source of truth for terrain → sprite mapping.
 */

import type { SpriteIndex } from '../sprites';

// Floor type numeric values from backend (world/src/world/terrain_types.py)
export const FloorType = {
  DEEP_WATER: 0,
  SHALLOW_WATER: 1,
  SAND: 2,
  GRASS: 3,
  DIRT: 4,
  MOUNTAIN: 5,
  STONE: 6,
} as const;

export type FloorType = (typeof FloorType)[keyof typeof FloorType];

/**
 * Maps floor type values to sprite keys from TSX files.
 * All keys must exist in sprite-index.json (generated from TSX files).
 */
export const FLOOR_TYPE_TO_SPRITE_KEY: Record<number, string> = {
  [FloorType.DEEP_WATER]: 'water', // Tile.tsx
  [FloorType.SHALLOW_WATER]: 'shallow-water', // Tile.tsx
  [FloorType.SAND]: 'dirt-full', // TODO: Add proper sand sprite - using dirt as placeholder
  [FloorType.GRASS]: 'grass-full', // Floor.tsx
  [FloorType.DIRT]: 'dirt-full', // Floor.tsx
  [FloorType.MOUNTAIN]: 'mountain', // Hill.tsx
  [FloorType.STONE]: 'stone-ground', // Floor.tsx
};

/**
 * Default sprite key used when a floor type is not in the mapping.
 */
export const DEFAULT_TERRAIN_SPRITE_KEY = 'grass-full';

/**
 * Tileset configuration for multi-tileset terrain rendering.
 * Each tileset gets a GID offset to create non-overlapping tile index ranges.
 */
export interface TilesetConfig {
  /** Phaser texture key (e.g., 'Objects-Floor') */
  textureKey: string;
  /** Name used in tilemap.addTilesetImage */
  name: string;
  /** Starting GID for this tileset */
  gid: number;
  /** Total number of tiles in this tileset */
  tileCount: number;
}

/**
 * Tilesets used for terrain rendering, with their GID offsets.
 * GIDs are spaced to avoid overlap.
 */
export const TERRAIN_TILESETS: TilesetConfig[] = [
  { textureKey: 'Objects-Floor', name: 'Floor', gid: 0, tileCount: 819 },
  { textureKey: 'Objects-Tile', name: 'Tile', gid: 1000, tileCount: 32 },
  { textureKey: 'Objects-Hill0', name: 'Hill0', gid: 2000, tileCount: 288 },
];

/**
 * Maps spritesheet paths to their GID offset.
 */
const SPRITESHEET_TO_GID: Record<string, number> = {
  'Objects/Floor.png': 0,
  'Objects/Tile.png': 1000,
  'Objects/Hill0.png': 2000,
};

/**
 * Resolves a sprite key to a global tile ID for use in tilemaps.
 * Returns the GID offset + local frame number.
 */
export function resolveTerrainTileId(
  spriteIndex: SpriteIndex,
  spriteKey: string
): number | null {
  const entry = spriteIndex[spriteKey];
  if (!entry) {
    console.warn(`Sprite key not found: ${spriteKey}`);
    return null;
  }

  const gidOffset = SPRITESHEET_TO_GID[entry.spritesheet];
  if (gidOffset === undefined) {
    console.warn(`Spritesheet not configured for terrain: ${entry.spritesheet}`);
    return null;
  }

  return gidOffset + entry.frame;
}

/**
 * Builds a lookup table from floor type to global tile ID.
 * Call this once after loading the sprite index.
 */
export function buildFloorTypeToTileId(
  spriteIndex: SpriteIndex
): Record<number, number> {
  const result: Record<number, number> = {};

  for (const [floorTypeStr, spriteKey] of Object.entries(FLOOR_TYPE_TO_SPRITE_KEY)) {
    const floorType = Number(floorTypeStr);
    const tileId = resolveTerrainTileId(spriteIndex, spriteKey);
    if (tileId !== null) {
      result[floorType] = tileId;
    }
  }

  // Add default fallback
  const defaultTileId = resolveTerrainTileId(spriteIndex, DEFAULT_TERRAIN_SPRITE_KEY);
  if (defaultTileId !== null) {
    result[-1] = defaultTileId; // Use -1 as sentinel for default
  }

  return result;
}
