/**
 * Object inspector: shows the selected chest, item pile, message board, bush,
 * reeds, clay deposit or placed building. The markup lives in index.html; this
 * module only fills it in.
 *
 * All the data comes from `ObjectState.state`, which the world server already
 * sends in both live and replay mode: `contents` and `notes` are JSON strings,
 * `berry_count` is "0" or "1", `owner` is an entity id, and `progress` is the
 * extraction or dismantle work done so far.
 */

import type { TrackedObject, WorldState } from '../network';
import {
  CONVERSATION_TYPE,
  parseConversationParticipants,
  parseConversationTranscript,
} from '../conversation';

export interface ObjectPanelCallbacks {
  /** Called when the user closes the panel (clears the selection). */
  onClose: () => void;
}

/**
 * Placed buildings (docs/08_building.md). Every one of them carries an `owner`
 * and, once someone starts taking it apart, a dismantle `progress`.
 */
const BUILDING_TYPES = new Set([
  'road',
  'wood_floor',
  'stone_floor',
  'wood_wall',
  'stone_wall',
  'door',
  'sign',
  'bed',
  'chair',
  'table',
  'workshop_table',
  // Crafting stations (docs/10_metal_and_sleep.md, section 1).
  'furnace',
  'anvil',
]);

/**
 * A sign (docs/08_building.md, "Signs"): one line of at most 80 characters in
 * `text`, plus `author` and `tick`. It is a building, so it also dismantles.
 */
const SIGN_TYPE = 'sign';

/** Stations that hold per-settler craft progress in `craft:<entity_id>`. */
const STATION_TYPES = new Set(['workshop_table', 'furnace', 'anvil']);

/** Ground-layer buildings: they lie under structures and never block. */
const GROUND_LAYER_TYPES = new Set(['road', 'wood_floor', 'stone_floor']);

/** Buildings that stop someone walking through (a door only stops wolves). */
const BLOCKING_TYPES = new Set(['wood_wall', 'stone_wall', 'door']);

/** Work units needed to dismantle a building (world/src/world/items.py). */
const DISMANTLE_WORK = 3;

/** Work units for one unit of material from a natural object. */
const EXTRACT_THRESHOLD = 3;

/**
 * Natural objects worked with `extract` rather than opened. Trees and rocks
 * belong here too, but they are not in INSPECTABLE_TYPES: a map holds tens of
 * thousands of them and there is no point making every one of them clickable.
 */
const RESOURCE_TYPES = new Set([
  'tree',
  'rock_small',
  'rock_medium',
  'rock_large',
  'boulder',
  'reeds',
  'clay_deposit',
  'copper_vein',
  'iron_vein',
]);

/** Object types that have something worth inspecting. */
export const INSPECTABLE_TYPES = new Set([
  'chest',
  'item_pile',
  'message_board',
  'sign',
  'bush',
  'reeds',
  'clay_deposit',
  'copper_vein',
  'iron_vein',
  ...BUILDING_TYPES,
]);

interface BoardNote {
  title?: string;
  text?: string;
  author?: string;
  tick?: number;
}

function requireElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Object panel element #${id} is missing from index.html`);
  }
  return element as T;
}

/**
 * Parse a JSON string from an object's state.
 * Malformed JSON is reported rather than silently swallowed, because it means
 * the server changed shape.
 */
function parseState(raw: string | undefined, objectId: string, field: string): unknown {
  if (raw === undefined || raw === '') return null;
  try {
    return JSON.parse(raw);
  } catch (error) {
    console.error(`Object ${objectId}: ${field} is not valid JSON`, error, raw);
    return null;
  }
}

/** "workshop_table" -> "workshop table", for the panel heading. */
function describeType(objectType: string): string {
  return objectType.replace(/_/g, ' ');
}

/**
 * Work already done on this object, shared by extraction and dismantling.
 * A missing or malformed value counts as no progress.
 */
function readProgress(obj: TrackedObject): number {
  const raw = obj.state.progress;
  if (raw === undefined || raw === '') return 0;
  const value = Number.parseInt(raw, 10);
  return Number.isNaN(value) ? 0 : value;
}

/**
 * Per-settler craft progress on a station: state keys `craft:<entity_id>` hold
 * `<recipe>:<done>` (docs/10_metal_and_sleep.md, section 1). Malformed values
 * are skipped rather than guessed at.
 */
function readCraftProgress(
  obj: TrackedObject
): Array<{ entityId: string; recipe: string; done: string }> {
  const rows: Array<{ entityId: string; recipe: string; done: string }> = [];
  for (const [key, value] of Object.entries(obj.state)) {
    if (!key.startsWith('craft:')) continue;
    const entityId = key.slice('craft:'.length);
    const separator = value.lastIndexOf(':');
    if (separator <= 0) {
      console.warn(`Object ${obj.objectId}: unreadable craft progress ${key}=${value}`);
      continue;
    }
    rows.push({
      entityId,
      recipe: value.slice(0, separator),
      done: value.slice(separator + 1),
    });
  }
  return rows.sort((a, b) => a.entityId.localeCompare(b.entityId));
}

function readContents(obj: TrackedObject): Array<[string, number]> {
  const parsed = parseState(obj.state.contents, obj.objectId, 'contents');
  if (typeof parsed !== 'object' || parsed === null) return [];
  return Object.entries(parsed as Record<string, unknown>)
    .filter((pair): pair is [string, number] => typeof pair[1] === 'number' && pair[1] !== 0)
    .sort((a, b) => a[0].localeCompare(b[0]));
}

function readNotes(obj: TrackedObject): Array<{ slot: number; note: BoardNote }> {
  const parsed = parseState(obj.state.notes, obj.objectId, 'notes');
  if (!Array.isArray(parsed)) return [];
  const notes: Array<{ slot: number; note: BoardNote }> = [];
  parsed.forEach((entry, slot) => {
    if (entry && typeof entry === 'object') {
      notes.push({ slot, note: entry as BoardNote });
    }
  });
  return notes;
}

export class ObjectPanel {
  private worldState: WorldState;
  private panel: HTMLElement;
  private titleEl: HTMLElement;
  private bodyEl: HTMLElement;

  constructor(worldState: WorldState, callbacks: ObjectPanelCallbacks) {
    this.worldState = worldState;
    this.panel = requireElement('object-panel');
    this.titleEl = requireElement('op-title');
    this.bodyEl = requireElement('op-body');
    requireElement<HTMLButtonElement>('op-close').addEventListener('click', () =>
      callbacks.onClose()
    );
  }

  /** Redraw from the current selection; hides itself when nothing is selected. */
  refresh(): void {
    const obj = this.worldState.getSelectedObject();
    if (!obj) {
      this.panel.classList.add('hidden');
      return;
    }
    this.panel.classList.remove('hidden');
    this.titleEl.textContent = describeType(obj.objectType);

    const rows: HTMLElement[] = [
      this.kvRow('id', obj.objectId),
      this.kvRow('position', `(${obj.position.x}, ${obj.position.y})`),
    ];

    const owner = obj.state.owner;
    if (owner) rows.push(this.kvRow('owner', owner));

    if (obj.objectType === 'bush') {
      rows.push(this.kvRow('berries', obj.state.berry_count === '1' ? 'ripe' : 'none'));
    } else if (BUILDING_TYPES.has(obj.objectType)) {
      const layer = GROUND_LAYER_TYPES.has(obj.objectType) ? 'ground' : 'structure';
      rows.push(this.kvRow('layer', layer));
      if (BLOCKING_TYPES.has(obj.objectType)) {
        rows.push(this.kvRow('blocks', obj.objectType === 'door' ? 'wolves' : 'everyone'));
      }
      rows.push(this.kvRow('dismantling', `${readProgress(obj)} / ${DISMANTLE_WORK}`));
      if (obj.objectType === SIGN_TYPE) {
        rows.push(this.sectionLabel('Sign'));
        const text = obj.state.text ?? '';
        if (text === '') {
          rows.push(this.muted('blank'));
        } else {
          rows.push(this.kvRow('reads', text));
          rows.push(this.kvRow('written by', obj.state.author || '(nobody)'));
          const tick = obj.state.tick;
          if (tick) rows.push(this.kvRow('written at', `t${tick}`));
        }
      }
      if (STATION_TYPES.has(obj.objectType)) {
        const crafts = readCraftProgress(obj);
        if (crafts.length > 0) {
          rows.push(this.sectionLabel('Crafting'));
          for (const craft of crafts) {
            rows.push(this.kvRow(craft.entityId, `crafting ${craft.recipe} ${craft.done}`));
          }
        }
      }
    } else if (RESOURCE_TYPES.has(obj.objectType)) {
      const remaining = obj.state.remaining;
      if (remaining !== undefined && remaining !== '') {
        rows.push(this.kvRow('remaining', remaining));
      }
      rows.push(this.kvRow('worked', `${readProgress(obj)} / ${EXTRACT_THRESHOLD}`));
    } else if (obj.objectType === 'message_board') {
      rows.push(this.sectionLabel('Notes'));
      const notes = readNotes(obj);
      if (notes.length === 0) {
        rows.push(this.muted('empty board'));
      } else {
        for (const { slot, note } of notes) {
          rows.push(this.noteBlock(slot, note));
        }
      }
    } else if (obj.objectType === CONVERSATION_TYPE) {
      const participants = parseConversationParticipants(obj.state.participants);
      const speaker = obj.state.speaker ?? '';
      rows.push(this.kvRow('participants', participants.join(', ') || '(none)'));
      rows.push(this.kvRow('speaker', speaker || '(none)'));
      if (obj.state.utterances) rows.push(this.kvRow('utterances', obj.state.utterances));
      if (obj.state.opened_by) rows.push(this.kvRow('opened by', obj.state.opened_by));
      if (obj.state.opened_tick) rows.push(this.kvRow('opened at', `t${obj.state.opened_tick}`));

      rows.push(this.sectionLabel('Transcript'));
      const transcript = parseConversationTranscript(obj.state.transcript);
      if (transcript.length === 0) {
        rows.push(this.muted('no lines yet'));
      } else {
        for (const line of transcript) {
          rows.push(this.transcriptLine(line.tick, line.speaker, line.text));
        }
      }
    } else {
      rows.push(this.sectionLabel('Contents'));
      const contents = readContents(obj);
      if (contents.length === 0) {
        rows.push(this.muted('empty'));
      } else {
        for (const [kind, count] of contents) {
          rows.push(this.kvRow(kind, String(count)));
        }
      }
    }

    this.bodyEl.replaceChildren(...rows);
  }

  private noteBlock(slot: number, note: BoardNote): HTMLElement {
    const block = document.createElement('div');
    block.className = 'note';

    const head = document.createElement('div');
    head.className = 'note-head';
    head.textContent = `#${slot} ${note.title ?? '(untitled)'}`;

    const meta = document.createElement('div');
    meta.className = 'note-meta';
    meta.textContent = `${note.author ?? 'unknown'} · t${note.tick ?? '?'}`;

    const body = document.createElement('div');
    body.className = 'note-text';
    body.textContent = note.text ?? '';

    block.append(head, meta, body);
    return block;
  }

  /** One transcript line: `t<tick> <speaker>: <text>`. */
  private transcriptLine(tick: number, speaker: string, text: string): HTMLElement {
    const block = document.createElement('div');
    block.className = 'note';

    const head = document.createElement('div');
    head.className = 'note-meta';
    head.textContent = `t${tick} ${speaker || 'unknown'}`;

    const body = document.createElement('div');
    body.className = 'note-text';
    body.textContent = text;

    block.append(head, body);
    return block;
  }

  private sectionLabel(text: string): HTMLElement {
    const el = document.createElement('div');
    el.className = 'label';
    el.textContent = text;
    return el;
  }

  private muted(text: string): HTMLElement {
    const el = document.createElement('div');
    el.className = 'muted';
    el.textContent = text;
    return el;
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
}
