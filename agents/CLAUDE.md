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

**Key insight**: `visible_objects` includes state like `berry_count` for bushes. Check `obj.state.get("berry_count", "0")` to see how many berries are available.

### Intent Types

Available intents (defined in `proto/world.proto`):
- `MoveIntent` - Move in a direction (N, NE, E, SE, S, SW, W, NW)
- `CollectIntent` - Collect from an object at current position
- `EatIntent` - Consume items from inventory
- `WaitIntent` - Do nothing this tick
- `SayIntent`, `PickupIntent`, `UseIntent` - Not yet implemented

### Foraging Pattern

To collect berries from bushes:

```python
def _decide_action(self, observation: pb.Observation) -> pb.Intent:
    self_pos = observation.self.position

    # Check for bushes at current position
    for obj in observation.visible_objects:
        if obj.object_type == "bush":
            if obj.position.x == self_pos.x and obj.position.y == self_pos.y:
                berry_count = int(obj.state.get("berry_count", "0"))
                if berry_count > 0:
                    return pb.Intent(
                        collect=pb.CollectIntent(
                            object_id=obj.object_id,
                            item_type="berry",
                            amount=1,
                        )
                    )

    # No bush at position, do something else
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
- **SEEK**: Move toward the nearest visible bush with berries (greedy bee-line)
- **COLLECT**: Collect berries when standing on a bush with berries
- **EAT**: Occasionally consume berries from inventory (configurable probability)

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
