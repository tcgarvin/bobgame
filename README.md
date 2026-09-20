# Bob's World

A tick-based world simulation with gRPC-controlled LLM agents and a Phaser 3 viewer.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Node.js 20+
- npm
- netcat (`nc`) - for the dev script's port checking

## Development

Run the development environment with a single command:

```bash
./dev.sh
```

This runs the `hamlet` scenario: six LLM settlers on a 4000x4000 procedural
island with one wolf. It starts:
- **World Server** - gRPC on `:50051`, WebSocket on `:8765`
- **Runner** - launches and supervises the agent processes
- **Replay Server** - WebSocket on `:8766`, serves recorded runs
- **Viewer** - Vite dev server at http://localhost:5173

All logs are streamed to the terminal with color-coded prefixes and saved to the
run directory (see below).

Press `Ctrl+C` to stop all components.

## Replaying a run

Every `./dev.sh` run is recorded to its own directory:

```
runs/<YYYYMMDD-HHMMSS-config>/
  meta.json                  # run metadata
  world.log runner.log viewer.log replay.log
  world/ticks.jsonl.gz       # everything that happened, tick by tick
  agents/agent-<id>/         # each agent's stints, Jev inputs and planner turns
```

`runs/latest` points at the most recent run. `dev.sh` starts a replay server
alongside the live world, so you can open any past tick of the run you are
watching. To replay an earlier run on its own:

```bash
./replay.sh                           # replays runs/latest
./replay.sh 20260920-143000-hamlet    # a specific run
```

Then open the deep link it prints. Any tick, entity or object can be linked
directly:

```
http://localhost:5173/?run=<run_id>&tick=412&entity=bram
```

To summarise a run and get links to its notable moments:

```bash
python tools/analyze_run.py                 # runs/latest
python tools/analyze_run.py runs/<run_id>
python tools/analyze_run.py --json          # machine-readable
```

The recording format and replay protocol are specified in
[docs/07_replay.md](docs/07_replay.md).

## Resuming a run

Every third night the world writes a save (the "new moon"). To continue a saved
run as an exact continuation:

```bash
./dev.sh --resume 20260920-143000-hamlet          # newest complete save
./dev.sh --resume 20260920-143000-hamlet@903      # a specific save tick
```

See [docs/14_new_moon_and_saves.md](docs/14_new_moon_and_saves.md).

## Project Structure

```
bobgame/
├── rules/      # Python - bobgame_rules: the game's constants, no dependencies
├── world/      # Python - Tick-based simulation engine (gRPC + WebSocket)
├── agents/     # Python - The settler agent (planner + Jev) over gRPC
├── viewer/     # TypeScript - Phaser 3 visualization
├── runner/     # Python - Agent process manager (spawns, monitors, restarts)
├── proto/      # Protocol Buffer definitions
├── tools/      # Build scripts, run analysis (tools/runlib/)
├── docs/       # Architecture and design documentation
├── runs/       # Recorded runs (gitignored); runs/latest -> newest
├── dev.sh      # Development environment launcher
└── replay.sh   # Replay an earlier run (replay server + viewer)
```

## Documentation

See [docs/README.md](docs/README.md) for architecture, design specs, and implementation details.
