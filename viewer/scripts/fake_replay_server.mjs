#!/usr/bin/env node
/**
 * Fake replay server for viewer development.
 *
 * Speaks the replay protocol from docs/07_replay.md against a synthetic
 * 64x64 world: four actors, a chest, an item pile, a message board, a bush,
 * and 200 ticks of scripted movement, actions, chat, agent status and agent
 * detail. It exists so the viewer's replay UI can be built and exercised
 * without a recorded run.
 *
 *   node scripts/fake_replay_server.mjs [--port 8766] [--run fake]
 *
 * Then open http://localhost:5173/?run=fake&tick=50&entity=ada
 */

import { WebSocketServer } from 'ws';

const args = process.argv.slice(2);
function argValue(name, fallback) {
  const index = args.indexOf(name);
  return index >= 0 && index + 1 < args.length ? args[index + 1] : fallback;
}

const PORT = Number(argValue('--port', '8766'));
const RUN_ID = argValue('--run', 'fake');
const WORLD_SIZE = 64;
const CHUNK_SIZE = 32;
const TICK_DURATION_MS = 1000;
const FIRST_TICK = 0;
const LAST_TICK = 199;
const GRASS = 3;
const SETTLEMENT = { x: 16, y: 16 };

// --- The scripted world -----------------------------------------------------

const PLAYERS = ['ada', 'bram', 'cleo'];
const ALL_ENTITIES = [...PLAYERS, 'wolf_1'];

function clampTile(value) {
  return Math.max(0, Math.min(WORLD_SIZE - 1, Math.round(value)));
}

/** Deterministic position of an entity at a tick. */
function positionAt(entityId, tick) {
  switch (entityId) {
    case 'ada':
      return { x: clampTile(8 + (tick % 24)), y: clampTile(10 + (Math.floor(tick / 24) % 6)) };
    case 'bram':
      return {
        x: clampTile(20 + 5 * Math.cos(tick / 6)),
        y: clampTile(20 + 5 * Math.sin(tick / 6)),
      };
    case 'cleo':
      return { x: clampTile(40 - (tick % 30)), y: clampTile(30 + (tick % 5)) };
    case 'wolf_1':
      return {
        x: clampTile(30 + 8 * Math.sin(tick / 9)),
        y: clampTile(40 + 6 * Math.cos(tick / 7)),
      };
    default:
      return { x: 0, y: 0 };
  }
}

function entityState(entityId, tick) {
  const isWolf = entityId.startsWith('wolf');
  const damaged = entityId === 'ada' && tick >= 120 ? 6 : 0;
  return {
    entity_id: entityId,
    position: positionAt(entityId, tick),
    entity_type: isWolf ? 'wolf' : 'player',
    tags: [],
    health: Math.max(1, (isWolf ? 12 : 20) - damaged),
    max_health: isWolf ? 12 : 20,
    hunger: isWolf ? 0 : Math.max(0, 100 - (tick % 80)),
    max_hunger: 100,
    wielded: entityId === 'ada' ? 'axe' : '',
    alive: true,
    inventory: isWolf ? {} : { wood: tick % 7, berry: Math.floor(tick / 30) },
  };
}

/** Objects, with the state they have at a given tick. */
function objectsAt(tick) {
  const notes = new Array(20).fill(null);
  notes[0] = {
    title: 'Wood pile',
    text: 'Stacked the oak by the north gate.\nTake what you need.',
    author: 'ada',
    tick: 12,
  };
  notes[3] = {
    title: 'Wolves',
    text: 'Two wolves seen east of the bush line. Carry a sword.',
    author: 'bram',
    tick: 48,
  };
  return [
    {
      object_id: 'chest_1',
      position: { x: 10, y: 10 },
      object_type: 'chest',
      state: {
        contents: JSON.stringify({ wood: 3 + Math.floor(tick / 50), stone: 1, berry: 4 }),
      },
    },
    {
      object_id: 'item_pile_1',
      position: { x: 11, y: 12 },
      object_type: 'item_pile',
      state: { contents: JSON.stringify({ berry: 2 }) },
    },
    {
      object_id: 'board_1',
      position: { x: 13, y: 10 },
      object_type: 'message_board',
      state: { notes: JSON.stringify(notes) },
    },
    {
      object_id: 'bush_1',
      position: { x: 14, y: 13 },
      object_type: 'bush',
      state: { berry_count: tick % 20 < 10 ? '1' : '0' },
    },
    {
      object_id: 'tree_1',
      position: { x: 18, y: 14 },
      object_type: 'tree',
      state: {},
    },
  ];
}

/** Actions recorded at a tick. */
function actionsAt(tick) {
  const actions = [];
  if (tick % 3 === 0) {
    actions.push({
      entity_id: 'ada',
      action_type: 'extract',
      success: tick % 12 !== 0,
      details: 'oak tree -> wood',
    });
  }
  if (tick % 5 === 0) {
    actions.push({
      entity_id: 'bram',
      action_type: 'collect',
      success: true,
      details: 'berry from bush_1',
    });
  }
  if (tick % 17 === 0) {
    actions.push({
      entity_id: 'cleo',
      action_type: 'craft',
      success: true,
      details: 'axe',
    });
  }
  return actions;
}

function utterancesAt(tick) {
  if (tick % 10 !== 0) return [];
  return [
    {
      speaker_id: 'ada',
      channel: 'local',
      text: `tick ${tick}: heading for the tree line`,
      position: positionAt('ada', tick),
    },
  ];
}

function objectChangesAt(tick) {
  const changes = [];
  if (tick % 20 === 0) {
    changes.push({
      object_id: 'bush_1',
      field: 'berry_count',
      old_value: '0',
      new_value: '1',
    });
  } else if (tick % 20 === 10) {
    changes.push({
      object_id: 'bush_1',
      field: 'berry_count',
      old_value: '1',
      new_value: '0',
    });
  }
  if (tick % 50 === 0 && tick > 0) {
    changes.push({
      object_id: 'chest_1',
      field: 'contents',
      old_value: JSON.stringify({ wood: 3 + Math.floor((tick - 1) / 50), stone: 1, berry: 4 }),
      new_value: JSON.stringify({ wood: 3 + Math.floor(tick / 50), stone: 1, berry: 4 }),
    });
  }
  return changes;
}

function tickCompleted(tick) {
  const moves = ALL_ENTITIES.map((entityId) => ({
    entity_id: entityId,
    from: positionAt(entityId, Math.max(FIRST_TICK, tick - 1)),
    to: positionAt(entityId, tick),
    success: true,
  }));
  const actions = actionsAt(tick);
  return {
    type: 'tick_completed',
    tick_id: tick,
    moves,
    object_changes: objectChangesAt(tick),
    actions_processed: actions.length,
    entity_updates: ALL_ENTITIES.map((entityId) => entityState(entityId, tick)),
    actions,
    utterances: utterancesAt(tick),
    objects_added: [],
    objects_removed: [],
  };
}

// --- Agent status and detail ------------------------------------------------

const BRIEFS = {
  ada: {
    instruction: 'Fell oaks north of camp and stock the chest with wood',
    success_condition: 'chest_1 holds at least 10 wood',
    max_ticks: 40,
    notes: 'Axe is already wielded. Avoid the wolf to the east.',
    check_every: 5,
    travel: { target: [18, 14], label: 'oak stand' },
  },
  bram: {
    instruction: 'Pick berries and keep everyone fed',
    success_condition: 'nobody below 40 hunger',
    max_ticks: 30,
    notes: 'Bushes regrow every 20 ticks.',
    check_every: 5,
    travel: null,
  },
  cleo: {
    instruction: 'Craft a second axe at the workbench',
    success_condition: 'inventory holds an axe',
    max_ticks: 25,
    notes: '',
    check_every: 5,
    travel: null,
  },
};

const OPTIONS = {
  ada: ['extract', 'move_north', 'move_east', 'deposit', 'wait'],
  bram: ['collect', 'move_west', 'eat', 'say', 'wait'],
  cleo: ['craft', 'move_south', 'pick_up', 'wait'],
};

function probabilities(entityId, tick) {
  const options = OPTIONS[entityId] ?? ['wait'];
  const weights = options.map((_, index) => {
    const phase = (tick + index * 7) % 13;
    return 1 + phase;
  });
  const total = weights.reduce((a, b) => a + b, 0);
  const map = {};
  options.forEach((option, index) => {
    map[option] = Math.round((weights[index] / total) * 1000) / 1000;
  });
  return map;
}

function stintIdFor(entityId, tick) {
  return `${entityId}-${Math.floor(tick / 40) * 40}`;
}

function stintRecord(entityId, tick) {
  const map = probabilities(entityId, tick);
  const action = Object.entries(map).sort((a, b) => b[1] - a[1])[0][0];
  const brief = BRIEFS[entityId];
  return {
    tick,
    stint_id: stintIdFor(entityId, tick),
    brief: brief.instruction,
    success_condition: brief.success_condition,
    notes: brief.notes,
    ticks_used: tick % 40,
    max_ticks: brief.max_ticks,
    action,
    probabilities: map,
    confidence: 0.4 + ((tick % 7) / 20),
    eject: (tick % 11) / 40,
    danger: entityId === 'ada' && tick >= 120 ? 0.62 : (tick % 5) / 25,
    latency_ms: 180 + (tick % 9) * 11,
    input_tokens: 1400 + (tick % 17) * 13,
  };
}

function agentStatus(entityId, tick) {
  return {
    type: 'agent_status',
    tick_id: tick,
    entity_id: entityId,
    mode: tick % 40 < 2 ? 'planning' : 'stint',
    brief: BRIEFS[entityId].instruction,
    planner_thought:
      `The chest is still light on wood and the oak stand is four tiles north. ` +
      `Keeping ${entityId} on the axe for another stint (tick ${tick}).`,
    stint: stintRecord(entityId, tick),
  };
}

function agentDetail(entityId, tick) {
  const brief = BRIEFS[entityId];
  if (!brief) {
    return {
      type: 'agent_detail',
      entity_id: entityId,
      tick_id: tick,
      stint: null,
      record: null,
      jev_state: null,
      criteria: null,
      planner_turn: null,
      memory: '',
    };
  }
  const stintStartTick = Math.floor(tick / 40) * 40;
  const options = OPTIONS[entityId] ?? ['wait'];
  const criteria = {};
  for (const option of options) {
    criteria[option] = `Choose ${option} when it best advances: ${brief.success_condition}`;
  }
  return {
    type: 'agent_detail',
    entity_id: entityId,
    tick_id: tick,
    stint: {
      event: 'stint_start',
      entity_id: entityId,
      tick: stintStartTick,
      stint_id: stintIdFor(entityId, tick),
      brief,
    },
    record: stintRecord(entityId, tick),
    jev_state: {
      tick,
      self: {
        entity_id: entityId,
        position: positionAt(entityId, tick),
        health: entityState(entityId, tick).health,
        hunger: entityState(entityId, tick).hunger,
        wielded: entityState(entityId, tick).wielded,
        inventory: entityState(entityId, tick).inventory,
      },
      brief,
      surroundings: {
        visible_entities: ALL_ENTITIES.filter((id) => id !== entityId).map((id) => ({
          entity_id: id,
          position: positionAt(id, tick),
        })),
        visible_objects: objectsAt(tick).map((obj) => ({
          object_id: obj.object_id,
          object_type: obj.object_type,
          position: obj.position,
        })),
      },
      recent_actions: actionsAt(tick).filter((a) => a.entity_id === entityId),
    },
    criteria,
    planner_turn: {
      turn: Math.floor(tick / 40) + 1,
      started_tick: stintStartTick,
      ended_tick: tick >= stintStartTick + 3 ? stintStartTick + 3 : null,
      prompt:
        `You are ${entityId}, a settler on the big island.\n\n` +
        `World model at tick ${stintStartTick}:\n` +
        `  position: ${JSON.stringify(positionAt(entityId, stintStartTick))}\n` +
        `  inventory: ${JSON.stringify(entityState(entityId, stintStartTick).inventory)}\n\n` +
        `Decide what to do next and start a stint.`,
      thought:
        `Wood is the bottleneck for the palisade, so ${entityId} keeps chopping. ` +
        `If the wolf comes within four tiles, eject and head for the chest.`,
      events: [
        {
          event: 'tool_call',
          entity_id: entityId,
          tick: stintStartTick,
          turn: Math.floor(tick / 40) + 1,
          tool: 'read_board',
          args: { object_id: 'board_1' },
        },
        {
          event: 'tool_result',
          entity_id: entityId,
          tick: stintStartTick + 1,
          turn: Math.floor(tick / 40) + 1,
          tool: 'read_board',
          result: '#0 Wood pile (ada, t12): Stacked the oak by the north gate.\n#3 Wolves (bram, t48): Two wolves seen east of the bush line.',
        },
        {
          event: 'tool_call',
          entity_id: entityId,
          tick: stintStartTick + 1,
          turn: Math.floor(tick / 40) + 1,
          tool: 'start_stint',
          args: brief,
        },
        {
          event: 'tool_result',
          entity_id: entityId,
          tick: stintStartTick + 3,
          turn: Math.floor(tick / 40) + 1,
          tool: 'start_stint',
          result: `stint ended after ${brief.max_ticks} ticks: success_condition not met, 7 wood gathered`,
        },
      ],
    },
    memory: [
      `# ${entityId}'s notes`,
      '',
      '- The oak stand north of camp regrows slowly; rotate between three trees.',
      '- chest_1 at (10,10) is the shared store. Deposit wood there, not in piles.',
      '- Wolves prowl east after tick 100. Do not chase them.',
    ].join('\n'),
  };
}

// --- Run index --------------------------------------------------------------

function runIndex() {
  const events = [];
  for (let tick = FIRST_TICK; tick <= LAST_TICK; tick++) {
    if (tick % 40 === 0) {
      for (const entityId of PLAYERS) {
        events.push({
          tick_id: tick,
          kind: 'stint_start',
          entity_id: entityId,
          text: BRIEFS[entityId].instruction,
        });
      }
    }
    if (tick % 17 === 0 && tick > 0) {
      events.push({ tick_id: tick, kind: 'craft', entity_id: 'cleo', text: 'crafted an axe' });
    }
    if (tick === 120) {
      events.push({
        tick_id: 120,
        kind: 'wolf_spawned',
        entity_id: 'wolf_1',
        text: 'wolf_1 closed on ada',
      });
    }
    if (tick === 160) {
      events.push({
        tick_id: 160,
        kind: 'write_note',
        entity_id: 'bram',
        text: 'wrote "Wolves" on board_1',
      });
    }
  }
  return { type: 'run_index', run_id: RUN_ID, agents: PLAYERS, events };
}

// --- Terrain ----------------------------------------------------------------

/** Base64 of the RLE (value, count) pairs for a chunk of solid grass. */
function grassChunkBase64() {
  const tiles = CHUNK_SIZE * CHUNK_SIZE;
  const bytes = [];
  let remaining = tiles;
  while (remaining > 0) {
    const run = Math.min(255, remaining);
    bytes.push(GRASS, run);
    remaining -= run;
  }
  return Buffer.from(Uint8Array.from(bytes)).toString('base64');
}

const TERRAIN_B64 = grassChunkBase64();

// --- Session ----------------------------------------------------------------

/** Entity/utterance log entries over the fifty ticks before `tick`. */
function entityLog(tick) {
  const perEntity = new Map();
  const from = Math.max(FIRST_TICK, tick - 50);
  for (let t = from; t <= tick; t++) {
    for (const action of actionsAt(t)) {
      const list = perEntity.get(action.entity_id) ?? [];
      list.push({
        tick_id: t,
        entity_id: action.entity_id,
        kind: 'action',
        text: `${action.action_type} ${action.details}`,
        success: action.success,
        channel: '',
      });
      perEntity.set(action.entity_id, list);
    }
    for (const utterance of utterancesAt(t)) {
      const list = perEntity.get(utterance.speaker_id) ?? [];
      list.push({
        tick_id: t,
        entity_id: utterance.speaker_id,
        kind: 'utterance',
        text: utterance.text,
        success: true,
        channel: utterance.channel,
      });
      perEntity.set(utterance.speaker_id, list);
    }
  }
  const entries = [];
  for (const list of perEntity.values()) {
    entries.push(...list.slice(-10));
  }
  entries.sort((a, b) => a.tick_id - b.tick_id);
  return { type: 'entity_log', tick_id: tick, entries };
}

class Session {
  constructor(socket) {
    this.socket = socket;
    this.tick = FIRST_TICK;
    this.playing = false;
    this.speed = 1;
    this.timer = null;
    this.chunks = new Set();
    this.open = false;
  }

  send(message) {
    if (this.socket.readyState === 1) {
      this.socket.send(JSON.stringify(message));
    }
  }

  snapshot() {
    this.send({
      type: 'snapshot',
      tick_id: this.tick,
      world_size: { width: WORLD_SIZE, height: WORLD_SIZE },
      chunk_size: CHUNK_SIZE,
      tick_duration_ms: TICK_DURATION_MS,
      settlement: SETTLEMENT,
      run_id: RUN_ID,
      replay: {
        run_id: RUN_ID,
        first_tick: FIRST_TICK,
        last_tick: LAST_TICK,
        tick_id: this.tick,
        playing: this.playing,
        speed: this.speed,
      },
    });
  }

  replayStatus() {
    this.send({
      type: 'replay_status',
      tick_id: this.tick,
      playing: this.playing,
      speed: this.speed,
      first_tick: FIRST_TICK,
      last_tick: LAST_TICK,
    });
  }

  sendChunk(chunkX, chunkY) {
    const objects = objectsAt(this.tick).filter(
      (obj) =>
        Math.floor(obj.position.x / CHUNK_SIZE) === chunkX &&
        Math.floor(obj.position.y / CHUNK_SIZE) === chunkY
    );
    const entities = ALL_ENTITIES.map((id) => entityState(id, this.tick)).filter(
      (entity) =>
        Math.floor(entity.position.x / CHUNK_SIZE) === chunkX &&
        Math.floor(entity.position.y / CHUNK_SIZE) === chunkY
    );
    this.send({
      type: 'chunk_data',
      chunk_x: chunkX,
      chunk_y: chunkY,
      version: 1,
      terrain: TERRAIN_B64,
      entities,
      objects,
    });
  }

  /** The ordered sequence the contract requires after open_run/seek/step. */
  sendPosition(skipSnapshot = false) {
    if (!skipSnapshot) this.snapshot();
    for (const key of this.chunks) {
      const [chunkX, chunkY] = key.split(',').map(Number);
      this.sendChunk(chunkX, chunkY);
    }
    this.send(tickCompleted(this.tick));
    this.send(entityLog(this.tick));
    for (const entityId of PLAYERS) {
      this.send(agentStatus(entityId, this.tick));
    }
    this.replayStatus();
  }

  seek(tick) {
    this.tick = Math.max(FIRST_TICK, Math.min(LAST_TICK, Math.round(tick)));
    this.sendPosition();
  }

  play(speed) {
    this.stopTimer();
    this.speed = speed > 0 ? speed : 1;
    this.playing = true;
    this.replayStatus();
    const interval = Math.max(20, TICK_DURATION_MS / this.speed);
    this.timer = setInterval(() => this.advance(), interval);
  }

  advance() {
    if (this.tick >= LAST_TICK) {
      this.pause();
      return;
    }
    this.tick += 1;
    this.send({
      type: 'tick_started',
      tick_id: this.tick,
      tick_start_ms: Date.now(),
      deadline_ms: Date.now() + TICK_DURATION_MS / 2,
      tick_duration_ms: TICK_DURATION_MS / this.speed,
    });
    this.send(tickCompleted(this.tick));
    for (const entityId of PLAYERS) {
      this.send(agentStatus(entityId, this.tick));
    }
    this.replayStatus();
  }

  pause() {
    this.stopTimer();
    this.playing = false;
    this.replayStatus();
  }

  stopTimer() {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  handle(message) {
    switch (message.type) {
      case 'open_run':
        if (message.run_id !== RUN_ID) {
          this.send({ type: 'error', message: `unknown run: ${message.run_id}` });
          return;
        }
        this.open = true;
        this.tick = FIRST_TICK;
        this.playing = false;
        // Contract order: snapshot, run_index, then the position sequence.
        this.snapshot();
        this.send(runIndex());
        this.sendPosition(true);
        return;
      case 'subscribe_chunks': {
        const chunks = message.chunks ?? [];
        for (const [chunkX, chunkY] of chunks) {
          const key = `${chunkX},${chunkY}`;
          if (!this.chunks.has(key)) {
            this.chunks.add(key);
          }
          this.sendChunk(chunkX, chunkY);
        }
        return;
      }
      case 'seek':
        this.seek(message.tick_id);
        return;
      case 'step':
        this.seek(this.tick + (message.delta ?? 0));
        return;
      case 'play':
        this.play(Number(message.speed) || 1);
        return;
      case 'pause':
        this.pause();
        return;
      case 'get_agent_detail':
        this.send(agentDetail(message.entity_id, Number(message.tick_id) || this.tick));
        return;
      case 'get_run_index':
        this.send(runIndex());
        return;
      default:
        this.send({ type: 'error', message: `unknown message: ${message.type}` });
    }
  }
}

const server = new WebSocketServer({ port: PORT });
server.on('connection', (socket) => {
  const session = new Session(socket);
  console.log('viewer connected');
  socket.on('message', (raw) => {
    let message;
    try {
      message = JSON.parse(raw.toString());
    } catch (error) {
      console.error('bad message', error);
      return;
    }
    session.handle(message);
  });
  socket.on('close', () => {
    session.stopTimer();
    console.log('viewer disconnected');
  });
});

console.log(
  `fake replay server on ws://localhost:${PORT} (run "${RUN_ID}", ticks ${FIRST_TICK}-${LAST_TICK})\n` +
    `open http://localhost:5173/?run=${RUN_ID}&tick=50&entity=ada`
);
