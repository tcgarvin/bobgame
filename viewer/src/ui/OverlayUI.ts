/**
 * HTML overlay UI: entity picker and agent panel.
 *
 * The markup lives in index.html; this module only wires it to the WorldState
 * and re-renders it when the world changes. It deliberately knows nothing
 * about Phaser: camera work is delegated through OverlayCallbacks.
 *
 * In replay mode the panel also shows the `agent_detail` sections ("what Jev
 * saw", the planner turn and the planner's memory), which are requested
 * through `onRequestAgentDetail` whenever the selection or the tick changes.
 */

import type { WorldState } from '../network';
import type {
  AgentDetailMessage,
  AgentStint,
  PlannerTurnEvent,
  StintBrief,
  StintOptionProbability,
} from '../network';

export interface OverlayCallbacks {
  /** Called when the user picks an entity (via dropdown or by clicking it). */
  onSelectEntity: (entityId: string) => void;
  /** Replay only: ask the server for the full detail at this entity/tick. */
  onRequestAgentDetail: (entityId: string, tickId: number) => void;
}

/** Entity types that get a hunger bar and a player-style label. */
const PLAYER_TYPE = 'player';

/** How long to wait before re-requesting detail while playback runs. */
const DETAIL_DEBOUNCE_MS = 250;

function requireElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Overlay UI element #${id} is missing from index.html`);
  }
  return element as T;
}

/**
 * Normalize the agent-reported probability map, which may arrive as an object
 * of option -> probability, an array of {option, probability}, or the
 * `[option, probability]` pairs the stint log writes. Sorted highest first.
 */
export function readProbabilities(stint: AgentStint): StintOptionProbability[] {
  const raw = stint.probabilities ?? stint.top ?? stint.top_probabilities;
  let parsed: StintOptionProbability[] = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      if (Array.isArray(item) && typeof item[0] === 'string' && typeof item[1] === 'number') {
        parsed.push({ option: item[0], probability: item[1] });
      } else if (typeof item === 'object' && item !== null && 'option' in item) {
        parsed.push(item as StintOptionProbability);
      }
    }
  } else if (typeof raw === 'object' && raw !== null) {
    parsed = Object.entries(raw as Record<string, unknown>)
      .filter((pair): pair is [string, number] => typeof pair[1] === 'number')
      .map(([option, probability]) => ({ option, probability }));
  }
  return parsed.sort((a, b) => b.probability - a.probability);
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
  private briefMetaEl: HTMLElement;
  private thoughtEl: HTMLElement;
  private jevEl: HTMLElement;
  private inventoryEl: HTMLElement;
  private logEl: HTMLElement;
  private replayEl: HTMLElement;
  private jevStateEl: HTMLElement;
  private plannerEl: HTMLElement;
  private memoryEl: HTMLElement;

  private pickerSignature: string = '';
  private requestedDetailKey: string = '';
  private detailTimer: number | null = null;

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
    this.briefMetaEl = requireElement('ap-brief-meta');
    this.thoughtEl = requireElement('ap-thought');
    this.jevEl = requireElement('ap-jev');
    this.inventoryEl = requireElement('ap-inventory');
    this.logEl = requireElement('ap-log');
    this.replayEl = requireElement('ap-replay');
    this.jevStateEl = requireElement('ap-jev-state');
    this.plannerEl = requireElement('ap-planner');
    this.memoryEl = requireElement('ap-memory');

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

  setPanelVisible(visible: boolean): void {
    this.panel.classList.toggle('hidden', !visible);
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
      this.briefMetaEl.replaceChildren();
      this.setText(this.thoughtEl, '');
      this.setText(this.jevEl, '');
      this.setText(this.inventoryEl, '');
      this.logEl.replaceChildren(this.mutedItem('-'));
      this.replayEl.classList.add('hidden');
      return;
    }

    const status = this.worldState.getAgentStatus(entityId);
    const detail = this.requestDetail(entityId);

    this.nameEl.textContent = `${entityId} (${entity.entityType})`;

    const mode = status?.mode ?? (entity.entityType === PLAYER_TYPE ? 'idle' : entity.entityType);
    this.modeEl.textContent = mode;
    this.modeEl.className = `mode-badge ${['planning', 'stint', 'idle'].includes(mode) ? mode : ''}`;

    this.renderStats(entity.health, entity.maxHealth, entity.hunger, entity.maxHunger,
      entity.entityType === PLAYER_TYPE, entity.alive, entity.wielded);

    const stint = detail?.record ?? status?.stint ?? null;
    this.renderBrief(status?.brief ?? '', stint, detail);
    this.setText(this.thoughtEl, status?.planner_thought ?? detail?.planner_turn?.thought ?? '');
    this.renderJev(stint);
    this.renderInventory(entity.inventory);
    this.renderLog(entityId);
    this.renderReplaySections(detail);
  }

  /**
   * Ask the server for the detail at the current entity and tick, at most once
   * per entity/tick pair, debounced while playback is running. Returns the
   * cached detail for the current tick if it has already arrived.
   */
  private requestDetail(entityId: string): AgentDetailMessage | null {
    if (!this.worldState.isReplay()) return null;

    const status = this.worldState.getReplayStatus();
    const tickId = status ? status.tick_id : this.worldState.getCurrentTick();
    const key = `${entityId}@${tickId}`;

    const cached = this.worldState.getAgentDetail(entityId, tickId);
    if (cached) return cached;

    if (this.requestedDetailKey === key) return null;

    if (status?.playing) {
      if (this.detailTimer !== null) return null;
      this.detailTimer = window.setTimeout(() => {
        this.detailTimer = null;
        const now = this.worldState.getReplayStatus();
        const latestTick = now ? now.tick_id : tickId;
        const latestId = this.worldState.getSelectedEntityId();
        if (!latestId) return;
        this.requestedDetailKey = `${latestId}@${latestTick}`;
        this.callbacks.onRequestAgentDetail(latestId, latestTick);
      }, DETAIL_DEBOUNCE_MS);
      return null;
    }

    this.requestedDetailKey = key;
    this.callbacks.onRequestAgentDetail(entityId, tickId);
    return null;
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

  /** The brief: instruction, success condition, ticks used of max, notes. */
  private renderBrief(
    statusBrief: string,
    stint: AgentStint | null,
    detail: AgentDetailMessage | null
  ): void {
    const brief: StintBrief = detail?.stint?.brief ?? {};
    const fallbackBrief = typeof stint?.brief === 'string' ? stint.brief : '';
    this.setText(this.briefEl, brief.instruction || statusBrief || fallbackBrief);

    const rows: HTMLElement[] = [];
    const success = brief.success_condition ?? stint?.success_condition;
    if (typeof success === 'string' && success) {
      rows.push(this.kvRow('Success when', success));
    }
    const maxTicks = stint?.max_ticks ?? brief.max_ticks;
    if (typeof stint?.ticks_used === 'number') {
      rows.push(this.kvRow('Ticks used', `${stint.ticks_used}/${maxTicks ?? '?'}`));
    } else if (typeof maxTicks === 'number') {
      rows.push(this.kvRow('Max ticks', String(maxTicks)));
    }
    const notes = brief.notes ?? stint?.notes;
    if (typeof notes === 'string' && notes) {
      rows.push(this.kvRow('Notes', notes));
    }
    const travel = brief.travel;
    if (travel && Array.isArray(travel.target)) {
      const label = travel.label ? ` (${travel.label})` : '';
      rows.push(this.kvRow('Travel', `(${travel.target[0]}, ${travel.target[1]})${label}`));
    }
    this.briefMetaEl.replaceChildren(...rows);
  }

  private renderJev(stint: AgentStint | null): void {
    if (!stint) {
      this.setText(this.jevEl, '');
      return;
    }

    const parts: HTMLElement[] = [];
    const chosen = typeof stint.action === 'string' ? stint.action : '';
    parts.push(this.kvRow('Action', chosen || '-'));

    for (const item of readProbabilities(stint)) {
      const row = this.barRow(
        item.option,
        item.probability,
        1,
        item.option === chosen ? '#7fd67f' : '#5f8dd3',
        formatPercent(item.probability)
      );
      if (item.option === chosen) row.classList.add('chosen');
      parts.push(row);
    }

    if (typeof stint.confidence === 'number') {
      parts.push(this.kvRow('Confidence', formatPercent(stint.confidence)));
    }
    parts.push(this.kvRow('Eject', formatPercent(stint.eject)));
    parts.push(this.kvRow('Danger', formatPercent(stint.danger)));
    if (typeof stint.latency_ms === 'number') {
      parts.push(this.kvRow('Latency', `${Math.round(stint.latency_ms)} ms`));
    }
    if (typeof stint.input_tokens === 'number') {
      parts.push(this.kvRow('Input tokens', String(stint.input_tokens)));
    }
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

  /** The replay-only sections, hidden entirely in live mode. */
  private renderReplaySections(detail: AgentDetailMessage | null): void {
    if (!this.worldState.isReplay()) {
      this.replayEl.classList.add('hidden');
      return;
    }
    this.replayEl.classList.remove('hidden');

    if (!detail) {
      this.jevStateEl.replaceChildren(this.mutedDiv('loading...'));
      this.plannerEl.replaceChildren(this.mutedDiv('loading...'));
      this.memoryEl.replaceChildren(this.mutedDiv('loading...'));
      return;
    }

    // What Jev saw.
    const jevParts: HTMLElement[] = [];
    if (detail.jev_state) {
      jevParts.push(this.pre(JSON.stringify(detail.jev_state, null, 2)));
    } else {
      jevParts.push(this.mutedDiv('no Jev call at this tick'));
    }
    if (detail.criteria && Object.keys(detail.criteria).length > 0) {
      jevParts.push(this.label('Criteria'));
      for (const [option, description] of Object.entries(detail.criteria)) {
        jevParts.push(this.kvRow(option, description));
      }
    }
    this.jevStateEl.replaceChildren(...jevParts);

    // Planner turn.
    this.plannerEl.replaceChildren(...this.plannerParts(detail));

    // Memory.
    const memory = detail.memory ?? '';
    this.memoryEl.replaceChildren(
      memory.trim() ? this.pre(memory) : this.mutedDiv('no memory notes')
    );
  }

  private plannerParts(detail: AgentDetailMessage): HTMLElement[] {
    const turn = detail.planner_turn;
    if (!turn) return [this.mutedDiv('no planner turn at this tick')];

    const parts: HTMLElement[] = [];
    const range = turn.ended_tick == null ? 'in progress' : `ended t${turn.ended_tick}`;
    parts.push(this.kvRow(`Turn ${turn.turn}`, `t${turn.started_tick ?? '?'} - ${range}`));

    if (turn.prompt) {
      parts.push(this.details('Prompt', [this.pre(turn.prompt)]));
    }

    for (const event of turn.events ?? []) {
      parts.push(this.plannerEventPart(event));
    }

    if (turn.thought) {
      parts.push(this.label('Reflection'));
      parts.push(this.pre(turn.thought));
    }
    return parts;
  }

  private plannerEventPart(event: PlannerTurnEvent): HTMLElement {
    if (event.event === 'tool_call') {
      const args = event.args ? JSON.stringify(event.args, null, 2) : '{}';
      return this.details(`call ${event.tool ?? '?'}`, [this.pre(args)]);
    }
    if (event.event === 'tool_result') {
      return this.details(`result ${event.tool ?? '?'}`, [this.pre(event.result ?? '')]);
    }
    return this.mutedDiv(`${event.event}${event.tool ? ` ${event.tool}` : ''}`);
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

  private mutedDiv(text: string): HTMLElement {
    const div = document.createElement('div');
    div.className = 'muted';
    div.textContent = text;
    return div;
  }

  private label(text: string): HTMLElement {
    const div = document.createElement('div');
    div.className = 'label';
    div.textContent = text;
    return div;
  }

  private pre(text: string): HTMLElement {
    const pre = document.createElement('pre');
    pre.className = 'json';
    pre.textContent = text;
    return pre;
  }

  private details(summaryText: string, children: HTMLElement[]): HTMLElement {
    const details = document.createElement('details');
    details.className = 'sub';
    const summary = document.createElement('summary');
    summary.textContent = summaryText;
    details.append(summary, ...children);
    return details;
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
