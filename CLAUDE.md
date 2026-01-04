# Bob's World - Agent Context

Quick reference for AI agents working on this codebase.

## Current Status

**Next Milestone**: 5a - Berry Foraging Foundation
**Completed**: 0, 1, 2, 3, 4
**Implementation Plan**: [docs/03_implementation_plan.md](docs/03_implementation_plan.md)

## Project Structure

```
bobgame/
├── world/      # Python - Tick-based simulation engine (gRPC + WebSocket)
├── agents/     # Python - Agent implementations (currently RandomAgent)
├── viewer/     # TypeScript/Phaser 3 - Browser visualization
├── runner/     # Python - Agent launcher (placeholder)
├── proto/      # Protocol Buffer definitions (world.proto)
├── tools/      # Build scripts (proto compilation, atlas generation)
└── docs/       # Architecture and design documentation
```

## Key Architecture

- **Tick rate**: 1 Hz (1000ms per tick, 500ms deadline for intents)
- **Communication**: gRPC (agents ↔ world), WebSocket (world → viewer)
- **Data models**: Pydantic frozen models (immutable, `.model_copy(update={})` for changes)
- **Conflict resolution**: Claim-resolve-enact pipeline, lexicographic entity_id wins ties

## Running the System

```bash
./dev.sh              # Uses 'foraging' config by default (bob + berry bushes)
./dev.sh default      # Minimal config (bob only, no objects)
```

Or manually:
```bash
cd world && uv run python -m world.server --config foraging
cd agents && uv run python -m agents.random_agent --entity bob
cd viewer && npm run dev
```

**Configs** are in `world/configs/`:
- `foraging.toml` - 10x10 world with bob at (5,5) and bushes at (3,3), (7,7)
- `default.toml` - Minimal 10x10 world

## Key Files by Component

**World Core** (`world/src/world/`):
- `state.py` - World, Entity, Tile, WorldObject, Inventory data models
- `tick.py` - Async tick loop with deadline handling
- `movement.py` - Claim-resolve-enact conflict resolution
- `foraging.py` - Collect/eat actions and bush regeneration
- `server.py` - WorldServer entry point
- `services/` - gRPC service implementations

**Agents** (`agents/src/agents/`):
- `random_agent.py` - Reference agent that moves randomly and collects berries

**Proto** (`proto/world.proto`):
- Defines all gRPC services and message types
- Compile with `./tools/compile_proto.sh`

**Viewer** (`viewer/src/`):
- `scenes/GameScene.ts` - Main rendering
- `network/WebSocketClient.ts` - Server connection
- `network/WorldState.ts` - Entity interpolation

## Component-Specific Notes

See component CLAUDE.md files for detailed architecture decisions:
- `world/CLAUDE.md` - Simulation engine, tick loop, movement conflict resolution
- `viewer/CLAUDE.md` - Phaser rendering, WebSocket integration
- `agents/CLAUDE.md` - Agent implementation patterns, foraging, intent submission

## Tests

```bash
cd world && uv run pytest tests/ -v
```
