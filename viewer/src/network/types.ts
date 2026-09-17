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

/**
 * Replay position, sent inside `snapshot.replay` by the replay server only.
 * Its presence is what puts the viewer into replay mode.
 */
export interface ReplayInfo {
  run_id: string;
  first_tick: number;
  last_tick: number;
  tick_id: number;
  playing: boolean;
  speed: number;
}

export interface SnapshotMessage {
  type: 'snapshot';
  tick_id: number;
  world_size: { width: number; height: number };
  chunk_size: number;
  tick_duration_ms: number;
  /** Settlement centre; absent on servers without the settlement feature. */
  settlement?: Position;
  /** Run id of the recording being watched (live server sends it too, and
   * sends null when it runs with --no-record). */
  run_id?: string | null;
  /** Present only in replay mode. */
  replay?: ReplayInfo;
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
  /** The stint brief instruction (status_json) - not the full brief object. */
  brief?: string;
  /** Full option -> probability map from Jev. */
  probabilities?: Record<string, number>;
  confidence?: number;
  ticks_used?: number;
  max_ticks?: number;
  input_tokens?: number;
  /** Added by the agent track; treated as optional here. */
  success_condition?: string;
  notes?: string;
  stint_id?: string;
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
  /** Replay only: the tick this status was recorded at. */
  tick_id?: number;
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

// --- Replay-only messages (see docs/07_replay.md, "Replay server") ---

/** Sent after every replay state change. */
export interface ReplayStatusMessage {
  type: 'replay_status';
  tick_id: number;
  playing: boolean;
  speed: number;
  first_tick: number;
  last_tick: number;
}

export type RunEventKind =
  | 'death'
  | 'respawn'
  | 'wolf_spawned'
  | 'wolf_killed'
  | 'craft'
  | 'place'
  | 'write_note'
  | 'say'
  | 'stint_start'
  | 'planner_turn'
  | 'planner_failed'
  | string;

export interface RunEvent {
  tick_id: number;
  kind: RunEventKind;
  entity_id: string;
  text: string;
}

export interface RunIndexMessage {
  type: 'run_index';
  run_id: string;
  agents: string[];
  events: RunEvent[];
}

/** One entry of the backfilled per-entity log sent after a seek. */
export interface EntityLogEntryMessage {
  tick_id: number;
  entity_id: string;
  kind: 'action' | 'utterance';
  text: string;
  success?: boolean;
  channel?: string;
}

export interface EntityLogMessage {
  type: 'entity_log';
  tick_id: number;
  entries: EntityLogEntryMessage[];
}

/** The brief a stint was started with (from the `stint_start` trace line). */
export interface StintBrief {
  instruction?: string;
  success_condition?: string;
  max_ticks?: number;
  notes?: string;
  check_every?: number;
  travel?: { target?: [number, number]; label?: string } | null;
  [key: string]: unknown;
}

export interface StintStart {
  stint_id?: string;
  entity_id?: string;
  tick?: number;
  brief?: StintBrief;
  [key: string]: unknown;
}

/** A `tool_call` / `tool_result` line inside a planner turn. */
export interface PlannerTurnEvent {
  event: string;
  tool?: string;
  args?: Record<string, unknown>;
  result?: string;
  tick?: number;
  [key: string]: unknown;
}

export interface PlannerTurn {
  turn: number;
  started_tick?: number;
  ended_tick?: number | null;
  prompt?: string;
  thought?: string;
  events?: PlannerTurnEvent[];
}

export interface AgentDetailMessage {
  type: 'agent_detail';
  entity_id: string;
  tick_id: number;
  stint: StintStart | null;
  record: AgentStint | null;
  jev_state: Record<string, unknown> | null;
  criteria: Record<string, string> | null;
  planner_turn: PlannerTurn | null;
  memory?: string;
}

export interface ErrorMessage {
  type: 'error';
  message: string;
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
  | AgentStatusMessage
  | ReplayStatusMessage
  | RunIndexMessage
  | EntityLogMessage
  | AgentDetailMessage
  | ErrorMessage;

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

export function isReplayStatusMessage(msg: ViewerMessage): msg is ReplayStatusMessage {
  return msg.type === 'replay_status';
}

export function isRunIndexMessage(msg: ViewerMessage): msg is RunIndexMessage {
  return msg.type === 'run_index';
}

export function isEntityLogMessage(msg: ViewerMessage): msg is EntityLogMessage {
  return msg.type === 'entity_log';
}

export function isAgentDetailMessage(msg: ViewerMessage): msg is AgentDetailMessage {
  return msg.type === 'agent_detail';
}

export function isErrorMessage(msg: ViewerMessage): msg is ErrorMessage {
  return msg.type === 'error';
}
