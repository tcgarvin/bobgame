/**
 * Map and tile data structures for the viewer
 */

export interface Position {
  x: number;
  y: number;
}

export const TileType = {
  FLOOR: 'floor',
  WALL: 'wall',
} as const;

export type TileType = typeof TileType[keyof typeof TileType];

export interface Tile {
  type: TileType;
  walkable: boolean;
  opaque: boolean;
  spriteIndex: number;
}

export interface Entity {
  id: string;
  position: Position;
  spriteKey: string;
  spriteFrame: number;
}

export interface MapData {
  width: number;
  height: number;
  tiles: Tile[][];
  entities: Entity[];
}

/**
 * Creates an empty map filled with floor tiles
 */
export function createEmptyMap(width: number, height: number): MapData {
  const tiles: Tile[][] = [];

  for (let y = 0; y < height; y++) {
    tiles[y] = [];
    for (let x = 0; x < width; x++) {
      tiles[y][x] = {
        type: TileType.FLOOR,
        walkable: true,
        opaque: false,
        spriteIndex: 0,
      };
    }
  }

  return {
    width,
    height,
    tiles,
    entities: [],
  };
}

/**
 * Creates a simple room with walls around the border
 */
export function createTestRoom(width: number, height: number): MapData {
  const map = createEmptyMap(width, height);
  const stoneFloorTiles = [0, 1, 2];

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const isWall = x === 0 || x === width - 1 || y === 0 || y === height - 1;

      if (isWall) {
        map.tiles[y][x] = {
          type: TileType.WALL,
          walkable: false,
          opaque: true,
          spriteIndex: 0,
        };
      } else {
        map.tiles[y][x] = {
          type: TileType.FLOOR,
          walkable: true,
          opaque: false,
          spriteIndex: stoneFloorTiles[Math.floor(Math.random() * stoneFloorTiles.length)],
        };
      }
    }
  }

  // Add a player entity in the center
  map.entities.push({
    id: 'player',
    position: { x: Math.floor(width / 2), y: Math.floor(height / 2) },
    spriteKey: 'player0',
    spriteFrame: 0,
  });

  return map;
}
