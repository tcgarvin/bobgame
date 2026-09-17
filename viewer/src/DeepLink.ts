/**
 * Deep links: the viewer's whole visible state lives in the query string, so
 * an analysis can point at "tick 412, following bram, panel open".
 *
 * See docs/07_replay.md, "Viewer > Deep links", for the parameter table.
 */

export const LIVE_WS_URL = 'ws://localhost:8765';
export const REPLAY_WS_URL = 'ws://localhost:8766';

export interface DeepLinkParams {
  /** Replay mode: the run id to open. '' means live mode. */
  run: string;
  /** Explicit WebSocket URL override, or '' for the mode default. */
  ws: string;
  /** Tick to seek to after the snapshot (replay only). */
  tick: number | null;
  /** Entity to select and follow. */
  entity: string;
  /** Camera tile position; when set, camera follow is off. */
  x: number | null;
  y: number | null;
  zoom: number | null;
  play: boolean;
  speed: number | null;
  /** null when the parameter was absent (the panel defaults to shown). */
  panel: boolean | null;
  /** Object to select and inspect. */
  object: string;
}

/** The parts of the viewer state that the URL mirrors. */
export interface DeepLinkState {
  run: string;
  /** Written back only when the user supplied it. */
  ws: string;
  tick: number | null;
  entity: string;
  object: string;
  /** Camera tile position, or null while the camera is following. */
  x: number | null;
  y: number | null;
  zoom: number | null;
  play: boolean;
  speed: number | null;
  panel: boolean;
}

function readNumber(value: string | null): number | null {
  if (value === null || value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function readFlag(value: string | null): boolean | null {
  if (value === null) return null;
  return value === '1' || value === 'true';
}

export function parseDeepLink(search: string): DeepLinkParams {
  const params = new URLSearchParams(search);
  return {
    run: params.get('run') ?? '',
    ws: params.get('ws') ?? '',
    tick: readNumber(params.get('tick')),
    entity: params.get('entity') ?? '',
    x: readNumber(params.get('x')),
    y: readNumber(params.get('y')),
    zoom: readNumber(params.get('zoom')),
    play: readFlag(params.get('play')) === true,
    speed: readNumber(params.get('speed')),
    panel: readFlag(params.get('panel')),
    object: params.get('object') ?? '',
  };
}

/** The WebSocket URL a set of deep-link parameters asks for. */
export function resolveWsUrl(params: DeepLinkParams): string {
  if (params.ws) return params.ws;
  return params.run ? REPLAY_WS_URL : LIVE_WS_URL;
}

/** Build the query string (including the leading '?') for a viewer state. */
export function buildQuery(state: DeepLinkState): string {
  const params = new URLSearchParams();
  if (state.run) params.set('run', state.run);
  if (state.ws) params.set('ws', state.ws);
  if (state.tick !== null) params.set('tick', String(Math.round(state.tick)));
  if (state.entity) params.set('entity', state.entity);
  if (state.object) params.set('object', state.object);
  if (state.x !== null && state.y !== null) {
    params.set('x', String(Math.round(state.x)));
    params.set('y', String(Math.round(state.y)));
  }
  if (state.zoom !== null) params.set('zoom', state.zoom.toFixed(2));
  if (state.play) params.set('play', '1');
  if (state.speed !== null && state.speed !== 1) params.set('speed', String(state.speed));
  if (!state.panel) params.set('panel', '0');
  const query = params.toString();
  return query ? `?${query}` : '';
}

/** Absolute deep link for a viewer state, based on the current page URL. */
export function buildUrl(state: DeepLinkState): string {
  const { origin, pathname } = window.location;
  return `${origin}${pathname}${buildQuery(state)}`;
}
