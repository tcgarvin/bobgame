# World Project Notes

Development notes and patterns for the world simulation core.

## Architecture Decisions (Milestone 2)

### Data Models: Pydantic Frozen Models

Internal state uses Pydantic v2 with `frozen=True` for immutability:

```python
class Entity(BaseModel, frozen=True):
    ...
    def with_position(self, new_position: Position) -> "Entity":
        return self.model_copy(update={"position": new_position})
```

**Rationale**: Validation at construction, immutability prevents accidental mutation, `.model_copy(update={})` provides clean update pattern.

**Proto types are for API boundaries only** - convert at gRPC layer, not internally.

### World State: Sparse Tile Storage

Tiles use a sparse dict rather than a 2D array:
- `_tiles: dict[Position, Tile]` stores only non-default tiles
- Default tiles (walkable=True, opaque=False) are generated on-demand
- Out-of-bounds positions return non-walkable tiles

### Entity Registry: Dual Indexing

Entities are indexed by both ID and position for O(1) lookups:
- `_entities: dict[str, Entity]` - lookup by ID
- `_entity_positions: dict[Position, str]` - lookup by position

Both indices must be updated atomically via `update_entity_position()`.

### Movement Conflict Resolution

The claim-resolve-enact pipeline handles conflicts deterministically:

1. **Claim**: Validate moves (bounds, walkability, diagonal blocking)
2. **Resolve**: Detect conflicts in order:
   - Swaps (A→B, B→A) → both fail
   - Cycles (A→B→C→A) → all fail
   - Same destination → lexicographic entity_id wins
   - Destination occupied by non-mover → fail
3. **Enact**: Apply winning moves simultaneously

**Key insight**: Chains succeed (A→B, B→empty both move) because cycle detection only fails actual cycles, not chains.

## Testing Patterns

### Fixtures (conftest.py)

Standard fixtures for common scenarios:
- `empty_world` - 10x10 all walkable
- `world_with_walls` - wall at y=5
- `world_with_l_wall` - L-shaped wall for diagonal blocking tests
- `two_entities` - entities at (2,2) and (7,7)
- `adjacent_entities` - entities at (3,3) and (4,3)
- `three_entities_triangle` - for cycle testing

### Async Tests

Use `@pytest.mark.asyncio` decorator. pytest-asyncio is configured in pyproject.toml.

For tick loop tests, use short durations (30-100ms) to keep tests fast.

## Running Tests

```bash
cd world
uv run pytest tests/ -v          # all tests
uv run pytest tests/ -v -k swap  # filter by name
uv run mypy src/world/           # type check
```

## Architecture Decisions (Milestone 3)

### gRPC Service Architecture

Services are implemented in `services/` directory, each as a separate servicer class:

```
services/
├── __init__.py           # Exports all servicers
├── action_service.py     # SubmitIntent RPC
├── discovery_service.py  # ListControllableEntities RPC
├── lease_service.py      # Acquire/Renew/Release lease RPCs
├── observation_service.py # StreamObservations RPC
└── tick_service.py       # StreamTicks RPC
```

### WorldServer: Central Coordinator

`WorldServer` in `server.py` wires everything together:
- Creates shared `LeaseManager` and `TickLoop`
- Registers all service implementations
- Hooks `on_tick_complete` to broadcast observations
- Provides `add_entity()` that tracks spawn ticks

### Lease Management

`LeaseManager` in `lease.py` handles entity control leases:
- Leases expire after 30 seconds by default
- Same controller re-acquiring gets renewal
- Expired leases cleaned up on access or periodic cleanup
- Validation: `is_valid_lease(lease_id, entity_id)`

### Proto Type Conversion

`conversion.py` provides bidirectional conversion:
- `direction_to_proto()` / `direction_from_proto()`
- `position_to_proto()` / `position_from_proto()`
- `entity_to_proto()` / `entity_from_proto()`
- `tile_to_proto()` / `tile_from_proto()`

**Python keyword handling**: Proto fields named `self` or `from` require special handling:
```python
# For 'self' field in Observation:
observation.self.CopyFrom(entity_proto)

# For 'from' field in EntityMoved (constructor uses from_):
pb.EntityMoved(entity_id=id, from_=from_pos, to=to_pos)
```

### Observation Generation (Basic)

Current implementation (no LOS):
- All entities visible to all observers
- Nearby tiles within radius=5 returned
- Movement events generated from `TickResult`

Future work: Line-of-sight filtering, enter/leave events.

## Running the Server

```bash
cd world
# Start server with one entity
uv run python -m world.server --spawn-entity bob:5,5 --tick-duration 1000

# In another terminal, run random agent
cd ../agents
uv run python -m agents.random_agent --entity bob
```

## Gotchas & Learnings

### Observation Timing Model

Observations must be sent at the **start** of a tick (via `on_tick_start`), not after processing. This gives agents time to receive the observation and submit intents before the deadline.

```
Tick N starts → observation sent (tick_id=N) → agent submits → deadline → process → Tick N+1
```

If observations are sent after processing, agents will always be one tick behind and get "wrong_tick" rejections.

### Proto Import Paths

Generated `world_pb2_grpc.py` files have incorrect imports. After running `compile_proto.sh`, fix:

```python
# Change this:
import world_pb2 as world__pb2

# To this:
from . import world_pb2 as world__pb2
```

This must be done in both `world/src/world/` and `agents/src/agents/`.

### Proto Python Keywords

Proto fields named after Python keywords need special handling:

```python
# 'self' field - use CopyFrom after construction
observation = pb.Observation(tick_id=..., ...)
observation.self.CopyFrom(entity_proto)

# 'from' field - use trailing underscore in constructor
pb.EntityMoved(entity_id=id, from_=from_pos, to=to_pos)
```

### Agent Module Imports

To avoid RuntimeWarning when running `python -m agents.random_agent`, use lazy imports in `__init__.py`:

```python
def __getattr__(name: str):
    if name == "RandomAgent":
        from .random_agent import RandomAgent
        return RandomAgent
    raise AttributeError(...)
```

### Test Port Allocation

Integration tests that start `WorldServer` must use unique ports for **both** gRPC and WebSocket to avoid "address already in use" errors when tests run in parallel:

```python
# WRONG - uses default ws_port=8765 which will conflict
server = WorldServer(world, port=50099, tick_config=config)

# CORRECT - unique ports for each test
server = WorldServer(world, port=50099, ws_port=18765, tick_config=config)
```

Port ranges in use:
- gRPC: 50051 (default), 50098-50099 (tests)
- WebSocket: 8765 (default), 18765-18766 (tests)

## Architecture Decisions (Milestone 4)

### ViewerWebSocketService

Chose WebSocket bridge over gRPC-Web for simplicity:

```python
# world/src/world/services/viewer_ws_service.py
class ViewerWebSocketService:
    """Embedded WebSocket server for viewer clients."""
```

**Design choices**:
- Embedded in WorldServer (same process) for direct state access
- JSON messages (not protobuf) for browser compatibility
- Async broadcast queue for non-blocking event distribution
- Uses `websockets` library for async WebSocket server

**Message types**:
- `snapshot` - Sent on connect with full world state
- `tick_started` - Broadcast at tick start with timing info
- `tick_completed` - Broadcast after processing with move results

**Integration hooks**:
- `on_tick_start()` called from WorldServer tick callback
- `on_tick_complete()` called after movement resolution
- `_generate_snapshot()` creates initial state for new clients

### CLI Arguments

Server now accepts `--ws-port` (default 8765):
```bash
uv run python -m world.server --spawn-entity bob:5,5 --ws-port 8765
```

## Future Considerations (Milestone 5+)

### Line-of-Sight (Milestone 8)

Will need to add:
- Bresenham ray casting
- Visibility filtering per observer
- Enter/leave visibility events
