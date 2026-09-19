/**
 * WebSocket client for connecting to the world server's viewer service.
 * Handles connection, reconnection, and message parsing.
 */

import type { ViewerMessage } from './types';

export interface WebSocketClientConfig {
  url: string;
  reconnectDelayMs: number;
  maxReconnectAttempts: number;
}

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';

export type MessageHandler = (message: ViewerMessage) => void;
export type StateChangeHandler = (state: ConnectionState) => void;

const DEFAULT_CONFIG: WebSocketClientConfig = {
  url: 'ws://localhost:8765',
  reconnectDelayMs: 1000,
  maxReconnectAttempts: 10,
};

export class WebSocketClient {
  private ws: WebSocket | null = null;
  private config: WebSocketClientConfig;
  private messageHandler: MessageHandler;
  private stateChangeHandler: StateChangeHandler | null = null;
  private reconnectAttempts: number = 0;
  private reconnectTimeout: number | null = null;
  private state: ConnectionState = 'disconnected';
  private intentionalClose: boolean = false;

  constructor(
    messageHandler: MessageHandler,
    config: Partial<WebSocketClientConfig> = {}
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.messageHandler = messageHandler;
  }

  /**
   * Set a handler for connection state changes
   */
  onStateChange(handler: StateChangeHandler): void {
    this.stateChangeHandler = handler;
  }

  /**
   * Get current connection state
   */
  getState(): ConnectionState {
    return this.state;
  }

  /**
   * Connect to the WebSocket server
   */
  connect(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      console.log('WebSocket already connected');
      return;
    }

    this.intentionalClose = false;
    this.setState('connecting');
    console.log(`Connecting to ${this.config.url}...`);

    try {
      this.ws = new WebSocket(this.config.url);
      this.ws.onopen = this.onOpen.bind(this);
      this.ws.onmessage = this.onMessage.bind(this);
      this.ws.onclose = this.onClose.bind(this);
      this.ws.onerror = this.onError.bind(this);
    } catch (error) {
      console.error('Failed to create WebSocket:', error);
      this.scheduleReconnect();
    }
  }

  /**
   * Disconnect from the WebSocket server
   */
  disconnect(): void {
    this.intentionalClose = true;
    this.cancelReconnect();

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    this.setState('disconnected');
  }

  private setState(newState: ConnectionState): void {
    if (this.state !== newState) {
      this.state = newState;
      if (this.stateChangeHandler) {
        this.stateChangeHandler(newState);
      }
    }
  }

  private onOpen(): void {
    console.log('WebSocket connected');
    this.reconnectAttempts = 0;
    this.setState('connected');
  }

  private onMessage(event: MessageEvent): void {
    try {
      const message = JSON.parse(event.data) as ViewerMessage;
      this.messageHandler(message);
    } catch (error) {
      console.error('Failed to parse WebSocket message:', error, event.data);
    }
  }

  private onClose(event: CloseEvent): void {
    console.log(`WebSocket closed: code=${event.code}, reason=${event.reason}`);
    this.ws = null;

    if (!this.intentionalClose) {
      this.scheduleReconnect();
    } else {
      this.setState('disconnected');
    }
  }

  private onError(event: Event): void {
    console.error('WebSocket error:', event);
    // The close event will follow, so we don't need to do much here
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.config.maxReconnectAttempts) {
      console.error(
        `Max reconnect attempts (${this.config.maxReconnectAttempts}) reached. Giving up.`
      );
      this.setState('disconnected');
      return;
    }

    this.reconnectAttempts++;
    this.setState('reconnecting');

    const delay = this.config.reconnectDelayMs * Math.pow(1.5, this.reconnectAttempts - 1);
    console.log(
      `Reconnecting in ${Math.round(delay)}ms (attempt ${this.reconnectAttempts}/${this.config.maxReconnectAttempts})...`
    );

    this.reconnectTimeout = window.setTimeout(() => {
      this.reconnectTimeout = null;
      this.connect();
    }, delay);
  }

  private cancelReconnect(): void {
    if (this.reconnectTimeout !== null) {
      window.clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
  }

  /**
   * Send a message to the server.
   */
  private send(message: object): boolean {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
      return true;
    }
    return false;
  }

  /**
   * Subscribe to specific chunks.
   */
  subscribeChunks(chunks: Array<[number, number]>): boolean {
    return this.send({
      type: 'subscribe_chunks',
      chunks: chunks,
    });
  }

  /**
   * Subscribe to chunks covering a viewport region.
   */
  subscribeViewport(x: number, y: number, width: number, height: number): boolean {
    return this.send({
      type: 'subscribe_viewport',
      viewport: { x, y, width, height },
    });
  }

  // --- Replay control (ignored by the live world server) ---

  /** Ask the replay server to load a run and position at its first tick. */
  openRun(runId: string): boolean {
    return this.send({ type: 'open_run', run_id: runId });
  }

  /** Jump to an absolute tick. The server clamps to [first_tick, last_tick]. */
  seek(tickId: number): boolean {
    return this.send({ type: 'seek', tick_id: Math.round(tickId) });
  }

  /** Jump by a relative number of ticks (negative allowed). */
  step(delta: number): boolean {
    return this.send({ type: 'step', delta: Math.round(delta) });
  }

  /** Start playback at `speed` ticks per tick_duration. */
  play(speed: number = 1): boolean {
    return this.send({ type: 'play', speed });
  }

  /** Stop playback. */
  pause(): boolean {
    return this.send({ type: 'pause' });
  }

  /**
   * Request the full agent detail for one entity at one tick.
   *
   * `runId` asks the replay server about a run this connection has not opened,
   * which is how the live viewer reads agent detail (docs/07_replay.md).
   */
  getAgentDetail(entityId: string, tickId: number, runId: string = ''): boolean {
    return this.send({
      type: 'get_agent_detail',
      entity_id: entityId,
      tick_id: Math.round(tickId),
      ...(runId ? { run_id: runId } : {}),
    });
  }

  /** Request the run index (agents + notable events). */
  getRunIndex(): boolean {
    return this.send({ type: 'get_run_index' });
  }
}
