/**
 * HTML overlay UI: entity picker and agent panel.
 *
 * The markup lives in index.html; this module only wires it to the WorldState
 * and re-renders it when the world changes. It deliberately knows nothing
 * about Phaser: camera work is delegated through OverlayCallbacks, and the
 * wielded-tool icon is drawn from the sprite index handed in by GameScene.
 *
 * The panel is written for someone watching a settler, not for someone reading
 * a trace: who this is, how it is doing, what it is doing now, what it is
 * thinking, what it has written in its journal. Every number behind that lives
 * in the collapsed "Details" block at the bottom, fed by `agent_detail`, which
 * is requested through `onRequestAgentDetail` whenever the selection or the
 * tick changes (in replay and in live mode alike).
 */

import { TIRED_FATIGUE } from '../generated/rules';
import type { InterpolatedEntity, TrackedObject, WorldState } from '../network';
import type {
  AgentCost,
  AgentDetailMessage,
  AgentStint,
  PlannerTurnEvent,
  StintBrief,
  StintOptionProbability,
} from '../network';
import type { SpriteIndex } from '../sprites/SpriteIndex';
import {
  CONVERSATION_TYPE,
  parseConversationParticipants,
  parseConversationTranscript,
} from '../conversation';
import { actionInWords } from './ActionWords';
import { barRow, details, kvRow, label, mutedDiv, mutedItem, noteLine, pre, textDiv } from './dom';
import { createItemIcon } from './ItemIcon';
import { journalParts, journalRawText } from './JournalView';

/** Modes with a distinct badge color (index.html `.mode-badge.*`). */
const KNOWN_MODES = ['planning', 'stint', 'idle', 'reflex', 'conversation'];

export interface OverlayCallbacks {
  /** Called when the user picks an entity (via dropdown or by clicking it). */
  onSelectEntity: (entityId: string) => void;
  /** Ask the detail server for the full detail at this entity/tick. */
  onRequestAgentDetail: (entityId: string, tickId: number) => void;
}

/** Entity types that get a food bar and a player-style label. */
const PLAYER_TYPE = 'player';

/**
 * Fatigue thresholds (docs/10_metal_and_sleep.md, section 4): under
 * TIRED_FATIGUE fresh, then tired (halved work, weaker hits, no regen), at max
 * exhausted.
 */

/** The word for a fatigue level, shown next to the number. */
export function fatigueState(fatigue: number, maxFatigue: number): string {
  if (maxFatigue > 0 && fatigue >= maxFatigue) return 'exhausted';
  return fatigue >= TIRED_FATIGUE ? 'tired' : 'fresh';
}

/**
 * Dollars the way the cost report prints them (docs/11_cost_accounting.md):
 * four decimals under $1, two above.
 */
export function formatUsd(usd: number): string {
  const decimals = Math.abs(usd) < 1 ? 4 : 2;
  return `$${usd.toFixed(decimals)}`;
}

/** `$0.0074 · planner $0.0049 · Jev $0.0025 (1 turn, 60 Jev calls)`. */
export function formatAgentCost(cost: AgentCost): string {
  const parts = [formatUsd(cost.total_usd), `planner ${formatUsd(cost.planner_usd)}`];
  if (cost.converser_usd > 0) parts.push(`converser ${formatUsd(cost.converser_usd)}`);
  parts.push(`Jev ${formatUsd(cost.jev_usd)}`);
  const turns = `${cost.planner_turns} turn${cost.planner_turns === 1 ? '' : 's'}`;
  return `${parts.join(' · ')} (${turns}, ${cost.jev_calls} Jev calls)`;
}

/** How long to wait before re-requesting detail while the tick keeps moving. */
const DETAIL_DEBOUNCE_MS = 250;

/** The one notable state worth a chip next to the mode badge, or ''. */
export function statusChip(entity: InterpolatedEntity): { text: string; kind: string } | null {
  if (!entity.alive) return { text: 'dead', kind: 'dead' };
  if (entity.asleep) {
    const collapsed =
      entity.maxFatigue > 0 && entity.fatigue >= entity.maxFatigue;
    return collapsed ? { text: 'collapsed', kind: '' } : { text: 'asleep', kind: '' };
  }
  return null;
}

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
  private spriteIndex?: SpriteIndex;

  private picker: HTMLSelectElement;
  private followIndicator: HTMLElement;
  private clockEl: HTMLElement;
  private costEl: HTMLElement;
  private panel: HTMLElement;
  private nameEl: HTMLElement;
  private wieldedEl: HTMLElement;
  private modeEl: HTMLElement;
  private statusEl: HTMLElement;
  private statsEl: HTMLElement;
  private briefEl: HTMLElement;
  private briefMetaEl: HTMLElement;
  private thoughtEl: HTMLElement;
  private jevEl: HTMLElement;
  private journalEl: HTMLElement;
  private inventoryEl: HTMLElement;
  private logEl: HTMLElement;
  private debugEl: HTMLElement;
  private jevStateEl: HTMLElement;
  private plannerEl: HTMLElement;
  private memoryEl: HTMLElement;
  private conversationEl: HTMLElement;
  private conversationBodyEl: HTMLElement;

  private pickerSignature: string = '';
  private requestedDetailKey: string = '';
  private detailTimer: number | null = null;

  constructor(worldState: WorldState, callbacks: OverlayCallbacks, spriteIndex?: SpriteIndex) {
    this.worldState = worldState;
    this.callbacks = callbacks;
    this.spriteIndex = spriteIndex;

    this.picker = requireElement<HTMLSelectElement>('entity-picker');
    this.followIndicator = requireElement('follow-indicator');
    this.clockEl = requireElement('clock-readout');
    this.costEl = requireElement('cost-readout');
    this.panel = requireElement('agent-panel');
    this.nameEl = requireElement('ap-name');
    this.wieldedEl = requireElement('ap-wielded');
    this.modeEl = requireElement('ap-mode');
    this.statusEl = requireElement('ap-status');
    this.statsEl = requireElement('ap-stats');
    this.briefEl = requireElement('ap-brief');
    this.briefMetaEl = requireElement('ap-brief-meta');
    this.thoughtEl = requireElement('ap-thought');
    this.jevEl = requireElement('ap-jev');
    this.journalEl = requireElement('ap-journal');
    this.inventoryEl = requireElement('ap-inventory');
    this.logEl = requireElement('ap-log');
    this.debugEl = requireElement('ap-debug');
    this.jevStateEl = requireElement('ap-jev-state');
    this.plannerEl = requireElement('ap-planner');
    this.memoryEl = requireElement('ap-memory');
    this.conversationEl = requireElement('ap-conversation');
    this.conversationBodyEl = requireElement('ap-conversation-body');

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
    this.refreshClock();
    this.refreshCost();
    this.refreshPicker();
    this.refreshPanel();
  }

  /**
   * `day 2 · 143/300 · night`, straight from the latest tick's clock so it is
   * correct after a replay seek as well.
   */
  private refreshClock(): void {
    const clock = this.worldState.getClock();
    if (!clock) {
      this.clockEl.textContent = '-';
      this.clockEl.classList.add('muted');
      this.clockEl.classList.remove('night');
      return;
    }
    this.clockEl.classList.remove('muted');
    this.clockEl.classList.toggle('night', clock.night);
    this.clockEl.textContent = `day ${clock.day} · ${clock.tick_of_day}/${clock.day_length} · ${
      clock.night ? 'night' : 'day'
    }`;
  }

  /**
   * `$0.11 · $1.36/h · planner $0.03 · Jev $0.08` for the whole run, muted
   * until the first cost report arrives. The converser is shown only when it
   * has spent something, to keep the line short.
   */
  private refreshCost(): void {
    const cost = this.worldState.getRunCost();
    if (!cost) {
      this.costEl.textContent = '-';
      this.costEl.classList.add('muted');
      return;
    }
    this.costEl.classList.remove('muted');
    const parts = [formatUsd(cost.total_usd)];
    const rate = cost.usd_per_hour_recent ?? cost.usd_per_hour_average;
    if (rate !== null) parts.push(`${formatUsd(rate)}/h`);
    parts.push(`planner ${formatUsd(cost.planner_usd)}`);
    if (cost.converser_usd > 0) parts.push(`converser ${formatUsd(cost.converser_usd)}`);
    parts.push(`Jev ${formatUsd(cost.jev_usd)}`);
    this.costEl.textContent = parts.join(' · ');
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
      this.clearPanel();
      return;
    }

    const status = this.worldState.getAgentStatus(entityId);
    const detail = this.requestDetail(entityId);
    const isPlayer = entity.entityType === PLAYER_TYPE;
    const stint = detail?.record ?? status?.stint ?? null;

    this.renderHeader(entityId, entity, status?.mode ?? (isPlayer ? 'idle' : entity.entityType));
    this.renderStats(entity, isPlayer);
    this.renderDoingNow(status?.brief ?? '', stint, detail);
    this.renderConversation(entityId);
    this.setText(this.thoughtEl, status?.planner_thought ?? detail?.planner_turn?.thought ?? '');
    this.renderJournal(detail);
    this.renderInventory(entity.inventory);
    this.renderLog(entityId);
    this.renderDetails(entity, stint, detail);
  }

  /** Reset every section to its empty state (nothing selected). */
  private clearPanel(): void {
    this.nameEl.textContent = 'No entity selected';
    this.wieldedEl.classList.add('hidden');
    this.wieldedEl.replaceChildren();
    this.modeEl.textContent = 'idle';
    this.modeEl.className = 'mode-badge idle';
    this.statusEl.classList.add('hidden');
    this.statsEl.replaceChildren();
    this.setText(this.briefEl, '');
    this.jevEl.replaceChildren();
    this.briefMetaEl.replaceChildren();
    this.setText(this.thoughtEl, '');
    this.journalEl.replaceChildren(mutedDiv('no journal yet'));
    this.setText(this.inventoryEl, '');
    this.logEl.replaceChildren(mutedItem('-'));
    this.debugEl.replaceChildren();
    this.jevStateEl.replaceChildren();
    this.plannerEl.replaceChildren();
    this.memoryEl.replaceChildren();
    this.conversationEl.classList.add('hidden');
  }

  /** Name, the wielded tool's icon and tier, the mode badge and a status chip. */
  private renderHeader(entityId: string, entity: InterpolatedEntity, mode: string): void {
    this.nameEl.textContent = entityId;

    const icon = createItemIcon(this.spriteIndex, entity.wielded);
    if (icon) {
      const children: HTMLElement[] = [icon.element];
      if (icon.tier) {
        const tier = document.createElement('span');
        tier.className = 'tier';
        tier.textContent = icon.tier;
        children.unshift(tier);
      }
      this.wieldedEl.replaceChildren(...children);
      this.wieldedEl.classList.remove('hidden');
    } else {
      this.wieldedEl.replaceChildren();
      this.wieldedEl.classList.add('hidden');
    }

    this.modeEl.textContent = mode;
    this.modeEl.className = `mode-badge ${KNOWN_MODES.includes(mode) ? mode : ''}`;

    const chip = statusChip(entity);
    if (chip) {
      this.statusEl.textContent = chip.text;
      this.statusEl.className = `status-chip ${chip.kind}`;
    } else {
      this.statusEl.className = 'status-chip hidden';
      this.statusEl.textContent = '';
    }
  }

  /** Find the conversation object, if any, this entity currently sits in. */
  private findConversation(entityId: string): TrackedObject | undefined {
    for (const obj of this.worldState.getObjects()) {
      if (obj.objectType !== CONVERSATION_TYPE) continue;
      if (parseConversationParticipants(obj.state.participants).includes(entityId)) {
        return obj;
      }
    }
    return undefined;
  }

  /** Show the conversation's participants, speaker and transcript when the selected entity is seated in one. */
  private renderConversation(entityId: string): void {
    const conversation = this.findConversation(entityId);
    if (!conversation) {
      this.conversationEl.classList.add('hidden');
      this.conversationBodyEl.replaceChildren();
      return;
    }
    this.conversationEl.classList.remove('hidden');

    const participants = parseConversationParticipants(conversation.state.participants);
    const speaker = conversation.state.speaker ?? '';
    const transcript = parseConversationTranscript(conversation.state.transcript);

    const rows: HTMLElement[] = [
      kvRow('Participants', participants.join(', ') || '-'),
      kvRow('Speaker', speaker || '-'),
    ];

    if (transcript.length === 0) {
      rows.push(mutedDiv('no lines yet'));
    } else {
      for (const line of transcript) {
        rows.push(textDiv(`t${line.tick} ${line.speaker}: ${line.text}`));
      }
    }
    this.conversationBodyEl.replaceChildren(...rows);
  }

  /**
   * Ask the server for the detail at the current entity and tick, at most once
   * per entity/tick pair and debounced whenever the tick keeps moving on its
   * own (replay playback, or a live world ticking). Returns the cached detail
   * for the current tick if it has already arrived.
   */
  private requestDetail(entityId: string): AgentDetailMessage | null {
    const status = this.worldState.getReplayStatus();
    const tickId = status ? status.tick_id : this.worldState.getCurrentTick();
    const key = `${entityId}@${tickId}`;

    const cached = this.worldState.getAgentDetail(entityId, tickId);
    if (cached) return cached;

    if (this.requestedDetailKey === key) return null;

    const movingOnItsOwn = status ? status.playing : true;
    if (movingOnItsOwn) {
      if (this.detailTimer !== null) return null;
      this.detailTimer = window.setTimeout(() => {
        this.detailTimer = null;
        const now = this.worldState.getReplayStatus();
        const latestTick = now ? now.tick_id : this.worldState.getCurrentTick();
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

  /** Health, food and tiredness as compact bars. */
  private renderStats(entity: InterpolatedEntity, isPlayer: boolean): void {
    const rows: HTMLElement[] = [
      barRow('Health', entity.health, entity.maxHealth, '#d35f5f'),
    ];
    if (isPlayer) {
      rows.push(barRow('Food', entity.food, entity.maxFood, '#e0913a'));
      const state = fatigueState(entity.fatigue, entity.maxFatigue);
      rows.push(
        barRow(
          'Tiredness',
          entity.fatigue,
          entity.maxFatigue,
          state === 'fresh' ? '#5f8dd3' : '#b07fd3',
          state
        )
      );
    }
    this.statsEl.replaceChildren(...rows);
  }

  /**
   * "Doing now": the planner's instruction as the headline, then Jev's last
   * chosen action and the criterion it was chosen on, then the small print
   * (ticks used, success condition, travel target, notes).
   */
  private renderDoingNow(
    statusBrief: string,
    stint: AgentStint | null,
    detail: AgentDetailMessage | null
  ): void {
    const brief: StintBrief = detail?.stint?.brief ?? {};
    const fallbackBrief = typeof stint?.brief === 'string' ? stint.brief : '';
    this.setText(this.briefEl, brief.instruction || statusBrief || fallbackBrief);
    this.jevEl.replaceChildren(...this.jevNowParts(stint, detail));
    this.briefMetaEl.replaceChildren(...this.briefNoteLines(brief, stint));
  }

  /** Jev's chosen action in plain words, plus the reason it was offered on. */
  private jevNowParts(
    stint: AgentStint | null,
    detail: AgentDetailMessage | null
  ): HTMLElement[] {
    const chosen = typeof stint?.action === 'string' ? stint.action : '';
    if (!chosen) return [];

    const confidence =
      typeof stint?.confidence === 'number' ? ` · ${formatPercent(stint.confidence)} sure` : '';
    const parts: HTMLElement[] = [textDiv(`${actionInWords(chosen)}${confidence}`, 'doing')];

    const reason = detail?.criteria?.[chosen] ?? '';
    if (reason) parts.push(textDiv(reason, 'reason'));
    return parts;
  }

  /** The brief's small print, demoted to muted lines under the action. */
  private briefNoteLines(brief: StintBrief, stint: AgentStint | null): HTMLElement[] {
    const lines: HTMLElement[] = [];
    const maxTicks = stint?.max_ticks ?? brief.max_ticks;
    if (typeof stint?.ticks_used === 'number') {
      lines.push(noteLine(`tick ${stint.ticks_used} of ${maxTicks ?? '?'}`));
    } else if (typeof maxTicks === 'number') {
      lines.push(noteLine(`up to ${maxTicks} ticks`));
    }
    const success = brief.success_condition ?? stint?.success_condition;
    if (typeof success === 'string' && success) {
      lines.push(noteLine(`done when: ${success}`));
    }
    const travel = brief.travel;
    if (travel && Array.isArray(travel.target)) {
      const suffix = travel.label ? ` (${travel.label})` : '';
      lines.push(noteLine(`heading for (${travel.target[0]}, ${travel.target[1]})${suffix}`));
    }
    const notes = brief.notes ?? stint?.notes;
    if (typeof notes === 'string' && notes) {
      lines.push(noteLine(`notes: ${notes}`));
    }
    return lines;
  }

  /** The settler's journal, the way it wrote it (docs/12_sleep_journal.md). */
  private renderJournal(detail: AgentDetailMessage | null): void {
    if (!detail) {
      this.journalEl.replaceChildren(mutedDiv('loading...'));
      return;
    }
    this.journalEl.replaceChildren(...journalParts(detail.journal));
  }

  /**
   * The collapsed Details block: the numbers behind the Jev decision, its
   * spend, the state Jev was given, the planner turn and the journal text.
   */
  private renderDetails(
    entity: InterpolatedEntity,
    stint: AgentStint | null,
    detail: AgentDetailMessage | null
  ): void {
    this.debugEl.replaceChildren(...this.debugParts(entity, stint));

    if (!detail) {
      const waiting = 'no detail for this tick (is the replay server running?)';
      this.jevStateEl.replaceChildren(mutedDiv(waiting));
      this.plannerEl.replaceChildren(mutedDiv(waiting));
      this.memoryEl.replaceChildren(mutedDiv(waiting));
      return;
    }

    // What Jev saw: the state it was asked about, plus every option's criterion.
    const jevParts: HTMLElement[] = [];
    if (detail.jev_state) {
      jevParts.push(pre(JSON.stringify(detail.jev_state, null, 2)));
    } else {
      jevParts.push(mutedDiv('no Jev call at this tick'));
    }
    if (detail.criteria && Object.keys(detail.criteria).length > 0) {
      jevParts.push(label('Criteria'));
      for (const [option, description] of Object.entries(detail.criteria)) {
        jevParts.push(kvRow(option, description));
      }
    }
    this.jevStateEl.replaceChildren(...jevParts);

    this.plannerEl.replaceChildren(...this.plannerParts(detail));

    const raw = journalRawText(detail.journal) || (detail.memory ?? '');
    this.memoryEl.replaceChildren(
      raw.trim() ? pre(raw) : mutedDiv('no journal text at this tick')
    );
  }

  /** Every option's probability plus the Jev call's numbers and this agent's spend. */
  private debugParts(entity: InterpolatedEntity, stint: AgentStint | null): HTMLElement[] {
    const parts: HTMLElement[] = [];
    parts.push(kvRow('Wielded', entity.wielded || 'nothing'));
    const cost = this.worldState.getAgentCost(entity.entityId);
    if (cost) parts.push(kvRow('Spend', formatAgentCost(cost)));

    if (!stint) {
      parts.push(mutedDiv('no Jev decision recorded'));
      return parts;
    }

    const chosen = typeof stint.action === 'string' ? stint.action : '';
    parts.push(kvRow('Action', chosen || '-'));
    for (const item of readProbabilities(stint)) {
      const row = barRow(
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
      parts.push(kvRow('Confidence', formatPercent(stint.confidence)));
    }
    parts.push(kvRow('Eject', formatPercent(stint.eject)));
    parts.push(kvRow('Danger', formatPercent(stint.danger)));
    if (typeof stint.latency_ms === 'number') {
      parts.push(kvRow('Latency', `${Math.round(stint.latency_ms)} ms`));
    }
    if (typeof stint.input_tokens === 'number') {
      parts.push(kvRow('Input tokens', String(stint.input_tokens)));
    }
    if (typeof stint.ticks_left === 'number') {
      parts.push(kvRow('Ticks left', String(stint.ticks_left)));
    }
    const result = stint.intent_result ?? stint.result;
    if (typeof result === 'string' && result) {
      parts.push(kvRow('Result', result));
    }
    if (typeof stint.options === 'number') {
      parts.push(kvRow('Options', String(stint.options)));
    }
    return parts;
  }

  private plannerParts(detail: AgentDetailMessage): HTMLElement[] {
    const turn = detail.planner_turn;
    if (!turn) return [mutedDiv('no planner turn at this tick')];

    const parts: HTMLElement[] = [];
    const range = turn.ended_tick == null ? 'in progress' : `ended t${turn.ended_tick}`;
    parts.push(kvRow(`Turn ${turn.turn}`, `t${turn.started_tick ?? '?'} - ${range}`));

    if (turn.prompt) {
      parts.push(details('Prompt', [pre(turn.prompt)]));
    }

    for (const event of turn.events ?? []) {
      parts.push(this.plannerEventPart(event));
    }

    if (turn.thought) {
      parts.push(label('Reflection'));
      parts.push(pre(turn.thought));
    }
    return parts;
  }

  private plannerEventPart(event: PlannerTurnEvent): HTMLElement {
    if (event.event === 'tool_call') {
      const args = event.args ? JSON.stringify(event.args, null, 2) : '{}';
      return details(`call ${event.tool ?? '?'}`, [pre(args)]);
    }
    if (event.event === 'tool_result') {
      return details(`result ${event.tool ?? '?'}`, [pre(event.result ?? '')]);
    }
    return mutedDiv(`${event.event}${event.tool ? ` ${event.tool}` : ''}`);
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
      ...entries.map(([kind, count]) => kvRow(kind, String(count)))
    );
  }

  private renderLog(entityId: string): void {
    const log = this.worldState.getEntityLog(entityId);
    if (log.length === 0) {
      this.logEl.replaceChildren(mutedItem('-'));
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
}
