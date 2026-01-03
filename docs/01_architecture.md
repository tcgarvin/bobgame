# Architecture Overview

This document provides a high-level view of the system architecture. For detailed specifications, see `boardgame_world_design.md`.

## System Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              SYSTEM OVERVIEW                            │
└─────────────────────────────────────────────────────────────────────────┘

     ┌──────────────┐
     │    Runner    │  Discovers entities, launches/supervises agents
     │   (Python)   │
     └──────┬───────┘
            │ spawns
            ▼
┌─────────────────────────────────────────┐
│              Agent Processes            │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐   │
│  │ Agent 1 │ │ Agent 2 │ │ Agent N │   │  External processes (LLM-powered)
│  │(Python) │ │(Python) │ │(Python) │   │  Each controls one entity
│  └────┬────┘ └────┬────┘ └────┬────┘   │
└───────┼───────────┼───────────┼────────┘
        │           │           │
        │    gRPC   │           │
        ▼           ▼           ▼
┌─────────────────────────────────────────┐
│           World Runtime                 │
│              (Python)                   │
│                                         │
│  • Tick loop (1 Hz)                     │
│  • World state & entity lifecycle       │
│  • Intent validation & conflict res.    │
│  • Observation generation               │
│  • Parquet logging                      │
└─────────────────┬───────────────────────┘
                  │ gRPC (read-only)
                  ▼
         ┌────────────────┐
         │     Viewer     │
         │   (Phaser 3)   │  Live view & replay
         │   + Python     │
         └────────────────┘
```

## Component Responsibilities

### World Runtime (Python)
The authoritative simulation server:
- **Tick Loop**: 1 Hz (1000ms per tick), 500ms intent deadline
- **State Management**: Grid positions, entities, objects, inventories
- **Conflict Resolution**: Movement claims, action validation
- **Observation Generation**: Per-entity visibility with LOS
- **gRPC API**: Tick stream, lease management, observations, actions
- **Logging**: Parquet files for replay

### Runner (Python)
Process supervisor:
- Discovers controllable entities from World Runtime
- Maps entity tags to agent implementations (YAML config)
- Launches agent subprocesses with environment config
- Monitors health, restarts with backoff

### Agents (Python)
LLM-controlled entity controllers:
- Each agent process controls exactly one entity
- Acquires/renews lease from World Runtime
- Receives observations, submits intents
- Contains all LLM logic internally

### Viewer (Phaser 3 + Python backend)
Visualization layer:
- **Live Mode**: Streams viewer events from World Runtime
- **Replay Mode**: Reads Parquet logs
- Renders tile map, sprites, interpolated movement, speech bubbles

## Data Flow

### Per-Tick Cycle
```
TickStart(T)
    │
    ├─► Agents receive observation for tick T
    │
    ├─► Agents compute intent (LLM call, etc.)
    │
    ├─► Agents submit intent before deadline (T + 500ms)
    │
    ├─► World validates intents
    │
    ├─► Movement phase: claim → resolve conflicts → enact
    │
    ├─► Action phase: pickup/use/say/wait
    │
    ├─► Emit: observations(T+1), viewer events(T), logs(T)
    │
    ▼
TickStart(T+1)
```

## Directory Structure (Target)

```
bobgame/
├── docs/                    # Documentation
├── assets/
│   └── dawnlike-tileset/    # Raw tileset
├── world/                   # World Runtime
│   ├── pyproject.toml
│   ├── src/
│   │   └── world/
│   │       ├── __init__.py
│   │       ├── server.py    # gRPC server
│   │       ├── tick.py      # Tick loop
│   │       ├── state.py     # World state
│   │       ├── movement.py  # Conflict resolution
│   │       ├── actions.py   # Action handlers
│   │       ├── observation.py
│   │       └── logging.py   # Parquet writer
│   ├── proto/
│   │   └── world.proto      # gRPC definitions
│   └── tests/
├── runner/                  # Runner process
│   ├── pyproject.toml
│   └── src/
├── agents/                  # Agent implementations
│   ├── pyproject.toml
│   └── src/
└── viewer/                  # Phaser 3 viewer
    ├── package.json
    ├── src/
    └── public/
        └── assets/          # Processed tilesets
```
