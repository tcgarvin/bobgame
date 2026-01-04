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
}
