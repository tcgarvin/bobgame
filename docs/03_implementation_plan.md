# Implementation Plan

This plan is organized into incremental milestones. Each milestone produces a working, demonstrable artifact.

## Overview

```
Milestone 0: Project Scaffolding & Asset Prep
     │
     ▼
Milestone 1: Static Viewer (Phaser renders tiles)
     │
     ▼
Milestone 2: World Core (tick loop, state, movement)
     │
     ▼
Milestone 3: gRPC API & Basic Agent
     │
     ▼
Milestone 4: Live Viewer Integration
     │
     ▼
Milestone 5: Actions, Inventory, Objects
     │
     ▼
Milestone 6: Runner & Multi-Agent
     │
     ▼
Milestone 7: Logging & Replay
     │
     ▼
Milestone 8: LLM Agent Integration
```

---

## Milestone 0: Project Scaffolding & Asset Preparation

**Goal**: Working project structure with processed tilesets

### Tasks

#### 0.1 Initialize Python Projects
- [ ] Create `world/` with `uv init`, add core dependencies
- [ ] Create `runner/` with `uv init`
- [ ] Create `agents/` with `uv init`
- [ ] Setup shared proto directory

#### 0.2 Initialize Viewer
- [ ] Create Vite + TypeScript project
- [ ] Install Phaser 3
- [ ] Setup development server

#### 0.3 Prepare DawnLike Tileset
The tileset needs processing for Phaser:

- [ ] Create tileset manifest (JSON) mapping sprite names to coordinates
- [ ] Generate texture atlases for efficient loading:
  - `characters.json` + `characters.png`
  - `objects.json` + `objects.png`
  - `items.json` + `items.png`
  - `tiles.json` + `tiles.png` (floors, walls)
- [ ] Write Python script to generate atlas manifests
- [ ] Document sprite naming convention

See `04_tileset_preparation.md` for details.

**Deliverable**: Projects initialize, tilesets load in Phaser test scene

---

## Milestone 1: Static Viewer

**Goal**: Render a tile map with entities using Phaser

### Tasks

#### 1.1 Map Data Structure
- [ ] Define TypeScript types for map/tile data
- [ ] Create hardcoded test map (10x10 room)

#### 1.2 Tile Rendering
- [ ] Load tileset atlas
- [ ] Render floor tiles
- [ ] Render wall tiles with correct autotiling (or manual placement)

#### 1.3 Entity Rendering
- [ ] Load character sprites
- [ ] Place entity sprite on map
- [ ] Basic 2-frame animation toggle

#### 1.4 Camera Controls
- [ ] Pan with arrow keys or WASD
- [ ] Zoom with scroll wheel

**Deliverable**: Static dungeon room with animated character sprite

---

## Milestone 2: World Core ✓

**Goal**: Tick-based simulation with movement

**Status**: Complete (84 tests passing)

### Tasks

#### 2.1 World State
- [x] Define core data classes: `World`, `Entity`, `Tile`, `Position`
- [x] Grid representation with walkability/opacity
- [x] Entity registry

#### 2.2 Tick Loop
- [x] Async tick loop at 1 Hz
- [x] Tick counter and timing
- [x] Intent collection with deadline

#### 2.3 Movement System
- [x] Cardinal and diagonal movement
- [x] Diagonal blocking rule
- [x] Claim-resolve-enact pipeline
- [x] Conflict resolution (priority-based)

#### 2.4 Unit Tests
- [x] Movement validation tests
- [x] Conflict resolution tests (same destination, swaps, cycles)
- [x] Diagonal blocking tests

**Deliverable**: In-memory world that processes movement intents

### Implementation Notes

Files created in `world/src/world/`:
- `types.py` - Position, Direction, constants
- `state.py` - World, Entity, Tile with dual-indexed registry
- `movement.py` - Claim-resolve-enact conflict resolution
- `tick.py` - Async tick loop with deadline handling
- `exceptions.py` - Custom exceptions

See `world/CLAUDE.md` for detailed architecture decisions

---

## Milestone 3: gRPC API & Basic Agent ✓

**Goal**: Agent connects and controls entity via gRPC

**Status**: Complete (126 tests passing)

### Tasks

#### 3.1 Protobuf Definitions
- [x] Define `world.proto`:
  - `TickService`: `StreamTicks`
  - `LeaseService`: `AcquireLease`, `RenewLease`, `ReleaseLease`
  - `ObservationService`: `StreamObservations`
  - `ActionService`: `SubmitIntent`
  - `EntityDiscoveryService`: `ListControllableEntities`
- [x] Compile for Python

#### 3.2 gRPC Server
- [x] Implement tick streaming
- [x] Implement lease management with expiry
- [x] Implement observation generation (basic, no LOS yet)
- [x] Implement intent submission
- [x] Implement entity discovery service

#### 3.3 Simple Test Agent
- [x] gRPC client connecting to world
- [x] Acquire lease
- [x] Receive observations
- [x] Submit random movement intents

**Deliverable**: Agent moves entity via gRPC

### Implementation Notes

Files created in `world/src/world/`:
- `conversion.py` - Proto type conversion functions
- `lease.py` - Lease management with expiry tracking
- `server.py` - Main WorldServer entry point
- `services/` - gRPC service implementations:
  - `lease_service.py` - LeaseServiceServicer
  - `tick_service.py` - TickServiceServicer
  - `action_service.py` - ActionServiceServicer
  - `observation_service.py` - ObservationServiceServicer
  - `discovery_service.py` - EntityDiscoveryServiceServicer

Files created in `agents/src/agents/`:
- `random_agent.py` - RandomAgent class for testing

See `world/CLAUDE.md` for detailed architecture decisions

---

## Milestone 4: Live Viewer Integration ✓

**Goal**: Viewer shows real-time world state

**Status**: Complete (137 tests passing)

### Tasks

#### 4.1 Viewer WebSocket Bridge
Chose WebSocket bridge approach (simpler than gRPC-Web):
- [x] WebSocket server embedded in WorldServer
- [x] JSON message format for events
- [x] Stream viewer events to frontend

#### 4.2 Viewer Service
- [x] Implement `ViewerWebSocketService` (Python)
- [x] Snapshot on connect (entities, world size, tick info)
- [x] `tick_started` and `tick_completed` events
- [x] Entity position updates with move results

#### 4.3 Viewer Updates
- [x] TypeScript WebSocket client with reconnection
- [x] `WorldState` class with interpolation
- [x] Update entity positions on tick
- [x] Smooth 60fps movement interpolation (ease-out curve)

**Deliverable**: Viewer shows live entity movement

### Implementation Notes

Files created in `world/src/world/services/`:
- `viewer_ws_service.py` - WebSocket server with snapshot/event broadcasting

Files created in `viewer/src/network/`:
- `types.ts` - Message type definitions
- `WebSocketClient.ts` - Connection management with reconnection
- `WorldState.ts` - Entity state with interpolation

Modified files:
- `world/src/world/server.py` - Integrated WebSocket service
- `viewer/src/scenes/GameScene.ts` - Connected to live world state

Usage:
```bash
# Start world server (gRPC on 50051, WebSocket on 8765)
cd world && uv run python -m world.server --spawn-entity bob:5,5

# Start random agent
cd agents && uv run python -m agents.random_agent --entity bob

# Start viewer (opens browser)
cd viewer && npm run dev
```

---

## Milestone 5: Actions, Inventory, Objects

**Goal**: Full action model with items

### Tasks

#### 5.1 Inventory System
- [ ] Multiset inventory data structure
- [ ] Inventory in entity state
- [ ] Inventory in observations

#### 5.2 Objects
- [ ] Object types: doors, chests, trees, items-on-ground
- [ ] Object state (open/closed, progress)
- [ ] Objects affect walkability/opacity

#### 5.3 Actions
- [ ] `Pickup` action
- [ ] `Use` action
- [ ] `Say` action (local speech)
- [ ] `Wait` action
- [ ] Partial-progress actions (tree chopping)

#### 5.4 Viewer Updates
- [ ] Render objects
- [ ] Object state changes
- [ ] Speech bubbles

**Deliverable**: Agents can interact with world objects

---

## Milestone 6: Runner & Multi-Agent

**Goal**: Multiple agents managed by runner

### Tasks

#### 6.1 Runner Core
- [ ] Entity discovery from world
- [ ] YAML config parsing
- [ ] Agent process launching
- [ ] Environment variable setup

#### 6.2 Process Management
- [ ] Health monitoring
- [ ] Restart with backoff
- [ ] Fallback handling

#### 6.3 Multi-Entity World
- [ ] Multiple entities in world
- [ ] Separate leases per entity
- [ ] Conflict resolution with multiple movers

**Deliverable**: Runner manages multiple agent processes

---

## Milestone 7: Logging & Replay

**Goal**: Parquet logging and replay viewer

### Tasks

#### 7.1 Parquet Writer
- [ ] `ticks.parquet` logging
- [ ] `actor_state.parquet` logging
- [ ] `action_results.parquet` logging
- [ ] `utterances.parquet` logging
- [ ] `object_deltas.parquet` logging

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

**Goal**: Agents powered by LLMs

### Tasks

#### 8.1 Observation Formatting
- [ ] Convert observations to text/JSON for LLM
- [ ] Include visible entities, objects, events
- [ ] Memory/context management

#### 8.2 LLM Integration
- [ ] Anthropic API integration
- [ ] Prompt engineering for intent selection
- [ ] Response parsing to intent

#### 8.3 Agent Behaviors
- [ ] Simple goal-seeking behavior
- [ ] Memory of past observations
- [ ] Speech generation

#### 8.4 Line-of-Sight
- [ ] Bresenham ray casting
- [ ] Visibility filtering
- [ ] Enter/leave visibility events

**Deliverable**: LLM-controlled agents exploring world

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
