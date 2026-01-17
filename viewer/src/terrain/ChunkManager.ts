/**
 * ChunkManager handles chunk-based terrain rendering using Phaser Tilemaps.
 * Each chunk is a separate tilemap for efficient loading/unloading.
 */

import Phaser from 'phaser';

const TILE_SIZE = 16;
const SCALE = 3;

// Floor type values from backend (world/src/world/state.py)
// Maps to sprite frames in Objects/Floor.png
const FLOOR_TYPE_TO_FRAME: Record<number, number> = {
  0: 8,    // deep_water -> water tile
  1: 8,    // shallow_water -> water tile (lighter)
  2: 407,  // sand -> sand/dirt tile
  3: 155,  // grass -> grass-full
  4: 400,  // dirt -> dirt-full
  5: 400,  // mountain -> use dirt for now (should be rock)
  6: 407,  // stone -> stone/floor tile
};

interface LoadedChunk {
  chunkX: number;
  chunkY: number;
  version: number;
  tilemap: Phaser.Tilemaps.Tilemap;
  layer: Phaser.Tilemaps.TilemapLayer;
}

export class ChunkManager {
  private scene: Phaser.Scene;
  private chunks: Map<string, LoadedChunk> = new Map();
  private chunkSize: number = 32;
  private tilesetKey: string = 'Objects-Floor';

  constructor(scene: Phaser.Scene, chunkSize: number = 32) {
    this.scene = scene;
    this.chunkSize = chunkSize;
  }

  /**
   * Get chunk key string from coordinates.
   */
  private chunkKey(chunkX: number, chunkY: number): string {
    return `${chunkX},${chunkY}`;
  }

  /**
   * Decode base64 RLE terrain data to Uint8Array.
   */
  private decodeTerrainBase64(data: string): Uint8Array {
    const binary = atob(data);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return this.decodeRLE(bytes);
  }

  /**
   * Decode RLE compressed terrain data.
   */
  private decodeRLE(data: Uint8Array): Uint8Array {
    const result: number[] = [];
    for (let i = 0; i < data.length - 1; i += 2) {
      const value = data[i];
      const count = data[i + 1];
      for (let j = 0; j < count; j++) {
        result.push(value);
      }
    }
    return new Uint8Array(result);
  }

  /**
   * Convert flat terrain array to 2D tile index array for Phaser.
   */
  private terrainToTileData(terrain: Uint8Array): number[][] {
    const data: number[][] = [];
    for (let y = 0; y < this.chunkSize; y++) {
      const row: number[] = [];
      for (let x = 0; x < this.chunkSize; x++) {
        const floorType = terrain[y * this.chunkSize + x];
        const frame = FLOOR_TYPE_TO_FRAME[floorType] ?? 155; // Default to grass
        row.push(frame);
      }
      data.push(row);
    }
    return data;
  }

  /**
   * Load a chunk with terrain data from the server.
   */
  loadChunk(
    chunkX: number,
    chunkY: number,
    terrainBase64: string,
    version: number
  ): void {
    const key = this.chunkKey(chunkX, chunkY);

    // Unload existing chunk if present
    if (this.chunks.has(key)) {
      this.unloadChunk(chunkX, chunkY);
    }

    // Decode terrain data
    const terrain = this.decodeTerrainBase64(terrainBase64);
    const tileData = this.terrainToTileData(terrain);

    // Create tilemap from data
    const tilemap = this.scene.make.tilemap({
      data: tileData,
      tileWidth: TILE_SIZE,
      tileHeight: TILE_SIZE,
    });

    // Add the tileset (must be preloaded)
    const tileset = tilemap.addTilesetImage('terrain', this.tilesetKey);
    if (!tileset) {
      console.error('Failed to add tileset for chunk', chunkX, chunkY);
      return;
    }

    // Calculate world position for this chunk
    const worldX = chunkX * this.chunkSize * TILE_SIZE * SCALE;
    const worldY = chunkY * this.chunkSize * TILE_SIZE * SCALE;

    // Create the layer at the correct world position
    const layer = tilemap.createLayer(0, tileset, worldX, worldY);
    if (!layer) {
      console.error('Failed to create layer for chunk', chunkX, chunkY);
      return;
    }

    layer.setScale(SCALE);
    layer.setDepth(0); // Below entities and objects

    // Store the loaded chunk
    this.chunks.set(key, {
      chunkX,
      chunkY,
      version,
      tilemap,
      layer,
    });

    console.log(`Loaded chunk (${chunkX}, ${chunkY}) v${version}`);
  }

  /**
   * Unload a chunk, freeing its resources.
   */
  unloadChunk(chunkX: number, chunkY: number): void {
    const key = this.chunkKey(chunkX, chunkY);
    const chunk = this.chunks.get(key);

    if (chunk) {
      chunk.layer.destroy();
      chunk.tilemap.destroy();
      this.chunks.delete(key);
      console.log(`Unloaded chunk (${chunkX}, ${chunkY})`);
    }
  }

  /**
   * Update a single tile within a chunk.
   */
  updateTile(
    chunkX: number,
    chunkY: number,
    localX: number,
    localY: number,
    floorType: number
  ): void {
    const key = this.chunkKey(chunkX, chunkY);
    const chunk = this.chunks.get(key);

    if (chunk) {
      const frame = FLOOR_TYPE_TO_FRAME[floorType] ?? 155;
      chunk.layer.putTileAt(frame, localX, localY);
    }
  }

  /**
   * Check if a chunk is loaded.
   */
  isChunkLoaded(chunkX: number, chunkY: number): boolean {
    return this.chunks.has(this.chunkKey(chunkX, chunkY));
  }

  /**
   * Get the version of a loaded chunk.
   */
  getChunkVersion(chunkX: number, chunkY: number): number | undefined {
    const chunk = this.chunks.get(this.chunkKey(chunkX, chunkY));
    return chunk?.version;
  }

  /**
   * Get all loaded chunk coordinates.
   */
  getLoadedChunks(): Array<[number, number]> {
    return Array.from(this.chunks.values()).map((c) => [c.chunkX, c.chunkY]);
  }

  /**
   * Unload all chunks.
   */
  clear(): void {
    for (const chunk of this.chunks.values()) {
      chunk.layer.destroy();
      chunk.tilemap.destroy();
    }
    this.chunks.clear();
  }
}
