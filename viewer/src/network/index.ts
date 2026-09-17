/**
 * Network module for WebSocket communication with the world server.
 */

export * from './types';
export { WebSocketClient } from './WebSocketClient';
export type { WebSocketClientConfig, ConnectionState, MessageHandler, StateChangeHandler } from './WebSocketClient';
export { WorldState, ENTITY_LOG_SIZE } from './WorldState';
export type {
  InterpolatedEntity,
  EntityChangeHandler,
  EntityLogEntry,
  EntityStatsHandler,
  TrackedObject,
  ObjectChangeHandler,
  ChunkChangeHandler,
  UtteranceHandler,
  StateUpdateHandler,
  SelectionHandler,
  ObjectSelectionHandler,
} from './WorldState';
