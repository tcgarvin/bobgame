/**
 * The replay transport bar along the bottom of the screen.
 *
 * In replay mode it shows the run id, the transport buttons, a tick input, a
 * slider over [first_tick, last_tick], a speed select and the notable-events
 * dropdown built from `run_index`. In live mode everything but the "copy link"
 * button is hidden.
 *
 * The markup lives in index.html; this module wires it to WorldState and the
 * callbacks. It never touches Phaser.
 */

import type { WorldState } from '../network';

export interface ReplayBarCallbacks {
  onSeek: (tickId: number) => void;
  onStep: (delta: number) => void;
  onPlay: (speed: number) => void;
  onPause: () => void;
  onSelectEntity: (entityId: string) => void;
  onCopyLink: () => void;
  /** Raised while a text field has focus, so game keys stay quiet. */
  onTypingChange: (typing: boolean) => void;
}

export const SPEED_OPTIONS = [0.5, 1, 2, 5, 10, 25];

/** Minimum gap between seeks while dragging the slider. */
const SCRUB_THROTTLE_MS = 100;

function requireElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Replay bar element #${id} is missing from index.html`);
  }
  return element as T;
}

export class ReplayBar {
  private worldState: WorldState;
  private callbacks: ReplayBarCallbacks;

  private transport: HTMLElement;
  private runEl: HTMLElement;
  private playButton: HTMLButtonElement;
  private tickInput: HTMLInputElement;
  private slider: HTMLInputElement;
  private speedSelect: HTMLSelectElement;
  private eventsSelect: HTMLSelectElement;
  private boundsEl: HTMLElement;

  private copyButton: HTMLButtonElement;

  private scrubbing: boolean = false;
  private copyFlashTimer: number | null = null;
  private lastScrubMs: number = 0;
  private eventsSignature: string = '';

  constructor(worldState: WorldState, callbacks: ReplayBarCallbacks) {
    this.worldState = worldState;
    this.callbacks = callbacks;

    this.transport = requireElement('rb-transport');
    this.runEl = requireElement('rb-run');
    this.playButton = requireElement<HTMLButtonElement>('rb-play');
    this.tickInput = requireElement<HTMLInputElement>('rb-tick');
    this.slider = requireElement<HTMLInputElement>('rb-slider');
    this.speedSelect = requireElement<HTMLSelectElement>('rb-speed');
    this.eventsSelect = requireElement<HTMLSelectElement>('rb-events');
    this.boundsEl = requireElement('rb-bounds');
    this.copyButton = requireElement<HTMLButtonElement>('rb-copy');

    this.wireButtons();
    this.wireInputs();
  }

  private wireButtons(): void {
    const status = () => this.worldState.getReplayStatus();

    requireElement<HTMLButtonElement>('rb-start').addEventListener('click', () => {
      const s = status();
      if (s) this.callbacks.onSeek(s.first_tick);
    });
    requireElement<HTMLButtonElement>('rb-end').addEventListener('click', () => {
      const s = status();
      if (s) this.callbacks.onSeek(s.last_tick);
    });
    requireElement<HTMLButtonElement>('rb-back10').addEventListener('click', () =>
      this.callbacks.onStep(-10)
    );
    requireElement<HTMLButtonElement>('rb-back1').addEventListener('click', () =>
      this.callbacks.onStep(-1)
    );
    requireElement<HTMLButtonElement>('rb-fwd1').addEventListener('click', () =>
      this.callbacks.onStep(1)
    );
    requireElement<HTMLButtonElement>('rb-fwd10').addEventListener('click', () =>
      this.callbacks.onStep(10)
    );
    this.playButton.addEventListener('click', () => this.togglePlay());
    this.copyButton.addEventListener('click', () => this.callbacks.onCopyLink());
  }

  /** Briefly confirm on the copy button. */
  flashCopy(message: string): void {
    this.copyButton.textContent = message;
    if (this.copyFlashTimer !== null) window.clearTimeout(this.copyFlashTimer);
    this.copyFlashTimer = window.setTimeout(() => {
      this.copyFlashTimer = null;
      this.copyButton.textContent = 'copy link';
    }, 1500);
  }

  private wireInputs(): void {
    for (const speed of SPEED_OPTIONS) {
      const option = document.createElement('option');
      option.value = String(speed);
      option.textContent = `${speed}x`;
      this.speedSelect.appendChild(option);
    }
    this.speedSelect.value = '1';
    this.speedSelect.addEventListener('change', () => {
      const status = this.worldState.getReplayStatus();
      if (status?.playing) {
        this.callbacks.onPlay(this.getSpeed());
      }
      this.speedSelect.blur();
    });

    this.tickInput.addEventListener('focus', () => this.callbacks.onTypingChange(true));
    this.tickInput.addEventListener('blur', () => this.callbacks.onTypingChange(false));
    this.tickInput.addEventListener('change', () => this.commitTickInput());
    this.tickInput.addEventListener('keydown', (event) => {
      event.stopPropagation();
      if (event.key === 'Enter') {
        this.commitTickInput();
        this.tickInput.blur();
      }
    });

    this.slider.addEventListener('pointerdown', () => {
      this.scrubbing = true;
    });
    const endScrub = () => {
      if (!this.scrubbing) return;
      this.scrubbing = false;
      this.callbacks.onSeek(Number(this.slider.value));
    };
    this.slider.addEventListener('pointerup', endScrub);
    this.slider.addEventListener('pointercancel', endScrub);
    this.slider.addEventListener('change', endScrub);
    this.slider.addEventListener('input', () => {
      const now = performance.now();
      if (now - this.lastScrubMs < SCRUB_THROTTLE_MS) return;
      this.lastScrubMs = now;
      this.callbacks.onSeek(Number(this.slider.value));
    });

    this.eventsSelect.addEventListener('change', () => {
      const value = this.eventsSelect.value;
      this.eventsSelect.blur();
      if (!value) return;
      const [tick, entityId] = value.split('|');
      this.callbacks.onSeek(Number(tick));
      if (entityId) this.callbacks.onSelectEntity(entityId);
    });
  }

  private commitTickInput(): void {
    const value = Number(this.tickInput.value);
    if (!Number.isFinite(value)) return;
    this.callbacks.onSeek(value);
  }

  /** The speed currently chosen in the select. */
  getSpeed(): number {
    const value = Number(this.speedSelect.value);
    return Number.isFinite(value) && value > 0 ? value : 1;
  }

  /** Set the speed select without sending anything (used by deep links). */
  setSpeed(speed: number): void {
    const nearest = SPEED_OPTIONS.reduce((best, option) =>
      Math.abs(option - speed) < Math.abs(best - speed) ? option : best
    );
    this.speedSelect.value = String(nearest);
  }

  private togglePlay(): void {
    const status = this.worldState.getReplayStatus();
    if (status?.playing) {
      this.callbacks.onPause();
    } else {
      this.callbacks.onPlay(this.getSpeed());
    }
  }

  /** Space bar handler: same as clicking play/pause. */
  toggle(): void {
    this.togglePlay();
  }

  /** Redraw from the current world state. */
  refresh(): void {
    const replay = this.worldState.isReplay();
    this.transport.classList.toggle('hidden', !replay);
    this.runEl.classList.toggle('hidden', !replay);
    if (!replay) return;

    const status = this.worldState.getReplayStatus();
    this.runEl.textContent = this.worldState.getRunId() || '(run)';
    if (!status) return;

    this.boundsEl.textContent = `${status.first_tick}-${status.last_tick}`;
    this.playButton.textContent = status.playing ? 'pause' : 'play';

    this.slider.min = String(status.first_tick);
    this.slider.max = String(status.last_tick);
    if (!this.scrubbing) {
      this.slider.value = String(status.tick_id);
    }
    if (document.activeElement !== this.tickInput) {
      this.tickInput.value = String(status.tick_id);
    }
    if (document.activeElement !== this.speedSelect && !status.playing) {
      // Keep the select honest when the server clamps or resets the speed.
      this.setSpeed(status.speed);
    }

    this.refreshEvents();
  }

  private refreshEvents(): void {
    const index = this.worldState.getRunIndex();
    const events = index?.events ?? [];
    const signature = `${index?.run_id ?? ''}:${events.length}`;
    if (signature === this.eventsSignature) return;
    this.eventsSignature = signature;

    this.eventsSelect.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = `events (${events.length})`;
    this.eventsSelect.appendChild(placeholder);

    for (const event of events) {
      const option = document.createElement('option');
      option.value = `${event.tick_id}|${event.entity_id ?? ''}`;
      option.textContent = `t${event.tick_id} ${event.kind}: ${event.text}`;
      this.eventsSelect.appendChild(option);
    }
    this.eventsSelect.value = '';
  }
}
