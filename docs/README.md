# Bob's World - Documentation

A tick-based world simulation with gRPC-controlled LLM agents and a Phaser 3
viewer. Start with [../CLAUDE.md](../CLAUDE.md) for how to run it and where the
code lives, and [../CHANGELOG.md](../CHANGELOG.md) for how it got here.

## Documents

| Document | Description |
|----------|-------------|
| [boardgame_world_design.md](boardgame_world_design.md) | The original design document: the full system specification the project started from |
| [01_architecture.md](01_architecture.md) | High-level architecture and component diagram |
| [02_tech_stack_and_standards.md](02_tech_stack_and_standards.md) | Libraries, tools, and coding standards |
| [03_implementation_plan.md](03_implementation_plan.md) | The original milestone plan (historical; see the status note at its top) |
| [04_tileset_preparation.md](04_tileset_preparation.md) | DawnLike tileset processing for Phaser, and the sprite index |
| [05_jev_agents_design.md](05_jev_agents_design.md) | The two-layer settler agent: planner tools, the Jev stint contract, prompts |
| [06_settlement_run_notes.md](06_settlement_run_notes.md) | Findings from the first settlement runs (historical) |
| [07_replay.md](07_replay.md) | Run directories, recording formats, the replay protocol, deep links, `analyze_run.py` |
| [08_building.md](08_building.md) | Materials, recipes, the two object layers, blocking, dismantling, resting, signs, build shapes |
| [09_conversation_and_reflex.md](09_conversation_and_reflex.md) | Conversations, hailing, giving, the reflex brief, the converser, the four channels |
| [10_metal_and_sleep.md](10_metal_and_sleep.md) | Stations and multi-tick work, tool tiers and ore, the day clock, fatigue and sleep |
| [11_cost_accounting.md](11_cost_accounting.md) | What a run costs: Jev pricing, OpenRouter per-request cost, the cost report |
| [12_sleep_journal.md](12_sleep_journal.md) | The five-section journal, its triggers, the day log and the history reset |
| [13_jev_vs_chat_evals.md](13_jev_vs_chat_evals.md) | The intelligence/cost comparison harness between Jev and a chat model |
| [14_new_moon_and_saves.md](14_new_moon_and_saves.md) | The new moon, saving a world at one and resuming a run from it |
| [terrain_generation_proposal.md](terrain_generation_proposal.md) | The procedural island: noise, hydrology, classification, object placement |

## Architecture Summary

```
Runner ──spawns──► Agents ──gRPC──► World Runtime ──WebSocket──► Viewer
                     │                    │
                     └── lease + intents ─┘
```

- **World Runtime**: authoritative tick-based simulation
- **Agents**: external processes controlling entities via gRPC
- **Runner**: discovers entities, launches and supervises agents
- **Viewer**: Phaser 3 visualization (live on `:8765`, replay on `:8766`)

## Prerequisites

Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 20+ and npm. Each
Python component (`world/`, `agents/`, `runner/`, `tools/`) is its own uv
project; `cd <component> && uv sync` installs it. `rules/` is the shared,
dependency-free `bobgame_rules` package that `world/`, `agents/` and `tools/`
pull in as a path dependency. The viewer is `cd viewer &&
npm install`. Then `./dev.sh`.
