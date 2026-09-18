# Run Recording and Replay: Design and Contract

Milestone 7. Every run is recorded to a run directory that a replay server can
serve to the viewer over the same WebSocket protocol as a live world, with
seeking, stepping and playback. The viewer supports deep links so an analysis
can point at "tick 412, following bram, panel open".

This document is the contract for the four tracks (world recording + replay
server, agent tracing, viewer, tooling). Tracks deviate only by noting it in
"Deviations" at the bottom.

## Goals

- Record enough to replay a run visually and to inspect what every AI saw and
  decided at any tick: world state, actions, chat, board notes, chest contents,
  planner turns (prompt, tool calls, results, reflection), Jev decisions with the
  full probability map and the exact state Jev was given.
- Deep links: `http://localhost:5173/?run=<run_id>&tick=<n>&entity=<id>`.
- Keep disk use modest: one directory per run, gzip-compressed JSONL, heavy
  per-tick payloads (Jev state) in their own files so the light files stay
  light.

Not Parquet: the earlier plan named Parquet, but the experiment crashes and
gets Ctrl-C'd often, and Parquet files are only valid once their footer is
written. Streaming JSONL inside gzip survives a kill (readers tolerate a
truncated last member) and matches the agents' existing trace format. A
Parquet export for analysis can be added as a tool later.

## Run identity and directory layout

A **run id** is `YYYYMMDD-HHMMSS-<config>` in local time, e.g.
`20260917-143000-settlement`. `dev.sh` creates it and exports two environment
variables that every process inherits:

| variable | value |
| --- | --- |
| `BOBGAME_RUN_ID` | the run id |
| `BOBGAME_RUN_DIR` | absolute path of `runs/<run_id>` |

The world server and the agents read `BOBGAME_RUN_DIR` (CLI flags override
it). When neither is set the world server makes its own run id and writes under
`<project_root>/runs/`; agents fall back to `logs/` as today. `runs/` is
gitignored. `dev.sh` also maintains the symlink `runs/latest -> <run_id>`.

```
runs/<run_id>/
  meta.json                       # run metadata, written at start, updated at clean stop
  world.log, runner.log, viewer.log   # process stdout/stderr (dev.sh)
  world/
    objects.jsonl.gz              # every object at tick 0, one per line, viewer ObjectState shape
    ticks.jsonl.gz                # tick and agent_status records in time order
  agents/
    agent-<id>.log                # agent stderr (runner)
    agent-<id>/
      stints.jsonl.gz             # stint_start / per-tick Jev record / stint_end (light)
      jev_states.jsonl.gz         # the exact state + criteria sent to Jev, per call (heavy)
      planner.jsonl.gz            # planner turns: prompt, tool calls, results, reflection
      memory.md                   # the planner's persistent notes (plain text, as today)
```

### Gzip JSONL writing

Files are opened once per process with `gzip.open(path, "at")` and kept open.
After each write the writer calls `flush()` with `zlib.Z_SYNC_FLUSH` (at most
once per second is fine for the world; per line is fine for agents, which write
far less), so a reader sees everything up to the last flush even if the process
is killed. Readers must tolerate a truncated final block (`EOFError` /
`zlib.error` on the last partial line means "end of file"). Never append by
reopening per line: that produces one gzip member per line and no compression.

## meta.json

```json
{
  "format_version": 1,
  "run_id": "20260917-143000-settlement",
  "config_name": "settlement",
  "config_path": "world/configs/settlement.toml",
  "started_at": "2026-09-17T14:30:00-04:00",
  "finished_at": null,
  "last_tick": null,
  "world_size": {"width": 4000, "height": 4000},
  "chunk_size": 32,
  "tick_duration_ms": 2000,
  "intent_deadline_ms": 1200,
  "day_length_ticks": 300,
  "settlement": {"x": 168, "y": 904},
  "wolves": true,
  "map_path": "saves/island.npz",
  "map_sha256": "…",
  "entities": [{"entity_id": "ada", "entity_type": "player"}, "…"]
}
```

`map_path` is relative to the project root; `map_sha256` lets the replay server
warn when the saved map has been regenerated since. `finished_at` and
`last_tick` are written at clean shutdown; when missing, the replay server
derives `last_tick` from the last tick record.

For configs with no map file (`generation_mode = "empty"`) `map_path` is
`null` and the replay server builds an empty `World(width, height)`.

## world/ticks.jsonl.gz records

One JSON object per line, in the order they happened. `tick_id` on every line.

### `tick`

Written once per completed tick by the world server, from `TickResult`. The
field names and shapes are exactly the viewer's `tick_completed` message, plus
the events the viewer message leaves out:

```json
{"type": "tick", "tick_id": 412,
 "clock": {"day": 1, "tick_of_day": 112, "day_length": 300, "night": false},
 "wall_ms": 1758133812345, "duration_ms": 3.1,
 "moves": [{"entity_id": "ada", "from": {"x":1,"y":1}, "to": {"x":2,"y":1}, "success": true}],
 "entity_updates": [ {…viewer EntityState for every entity, every tick…} ],
 "object_changes": [{"object_id": "bush_5060", "field": "berry_count", "old_value": "1", "new_value": "0"}],
 "objects_added": [ {…viewer ObjectState…} ],
 "objects_removed": ["item_pile_12"],
 "actions": [{"entity_id": "ada", "action_type": "extract", "success": true, "details": "…"}],
 "utterances": [{"speaker_id": "ada", "channel": "local", "text": "…", "position": {"x":1,"y":1}}],
 "damage": [{"entity_id": "ada", "attacker_id": "wolf_6", "amount": 2, "remaining_health": 14, "position": {"x":1,"y":1}}],
 "deaths": [{"entity_id": "ada", "killer_id": "wolf_6", "position": {"x":1,"y":1}}],
 "respawns": [{"entity_id": "ada", "position": {"x":168,"y":904}}],
 "entities_spawned": [{"entity_id": "wolf_7", "entity_type": "wolf", "position": {"x":1,"y":1}}],
 "entities_despawned": [{"entity_id": "wolf_6", "reason": "killed", "position": {"x":1,"y":1}}]}
```

`entity_updates` is the full state of every entity after the tick, which is
what makes seeking cheap: entity state at tick N is just that list. Objects are
deltas over `world/objects.jsonl.gz`.

Each entity state carries `entity_id`, `position`, `entity_type`, `tags`,
`health`, `max_health`, `hunger`, `max_hunger`, `wielded`, `alive`, `fatigue`,
`max_fatigue`, `asleep`, `sleeping_on`, `collapsed` and `inventory`
(docs/10_metal_and_sleep.md adds the last five). `clock` is the world clock at
that tick and appears on the `tick` record, on the viewer's `tick_completed`
and `snapshot` messages, and on the replay server's copies of both; the replay
server falls back to computing it from `day_length_ticks` for runs recorded
before the clock existed.

### `agent_status`

Written whenever the world receives an `AgentStatusService.ReportStatus` call,
with `tick_id` set to the world's current tick at receipt. Same fields as the
viewer's `agent_status` message:

```json
{"type": "agent_status", "tick_id": 412, "entity_id": "ada", "mode": "stint",
 "brief": "…", "planner_thought": "…", "stint": {…status_json…} | null}
```

This is what lets a replay show the agent panel even when the agent trace
files are missing.

## Agent trace files

All lines carry `entity_id` and `tick` (the agent's world-model tick when the
line was written). `stint_id` is `<entity_id>-<start_tick>`.

### `stints.jsonl.gz`

- `{"event": "stint_start", "entity_id", "tick", "stint_id",
  "brief": {"instruction", "success_condition", "max_ticks", "notes",
  "check_every", "travel": {"target": [x, y], "label"} | null}}`
- Per-tick record (today's `TickRecord` fields) plus `stint_id`,
  `confidence`, and `probabilities` (the full option → probability map; `top`
  stays for `analyze_run.py`). No `event` key, as today.
- `{"event": "stint_end", "entity_id", "tick", "stint_id", "end_reason",
  "ticks_used", "max_ticks", "brief", "report": "<StintReport.to_text()>"}`

### `jev_states.jsonl.gz`

One line per actual Jev call (not for repeated actions or timeouts):
`{"entity_id", "tick", "stint_id", "state": {…build_state() output…},
"criteria": {option_key: description}}`. This is the heavy file (about 8 KB
raw per line); nothing else should go in it.

### `planner.jsonl.gz`

`turn` numbers count from 1 per process.

- `{"event": "turn_start", "entity_id", "tick", "turn", "prompt": "…"}`
- `{"event": "tool_call", "entity_id", "tick", "turn", "tool", "args": {…}}`
  (parsed JSON args, untruncated)
- `{"event": "tool_result", "entity_id", "tick", "turn", "tool", "result": "…"}`
  (full text; `tick` is the tick the result came back, so a `start_stint`
  result's tick is the stint's end)
- `{"event": "turn_end", "entity_id", "tick", "turn", "thought": "…",
  "tool_calls": 3, "duration_ms": 8100, "usage": {"input_tokens", "output_tokens"}}`
- `{"event": "turn_failed", "entity_id", "tick", "turn", "error": "…"}`
- `{"event": "tool_budget_reached", "entity_id", "tick", "turn"}`
- `{"event": "history_reset", "entity_id", "tick", "turn"}`

## Replay server

`cd world && uv run python -m world.replay --runs-dir ../runs --port 8766`

One server serves every run under `--runs-dir`. It speaks the live viewer
protocol (`snapshot`, `chunk_data`, `chunk_unload`, `tick_started`,
`tick_completed`, `entity_spawned`, `entity_despawned`, `agent_status`) so
the viewer renders a replay with the same code as a live world, plus the
control messages below. Runs are loaded lazily on first `open_run` and kept
in memory (index of tick records by tick id; the light agent files; the heavy
`jev_states` file is indexed by byte offset and read on demand).

The replay server keeps a `World` per connection-independent run session:
terrain from `map_path`, objects from `objects.jsonl.gz`, then it applies
tick records. Seeking to tick N means: restore every object touched since the
baseline, apply object deltas for ticks `first..N`, replace the entity set with
tick N's `entity_updates`, and resync the chunk index. Keep the set of touched
object ids so a rollback never rebuilds all 60k island objects.

### Client → server

| message | effect |
| --- | --- |
| `{"type": "open_run", "run_id"}` | load the run, send `snapshot` (see below) then `run_index`; position at `first_tick`, paused |
| `subscribe_chunks` / `subscribe_viewport` | as live |
| `{"type": "seek", "tick_id"}` | jump; clamps to `[first_tick, last_tick]` |
| `{"type": "step", "delta"}` | seek to current + delta (negative allowed) |
| `{"type": "play", "speed"}` | advance one tick every `tick_duration_ms / speed` ms until `last_tick`, then pause. `speed` > 0, default 1 |
| `{"type": "pause"}` | stop advancing |
| `{"type": "get_agent_detail", "entity_id", "tick_id"}` | reply `agent_detail` |
| `{"type": "get_run_index"}` | reply `run_index` |

### Server → client

`snapshot` is the live snapshot plus:

```json
{"type": "snapshot", "tick_id": 412, "world_size": …, "chunk_size": 32,
 "tick_duration_ms": 2000, "settlement": …, "run_id": "…",
 "replay": {"run_id": "…", "first_tick": 0, "last_tick": 1830, "tick_id": 412,
            "playing": false, "speed": 1}}
```

The live world server also puts `run_id` in its snapshot (no `replay` block),
so the viewer can offer a replay link for the run it is watching.

After `open_run` or any `seek`/`step` the server sends, in order:
1. `snapshot` (the viewer clears entities, objects and logs; it must keep the
   camera and chunk subscriptions),
2. `chunk_data` for every chunk the client is subscribed to,
3. `tick_completed` for the target tick (its actions and utterances populate
   the panel; `entity_updates` places every entity),
4. `entity_log` with the last ten actions/utterances per entity from the
   preceding fifty ticks: `{"type": "entity_log", "tick_id", "entries":
   [{"tick_id", "entity_id", "kind": "action" | "utterance", "text",
   "success", "channel"}]}`,
5. `agent_status` for each entity that has one at or before the target tick,
6. `replay_status`.

During playback each tick sends `tick_started` (with `tick_duration_ms`
divided by `speed`, so interpolation matches the pace), `tick_completed`,
`entity_spawned`/`entity_despawned` derived from the record, any
`agent_status` records stamped with that tick, then `replay_status`.

- `{"type": "replay_status", "tick_id", "playing", "speed", "first_tick",
  "last_tick"}` after every state change.
- `{"type": "run_index", "run_id", "agents": ["ada", …], "events": [{"tick_id",
  "kind", "entity_id", "text"}]}` where `kind` is one of `death`, `respawn`,
  `wolf_spawned`, `wolf_killed`, `craft`, `place`, `write_note`, `say`,
  `stint_start`, `planner_turn`, `planner_failed`. Cheap to build from the
  tick records and the light agent files; capped at a few thousand entries,
  dropping `say` and `stint_start` first.
- `{"type": "agent_detail", "entity_id", "tick_id", "stint": {…stint_start
  line or null…}, "record": {…per-tick Jev record at that tick or the latest
  before it, or null…}, "jev_state": {…} | null, "criteria": {…} | null,
  "planner_turn": {"turn", "started_tick", "ended_tick", "prompt", "thought",
  "events": [tool_call / tool_result lines in order]} | null, "memory": "…"}`.
  `planner_turn` is the turn in progress at `tick_id`, else the last one that
  ended before it. `memory` is the memory file's full text (it is small).
- `{"type": "error", "message"}` for unknown runs or bad requests.

## Viewer

### Deep links

Query parameters, all optional:

| param | meaning |
| --- | --- |
| `run` | replay mode: connect to the replay server and `open_run` |
| `ws` | WebSocket URL override (defaults: live `ws://localhost:8765`, replay `ws://localhost:8766`) |
| `tick` | seek here after the snapshot (replay only) |
| `entity` | select this entity, camera follows it |
| `x`, `y` | put the camera on this tile, follow off (overrides `entity` for the camera only) |
| `zoom` | camera zoom |
| `play` | `1` to start playing after the seek |
| `speed` | playback speed |
| `panel` | `1` / `0` to show or hide the agent panel (default shown) |
| `object` | select this object id and open its inspector |

The viewer keeps the URL in sync (`history.replaceState`, throttled to a few
times per second) with the current run, tick, selected entity/object, and
camera, so the address bar is always a valid deep link; a "copy link" button
does the same explicitly.

### Replay transport

A bottom bar, present only in replay mode: run id, jump-to-start, -10, -1,
play/pause, +1, +10, jump-to-end, a tick number input, a slider over
`[first_tick, last_tick]`, a speed select (0.5, 1, 2, 5, 10, 25), and the
events dropdown from `run_index` (choosing one seeks there and selects the
entity). Keys: space play/pause, `,` and `.` step ±1, `[` and `]` step ±10.

### Inspectors

- Clicking a chest, item pile, message board or bush selects that object and
  shows an object panel: type, id, position, contents (kind and count) or the
  board's notes (slot, title, author, tick, text) or berry state. Object
  `state` already carries `contents` / `notes` as JSON strings in both live
  and replay modes, so this needs no new server data.
- The agent panel grows: the brief with its success condition, ticks used of
  max, and notes; the planner reflection; the Jev decision with every option
  and its probability, plus confidence, eject, danger, latency and input
  tokens; and, in replay mode only (from `agent_detail`), collapsible "what
  Jev saw" (pretty-printed state JSON), the current planner turn (prompt, tool
  calls with args, results, in order) and the planner's memory notes. The
  detail is requested when the selection or tick changes (debounced while
  playing).

## Tooling

- `dev.sh` creates the run id and directory, exports the environment
  variables, points every log at the run directory, maintains `runs/latest`,
  and starts the replay server alongside the live world (it is cheap and it
  lets "copy link" work during a run).
- `tools/analyze_run.py [runs/<run_id>]` reads the run directory (defaulting
  to `runs/latest`), keeps today's summary, and adds a "notable moments"
  section listing deaths, wolf kills, crafts, placed objects, notes written,
  planner failures and history resets, each with a deep link such as
  `http://localhost:5173/?run=<run_id>&tick=412&entity=bram`.

## Deviations

**Tooling track** (`dev.sh`, `replay.sh`, `tools/analyze_run.py`):

- `dev.sh` writes a fourth process log, `runs/<run_id>/replay.log`, alongside
  `world.log`, `runner.log` and `viewer.log`. `replay.sh` writes its viewer log
  to `viewer-replay.log` so replaying a run does not clobber the original
  `viewer.log`.
- `dev.sh` does not pass `--run-dir` to the world server or `--log-root` to the
  agents; it exports `BOBGAME_RUN_DIR` and lets both processes pick it up.
  `runner/configs/settlement.toml` no longer passes `--log-root ../logs`, and
  the runner is started with `--log-dir "$RUN_DIR/agents"`.
- `analyze_run.py` cannot report "first sighting of a wolf by a settler": the
  tick records carry no per-agent visibility. It reports the first tick a wolf
  was spawned instead (`first_wolf`).
- `entities_despawned` carries no killer, so `analyze_run.py` attributes a
  `wolf_killed` moment to the `attacker_id` of a `damage` record for that wolf
  in the same tick, and falls back to the wolf's own id.
- `analyze_run.py` identifies wolves by `entity_type == "wolf"` in
  `entities_spawned`, falling back to an `entity_id` starting with `wolf`.
  Crafts, placements and notes come from `actions` with `action_type` in
  `craft` / `place` / `write_note` and `success` true; the moment text uses the
  action's `details` string.
- `analyze_run.py` still reads the legacy flat `logs/` layout (detected by the
  absence of `meta.json`), where the traces are uncompressed and planner
  numbers are scraped from the structlog `.log` file.
- Agents: `jev_states.jsonl.gz` also gets a line when the Jev call times out or
  errors. The state and criteria on that line really were sent, and the stint
  trace's `note` (`jev_timeout` / `jev_error: …`) says what came back. Repeats
  under `check_every` still write no line, because no call is made.
- Agents: the `brief` field on a `stint_end` line is the instruction string, as
  before; the full brief object is on the `stint_start` line for that
  `stint_id`.
- World (recording/replay): the replay server identifies a run by its
  *directory name* under `--runs-dir`, not by `meta.run_id`. They match for
  runs made by `dev.sh` or by the world server's own run id; a run written to a
  hand-picked `--run-dir` is served under that directory's name.
- World (recording): the live `snapshot` always carries the `run_id` key; it is
  `null` when the server runs with `--no-record`.
- World (replay): the session builds terrain from the map file's floor array
  and takes every object from `world/objects.jsonl.gz` (the recorded baseline)
  rather than from the map file's object list.
- World (replay): `entity_log` entries always carry all five keys; `channel` is
  `""` for actions and `success` is `true` for utterances.
- Viewer: entity and object selection are mutually exclusive, so the URL
  carries at most one of `entity` and `object`; selecting one clears the other.
- Viewer: it sends `get_run_index` once after the first snapshot in replay
  mode even though `open_run` already replies with one, so `get_run_index`
  must be idempotent.
- Viewer: `entity_log` entries' `success` and `channel` are treated as
  optional (defaulting to `true` and `""`), and entries are appended in the
  order they arrive, so they must be sent oldest first.
- Viewer: `agent_status.tick_id` and `agent_detail.memory` are optional; the
  viewer falls back to the current replay tick and to "no memory notes".
- Viewer: probabilities are read from `record.probabilities` first, falling
  back to `top` (`[option, probability]` pairs or `{option, probability}`
  objects). Every option is shown, sorted by probability.
- Viewer: it uses `snapshot.replay.first_tick` / `last_tick` for the slider
  immediately, before the first `replay_status` arrives.
- Viewer: `tick` is written to the address bar only in replay mode, so
  reloading a live session never turns into a replay by accident.
