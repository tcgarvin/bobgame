# Changelog

How Bob's World got here, newest first. The CLAUDE.md files describe the system
as it is now; this file is the record of the changes that made it that way.
Entries are grouped by date; text is preserved from the CLAUDE.md files they
were written in.

---

## 2026-09-20 — Simplification (branch `simplify-and-save`)

**One scenario.** `hamlet` is the only world and runner config: six settlers
(ada, bram, cleo, dov, esme, finn), one wolf spawning 30-45 tiles out, 120
ticks between spawn attempts. It generates the 4000x4000 island (seed 12345)
into `saves/island.npz` when the save is missing. `./dev.sh` with no argument
runs it and a missing config is now an error rather than a silent fallback.
`tools/live_run.sh start <seconds>` takes no config. The `settlement`,
`settlement_peaceful`, `island`, `island_small`, `foraging` and `default`
configs were deleted, and so were `agents/src/agents/random_agent.py`
(SimpleAgent / RandomAgent) and `python -m agents`; the runner has no built-in
default agent module and its tests use a fake agent module.

**The invitation mechanic is gone.** `SayIntent.open_to_talk`,
`Entity.open_to_talk`, the `accept` converse action and the viewer's marker
were removed from the world, the proto (field numbers reserved), the agents,
the viewer and the analysis tools. Conversations are started by hailing
(docs/09 section 9). Entity conversion lost the tick parameter it only needed
to compute the derived `open_to_talk` boolean: `entity_to_proto`,
`entity_from_proto`, `entity_state` and `entity_from_state` all take no tick.

**Dead proto and dead code removed.** `UseIntent`, the gRPC `ViewerService`
family and the `EntityEnteredView` / `EntityLeftView` events are gone;
`Entity` now carries `sleeping_on` and `collapsed`, so agents can see that
another settler is asleep and on what. The world server's `--no-record` flag,
the dead terrain decoders and 19 pass-through `submit_<x>_intent` wrappers on
`TickContext` were removed — everything now goes through `submit_intent`, with
`submit_move_intent` the one survivor. `tools/compile_proto.sh` fixes the
generated `world_pb2_grpc.py` relative import itself, so there is no manual
fix-up step any more.

**Analysis tooling.** `tools/analyze_run.py` and `tools/settlement_progress.py`
are thin CLIs over a new `tools/runlib/` package (`runio`, `worldscan`,
`agenttrace`, `cost`, `social`, `report`, `rooms`): one pass over the world
ticks and one read of each agent trace. Recordings that still use the old
`hunger` key classify starvation correctly. The legacy flat `logs/` layout is
no longer read.

**New moon and saving** (docs/14_new_moon_and_saves.md): `WorldClock` gained
`new_moon_tonight`, `next_new_moon_day` and `save_tick`. In progress on this
branch.

**Documentation restructured.** The dated "update" paragraphs and
"<scenario>-run fixes, round N" narratives moved out of `CLAUDE.md`,
`world/CLAUDE.md` and `agents/CLAUDE.md` into this file; those files now
describe the system as it is.

---

## 2026-09-20 — Hamlet-run fixes, round 4

After `runs/20260920-043742-hamlet` (goal: six enclosed personal rooms with a
door and a bed each; at tick 1350: 0 beds, 0 doors, 0 rooms) — every raw source
now carries a **habitat** sentence generated from the terrain generator's own
rules (`items.HABITAT_TEXT`), shown in the narrative's "Where things are found"
list and appended to every `source_text`, so a failed craft says where reeds
grow instead of leaving a settler to random-walk; **sleep** recovers faster by
day (bed 2 per 3 ticks, ground 1 per 2) and `MIN_SLEEP_FATIGUE` (20) refuses a
one-tick nap; `build(..., door="x,y")` places doors in the shape it builds and
the geometry lines list what stands inside an enclosed interior; a refused
`place` names the occupant of the tile and the free neighbouring directions;
`WorldModel.recent_deaths` no longer mutates the deque it iterates (three turns
died with `deque mutated during iteration`); and `travel_to` routes to a free
neighbour when the destination cannot be stood on (32 of 41 `no_path` walks).

Agent-side detail:

- **Habitat as physics.** `items.HABITAT_TEXT` maps a natural object type to
  one sentence saying where it grows, written from `world/terrain/objects.py`
  and its `ObjectPlacementConfig`, with the numbers mirrored as
  `REED_BANK_WIDTH` (2), `REED_COAST_EXCLUSION` (12), `CLAY_MIN_DISTANCE` /
  `CLAY_MAX_DISTANCE` (2, 7), `BUSH_WATER_MIN_DISTANCE` /
  `BUSH_WATER_MAX_DISTANCE` (4, 60), `TREE_COAST_DISTANCE` (40) and
  `ORE_EXCLUSION_RADIUS` (60). `items.source_text` now appends the habitats of
  every source (deduplicated, so four rock types say it once), so every craft
  failure reaching `missing_input_lines` reads `fiber comes from reeds (bare
  hands); reeds grow on the banks and in the shallows of fresh water (lakes,
  rivers and their fords), within 2 tiles of the water, and never within 12
  tiles of the sea`. `items.habitat_table_text()` is the narrative's "Where
  things are found" list, and the hand-written habitat scraps in the
  `Materials` section were removed so there is one statement of each rule.
  `planner._water_hint_lines` adds `nearest water you have seen: (x, y) (dN)`
  under `you know of none yet` for a water-bound material
  (`items.is_water_bound`), from `WorldModel.nearest_water()`. The
  observation's `Tile.floor_type` does **not** distinguish fresh water from the
  sea, so that line says "water", never "fresh water"; the habitat sentence is
  what says the stuff wants fresh water.
- **Sleep retune, take 2.** Round 3 was not enough: 31-45% of all settler-ticks
  were still asleep and 70% of those were in daylight, clearing at the slow day
  rate a debt the 100-tick night cannot. The day rates went up, the bed stays
  strictly ahead of the ground in both periods, and `sleep.MIN_SLEEP_FATIGUE`
  (20) refuses a settler below it with `"not tired enough to sleep: fatigue F,
  and sleep needs fatigue 20"` (the old floor was 1, and one settler took ten
  sleeps of one or two ticks at fatigue 1).
- **`recent_deaths` snapshots its deque.** `WorldModel.recent_deaths` iterated
  `reversed(self.deaths_seen)` while calling `death_of`, which calls
  `_forget_death`, which clears and re-extends the same deque: three planner
  turns died with `deque mutated during iteration` (ada t960 and t1624, esme
  t1626), two of them while building the turn prompt. Both `recent_deaths` and
  `death_of` now iterate `reversed(tuple(...))`.
- **`travel_to` routes to adjacency.** 41 of 194 walks ended `no_path`, and 32
  of those aimed at a tile that cannot be stood on with free reachable ground
  all around it (22 with another settler standing on it, 5 a tree, 3 a wall, 2
  a rock; median 4-5 tiles out). `travel_arrival` has always ended a walk with
  `arrived_next_to`, but nothing planned a route to adjacency, so A* failed, the
  step option vanished and `NO_PATH_PATIENCE` fired after 3 ticks.
  `options._step_option_for_place` now retries `find_path(...,
  stop_adjacent=True)` when the direct path fails and the target is known and
  unwalkable, and puts `stop_adjacent` on the `TravelState` it installs;
  `_travel_control_options` does the same for `keep_going`, so the option does
  not vanish when somebody steps onto the destination mid-walk. The remaining 9:
  1 unknown target 580 tiles off (a planner call, not a routing defect), 1
  builder pocketed in his own hut, 7 aimed inside a one-gap hut whose gap idle
  settlers were standing in.

## 2026-09-20 — Hamlet-run fixes, round 3

Sleeping on the ground now recovers 2 fatigue per 3 ticks at night and 1 per 3
by day (was 1 per 2 and 1 per 4; bed numbers unchanged), because days 4-6 of
`runs/20260920-030501-hamlet` were 41% asleep — 20% idle while the planner
thought, 10% travelling and 3% actually gathering or building.
`wolf_spawn_interval_ticks` is a world-config setting (default 40,
`hamlet.toml` sets 120) so a killed wolf stays gone. `travel_to(x, y)` lost its
`max_ticks` and runs to arrival, no route, danger or hunger on a code-computed
budget. The tick loop now waits out the world's own intent deadline for the
planner, so a single-tick tool can cost one tick instead of two. `place` crafts
the piece when the pack holds the inputs. The world model remembers deaths it
witnessed: `look` lists wolves seen die and wolves merely out of sight,
`start_stint`/`set_reflex` refuse a dead id with the fact, and the journal's day
log records it. The journal writer's prompt now opens with the same goal
sentences as the planner's (`items.island_opening`).

Detail:

- **Ground sleep retune.** Recovery is a rate, not an interval:
  `sleep.recovery_rate(on_bed, night)` returns `(points, ticks)` and
  `_recover_fatigue` sheds `points` on every tick divisible by `ticks`. The
  ground was 1 per 2 at night and 1 per 4 by day, which made a bedless settler
  sleep ~233 of a 300-tick day. `items.SLEEP_RECOVERY` and
  `items.sleep_recovery_text` mirror it, and every agent-facing sleep number is
  generated from them.
- **`travel_to(x, y)` has no `max_ticks`.** One settler made 16 `travel_to`
  calls inside a 10-tile radius, each re-called the moment its model-chosen
  budget ran out, spending a whole 20-call turn and ~220 ticks.
  `planner.travel_budget(model, target)` computes a backstop of `path_length *
  TRAVEL_TICKS_PER_STEP (2) + TRAVEL_TICK_ALLOWANCE (20)`, clamped to
  `TRAVEL_MIN_TICKS` (30) .. `TRAVEL_MAX_TICKS` (600), falling back to the
  straight-line distance when A* finds no route. A model that passes
  `max_ticks` anyway gets `_FriendlyArgsValidator`'s retry (`"travel_to takes:
  x, y"`), not a failed turn.
- **One tick for a one-tick tool.** `direct_action`'s future was already
  resolved at the top of tick N+1 (`_resolve_awaiting_direct`), but nothing on
  the planning path suspends between there and `submit_intent`, so the planner
  task was only *scheduled*: tick N+1 went out as a `Wait` and the next action
  could not leave before N+2. `JevAgent._await_planner_work` now polls
  (`PLANNER_POLL_SECONDS`, 0.02) until a direct or stint request appears or the
  world's own `Observation.deadline_ms` runs out, minus `SUBMIT_MARGIN_MS` (200)
  and capped at `MAX_PLANNER_WAIT_MS` (2000) against clock skew. Measured floor
  before: 2 ticks per action, 3 for 63% of calls; the floor is now 1 whenever
  the model round-trip fits inside the tick's `intent_deadline_ms` (1.2 s).
- **`place` crafts what it is short of**, like `build` and `place_sign`.
  `_stock_one_to_place` runs `_craft_chain(kind, 1)` when the pack is empty and
  the kind has a recipe; on failure the existing shortfall plus
  `items.source_text` lines are returned and nothing is placed.
- **Known-dead entities.** `WorldModel.deaths_seen` is a deque of `DeathSeen`,
  filled from the `entity_died` observation event; `death_of(id)` forgets a
  death once the body has been seen alive since (settlers respawn; wolf ids are
  never reused). `look` gains `wolves you know of but cannot see:` and `wolves
  you saw die:`; `_refuse_dead_targets` raises a `ModelRetry` carrying the fact
  when `start_stint` or `set_reflex` names a dead id; and
  `agent._note_life_transitions` writes each witnessed death into the journal's
  `DayLog`. esme, finn and ada hunted a wolf esme had killed at t953 until
  t2008.
- **The journal writer knows the goal.** `items.island_opening(settler_count)`
  holds the setting and goal paragraph; `settlement_narrative()` and
  `journal_narrative()` both open with it. Nothing was added about what to
  write.

## 2026-09-20 — Hamlet-run fixes, round 2

The same run, read for the shelter goal. esme walled the six free neighbours of
her own tile and starved in the 1-tile cell; cleo built a 3x3 ring with no door
and was never told what it enclosed; dov slept from food 39 to death because
only food 0 woke a sleeper; ada stepped north and south for 108 ticks inside her
own walls toward a bush four tiles away; bram spent 8 of 20 tool calls on calls
that each bounced off one conversation; nobody knew fiber comes from reeds (0
doors, 1 bed); and no journal carried the build site, so each day started a new
wall cluster (5 disjoint ones). Fixed, all facts and no advice:

- **Geometry in build results.** A new `agents/.../enclosure.py` holds one set
  of flood fills three callers share. `BuildExecutor.geometry_lines(model)`
  (kept apart from `summary()`, which the `StintDriver` protocol says takes
  nothing) appends what the standing pieces around the shape now form, from a
  4-connected room fill: `the walls here now enclose N interior tile(s)
  spanning WxH; doors: D; gaps: G; you are inside/outside`, or `these walls
  enclose nothing yet: K gap(s) remain at (x, y), ...`. A closed ring with no
  door and no gap adds `the interior has no entrance: no door and no gap`, and
  when the seal guard made the builder close it from outside, `; the last piece
  was placed from outside, because placing it from inside would have shut you
  in`. `build_would_seal_you_in` now names the tiles it refused. Nothing is said
  about a road, a floor or a bed: only walls and doors bound a room.
- **Seal guard for Jev's `place`.** `options._place_options` skips a structure
  placement where `enclosure.would_seal` says the actor would be left with fewer
  than `SEAL_MIN_FREE_TILES` (64) reachable tiles. A door is passable to
  settlers, so placing one is never a seal.
- **The enclosed fact.** `enclosure.enclosed_fact` returns `!! ENCLOSED: you can
  reach only N tile(s); the pieces around you: wood_wall_31 (N), ....  dismantle
  removes a placed piece (3 extract actions, one item back).` whenever the body
  reaches fewer than `ENCLOSED_REACH_LIMIT` (12) tiles. It is one of
  `planner.body_alerts`, so it reaches every tool result and the turn prompt,
  and `jevstate._facts` appends it to Jev's always-present `facts`.
- **Food ends a stint.** `Stint` ends with `food_low` the tick food *crosses*
  down to `items.FOOD_ALERT_AT` (25) and `food_zero` when it crosses to 0.
  Crossing only, so a stint started hungry is not ended on its first tick, and a
  reflex stint is exempt.
- **`no_path`.** Root cause of ada's 108 ticks: `_step_option_for_place` fell
  back to `_greedy_step` when A* failed, and that memoryless hill-climb offered
  a step on the tile where stepping south shortened the chebyshev distance and
  nothing on the tile where it lengthened it — a 2-cycle re-armed every tick,
  with the option worded "it is beyond what you can see, so keep stepping",
  which is what kept `lost` at 0.3. The fallback is now only for a destination
  `model.is_known` has never seen. On top of that `Stint` ends with
  `END_NO_PATH` after `NO_PATH_PATIENCE` (3) ticks on which no brief target has
  a step option and the body is not already beside one.
- **Interrupted calls are free.** `stint.was_interrupted` spots a result of the
  form `<what> -> interrupted: ...`, and `BudgetedToolset.call_tool` refunds the
  call. `INTERRUPTED_BY_REFLEX`, `INTERRUPTED_BY_CONVERSATION` and
  `conversation_interruption` moved from `agent.py` to `stint.py` so
  `planner.py` can see them. The first interrupted result still does *not* carry
  the conversation report — that would mean blocking a single-tick tool on the
  whole conversation.
- **Craft failures name sources.** `items.source_text(kind)` is generic over
  `EXTRACT_YIELD` / `EXTRACT_TOOLS` / `EXTRACT_REQUIRED_TOOLS`, and
  `planner.missing_input_lines` appends it plus the nearest `SOURCES_SHOWN` (3)
  such objects to a failed `craft` and to `_craft_inputs`' shortfall inside
  `build`.
- **Your own placed pieces.** `enclosure.own_pieces_line` reads
  `ObjectInfo.owner` and groups this settler's standing building pieces into
  8-connected clusters, capped at `OWN_PIECE_CLUSTERS_SHOWN` (6). It is part of
  `describe_world`, so the journal's day log keeps it and the site survives the
  nightly history reset.
- **The hungry wake.** `sleep.HUNGRY_WAKE_FOOD` (20) is one number read both
  ways: `_apply_sleep_intents` refuses a settler at or below it and
  `_wake_reason` returns `hungry` when a non-collapsed sleeper falls to it. The
  old line was food 0 both ways, and a settler slept from food 39 through 156
  ticks and died four ticks after waking.

## 2026-09-20 — Hamlet-run fixes, round 1

The first six-settler run showed planner turns lasting 100-500 ticks with no
sight of the body (four settlers starved mid-turn), `eat` failing
`insufficient_items` 14 times, 30 of 89 `travel_to` calls ending
`ticks_exhausted` (8 of them already standing on the target), and walls going up
slowly because `build` refused a pack that held the materials but not the
pieces. Fixed, all in `planner.py`:

- **The body on every tool result.** `turn_clock_line` now ends `food F/M,
  health H/M, fatigue F/M`, and `body_alerts(model)` returns the `!!` lines —
  physics only, no advice — for food at or below `FOOD_ALERT_AT` (25), food 0
  (the starvation rate and what a berry restores) and fatigue within
  `FATIGUE_ALERT_MARGIN` (10) of `items.MAX_FATIGUE`. A dead or sleeping body
  gets none.
- **`travel_to` arrives.** `travel_arrival(model, target)` returns `arrived`
  when standing on the target, `arrived_next_to` when standing beside a target
  that `is_walkable` refuses, else `""`. The tool checks it before doing
  anything (returning "no walk needed: ... No tick spent.") and hands it to the
  stint as an `end_check`, so the walk ends the tick it arrives instead of
  waiting for two Jev `done` ticks.
- **`eat` with an empty pack.** With no `kind` in the pack it submits nothing:
  if a bush on the actor's own tile has a berry it collects and then eats it,
  reporting both actions; otherwise it lists the nearest known bushes with a
  berry.
- **`build` crafts what it is short of.** `_craft_chain` crafts a kind, crafting
  missing inputs first to `CRAFT_CHAIN_DEPTH` (2: wood -> plank -> wall),
  refusing a station recipe unless `WorldModel.station_near` finds the station on
  or next to the tile. `_run_build` stocks up before the first stint and again
  on every `BUILD_OUT_OF_ITEMS`, at most `BUILD_CRAFT_LIMIT` (20) pieces and
  `BUILD_RESUPPLY_ROUNDS` (3) rounds.
- `tools/analyze_run.py` no longer counts a wolf's death as a settler's,
  reporting `deaths: N settlers by={wolf, starvation, unknown, <settler>}` and
  `wolf kills: N by={<settler>}` separately.

## 2026-09-20 — Hamlet scenario

`hamlet` is a smaller settlement variant — six settlers (ada, bram, cleo, dov,
esme, finn) and a single wolf that arrives 30 to 45 tiles out instead of three
at 20 to 40. The wolf tunables are world config now (`max_wolves`,
`wolf_spawn_min_distance`, `wolf_spawn_max_distance`, validated at load and
defaulting to the settlement values), and the settler count reaches the prompts
through `python -m agents.jev_agent --settlers N`, which
`runner/configs/hamlet.toml` passes. The planner's goal paragraph now names the
shelter: a settlement that lasts, where every one of you has a shelter of your
own to sleep in, and where food, safety and rest are things you can count on
tomorrow. (At the time, the 12-settler `settlement` and `settlement_peaceful`
scenarios were left unchanged; they were deleted later the same day.)

---

## 2026-09-19 — Live-run fixes: purpose, the closing note, asleep visibility

A live run of the hail/signs/no-`say` build (`runs/20260919-211823-settlement`)
showed a sleeping settler was invisible to the planner (13 of 23 hails refused
"X is asleep") and could still be walked into a hail; `write_sign` could
silently overwrite a message board's own note slot; no sign was ever crafted
because `place_sign` required one already in the pack; a wrong tool kwarg
(`sleep(bed_object_id=...)`, the real name is `bed`) failed the whole turn
because pydantic-ai's retry text never named the tool's parameters; and
conversations died after a couple of lines because the converser had no idea why
the conversation started, and its closing call kept only one free-text line.

Fixed: `describe_world`/`look` mark an asleep settler, `talk_to` refuses one up
front; `write_sign`/`write_note` refuse the wrong object naming the right tool;
`place_sign` crafts a sign from 2 wood when needed; every planner tool's
argument validator (`_FriendlyArgsValidator`, wrapped in
`BudgetedToolset.get_tools`) now names the tool's parameters on a bad call, and
retries rose from 2 to 3 (`PLANNER_TOOL_RETRIES`); `talk_to`/
`open_conversation` take a required `purpose` (and Jev's brief hails an optional
one) shown only to the settler that started the conversation; the closing note
is now two fields (`ClosingNote`: `agreed_or_learned`, `you_said_you_would`),
both reaching the journal and the planner's report. Contract: docs/09 section
11.

## 2026-09-19 — Channels

Settlers now have exactly four ways to reach each other, and the prompt states
what each is good for: `shout` (60 tiles, one line, no reply), a conversation
(the only back-and-forth; its opening line is heard within 10 tiles and anyone
who sees it can join, up to 4), a message board (20 notes, standing information,
`look` marks what is new to you) and a sign (one pushed line tied to a place).
`say` and the whole invitation mechanic left the agents' surface: no planner
`say` tool, no Jev `say:`, `invite:` or `talk_to:` options, no
`Brief.invitations`. (The world kept `open_to_talk` and `accept` intact but
dormant at this point; they were removed on 2026-09-20.) In their place the
planner grants Jev **brief hails**: `start_stint(..., hails=[{"settler": "dov",
"line": "..."}])`, at most 3, each naming a settler it has met; Jev is offered
`hail:<settler>` (the hail next to them, a code-owned walk otherwise) and a hail
that lands ends the stint into the conversation, exactly as a join does. A spent
or twice-refused hail drops out of the offer and both facts go into the stint
report. `say`/`shout` also tell the speaker who heard them, and boards track
which notes you have read. Contract: docs/09 sections 9 and 10.

## 2026-09-19 — Hail

A settler no longer needs an invitation to start a conversation.
`ConverseIntent` action `hail` (target + opening line) walks up to another
settler and addresses it: the conversation appears on a free tile next to them
both with the hailer's line first and the target speaking next, and the target
is seated without being asked. A settler cannot be hailed for
`HAIL_COOLDOWN_TICKS` (60) after its last conversation ended. The planner tool
is `talk_to(entity_id, opening_line, max_ticks)`, and the planner can also grant
Jev up to 3 hails per brief. Contract: docs/09 section 9.

## 2026-09-19 — Signs

Settlers can craft a `sign` (2 wood, by hand), place it like any other structure
and write one line of at most 80 characters on it with the existing
`WriteNoteIntent`. A sign blocks nobody, anyone may rewrite or blank it, and it
dismantles like any other built piece. Unlike a message board it is *pushed*:
the first time a settler comes within view (8 tiles) of a written sign, and
again whenever its text changes, the line `[sign at (x, y) by ada, written tick
N: "..."]` lands in the planner's next tool result, its next turn prompt, the
journal's day log and any stint report running at the time. The planner has
`place_sign` and `write_sign` (the generic `place` refuses a sign); Jev sees
signs as `S` with their text and can walk to one, but never places a blank one.
Contract: docs/08_building.md ("Signs").

---

## 2026-09-18 — Week-run fixes

After the first 7-day run (`runs/20260918-165534-settlement`, credit-limited at
tick 1919) the planner no longer takes a turn while the body is asleep,
collapsed or dead (`await_active`), any sleep ends the turn, `build` refuses an
empty pack and names the shortfall and recipe, `look` lists item piles with
contents, a failed `pickup` names the nearest piles, and the prompt states the
physics of dying (pile, respawn place and state, no permanent death, no armor).

## 2026-09-18 — Journal

`memory.md` is no longer an append-only note file. It is a five-section journal
(`Story so far`, `Me`, `Others`, `Learnings`, `Tomorrow`) plus a `Today's notes`
scratch section, and the settler rewrites the five sections itself in one
background model call when it falls asleep or dies, from a de-duplicated log of
the day's tool calls, results, reflections and events. Each section is capped at
600 tiktoken tokens (one retry, then hard truncation). Falling asleep or dying
ends the planner's turn (the budget is spent) and the turn after a wake or a
respawn starts with an empty message history and the fresh journal. The model is
`--journal-model` / `JOURNAL_MODEL`, defaulting to the planner's. Contract:
docs/12_sleep_journal.md.

## 2026-09-18 — Jev context

Jev's walk options are `step_towards:<object id | entity id | place name |
shout:<speaker>>` with a per-type quota (nearest 2 of each object group, every
wolf, nearest 3 settlers) instead of one shared top 6, plus a step toward every
object id the brief names; briefs carry `places` (`{"river": [x, y]}`) so Jev
never sees an absolute coordinate; the state has one always-present `facts` list
(food, wolves, fatigue, day), a `so_far` block (ticks, inventory change, action
counts, net movement this stint), `B`/`b` map glyphs for bushes with and without
berries, and "arrived" instead of "blocked" on a finished walk. Jev is asked a
`lost` question and a stint that scores it twice ends with reason `lost` and a
report line telling the planner to name the target or move closer.
`agents/evals/` is a live functional suite against the real Jev API for prompt
and model-version drift; `evals/replay_states.py` re-asks recorded states. The
planner prompt has a "What Jev sees, and how to write for it" section. `lost`
was added after a settler starved stepping north and south for thirty ticks
toward a bush it had no option to reach while `stuck` sat at 0.4. Also: a stint
trace tick row is written at the *start of the next* `decide`, after
`_detect_blocked_move` has had its say, so a silently blocked move is recorded.
Contract: docs/05 "Stint (Jev executor)".

## 2026-09-18 — Invitations

`say(open_to_talk=True)` keeps a settler open to talk for 40 ticks; a settler
standing next to an open inviter can `accept` (planner tool `talk_to`, Jev
option `talk_to:<id>`) and the world creates the conversation on a free tile
next to both. Briefs carry `invitations` (lines Jev may say with the flag). A
conversation can now start while the planner is mid-turn: in-flight single-tick
tools return "interrupted: conversation conv_N started", stints wait, and the
report arrives in the next tool result. The planner no longer has `move`,
`attack`, `extract` or `collect` tools (Jev, `travel_to` and `build` cover them)
and its budget is 20 calls. Contract: docs/09 section 8. *(The invitation
mechanic itself was superseded by hailing on 2026-09-19 and removed entirely on
2026-09-20.)*

---

## 2026-09-17 and earlier

**Metal and sleep.** Recipes have a station (`workshop_table`, `furnace`,
`anvil`) and a work count; station recipes with work > 1 take one craft action
per tick with progress kept on the station. Copper and iron veins sit in inland
outcrops (never within 60 tiles of the site) and need a pickaxe of a high enough
tier; ore + charcoal smelt to ingots at a furnace, copper tools are made at the
workshop table, iron tools and the iron sword at an anvil. The world has a
300-tick day (night is the last third), settlers have fatigue (tired at 60: tool
work halved, hits 1 softer, no regen; collapse at 100), and sleep on beds or the
ground with `SleepIntent`/`WakeIntent`; the planner has `sleep` and `wake` tools
and `craft` loops multi-tick recipes. Contract: docs/10_metal_and_sleep.md.

**Conversation and reflex.** Settlers can open a conversation on a tile
(`ConverseIntent`: up to 4 seats adjacent to the anchor, world-enforced
round-robin turns, closes on a full round of passes), hand items to each other
(`GiveIntent`), and register a reflex brief with `set_reflex` that the agent
drops into without the planner when a wolf comes within the chosen distance or
bites, during planning, conversations and code-driven stints. In conversation
mode a small "converser" model call takes each turn and a closing call writes a
note to `memory.md`. Contract: docs/09_conversation_and_reflex.md.

**Cooperation.** Wolves are tuned so nobody beats them alone (16 health, bite 3,
simultaneous damage: a lone swordsman loses 12 of 20 health, two armed settlers
lose 6 between them). Settlers have a 60-tile `shout` channel whose events carry
the speaker's position, Jev shouts when a wolf is in view and is offered a walk
to anyone it hears shouting, Jev's state has a `threat` block and the actor's own
name, and the planner's `look` lists every settler met. The planner is told it
runs in real time: every tool result shows the tick, the ticks the turn has cost,
and a `!!` alert when a wolf is near or biting that tells it to hand back to Jev
with a fighting `start_stint`. The planner's tool budget is 20 per turn and
soft: every tool result says what is left, and a spent budget refuses calls
instead of discarding the turn. Details: "Implementation notes" in
docs/05_jev_agents_design.md.

**Building.** Settlers can gather fiber (reeds) and clay, craft planks, rope,
roads, walls, floors, doors, beds, chairs, tables and a workshop table (which
gates the advanced recipes), place them on two object layers, dismantle them,
and rest on beds. Walls block everyone, doors block wolves. The planner has a
deterministic `build` tool for lines and rectangles. The island was regenerated
with groves, outcrops, reeds and clay; the settlement site is a lakeside clearing
at (1539, 974). Contract: docs/08_building.md.

**Settlement mechanics and milestones.** Milestones 0, 1, 2, 3, 4, 5a, 5b and 6,
procedural terrain generation, chunked terrain streaming, and the settlement
mechanics (stats, food, combat, wolves, extraction, crafting, chests, message
boards, say). Milestone 8 (LLM agents) was superseded by `agents.jev_agent`.
Plan: docs/03_implementation_plan.md.

**gRPC thread pool.** Every `StreamObservations` call holds a worker thread for
its whole lifetime. The pool was sized up to 64 in `server.py`; with the old
default of 10, twelve agents starved every unary RPC (renewals failed, leases
expired, agents exited).

**Blocking index.** Movement resolution was fixed to treat a tile as free only
when its occupant's own move succeeds. Before that two settlers could end up on
one tile, and the position index raised `KeyError` a few ticks later, killing the
tick loop; the loop now also logs `tick_loop_crashed` with the traceback.
