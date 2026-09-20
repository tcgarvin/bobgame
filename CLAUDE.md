# Bob's World - Agent Context

Quick reference for AI agents working on this codebase.

## Current Status

**Current experiment**: the "settlement" scenario: 12 planner+Jev actors (pydantic-ai on OpenRouter for slow thinking, TypeSafe's Jev for per-tick action) building a settlement on the big island while wolves roam. Design and contract: [docs/05_jev_agents_design.md](docs/05_jev_agents_design.md).
**Completed milestones**: 0, 1, 2, 3, 4, 5a, 5b, 6, procedural terrain generation, chunked terrain streaming, and the settlement mechanics (stats, food, combat, wolves, extraction, crafting, chests, message boards, say).
**Building update**: settlers can gather fiber (reeds) and clay, craft planks, rope, roads, walls, floors, doors, beds, chairs, tables and a workshop table (which gates the advanced recipes), place them on two object layers, dismantle them, and rest on beds. Walls block everyone, doors block wolves. The planner has a deterministic `build` tool for lines and rectangles. The island was regenerated with groves, outcrops, reeds and clay; the settlement site is a lakeside clearing at (1539, 974). Contract: [docs/08_building.md](docs/08_building.md).
**Cooperation update**: wolves are tuned so nobody beats them alone (16 health, bite 3, simultaneous damage: a lone swordsman loses 12 of 20 health, two armed settlers lose 6 between them). Settlers have a 60-tile `shout` channel whose events carry the speaker's position, Jev shouts when a wolf is in view and is offered a walk to anyone it hears shouting, Jev's state has a `threat` block and the actor's own name, and the planner's `look` lists every settler met. The planner is told it runs in real time: every tool result shows the tick, the ticks the turn has cost, and a `!!` alert when a wolf is near or biting that tells it to hand back to Jev with a fighting `start_stint`. The planner's tool budget is 20 per turn and soft: every tool result says what is left, and a spent budget refuses calls instead of discarding the turn. Details: "Implementation notes" in [docs/05_jev_agents_design.md](docs/05_jev_agents_design.md).
**Conversation and reflex update**: settlers can open a conversation on a tile (`ConverseIntent`: up to 4 seats adjacent to the anchor, world-enforced round-robin turns, closes on a full round of passes), hand items to each other (`GiveIntent`), and register a reflex brief with `set_reflex` that the agent drops into without the planner when a wolf comes within the chosen distance or bites, during planning, conversations and code-driven stints. In conversation mode a small "converser" model call takes each turn and a closing call writes a note to `memory.md`. Contract: [docs/09_conversation_and_reflex.md](docs/09_conversation_and_reflex.md).
**Invitations update (2026-09-18)**: `say(open_to_talk=True)` keeps a settler open to talk for 40 ticks; a settler standing next to an open inviter can `accept` (planner tool `talk_to`, Jev option `talk_to:<id>`) and the world creates the conversation on a free tile next to both. Briefs carry `invitations` (lines Jev may say with the flag). A conversation can now start while the planner is mid-turn: in-flight single-tick tools return "interrupted: conversation conv_N started", stints wait, and the report arrives in the next tool result. The planner no longer has `move`, `attack`, `extract` or `collect` tools (Jev, `travel_to` and `build` cover them) and its budget is 20 calls. Contract: docs/09 section 8.
**Hail update (2026-09-19)**: a settler no longer needs an invitation to start
a conversation. `ConverseIntent` action `hail` (target + opening line) walks up
to another settler and addresses it: the conversation appears on a free tile
next to them both with the hailer's line first and the target speaking next,
and the target is seated without being asked. A settler cannot be hailed for
`HAIL_COOLDOWN_TICKS` (60) after its last conversation ended. The planner tool
is `talk_to(entity_id, opening_line, max_ticks)`, and the planner can also
grant Jev up to 3 hails per brief (see the channels update below). Contract:
[docs/09_conversation_and_reflex.md](docs/09_conversation_and_reflex.md)
section 9.
**Jev context update (2026-09-18)**: Jev's walk options are `step_towards:<object id | entity id | place name | shout:<speaker>>` with a per-type quota (nearest 2 of each object group, every wolf, nearest 3 settlers) instead of one shared top 6, plus a step toward every object id the brief names; briefs carry `places` (`{"river": [x, y]}`) so Jev never sees an absolute coordinate; the state has one always-present `facts` list (food, wolves, fatigue, day), a `so_far` block (ticks, inventory change, action counts, net movement this stint), `B`/`b` map glyphs for bushes with and without berries, and "arrived" instead of "blocked" on a finished walk. Jev is asked a `lost` question and a stint that scores it twice ends with reason `lost` and a report line telling the planner to name the target or move closer. `agents/evals/` is a live functional suite against the real Jev API (`cd agents && uv run pytest evals -q`, needs `TYPESAFE_API_KEY`) for prompt and model-version drift; `evals/replay_states.py` re-asks recorded states. The planner prompt has a "What Jev sees, and how to write for it" section. Contract: docs/05 "Stint (Jev executor)".
**Metal and sleep update**: recipes have a station (`workshop_table`, `furnace`, `anvil`) and a work count; station recipes with work > 1 take one craft action per tick with progress kept on the station. Copper and iron veins sit in inland outcrops (never within 60 tiles of the site) and need a pickaxe of a high enough tier; ore + charcoal smelt to ingots at a furnace, copper tools are made at the workshop table, iron tools and the iron sword at an anvil. The world has a 300-tick day (night is the last third), settlers have fatigue (tired at 60: tool work halved, hits 1 softer, no regen; collapse at 100), and sleep on beds or the ground with `SleepIntent`/`WakeIntent`; the planner has `sleep` and `wake` tools and `craft` loops multi-tick recipes. Contract: [docs/10_metal_and_sleep.md](docs/10_metal_and_sleep.md).
**Journal update**: `memory.md` is no longer an append-only note file. It is a
five-section journal (`Story so far`, `Me`, `Others`, `Learnings`, `Tomorrow`)
plus a `Today's notes` scratch section, and the settler rewrites the five
sections itself in one background model call when it falls asleep or dies, from
a de-duplicated log of the day's tool calls, results, reflections and events.
Each section is capped at 600 tiktoken tokens (one retry, then hard
truncation). Falling asleep or dying ends the planner's turn (the budget is
spent) and the turn after a wake or a respawn starts with an empty message
history and the fresh journal. The model is `--journal-model` / `JOURNAL_MODEL`,
defaulting to the planner's. Contract: [docs/12_sleep_journal.md](docs/12_sleep_journal.md).
**Week-run fixes (2026-09-18)**: after the first 7-day run (`runs/20260918-165534-settlement`,
credit-limited at tick 1919) the planner no longer takes a turn while the body
is asleep, collapsed or dead (`await_active`), any sleep ends the turn, `build`
refuses an empty pack and names the shortfall and recipe, `look` lists item
piles with contents, a failed `pickup` names the nearest piles, and the prompt
states the physics of dying (pile, respawn place and state, no permanent death,
no armor).
**Signs update (2026-09-19)**: settlers can craft a `sign` (2 wood, by hand),
place it like any other structure and write one line of at most 80 characters
on it with the existing `WriteNoteIntent`. A sign blocks nobody, anyone may
rewrite or blank it, and it dismantles like any other built piece. Unlike a
message board it is *pushed*: the first time a settler comes within view (8
tiles) of a written sign, and again whenever its text changes, the line
`[sign at (x, y) by ada, written tick N: "..."]` lands in the planner's next
tool result, its next turn prompt, the journal's day log and any stint report
running at the time. The planner has `place_sign` and `write_sign` (the generic
`place` refuses a sign); Jev sees signs as `S` with their text and can walk to
one, but never places a blank one. Contract: [docs/08_building.md](docs/08_building.md) ("Signs").
**Channels update (2026-09-19)**: settlers now have exactly four ways to reach
each other, and the prompt states what each is good for: `shout` (60 tiles, one
line, no reply), a conversation (the only back-and-forth; its opening line is
heard within 10 tiles and anyone who sees it can join, up to 4), a message
board (20 notes, standing information, `look` marks what is new to you) and a
sign (one pushed line tied to a place). `say` and the whole invitation
mechanic left the agents' surface: no planner `say` tool, no Jev `say:`,
`invite:` or `talk_to:` options, no `Brief.invitations`. The world keeps
`open_to_talk` and `accept` intact but dormant. In their place the planner
grants Jev **brief hails**: `start_stint(..., hails=[{"settler": "dov",
"line": "..."}])`, at most 3, each naming a settler it has met; Jev is offered
`hail:<settler>` (the hail next to them, a code-owned walk otherwise) and a
hail that lands ends the stint into the conversation, exactly as a join does.
A spent or twice-refused hail drops out of the offer and both facts go into the
stint report. `say`/`shout` also tell the speaker who heard them, and boards
track which notes you have read. Contract: docs/09 sections 9 and 10.
**Live-run fixes (2026-09-19)**: a live run of the hail/signs/no-`say` build
(`runs/20260919-211823-settlement`) showed a sleeping settler was invisible to
the planner (13 of 23 hails refused "X is asleep") and could still be walked
into a hail; `write_sign` could silently overwrite a message board's own note
slot; no sign was ever crafted because `place_sign` required one already in
the pack; a wrong tool kwarg (`sleep(bed_object_id=...)`, the real name is
`bed`) failed the whole turn because pydantic-ai's retry text never named the
tool's parameters; and conversations died after a couple of lines because the
converser had no idea why the conversation started, and its closing call kept
only one free-text line. Fixed: `describe_world`/`look` mark an asleep
settler, `talk_to` refuses one up front; `write_sign`/`write_note` refuse the
wrong object naming the right tool; `place_sign` crafts a sign from 2 wood
when needed; every planner tool's argument validator now names the tool's
parameters on a bad call, and retries rose from 2 to 3; `talk_to`/
`open_conversation` take a required `purpose` (and Jev's brief hails an
optional one) shown only to the settler that started the conversation; the
closing note is now two fields (what was agreed/learned, what this settler
said it would do), both reaching the journal and the planner's report.
Contract: docs/09 section 11.
**Hamlet scenario (2026-09-20)**: `hamlet` is a smaller settlement variant —
six settlers (ada, bram, cleo, dov, esme, finn) and a single wolf that arrives
30 to 45 tiles out instead of three at 20 to 40. The wolf tunables are world
config now (`max_wolves`, `wolf_spawn_min_distance`, `wolf_spawn_max_distance`,
validated at load and defaulting to the settlement values), and the settler
count reaches the prompts through `python -m agents.jev_agent --settlers N`,
which `runner/configs/hamlet.toml` passes. The planner's goal paragraph now
names the shelter: a settlement that lasts, where every one of you has a
shelter of your own to sleep in, and where food, safety and rest are things you
can count on tomorrow. `settlement` and `settlement_peaceful` are unchanged.
**Hamlet-run fixes (2026-09-20)**: the first six-settler run showed planner
turns lasting 100-500 ticks with no sight of the body (four settlers starved
mid-turn), `eat` failing `insufficient_items` 14 times, 30 of 89 `travel_to`
calls ending `ticks_exhausted` (8 of them already standing on the target), and
walls going up slowly because `build` refused a pack that held the materials
but not the pieces. Fixed: every tool result's clock line now carries `food
X/100, health Y/20, fatigue Z/100`, and `!!` lines (physics only) fire at food
25, at food 0 and within 10 of collapse, in tool results and in the turn
prompt; `travel_to` returns at once and spends no tick when the body is
already there, ends the stint code-side the tick it arrives (`arrived`,
`arrived_next_to` for a destination nobody can stand on) instead of waiting
for Jev's `done`; `eat` with an empty pack picks and eats the berry under the
body's feet, or names the nearest bushes that had one; `build` crafts what it
is short of before it starts and whenever it runs dry — recipe chains
included (wood → plank → wall), never walking to a station, up to 20 pieces
and three refills per call; and `tools/analyze_run.py` no longer counts a
wolf's death as a settler's, reporting `deaths: N settlers by={wolf,
starvation, unknown, <settler>}` and `wolf kills: N by={<settler>}`
separately.
**Hamlet-run fixes, round 2 (2026-09-20)**: the same run, read for the
shelter goal. esme walled the six free neighbours of her own tile and starved
in the 1-tile cell; cleo built a 3x3 ring with no door and was never told what
it enclosed; dov slept from food 39 to death because only food 0 woke a
sleeper; ada stepped north and south for 108 ticks inside her own walls toward
a bush four tiles away; bram spent 8 of 20 tool calls on calls that each
bounced off one conversation; nobody knew fiber comes from reeds (0 doors, 1
bed); and no journal carried the build site, so each day started a new wall
cluster (5 disjoint ones). Fixed, all facts and no advice: a new
`agents/.../enclosure.py` holds one set of flood fills, so `build` now reports
what its walls form (`the walls here now enclose N interior tile(s) spanning
WxH; doors: D; gaps: G; you are inside/outside`) and names the tiles it refused
as sealing; Jev's `place` will not offer the wall that shuts the actor in (a
door never seals); a `!! ENCLOSED: you can reach only N tile(s); the pieces
around you: ...` line goes on every tool result, the turn prompt and Jev's
`facts`; a stint ends `food_low` / `food_zero` the tick food crosses 25 or 0
(reflexes exempt) and `no_path` after 3 ticks with no route to anything the
brief named; an interrupted tool call costs no budget; a failed `craft` names
where the missing raw input comes from and the nearest such objects; `look`
lists your own standing pieces by 8-connected cluster; and the world wakes a
sleeper at `HUNGRY_WAKE_FOOD` (20), which is also the level below which it
refuses to lie down. Details: "Hamlet-run fixes, round 2" in
[agents/CLAUDE.md](agents/CLAUDE.md).
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
./dev.sh settlement_peaceful   # same, wolves off: watch them build undisturbed
./dev.sh hamlet       # 6 settlers, one wolf that spawns further out
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

**Detached live runs**: `tools/live_run.sh start <config> <seconds>` starts
`./dev.sh` detached with an automatic stop, `status` prints a one-screen health
and progress report with harmless log noise filtered out, `wait` blocks until
the run ends, and `stop` ends it cleanly. The `live-run` sub-agent
(`.claude/agents/live-run.md`, Haiku) drives this script and reports back; use
it instead of babysitting a run yourself.

**Analysis**: `python tools/analyze_run.py` summarises `runs/latest` — stints,
Jev latency, planner tool use, failures, world-level deaths/wolves/crafts — and
prints a "notable moments" list where every line carries a deep link. It also
reports conversations, giving and reflex firings (docs/09 section 6). Pass a run
directory to pick another run, `--json` for a machine-readable dump,
`--max-moments` to change the cap, and `--viewer-url` to change the link base.
It still reads the legacy layout: `python tools/analyze_run.py logs`.
It also prints a cost section: OpenRouter's exact per-request cost for the
planner and converser, Jev priced locally at $42 per billion input tokens,
split per settler and per turn with $/100 ticks and $/hour. Contract:
[docs/11_cost_accounting.md](docs/11_cost_accounting.md).

Or manually:
```bash
cd world && uv run python -m world.server --config foraging
cd agents && uv run python -m agents.random_agent --entity alice
cd agents && uv run python -m agents.random_agent --entity bob  # In another terminal
cd viewer && npm run dev
```

**Configs** are in `world/configs/`:
- `settlement.toml` - loads the island, spawns 12 settlers at a resource-rich site, wolves on, 2 s ticks
- `hamlet.toml` - the same island and site with 6 settlers and one wolf spawning 30-45 tiles out
- `island.toml` - 4000x4000 procedural island, saved to `saves/island.npz` after first generation
- `island_small.toml` - 500x500 island for quick testing
- `foraging.toml` - 10x10 world with alice (2,2), bob (8,8), and 3 bushes
- `default.toml` - Minimal 10x10 world

**Terrain tools**:
```bash
cd world && uv run python -m world.terrain              # Standalone terrain generation CLI
uv run --with pillow python tools/visualize_world.py <map.npz> [out] [--crop X,Y,SIZE --scale N --mark X,Y]  # Render a saved map (or a zoomed crop) to PNG
```

## Key Files by Component

**World Core** (`world/src/world/`):
- `state.py` - World, Entity, Tile, WorldObject, Inventory data models
- `tick.py` - Async tick loop with deadline handling
- `movement.py` - Claim-resolve-enact conflict resolution
- `foraging.py` - Collect/eat/extract actions and bush regeneration
- `items.py` - Item/object kinds, layers, blocking sets, extraction maps (the building contract in code; agents mirror it in `jev_agent/items.py`)
- `conversations.py` - Conversation objects, turn order and lifecycle (docs/09); giving lives in `containers.py`
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
- `jev_agent/journal.py` - the sleep-time journal: file format, token cap, day log, writer (docs/12)
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
- `docs/08_building.md` - Materials, recipes, layers, blocking, dismantling, resting, site thresholds
- `docs/09_conversation_and_reflex.md` - Conversations, giving, the reflex brief, converser, traces
- `docs/12_sleep_journal.md` - The five-section journal, its triggers, the day log and the history reset

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
