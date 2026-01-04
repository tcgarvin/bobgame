/**
 * Network module for WebSocket communication with the world server.
 */

export * from './types';
export { WebSocketClient } from './WebSocketClient';
export type { WebSocketClientConfig, ConnectionState, MessageHandler, StateChangeHandler } from './WebSocketClient';
export { WorldState } from './WorldState';
export type { InterpolatedEntity, EntityChangeHandler, TrackedObject, ObjectChangeHandler } from './WorldState';
