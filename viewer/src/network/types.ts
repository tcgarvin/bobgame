/**
 * Network message types for WebSocket communication with the world server.
 * These types mirror the JSON messages sent by the Python ViewerWebSocketService.
 */

export interface Position {
  x: number;
  y: number;
}

export interface EntityState {
  entity_id: string;
  position: Position;
  entity_type: string;
  tags: string[];
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
  | ChunkUnloadMessage;

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
