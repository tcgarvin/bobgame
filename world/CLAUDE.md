# World Project Notes

Development notes and patterns for the world simulation core.

## Core Data Model and Movement

### Module Map

- `state/` - `models.py` (the frozen `WorldClock`, `Inventory`, `Tile`,
  `Entity`, `WorldObject`) and `world.py` (`World`, the one mutable
  container). `world.state` re-exports every name.
- `mechanics.py` - **the registry**: one `Mechanic` row per intent, holding the
  intent model, its `action_type` string, its proto `Intent` oneof field with
  the function that converts it, and the phase that applies it. `MECHANICS` is
  declared in phase order and `tick.process_tick` walks it, so
  `INTENT_ACTION_TYPES`, `intent_from_proto` and the phase order all come from
  this one table. Adding an intent = one row + one proto field.
- `events.py` - the per-tick event records, `TickEvents` and `commit_object`,
  the one way a mechanic changes an existing object's state.
- `objstate.py` - the typed codec for JSON kept in a `WorldObject.state` key
  (contents, notes, participants, transcripts).
- `speech.py` - the `say` mechanic; `conversations.py` owns the only
  back-and-forth channel.
- `server.py` (`WorldServer`), `bootstrap.py` (`ServerSettings` + `run_server`,
  building a world from a config or a save) and `cli.py` (argparse).
  `python -m world.server` runs `cli.main`.
- `moon.py` - the new moon: clock predicates (`is_new_moon_day`, `is_still`,
  `wolves_lie_low`) and `apply_new_moon`, the forced sleep. Pure physics; it
  happens whether or not a save is written.
- `snapshot.py` - a save on disk: `write_world_snapshot`, `load_world_snapshot`,
  `restore_world`, the path helpers and `check_map`.
- `save_coordinator.py` - `SaveSettings` (run dir, run config, map hash, wait)
  and `SaveCoordinator`: decides when a save is due, waits for the settlers'
  files, writes the world's half or abandons the directory.
- `terrain_types.py` - re-exports `FloorType` and `FLOOR_TYPE_BY_CODE` from
  `bobgame_rules.terrain`. `tests/test_terrain_types.py` parses
  `viewer/src/generated/rules.ts` and fails on drift.
- `chunks.py` - the chunk manager and `terrain_chunk(world, cx, cy)`.

### Game rules live in `rules/`

The kinds, the recipe table, the blocking and extraction tables and every
number behind food, health, fatigue, sleep, combat, wolves, conversations,
signs, the day clock and the floor codes are `bobgame_rules`, the
dependency-free package at the repo root (`rules/`, a uv path dependency).
`items.py`, `crafting.py`, `stats.py`, `sleep.py`, `wolves.py`, `moon.py`,
`containers.py`, `conversations.py`, `types.py`, `state/models.py` and
`services/observation_service.py` import from it and re-export under the names
world code already uses, so there is nothing left to keep in step by hand:
`agents/` and the viewer read the same definitions (the viewer through
`tools/generate_rules_ts.py`). Change a number in `rules/`, not here.

### Data Models: Pydantic Frozen Models

Internal state uses Pydantic v2 with `frozen=True` for immutability:

```python
class Entity(BaseModel, frozen=True):
    ...
    def with_position(self, new_position: Position) -> "Entity":
        return self.model_copy(update={"position": new_position})
```

**Rationale**: Validation at construction, immutability prevents accidental mutation, `.model_copy(update={})` provides clean update pattern.

**Proto types are for API boundaries only** - convert at gRPC layer, not internally.

### World State: Sparse Tile Storage

Tiles use a sparse dict rather than a 2D array:
- `_tiles: dict[Position, Tile]` stores only non-default tiles
- Default tiles (walkable=True, opaque=False) are generated on-demand
- Out-of-bounds positions return non-walkable tiles

### Entity Registry: Dual Indexing

Entities are indexed by both ID and position for O(1) lookups:
- `_entities: dict[str, Entity]` - lookup by ID
- `_entity_positions: dict[Position, str]` - lookup by position

**CRITICAL**: Both indices must be kept in sync. Use `update_entity_position()` for moves.

**Safe to modify `_entities` directly** only when position is unchanged (e.g., inventory updates):
```python
# OK - position unchanged
entity = world.get_entity(entity_id)
world._entities[entity_id] = entity.with_inventory(new_inventory)

# WRONG - use update_entity_position() instead
world._entities[entity_id] = entity.with_position(new_pos)
```

### Movement Conflict Resolution

The claim-resolve-enact pipeline handles conflicts deterministically:

1. **Claim**: Validate moves (bounds, walkability, diagonal blocking)
2. **Resolve**: Detect conflicts in order:
   - Swaps (A→B, B→A) → both fail
   - Cycles (A→B→C→A) → all fail
   - Same destination → lexicographic entity_id wins
   - Destination occupied by non-mover → fail
3. **Enact**: Apply winning moves simultaneously

**Key insight**: Chains succeed (A→B, B→empty both move) because cycle detection only fails actual cycles, not chains.

**CRITICAL - Chain Movement Atomicity**: When enacting moves, position index updates must be atomic across all moves. In a chain where A moves to B's position while B moves away:

```
# WRONG - sequential updates corrupt the index:
1. A: delete old_pos(2,2), add new_pos(3,3) -> A
2. B: delete old_pos(3,3)  <- DELETES A's NEW ENTRY!

# CORRECT - atomic updates (implemented in enact_moves):
1. Delete ALL old positions: (2,2), (3,3)
2. Add ALL new positions: (3,3)->A, (4,4)->B
3. Update entity objects
```

See `test_chain_preserves_position_index` for the regression test.

## Testing Patterns

### Fixtures (conftest.py)

Standard fixtures for common scenarios:
- `empty_world` - 10x10 all walkable
- `world_with_walls` - wall at y=5
- `world_with_l_wall` - L-shaped wall for diagonal blocking tests
- `two_entities` - entities at (2,2) and (7,7)
- `adjacent_entities` - entities at (3,3) and (4,3)
- `three_entities_triangle` - for cycle testing

### Async Tests

Use `@pytest.mark.asyncio` decorator. pytest-asyncio is configured in pyproject.toml.

For tick loop tests, use short durations (30-100ms) to keep tests fast.

## Running Tests

```bash
cd world
uv run pytest tests/ -v          # all tests
uv run pytest tests/ -v -k swap  # filter by name
uv run mypy src/world/           # type check
```

## gRPC Layer

### gRPC Service Architecture

Services are implemented in `services/` directory, each as a separate servicer class:

```
services/
├── __init__.py           # Exports all servicers
├── action_service.py     # SubmitIntent RPC
├── discovery_service.py  # ListControllableEntities RPC
├── lease_service.py      # Acquire/Renew/Release lease RPCs
├── observation_service.py # StreamObservations RPC
└── tick_service.py       # StreamTicks RPC
```

### WorldServer: Central Coordinator

`WorldServer` in `server.py` wires everything together (a run is built from a
`bootstrap.ServerSettings`, which `cli.py` fills from a config or a save):
- Creates shared `LeaseManager` and `TickLoop`
- Registers all service implementations
- Hooks `on_tick_complete` to broadcast observations
- Provides `add_entity()` that tracks spawn ticks

### Lease Management

`LeaseManager` in `lease.py` handles entity control leases:
- Leases expire after 30 seconds by default
- Same controller re-acquiring gets renewal
- Expired leases cleaned up on access or periodic cleanup
- Validation: `is_valid_lease(lease_id, entity_id)`

### Proto Type Conversion

`conversion.py` converts between the internal models and the proto:
- `direction_to_proto()` / `direction_from_proto()`
- `position_to_proto()` / `position_from_proto()`
- `entity_to_proto()` / `entity_from_proto()`
- `tile_to_proto()` / `tile_from_proto()`
- `object_to_proto()`, `clock_to_proto()` (one way: nothing reads them back)

**Python keyword handling**: Proto fields named `self` or `from` require special handling:
```python
# For 'self' field in Observation:
observation.self.CopyFrom(entity_proto)

# For 'from' field in EntityMoved: the constructor does NOT accept from_;
# build the message, then getattr(msg, "from").CopyFrom(from_pos)
```

### Observation Generation

`services/observation_service.py` builds each observer's `Observation` by
distance, not line of sight. Its `EVENT_KINDS` table has one row per event
class (source list on the `TickResult`, a visibility predicate, a proto
builder) and `_build_events` is a loop over it; the table order is the order
events arrive in. Otherwise: `VIEW_RADIUS` (8) filters visible entities, tiles
and objects, and every event class (movement, damage, death, respawn, object
added/changed/removed, utterances, actions) is filtered by the same radius
against the observer's position. There is no ray casting and no enter/leave
event; an entity simply stops appearing when it leaves the radius, which is why
`WorldModel` on the agent side has to remember what it has seen.

## Running the Server

```bash
cd world
uv run python -m world.server --config hamlet
uv run python -m world.server --spawn-entity bob:5,5 --tick-duration 1000

# In another terminal, a settler agent
cd ../agents
uv run python -m agents.jev_agent --entity bob
```

### Wolf tunables in the world config

`[world]` carries five wolf settings, all validated when the config loads
(`world/src/world/config.py`):

- `wolves` (bool) - whether the world simulates wolves at all.
- `max_wolves` (default 3, must be >= 0) - how many live at once.
- `wolf_spawn_min_distance` (default 20, must be >= 1)
- `wolf_spawn_max_distance` (default 40, must be > the minimum and below
  `wolves.DESPAWN_DISTANCE` = 50, or a wolf would be culled on arrival).
- `wolf_spawn_interval_ticks` (default 40, must be >= 1) - ticks between spawn
  attempts, so a killed wolf stays gone for at least that long.

The defaults are the `bobgame_rules.entities` constants, which `wolves.py`
re-exports as `MAX_WOLVES`, `SPAWN_MIN_DISTANCE`, `SPAWN_MAX_DISTANCE`,
`SPAWN_INTERVAL_TICKS` and `DESPAWN_DISTANCE`.
`WorldConfig.wolf_settings()` bundles them into a frozen `WolfSettings`, which
travels `run_server` -> `WorldServer` -> `TickLoop` -> `WolfSimulator`. Nothing
reads `MAX_WOLVES` / `SPAWN_MIN_DISTANCE` / `SPAWN_MAX_DISTANCE` /
`SPAWN_INTERVAL_TICKS` at spawn time; the simulator reads `self.settings`.
`hamlet.toml` overrides them: one wolf, 30-45 tiles, 120 ticks between spawn
attempts. No agent-facing text states the interval.

## Gotchas & Learnings

### Observation Timing Model

Observations must be sent at the **start** of a tick (via `on_tick_start`), not after processing. This gives agents time to receive the observation and submit intents before the deadline.

```
Tick N starts → observation sent (tick_id=N) → agent submits → deadline → process → Tick N+1
```

If observations are sent after processing, agents will always be one tick behind and get "wrong_tick" rejections.

### Proto Import Paths

`grpc_tools` writes an absolute `import world_pb2` into `world_pb2_grpc.py`,
which is wrong for a module inside a package. `tools/compile_proto.sh` rewrites
it to `from . import world_pb2 as world__pb2` in all three packages itself —
there is no manual fix-up step.

### Proto Python Keywords

Proto fields named after Python keywords need special handling:

```python
# 'self' field - use CopyFrom after construction
observation = pb.Observation(tick_id=..., ...)
observation.self.CopyFrom(entity_proto)

# 'from' field - set after construction
moved = pb.EntityMoved(entity_id=id, to=to_pos)
getattr(moved, "from").CopyFrom(from_pos)
```

### gRPC thread pool

Every `StreamObservations` call holds a worker thread for its whole lifetime.
The pool is sized at 64 in `server.py`; the gRPC default of 10 is far too small
(it starves every unary RPC, so lease renewals fail and agents exit).

### Test Port Allocation

Integration tests that start `WorldServer` must use unique ports for **both** gRPC and WebSocket to avoid "address already in use" errors when tests run in parallel:

```python
# WRONG - uses default ws_port=8765 which will conflict
server = WorldServer(world, port=50099, tick_config=config)

# CORRECT - unique ports for each test
server = WorldServer(world, port=50099, ws_port=18765, tick_config=config)
```

Port ranges in use:
- gRPC: 50051 (default), 50098-50099 (tests)
- WebSocket: 8765 (default), 18765-18766 (tests)

## Viewer WebSocket Bridge

### ViewerWebSocketService

Chose WebSocket bridge over gRPC-Web for simplicity:

```python
# world/src/world/services/viewer_ws_service.py
class ViewerWebSocketService:
    """Embedded WebSocket server for viewer clients."""
```

**Design choices**:
- Embedded in WorldServer (same process) for direct state access
- JSON messages (not protobuf) for browser compatibility
- Async broadcast queue for non-blocking event distribution
- Uses `websockets` library for async WebSocket server

**Message types**:
- `snapshot` - Sent on connect with full world state
- `tick_started` - Broadcast at tick start with timing info
- `tick_completed` - Broadcast after processing with move results

**Integration hooks**:
- `on_tick_start()` called from WorldServer tick callback
- `on_tick_complete()` called after movement resolution
- `_generate_snapshot()` creates initial state for new clients

### CLI Arguments

Server now accepts `--ws-port` (default 8765):
```bash
uv run python -m world.server --spawn-entity bob:5,5 --ws-port 8765
```

## Run Recording and Replay

Contract: [docs/07_replay.md](../docs/07_replay.md). Every run is recorded to a
run directory; the replay server serves it to the viewer over the live
WebSocket protocol.

### Recording

- `recording.py` - `JsonlGzWriter` (one gzip member per file, sync-flushed at
  most once a second so a killed run stays readable), `read_jsonl_gz` (tolerates
  a truncated last block), `file_sha256`, `RunRecorder` and the run id.
  `run_id_for(config_name)` returns `$BOBGAME_RUN_ID` when `dev.sh` exported
  one and falls back to `generate_run_id` (`YYYYMMDD-HHMMSS-<config>`): inventing
  a second id here made `meta.json` disagree with the directory name.
- `viewer_payload.py` - the JSON shapes shared by the live viewer service, the
  recorder and the replay server. Never duplicate an entity/object payload;
  add it here.
- `WorldServer` takes an optional `RunRecorder`: it starts it in `start()`,
  appends a `tick` record from `_on_tick_complete`, appends an `agent_status`
  record for every accepted status report, and closes it in `stop()` (which
  fills in `finished_at` and `last_tick`). A write error after startup logs
  once at ERROR and disables recording; it never takes the run down.

```bash
uv run python -m world.server --config hamlet                    # records to ../runs/<run id>
uv run python -m world.server --config hamlet --run-dir /tmp/run
```

`$BOBGAME_RUN_DIR` (exported by `dev.sh`) is the default when set.

### Replay server

```bash
uv run python -m world.replay --runs-dir ../runs --port 8766
```

- `replay/loader.py` - `RunLoader`: meta, the tick records indexed by tick id,
  the agent status records by tick, the light agent files, a byte-offset index
  into `jev_states.jsonl.gz` (read on demand), and the cached `run_index`. The
  object baseline is *streamed* (`iter_objects()`), not held in memory.
  Loaders are cached per run id by the service.
- `replay/session.py` - `ReplaySession`: terrain from `map_path`, objects from
  `objects.jsonl.gz`, then tick deltas. Seeking forward applies object deltas;
  seeking backward restores only the objects touched since the baseline
  (copy-on-write in `_baseline`/`_added`) and replays from the first tick, so a
  rollback never rebuilds the island's 700k objects. Entities are replaced
  wholesale from the tick's `entity_updates`; dead entities stay in the world
  but are detached from the position index, as in the live world.
- `replay/service.py` - one `ReplaySession` per connected client, an asyncio
  playback task honouring `speed`, and the message order from the contract
  (snapshot, chunk_data, tick_completed, entity_log, agent_status,
  replay_status).
- `services/chunk_subscriptions.py` - chunk subscribe/unload/`chunk_data`
  shared by the live and replay services.

Opening an island run costs ~10 s and ~1.2 GB (726k objects, the same cost the
live server pays at startup); it is paid once per run id. Seeks are sub-ms.

## Building Mechanics (docs/08_building.md)

Contract: [docs/08_building.md](../docs/08_building.md); `items.py` is the same
contract in code.

### Recipes and stations (docs/10_metal_and_sleep.md)

`crafting.RECIPES` maps a recipe name to a frozen `Recipe(inputs, output_count,
station, work)`. The recipe table's source of truth is
[docs/10_metal_and_sleep.md](../docs/10_metal_and_sleep.md), section 2.

`station` is `""` for hand crafting, otherwise `workshop_table`, `furnace` or
`anvil`: the crafter must stand on or 8-adjacent to one
(`crafting.nearest_station` / `station_nearby`, which scan the 9 neighbouring
tiles rather than every object in the world; the own tile first, then ties by
smallest object id, so the choice is deterministic).

`work` is the number of craft actions a recipe needs. `work == 1` completes
instantly. A station recipe with `work > 1` keeps progress **on the station
object** under `craft:<entity_id>` = `<recipe>:<done>`: per settler, per
station, surviving the settler walking away and coming back. Inputs are checked
on every action and only consumed on the action that completes the recipe;
crafting a different recipe at that station resets the settler's progress.
Progress actions report `"<recipe> 2/4"`, completion reports
`"crafted <kind>"` (the prefix run analysis greps for).

### Tool tiers and ore veins

`items.EXTRACT_TOOLS` maps an object type to the frozenset of tools that speed
it up and `EXTRACT_WORK_BY_TOOL` gives the work one action adds while wielding
each (stone 3, copper 4, iron 5; bare hands `EXTRACT_WORK_BARE` = 1).
`VEIN_REQUIRED_TOOLS` gates `copper_vein` (any pickaxe) and `iron_vein` (copper
or iron pickaxe): without one the extract fails with
`"<object> needs a <tools>"`. Everything else can still be worked bare-handed.

### Two placement layers

`containers.process_place_phase` puts at most one ground-layer object (`road`,
`wood_floor`, `stone_floor`) and one structure-layer object on a tile; a
structure may stand on a ground object. Ground kinds go on the placer's own
tile when `PlaceIntent.direction is None` (proto `DIRECTION_UNSPECIFIED`) and
may not cover a natural object. Only structures need the tile free of entities.
Placed objects carry `owner`; only message boards get `notes` and only chests
get `contents`.

### Blocking index

Movement resolution treats a tile as free only when its occupant's own move
succeeds. A follower whose leader lost a conflict fails with
`destination_occupied`, and that failure propagates back along a chain. Two
entities on one tile corrupt the position index and kill the tick loop a few
ticks later; the loop logs `tick_loop_crashed` with the traceback if it happens.

`World` keeps `_blocked_positions` / `_wolf_blocked_positions` (per-tile
counts), maintained in `add_object`, `remove_object` and `update_object`. Use
`world.is_passable(position, entity_type)` — never bare `is_walkable` — in
movement validation (including the diagonal corner rule), wolf steering and
spawn placement. Walls block everyone; doors block `entity_type == "wolf"`
only. Observations flip `Tile.walkable` to false on wall tiles so agent path
finding needs no new concept; door tiles stay walkable.

### Signs

`sign` is an ordinary structure-layer building kind (2 wood, by hand) that
blocks nobody. `containers.process_place_phase` gives it empty `text`, `author`
and `tick` state, and `containers._write_sign` - reached from
`process_write_note_phase`, which now accepts a `message_board` **or** a `sign`
- writes those three keys from a `WriteNoteIntent` on slot 0, ignoring the
title. Text over `SIGN_TEXT_MAX` (80) is refused with the limit and the length,
never truncated; empty text blanks all three keys. One `ObjectChange` per
changed key, so replay, chunks and the viewer need no new code. Contract:
[docs/08_building.md](../docs/08_building.md), "Signs".

### Dismantling and resting

An `ExtractIntent` aimed at a `BUILDING_KINDS` object dismantles it:
`DISMANTLE_WORK` work units, one per action, no tool bonus, progress in the
object's `progress` state key, `ObjectRemoved` plus one item back to whoever
lands the last blow. `RestIntent` heals `REST_HEAL` on a bed on or next to the
entity's tile; one rester per bed per tick (smallest entity id wins) and only
while food is above zero. The rest phase sits beside eat in `process_tick`.

## The Day, Fatigue and Sleep (docs/10_metal_and_sleep.md)

Contract: [docs/10_metal_and_sleep.md](../docs/10_metal_and_sleep.md),
sections 3 and 4.

### The clock

`World.day_length_ticks` (config `[world] day_length_ticks`, default 300)
drives `World.clock` -> `WorldClock(day, tick_of_day, day_length, night)`;
night starts at `night_start_tick()`, two thirds through the day. The clock
rides along with the tick everywhere it is reported: `Observation.clock`, the
viewer's `snapshot` and `tick_completed` messages, the recorded `tick` record
and the replay server's copies of both. `meta.json` records
`day_length_ticks` so a replay of an old run still has a clock.

### `sleep.py`

`Entity` gains `fatigue`, `max_fatigue`, `asleep`, `sleeping_on` (bed object
id, `""` for the ground) and `collapsed`. Wolves have no body clock: they
never tire and never sleep.

Two phases, both in `sleep.py`:

- `process_wake_phase` then `process_sleep_phase` (after movement and every
  action phase; the order is two adjacent rows of `MECHANICS`). One sleeper
  per bed, smallest entity id wins, as everywhere else. `wake` is the one
  mechanic with `allowed_while_asleep=True`, which is also what lets a sleeper
  submit it at all.
- `process_fatigue_phase` (beside `process_food_phase`) accumulates fatigue
  for the awake, recovers it for sleepers at the bed/ground x night/day rate,
  heals bed sleepers, collapses anyone at max fatigue and wakes sleepers whose
  reason to sleep has gone — except inside a new-moon still window, where both
  the `wake` mechanic and `_wake_reason` refuse (see the new moon below).

Sleep is a *state*: `TickContext.submit_intent` refuses every intent that is
not `mechanics.sleeper_may_submit` from a sleeper with reason `asleep`, and `tick._living_subset` repeats
the guard for intents the world injects itself. Waking on damage reads
`TickEvents.damage_events` in the fatigue phase rather than hooking
`combat.apply_damage`, which keeps combat unaware of sleep and makes
starvation damage (applied in the food phase, just before) behave the same.

**Behaviour contracts.**

- **The hungry wake.** `sleep.HUNGRY_WAKE_FOOD` (20) is one number read both
  ways: `_apply_sleep_intents` refuses a settler at or below it (`"too hungry
  to sleep: food F, and a sleeper wakes at food 20"`) and `_wake_reason`
  returns `hungry` when a non-collapsed sleeper falls to it. Waking ends the
  sleep, so it fires at most once per sleep; a collapsed sleeper ignores hunger
  entirely.
- **A minimum to lie down.** `sleep.MIN_SLEEP_FATIGUE` (20):
  `_apply_sleep_intents` refuses a settler below it with `"not tired enough to
  sleep: fatigue F, and sleep needs fatigue 20"`. Collapse at `max_fatigue` is
  unaffected.
- **Recovery is a rate, not an interval.** `sleep.recovery_rate(on_bed, night)`
  returns `(points, ticks)` and `_recover_fatigue` sheds `points` on every tick
  divisible by `ticks`. The bed stays strictly ahead of the ground in both
  periods:

| | night | day |
|---|---|---|
| `BED_*_RECOVERY` | (1, 1) = 1.00/tick | (2, 3) = 0.67/tick |
| `GROUND_*_RECOVERY` | (2, 3) = 0.67/tick | (1, 2) = 0.50/tick |

`HUNGRY_WAKE_FOOD`, `MIN_SLEEP_FATIGUE` and the recovery rates are
`bobgame_rules.body`; the agent's prompts and option strings are generated from
the same constants, so there is nothing to copy. Jev is offered no `sleep:`
option below the floor.

`is_tired(entity)` (fatigue >= 60) is the one predicate other modules use:
`stats.process_health_regen` skips the tired, and extraction and combat apply
their penalties through it.

## The New Moon, Saves and Resume (docs/14_new_moon_and_saves.md)

Contract: [docs/14_new_moon_and_saves.md](../docs/14_new_moon_and_saves.md).
Three `[world]` settings, validated in `config.py`: `new_moon_every_days`
(default 0, must be >= 0; 0 means the world never has a new moon and never
saves — `hamlet.toml` sets 3), `save_on_new_moon` (default true) and
`save_wait_seconds` (default 180, must be >= 1).

### The physics (`moon.py`)

The night of day `d` is a new moon when `(d + 1) % every_days == 0`. At
`night_start_tick(day_length)` exactly, `apply_new_moon` puts every living,
awake, non-wolf entity to sleep where it stands (lowest-id free bed on or next
to its tile, else the ground) and `conversations.close_all_conversations` closes
every open conversation with end reason `new_moon`. For the next
`NEW_MOON_STILL_TICKS` (6) ticks `is_still(world)` holds: `sleep.py` refuses
every wake and `stats.py` holds respawns that fall due. `wolves_lie_low(world)`
holds from the forced sleep to dawn, so no wolf hunts or arrives that night.
The constants are `bobgame_rules.clock`. `moon.py` is imported *inside* the
functions in `sleep.py`, `stats.py` and `state/world.py`, because it imports
them back.

`WorldClock` (`state/models.py`, `ClockState` in the proto, `viewer_payload`)
carries `new_moon_tonight`, `next_new_moon_day` (-1 when there is no new moon)
— both computed in `World.clock` — and `save_tick`, which is 0 except in the
observation of the one tick a save is taken on, where `observation_service`
copies it from `TickContext.save_tick`.

### Taking the save

`SaveCoordinator` is built by `WorldServer` (it needs the tick loop's wolf
simulator). `pending_save_tick()` is non-zero at `night_start +
NEW_MOON_SAVE_OFFSET` (3) of a new-moon night, when saving is on, the run is
recorded and the tick is not `SaveSettings.suppress_tick`. `TickLoop.run` asks
it once per tick *before* the observations: `prepare()` makes
`saves/tick-<T>/agents/`, the observations go out carrying `save_tick`, then
`wait_and_write()` polls for one `agents/<entity_id>.json.gz` per non-wolf
entity and restarts the intent deadline, so the pause costs no thinking time.
On a timeout it logs `save_abandoned` naming who did not report, renames the
directory `tick-<T>.abandoned` keeping what is there, and the run carries on: a
run is never blocked by a save.

The layout is `runs/<run>/saves/tick-<T>/` with `agents/<id>.json.gz`,
`world.json` (clock, config, entities, wolf RNG state), `objects.jsonl.gz` and
`complete.json` **last** — a directory without it is not a save. The world's
two data files are written `.partial` and renamed. Loading refuses an unknown
`format_version` (`SNAPSHOT_FORMAT_VERSION` = 1) and a map whose sha256 has
moved; `restore_world` needs an empty world with terrain in it already and
rebuilds every derived index as objects and entities go back, dead ones
unplaced.

### Resuming

```bash
uv run python -m world.server --resume runs/<run>/saves/tick-1234 \
    --parent-run-id 20260920-030501-hamlet
```

`cli._resume` calls `bootstrap.load_resume` (read `world.json`, check the map
hash, load that map, restore into it); the config in the snapshot wins over the
TOML on disk. The resume gets a **new** run id and run directory, with
`--parent-run-id` (defaulting to the snapshot's run id) and `resumed_from_tick`
in the new `meta.json`, and `suppress_tick` set to the resumed tick so it does
not immediately ask for a save it already has. `run_server` waits for every
`snapshot.settler_ids()` to connect before the first tick; if they do not, it
raises `ResumeStartupError` and the CLI exits 1.

## Conversations and Giving (docs/09_conversation_and_reflex.md)

Contract: [docs/09_conversation_and_reflex.md](../docs/09_conversation_and_reflex.md),
sections 2 and 3. The world owns the rules; the agents own who talks and when.

### Conversations (`conversations.py`)

A conversation is a `WorldObject` of type `conversation` (id `conv_<n>`) on its
**anchor tile**. It blocks nobody, is in neither building layer and is not
extractable, so it needs no entry in the `items.py` blocking or placement maps —
but because it is *not* a ground-layer kind, `process_place_phase` already
refuses a structure on its tile.

State keys (all strings, see the contract): `participants` (JSON list in join
order, opener first), `speaker`, `turn_started`, `opened_tick`, `opened_by`,
`utterances`, `passes`, `transcript` (last 12 lines). Constants live in
`bobgame_rules.social` (`CONVERSATION*`), re-exported by `items.py`.

`process_conversation_phase` runs **once per tick after movement and combat**:
it applies `open`, `hail`, `join`, `speak`, `pass` and `leave` in that order (each group
sorted by entity id, so conflicts resolve lexicographically like every other
conflict), then runs the lifecycle for every conversation — drop participants
who died or walked out of adjacency, move the turn on when the speaker goes,
count an unused turn as a pass after `CONVERSATION_TURN_TICKS`, and close on a
full round of passes, the utterance cap, dropping below two after having had
two, or the lonely timeout.

Every state write goes through `events.commit_object`, which emits one
`ObjectChange` per changed key, so the existing object machinery carries conversations to agent
observations, the viewer WebSocket, the recorder and the replay server with no
new code on those paths.

Utterances now carry a `conversation_id`: the opening line goes out on `local`,
`speak` lines on the `conversation` channel (earshot = the `local` radius, so
bystanders overhear). `conversation` is in `AUDIBLE_CHANNELS` but deliberately
**not** in `SAY_CHANNELS`, so only a `ConverseIntent` can put a line on it.

### Hailing (docs/09 section 9)

`converse` action `hail` (with `target_entity_id` and `text`) needs no
invitation: the hailer must be adjacent to a living, awake, unseated settler
that is out of its hail cooldown, and there must be a free shared anchor
(`_shared_anchor`, as for `accept`). It creates the conversation with
`participants = [hailer, target]`, `opened_by` the hailer, its line as
transcript entry 0 and the **target** speaking first, emits that line as a
`local` utterance with the conversation id, and acts twice: `hail conv_N
<target>` and `hailed conv_N <hailer>`. `HAIL_COOLDOWN_TICKS` (60) is enforced
through `Entity.last_conversation_end_tick`, stamped in `_remove_participants`
and `_close` for everyone whose seat ends; it is world-only state and is not in
the proto, the viewer payload or the recording.

### Giving (`containers.process_give_phase`)

`GiveIntent` moves items straight between inventories when the target is a
living non-wolf entity that is adjacent or seated in the same conversation
(which is how participants reach across the anchor tile). A wielded kind that
runs out is unequipped. The phase runs just before the conversation phase, so a
hand-over still works on the tick a conversation closes. The receiving agent
already sees the giver's `EntityActed` event: `_build_events` shows any action
whose actor is within `VIEW_RADIUS`.

## Terrain Objects and the Settlement Site (docs/08_building.md)

### Object placement (`terrain/objects.py`)

Every pass is a vectorised Bernoulli draw against a probability field built
from `PlacementFields` (floor, forest density, ridged noise, slope, and four
distance fields: any water, ocean, fresh water, mountains). Noise masks go
through `uniformise()` (z-score -> normal CDF) so a threshold of `t` leaves
about `1 - t` of the map above it; tune thresholds as area fractions.

- **Trees**: `canopy_field` = broad forest regions x grove-scale noise through a
  narrow smoothstep: dense stands, clearings, soft edges, copses in the open.
  Trees thin toward the *ocean* only; they come down to river and lake banks.
  Outcrops suppress trees, so stony ground opens glades.
- **Rocks**: `outcrop_field` = patch noise x ridged noise, plus patchy scree at
  mountain feet, plus rare strays. Size follows the field: boulders at the
  heart of an outcrop, small stones at the rim.
- **Bushes**: thickets along canopy edges, in a band back from water.
- **Reeds**: bank tiles within `reed_bank_width` of fresh water and in lake/ford
  shallows, in stands; never near the ocean. Reeds can sit on shallow water.
- **Clay**: tight clusters 2-7 tiles back from fresh water, on grass/dirt.
- One natural object per tile. Order: reeds, clay, trees, rocks, bushes.

Fresh water = rivers + fords + lakes; `split_ocean_and_lakes` labels open water
and calls anything not touching the map border a lake.

### Terrain invariants

- `priority_flood_fill` adds `FILL_EPSILON` per step, so filled flats still
  drain. Without it rivers died a few hundred tiles from their source.
- `_apply_mountain_cap` keeps the highest-scoring candidates (it used to take a
  random subset with the global numpy RNG: one-tile mountains, not reproducible).
- Rivers: 6-9 per island, sources >= 150 tiles from open water, >= 400 apart.

- **Ore veins** (docs/10, section 5): `copper_vein` and `iron_vein` in clusters
  of 3-8 inside outcrops on high ground (`vein_suitability` = outcrop x
  `highland_field` x a slow ore-bearing noise), so most outcrops are barren and
  the metal sits in a few inland districts. Densities are per million tiles
  (9.4 copper, 5.0 iron: about 150 and 80 on the island). Unlike every other
  pass this one places whole clusters around chosen seed tiles, at least
  `vein_cluster_spacing` apart.

### Settlement site (`settlement.py`)

`select_site` tests **every tile** exactly with integral-image box sums (about
2.5 s on 4000x4000); `find_settlement_site` is the `World` wrapper. Requirements
are the constants at the top of the module (`MINIMUMS`, `CLEARING_SIZE`,
`MIN_LAND_FRACTION`, `ORE_MINIMUMS`). Score = min capped supply ratio + 0.3 x
mean ratio + openness of the 27 x 27 square; ties go to the smallest (y, x).

Ore only gates eligibility, never the score, and it is counted in the **ring**
between `ORE_EXCLUSION_RADIUS` (60) and `ORE_SEARCH_RADIUS` (200) of the centre.
That is what lets the generator arrange ore around a site it has already
chosen: `objects.fit_ore_to_settlement` picks the site with `require_ore=False`,
adds a copper and an iron cluster in the ring if the random placement left the
site short, deletes every vein within 60 tiles, and then checks that the finder
still picks the same tile on the saved map (it raises if it does not).

### Regenerating the island

```bash
cd world && uv run python -m world.terrain --seed 12345 -o saves/island.npz   # ~3 min
uv run --with pillow python ../tools/visualize_world.py ../saves/island.npz out.png \
    --crop 1539,974,150 --scale 8 --mark 1539,974
```

With seed 12345 the settlement centre is (1539, 974): a lakeside clearing on a
neck of land between two lakes, forest behind, an outcrop and a mountain to the
north-east. The map carries 151 copper and 87 iron veins; the nearest copper is
187 tiles from the centre and the nearest iron 121 (the iron is the home
district the generator added).
