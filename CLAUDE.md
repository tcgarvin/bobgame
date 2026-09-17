# Bob's World - Agent Context

Quick reference for AI agents working on this codebase.

## Current Status

**Current experiment**: the "settlement" scenario: 12 planner+Jev actors (pydantic-ai on OpenRouter for slow thinking, TypeSafe's Jev for per-tick action) building a settlement on the big island while wolves roam. Design and contract: [docs/05_jev_agents_design.md](docs/05_jev_agents_design.md).
**Completed milestones**: 0, 1, 2, 3, 4, 5a, 5b, 6, procedural terrain generation, chunked terrain streaming, and the settlement mechanics (stats, hunger, combat, wolves, extraction, crafting, chests, message boards, say).
**In progress**: milestone 7, run recording & replay — every run is recorded to `runs/<run_id>/` as gzip JSONL (not Parquet) and a replay server serves it to the viewer with seeking, playback and deep links. Contract: [docs/07_replay.md](docs/07_replay.md).
**Not done**: milestone 8 (LLM agents) is superseded by `agents.jev_agent`.
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
`runner/configs/<config>.toml` when it exists.

**Run directories**: each `./dev.sh` run creates `runs/<YYYYMMDD-HHMMSS-config>/`,
exports `BOBGAME_RUN_ID` and `BOBGAME_RUN_DIR` to every child process, and points
`runs/latest` at it. Everything for a run lives there: `meta.json`, the process
logs (`world.log`, `runner.log`, `viewer.log`, `replay.log`), the world recording
(`world/ticks.jsonl.gz`, `world/objects.jsonl.gz`) and the agent traces
(`agents/agent-<id>.log`, `agents/agent-<id>/{stints,jev_states,planner}.jsonl.gz`,
`memory.md`). `runs/` is gitignored. The old flat `logs/` directory is no longer
written to. Format details: [docs/07_replay.md](docs/07_replay.md).

**Replay**: `dev.sh` also starts the replay server on `ws://localhost:8766`, so a
live run can be opened at any past tick. To replay an earlier run without the
world and agents:

```bash
./replay.sh              # replays runs/latest
./replay.sh 20260917-143000-settlement
```

Deep links are `http://localhost:5173/?run=<run_id>&tick=<n>&entity=<id>`
(also `x`, `y`, `zoom`, `play`, `speed`, `panel`, `object`).

**Analysis**: `python tools/analyze_run.py` summarises `runs/latest` — stints,
Jev latency, planner tool use, failures, world-level deaths/wolves/crafts — and
prints a "notable moments" list where every line carries a deep link. Pass a run
directory to pick another run, `--json` for a machine-readable dump,
`--max-moments` to change the cap, and `--viewer-url` to change the link base.
It still reads the legacy layout: `python tools/analyze_run.py logs`.

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
- `server.py` - WorldServer entry point (`--run-dir`, defaults to `$BOBGAME_RUN_DIR`)
- `recording.py` - Run recorder: `meta.json`, `world/objects.jsonl.gz`, `world/ticks.jsonl.gz`
- `replay/` - Replay server (`python -m world.replay --runs-dir ../runs --port 8766`)
- `services/` - gRPC service implementations
- `chunks.py` - Server-side chunk manager (terrain + object chunks sent to viewers)
- `terrain_types.py` - FloorType enum (numeric values must match `viewer/src/terrain/TerrainConfig.ts`)
- `terrain/` - Procedural generation (noise, island shaping, hydrology, classification, object placement, persistence)

**Agents** (`agents/src/agents/`):
- `random_agent.py` - SimpleAgent with state machine (WANDER/SEEK/COLLECT/EAT)
- `jev_agent/` - Planner (pydantic-ai, OpenRouter) + Jev stint executor; see `agents/CLAUDE.md`
- `jev_agent/tracelog.py` - gzip JSONL traces (`stints`, `jev_states`, `planner`) under `$BOBGAME_RUN_DIR/agents`

**Tooling** (repo root, `tools/`):
- `dev.sh` - Makes the run dir, exports `BOBGAME_RUN_ID`/`BOBGAME_RUN_DIR`, starts world + replay + runner + viewer
- `replay.sh` - Replay server + viewer only, for an earlier run
- `tools/analyze_run.py` - Run summary, notable moments with deep links, `--json`
- `tools/tests/test_analyze_run.py` - `cd tools && uv run pytest -q`

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
- `network/WebSocketClient.ts` - Server connection (live `:8765`, replay `:8766`)
- `network/WorldState.ts` - Entity interpolation
- Replay mode: deep-link parsing, the transport bar and the object inspector -
  see `viewer/CLAUDE.md` for the current file names

## Component-Specific Notes

See component CLAUDE.md files for detailed architecture decisions:
- `world/CLAUDE.md` - Simulation engine, tick loop, movement conflict resolution
- `viewer/CLAUDE.md` - Phaser rendering, WebSocket integration
- `agents/CLAUDE.md` - Agent implementation patterns, foraging, intent submission
- `docs/07_replay.md` - Run recording formats, replay protocol, deep links

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
cd tools && uv run pytest -q
```
