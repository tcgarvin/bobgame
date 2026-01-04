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

This starts:
- **World Server** - gRPC on `:50051`, WebSocket on `:8765`, with one entity (bob)
- **Random Agent** - controls 'bob' with random movement
- **Viewer** - Vite dev server at http://localhost:5173

All logs are streamed to the terminal with color-coded prefixes and saved to `logs/`.

Press `Ctrl+C` to stop all components.

## Project Structure

```
bobgame/
├── world/      # Python - Tick-based simulation engine (gRPC + WebSocket)
├── agents/     # Python - Example agent that controls entities via gRPC
├── viewer/     # TypeScript - Phaser 3 visualization
├── runner/     # Python - Agent launcher (placeholder)
├── proto/      # Protocol Buffer definitions
├── tools/      # Build scripts (proto compilation)
├── docs/       # Architecture and design documentation
└── dev.sh      # Development environment launcher
```

## Documentation

See [docs/README.md](docs/README.md) for architecture, design specs, and implementation details.
