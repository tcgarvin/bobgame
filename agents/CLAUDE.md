# Agents Package Notes

Development notes for agent implementations.

## Architecture Overview

Agents connect to the world server via gRPC and control entities by:
1. Streaming observations via `ObservationService.StreamObservations`
2. Submitting intents via `ActionService.SubmitIntent`

### Observation Flow

Each tick, agents receive an `Observation` containing:
- `self` - The agent's entity state (position, inventory, etc.)
- `visible_entities` - Other entities within view radius
- `visible_objects` - World objects (bushes, etc.) within view radius
- `visible_tiles` - Terrain information
- `events` - What happened last tick

**Key insight**: `visible_objects` includes state like `berry_count` for bushes. Berry bushes have binary state: `"1"` means has a berry, `"0"` means empty. Check `obj.state.get("berry_count", "0") == "1"` to see if a berry is available.

### Intent Types

Available intents (defined in `proto/world.proto`):
- `MoveIntent` - Move in a direction (N, NE, E, SE, S, SW, W, NW)
- `CollectIntent` - Collect from an object at current position
- `EatIntent` - Consume items from inventory
- `WaitIntent` - Do nothing this tick
- `SayIntent`, `PickupIntent`, `UseIntent` - Not yet implemented

### Foraging Pattern

To collect berries from bushes (binary state: bush either has a berry or doesn't):

```python
def _decide_action(self, observation: pb.Observation) -> pb.Intent:
    self_pos = observation.self.position

    # Check for bushes at current position
    for obj in observation.visible_objects:
        if obj.object_type == "bush":
            if obj.position.x == self_pos.x and obj.position.y == self_pos.y:
                has_berry = obj.state.get("berry_count", "0") == "1"
                if has_berry:
                    return pb.Intent(
                        collect=pb.CollectIntent(
                            object_id=obj.object_id,
                            item_type="berry",
                        )
                    )

    # No bush with berry at position, do something else
    return pb.Intent(move=pb.MoveIntent(direction=...))
```

## SimpleAgent

The `SimpleAgent` class (formerly `RandomAgent`, alias preserved for backward compatibility) implements a state machine for foraging behavior:

### State Machine

```
    ┌─────────┐
    │ WANDER  │ ← No visible berries
    └────┬────┘
         │ sees bush with berries
         ▼
    ┌─────────┐
    │  SEEK   │ → Move toward nearest bush
    └────┬────┘
         │ arrives at bush
         ▼
    ┌─────────┐
    │ COLLECT │ → Collect berry from bush
    └────┬────┘
         │ bush empty or no bush here
         ▼
    ┌─────────┐
    │   EAT   │ → Randomly eat berry (10% chance when idle)
    └─────────┘
```

### States

- **WANDER**: Move randomly when no berries visible
- **SEEK**: Move toward the nearest visible bush with a berry (greedy bee-line)
- **COLLECT**: Collect the berry when standing on a bush that has one
- **EAT**: Occasionally consume a berry from inventory (configurable probability)

### Configuration

```python
agent = SimpleAgent(
    server_address="localhost:50051",
    entity_id="alice",
    eat_probability=0.1,  # 10% chance to eat when idle with berries
)
```

### Key Methods

- `_update_state()` - Transitions between states based on observation
- `_decide_action()` - Returns intent based on current state
- `direction_toward()` - Computes best direction to move toward a target (greedy)

## Running Agents

```bash
# Single agent
cd agents
uv run python -m agents.random_agent --entity alice --server localhost:50051

# With custom eat probability
uv run python -m agents.random_agent --entity bob --eat-probability 0.2
```

Or use `./dev.sh` which starts world, two agents (alice and bob), and viewer together.

### Multi-Agent Setup

The default foraging config spawns two entities (alice and bob) with three berry bushes:

```
alice (2,2)     bush1 (3,3)
                          bush3 (5,5)
                                    bush2 (7,7)     bob (8,8)
```

Run multiple agents in separate terminals:
```bash
# Terminal 1: Start world
cd world && uv run python -m world.server --config foraging

# Terminal 2: Agent alice
cd agents && uv run python -m agents.random_agent --entity alice

# Terminal 3: Agent bob
cd agents && uv run python -m agents.random_agent --entity bob

# Terminal 4: Viewer
cd viewer && npm run dev
```

## Testing Agents

For integration tests, use short tick durations and the world's test fixtures:

```python
# Start a world server with test config
server = WorldServer(world, port=50099, ws_port=18099, tick_config=config)
await server.start()

# Run agent against it
agent = RandomAgent("localhost:50099", "test_entity")
agent.connect()
agent.run(duration_seconds=5.0)
```

## Common Issues

### Agent doesn't collect berries
Check that:
1. The world has bushes spawned (use `--config foraging` or `--spawn-bush`)
2. Agent checks `observation.visible_objects` for bushes at its position
3. Agent submits `CollectIntent` when conditions are met

### Intent rejected with "wrong_tick"
The agent is submitting intents for an old tick. Ensure you use `observation.tick_id` from the current observation.

### Intent rejected with "invalid_lease"
Lease expired. Call `lease_stub.RenewLease()` periodically (default expiry is 30s).

## JevAgent (planner + Jev)

`agents.jev_agent` is the two-layer settler agent described in
[docs/05_jev_agents_design.md](../docs/05_jev_agents_design.md). Run it with:

```bash
cd agents
set -a; . ../.env; set +a          # TYPESAFE_API_KEY, OPENROUTER_API_KEY
uv run python -m agents.jev_agent --entity ada --server localhost:50051
```

Flags: `--log-root` (default `./logs`), `--planner-model`, `--jev-model`,
`--log-level`. Logs go to stderr; per-tick Jev traces go to
`logs/agent-<id>/stints.jsonl` and planner notes to `logs/agent-<id>/memory.md`.

### Module map

| Module | Responsibility |
| --- | --- |
| `client.py` | Async wrapper over the sync gRPC stubs (stream on a thread, unary via `to_thread`), lease renewal every 10 s |
| `geometry.py` | Direction tables, offsets, Chebyshev distance (`+y` is south) |
| `pathfinding.py` | 8-connected A* with the world's diagonal-blocking rule; unknown tiles cost 3 |
| `worldmodel.py` | Everything ever observed: tiles, objects, entities, own history, settlement |
| `options.py` | The legal actions for this tick, each carrying its proto Intent |
| `jevstate.py` | The compact JSON state (with the 17x17 ASCII map) Jev sees |
| `jevclient.py` | The TypeSafe System One call; `JevClient` protocol for fakes |
| `stint.py` | `Brief` -> one Jev call per tick -> Intent, plus the code rules and `StintReport` |
| `planner.py` | The pydantic-ai agent, its tools, and the turn loop |
| `agent.py` | The tick loop and the planner handshake |

### The two modes

- **Stint**: Jev picks one action per tick from the code-enumerated options.
  The stint ends on two consecutive `eject >= 0.7`, an exhausted tick budget,
  death, or the same failed action three times running.
- **Planning**: the tick loop submits `Wait` (or one `Say` on the `thought`
  channel when the planner produces a new reflection) while the planner task
  thinks. Planner tools reach the tick loop through asyncio Futures, so
  `start_stint` resolves only when the stint has actually finished.

Jev and the planner never run at the same time: during a stint the planner task
is parked on the `start_stint` future, and during planning Jev is not called.

### Testing

```bash
cd agents && uv run pytest -q          # offline; a scripted fake replaces Jev
uv run mypy src/agents/jev_agent
uv run black src/agents/jev_agent tests
```

`tests/helpers.py` builds synthetic `Observation` protos and the `FakeJevClient`.
