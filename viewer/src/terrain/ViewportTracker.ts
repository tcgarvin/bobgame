/**
 * ViewportTracker monitors camera viewport and calculates which chunks are visible.
 * Triggers chunk subscription updates when the viewport changes.
 */

import Phaser from 'phaser';

const TILE_SIZE = 16;
const SCALE = 3;

interface Viewport {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type ChunkSubscriptionCallback = (chunks: Array<[number, number]>) => void;

export class ViewportTracker {
  private scene: Phaser.Scene;
  private chunkSize: number;
  private lastChunks: Set<string> = new Set();
  private onChunkSubscriptionChange: ChunkSubscriptionCallback;
  private padding: number = 1; // Load extra chunks around viewport

  constructor(
    scene: Phaser.Scene,
    chunkSize: number,
    onChunkSubscriptionChange: ChunkSubscriptionCallback
  ) {
    this.scene = scene;
    this.chunkSize = chunkSize;
    this.onChunkSubscriptionChange = onChunkSubscriptionChange;
  }

  /**
   * Get the current viewport in world tile coordinates.
   */
  private getWorldViewport(): Viewport {
    const camera = this.scene.cameras.main;
    const tilePixelSize = TILE_SIZE * SCALE;

    // Get camera bounds in world space
    const scrollX = camera.scrollX;
    const scrollY = camera.scrollY;
    const viewWidth = camera.width / camera.zoom;
    const viewHeight = camera.height / camera.zoom;

    return {
      x: Math.floor(scrollX / tilePixelSize),
      y: Math.floor(scrollY / tilePixelSize),
      width: Math.ceil(viewWidth / tilePixelSize) + 1,
      height: Math.ceil(viewHeight / tilePixelSize) + 1,
    };
  }

  /**
   * Calculate which chunks cover the given viewport.
   */
  private getChunksForViewport(viewport: Viewport): Array<[number, number]> {
    const chunks: Array<[number, number]> = [];

    const startChunkX = Math.max(0, Math.floor(viewport.x / this.chunkSize) - this.padding);
    const startChunkY = Math.max(0, Math.floor(viewport.y / this.chunkSize) - this.padding);
    const endChunkX = Math.floor((viewport.x + viewport.width) / this.chunkSize) + this.padding;
    const endChunkY = Math.floor((viewport.y + viewport.height) / this.chunkSize) + this.padding;

    for (let cy = startChunkY; cy <= endChunkY; cy++) {
      for (let cx = startChunkX; cx <= endChunkX; cx++) {
        chunks.push([cx, cy]);
      }
    }

    return chunks;
  }

  /**
   * Convert chunk array to set key for comparison.
   */
  private chunksToSet(chunks: Array<[number, number]>): Set<string> {
    return new Set(chunks.map(([x, y]) => `${x},${y}`));
  }

  /**
   * Check if two chunk sets are equal.
   */
  private chunksEqual(a: Set<string>, b: Set<string>): boolean {
    if (a.size !== b.size) return false;
    for (const key of a) {
      if (!b.has(key)) return false;
    }
    return true;
  }

  /**
   * Update viewport tracking. Call this every frame or on camera change.
   * Returns true if chunks changed.
   */
  update(): boolean {
    const viewport = this.getWorldViewport();
    const chunks = this.getChunksForViewport(viewport);
    const chunkSet = this.chunksToSet(chunks);

    if (!this.chunksEqual(chunkSet, this.lastChunks)) {
      this.lastChunks = chunkSet;
      this.onChunkSubscriptionChange(chunks);
      return true;
    }

    return false;
  }

  /**
   * Force an immediate subscription update.
   */
  forceUpdate(): void {
    const viewport = this.getWorldViewport();
    const chunks = this.getChunksForViewport(viewport);
    this.lastChunks = this.chunksToSet(chunks);
    this.onChunkSubscriptionChange(chunks);
  }

  /**
   * Get current viewport in tile coordinates (for debugging).
   */
  getViewport(): Viewport {
    return this.getWorldViewport();
  }

  /**
   * Get currently subscribed chunks (for debugging).
   */
  getSubscribedChunks(): Array<[number, number]> {
    return Array.from(this.lastChunks).map((key) => {
      const [x, y] = key.split(',').map(Number);
      return [x, y];
    });
  }
}
