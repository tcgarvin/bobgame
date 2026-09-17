/**
 * Network message types for WebSocket communication with the world server.
 * These types mirror the JSON messages sent by the Python ViewerWebSocketService.
 */

export interface Position {
  x: number;
  y: number;
}

/**
 * Per-entity stats. All fields are optional so the viewer keeps working
 * against older servers that do not send them yet.
 */
export interface EntityStats {
  health?: number;
  max_health?: number;
  hunger?: number;
  max_hunger?: number;
  wielded?: string;
  alive?: boolean;
  inventory?: Record<string, number>;
}

export interface EntityState extends EntityStats {
  entity_id: string;
  position: Position;
  entity_type: string;
  tags: string[];
}

/**
 * Sent for every entity, every tick, inside `tick_completed.entity_updates`.
 */
export interface EntityUpdate extends EntityStats {
  entity_id: string;
  position: Position;
  entity_type: string;
}

export interface ActionEvent {
  entity_id: string;
  action_type: string;
  success: boolean;
  details: string;
}

export type UtteranceChannel = 'local' | 'thought';

export interface UtteranceEvent {
  speaker_id: string;
  channel: UtteranceChannel | string;
  text: string;
  position: Position;
}

export interface MoveResult {
  entity_id: string;
  from: Position;
  to: Position;
  success: boolean;
}

export interface ObjectState {
  object_id: string;
  position: Position;
  object_type: string;
  state: Record<string, string>;
}

export interface ObjectChange {
  object_id: string;
  field: string;
  old_value: string;
  new_value: string;
}

export interface TerrainChange {
  x: number;
  y: number;
  floor_type: number;
}

// Message types

export interface SnapshotMessage {
  type: 'snapshot';
  tick_id: number;
  world_size: { width: number; height: number };
  chunk_size: number;
  tick_duration_ms: number;
  /** Settlement centre; absent on servers without the settlement feature. */
  settlement?: Position;
}

export interface TickStartedMessage {
  type: 'tick_started';
  tick_id: number;
  tick_start_ms: number;
  deadline_ms: number;
  tick_duration_ms: number;
}

export interface TickCompletedMessage {
  type: 'tick_completed';
  tick_id: number;
  moves: MoveResult[];
  object_changes: ObjectChange[];
  actions_processed: number;
  /** Full stat/position state for every entity. Optional on older servers. */
  entity_updates?: EntityUpdate[];
  actions?: ActionEvent[];
  utterances?: UtteranceEvent[];
  objects_added?: ObjectState[];
  objects_removed?: string[];
}

/**
 * One Jev decision, as reported by the agent. Field names are treated
 * leniently because the stint JSON is produced by the agent track.
 */
export interface StintOptionProbability {
  option: string;
  probability: number;
}

export interface AgentStint {
  tick?: number;
  action?: string;
  /**
   * Top options by probability, highest first. The agent currently sends
   * `[option, probability]` pairs; objects are accepted too.
   */
  top?: Array<StintOptionProbability | [string, number]>;
  eject?: number;
  danger?: number;
  options?: number;
  latency_ms?: number;
  intent_result?: string;
  result?: string;
  ticks_left?: number;
  [key: string]: unknown;
}

export type AgentMode = 'planning' | 'stint' | 'idle' | string;

export interface AgentStatusMessage {
  type: 'agent_status';
  entity_id: string;
  mode: AgentMode;
  brief: string;
  planner_thought: string;
  stint: AgentStint | null;
}

export interface EntitySpawnedMessage {
  type: 'entity_spawned';
  tick_id: number;
  entity: EntityState;
}

export interface EntityDespawnedMessage {
  type: 'entity_despawned';
  tick_id: number;
  entity_id: string;
}

export interface ChunkDataMessage {
  type: 'chunk_data';
  chunk_x: number;
  chunk_y: number;
  version: number;
  terrain: string; // Base64-encoded RLE terrain data
  entities: EntityState[];
  objects: ObjectState[];
}

export interface TerrainUpdateMessage {
  type: 'terrain_update';
  chunk_x: number;
  chunk_y: number;
  version: number;
  changes: TerrainChange[];
}

export interface ChunkUnloadMessage {
  type: 'chunk_unload';
  chunk_x: number;
  chunk_y: number;
}

export type ViewerMessage =
  | SnapshotMessage
  | TickStartedMessage
  | TickCompletedMessage
  | EntitySpawnedMessage
  | EntityDespawnedMessage
  | ChunkDataMessage
  | TerrainUpdateMessage
  | ChunkUnloadMessage
  | AgentStatusMessage;

/**
 * Type guard for checking message types
 */
export function isSnapshotMessage(msg: ViewerMessage): msg is SnapshotMessage {
  return msg.type === 'snapshot';
}

export function isTickStartedMessage(msg: ViewerMessage): msg is TickStartedMessage {
  return msg.type === 'tick_started';
}

export function isTickCompletedMessage(msg: ViewerMessage): msg is TickCompletedMessage {
  return msg.type === 'tick_completed';
}

export function isEntitySpawnedMessage(msg: ViewerMessage): msg is EntitySpawnedMessage {
  return msg.type === 'entity_spawned';
}

export function isEntityDespawnedMessage(msg: ViewerMessage): msg is EntityDespawnedMessage {
  return msg.type === 'entity_despawned';
}

export function isChunkDataMessage(msg: ViewerMessage): msg is ChunkDataMessage {
  return msg.type === 'chunk_data';
}

export function isTerrainUpdateMessage(msg: ViewerMessage): msg is TerrainUpdateMessage {
  return msg.type === 'terrain_update';
}

export function isChunkUnloadMessage(msg: ViewerMessage): msg is ChunkUnloadMessage {
  return msg.type === 'chunk_unload';
}

export function isAgentStatusMessage(msg: ViewerMessage): msg is AgentStatusMessage {
  return msg.type === 'agent_status';
}
