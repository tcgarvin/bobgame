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
- `SayIntent` - Speak on a channel (`local` 10 tiles, `shout` 60 tiles, `thought` viewer only)
- `AttackIntent`, `ExtractIntent` - Fight, and chop/mine/dismantle
- `CraftIntent`, `EquipIntent`, `PlaceIntent` - Make, wield and put down items
- `PickupIntent`, `DropIntent`, `DepositIntent`, `WithdrawIntent` - Piles and chests
- `WriteNoteIntent` - Write a message board slot
- `RestIntent` - Heal on a bed (see docs/08_building.md)

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

Flags: `--log-root`, `--planner-model`, `--jev-model`, `--log-level`. Logs go to
stderr; the trace files go under `<log_root>/agent-<id>/`.

### Trace files and the log root

The log root is `--log-root` when it is given, else `$BOBGAME_RUN_DIR/agents`
when that environment variable is set (`dev.sh` exports it; see
[docs/07_replay.md](../docs/07_replay.md)), else `./logs`. Each agent process
opens four gzip JSONL files in `<log_root>/agent-<id>/` once, writes one JSON
object per line, and flushes with `zlib.Z_SYNC_FLUSH` after every line, so a
reader sees everything up to a Ctrl-C (readers must treat an `EOFError` or
`zlib.error` on the last partial line as end of file). `stints.jsonl.gz` is the
light one: a `stint_start` line with the whole brief, one record per tick with
the chosen action, the full probability map, confidence, eject, danger, latency
and the world's verdict, and a `stint_end` line carrying the end reason and the
rendered `StintReport`. A tick driven by code instead of Jev (the `build` tool)
writes the same row with `"driver": "build"` and without Jev's numbers. `jev_states.jsonl.gz` is the heavy one: the exact state
and criteria sent to Jev, one line per real call (repeats under `check_every`
make no call and write no line; a timed-out call still wrote its state).
`planner.jsonl.gz` holds the planner's turns: `turn_start` with the full prompt,
`tool_call` and `tool_result` with untruncated args and results, `turn_end` with
the reflection, tool count, duration and token usage, plus `turn_failed`,
`tool_budget_spent` (the soft 30-call budget ran out and the turn ended
normally), `tool_budget_reached` (the hard backstop fired) and `history_reset`. `conversations.jsonl.gz` holds one
conversation per `conversation_start`/`turn`/`conversation_end` triple.
`memory.md`, the planner's persistent notes, and `reflex.json`, the registered
reflex brief, sit in the same directory. A file that cannot be opened or written
complains once and then goes inert - tracing never stops the agent.

`tracelog.py` owns this: `JsonlGzWriter`, the per-entity `AgentTrace` (created
once per process by `JevAgent`, closed in `run_agent`'s finally block) and
`resolve_log_root`.

### Module map

| Module | Responsibility |
| --- | --- |
| `client.py` | Async wrapper over the sync gRPC stubs (stream on a thread, unary via `to_thread`), lease renewal every 10 s |
| `geometry.py` | Direction tables, offsets, Chebyshev distance (`+y` is south) |
| `items.py` | The agent-side mirror of `world/src/world/items.py`: recipes, item kinds, object layers, extraction yields |
| `build.py` | Shape geometry and the `BuildExecutor` that drives the planner's `build` tool |
| `pathfinding.py` | 8-connected A* with the world's diagonal-blocking rule; unknown tiles cost 3 |
| `worldmodel.py` | Everything ever observed: tiles, objects, entities, own history, settlement |
| `options.py` | The legal actions for this tick, each carrying its proto Intent |
| `jevstate.py` | The compact JSON state (with the 17x17 ASCII map) Jev sees |
| `jevclient.py` | The TypeSafe System One call; `JevClient` protocol for fakes |
| `stint.py` | `Brief` -> one Jev call per tick -> Intent, plus the code rules and `StintReport` |
| `reflex.py` | The pre-registered reflex brief: persistence, trigger, cooldown, end rule |
| `conversation.py` | Conversation mode: the converser, the per-turn session, the report and the note |
| `llm.py` | Model id resolution and model settings shared by the planner and the converser |
| `tracelog.py` | The gzip JSONL trace files, the `AgentTrace` that owns them, and the log-root rules |
| `planner.py` | The pydantic-ai agent, its tools, and the turn loop |
| `agent.py` | The tick loop and the planner handshake |

### The four modes

`JevAgent.mode` is `planning`, `stint`, `reflex` or `conversation`
(docs/09_conversation_and_reflex.md section 4.1); it is reported to the viewer
on the status channel.

- **Stint**: Jev picks one action per tick from the code-enumerated options.
  The stint ends on two consecutive `eject >= 0.7`, an exhausted tick budget,
  death, or the same failed action three times running.
- **Planning**: the tick loop submits `Wait` (or one `Say` on the `thought`
  channel when the planner produces a new reflection) while the planner task
  thinks. Planner tools reach the tick loop through asyncio Futures, so
  `start_stint` resolves only when the stint has actually finished.
- **Reflex**: the brief the planner registered with `set_reflex` runs as an
  ordinary Jev stint, started by code.
- **Conversation**: the actor holds a seat and answers on its own turn.

Jev and the planner never run at the same time: during a stint the planner task
is parked on the `start_stint` future, and during planning Jev is not called.

`agent.py` is only the sequencer. It checks the reflex trigger at the very top
of the tick (before the last tick's single-tick action is resolved, so an
in-flight one comes back as `interrupted: reflex stint started`), then runs the
reflex stint, then the conversation session, then the ordinary stint or
planning path. A stint that a reflex or a join cut short is *held*: its
`StintReport` waits in `_held_stint` until the reflex line or the conversation
report exists, and `StintReport.append` puts them in one tool result.

### Reflex (docs/09 section 4.2)

`set_reflex(instruction, success_condition, max_ticks, trigger_distance,
notes, shouts)` stores a `ReflexBrief` on the agent and in
`<log_root>/agent-<id>/reflex.json`, reloaded at start; `clear_reflex` removes
it. There is no default. `ReflexWatch` fires it when a living wolf is within
`trigger_distance` (1-8) or an attacker damaged the actor on the tick just
observed; the distance trigger is ignored for `REFLEX_COOLDOWN_TICKS` (10)
after a reflex stint, damage is not. It runs in planning, in conversation and
during driver stints (`build`, code-driven `travel_to`), never during an
ordinary Jev stint. The stint ends when no wolf has been in view for
`REFLEX_CLEAR_TICKS` (3) consecutive ticks (`threat_gone`) or on the usual
stint endings, and the planner is told in one line:
`[reflex ran ticks A-B: ended because R; health X -> Y]`, shown both in the
next tool result (via `BudgetedToolset`) and in the next turn prompt. The
brief itself is in every turn prompt. `stint_start` lines carry `kind`
(`stint` or `reflex`) and, for a reflex, `trigger` and `interrupted`.

### Conversations (docs/09 sections 2, 3 and 4.3)

A conversation is a world object of type `conversation` on an anchor tile;
`worldmodel.py` parses it into `ConversationInfo` and `my_conversation()`
returns the seat this actor holds. Utterances carrying a `conversation_id` are
kept per conversation (`heard_conversation_lines`), because the object only
keeps the last twelve lines; the object's transcript is the fallback for lines
said before the actor joined.

`ConversationSession` owns the body while the seat lasts: `wait` every tick
except on the actor's own turn, and on its turn one call to the **converser**
(`ModelConverser`: two pydantic-ai agents on the planner's model, structured
`ConverserMove` output, no tools, a system prompt with the setting and the
physics of section 2 and nothing else). The call runs as a background task, so
the tick loop never waits on it; it is timed from inside (`run_converser`), and
every tick - including ticks that are not this actor's turn - a call whose turn
the world has moved past is traced `stale` and dropped, cancelled if it is
still running. `give` does not use up the turn, and the session records what
the world made of each of its own moves (`MoveOutcome`, from the digest's
`own_actions`) so the next prompt shows "Your moves so far this turn" with the
failure reason verbatim; a `give` with no receiver or no kind is refused in
code without spending a tick. When the seat
is gone the session makes one more model call - what to keep - and appends it
to `memory.md` as `- [conversation, tick N, with a, b] <text>`, then returns a
`ConversationReport`. Trace: `conversations.jsonl.gz` with `conversation_start`,
`turn` and `conversation_end`.

Planner tools: `open_conversation(direction, opening_line)` and
`join_conversation(conversation_id)` (a code-owned `ApproachDriver` walk to a
free tile beside the anchor, then the join) both block until the conversation
is over and return the report; `give(entity_id, kind, amount)` is single-tick;
`look` lists the conversations in view. Jev's option is
`join_conversation:<id>` - the join intent next to the anchor, otherwise a
code-owned walk like the heard-shout option. A join during a stint ends it with
reason `joined_conversation` and `start_stint` returns after the conversation
with the report appended.

### Building (docs/08_building.md)

`items.py` restates the world's contract for the agent: the full recipe table
(inputs, output count, `needs_workshop`), the ground/structure layer split, the
building kinds, and what `reeds` and `clay_deposit` yield. It is a mirror, so a
change in `world/src/world/items.py` has to be copied here in the same commit.

What Jev may choose (`options.py`):

- **craft** only when the recipe would actually succeed: the inputs are in the
  pack and, for a workshop recipe, `WorldModel.workshop_table_near()` finds a
  placed `workshop_table` on or next to the tile. Craft options are ordered by
  `CRAFT_PRIORITY`, drop recipes for something already carried, and are capped
  at `CRAFT_OPTION_LIMIT`, because `MAX_OPTIONS` is 40 and the movement options
  must survive.
- **place**: one option per carried building item, capped at
  `PLACE_OPTION_LIMIT`. Ground kinds (road, floors) go on the actor's own tile
  with `DIRECTION_UNSPECIFIED`; structures go on the first free neighbour.
- **rest**: only when wounded, fed, and standing on or next to a bed.
- **shout:<n>**: one option per phrase in `Brief.shouts`, which the planner
  writes in `start_stint` (at most `MAX_BRIEF_SHOUTS`, with a cooldown). Jev
  has no shout of its own and none is tied to wolves.
  **travel_to:shout:<speaker>** walks to where a shout came from for
  `HEARD_SHOUT_MAX_AGE_TICKS` after hearing it, whatever it said.
  `jevstate.py` adds a `threat` block with wolf counts and wolf physics (no
  tactics) whenever a wolf is in view.
- **Tools, not rules**: the planner prompt (`SETTLEMENT_NARRATIVE`) gives the
  setting, the goal "build a civilization", the physics with numbers, and how
  to operate Jev. It gives no strategy, etiquette or uses for the tools; those
  are meant to emerge. Keep advice out of option descriptions and alerts too. The wolf numbers in `items.py` mirror
  `world/wolves.py` and `world/items.py`.
- **extract** now covers `reeds` (fiber, no tool) and `clay_deposit` (clay,
  pickaxe).
- **dismantle is deliberately not offered.** It is `ExtractIntent` on a placed
  building, and Jev reads "extract" as "gather", so it would cheerfully eat the
  town wall. Dismantling is a planner tool only.

The planner's `build` tool is the deterministic one, in the spirit of
`travel_to`: `build(kind, shape, x1, y1, x2, y2, max_ticks, skip, tiles)` where
the shape is `line`, `rect` (outline), `rect_filled` or `tiles`, and `skip` is
the door gap, written `"x,y; x,y"`. `make_plan` resolves the shape to an
ordered tile list (a bad kind or shape raises `BuildPlanError`, which the tool
turns into a `ModelRetry`), and a `BuildExecutor` runs it as a **driven stint**:

- `Stint` takes an optional `StintDriver`. With one, Jev is never called, no
  option list is enumerated and no `jev_states` line is written; the driver's
  `stop_reason(model)` is consulted where Jev's rules would be, and
  `choose(model)` returns the tick's `Option`.
- The executor walks with the ordinary path finder, standing on the tile for
  ground kinds and next to it for structures, skipping tiles that already carry
  that layer or are blocked by something else, and refusing any placement that
  would shut the builder into a pocket (a capped flood fill from where it would
  stand, so a wall ring gets closed from the outside).
- Stop reasons: `build_done`, `build_out_of_items`, `build_blocked`,
  `build_danger` (a wolf within `BUILD_DANGER_RADIUS`, or health below the
  stint's `DANGER_HEALTH_FLOOR`), `build_would_seal_you_in`, plus the stint's
  own `ticks_exhausted`, `death` and `repeated_failure`. The tool returns the
  stint report followed by `BuildExecutor.summary()`.
- Trace shape: driver ticks are ordinary `stints.jsonl.gz` tick rows with
  `"driver": "build"` and an action of `build_step:<dir>` or
  `build_place:<kind>:<x>,<y>`. They carry no `latency_ms`, `eject`, `danger`,
  `confidence` or token count, because there was no Jev call and averaging
  zeros would poison `tools/analyze_run.py`.

### Testing

```bash
cd agents && uv run pytest -q          # offline; a scripted fake replaces Jev
uv run mypy src/agents/jev_agent
uv run black src/agents/jev_agent tests
```

`tests/helpers.py` builds synthetic `Observation` protos, the `FakeJevClient`,
the `FakeConverser` and the `converse_object`/`conversation_utterance_event`
builders. No test makes a model call: the planner runs on pydantic-ai's
`TestModel`/`FunctionModel` and the converser on the fake.
