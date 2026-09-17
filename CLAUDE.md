# Bob's World - Agent Context

Quick reference for AI agents working on this codebase.

## Current Status

**Current experiment**: the "settlement" scenario: 12 planner+Jev actors (pydantic-ai on OpenRouter for slow thinking, TypeSafe's Jev for per-tick action) building a settlement on the big island while wolves roam. Design and contract: [docs/05_jev_agents_design.md](docs/05_jev_agents_design.md).
**Completed milestones**: 0, 1, 2, 3, 4, 5a, 5b, 6, procedural terrain generation, chunked terrain streaming, and the settlement mechanics (stats, hunger, combat, wolves, extraction, crafting, chests, message boards, say).
**Not done**: milestone 7 (Parquet logging & replay). Milestone 8 (LLM agents) is superseded by `agents.jev_agent`.
**Implementation Plan**: [docs/03_implementation_plan.md](docs/03_implementation_plan.md)

## Project Structure

```
bobgame/
├── world/      # Python - Tick-based simulation engine (gRPC + WebSocket)
├── agents/     # Python - SimpleAgent (foraging) and jev_agent (planner + Jev)
├── viewer/     # TypeScript/Phaser 3 - Browser visualization
├── runner/     # Python - Agent process manager (spawns, monitors, restarts)
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
./dev.sh settlement   # 12 planner+Jev settlers on the island, wolves on (needs .env keys)
./dev.sh              # Uses 'island' config (4000x4000 procedural island, generated on first run)
./dev.sh foraging     # 10x10 world, alice + bob competing for berries
./dev.sh default      # Minimal config (single entity, no objects)
```

`dev.sh` loads `.env` (`TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`) and uses
`runner/configs/<config>.toml` when it exists. After a settlement run:
`python tools/analyze_run.py logs` summarises stints, Jev latency, planner
tool use, and failures. Per-tick Jev traces: `logs/agent-<id>/stints.jsonl`.

Or manually:
```bash
cd world && uv run python -m world.server --config foraging
cd agents && uv run python -m agents.random_agent --entity alice
cd agents && uv run python -m agents.random_agent --entity bob  # In another terminal
cd viewer && npm run dev
```

**Configs** are in `world/configs/`:
- `settlement.toml` - loads the island, spawns 12 settlers at a resource-rich site, wolves on, 2 s ticks
- `island.toml` - 4000x4000 procedural island, saved to `saves/island.npz` after first generation
- `island_small.toml` - 500x500 island for quick testing
- `foraging.toml` - 10x10 world with alice (2,2), bob (8,8), and 3 bushes
- `default.toml` - Minimal 10x10 world

**Terrain tools**:
```bash
cd world && uv run python -m world.terrain              # Standalone terrain generation CLI
uv run python tools/visualize_world.py <map.npz> [out]  # Render a saved map to PNG (output is gitignored)
```

## Key Files by Component

**World Core** (`world/src/world/`):
- `state.py` - World, Entity, Tile, WorldObject, Inventory data models
- `tick.py` - Async tick loop with deadline handling
- `movement.py` - Claim-resolve-enact conflict resolution
- `foraging.py` - Collect/eat/extract actions and bush regeneration
- `combat.py`, `crafting.py`, `containers.py`, `stats.py`, `wolves.py` - settlement mechanics (see docs/05)
- `settlement.py` - Settlement site finder and spawn placement
- `tick_context.py` - TickContext with one `submit_intent` dispatcher for all intent types
- `server.py` - WorldServer entry point
- `services/` - gRPC service implementations
- `chunks.py` - Server-side chunk manager (terrain + object chunks sent to viewers)
- `terrain_types.py` - FloorType enum (numeric values must match `viewer/src/terrain/TerrainConfig.ts`)
- `terrain/` - Procedural generation (noise, island shaping, hydrology, classification, object placement, persistence)

**Agents** (`agents/src/agents/`):
- `random_agent.py` - SimpleAgent with state machine (WANDER/SEEK/COLLECT/EAT)
- `jev_agent/` - Planner (pydantic-ai, OpenRouter) + Jev stint executor; see `agents/CLAUDE.md`

**Proto** (`proto/world.proto`):
- Defines all gRPC services and message types
- Compile with `./tools/compile_proto.sh`

**Assets** (`assets/dawnlike-tileset/`):
- DawnLike tileset PNGs organized by type (Characters/, Objects/, etc.)
- TSX files (Tiled tileset definitions) with `key` attributes for sprite mapping
- Sprite index generated to `viewer/public/assets/sprite-index.json`

**Viewer** (`viewer/src/`):
- `scenes/GameScene.ts` - Main rendering, object sprites, health bars, camera controls (F toggles follow, 0 jumps to the settlement, P toggles the agent panel, `window.cam` helpers in console)
- `ui/OverlayUI.ts` - Entity picker and the agent panel (brief, planner thought, Jev decision)
- `terrain/TerrainConfig.ts` - Floor type → sprite key mapping and multi-tileset GID offsets
- `terrain/ChunkManager.ts` - Per-chunk Phaser tilemaps built from the sprite index
- `terrain/ViewportTracker.ts` - Requests chunks as the camera moves
- `network/WebSocketClient.ts` - Server connection
- `network/WorldState.ts` - Entity interpolation

## Component-Specific Notes

See component CLAUDE.md files for detailed architecture decisions:
- `world/CLAUDE.md` - Simulation engine, tick loop, movement conflict resolution
- `viewer/CLAUDE.md` - Phaser rendering, WebSocket integration
- `agents/CLAUDE.md` - Agent implementation patterns, foraging, intent submission

## Sprite Index

Sprites are managed via Tiled TSX files with custom properties:
- `key` (string): Sprite identifier (e.g., "bush.with_berry", "actor-1")
- `two-frame-animation` (bool): If true, pairs with File1.png for 2-frame animation

Generate the index after editing TSX files:
```bash
python tools/generate_sprite_index.py
```

Output: `viewer/public/assets/sprite-index.json`

## Tests

```bash
cd world && uv run pytest tests/ -v
cd agents && uv run pytest -q
cd runner && uv run pytest -q
```
