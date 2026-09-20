# Implementation Plan

**Status note (2026-09-20).** This is the original milestone plan and is kept
as the record of how the system was built; it is not a description of the
system as it is. Milestones 0-6 are done. Milestone 7 (run recording and
replay) is largely done — every run is recorded to `runs/<run_id>/` as gzip
JSONL and a replay server serves it to the viewer with seeking, playback and
deep links; the contract is [07_replay.md](07_replay.md). Milestone 8 (LLM
agent integration) was superseded by `agents.jev_agent`, whose contract is
[05_jev_agents_design.md](05_jev_agents_design.md). The commands and agent
names below refer to scenarios and a `random_agent` that no longer exist; there
is one scenario, `hamlet`. See [../CLAUDE.md](../CLAUDE.md) for the current
system and [../CHANGELOG.md](../CHANGELOG.md) for everything built after
milestone 6.

This plan is organized into incremental milestones. Each milestone produces a working, demonstrable artifact.

## Current Status

| Status | Milestone |
|--------|-----------|
| ✓ | 0: Project Scaffolding |
| ✓ | 1: Static Viewer |
| ✓ | 2: World Core |
| ✓ | 3: gRPC API & Basic Agent |
| ✓ | 4: Live Viewer Integration |
| ✓ | 5a: Berry Foraging Foundation |
| ✓ | 5b: Simple Agent & Multi-Agent |
| ✓ | 6: Runner & Process Management |
| ◐ | 7: Run Recording & Replay (tooling done; world/agents/viewer in progress) |
| | 8: LLM Agent Integration |

---

## Overview

```
Milestone 0: Project Scaffolding & Asset Prep        ✓
     │
     ▼
Milestone 1: Static Viewer (Phaser renders tiles)    ✓
     │
     ▼
Milestone 2: World Core (tick loop, state, movement) ✓
     │
     ▼
Milestone 3: gRPC API & Basic Agent                  ✓
     │
     ▼
Milestone 4: Live Viewer Integration                 ✓
     │
     ▼
Milestone 5a: Berry Foraging (objects, inventory, collect, eat) ✓
     │
     ▼
Milestone 5b: Simple Agent & Multi-Agent             ✓
     │
     ▼
Milestone 6: Runner & Process Management             ✓
     │
     ▼
Milestone 7: Run Recording & Replay                  ← IN PROGRESS
     │
     ▼
Milestone 8: LLM Agent Integration
```

---

## Completed Milestones (0-4)

<details>
<summary><strong>Milestone 0: Project Scaffolding</strong> - Working project structure with processed tilesets</summary>

- Python projects: `world/`, `runner/`, `agents/` with uv
- Viewer: Vite + TypeScript + Phaser 3
- DawnLike tileset processing with atlas manifests
- See `04_tileset_preparation.md` for tileset details
</details>

<details>
<summary><strong>Milestone 1: Static Viewer</strong> - Render tile map with entities using Phaser</summary>

- TypeScript types for map/tile data
- Floor and wall tile rendering
- Entity sprites with 2-frame animation
- Camera pan (WASD) and zoom (scroll wheel)
</details>

<details>
<summary><strong>Milestone 2: World Core</strong> - Tick-based simulation with movement (84 tests)</summary>

- Core data classes: `World`, `Entity`, `Tile`, `Position` in `world/src/world/`
- Async tick loop at 1 Hz with 500ms deadline
- Claim-resolve-enact movement pipeline
- Conflict resolution: swaps fail, cycles fail, lexicographic winner for same destination
- See `world/CLAUDE.md` for architecture decisions
</details>

<details>
<summary><strong>Milestone 3: gRPC API & Basic Agent</strong> - Agent controls entity via gRPC (126 tests)</summary>

- Proto services: TickService, LeaseService, ObservationService, ActionService, EntityDiscoveryService
- Lease management with 30s expiry
- RandomAgent in `agents/src/agents/random_agent.py`
- See `world/CLAUDE.md` for architecture decisions
</details>

<details>
<summary><strong>Milestone 4: Live Viewer Integration</strong> - Viewer shows real-time world state (137 tests)</summary>

- WebSocket bridge (simpler than gRPC-Web) on port 8765
- JSON messages: `snapshot`, `tick_started`, `tick_completed`
- 60fps entity interpolation with ease-out curve
- Files: `viewer_ws_service.py`, `WebSocketClient.ts`, `WorldState.ts`

Usage:
```bash
./dev.sh  # Or manually:
cd world && uv run python -m world.server --spawn-entity bob:5,5
cd agents && uv run python -m agents.random_agent --entity bob
cd viewer && npm run dev
```
</details>

---

## Milestone 5a: Berry Foraging Foundation

**Goal**: Entities can collect berries from bushes into inventory and eat them

### Tasks

#### 5a.1 Object System
- [ ] `WorldObject` class with object_id, position, object_type, state
- [ ] Object registry in World (`_objects`, `_object_positions`)
- [ ] `get_objects_at(position)` for visibility

#### 5a.2 Bush Object Type
- [x] `object_type: "bush"` with binary `berry_count` state ("0" or "1")
- [x] Regeneration: regrows berry every N ticks when empty
- [x] Does NOT block movement (can walk through)

#### 5a.3 Inventory System
- [ ] Immutable `Inventory` class (multiset: item_type → count)
- [ ] `add()`, `remove()`, `count()`, `has()` methods
- [ ] Add `inventory` field to Entity model
- [ ] Include inventory in proto Observation

#### 5a.4 Collect Action
- [ ] `CollectIntent` targeting object at current position
- [ ] Transfer berries from bush to entity inventory
- [ ] Conflict resolution: first by entity_id wins

#### 5a.5 Eat Action
- [ ] `EatIntent` specifying item type from inventory
- [ ] Remove item from inventory (no gameplay effect yet)

#### 5a.6 Viewer Updates
- [ ] Render bushes (Tree0.png has bush sprites)
- [ ] Show berry count or berry state visually
- [ ] Update on object state changes

**Deliverable**: Entity can collect berries from bush and eat them

---

## Milestone 5b: Simple Agent & Multi-Agent ✓

**Goal**: Two simple agents compete for berries

### Tasks

#### 5b.1 Simple Agent
- [x] Replace RandomAgent with SimpleAgent
- [x] State machine: WANDER → SEEK → COLLECT → EAT
- [x] Wander: Move randomly when no berries visible
- [x] Seek: Path toward visible berries (greedy bee-line)
- [x] Collect: When at bush with berries, collect
- [x] Eat: Consume berries from inventory sometimes

#### 5b.2 Multi-Agent Setup
- [x] World spawns multiple entities (alice, bob)
- [x] Multiple SimpleAgents connect
- [x] Each claims different entity via lease

#### 5b.3 Competition Behavior
- [x] Both agents see same bushes
- [x] Race to reach bushes first
- [x] Conflict resolution decides winner

**Deliverable**: Two agents competing for berry resources

---

## Milestone 6: Runner & Process Management ✓

**Goal**: Runner orchestrates multiple agent processes

### Tasks

#### 6.1 Runner Core
- [x] Entity discovery from world via gRPC EntityDiscoveryService
- [x] TOML config parsing (consistent with world/configs/*.toml)
- [x] Agent process launching via subprocess
- [x] Per-entity agent configuration with default fallback

#### 6.2 Process Management
- [x] Health monitoring via process polling
- [x] Restart crashed agents with exponential backoff
- [x] Graceful shutdown (SIGTERM then SIGKILL)
- [x] Signal handling (SIGINT, SIGTERM)

**Deliverable**: Runner manages multiple agent processes

**Key Files**:
- `runner/src/runner/config.py` - TOML config parsing with Pydantic
- `runner/src/runner/discovery.py` - gRPC entity discovery
- `runner/src/runner/process.py` - AgentProcess wrapper
- `runner/src/runner/manager.py` - ProcessManager with restart logic
- `runner/configs/foraging.toml` - Example config

**Usage**:
```bash
# Using runner directly
cd runner && uv run python -m runner --config configs/foraging.toml

# Or via dev.sh (now uses runner)
./dev.sh
```

---

## Milestone 7: Run Recording & Replay

**Goal**: Every run is recorded to a run directory that a replay server can
serve to the viewer, with seeking, stepping, playback and deep links into any
tick.

**Contract**: [07_replay.md](07_replay.md) — file formats, replay protocol,
deep-link parameters. Read it before touching any of the tracks below.

Not Parquet: the earlier plan named Parquet, but the experiment gets killed
often and a Parquet file is only valid once its footer is written. The
recording is gzip-compressed JSONL, flushed with `Z_SYNC_FLUSH`, so a killed
run still reads up to its last flush. A Parquet export can be added later as
an analysis tool.

### Tasks

#### 7.1 World recording (`world/src/world/recording.py`)
- [ ] Run directory from `BOBGAME_RUN_DIR` / `--run-dir`, else a fresh
      `runs/<run_id>`
- [ ] `meta.json` at start; `finished_at` and `last_tick` at clean stop
- [ ] `world/objects.jsonl.gz` - every object at tick 0
- [ ] `world/ticks.jsonl.gz` - `tick` records (moves, entity updates, object
      deltas, actions, utterances, damage, deaths, respawns, spawns,
      despawns) and `agent_status` records

#### 7.2 Agent tracing (`agents/src/agents/jev_agent/tracelog.py`)
- [ ] Log root from `BOBGAME_RUN_DIR/agents`, else `logs/`
- [ ] `stints.jsonl.gz` - `stint_start`, per-tick Jev record (now with
      `stint_id`, `confidence`, full `probabilities`), `stint_end`
- [ ] `jev_states.jsonl.gz` - the exact state and criteria per Jev call
- [ ] `planner.jsonl.gz` - turns, tool calls, results, reflections, failures

#### 7.3 Replay server (`world/src/world/replay/`)
- [ ] `python -m world.replay --runs-dir ../runs --port 8766`, one server for
      every run
- [ ] Speaks the live viewer protocol, so replay renders with live code
- [ ] `open_run`, `seek`, `step`, `play`, `pause`, `get_agent_detail`,
      `get_run_index`
- [ ] `run_index` event list (deaths, wolf kills, crafts, notes, stints)

#### 7.4 Viewer replay mode
- [ ] Deep links: `?run=&tick=&entity=&x=&y=&zoom=&play=&speed=&panel=&object=`,
      URL kept in sync
- [ ] Transport bar: seek, step, play/pause, speed, events dropdown
- [ ] Object inspector (chests, piles, boards, bushes)
- [ ] Agent panel grows: brief, probabilities, "what Jev saw", planner turn,
      memory notes

#### 7.5 Tooling
- [x] `dev.sh` makes the run id and directory, exports `BOBGAME_RUN_ID` /
      `BOBGAME_RUN_DIR`, points every log at it, maintains `runs/latest`, and
      starts the replay server
- [x] `replay.sh [run_id]` starts just the replay server and the viewer
- [x] `tools/analyze_run.py [run_dir]` reads run directories (default
      `runs/latest`), adds a notable-moments section with deep links and a
      `--json` dump, and still reads the old `logs/` layout

**Deliverable**: a finished run can be opened at any tick from a link, with
every agent's reasoning visible at that tick

---

## Milestone 8: LLM Agent Integration

**Goal**: Claude-powered agents with reasoning

### Tasks

#### 8.1 Observation Formatting
- [ ] Convert observations to text/JSON for LLM
- [ ] Include visible bushes, entities, inventory
- [ ] Memory/context management

#### 8.2 LLM Integration
- [ ] Anthropic API integration
- [ ] Prompt engineering for intent selection
- [ ] Response parsing to intent

#### 8.3 Line-of-Sight (if needed)
- [ ] Bresenham ray casting
- [ ] Visibility filtering
- [ ] Enter/leave visibility events

**Deliverable**: LLM-controlled agents exploring world

---

## Future Extensions (Post-Milestone 8)

These can be added incrementally as needed:

- **Doors**: Toggle open/closed, affect walkability
- **Chests**: Contain items, open to loot
- **Trees**: Multi-tick chopping, yield wood
- **Say Action**: Speech bubbles, local communication
- **Food/Health**: Stats affected by eating
- **Crafting**: Combine items

---

## Testing Strategy

### Unit Tests (per milestone)
- Pure functions: movement validation, conflict resolution, LOS
- Data transformations: observation generation, intent parsing
- Parquet schema validation

### Integration Tests
- gRPC round-trip: submit intent → observe result
- Multi-agent conflicts
- Lease expiry behavior

### Visual Tests
- Manual viewer verification for rendering
- Screenshot comparison (optional)

### Scenario Tests
- Golden file comparison against expected Parquet output
- Deterministic seeds for reproducibility

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| gRPC-Web complexity | Start with REST/WebSocket bridge |
| Tileset processing | Manual JSON manifest as fallback |
| LLM latency > tick deadline | Async observation, deadline-aware prompts |
| Conflict resolution edge cases | Extensive unit tests, fuzzing |
