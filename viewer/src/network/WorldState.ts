/**
 * Synchronized world state with smooth interpolation between server ticks.
 * Manages entity positions, per-entity stats, agent status and selection,
 * and provides interpolated coordinates for rendering.
 */

import type {
  AgentCost,
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
  WorldClock,
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

/** How many (tick, total spend) points the run cost history keeps. */
export const COST_HISTORY_SIZE = 600;

/** Window, in ticks, the recent spend rate is measured over. */
const COST_RECENT_WINDOW_TICKS = 150;

/** Ticks of history needed before a recent rate is reported at all. */
const COST_MIN_WINDOW_TICKS = 30;

const MS_PER_HOUR = 3_600_000;

/** One point in the run's spend history. */
export interface CostPoint {
  tickId: number;
  totalUsd: number;
}

/**
 * Run-wide spend: every settler's latest cost summed, plus spend rates.
 * The rates are null until there is enough history to measure them.
 */
export interface RunCost {
  planner_usd: number;
  converser_usd: number;
  jev_usd: number;
  total_usd: number;
  usd_per_hour_recent: number | null;
  usd_per_hour_average: number | null;
}

/** Read a cost payload from the wire, defaulting every missing number. */
function normaliseCost(raw: Partial<AgentCost> | null | undefined): AgentCost | null {
  if (!raw || typeof raw !== 'object') return null;
  const number = (value: unknown): number =>
    typeof value === 'number' && Number.isFinite(value) ? value : 0;
  return {
    planner_usd: number(raw.planner_usd),
    converser_usd: number(raw.converser_usd),
    jev_usd: number(raw.jev_usd),
    total_usd: number(raw.total_usd),
    planner_turns: number(raw.planner_turns),
    jev_calls: number(raw.jev_calls),
  };
}

const ZERO_COST: AgentCost = {
  planner_usd: 0,
  converser_usd: 0,
  jev_usd: 0,
  total_usd: 0,
  planner_turns: 0,
  jev_calls: 0,
};

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
  /** Tiredness, 0 = fresh; `maxFatigue` is the collapse point. */
  fatigue: number;
  maxFatigue: number;
  asleep: boolean;
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
const DEFAULT_MAX_FATIGUE = 100;

export class WorldState {
  private entities: Map<string, InterpolatedEntity> = new Map();
  private objects: Map<string, TrackedObject> = new Map();
  private entityLogs: Map<string, EntityLogEntry[]> = new Map();
  private agentStatuses: Map<string, AgentStatusMessage> = new Map();
  private agentCosts: Map<string, AgentCost> = new Map();
  /** Spend an agent process booked before its current process restarted. */
  private costOffsets: Map<string, AgentCost> = new Map();
  private costHistory: CostPoint[] = [];
  private agentDetails: Map<string, AgentDetailMessage> = new Map();
  private selectedEntityId: string = '';
  private selectedObjectId: string = '';
  private runId: string = '';
  private replayInfo: ReplayInfo | null = null;
  private replayStatus: ReplayStatusMessage | null = null;
  private runIndex: RunIndexMessage | null = null;
  private lastError: string = '';
  private settlement: Position | null = null;
  private clock: WorldClock | null = null;
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

  /**
   * The clock reported with the latest tick, or null on a server without one.
   * Everything day/night is derived from this, so a replay seek is exact.
   */
  getClock(): WorldClock | null {
    return this.clock ? { ...this.clock } : null;
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

  /** Latest reported spend for an entity, or null if none has arrived. */
  getAgentCost(entityId: string): AgentCost | null {
    const cost = this.agentCosts.get(entityId);
    return cost ? { ...cost } : null;
  }

  /**
   * Run-wide spend, or null until the first cost report arrives.
   *
   * `usd_per_hour_recent` comes from the last COST_RECENT_WINDOW_TICKS ticks of
   * history and is null until COST_MIN_WINDOW_TICKS of it exist;
   * `usd_per_hour_average` spreads the total over the whole run so far.
   */
  getRunCost(): RunCost | null {
    if (this.agentCosts.size === 0) return null;
    const summed = this.sumCosts();
    return {
      planner_usd: summed.planner_usd,
      converser_usd: summed.converser_usd,
      jev_usd: summed.jev_usd,
      total_usd: summed.total_usd,
      usd_per_hour_recent: this.recentSpendRate(),
      usd_per_hour_average: this.averageSpendRate(summed.total_usd),
    };
  }

  private recentSpendRate(): number | null {
    if (this.costHistory.length < 2) return null;
    const newest = this.costHistory[this.costHistory.length - 1];
    let oldest = newest;
    for (let i = this.costHistory.length - 1; i >= 0; i--) {
      const point = this.costHistory[i];
      if (newest.tickId - point.tickId > COST_RECENT_WINDOW_TICKS) break;
      oldest = point;
    }
    const ticks = newest.tickId - oldest.tickId;
    if (ticks < COST_MIN_WINDOW_TICKS || this.tickDurationMs <= 0) return null;
    return ((newest.totalUsd - oldest.totalUsd) * MS_PER_HOUR) / (ticks * this.tickDurationMs);
  }

  private averageSpendRate(totalUsd: number): number | null {
    const elapsedMs = this.currentTickId * this.tickDurationMs;
    if (elapsedMs <= 0) return null;
    return (totalUsd * MS_PER_HOUR) / elapsedMs;
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
    this.agentCosts.clear();
    this.costOffsets.clear();
    this.costHistory = [];

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
    // A seek sends a snapshot: take its clock, else keep waiting for a tick.
    this.clock = msg.clock ? { ...msg.clock } : null;
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
    if (msg.clock) this.clock = { ...msg.clock };
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
    if (msg.clock) this.clock = { ...msg.clock };

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
    if (update.max_fatigue !== undefined) entity.maxFatigue = update.max_fatigue;
    if (update.fatigue !== undefined) entity.fatigue = update.fatigue;
    if (update.asleep !== undefined) entity.asleep = update.asleep;
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
    const cost = normaliseCost(msg.cost);
    if (cost) {
      this.recordAgentCost(msg.entity_id, cost, msg.tick_id ?? this.currentTickId);
    }
    this.stateUpdateHandler?.();
  }

  /**
   * Store one agent's latest cost and extend the run's spend history.
   *
   * An agent process that restarted reports a total below the one it last
   * reported; the value it reached is moved into a per-entity offset so the
   * run total never goes backwards.
   */
  private recordAgentCost(entityId: string, cost: AgentCost, tickId: number): void {
    const previous = this.agentCosts.get(entityId);
    if (previous && cost.total_usd < previous.total_usd) {
      const offset = this.costOffsets.get(entityId) ?? ZERO_COST;
      this.costOffsets.set(entityId, {
        planner_usd: offset.planner_usd + previous.planner_usd,
        converser_usd: offset.converser_usd + previous.converser_usd,
        jev_usd: offset.jev_usd + previous.jev_usd,
        total_usd: offset.total_usd + previous.total_usd,
        planner_turns: offset.planner_turns + previous.planner_turns,
        jev_calls: offset.jev_calls + previous.jev_calls,
      });
    }
    this.agentCosts.set(entityId, cost);
    this.pushCostPoint(tickId, this.sumCosts().total_usd);
  }

  /**
   * Add the summed total for `tickId` to the history.
   *
   * Replay delivers several statuses for one tick and, after a backwards seek,
   * statuses for ticks already in the history, so the point for a tick is
   * replaced and anything after it dropped.
   */
  private pushCostPoint(tickId: number, totalUsd: number): void {
    while (this.costHistory.length > 0) {
      const last = this.costHistory[this.costHistory.length - 1];
      if (last.tickId < tickId) break;
      this.costHistory.pop();
    }
    this.costHistory.push({ tickId, totalUsd });
    if (this.costHistory.length > COST_HISTORY_SIZE) {
      this.costHistory.splice(0, this.costHistory.length - COST_HISTORY_SIZE);
    }
  }

  /** Every entity's latest cost plus its restart offsets, summed. */
  private sumCosts(): AgentCost {
    const total = { ...ZERO_COST };
    for (const [entityId, cost] of this.agentCosts) {
      const offset = this.costOffsets.get(entityId) ?? ZERO_COST;
      total.planner_usd += cost.planner_usd + offset.planner_usd;
      total.converser_usd += cost.converser_usd + offset.converser_usd;
      total.jev_usd += cost.jev_usd + offset.jev_usd;
      total.total_usd += cost.total_usd + offset.total_usd;
      total.planner_turns += cost.planner_turns + offset.planner_turns;
      total.jev_calls += cost.jev_calls + offset.jev_calls;
    }
    return total;
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
      fatigue: state.fatigue ?? 0,
      maxFatigue: state.max_fatigue ?? DEFAULT_MAX_FATIGUE,
      asleep: state.asleep ?? false,
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
