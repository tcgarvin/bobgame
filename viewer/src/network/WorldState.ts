/**
 * Synchronized world state with smooth interpolation between server ticks.
 * Manages entity positions and provides interpolated coordinates for rendering.
 */

import type {
  EntityState,
  ObjectState,
  SnapshotMessage,
  TickStartedMessage,
  TickCompletedMessage,
  EntitySpawnedMessage,
  EntityDespawnedMessage,
  ChunkDataMessage,
  TerrainUpdateMessage,
  ChunkUnloadMessage,
  ViewerMessage,
} from './types';
import {
  isSnapshotMessage,
  isTickStartedMessage,
  isTickCompletedMessage,
  isEntitySpawnedMessage,
  isEntityDespawnedMessage,
  isChunkDataMessage,
  isTerrainUpdateMessage,
  isChunkUnloadMessage,
} from './types';

/**
 * Entity with interpolation state for smooth rendering
 */
export interface InterpolatedEntity {
  entityId: string;
  entityType: string;
  tags: string[];
  // Current interpolated position (for rendering)
  currentX: number;
  currentY: number;
  // Target position (from last tick_completed)
  targetX: number;
  targetY: number;
  // Position at tick start (for interpolation origin)
  startX: number;
  startY: number;
}

export type EntityChangeHandler = (
  action: 'added' | 'removed',
  entity: InterpolatedEntity
) => void;

/**
 * Tracked world object (bushes, etc.)
 */
export interface TrackedObject {
  objectId: string;
  objectType: string;
  position: { x: number; y: number };
  state: Record<string, string>;
}

export type ObjectChangeHandler = (
  action: 'added' | 'removed' | 'updated',
  object: TrackedObject
) => void;

/**
 * Chunk change handler types
 */
export type ChunkChangeHandler = (
  action: 'loaded' | 'unloaded' | 'terrain_updated',
  chunkX: number,
  chunkY: number,
  terrain?: string,
  version?: number,
  changes?: Array<{ x: number; y: number; floor_type: number }>
) => void;

/**
 * Ease-out quadratic function for smoother movement feel
 */
function easeOutQuad(t: number): number {
  return t * (2 - t);
}

export class WorldState {
  private entities: Map<string, InterpolatedEntity> = new Map();
  private objects: Map<string, TrackedObject> = new Map();
  private currentTickId: number = 0;
  private tickDurationMs: number = 1000;
  private tickStartTime: number = 0;
  private worldSize: { width: number; height: number } = { width: 100, height: 100 };
  private chunkSize: number = 32;
  private initialized: boolean = false;
  private entityChangeHandler: EntityChangeHandler | null = null;
  private objectChangeHandler: ObjectChangeHandler | null = null;
  private chunkChangeHandler: ChunkChangeHandler | null = null;

  /**
   * Set handler for entity add/remove events
   */
  onEntityChange(handler: EntityChangeHandler): void {
    this.entityChangeHandler = handler;
  }

  /**
   * Set handler for object add/remove/update events
   */
  onObjectChange(handler: ObjectChangeHandler): void {
    this.objectChangeHandler = handler;
  }

  /**
   * Set handler for chunk load/unload/update events
   */
  onChunkChange(handler: ChunkChangeHandler): void {
    this.chunkChangeHandler = handler;
  }

  /**
   * Check if world state has been initialized with a snapshot
   */
  isInitialized(): boolean {
    return this.initialized;
  }

  /**
   * Get world dimensions
   */
  getWorldSize(): { width: number; height: number } {
    return { ...this.worldSize };
  }

  /**
   * Get chunk size
   */
  getChunkSize(): number {
    return this.chunkSize;
  }

  /**
   * Get current tick ID
   */
  getCurrentTick(): number {
    return this.currentTickId;
  }

  /**
   * Get all entities (for rendering)
   */
  getEntities(): InterpolatedEntity[] {
    return Array.from(this.entities.values());
  }

  /**
   * Get a specific entity by ID
   */
  getEntity(entityId: string): InterpolatedEntity | undefined {
    return this.entities.get(entityId);
  }

  /**
   * Get all objects (for rendering)
   */
  getObjects(): TrackedObject[] {
    return Array.from(this.objects.values());
  }

  /**
   * Get a specific object by ID
   */
  getObject(objectId: string): TrackedObject | undefined {
    return this.objects.get(objectId);
  }

  /**
   * Handle incoming WebSocket message
   */
  handleMessage(message: ViewerMessage): void {
    if (isSnapshotMessage(message)) {
      this.handleSnapshot(message);
    } else if (isTickStartedMessage(message)) {
      this.handleTickStarted(message);
    } else if (isTickCompletedMessage(message)) {
      this.handleTickCompleted(message);
    } else if (isEntitySpawnedMessage(message)) {
      this.handleEntitySpawned(message);
    } else if (isEntityDespawnedMessage(message)) {
      this.handleEntityDespawned(message);
    } else if (isChunkDataMessage(message)) {
      this.handleChunkData(message);
    } else if (isTerrainUpdateMessage(message)) {
      this.handleTerrainUpdate(message);
    } else if (isChunkUnloadMessage(message)) {
      this.handleChunkUnload(message);
    }
  }

  /**
   * Initialize world state from snapshot (metadata only, no entities/objects)
   */
  private handleSnapshot(msg: SnapshotMessage): void {
    console.log(
      `Received snapshot: tick=${msg.tick_id}, world=${msg.world_size.width}x${msg.world_size.height}, chunk_size=${msg.chunk_size}`
    );

    // Clear existing entities
    for (const entity of this.entities.values()) {
      this.entityChangeHandler?.('removed', entity);
    }
    this.entities.clear();

    // Clear existing objects
    for (const obj of this.objects.values()) {
      this.objectChangeHandler?.('removed', obj);
    }
    this.objects.clear();

    // Set world state
    this.currentTickId = msg.tick_id;
    this.tickDurationMs = msg.tick_duration_ms;
    this.worldSize = msg.world_size;
    this.chunkSize = msg.chunk_size;
    this.tickStartTime = performance.now();

    // Note: Entities and objects now come via chunk_data messages
    this.initialized = true;
  }

  /**
   * Handle chunk data from server
   */
  private handleChunkData(msg: ChunkDataMessage): void {
    console.log(
      `Received chunk (${msg.chunk_x}, ${msg.chunk_y}) v${msg.version}: ${msg.entities.length} entities, ${msg.objects.length} objects`
    );

    // Notify chunk manager to load terrain
    this.chunkChangeHandler?.(
      'loaded',
      msg.chunk_x,
      msg.chunk_y,
      msg.terrain,
      msg.version
    );

    // Add entities from this chunk
    for (const entityState of msg.entities) {
      if (!this.entities.has(entityState.entity_id)) {
        const entity = this.createInterpolatedEntity(entityState);
        this.entities.set(entity.entityId, entity);
        this.entityChangeHandler?.('added', entity);
      }
    }

    // Add objects from this chunk
    for (const objState of msg.objects) {
      if (!this.objects.has(objState.object_id)) {
        const obj = this.createTrackedObject(objState);
        this.objects.set(obj.objectId, obj);
        this.objectChangeHandler?.('added', obj);
      }
    }
  }

  /**
   * Handle terrain update within a chunk
   */
  private handleTerrainUpdate(msg: TerrainUpdateMessage): void {
    this.chunkChangeHandler?.(
      'terrain_updated',
      msg.chunk_x,
      msg.chunk_y,
      undefined,
      msg.version,
      msg.changes
    );
  }

  /**
   * Handle chunk unload notification
   */
  private handleChunkUnload(msg: ChunkUnloadMessage): void {
    console.log(`Unloading chunk (${msg.chunk_x}, ${msg.chunk_y})`);
    this.chunkChangeHandler?.('unloaded', msg.chunk_x, msg.chunk_y);

    // Note: We don't remove entities/objects here because they may still be
    // visible in other chunks or moving between chunks. The tick_completed
    // updates will handle entity positions regardless of chunk subscriptions.
  }

  /**
   * Handle tick start - prepare for new interpolation cycle
   */
  private handleTickStarted(msg: TickStartedMessage): void {
    this.currentTickId = msg.tick_id;
    this.tickDurationMs = msg.tick_duration_ms;
    this.tickStartTime = performance.now();

    // Snap current positions to targets and prepare for new interpolation
    for (const entity of this.entities.values()) {
      entity.startX = entity.currentX;
      entity.startY = entity.currentY;
      // Target remains unchanged until tick_completed
    }
  }

  /**
   * Handle tick completion - update entity target positions
   */
  private handleTickCompleted(msg: TickCompletedMessage): void {
    for (const move of msg.moves) {
      const entity = this.entities.get(move.entity_id);
      if (entity && move.success) {
        // Update target position for interpolation
        entity.targetX = move.to.x;
        entity.targetY = move.to.y;
        // Reset start position to current for smooth transition
        entity.startX = entity.currentX;
        entity.startY = entity.currentY;
      }
    }

    // Apply object changes
    for (const change of msg.object_changes ?? []) {
      const obj = this.objects.get(change.object_id);
      if (obj) {
        obj.state[change.field] = change.new_value;
        this.objectChangeHandler?.('updated', obj);
      }
    }

    // Reset tick start time for movement interpolation
    this.tickStartTime = performance.now();
  }

  /**
   * Handle new entity spawn
   */
  private handleEntitySpawned(msg: EntitySpawnedMessage): void {
    const entity = this.createInterpolatedEntity(msg.entity);
    this.entities.set(entity.entityId, entity);
    this.entityChangeHandler?.('added', entity);
  }

  /**
   * Handle entity despawn
   */
  private handleEntityDespawned(msg: EntityDespawnedMessage): void {
    const entity = this.entities.get(msg.entity_id);
    if (entity) {
      this.entities.delete(msg.entity_id);
      this.entityChangeHandler?.('removed', entity);
    }
  }

  /**
   * Update interpolation for all entities (call each frame)
   */
  update(_deltaMs: number): void {
    if (!this.initialized) return;

    const now = performance.now();
    const elapsed = now - this.tickStartTime;
    const rawProgress = Math.min(1, elapsed / this.tickDurationMs);
    const progress = easeOutQuad(rawProgress);

    for (const entity of this.entities.values()) {
      // Interpolate position
      entity.currentX = entity.startX + (entity.targetX - entity.startX) * progress;
      entity.currentY = entity.startY + (entity.targetY - entity.startY) * progress;
    }
  }

  /**
   * Create an interpolated entity from server state
   */
  private createInterpolatedEntity(state: EntityState): InterpolatedEntity {
    return {
      entityId: state.entity_id,
      entityType: state.entity_type,
      tags: state.tags,
      currentX: state.position.x,
      currentY: state.position.y,
      targetX: state.position.x,
      targetY: state.position.y,
      startX: state.position.x,
      startY: state.position.y,
    };
  }

  /**
   * Create a tracked object from server state
   */
  private createTrackedObject(state: ObjectState): TrackedObject {
    return {
      objectId: state.object_id,
      objectType: state.object_type,
      position: { x: state.position.x, y: state.position.y },
      state: { ...state.state },
    };
  }
}
