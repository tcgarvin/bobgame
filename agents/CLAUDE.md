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
- `ConverseIntent`, `GiveIntent` - Conversations and handing items over (docs/09)
- `SleepIntent`, `WakeIntent` - Sleep on a bed (`object_id`) or on the ground
  (empty), and wake again (see docs/10_metal_and_sleep.md)

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

Flags: `--log-root`, `--planner-model`, `--jev-model`, `--journal-model`,
`--settlers`, `--log-level`. Logs go to stderr; the trace files go under
`<log_root>/agent-<id>/`.

`--settlers N` (default `items.DEFAULT_SETTLER_COUNT`, 12) is how many settlers
the scenario started with. It reaches the planner, converser and journal
prompts, which open with "You are one of <N spelled out> people ..."
(`settlement_narrative()`, `converser_narrative()`, `journal_narrative()`; the
module constants `SETTLEMENT_NARRATIVE`, `CONVERSER_NARRATIVE` and
`JOURNAL_NARRATIVE` are those functions at the default). A runner config passes
it per scenario: `runner/configs/hamlet.toml` has `args = ["--settlers", "6"]`.
Nothing else in the agent depends on the count, so it is a prompt fact, not a
world fact.

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
`planner.jsonl.gz` holds the planner's turns: `turn_start` with the full prompt
and a `journal` block (the six journal sections as text, so the viewer can show
the journal at any tick - docs/12),
`tool_call` and `tool_result` with untruncated args and results, `turn_end` with
the reflection, tool count, duration and token usage, plus `turn_failed`,
`tool_budget_spent` (the soft 20-call budget ran out and the turn ended
normally), `tool_budget_reached` (the hard backstop fired) and `history_reset`. `conversations.jsonl.gz` holds one
conversation per `conversation_start`/`turn`/`conversation_end` triple.
`memory.md`, the settler's journal (five sections it rewrites itself at the end
of every day, plus today's notes - docs/12_sleep_journal.md), and
`reflex.json`, the registered reflex brief, sit in the same directory. The
journal's rewrites are traced in `planner.jsonl.gz` as `journal_rewrite`
(carrying the same `journal` block, with the scratch section cleared) and
`journal_rewrite_failed`. A file that cannot be opened or written
complains once and then goes inert - tracing never stops the agent.

`tracelog.py` owns this: `JsonlGzWriter`, the per-entity `AgentTrace` (created
once per process by `JevAgent`, closed in `run_agent`'s finally block) and
`resolve_log_root`.

Every model call's dollar cost is recorded too - `cost_usd` on Jev tick rows,
a `usage` block on planner turns and conversation turns, and `pricing.json`
written at startup with the prices the run is billed at (`pricing.py`). Contract:
[docs/11_cost_accounting.md](../docs/11_cost_accounting.md).

### Module map

| Module | Responsibility |
| --- | --- |
| `client.py` | Async wrapper over the sync gRPC stubs (stream on a thread, unary via `to_thread`), lease renewal every 10 s |
| `geometry.py` | Direction tables, offsets, Chebyshev distance (`+y` is south) |
| `items.py` | The agent-side mirror of `world/src/world/items.py`: recipes, item kinds, object layers, extraction yields |
| `build.py` | Shape geometry and the `BuildExecutor` that drives the planner's `build` tool |
| `pathfinding.py` | 8-connected A* with the world's diagonal-blocking rule; unknown tiles cost 3 |
| `worldmodel.py` | Everything ever observed: tiles, objects, entities, own history, settlement |
| `enclosure.py` | Seals, rooms, the enclosed fact and the actor's own pieces: the flood fills `build.py`, `options.py`, `planner.py` and `jevstate.py` share |
| `options.py` | The legal actions for this tick, each carrying its proto Intent; walking is `step_towards:<target>` with a per-type quota (`STEP_GROUPS`), and `OPTION_SECTIONS` fixes what gets truncated first |
| `jevstate.py` | The compact JSON state (17x17 ASCII map, one `facts` list, the `so_far` block, a `nearby` list of only what the map cannot say) Jev sees |
| `jevclient.py` | The TypeSafe System One call; `JevClient` protocol for fakes |
| `stint.py` | `Brief` (instruction, success condition, notes, shouts, hails, `places`) -> one Jev call per tick -> Intent, plus the code rules, `StintProgress` and `StintReport` |
| `reflex.py` | The pre-registered reflex brief: persistence, trigger, cooldown, end rule |
| `conversation.py` | Conversation mode: the converser, the per-turn session, the report and the note |
| `llm.py` | Model id resolution and model settings shared by the planner and the converser |
| `pricing.py` | What a call cost: the Jev price constant and the `usage` block built from a run's model responses |
| `tracelog.py` | The gzip JSONL trace files, the `AgentTrace` that owns them, and the log-root rules |
| `journal.py` | The sleep-time journal: sections, token cap, the day log and the writer (docs/12) |
| `planner.py` | The pydantic-ai agent, its tools, and the turn loop |
| `agent.py` | The tick loop and the planner handshake |

### The four modes

`JevAgent.mode` is `planning`, `stint`, `reflex` or `conversation`
(docs/09_conversation_and_reflex.md section 4.1); it is reported to the viewer
on the status channel.

- **Stint**: Jev picks one action per tick from the code-enumerated options.
  The stint ends on two consecutive ticks with `done` or `stuck` at or above
  `DONE_OR_STUCK_THRESHOLD` (0.6) (reason `eject`), or with `lost` at or above
  `LOST_THRESHOLD` (0.6) (reason `lost`), on an exhausted tick budget, death,
  or the same failed action three times running.
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

### Invitations to talk (docs/09 section 8) - historical

**The agent side no longer uses invitations (2026-09-19).** There is no planner
`say` tool, no Jev `say:`, `invite:` or `talk_to:` option, no
`Brief.invitations`, no `invitations` block in the Jev state, no open-to-talk
marks in `look`, and `WorldModel` has no `open_invitations()` /
`my_invitation_live()`. The world still implements `SayIntent.open_to_talk`,
`Entity.open_to_talk` and the `accept` action, and the viewer still draws the
marker; nothing on this side reaches for them. Conversations are started by
hailing instead (next section). This paragraph stays because old runs and the
world code still speak the language.

Accepting seats both settlers at once, so a conversation can start while the
planner is mid-turn: `_detect_join` runs before the in-flight single-tick action
resolves, and a seat the actor did not ask for answers that action and every
queued one with `interrupted: conversation conv_N started`
(`INTERRUPTED_BY_CONVERSATION`); a queued `start_stint`, `travel_to` or `build`
simply waits, because `_choose_intent` prefers the session. That conversation's
report has no tool waiting for it, so it goes to the planner as a note:
`drain_reflex_notes` is now `drain_notes` and carries reflex lines and
conversation reports alike. `conversation_start` records `via`: `open`, `join`,
`accept`, or `accepted` for the inviter.

### Hailing (docs/09 section 9)

`talk_to(entity_id, opening_line, max_ticks)` takes up an invitation when the
settler has one open and otherwise **hails** it: the code-owned
`ApproachDriver` walk re-aims at the settler's latest known position for up to
`WALK_LEGS` (3) legs of one tick budget, then submits
`ConverseIntent(action="hail", target_entity_id=..., text=opening_line)` and
blocks until the conversation ends. The world seats the target without asking,
so `joined_conversation` also reads `hail conv_N <target>` (the hailer) and
`hailed conv_N <hailer>` (the target), and `UNASKED_VIA` — `accepted` and
`hailed` — is what `_detect_join` treats as a seat nobody asked for.
`conversation_start` `via` is `hail` or `hailed`. `items.py` mirrors
`ACTION_HAIL`, `ACTION_HAILED`, `CONVERSE_ACTION_TYPE` and
`HAIL_COOLDOWN_TICKS` (60): a settler cannot be hailed until that long after
its last conversation ended.

**Brief hails (2026-09-19).** The planner may also hand Jev up to
`MAX_BRIEF_HAILS` (3) hails per stint:
`start_stint(..., hails=[{"settler": "dov", "line": "..."}])`.
`_validated_hails` refuses a settler this actor has not met (naming who it
has), itself, a blank pair and a line over `CONVERSATION_TEXT_LIMIT`, each as a
`ModelRetry`; `ReflexBrief` has none. `Brief.hails` is a tuple of
`options.BriefHail(settler, line)` - it lives in `options.py` because
`options.py` cannot import `stint.py`. `options.py` offers `hail:<settler>` in
its own `OPTION_SECTIONS` entry (`hail`, between `survival` and
`conversation`, so truncation cannot drop it): the hail intent next to the
settler, otherwise a code-owned `stop_adjacent` walk, and never while this
actor holds a seat or for a settler that is dead, asleep, seated or unseen.
`jevstate.py` puts them in the `brief` block as `say to dov: "<line>"`.
`Stint` drops a hail once it lands and once the world has refused it
`HAIL_REFUSAL_LIMIT` (2) times (`_absorb_hail_outcomes`, which reads
`self._last_option` because a converse failure names no target), and writes
`hailed dov at tick N` / `hail to dov refused: <reason>` into the report's
`notable` list. A landed hail ends the stint with `joined_conversation` like
any other seat.

**Live-run fixes (2026-09-19, docs/09 section 11).** `talk_to` and
`open_conversation` take a required `purpose` argument (only the settler that
opened or hailed sees it, rendered in the converser's prompt as "You started
this conversation because: …"); `BriefHail` gains an optional `purpose` that
flows the same way through `JevAgent._detect_join`/`hailed_target`. The
closing converser call now returns two fields (`ClosingNote`:
`agreed_or_learned`, `you_said_you_would`) instead of one line, both appended
to the journal and both leading `ConversationReport.to_text()`. `describe_world`
marks an asleep settler in view and in the roster, and `talk_to` refuses one
up front instead of walking into a world refusal. `write_sign`/`write_note`
refuse the other's kind of object before submitting anything, and
`place_sign` crafts its own sign from 2 wood when needed.
`BudgetedToolset.get_tools` wraps every tool's argument validator
(`_FriendlyArgsValidator`) so a bad kwarg's retry names the tool's actual
parameters; `sleep`'s parameter is `bed` (was `bed_object_id`), and the
planner agent's tool retries rose from 2 to 3 (`PLANNER_TOOL_RETRIES`).

### Hamlet-run fixes (2026-09-20)

Four things the first six-settler run showed, all in `planner.py`:

- **The body on every tool result.** `turn_clock_line` now ends
  `food F/M, health H/M, fatigue F/M`, and `body_alerts(model)` returns the
  `!!` lines - physics only, no advice - for food at or below `FOOD_ALERT_AT`
  (25), food 0 (the starvation rate and what a berry restores, from
  `items.py`) and fatigue within `FATIGUE_ALERT_MARGIN` (10) of
  `items.MAX_FATIGUE`. `BudgetedToolset.call_tool` appends them under the
  threat alert, and `Planner.build_prompt` puts the same lines above the
  `look`. A dead or sleeping body gets none.
- **`travel_to` arrives.** `travel_arrival(model, target)` returns `arrived`
  when standing on the target, `arrived_next_to` when standing beside a target
  that `is_walkable` refuses, else `""`. The tool checks it before doing
  anything (returning "no walk needed: ... No tick spent.") and hands it to
  the stint as an `end_check`, so the walk ends the tick it arrives instead of
  waiting for two Jev `done` ticks. `Stint` already had `end_check` (the
  reflex uses it); `never_ends` is now public and `AgentBridge.run_stint` /
  `JevAgent.run_stint` / `_StintRequest` carry it through.
- **`eat` with an empty pack.** With no `kind` in the pack it submits nothing
  (no tick): if a bush on the actor's own tile has a berry it collects and
  then eats it, reporting both actions; otherwise it lists the nearest known
  bushes with a berry (`_berry_bush_lines`, in the style of `_pile_lines`).
- **`build` crafts what it is short of.** `_craft_chain` crafts a kind,
  crafting missing inputs first to `CRAFT_CHAIN_DEPTH` (2: wood -> plank ->
  wall), refusing a station recipe unless `WorldModel.station_near` finds the
  station on or next to the tile, and recording everything in a `CraftTally`.
  `_run_build` stocks up before the first stint and again on every
  `BUILD_OUT_OF_ITEMS`, at most `BUILD_CRAFT_LIMIT` (20) pieces and
  `BUILD_RESUPPLY_ROUNDS` (3) rounds, taking the crafting ticks out of the
  tool's own `max_ticks`. The result opens with "for this build, crafted ...";
  a build that still cannot start keeps `missing_pieces_text` plus why the
  crafting stopped.

`tools/analyze_run.py` was fixed in the same pass: `WorldFacts.deaths` and
`killers` are settlers only (wolf ids collapsed to `wolf`, an empty killer
`starvation` only when the tick's `entity_updates` show food 0, else
`unknown`), and a wolf's death is a kill in `WorldFacts.wolf_kills`, keyed by
the settler who landed it.

### Hamlet-run fixes, round 2 (2026-09-20)

The second pass over `runs/20260920-030501-hamlet` (goal: six enclosed personal
rooms). New module `enclosure.py` holds the arithmetic three callers now share.

- **Geometry in build results.** `BuildExecutor.geometry_lines(model)` (kept
  apart from `summary()`, which the `StintDriver` protocol says takes nothing)
  appends what the standing pieces around the shape now form, from a
  4-connected room fill: `the walls here now enclose N interior tile(s)
  spanning WxH; doors: D; gaps: G; you are inside/outside`, or `these walls
  enclose nothing yet: K gap(s) remain at (x, y), ...`. A closed ring with no
  door and no gap adds `the interior has no entrance: no door and no gap`, and
  when the seal guard made the builder close it from outside, `; the last piece
  was placed from outside, because placing it from inside would have shut you
  in`. `build_would_seal_you_in` now names the tiles it refused. Nothing is
  said about a road, a floor or a bed: only walls and doors bound a room.
- **Seal guard for Jev's `place`.** `options._place_options` skips a structure
  placement where `enclosure.would_seal` says the actor would be left with
  fewer than `SEAL_MIN_FREE_TILES` (64) reachable tiles. A door is passable to
  settlers, so placing one is never a seal. esme walled the six free
  neighbours of her own tile one at a time and starved in the cell.
- **The enclosed fact.** `enclosure.enclosed_fact` returns
  `!! ENCLOSED: you can reach only N tile(s); the pieces around you:
  wood_wall_31 (N), .... dismantle removes a placed piece (3 extract actions,
  one item back).` whenever the body reaches fewer than `ENCLOSED_REACH_LIMIT`
  (12) tiles. It is one of `planner.body_alerts`, so it reaches every tool
  result and the turn prompt, and `jevstate._facts` appends it to Jev's always-
  present `facts`. `dismantle` already worked from inside (it is an
  `ExtractIntent` on an adjacent object); `world/tests/test_building.py` now
  pins that.
- **Food ends a stint.** `Stint` ends with `food_low` the tick food *crosses*
  down to `items.FOOD_ALERT_AT` (25) and `food_zero` when it crosses to 0.
  Crossing only, so a stint started hungry is not ended on its first tick, and
  a reflex stint is exempt. `StintReport.end_note` carries the numbers.
  `FOOD_ALERT_AT` moved to `items.py`, because `stint.py` cannot import
  `planner.py`.
- **`no_path`.** ada spent 108 ticks stepping north and south inside a 2-tile
  pocket of her own walls, toward a bush four tiles away. Root cause:
  `_step_option_for_place` fell back to `_greedy_step` when A* failed, and that
  memoryless hill-climb offered a step on the tile where stepping south
  shortened the chebyshev distance and nothing on the tile where it lengthened
  it — a 2-cycle re-armed every tick, with the option worded "it is beyond what
  you can see, so keep stepping", which is what kept `lost` at 0.3. The
  fallback is now only for a destination `model.is_known` has never seen; a
  known tile with no route gets no option at all. On top of that `Stint` ends
  with `END_NO_PATH` after `NO_PATH_PATIENCE` (3) ticks on which no brief
  target (a `places` name or an object id the brief text names) has a step
  option and the body is not already beside one, naming the targets in the
  report.
- **Interrupted calls are free.** `stint.was_interrupted` spots a result of the
  form `<what> -> interrupted: ...`, and `BudgetedToolset.call_tool` refunds
  the call: nothing happened, so nothing is charged. bram spent 8 of 20 calls
  bouncing off one conversation. `INTERRUPTED_BY_REFLEX`,
  `INTERRUPTED_BY_CONVERSATION` and `conversation_interruption` moved from
  `agent.py` to `stint.py` (and are re-imported there) so `planner.py` can see
  them. The first interrupted result still does *not* carry the conversation
  report — that would mean blocking a single-tick tool on the whole
  conversation; the report arrives as a note in the next tool result as before.
- **Craft failures name sources.** `items.source_text(kind)` is generic over
  `EXTRACT_YIELD` / `EXTRACT_TOOLS` / `EXTRACT_REQUIRED_TOOLS`: `fiber comes
  from reeds (bare hands)`, `clay comes from clay_deposit (bare hands, faster
  with a pickaxe)`, `copper_ore comes from copper_vein (needs a pickaxe in
  hand)`. `planner.missing_input_lines` appends it, plus the nearest
  `SOURCES_SHOWN` (3) such objects, to a failed `craft` and to `_craft_inputs`'
  shortfall inside `build`. In the whole run one settler gathered reeds and
  `craft bed -> bed needs 4 plank + 3 fiber` never said where fiber comes from.
- **Your own placed pieces.** `enclosure.own_pieces_line` reads
  `ObjectInfo.owner` (the world's `owner` state key, mirrored as
  `items.OWNER_KEY`) and groups this settler's standing building pieces into
  8-connected clusters: `your placed pieces: 8 wood_wall within (1545, 973)-
  (1547, 975); 1 bed at (1544, 973)`, capped at `OWN_PIECE_CLUSTERS_SHOWN` (6)
  with `and N more group(s)`. It is part of `describe_world`, so the journal's
  day log keeps it (it keeps the last `look`) and the site survives the nightly
  history reset. Journals carried goals across days but never the build site,
  and the run ended with five disjoint wall clusters.
- **The hungry wake** is a world change (`world/CLAUDE.md`, docs/10):
  `items.HUNGRY_WAKE_FOOD` (20) mirrors it into the prompt's sleep physics and
  Jev's `sleep:` option text, and `SleepRecord.to_text` spells the numbers out
  on a `hungry` wake.

### The eject question, split in three (docs/05)

Jev is asked `done` ("is the success condition met right now"), `stuck` ("has
the brief become impossible, or does this need a judgement the brief does not
cover") and `lost` ("is progress impossible because something the brief needs
is missing from this state: a target not on the map or with no step option
toward it, or an item or station you cannot get here; a named place with a
step option is not missing however far away")
as three Nouls every tick. `done` or `stuck` at 0.6 on two consecutive ticks
ends the stint with reason `eject`; `lost` at 0.6 on two consecutive ticks ends
it with reason `lost`, and the report adds a line telling the planner to name
the target by id or as a place, or to move closer. `lost` was added on
2026-09-18 after a settler starved stepping north and south for thirty ticks
toward a bush it had no option to reach while `stuck` sat at 0.4.
`JevDecision.eject` is a derived property, `max(done, stuck, lost)`, so the
traces, the report line (`(done X, stuck Y, lost L, danger Z)`), the viewer
panel and `tools/analyze_run.py`'s eject column keep working unchanged.

The live evaluation suite for these judgements is `agents/evals/` (run with
`uv run pytest evals -q`, needs `TYPESAFE_API_KEY`; see its README). It builds
hand-made states through the real `enumerate_options` and `build_state`, asks
the live model, records every answer under `evals/results/`, and asserts on
generous thresholds. `evals/replay_states.py` re-asks the current questions on
a recorded `jev_states.jsonl.gz`.

### Stations, metal and sleep (docs/10_metal_and_sleep.md)

`items.py` also mirrors the deeper tree: `Recipe` carries `station`
(`""`, `workshop_table`, `furnace` or `anvil`) and `work` (craft actions),
`EXTRACT_TOOLS`/`EXTRACT_WORK_BY_TOOL` give the tool tiers,
`EXTRACT_REQUIRED_TOOLS` gates the two veins, and the fatigue, day and
sleep-recovery numbers live beside them. `recipe_table_text()` renders the
station and the action count, and the planner prompt is generated from it.

`options.py` offers a craft only when the recipe's station is on or next to the
tile, names the work and the progress banked in the station under this actor's
name, refuses a vein the wielded tool cannot bite, and offers `sleep:<bed>`,
`sleep:ground` (whenever fatigue > 0) and `wake` (only while asleep).
`jevstate.py` adds `self.fatigue`, `self.asleep`, the `clock` block and the
fatigue physics line.

**The asleep wait**: while `observation.self.asleep` is true `agent.py` submits
nothing, and calls neither Jev nor the planner nor the converser - the one
exception is a `wake` the planner queued, which is the only intent the world
accepts from a sleeper. The reflex cannot fire while asleep; the tick the actor
wakes is an ordinary tick, so a bite that woke it triggers the reflex there.
Falling asleep and waking are written to `stints.jsonl.gz` as `sleep_start` and
`sleep_end` (with the wake reason). The planner's `sleep` tool parks on
`JevAgent.await_wake` until then; `craft` repeats the craft action until the
recipe completes or an action fails.

### The journal (docs/12_sleep_journal.md)

`memory.md` is the settler's journal: `## Story so far`, `## Me`, `## Others`,
`## Learnings`, `## Tomorrow` and `## Today's notes`. `remember` and the
closing note of a conversation append bullets to the scratch section only; the
five above are rewritten wholesale by one model call (`ModelJournalWriter`,
behind the `JournalWriter` protocol) when the actor falls asleep or dies. The
call runs in the background - one at a time per agent - from a snapshot of the
planner's `DayLog`, which holds every tool call, bare tool result, reflection,
bridge note and life event since the last rewrite, de-duplicated at render time
(last `look` only, no `recall`, consecutive duplicates collapsed, 60k chars).
Each section is capped at 600 tokens: a `ModelRetry` first, then a hard cut.
Falling asleep or dying spends the tool budget so the turn ends, and the turn
after a wake or a respawn drops `Planner.history`; `build_prompt` waits (up to
`JOURNAL_WAIT_SECONDS`) for a rewrite in flight before reading the file.

### Building (docs/08_building.md)

`items.py` restates the world's contract for the agent: the full recipe table
(inputs, output count, `station`, `work`), the ground/structure layer split, the
building kinds, and what `reeds` and `clay_deposit` yield. It is a mirror, so a
change in `world/src/world/items.py` has to be copied here in the same commit.

What Jev may choose (`options.py`):

- **craft** only when the recipe would actually succeed: the inputs are in the
  pack and, for a station recipe, `WorldModel.station_near(kind)` finds a
  placed station of that kind on or next to the tile. Craft options are ordered by
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
  **step_towards:shout:<speaker>** walks to where a shout came from for
  `HEARD_SHOUT_MAX_AGE_TICKS` after hearing it, whatever it said.
  `jevstate.py` adds a `threat` block with wolf counts and wolf physics (no
  tactics) whenever a wolf is in view.
- **Tools, not rules**: the planner prompt (`settlement_narrative()`) gives the
  setting, the goal (a civilization that lasts: a shelter of your own each, and
  food, safety and rest you can count on tomorrow), the physics with numbers,
  and how
  to operate Jev. It gives no strategy or etiquette; those are meant to
  emerge. Keep advice out of option descriptions and alerts too. The wolf
  numbers in `items.py` mirror `world/wolves.py` and `world/items.py`.
  **Owner's decision 2026-09-19**: this relaxed for the communication tools
  only, because settlers were under-using conversations, boards and signs.
  Each one (`shout`, `talk_to`, `open_conversation`,
  `join_conversation`, a message board, a sign - the four channels left after
  `say` was removed on 2026-09-19) now states plainly, in the
  narrative's "Reaching the others, and what each way is good for:" section
  and in its own tool docstring, what it is good for and not good for (a
  fact about the channel - one-way vs. back-and-forth, permanent vs.
  passing - never a "you should"). Strategy and etiquette still stay out.
- **extract** now covers `reeds` (fiber, no tool) and `clay_deposit` (clay,
  pickaxe).
- **dismantle is deliberately not offered.** It is `ExtractIntent` on a placed
  building, and Jev reads "extract" as "gather", so it would cheerfully eat the
  town wall. Dismantling is a planner tool only.

**No planner walking or fighting tools.** The planner has no `move`, `attack`,
`extract` or `collect`; walking, fighting, mining and picking berries go through
Jev, `travel_to` or `build`. A single-tick planner call costs about 3 ticks (2
for the action, ~1 of model thinking) where Jev does the same in 1. `dismantle`
still submits an `ExtractIntent`, and `build` still uses the direction helper.

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

### Signs (docs/08_building.md, "Signs")

A sign is a placed object carrying one line (`text`, `author`, `tick`). The
agent side owns the *read guarantee*: `WorldModel._read_signs_in_view` keeps
`sign_texts_read` (sign id -> the text this actor was shown) and puts one
formatted line per newly-seen or newly-changed sign on `TickDigest.sign_notes`.
`agent.py` pushes each through `_note_for_planner`, so it reaches the next tool
result, the next turn prompt and the journal's `DayLog` exactly as a reflex line
does, and `Stint._absorb` also folds them into the stint's `notable` list. The
actor's own signs are recorded as read without a note.

`jevstate.py` draws a sign as `S` and puts its `text` and `written_by` in
`nearby`; `options.py` gives it a `step_towards` quota group but excludes it
from `JEV_PLACEABLE_KINDS`, because Jev cannot write and a blank sign is
useless. The planner has `place_sign(direction, text)` (place, then write; the
sign id is read back out of the world's `placed <id> at ...` detail) and
`write_sign(sign_id, text)`; the generic `place` refuses `sign`.

### Walking, and what Jev is told (2026-09-18)

- **`step_towards:<target>`** is the one walking option: one A* step, and it
  sets the travel state so `keep_going` (was `follow_travel`) carries on and
  `stop_going` (was `stop_travel`) abandons it. Targets are the brief's own
  (`Brief.places` and every object id the instruction or notes name, matched by
  `OBJECT_ID_PATTERN`), then a per-group quota: `STEP_TARGETS_PER_GROUP` (2) per
  `STEP_GROUPS` entry, every living wolf in view, and the nearest
  `STEP_SETTLER_LIMIT` (3) settlers. Before this the six nearest objects of any
  type shared one budget, so in a grove all six were trees and nothing else was
  reachable. `OPTION_SECTIONS` is the order the sections are offered in;
  `MAX_OPTIONS` (40) cuts the tail, which is always the quota list.
- **Named places**: `start_stint(places={"the_lake": [x, y]})`, validated in
  `_validated_places` (1-24 chars of `[a-z0-9_]`, at most
  `MAX_BRIEF_PLACES`, never an object or entity id). Jev sees them under
  `brief.places` as offsets, never coordinates; `travel_to(x, y)` is just a
  brief with `places={"destination": (x, y)}`. `ReflexBrief` carries them too
  and `reflex.json` persists them.
- **The state**: one `facts` list (food, wolves, fatigue, the day) always
  present instead of three fields hidden in three blocks; a `so_far` block
  (`StintProgress`) with ticks used and left, inventory change, action counts
  and net movement, because Jev has no memory between ticks; `nearby` shrunk to
  what the map cannot express; `B`/`b` on the map for a bush with and without a
  berry (the message board moved to `M`); and `travel.next_step` says `arrived`
  rather than `blocked` when the actor is already there.
- **Trace**: a tick row is written at the *start of the next* `decide`, after
  `_detect_blocked_move` has had its say, and `finish` flushes the last one. It
  used to be written immediately, so a silently blocked move was never in
  `stints.jsonl.gz`.

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
