# Bob's World - Agent Context

Quick reference for AI agents working on this codebase. A tick-based world
simulation: a 4000x4000 procedural island, a Python world
server, LLM-driven settlers, and a Phaser 3 viewer that watches live or replays
any recorded run. Each settler is two-layered — a slow planner (pydantic-ai on
OpenRouter) that thinks in tools, and TypeSafe's Jev picking one action per tick.

## Current state

One scenario, `hamlet`: six settlers (sloopa, meduski, pooka, dov, esme, finn) on the
island with one wolf. What a settler can do today:

- Forage, extract, craft (stations, multi-tick work, tool tiers), equip, place
  on two object layers, dismantle, use chests and piles — docs/08, docs/10
- Fight wolves (nobody beats one alone); carry a food/health/fatigue body
  through a 300-tick day, sleeping on a bed or the ground — docs/10
- Build with a deterministic `build` tool that reports what its walls enclose,
  and put up signs — docs/08
- Reach the others four ways — shout, conversation (the only back-and-forth),
  message board, sign — and register a reflex brief that fires on a nearby wolf
  without the planner — docs/09
- Rewrite a five-section journal (`memory.md`) on every sleep or death — docs/12
- Live through the **new moon**: every third night in `hamlet` everyone falls
  asleep at nightfall, wolves lie low, and the world writes a save that
  `./dev.sh --resume` picks up as an exact continuation — docs/14
- Agent design and the planner/Jev contract: docs/05. Recording and replay:
  docs/07. Cost accounting: docs/11. Jev-vs-chat eval harness: docs/13.

History — what changed when, and why — is in [CHANGELOG.md](CHANGELOG.md).

## Project Structure

```
bobgame/
├── rules/      # Python - bobgame_rules: the game's constants and tables, no dependencies
├── world/      # Python - Tick-based simulation engine (gRPC + WebSocket)
├── agents/     # Python - jev_agent (planner + Jev)
├── viewer/     # TypeScript/Phaser 3 - Browser visualization
├── runner/     # Python - Agent process manager (spawns, monitors, restarts)
├── proto/      # Protocol Buffer definitions (world.proto)
├── tools/      # Build scripts, run analysis (tools/runlib/)
└── docs/       # Architecture and design documentation
```

## Key Architecture

- **Tick rate**: 1 Hz default (1000ms tick, 500ms intent deadline); `hamlet` uses 2000ms / 1200ms
- **Communication**: gRPC (agents ↔ world), WebSocket (world → viewer)
- **Data models**: Pydantic frozen models (immutable, `.model_copy(update={})` for changes)
- **Conflict resolution**: Claim-resolve-enact pipeline, lexicographic entity_id wins ties

## Running the System

`./dev.sh` (or `./dev.sh hamlet`, the same thing) runs the only scenario. It
loads `.env` (`TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`) and uses
`world/configs/<config>.toml` and `runner/configs/<config>.toml`; a config that
does not exist is an error. `hamlet.toml` generates the 4000x4000 island (seed
12345) into `saves/island.npz` on a first run and loads it afterwards.

**Run directories**: each `./dev.sh` run creates `runs/<YYYYMMDD-HHMMSS-config>/`
(gitignored), exports `BOBGAME_RUN_ID` and `BOBGAME_RUN_DIR` to every child
process, and points `runs/latest` at it. Everything lives there: `meta.json`,
the process logs (`world.log`, `runner.log`, `viewer.log`, `replay.log`), the
world recording (`world/{ticks,objects}.jsonl.gz`) and the agent traces
(`agents/agent-<id>.log`,
`agents/agent-<id>/{stints,jev_states,planner}.jsonl.gz`, `memory.md`). Format
details: [docs/07_replay.md](docs/07_replay.md).

**Resuming**: `./dev.sh --resume <run_id>[@<tick>]` continues a saved run from
the newest complete save (or the named tick). It makes a *new* run directory
whose `meta.json` carries `parent_run_id` and `resumed_from_tick`, copies the
parent's `memory.md` and `reflex.json` in, and starts the world with `--resume
<save dir>` and every agent with `--resume-from <save dir>`. Contract:
[docs/14_new_moon_and_saves.md](docs/14_new_moon_and_saves.md).

**Replay**: `dev.sh` also starts the replay server on `ws://localhost:8766`, so a
live run can be opened at any past tick. `./replay.sh [run_id]` (default
`runs/latest`) replays an earlier run with no world or agents. Deep links are
`http://localhost:5173/?run=<run_id>&tick=<n>&entity=<id>` (also `x`, `y`,
`zoom`, `play`, `speed`, `panel`, `object`).

**Stopping on a save**: `./dev.sh --stop-after-saves <n>` shuts the run down by
itself once it has written `<n>` complete new-moon saves, so it ends on a point
`--resume` can continue from (hamlet's first save is about tick 900, 30 minutes).

**Detached live runs**: `tools/live_run.sh start <seconds> [--saves <n>]
[--resume <run_id>[@<tick>]]` starts `./dev.sh` detached with an automatic stop
after `<seconds>`, or sooner once `<n>` saves are complete; `status`, `wait` and
`stop` follow it. The
`live-run` sub-agent (`.claude/agents/live-run.md`, Haiku) drives this script
and reports back; use it instead of babysitting a run yourself.

**Analysis**: `uv run --project tools python tools/analyze_run.py [run_dir]` summarises `runs/latest` —
stints, Jev latency, planner tool use, failures, world-level
deaths/wolves/crafts, conversations, giving and reflex firings (docs/09 section
6) — plus "notable moments" with deep links and a cost section (OpenRouter's
exact per-request cost for planner and converser, Jev priced locally at $42 per
billion input tokens, per settler and per turn with $/100 ticks and $/hour;
contract [docs/11_cost_accounting.md](docs/11_cost_accounting.md)). Flags:
`--json`, `--max-moments`, `--viewer-url`.
`uv run --project tools python tools/settlement_progress.py` answers one question instead: how far has
the settlement got, and has it plateaued. Both are thin CLIs over
`tools/runlib/` (`runio`, `worldscan`, `agenttrace`, `cost`, `social`, `report`,
`rooms`).

Or manually, and terrain by hand:
```bash
cd world && uv run python -m world.server --config hamlet
cd agents && uv run python -m agents.jev_agent --entity dov --settlers 6
cd viewer && npm run dev
cd world && uv run python -m world.terrain    # standalone terrain generation CLI
uv run --with pillow python tools/visualize_world.py <map.npz> [out] [--crop X,Y,SIZE --scale N --mark X,Y]
```

## Key Files by Component

**World Core** (`world/src/world/`):
- `state/` - `models.py` (frozen `WorldClock`, `Inventory`, `Tile`, `Entity`, `WorldObject`) and `world.py` (`World`, the one mutable container); `world.state` re-exports every name
- `mechanics.py` - **the intent registry**: one row per intent (model, action type, proto field, converter, phase), declared in phase order; `tick.py` walks it. `tick_context.py` - the one `submit_intent` dispatcher
- `movement.py` - Claim-resolve-enact conflict resolution
- `items.py` - The world's view of `bobgame_rules` (repo root `rules/`): item/object kinds, layers, blocking sets, extraction maps, re-exported under the names world code uses
- `foraging.py`, `combat.py`, `crafting.py`, `containers.py`, `stats.py`, `sleep.py`, `wolves.py`, `conversations.py`, `speech.py` - the mechanics (docs/05, docs/08, docs/09, docs/10); giving lives in `containers.py`
- `events.py` - the per-tick event records and `commit_object`, the one way a mechanic changes an object's state; `objstate.py` - the one JSON-in-object-state codec
- `moon.py` - the new moon (forced sleep, still window, wolves lying low); `snapshot.py` - writing and loading a save; `save_coordinator.py` - when a save is due and waiting for the settlers (docs/14)
- `settlement.py` - Settlement site finder and spawn placement
- `server.py` (`WorldServer`), `bootstrap.py` (`ServerSettings`, building a world from a config or a save) and `cli.py` (argparse; `--run-dir`, `--resume`, `--parent-run-id`); `python -m world.server` runs `cli.main`
- `recording.py` - run recorder (run id from `$BOBGAME_RUN_ID` when set); `replay/` - replay server (`python -m world.replay --runs-dir ../runs --port 8766`)
- `services/` - gRPC service implementations (`observation_service.py` holds `VIEW_RADIUS`)
- `chunks.py` - Server-side chunk manager (terrain + object chunks sent to viewers)
- `terrain_types.py` - re-exports `FloorType` from `bobgame_rules.terrain`
- `terrain/` - Procedural generation (noise, island shaping, hydrology, classification, object placement, persistence)

**Agents** (`agents/src/agents/`): `jev_agent/` is the planner (pydantic-ai,
OpenRouter) + Jev stint executor. Six packages under their old import names —
`planner/`, `agent/`, `worldmodel/`, `options/`, `conversation/`, `stint/` —
over the shared `briefs.py`, `bridge.py`, `actions.py`, `outcomes.py`,
`recipes.py`, `walk.py`, `snapshot.py` (docs/14), `journal.py` (docs/12) and
`tracelog.py` (traces under `$BOBGAME_RUN_DIR/agents`). Module map and import
layering: `agents/CLAUDE.md`. `agents/evals/` is a live suite against the real
Jev API (`uv run pytest evals -q`, needs `TYPESAFE_API_KEY`).

**Tooling** (repo root, `tools/`): `dev.sh` (makes the run dir, exports
`BOBGAME_RUN_ID`/`BOBGAME_RUN_DIR`, starts world + replay + runner + viewer),
`replay.sh` (replay server + viewer only), `tools/runlib/` (shared run readers,
with `analyze_run.py` / `settlement_progress.py` as the CLIs), `tools/tests/`.
`tools/generate_rules_ts.py` writes `viewer/src/generated/rules.ts` from
`bobgame_rules`; run it after changing anything in `rules/` or the tests fail
on the stale file.

**Proto** (`proto/world.proto`): all gRPC services and message types. Compile
with `./tools/compile_proto.sh`, which also fixes the generated relative import.

**Assets** (`assets/dawnlike-tileset/`): tileset PNGs by type, and TSX files
(Tiled tileset definitions) with `key` attributes for sprite mapping. After
editing a TSX file, regenerate `viewer/public/assets/sprite-index.json` with
`python tools/generate_sprite_index.py`.

**Viewer** (`viewer/src/`):
- `scenes/GameScene.ts` - Rendering, object sprites, health bars, camera (F toggles follow, 0 jumps to the settlement, P toggles the agent panel, `window.cam` in console); `ui/OverlayUI.ts` - entity picker and agent panel
- `terrain/` - `TerrainConfig.ts` (floor type → sprite key, multi-tileset GID offsets), `ChunkManager.ts` (per-chunk tilemaps), `ViewportTracker.ts` (chunk requests)
- `network/` - `WebSocketClient.ts` (live `:8765`, replay `:8766`), `WorldState.ts` (entity interpolation). Replay-mode files: `viewer/CLAUDE.md`

## Component-Specific Notes

`world/CLAUDE.md` (simulation engine, tick loop, movement resolution),
`agents/CLAUDE.md` (JevAgent architecture, modes, options, traces),
`viewer/CLAUDE.md` (Phaser rendering, WebSocket), `runner/CLAUDE.md` (agent
process supervision), `docs/README.md` (index of every design document).

## Tests

```bash
cd rules && uv run pytest -q
cd world && uv run pytest tests/ -v
cd agents && uv run pytest -q
cd runner && uv run pytest -q
cd tools && uv run pytest -q
```
