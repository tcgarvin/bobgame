# Technology Stack and Coding Standards

## Package Management

### Python (uv)
All Python projects use **uv** for dependency management:
- Fast, reliable dependency resolution
- Lock files for reproducible builds
- Per-project virtual environments

```bash
# Create new project
uv init world
cd world
uv add grpcio grpcio-tools pyarrow

# Run scripts
uv run python -m world.server

# Development mode
uv sync
source .venv/bin/activate
```

### JavaScript/TypeScript (npm)
The Phaser viewer uses npm:
```bash
cd viewer
npm install
npm run dev
```

## Core Libraries

### World Runtime (Python)

| Library | Purpose | Version |
|---------|---------|---------|
| `grpcio` | gRPC server | ^1.60 |
| `grpcio-tools` | Protobuf compilation | ^1.60 |
| `pyarrow` | Parquet logging | ^15.0 |
| `pydantic` | Data validation | ^2.0 |
| `structlog` | Structured logging | ^24.0 |
| `pytest` | Testing | ^8.0 |
| `pytest-asyncio` | Async test support | ^0.23 |

### Runner (Python)

| Library | Purpose |
|---------|---------|
| `grpcio` | gRPC client |
| `pyyaml` | Config parsing |
| `structlog` | Logging |

### Agents (Python)

| Library | Purpose |
|---------|---------|
| `grpcio` | gRPC client |
| `anthropic` | Claude API |
| `structlog` | Logging |

### Viewer (JavaScript/TypeScript)

| Library | Purpose |
|---------|---------|
| `phaser` | Game framework (v3.80+) |
| `typescript` | Type safety |
| `vite` | Build tool |
| `@grpc/grpc-js` | gRPC client (or REST proxy) |

## Coding Standards

### Python Standards

Follow PEP 8 with these specific requirements:

#### Error Handling
- Never fail silently; handle exceptions explicitly or re-raise with context
- Catch specific exceptions, avoid bare `except:`
- Custom exceptions when they add clarity
- Logging errors is fine, but logged errors followed by successful returns is not acceptable

```python
# Good
def load_config(path: Path) -> Config:
    try:
        data = path.read_text()
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}") from None
    return parse_config(data)

# Bad
def load_config(path: Path) -> Config | None:
    try:
        return parse_config(path.read_text())
    except Exception:
        logger.error("Failed to load config")
        return None  # Silent failure!
```

#### Data Handling
- Avoid `None` as general-purpose state; use explicit defaults or sentinel objects
- No `hasattr` unless handling external objects
- Validate external inputs at boundaries
- Don't mutate unless documented

```python
# Good
@dataclass
class Entity:
    position: Position
    inventory: Inventory = field(default_factory=Inventory)

# Bad
class Entity:
    def __init__(self):
        self.position = None  # Multiple meanings!
        self.inventory = None
```

#### Type Hints
- Use type hints for all public functions
- Run `mypy` in strict mode
- Use `TypedDict` for structured dicts

```python
from typing import TypedDict

class TickEvent(TypedDict):
    tick_id: int
    timestamp_ms: int
    world_version: str
```

#### Testing
- Use `pytest` exclusively
- Unit tests for core logic, integration tests for gRPC flows
- Mock external services
- Descriptive test names

```python
# test_movement.py
def test_diagonal_move_blocked_when_cardinal_neighbor_unwalkable():
    """Diagonal moves require both adjacent cardinal tiles walkable."""
    world = create_test_world("""
        ...
        .#.
        ...
    """)
    entity = world.spawn_entity(Position(0, 0))
    result = world.apply_move(entity, Direction.SE)
    assert result.blocked
    assert entity.position == Position(0, 0)
```

#### Formatting & Tooling
```bash
# pyproject.toml
[tool.black]
line-length = 100
target-version = ["py312"]

[tool.mypy]
strict = true
python_version = "3.12"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

### TypeScript/JavaScript Standards (Viewer)

#### TypeScript Configuration
```json
{
  "compilerOptions": {
    "strict": true,
    "noImplicitAny": true,
    "strictNullChecks": true,
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler"
  }
}
```

#### Style Guide
- Use `const` by default, `let` when reassignment needed
- Prefer `interface` over `type` for object shapes
- Use `readonly` for immutable properties
- No `any` without explicit comment

```typescript
// Good
interface TileData {
  readonly x: number;
  readonly y: number;
  walkable: boolean;
  opaque: boolean;
}

// Bad
type TileData = any;
```

#### Phaser Conventions
- Scenes extend `Phaser.Scene`
- Use scene keys as constants
- Preload all assets in dedicated preload scene
- Event-driven communication between scenes

## Project Setup Commands

### Initial Setup
```bash
# Clone and setup Python components
cd bobgame
uv init world
uv init runner
uv init agents

# Setup viewer
mkdir viewer && cd viewer
npm create vite@latest . -- --template vanilla-ts
npm install phaser
```

### Development Workflow
```bash
# Terminal 1: World Runtime
cd world && uv run python -m world.server

# Terminal 2: Runner
cd runner && uv run python -m runner.main

# Terminal 3: Viewer
cd viewer && npm run dev
```

### Pre-commit Hooks (Recommended)
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/psf/black
    rev: 24.1.0
    hooks:
      - id: black
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.8.0
    hooks:
      - id: mypy
        additional_dependencies: [pydantic]
```

## gRPC Protocol

Protocol buffers in `proto/world.proto`:
- Compiled with `grpcio-tools` for Python
- Compiled with `grpc-web` or proxied via REST for viewer

## Logging Standards

Use structured logging with `structlog`:

```python
import structlog

log = structlog.get_logger()

log.info("tick_completed", tick_id=42, entities_moved=5)
log.error("intent_rejected", entity_id="e123", reason="invalid_lease")
```
