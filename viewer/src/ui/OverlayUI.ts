/**
 * HTML overlay UI: entity picker and agent panel.
 *
 * The markup lives in index.html; this module only wires it to the WorldState
 * and re-renders it when the world changes. It deliberately knows nothing
 * about Phaser: camera work is delegated through OverlayCallbacks.
 */

import type { WorldState } from '../network';
import type { AgentStint, StintOptionProbability } from '../network';

export interface OverlayCallbacks {
  /** Called when the user picks an entity (via dropdown or by clicking it). */
  onSelectEntity: (entityId: string) => void;
}

/** Entity types that get a hunger bar and a player-style label. */
const PLAYER_TYPE = 'player';

function requireElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Overlay UI element #${id} is missing from index.html`);
  }
  return element as T;
}

/**
 * Normalize the agent-reported top-probability list, which may arrive as an
 * array of {option, probability} or as an object of option -> probability.
 */
function readTopProbabilities(stint: AgentStint): StintOptionProbability[] {
  const raw = stint.top ?? stint.top_probabilities ?? stint.probabilities;
  if (Array.isArray(raw)) {
    const parsed: StintOptionProbability[] = [];
    for (const item of raw) {
      // The agent logs [option, probability] pairs; objects are also accepted.
      if (Array.isArray(item) && typeof item[0] === 'string' && typeof item[1] === 'number') {
        parsed.push({ option: item[0], probability: item[1] });
      } else if (typeof item === 'object' && item !== null && 'option' in item) {
        parsed.push(item as StintOptionProbability);
      }
    }
    return parsed.slice(0, 3);
  }
  if (typeof raw === 'object' && raw !== null) {
    return Object.entries(raw as Record<string, unknown>)
      .filter((pair): pair is [string, number] => typeof pair[1] === 'number')
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([option, probability]) => ({ option, probability }));
  }
  return [];
}

function formatPercent(value: number | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-';
  return `${Math.round(value * 100)}%`;
}

export class OverlayUI {
  private worldState: WorldState;
  private callbacks: OverlayCallbacks;

  private picker: HTMLSelectElement;
  private followIndicator: HTMLElement;
  private panel: HTMLElement;
  private nameEl: HTMLElement;
  private modeEl: HTMLElement;
  private statsEl: HTMLElement;
  private briefEl: HTMLElement;
  private thoughtEl: HTMLElement;
  private jevEl: HTMLElement;
  private inventoryEl: HTMLElement;
  private logEl: HTMLElement;

  private pickerSignature: string = '';

  constructor(worldState: WorldState, callbacks: OverlayCallbacks) {
    this.worldState = worldState;
    this.callbacks = callbacks;

    this.picker = requireElement<HTMLSelectElement>('entity-picker');
    this.followIndicator = requireElement('follow-indicator');
    this.panel = requireElement('agent-panel');
    this.nameEl = requireElement('ap-name');
    this.modeEl = requireElement('ap-mode');
    this.statsEl = requireElement('ap-stats');
    this.briefEl = requireElement('ap-brief');
    this.thoughtEl = requireElement('ap-thought');
    this.jevEl = requireElement('ap-jev');
    this.inventoryEl = requireElement('ap-inventory');
    this.logEl = requireElement('ap-log');

    this.picker.addEventListener('change', () => {
      this.callbacks.onSelectEntity(this.picker.value);
      // Give the keyboard back to the game so arrow keys pan the camera.
      this.picker.blur();
    });
  }

  /** Reflect the camera-follow state in the header. */
  setFollowing(following: boolean): void {
    this.followIndicator.textContent = following ? 'follow on' : 'follow off';
    this.followIndicator.classList.toggle('off', !following);
  }

  /** Show or hide the agent panel (bound to the P key). */
  togglePanel(): void {
    this.panel.classList.toggle('hidden');
  }

  isPanelVisible(): boolean {
    return !this.panel.classList.contains('hidden');
  }

  /** Rebuild the picker options and the panel contents. */
  refresh(): void {
    this.refreshPicker();
    this.refreshPanel();
  }

  private refreshPicker(): void {
    const entities = this.worldState.getEntities().slice().sort((a, b) => {
      if (a.entityType !== b.entityType) {
        // Players first, then everything else alphabetically by type.
        if (a.entityType === PLAYER_TYPE) return -1;
        if (b.entityType === PLAYER_TYPE) return 1;
        return a.entityType.localeCompare(b.entityType);
      }
      return a.entityId.localeCompare(b.entityId);
    });

    const signature = entities
      .map((e) => `${e.entityId}:${e.entityType}:${e.alive ? 1 : 0}`)
      .join('|');

    if (signature !== this.pickerSignature) {
      this.pickerSignature = signature;
      this.picker.replaceChildren();
      const none = document.createElement('option');
      none.value = '';
      none.textContent = '(none)';
      this.picker.appendChild(none);
      for (const entity of entities) {
        const option = document.createElement('option');
        option.value = entity.entityId;
        const suffix = entity.alive ? '' : ' (dead)';
        option.textContent = `${entity.entityId} [${entity.entityType}]${suffix}`;
        this.picker.appendChild(option);
      }
    }

    const selected = this.worldState.getSelectedEntityId();
    if (this.picker.value !== selected) {
      this.picker.value = selected;
    }
  }

  private refreshPanel(): void {
    const entityId = this.worldState.getSelectedEntityId();
    const entity = this.worldState.getSelectedEntity();

    if (!entityId || !entity) {
      this.nameEl.textContent = 'No entity selected';
      this.modeEl.textContent = 'idle';
      this.modeEl.className = 'mode-badge idle';
      this.statsEl.replaceChildren();
      this.setText(this.briefEl, '');
      this.setText(this.thoughtEl, '');
      this.setText(this.jevEl, '');
      this.setText(this.inventoryEl, '');
      this.logEl.replaceChildren(this.mutedItem('-'));
      return;
    }

    const status = this.worldState.getAgentStatus(entityId);

    this.nameEl.textContent = `${entityId} (${entity.entityType})`;

    const mode = status?.mode ?? (entity.entityType === PLAYER_TYPE ? 'idle' : entity.entityType);
    this.modeEl.textContent = mode;
    this.modeEl.className = `mode-badge ${['planning', 'stint', 'idle'].includes(mode) ? mode : ''}`;

    this.renderStats(entity.health, entity.maxHealth, entity.hunger, entity.maxHunger,
      entity.entityType === PLAYER_TYPE, entity.alive, entity.wielded);

    this.setText(this.briefEl, status?.brief ?? '');
    this.setText(this.thoughtEl, status?.planner_thought ?? '');
    this.renderJev(status?.stint ?? null);
    this.renderInventory(entity.inventory);
    this.renderLog(entityId);
  }

  private renderStats(
    health: number,
    maxHealth: number,
    hunger: number,
    maxHunger: number,
    isPlayer: boolean,
    alive: boolean,
    wielded: string
  ): void {
    const rows: HTMLElement[] = [];
    rows.push(this.barRow('Health', health, maxHealth, '#d35f5f'));
    if (isPlayer) {
      rows.push(this.barRow('Hunger', hunger, maxHunger, '#e0913a'));
    }
    rows.push(this.kvRow('Wielded', wielded || 'nothing'));
    rows.push(this.kvRow('Alive', alive ? 'yes' : 'no'));
    this.statsEl.replaceChildren(...rows);
  }

  private renderJev(stint: AgentStint | null): void {
    if (!stint) {
      this.setText(this.jevEl, '');
      return;
    }

    const parts: HTMLElement[] = [];
    parts.push(this.kvRow('Action', String(stint.action ?? '-')));

    for (const item of readTopProbabilities(stint)) {
      parts.push(this.barRow(item.option, item.probability, 1, '#5f8dd3', formatPercent(item.probability)));
    }

    parts.push(this.kvRow('Eject', formatPercent(stint.eject)));
    parts.push(this.kvRow('Danger', formatPercent(stint.danger)));
    if (typeof stint.ticks_left === 'number') {
      parts.push(this.kvRow('Ticks left', String(stint.ticks_left)));
    }
    const result = stint.intent_result ?? stint.result;
    if (typeof result === 'string' && result) {
      parts.push(this.kvRow('Result', result));
    }
    if (typeof stint.options === 'number') {
      parts.push(this.kvRow('Options', String(stint.options)));
    }

    this.jevEl.classList.remove('muted');
    this.jevEl.replaceChildren(...parts);
  }

  private renderInventory(inventory: Record<string, number>): void {
    const entries = Object.entries(inventory).filter(([, count]) => count > 0);
    if (entries.length === 0) {
      this.setText(this.inventoryEl, '');
      return;
    }
    entries.sort((a, b) => a[0].localeCompare(b[0]));
    this.inventoryEl.classList.remove('muted');
    this.inventoryEl.replaceChildren(
      ...entries.map(([kind, count]) => this.kvRow(kind, String(count)))
    );
  }

  private renderLog(entityId: string): void {
    const log = this.worldState.getEntityLog(entityId);
    if (log.length === 0) {
      this.logEl.replaceChildren(this.mutedItem('-'));
      return;
    }
    const items = log.map((entry) => {
      const li = document.createElement('li');
      if (entry.kind === 'utterance') {
        li.className = 'utterance';
        const channel = entry.channel === 'thought' ? 'thought' : 'say';
        li.textContent = `t${entry.tick} ${channel}: ${entry.text}`;
      } else {
        li.className = entry.success ? '' : 'fail';
        li.textContent = `t${entry.tick} ${entry.text}${entry.success ? '' : ' (failed)'}`;
      }
      return li;
    });
    this.logEl.replaceChildren(...items);
  }

  private setText(element: HTMLElement, value: string): void {
    const text = value.trim();
    element.textContent = text || '-';
    element.classList.toggle('muted', text.length === 0);
  }

  private mutedItem(text: string): HTMLElement {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = text;
    return li;
  }

  private kvRow(label: string, value: string): HTMLElement {
    const row = document.createElement('div');
    row.className = 'kv';
    const left = document.createElement('span');
    left.textContent = label;
    const right = document.createElement('span');
    right.textContent = value;
    row.append(left, right);
    return row;
  }

  private barRow(
    label: string,
    value: number,
    max: number,
    color: string,
    valueText?: string
  ): HTMLElement {
    const row = document.createElement('div');
    row.className = 'bar-row';

    const labelEl = document.createElement('span');
    labelEl.className = 'bar-label';
    labelEl.textContent = label;
    labelEl.title = label;

    const track = document.createElement('div');
    track.className = 'bar-track';
    const fill = document.createElement('div');
    fill.className = 'bar-fill';
    const ratio = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
    fill.style.width = `${(ratio * 100).toFixed(1)}%`;
    fill.style.background = color;
    track.appendChild(fill);

    const valueEl = document.createElement('span');
    valueEl.className = 'bar-value';
    valueEl.textContent = valueText ?? `${Math.round(value)}/${Math.round(max)}`;

    row.append(labelEl, track, valueEl);
    return row;
  }
}
