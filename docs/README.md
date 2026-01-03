# Bob's World - Documentation

A tick-based world simulation with gRPC-controlled LLM agents and a Phaser 3 viewer.

## Documents

| Document | Description |
|----------|-------------|
| [Design Specification](boardgame_world_design.md) | Original design document with full system specification |
| [Architecture Overview](01_architecture.md) | High-level system architecture and component diagram |
| [Tech Stack & Standards](02_tech_stack_and_standards.md) | Libraries, tools, and coding standards |
| [Implementation Plan](03_implementation_plan.md) | Incremental milestones with tasks |
| [Tileset Preparation](04_tileset_preparation.md) | DawnLike tileset processing for Phaser |

## Quick Start

### Prerequisites
- Python 3.12+
- uv (Python package manager)
- Node.js 20+
- npm

### Project Setup
```bash
# Initialize Python projects
uv init world && cd world && uv add grpcio grpcio-tools pyarrow pydantic structlog && cd ..
uv init runner && cd runner && uv add grpcio pyyaml structlog && cd ..
uv init agents && cd agents && uv add grpcio anthropic structlog && cd ..

# Initialize viewer
mkdir viewer && cd viewer
npm create vite@latest . -- --template vanilla-ts
npm install phaser
cd ..
```

## Architecture Summary

```
Runner ──spawns──► Agents ──gRPC──► World Runtime ──gRPC──► Viewer
                     │                    │
                     └── lease + intents ─┘
```

- **World Runtime**: Authoritative tick-based simulation (1 Hz)
- **Agents**: External processes controlling entities via gRPC
- **Runner**: Discovers entities, launches/supervises agents
- **Viewer**: Phaser 3 visualization (live + replay)

## Next Steps

See [Implementation Plan](03_implementation_plan.md) - start with Milestone 0 (Project Scaffolding).
