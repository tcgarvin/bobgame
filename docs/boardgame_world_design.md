# 2D World With gRPC-Controlled LLM Agents
*Design document (initial implementation)*  
*Date: 2026-01-02*

## 1. Goals and non-goals

### Goals
- A **single authoritative World Runtime** runs a tick-based simulation and exposes a **gRPC API**.
- **Agents are external processes** (their own codebases) that connect to the World Runtime via gRPC, **lease** control of a single entity, receive observations, and submit intents.
- A **Runner** (single process in the initial implementation) handles:
  - discovering which entities need agents,
  - policy/config mapping (which agent implementation runs which entity),
  - launching/monitoring agent subprocesses,
  - restarting agents when they crash or misbehave.
- A **Viewer** can watch live and replay runs from logs.
- Simulation is vaguely rouglike in mechanical structure
  - positions are **tile centers** (no float positions in world state),
  - movement is **one tile per tick** (instant from world perspective),
  - “claim destinations → resolve conflicts → enact” pattern.

### Non-goals (initially)
- High-frequency (10–60 Hz) simulation.
- Continuous physics, substeps, or smooth movement as world state.
- Deterministic replay via re-simulation. Replay is **playback** from logged results/events.
- Perfect observation minimization or prioritization. (We will cap by geometry/LOS, but not build elaborate salience scoring yet.)

---

## 2. System components

### 2.1 World Runtime (authoritative)
Responsibilities:
- Owns the tick loop, world state, entity lifecycle.
- Resolves intents deterministically per tick.
- Enforces leases (exclusive control).
- Produces:
  - per-entity observations,
  - per-tick results/events for viewer and logging.

Key property: **World Runtime never trusts agents**; it validates every intent.

### 2.2 Runner (policy mapping + process supervisor)
Initial implementation is a **single Runner** that manages multiple entities/agents.

Responsibilities:
- Connects to World Runtime to discover controllable entities.
- Applies policy/config mapping to decide which agent to run for each entity.
- Launches agent subprocesses (zero arguments), sets environment/config.
- Monitors agent subprocess health (exit code, liveliness, log patterns if desired).
- Restarts agents with backoff and optional fallback assignments.

Runner **does not proxy** observation/action traffic. Agents talk directly to the World Runtime via gRPC.

### 2.3 Agent (per controlled entity)
Responsibilities:
- Connect to World Runtime via gRPC.
- Acquire and renew a lease for a single entity.
- Consume tick and observation streams.
- Submit one intent per tick (or explicit Wait).
- Maintain internal subroutines/options locally (LLM executive is inside agent; the world sees only intents).

### 2.4 Viewer (live + replay)
Responsibilities:
- Live mode: subscribe to a read-only world stream of **viewer events / snapshots**.
- Replay mode: read Parquet logs and reproduce the same visualization.
- Filters: by actor_id and/or region, event types.

Viewer is strictly **read-only**.

---

## 3. Tick model

### 3.1 Timing
- Tick rate: **1 Hz** (1000 ms per tick).
- Intent deadline: **500 ms** after tick start.
- World computation budget: remaining **~500 ms** to resolve and emit outputs.

World provides explicit tick events so agents do not infer time from observation cadence.

### 3.2 Tick phases
For tick `T`:

1. **TickStart(T)**
2. Accept intents until **deadline** (`TickStart + 500ms`).
3. Validate intents (lease valid, correct tick, legal action).
4. **Movement phase**
   - gather move claims,
   - resolve conflicts deterministically,
   - apply movement.
5. **Action phase**
   - resolve non-move actions (pickup/use/say/wait),
   - apply effects (including partial progress actions).
6. Emit:
   - observations for tick `T+1`,
   - viewer events/results for tick `T`,
   - logging records for tick `T`.

Agents that fail to submit an intent by the deadline are treated as **Wait()** for that tick.

---

## 4. Spatial model

### 4.1 World grid
- Discrete 2D grid of tiles.
- Entities occupy tile centers; world state stores `(x, y)` tile coordinates (integers).
- Movement is discrete: one step per tick.

### 4.2 Passability and occlusion
Each tile has:
- `walkable: bool`
- `opaque: bool` (blocks line-of-sight)
- optional metadata (floor type, biome, hazard tags)

Objects (doors, furniture) may modify walkability/opacity depending on state.

---

## 5. Movement and conflict resolution

### 5.1 Allowed moves
- Cardinal: N, S, E, W
- Diagonal: NE, NW, SE, SW

### 5.2 Diagonal rule (v1 default)
To reduce corner-cutting ambiguity:

**A diagonal move is allowed only if BOTH adjacent cardinal tiles are walkable.**  
Example: moving NE requires N and E tiles to be walkable.

(If this is too strict later, it can be relaxed as a versioned rule.)

### 5.3 Claim → resolve → enact
Each entity can submit at most one movement intent per tick.

#### Phase A: claim
Each entity claims a destination tile (or no move). Claims into:
- non-walkable tiles,
- out-of-bounds,
- illegal diagonals (per 5.2),
are rejected immediately (entity stays in place).

#### Phase B: resolve conflicts (v1 simple)
Conflict types and v1 handling:
- **Same destination claimed by multiple**: winner chosen by stable priority; losers stay.
- **Swaps (A→B and B→A)**: disallowed in v1; both fail and stay.
- **Cycles (A→B→C→A)**: disallowed in v1; all participants fail and stay.
- **Destination occupied by a non-moving entity**: move fails; entity stays.

Stable priority:
- default: lexicographic sort by `entity_id` (or numeric id).

#### Phase C: enact
Apply all winning moves simultaneously.

---

## 6. Action model (MVP)

### 6.1 Action types
Per tick, an entity submits exactly one intent:
- `Move(dir)` where `dir ∈ {N,NE,E,SE,S,SW,W,NW}`
- `Pickup(kind, amount?)` (initially non-unique items; pickup from current tile)
- `Use(kind)` (e.g., eat berries; must exist in inventory)
- `Say(text, channel="local")`
- `Wait()`

### 6.2 Action ordering
- Movement resolves first.
- Other actions resolve after movement, using the post-move position.

### 6.3 Partial-effect actions
Some actions require multiple ticks, represented via world-side progress counters, e.g.:
- chopping a tree: progress increments per action until completion
- opening a heavy chest: requires N interaction ticks

Implementation pattern:
- objects have `work_remaining` or `progress` state
- each relevant action applies a deterministic delta
- viewer sees repeated attempts as repeated `ActionResult` rows

### 6.4 Inventory model (non-unique now, unique later)
Initial:
- inventory is a multiset of `{kind, quantity}`.

Future-compatible extension:
- add optional unique `item_id` and/or unique objects `object_id`.
- action schemas allow either:
  - `(kind, quantity)` **or**
  - `(item_id)` when uniqueness exists.

Versioning ensures older agents continue to work in a “non-unique world”.

---

## 7. Observation model

### 7.1 Geometry cap
Observation is limited to:
- a radius `R` in tiles around the entity,
- filtered by **line-of-sight (LOS)**.

`R` is configurable (e.g., 8–12 for MVP).

### 7.2 LOS implementation (v1)
- Determine visibility of tiles using a discrete ray algorithm (e.g., Bresenham) from entity tile to candidate tile.
- Opaque tiles block LOS.
- Entities/objects are visible if their tile is visible.

### 7.3 Observation contents
An `Observation` for entity `E` at tick `T` includes:

- `tick_id`
- `deadline_ms` (time remaining for intent submission; agents can treat as advisory)
- `self` state:
  - tile position `(x, y)`
  - status flags (awake/asleep/incapacitated)
  - inventory summary
- `visible_tiles`:
  - list/bitmask of visible tiles within radius
  - optional tile metadata (floor type, walkable)
- `visible_entities` (full records for currently visible entities):
  - `entity_id`, position, type/tags, basic status
- `visible_objects` (doors/trees/chests/etc., as applicable)
- `events` (deltas since last tick):
  - entity enters visibility
  - entity leaves visibility
  - visible entity moved
  - visible entity acted (action results)
  - local utterances heard (for speech bubbles)

**Note:** v1 can simply send full visible lists plus an events list. This is redundant but easy to implement. Later versions can become delta-only for bandwidth.

### 7.4 Event semantics
Events are **observer-relative**. If an entity is not visible, its actions are not included (unless explicitly heard via “speech/hearing” rules).

---

## 8. Speech model

### 8.1 Channels
MVP:
- `local`: heard within a radius `H` around the speaker (configurable; can equal observation radius initially).

Speech is not blocked by LOS in v1 (hearing is radius-based). If you want realism later, add occlusion attenuation as a versioned feature.

### 8.2 Delivery
- When an entity says something, nearby entities receive an `utterance` event in their next observation.
- Viewer receives a corresponding utterance event for speech bubbles.

---

## 9. Control leases

### 9.1 Purpose
Prevent multiple agents controlling the same entity, and ensure safe recovery on crashes.

### 9.2 Lease policy (initial)
- TTL: **10 seconds**
- Renew: recommended **every tick** (1 Hz).
- Intents must include a valid `lease_id`.
- World rejects intents with missing/expired leases.

### 9.3 Expiry behavior (fallback)
If a lease expires or the entity is otherwise uncontrolled:
- Entity **stands still**.
- If the tile contains a bed or chair (or special furniture tag), entity transitions to **sleep/pass-out** state.
- Viewer should show a distinct “uncontrolled/asleep” state.

---

## 10. gRPC API (initial surface)

> The intent is to keep the v1 API small. Fields below are representative; implement with protobuf.

### 10.1 Tick service
- `StreamTicks(Empty) -> stream TickEvent`
  - `tick_id`
  - `tick_start_server_time`
  - `intent_deadline_server_time`
  - `tick_duration_ms` (1000)
  - optional: `world_version`

### 10.2 Entity discovery (Runner use)
- `ListControllableEntities(Empty) -> ControllableEntities`
  - list of `entity_id`, tags, type, spawn_time
- Optionally later:
  - `StreamEntityLifecycle(Empty) -> stream EntityLifecycleEvent`

### 10.3 Lease service (Agent use)
- `AcquireLease(entity_id, controller_id) -> Lease`
- `RenewLease(lease_id) -> Lease`
- `ReleaseLease(lease_id) -> Empty` (optional; TTL expiry is sufficient)

### 10.4 Observation service (Agent use)
- `StreamObservations(lease_id, entity_id) -> stream Observation`
  - Observations are keyed by tick.

### 10.5 Action service (Agent use)
- `SubmitIntent(lease_id, entity_id, tick_id, Intent) -> Ack`
  - `accepted: bool`
  - `reason: string` (e.g., late_tick, invalid_lease, illegal_action)

### 10.6 Viewer service (Viewer use, read-only)
- `StreamViewerEvents(ViewFilter) -> stream ViewerEvent`
- `GetViewerSnapshot(ViewFilter) -> ViewerSnapshot` (optional)

Viewer events should be derived from authoritative results (not from agent intents).

---

## 11. Intent schema (conceptual)

```json
{ "type": "Move", "dir": "NE" }

{ "type": "Pickup", "kind": "berries", "amount": 1 }

{ "type": "Use", "kind": "berries", "amount": 1 }

{ "type": "Say", "channel": "local", "text": "Hello." }

{ "type": "Wait" }
```

Notes:
- `amount` is optional; default 1.
- Future unique items:
  - `Pickup` may accept `item_id` instead of `kind`.
  - `Use` may accept `item_id`.

---

## 12. Runner policy/config mapping

### 12.1 Discovery loop
Runner:
1. Connects to World Runtime.
2. Fetches controllable entities periodically (or subscribes to lifecycle stream).
3. For each entity needing an agent:
   - choose an agent spec from config,
   - launch agent subprocess (zero args),
   - provide environment/config pointing to world and entity id.

### 12.2 Agent launch contract (zero args)
Runner sets env vars (suggested):
- `WORLD_ADDR=host:port`
- `ENTITY_ID=entity:123`
- `CONTROLLER_ID=runnerA:entity:123:agentX:v0.1`
- `AGENT_CONFIG_PATH=/run/agents/entity_123/config.json`
- `PYTHONUNBUFFERED=1`

Agent reads config and performs gRPC operations itself.

### 12.3 Mapping config (example YAML)
```yaml
world_addr: "127.0.0.1:50051"

defaults:
  restart:
    max_restarts_per_10m: 10
    backoff_ms: [250, 500, 1000, 2000, 5000]

agents:
  berry_eater:
    cmd: ["python", "-m", "agents.berry_eater"]
    env:
      MAX_TOKENS: "800"

assignments:
  - match:
      tags: ["player"]
    use: berry_eater
  - match:
      tags: ["npc"]
    use: berry_eater
```

### 12.4 Runner failure policy
- If an agent crashes repeatedly, runner may:
  - switch to a fallback agent spec (optional),
  - or leave entity uncontrolled (world fallback applies).

---

## 13. Logging and replay (Parquet-first)

### 13.1 Philosophy
Replay is **playback of world results**, not re-simulation. Therefore logs must capture the minimal state transitions required to reproduce what happened visually and narratively.

### 13.2 Parquet datasets (partitioned by run_id)
Store as directory structure:
- `runs/<run_id>/ticks.parquet`
- `runs/<run_id>/actor_state.parquet`
- `runs/<run_id>/action_results.parquet`
- `runs/<run_id>/utterances.parquet`
- `runs/<run_id>/object_deltas.parquet`
- `runs/<run_id>/meta.json`

### 13.3 Tables (suggested schemas)

#### ticks
- `run_id: string`
- `tick_id: int64`
- `sim_time_ms: int64`
- `world_version: string`

#### actor_state (keyframe each tick)
- `run_id: string`
- `tick_id: int64`
- `actor_id: string`
- `x: int32`
- `y: int32`
- `status_bits: int32`
- `facing: int8` (optional; 0..7)

#### action_results
- `run_id: string`
- `tick_id: int64`
- `actor_id: string`
- `action_type: string`
- `target_id: string?`
- `ok: bool`
- `reason: string?`
- `effects_json: string?`

#### utterances
- `run_id: string`
- `tick_id: int64`
- `actor_id: string`
- `channel: string`
- `text: string`
- `x: int32`
- `y: int32`

#### object_deltas
- `run_id: string`
- `tick_id: int64`
- `object_id: string`
- `field: string`
- `new_value_json: string`

---

## 14. Viewer design (MVP)

### 14.1 Live mode
- Subscribe to `StreamViewerEvents` (default: wide open filter).
- Render:
  - tile map
  - actor sprites at tile centers
  - animate movement by interpolating between tick positions
  - speech bubbles on utterance events

### 14.2 Replay mode
- Load Parquet tables for `run_id`.
- Step ticks and apply:
  - actor positions from `actor_state`
  - utterances from `utterances`
  - object changes from `object_deltas`
- Use the same interpolation rules as live mode.

---

## 15. Versioning
- `world_version`: semantic version for simulation rules.
- `api_version`: protobuf service version.
- `log_schema_version`: Parquet schema version.

---

## 16. Testing strategy (Parquet-first)

Core tests:
- Movement conflict: stable winner selection, swaps/cycles disallowed.
- LOS: enter/leave visibility around walls/doors.
- Actions: pickup/use and partial progress mechanics.
- Logging: Parquet row sequences match expected golden scenarios.
