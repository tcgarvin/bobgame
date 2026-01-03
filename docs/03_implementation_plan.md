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

## Milestone 2: World Core

**Goal**: Tick-based simulation with movement

### Tasks

#### 2.1 World State
- [ ] Define core data classes: `World`, `Entity`, `Tile`, `Position`
- [ ] Grid representation with walkability/opacity
- [ ] Entity registry

#### 2.2 Tick Loop
- [ ] Async tick loop at 1 Hz
- [ ] Tick counter and timing
- [ ] Intent collection with deadline

#### 2.3 Movement System
- [ ] Cardinal and diagonal movement
- [ ] Diagonal blocking rule
- [ ] Claim-resolve-enact pipeline
- [ ] Conflict resolution (priority-based)

#### 2.4 Unit Tests
- [ ] Movement validation tests
- [ ] Conflict resolution tests (same destination, swaps, cycles)
- [ ] Diagonal blocking tests

**Deliverable**: In-memory world that processes movement intents

---

## Milestone 3: gRPC API & Basic Agent

**Goal**: Agent connects and controls entity via gRPC

### Tasks

#### 3.1 Protobuf Definitions
- [ ] Define `world.proto`:
  - `TickService`: `StreamTicks`
  - `LeaseService`: `AcquireLease`, `RenewLease`
  - `ObservationService`: `StreamObservations`
  - `ActionService`: `SubmitIntent`
- [ ] Compile for Python

#### 3.2 gRPC Server
- [ ] Implement tick streaming
- [ ] Implement lease management
- [ ] Implement observation generation (basic, no LOS yet)
- [ ] Implement intent submission

#### 3.3 Simple Test Agent
- [ ] gRPC client connecting to world
- [ ] Acquire lease
- [ ] Receive observations
- [ ] Submit random movement intents

**Deliverable**: Agent moves entity via gRPC

---

## Milestone 4: Live Viewer Integration

**Goal**: Viewer shows real-time world state

### Tasks

#### 4.1 Viewer gRPC Connection
Option A: gRPC-Web with Envoy proxy
Option B: REST/WebSocket bridge (simpler)
- [ ] Implement chosen approach
- [ ] Stream viewer events to frontend

#### 4.2 Viewer Service
- [ ] Add `ViewerService` to proto
- [ ] Implement `StreamViewerEvents`
- [ ] Entity position updates
- [ ] Tick synchronization

#### 4.3 Viewer Updates
- [ ] Connect to event stream
- [ ] Update entity positions on tick
- [ ] Interpolate movement between ticks (smooth animation)

**Deliverable**: Viewer shows live entity movement

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
