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

Falling asleep already ends the planner's turn, runs the journal rewrite and
arms the history reset for the next wake (docs/12). A settler is **drained**
when all of these hold:

- the body is asleep (or dead and awaiting respawn),
- no stint, reflex stint or conversation session is active or held,
- the planner is parked in `await_active` with its turn ended and the history
  reset pending,
- no journal rewrite and no converser closing call is in flight,
- no request or waiter queue holds an entry.

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
level naming the entities that did not report, removes the partial directory
and carries on. A run is never blocked by a save.

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
