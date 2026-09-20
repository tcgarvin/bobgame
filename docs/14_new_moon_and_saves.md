# 14. The new moon, saving and resuming

A run can be saved and later resumed as an **exact continuation**: the same
world state at the same tick, and every settler with the same knowledge,
journal and counters. Saves are taken only at one kind of moment, which the
game itself creates: the **new moon**, a night on which every settler falls
asleep at the same tick. The forced sleep happens on every new moon whether or
not a save is written, so taking a save never changes what a run does.

Bit-identical *futures* are not a goal and not possible (model calls are not
seeded, and whether an intent makes a tick's deadline depends on the wall
clock). The guarantee is about state at the save tick.

**Status: implemented**, and verified live on 2026-09-20: a save with all six
settlers mid-stint completed in about a minute, and the resumed run woke
everyone into journal-fed first turns.

## 1. The new moon (world physics)

Config (`world/configs/hamlet.toml`, validated at load):

| key | default | meaning |
|---|---|---|
| `new_moon_every_days` | `0` | `0` = never. Otherwise day `d` (0-based) has a new-moon night when `(d + 1) % new_moon_every_days == 0`. Hamlet sets `3`: the nights of days 2, 5, 8, ... |
| `save_on_new_moon` | `true` | write a save on every new-moon night (section 3) |
| `save_wait_seconds` | `180` | how long the world waits for the settlers' snapshots |

On a new-moon day, with `N = night_start_tick(day_length)`:

1. **Tick-of-day `N`: everyone falls asleep.** After the normal sleep phase,
   every living non-wolf entity that is awake is put to sleep where it stands:
   in a free bed it is standing next to (lowest object id) if there is one,
   otherwise on the ground. Not a collapse (`collapsed` stays false). The event
   is `acted(entity, "sleep", True, "new moon")`. The usual refusals
   (`MIN_SLEEP_FATIGUE`, food below `HUNGRY_WAKE_FOOD`) do not apply.
2. **Every open conversation closes** the same tick with end reason
   `new_moon`.
3. **The still window**: for `NEW_MOON_STILL_TICKS` (6) ticks starting at `N`,
   nothing wakes a sleeper (rested, damaged, hungry, bed removed are all
   ignored), a `WakeIntent` is refused with `"new moon"`, and a respawn that
   falls due is held until the window ends. After the window the ordinary wake
   rules apply again, so a rested or hungry settler gets up then.
4. **Wolves lie low all night.** From tick-of-day `N` to dawn, wolves submit no
   intents and none spawn. The wolf RNG is not consumed.

The world's `WorldClock` (proto and model) gains `new_moon_tonight: bool` and
`next_new_moon_day: int` (`-1` when the setting is `0`), plus `save_tick: int`
(section 3; `0` except on the tick a save is taken). Nothing else about the
clock changes.

### What the settlers are told

Facts only, in line with the rest of the prompt:

- The planner's narrative (physics section) states the rule: how often, the
  tick of day, that everyone falls asleep where they stand (in a bed they are
  standing next to, otherwise on the ground), that nothing wakes anyone for a
  few ticks, and that wolves do not hunt that night.
- The clock line on every tool result and the turn prompt carries
  `new moon tonight (everyone falls asleep at tick-of-day 200)` on the day, and
  `next new moon: night of day D` otherwise.
- Jev's `facts` list carries the same line on the day.
- The journal writer's input carries the same clock fact, so `Tomorrow` can
  plan around it.

## 2. What "drained" means for a settler

Falling asleep ends everything that holds the body: the planner's turn, any
stint or reflex stint, any conversation seat, and the in-flight single-tick
action. It then runs the journal rewrite (docs/12). While the body is asleep,
collapsed or dead the tick loop **refuses** new requests from the planner
(`inactive_reason()`) instead of queueing them, so nothing new can start during
the night. A settler is **drained** when all of these hold:

- the body is asleep (or dead and awaiting respawn),
- no stint, reflex stint or conversation session is active, held or queued,
- the planner is parked in `await_active` with its turn ended,
- no journal rewrite and no converser closing call is in flight,
- no single-tick action is in flight and no tool is waiting for a conversation.

Two things deliberately do **not** block a drain. A `sleep` tool parked on
`await_wake` does not: that is what a settler who chose to sleep looks like all
night, and counting it would abandon the save for anyone who went to bed early.
And the planner's history reset is not checked, because it is never pending
while asleep — the reset reason is set at the *wake*, and the snapshot stores no
message history at all, which is the same state by another route.

If a journal rewrite failed, the restored day log is part of the snapshot, so
nothing is lost.

## 3. The save

The save tick is `T = N + NEW_MOON_SAVE_OFFSET` (3) of a new-moon night, inside
the still window.

1. At the start of tick `T` the world pushes observations as usual. The
   observation's clock carries `save_tick = T` (0 on every other tick).
2. Instead of opening the intent window, the world **pauses** and waits, up to
   `save_wait_seconds`, for one snapshot file per non-wolf entity:
   `runs/<run>/saves/tick-<T>/agents/<entity_id>.json.gz`.
3. Each agent, once it has folded observation `T` and is drained, writes its
   snapshot to a `.partial` name and renames it into place.
4. When every file is present the world writes
   `runs/<run>/saves/tick-<T>/world.json` and `objects.jsonl.gz` (state at the
   start of tick `T`, before any intent of `T`), then `complete.json`
   (`{format_version, tick, run_id, entities: [...]}`) last. A save directory
   without `complete.json` is not a save.
5. The world resumes tick `T` normally.

On timeout the save is abandoned: the world logs `save_abandoned` at error
level naming the entities that did not report, renames the directory to
`tick-<T>.abandoned` (whatever the agents did write is kept, for working out
who was late) and carries on. A run is never blocked by a save.

### World snapshot contents

`format_version`, `tick`, `day_length_ticks`, `settlement`, map path and
sha256, the full config the run was started with, `object_id_seq`, the respawn
ledger (`death_ticks`), the wolf simulator's RNG state and id counter, every
entity as `Entity.model_dump()` in registry order, and every object in
registry order (`objects.jsonl.gz`). Derived indexes and chunk caches are
rebuilt on load. Loading refuses an unknown `format_version` or a map whose
sha256 differs.

### Agent snapshot contents

`format_version`, `entity_id`, `tick`, the `WorldModel` (tiles, objects,
entities, clock, settlement, history, heard, damage log, deaths seen, entity
types, conversation lines, sign and board read-tracking; not the derived
position indexes or `last_digest`), the planner's `turn` counter, reports kept
for the next prompt, last thought, `DayLog`, journal sections cache, pending
history-reset reason, `CostLedger`, `ReflexWatch` counters, `SleepRecord` and
asleep-since fields, and the two note queues. `memory.md` and `reflex.json`
are copied alongside. No message history is stored: a drained planner has
none to keep. An agent that finds it is not drained at `T` does not write a
snapshot (and the save is abandoned by the timeout), it never writes a partial
truth.

## 4. Resuming

`./dev.sh --resume <run_id>` resumes from the newest complete save of that run
(`--resume <run_id>@<tick>` picks one).

- A **new run directory** is created; its `meta.json` carries `parent_run_id`
  and `resumed_from_tick`. Nothing is ever appended to the parent's files. The
  new run's `world/objects.jsonl.gz` baseline is the restored object set and
  its first tick record is `T`.
- The world starts with `--resume <save dir>`: terrain from the map file,
  everything else from the snapshot, config from the snapshot (not from the
  TOML on disk, which may have changed).
- Each agent starts with `--resume-from <save dir>`; `memory.md` and
  `reflex.json` are copied into the new run directory first.
- The world holds tick `T` until every settler in the save has an observation
  stream open (`save_wait_seconds`, then an explicit startup error), so no
  settler misses a tick.
- The observation for `T` is delivered again. The agent must treat a repeated
  tick as a refresh: no duplicate history, heard, day-log or trace entries.
- Everyone is asleep in the still window; they wake by the ordinary rules and
  the planner's first turn starts from the journal, exactly as after any night.

## 5. Tests that pin the guarantee

- World: a small world with wolves on, a conversation, station craft progress,
  a dead settler awaiting respawn and a hail cooldown; `process_tick` for 50
  ticks from a loaded snapshot produces the same `TickResult`s and final state
  as the uninterrupted run with the same (scripted) intents.
- World: new-moon sleep, the still window, held respawn, wolves lying low,
  conversation close, config validation.
- Agents: snapshot round trip equality for `WorldModel` and the scalars;
  a repeated observation is idempotent; a settler that is not drained refuses
  to snapshot; forced sleep mid-turn and mid-stint drains through the existing
  sleep path.
- Tools: `analyze_run.py` and the replay server read a run whose first tick is
  not 0 and show its parent.

## 6. Implementation notes (world)

Where the contract lives in code, and the few places the implementation had to
decide something the contract left open.

### Modules

- `world/src/world/moon.py` - the physics. Pure helpers (`is_new_moon_day`,
  `next_new_moon_day`, `in_still_window`, `save_tick_of_day`) plus the
  world-level predicates `is_forced_sleep_tick`, `is_still` and
  `wolves_lie_low`, and `apply_new_moon`, which does the forced sleep and
  closes every conversation. `tick.py` calls it once, right after the ordinary
  sleep phase; `sleep.py`, `stats.py` and `wolves.py` each consult one
  predicate.
- `world/src/world/snapshot.py` - writing, guarding and loading a save.
- `world/src/world/save_coordinator.py` - `SaveSettings` (everything a save
  needs that the world does not carry) and `SaveCoordinator`, which decides
  when a save is due, waits for the settlers and writes the world's half. The
  tick loop holds one and calls two methods, so the pause is testable on its
  own with a tmp_path and a fake agent.
- `World.new_moon_every_days` carries the setting, so `World.clock` can fill
  `new_moon_tonight` and `next_new_moon_day` without a lookup into config.

### Decisions

- **`next_new_moon_day` is today when today is a new moon.** It is the first
  day at or after the current one with a new-moon night, so a settler reading
  the clock on the day never sees a date in the past.
- **Wolves still despawn during the quiet night.** Lying low means no spawn
  and no intent; a wolf that has wandered 50 tiles away is still culled, which
  consumes no randomness and keeps the entity list honest.
- **The intent window is restarted after the pause.** The tick's deadline was
  set when the tick began, and a save takes as long as the settlers need.
  After the save the deadline is pushed out by one `intent_deadline_ms`, so
  tick `T` runs normally rather than rejecting every intent as late.
- **The save directory is `tick-<T>` from the start**, not `tick-<T>.partial`:
  the agents write into it before the world writes anything. Atomicity is per
  file (`world.json.partial` and `objects.jsonl.gz.partial`, each renamed) plus
  `complete.json` last.
- **`ObservationService._last_result` starts empty on a resume.** The first
  observation at `T` therefore replays no events from tick `T-1`. That tick's
  events were already delivered in the run being resumed, and the agent folds
  the repeated observation as a refresh, so nothing is lost.
- **The wolf RNG state is JSON as `[version, [ints...], gauss|null]`** and is
  restored through `random.Random.setstate`. `WolfSimulator` grew
  `rng_state`/`restore_rng_state` and `id_counter`/`set_id_counter` so nothing
  outside reaches into its privates; `World` grew `object_id_seq`/
  `set_object_id_seq` and `add_entity_unplaced` (a dead settler holds no tile,
  and two of them may share the coordinates they died on).

### The interface the agents use

- Each agent learns the run directory from `$BOBGAME_RUN_DIR`, as it always
  has, and the save tick from `WorldClock.save_tick` on the observation.
- It writes `"$BOBGAME_RUN_DIR"/saves/tick-<save_tick>/agents/<entity_id>.json.gz`.
  The directory (including `agents/`) exists before observation `T` is pushed.
- On a resume, `runner` appends `--resume-from <save dir>` to every agent
  command. `<save dir>` is an absolute path to the **parent** run's
  `saves/tick-<T>`; each agent reads its own `agents/<entity_id>.json.gz` from
  it. `dev.sh --resume` copies the parent's `memory.md` and `reflex.json` into
  the new run directory first, so the journal is where the agent expects it.
  The runner also accepts `$BOBGAME_RESUME_FROM`, which is what `dev.sh` sets.

## 7. Implementation notes (agents)

### Modules

- `agents/.../worldmodel/` — `WorldClock` parses `new_moon_tonight`,
  `next_new_moon_day` and `save_tick`, and `moon_text()` renders the one line
  every surface shows. `WorldModel.to_payload()/from_payload()` serialise
  everything the model remembers; the derived position indexes are rebuilt on
  load and `last_digest` is not carried. `update()` folds an observation for
  the tick it has already seen as a **refresh**: tiles, objects, entities,
  self and clock are taken, the events are not, and the digest comes back
  empty with `repeated=True`.
- `agents/.../snapshot.py` (new) — the versioned pydantic `AgentSnapshot`,
  `capture`, `restore`, `write_snapshot` (gzip JSON via `.partial` + rename),
  `read_snapshot` (explicit `SnapshotError` on a bad version, a bad file or
  another settler) and `snapshot_path`. Every component serialises itself
  (`WorldModel`, `Planner`, `CostLedger`, `ReflexWatch`, `DayLog` all gained
  `to_payload`/`from_payload`|`load_payload`); this module only assembles.
- `agents/.../agent/` — `saving.py` holds `DrainState` and where a snapshot
  goes; `core.py` holds `JevAgent.drained()`, `inactive_reason()`, the
  forced-sleep drain, `load_snapshot` and `_maybe_save`, plus the
  `--resume-from` path through `run_agent`.
- `agents/.../items.py` — re-exports `NEW_MOON_STILL_TICKS` (6) and
  `night_start_tick(day_length)` from `bobgame_rules.clock`, which the world
  reads too. The **period** is not there at all: it is world config and reaches
  the settlers only through the clock.

### What the settlers are told

The planner's narrative states the rule in the sleep section, in terms of "the
night of a new moon" with the tick of day, the still window, the conversation
close and the wolves; the concrete day comes from the clock. `turn_clock_line`
(every tool result) and `describe_world` (the turn prompt) append
`; new moon tonight (everyone falls asleep at tick-of-day 200)` or
`; next new moon: night of day D`, and nothing at all when
`next_new_moon_day` is -1. Jev's `facts` carry the line only on the day
itself. The journal writer's `rewrite` gained a `clock_fact` argument, shown
above "Write your journal now".

### Draining, and one bug it found

Falling asleep now ends everything that holds the body, whatever caused the
sleep (`JevAgent._drain_for_sleep`, run once per sleep): the in-flight
single-tick action is resolved from the world's own event and the rest of the
queue is refused `failed: asleep`, an active stint and a reflex stint are
finished (`asleep`, or `new_moon` on a new-moon night, with a factual
`end_note`), and a conversation seat is closed the same way
(`conversation.END_ASLEEP` / `END_NEW_MOON`, each with a sentence of physics in
the report). It happens before `end_turn_now` and the journal rewrite, so a
conversation's closing note still reaches the day it belongs to.

**This is a behaviour change beyond the new moon, and it fixes a bug.** A stint
used to be suspended for the whole sleep and resumed on the tick the settler
woke (`test_a_stint_resumes_on_the_tick_the_settler_wakes`, now rewritten).
But falling asleep already spends the planner's tool budget so the turn ends —
and the turn cannot end while `start_stint` is parked on a stint that will not
report until morning. A collapse therefore stranded the planner for the whole
night, and the brief it resumed was written for yesterday, before the journal
rewrite and the history reset. Ending the stint is both what `drained` needs
and what the sleep contract in docs/12 always implied.

### The second bug: a request queued after the drain

The first live save hung on a settler inside the planner's `build` tool. A
multi-phase tool runs its own Python loop — `build`'s three resupply rounds,
`craft_once`'s work loop, `recipes.craft_chain`, `talk_to`'s walk-then-hail,
`place_sign` — and that loop went on calling the bridge after the drain had
swept the queues. The stint it queued sat there all night, `drained()` answered
"a stint is queued", and the save was abandoned.

The fix is section 2's refusal rule: `run_stint`, `direct_action`, `wait_ticks`
and `await_conversation` all check `JevAgent.inactive_reason()` (`asleep`,
`new_moon` or `death`) before queueing anything. A refused stint comes back as
a finished `StintReport` of zero ticks carrying that reason, which is what
breaks every one of those loops. The `sleep`/`wake` tools' `await_wake` is the
one deliberate exception, as section 2 says.

`drained()` otherwise checks exactly what section 2 lists, and its
`DrainState.reason` is the sentence a skipped save reports.

### The save and the resume

On the tick whose clock carries `save_tick == tick`, `_maybe_save` polls
`drained()` every 100 ms up to `BOBGAME_SAVE_WAIT_SECONDS` (default 170 s,
under the world's 180 s) and then writes
`$BOBGAME_RUN_DIR/saves/tick-<T>/agents/<entity_id>.json.gz`. Blocking the
tick loop there is safe: the world is paused, and lease renewal is its own
task. A settler that has not drained writes nothing and says why, at error
level and as a `save_skipped` line in `planner.jsonl.gz`; a written save is a
`save_written` line carrying the path and the byte count, and a write that
raised is `save_failed`.

`python -m agents.jev_agent --resume-from <save dir>` builds the agent as
usual, restores it, and only then connects, so the re-delivered observation
for tick `T` lands on a model that has already seen it. `_handle_tick` skips
the life and sleep transitions for a repeated tick, so nothing is traced,
logged or day-logged twice. The cost ledger and the turn counter continue; the
trace files are fresh files in the new run directory.

### Size

Tiles are the bulk of a settled model, so they are stored as parallel arrays
with the floor types interned and the two booleans packed into one integer.
Measured (`test_a_hundred_thousand_tiles_fit_in_a_small_file`): **102,400 tiles
cost 180 KB gzipped** (1.9 MB before compression); the test asserts under 3 MB.

### Tests

`agents/tests/test_new_moon_and_saves.py` (31 tests): the clock's wording, the
tool-result line, Jev's facts on the day only, the narrative's rule; forced
sleep mid-stint, mid-turn, mid-conversation and mid-reflex; the drained
predicate and its reasons; `WorldModel` round-trip equality and rebuilt
indexes; every scalar through a written and re-read file; the size bound; a
repeated observation; version and entity-id mismatches; a resumed settler
waking into a journal-fed turn; the save written where the world waits; and a
settler that is not drained writing nothing.
