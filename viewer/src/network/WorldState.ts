/**
 * Synchronized world state with smooth interpolation between server ticks.
 * Manages entity positions, per-entity stats, agent status and selection,
 * and provides interpolated coordinates for rendering.
 */

import type {
  AgentDetailMessage,
  AgentStatusMessage,
  EntityLogMessage,
  ErrorMessage,
  ReplayInfo,
  ReplayStatusMessage,
  RunIndexMessage,
  EntityState,
  EntityUpdate,
  ObjectState,
  Position,
  SnapshotMessage,
  TickStartedMessage,
  TickCompletedMessage,
  EntitySpawnedMessage,
  EntityDespawnedMessage,
  ChunkDataMessage,
  TerrainUpdateMessage,
  ChunkUnloadMessage,
  UtteranceEvent,
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
  isAgentStatusMessage,
  isAgentDetailMessage,
  isEntityLogMessage,
  isErrorMessage,
  isReplayStatusMessage,
  isRunIndexMessage,
} from './types';

/** How many action/utterance log entries are kept per entity. */
export const ENTITY_LOG_SIZE = 10;

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
  // Stats (defaults are used until the server reports real values)
  health: number;
  maxHealth: number;
  hunger: number;
  maxHunger: number;
  wielded: string;
  alive: boolean;
  inventory: Record<string, number>;
}

/** One line in an entity's recent action/utterance log. */
export interface EntityLogEntry {
  tick: number;
  kind: 'action' | 'utterance';
  text: string;
  success: boolean;
  channel: string;
}

export type EntityChangeHandler = (
  action: 'added' | 'removed',
  entity: InterpolatedEntity
) => void;

/** Called when an entity's stats change, with the health delta for this tick. */
export type EntityStatsHandler = (
  entity: InterpolatedEntity,
  healthDelta: number
) => void;

export type UtteranceHandler = (utterance: UtteranceEvent) => void;

/** Called after any message that changes data the UI overlays display. */
export type StateUpdateHandler = () => void;

export type SelectionHandler = (entityId: string) => void;

/** Called when the selected object changes ('' when cleared). */
export type ObjectSelectionHandler = (objectId: string) => void;

/** How many agent_detail replies are cached (keyed by entity and tick). */
const AGENT_DETAIL_CACHE_SIZE = 64;

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

const DEFAULT_MAX_HEALTH = 20;
const DEFAULT_MAX_HUNGER = 100;

export class WorldState {
  private entities: Map<string, InterpolatedEntity> = new Map();
  private objects: Map<string, TrackedObject> = new Map();
  private entityLogs: Map<string, EntityLogEntry[]> = new Map();
  private agentStatuses: Map<string, AgentStatusMessage> = new Map();
  private agentDetails: Map<string, AgentDetailMessage> = new Map();
  private selectedEntityId: string = '';
  private selectedObjectId: string = '';
  private runId: string = '';
  private replayInfo: ReplayInfo | null = null;
  private replayStatus: ReplayStatusMessage | null = null;
  private runIndex: RunIndexMessage | null = null;
  private lastError: string = '';
  private settlement: Position | null = null;
  private currentTickId: number = 0;
  private tickDurationMs: number = 1000;
  private tickStartTime: number = 0;
  private worldSize: { width: number; height: number } = { width: 100, height: 100 };
  private chunkSize: number = 32;
  private initialized: boolean = false;
  private entityChangeHandler: EntityChangeHandler | null = null;
  private objectChangeHandler: ObjectChangeHandler | null = null;
  private chunkChangeHandler: ChunkChangeHandler | null = null;
  private entityStatsHandler: EntityStatsHandler | null = null;
  private utteranceHandler: UtteranceHandler | null = null;
  private stateUpdateHandler: StateUpdateHandler | null = null;
  private selectionHandler: SelectionHandler | null = null;
  private objectSelectionHandler: ObjectSelectionHandler | null = null;

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

  /** Set handler called once per entity per tick when stats arrive. */
  onEntityStats(handler: EntityStatsHandler): void {
    this.entityStatsHandler = handler;
  }

  /** Set handler for utterances (both channels). */
  onUtterance(handler: UtteranceHandler): void {
    this.utteranceHandler = handler;
  }

  /** Set handler called after any message that changes overlay data. */
  onStateUpdate(handler: StateUpdateHandler): void {
    this.stateUpdateHandler = handler;
  }

  /** Set handler called when the selected entity changes. */
  onSelectionChange(handler: SelectionHandler): void {
    this.selectionHandler = handler;
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

  /** Settlement centre, or null if the server never sent one. */
  getSettlement(): Position | null {
    return this.settlement ? { ...this.settlement } : null;
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

  /** Last 10 actions/utterances for an entity, newest first. */
  getEntityLog(entityId: string): EntityLogEntry[] {
    const log = this.entityLogs.get(entityId);
    if (!log) return [];
    return log.slice().reverse();
  }

  /** Latest agent status for an entity, or null if none has arrived. */
  getAgentStatus(entityId: string): AgentStatusMessage | null {
    return this.agentStatuses.get(entityId) ?? null;
  }

  /** Currently selected entity id, or '' when nothing is selected. */
  getSelectedEntityId(): string {
    return this.selectedEntityId;
  }

  getSelectedEntity(): InterpolatedEntity | undefined {
    return this.selectedEntityId ? this.entities.get(this.selectedEntityId) : undefined;
  }

  /** Select an entity (pass '' to clear). Notifies the selection handler. */
  setSelectedEntity(entityId: string): void {
    if (entityId) {
      this.setSelectedObject('');
    }
    if (this.selectedEntityId === entityId) return;
    this.selectedEntityId = entityId;
    this.selectionHandler?.(entityId);
    this.stateUpdateHandler?.();
  }

  /** Set handler called when the selected object changes. */
  onObjectSelectionChange(handler: ObjectSelectionHandler): void {
    this.objectSelectionHandler = handler;
  }

  /** Currently selected object id, or '' when nothing is selected. */
  getSelectedObjectId(): string {
    return this.selectedObjectId;
  }

  getSelectedObject(): TrackedObject | undefined {
    return this.selectedObjectId ? this.objects.get(this.selectedObjectId) : undefined;
  }

  /**
   * Select an object (pass '' to clear). Entity and object selection are
   * mutually exclusive so a deep link always names at most one of them.
   */
  setSelectedObject(objectId: string): void {
    if (objectId && this.selectedEntityId) {
      this.selectedEntityId = '';
      this.selectionHandler?.('');
    }
    if (this.selectedObjectId === objectId) return;
    this.selectedObjectId = objectId;
    this.objectSelectionHandler?.(objectId);
    this.stateUpdateHandler?.();
  }

  // --- Replay ---

  /** Run id of the recording being watched ('' when the server sends none). */
  getRunId(): string {
    return this.runId;
  }

  /** True when the server identified itself as a replay in its snapshot. */
  isReplay(): boolean {
    return this.replayInfo !== null;
  }

  /** Latest replay position, or null in live mode. */
  getReplayStatus(): ReplayStatusMessage | null {
    return this.replayStatus;
  }

  /** Latest run index, or null when none has arrived. */
  getRunIndex(): RunIndexMessage | null {
    return this.runIndex;
  }

  /** Last error message from the server ('' when none). */
  getLastError(): string {
    return this.lastError;
  }

  /** Cached agent detail for one entity at one tick, if it has arrived. */
  getAgentDetail(entityId: string, tickId: number): AgentDetailMessage | null {
    return this.agentDetails.get(`${entityId}@${tickId}`) ?? null;
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
    } else if (isAgentStatusMessage(message)) {
      this.handleAgentStatus(message);
    } else if (isReplayStatusMessage(message)) {
      this.handleReplayStatus(message);
    } else if (isRunIndexMessage(message)) {
      this.handleRunIndex(message);
    } else if (isEntityLogMessage(message)) {
      this.handleEntityLog(message);
    } else if (isAgentDetailMessage(message)) {
      this.handleAgentDetail(message);
    } else if (isErrorMessage(message)) {
      this.handleError(message);
    }
  }

  private handleReplayStatus(msg: ReplayStatusMessage): void {
    this.replayStatus = msg;
    if (this.replayInfo) {
      this.replayInfo = { ...this.replayInfo, ...msg, run_id: this.replayInfo.run_id };
    }
    this.currentTickId = msg.tick_id;
    this.stateUpdateHandler?.();
  }

  private handleRunIndex(msg: RunIndexMessage): void {
    this.runIndex = msg;
    if (msg.run_id) this.runId = msg.run_id;
    this.stateUpdateHandler?.();
  }

  /**
   * Backfilled action/utterance history sent after a seek. The logs were
   * cleared by the preceding snapshot, so entries are simply appended in the
   * order the server sent them (oldest first).
   */
  private handleEntityLog(msg: EntityLogMessage): void {
    for (const entry of msg.entries ?? []) {
      this.appendLog(entry.entity_id, {
        tick: entry.tick_id,
        kind: entry.kind === 'utterance' ? 'utterance' : 'action',
        text: entry.text,
        success: entry.success ?? true,
        channel: entry.channel ?? '',
      });
    }
    this.stateUpdateHandler?.();
  }

  private handleAgentDetail(msg: AgentDetailMessage): void {
    const key = `${msg.entity_id}@${msg.tick_id}`;
    this.agentDetails.delete(key);
    this.agentDetails.set(key, msg);
    while (this.agentDetails.size > AGENT_DETAIL_CACHE_SIZE) {
      const oldest = this.agentDetails.keys().next();
      if (oldest.done) break;
      this.agentDetails.delete(oldest.value);
    }
    this.stateUpdateHandler?.();
  }

  private handleError(msg: ErrorMessage): void {
    this.lastError = msg.message;
    console.error(`Server error: ${msg.message}`);
    this.stateUpdateHandler?.();
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
    this.entityLogs.clear();
    this.agentStatuses.clear();

    // Clear existing objects
    for (const obj of this.objects.values()) {
      this.objectChangeHandler?.('removed', obj);
    }
    this.objects.clear();

    // The selection survives a re-snapshot (a replay seek sends one); the
    // sprites and panels are rebuilt from the messages that follow.

    // Set world state
    this.runId = msg.run_id ?? this.runId;
    this.replayInfo = msg.replay ? { ...msg.replay } : null;
    if (msg.replay) {
      this.runId = msg.replay.run_id || this.runId;
      this.replayStatus = {
        type: 'replay_status',
        tick_id: msg.replay.tick_id,
        playing: msg.replay.playing,
        speed: msg.replay.speed,
        first_tick: msg.replay.first_tick,
        last_tick: msg.replay.last_tick,
      };
    }
    this.currentTickId = msg.tick_id;
    this.tickDurationMs = msg.tick_duration_ms;
    this.worldSize = msg.world_size;
    this.chunkSize = msg.chunk_size;
    this.settlement = msg.settlement ? { ...msg.settlement } : null;
    this.tickStartTime = performance.now();

    // Note: Entities and objects now come via chunk_data messages
    this.initialized = true;
    this.stateUpdateHandler?.();
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

    this.stateUpdateHandler?.();
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
   * Handle tick completion - update entity target positions, stats, log entries
   * and object additions/removals.
   */
  private handleTickCompleted(msg: TickCompletedMessage): void {
    for (const move of msg.moves ?? []) {
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

    // Full per-entity state (positions + stats). Also covers entities that
    // appeared without an explicit entity_spawned message.
    for (const update of msg.entity_updates ?? []) {
      this.applyEntityUpdate(update);
    }

    // Apply object changes
    for (const change of msg.object_changes ?? []) {
      const obj = this.objects.get(change.object_id);
      if (obj) {
        obj.state[change.field] = change.new_value;
        this.objectChangeHandler?.('updated', obj);
      }
    }

    for (const objState of msg.objects_added ?? []) {
      const obj = this.createTrackedObject(objState);
      const existing = this.objects.get(obj.objectId);
      this.objects.set(obj.objectId, obj);
      this.objectChangeHandler?.(existing ? 'updated' : 'added', obj);
    }

    for (const objectId of msg.objects_removed ?? []) {
      const obj = this.objects.get(objectId);
      if (obj) {
        this.objects.delete(objectId);
        this.objectChangeHandler?.('removed', obj);
        if (this.selectedObjectId === objectId) {
          this.setSelectedObject('');
        }
      }
    }

    for (const action of msg.actions ?? []) {
      const detail = action.details ? ` ${action.details}` : '';
      this.appendLog(action.entity_id, {
        tick: msg.tick_id,
        kind: 'action',
        text: `${action.action_type}${detail}`,
        success: action.success,
        channel: '',
      });
    }

    for (const utterance of msg.utterances ?? []) {
      this.appendLog(utterance.speaker_id, {
        tick: msg.tick_id,
        kind: 'utterance',
        text: utterance.text,
        success: true,
        channel: utterance.channel,
      });
      this.utteranceHandler?.(utterance);
    }

    // Reset tick start time for movement interpolation
    this.tickStartTime = performance.now();
    this.stateUpdateHandler?.();
  }

  /**
   * Apply one entity_updates entry, creating the entity if it is new.
   * Reports the health delta so the renderer can flash damaged entities.
   */
  private applyEntityUpdate(update: EntityUpdate): void {
    let entity = this.entities.get(update.entity_id);
    if (!entity) {
      entity = this.createInterpolatedEntity({ ...update, tags: [] });
      this.entities.set(entity.entityId, entity);
      this.entityChangeHandler?.('added', entity);
    }

    entity.entityType = update.entity_type;

    if (entity.targetX !== update.position.x || entity.targetY !== update.position.y) {
      entity.targetX = update.position.x;
      entity.targetY = update.position.y;
      entity.startX = entity.currentX;
      entity.startY = entity.currentY;
    }

    const previousHealth = entity.health;
    if (update.max_health !== undefined) entity.maxHealth = update.max_health;
    if (update.health !== undefined) entity.health = update.health;
    if (update.max_hunger !== undefined) entity.maxHunger = update.max_hunger;
    if (update.hunger !== undefined) entity.hunger = update.hunger;
    if (update.wielded !== undefined) entity.wielded = update.wielded;
    if (update.alive !== undefined) entity.alive = update.alive;
    if (update.inventory !== undefined) entity.inventory = { ...update.inventory };

    this.entityStatsHandler?.(entity, entity.health - previousHealth);
  }

  private appendLog(entityId: string, entry: EntityLogEntry): void {
    let log = this.entityLogs.get(entityId);
    if (!log) {
      log = [];
      this.entityLogs.set(entityId, log);
    }
    log.push(entry);
    if (log.length > ENTITY_LOG_SIZE) {
      log.splice(0, log.length - ENTITY_LOG_SIZE);
    }
  }

  /**
   * Handle new entity spawn
   */
  private handleEntitySpawned(msg: EntitySpawnedMessage): void {
    const existing = this.entities.get(msg.entity.entity_id);
    if (existing) {
      // Respawn of a known entity: reset its stats and position in place.
      this.entities.delete(existing.entityId);
      this.entityChangeHandler?.('removed', existing);
    }
    const entity = this.createInterpolatedEntity(msg.entity);
    this.entities.set(entity.entityId, entity);
    this.entityChangeHandler?.('added', entity);
    this.stateUpdateHandler?.();
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
    this.entityLogs.delete(msg.entity_id);
    this.agentStatuses.delete(msg.entity_id);
    if (this.selectedEntityId === msg.entity_id) {
      this.setSelectedEntity('');
    }
    this.stateUpdateHandler?.();
  }

  private handleAgentStatus(msg: AgentStatusMessage): void {
    this.agentStatuses.set(msg.entity_id, msg);
    this.stateUpdateHandler?.();
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
    const maxHealth = state.max_health ?? DEFAULT_MAX_HEALTH;
    const maxHunger = state.max_hunger ?? DEFAULT_MAX_HUNGER;
    return {
      entityId: state.entity_id,
      entityType: state.entity_type,
      tags: state.tags ?? [],
      currentX: state.position.x,
      currentY: state.position.y,
      targetX: state.position.x,
      targetY: state.position.y,
      startX: state.position.x,
      startY: state.position.y,
      health: state.health ?? maxHealth,
      maxHealth,
      hunger: state.hunger ?? maxHunger,
      maxHunger,
      wielded: state.wielded ?? '',
      alive: state.alive ?? true,
      inventory: { ...(state.inventory ?? {}) },
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
