# Implementation Plan

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
| | 6: Runner & Process Management |
| | 7: Logging & Replay |
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
Milestone 6: Runner & Process Management             ← NEXT
     │
     ▼
Milestone 7: Logging & Replay
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
- [ ] `object_type: "bush"` with `berry_count` state (0-5)
- [ ] Regeneration: +1 berry per N ticks when not full
- [ ] Does NOT block movement (can walk through)

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

## Milestone 6: Runner & Process Management

**Goal**: Runner orchestrates multiple agent processes

### Tasks

#### 6.1 Runner Core
- [ ] Entity discovery from world
- [ ] YAML config parsing
- [ ] Agent process launching
- [ ] Environment variable setup

#### 6.2 Process Management
- [ ] Health monitoring via lease renewal
- [ ] Restart crashed agents with backoff
- [ ] Graceful shutdown

**Deliverable**: Runner manages multiple agent processes

---

## Milestone 7: Logging & Replay

**Goal**: Parquet logging and replay viewer

### Tasks

#### 7.1 Parquet Writer
- [ ] `ticks.parquet` - tick timing
- [ ] `entity_state.parquet` - position, inventory per tick
- [ ] `actions.parquet` - intents and results
- [ ] `objects.parquet` - object state changes

#### 7.2 Run Management
- [ ] `run_id` generation
- [ ] Directory structure: `runs/<run_id>/`
- [ ] `meta.json` with run metadata

#### 7.3 Replay Mode
- [ ] Viewer loads Parquet files
- [ ] Tick stepping (forward, back, play, pause)
- [ ] Seek to tick
- [ ] Same rendering as live mode

**Deliverable**: Full run can be replayed from logs

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
- **Hunger/Health**: Stats affected by eating
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
