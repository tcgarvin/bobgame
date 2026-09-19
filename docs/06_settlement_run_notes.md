# Settlement Runs: Findings and Iteration Log

Overnight build and first runs of the planner + Jev settlement scenario
(2026-09-17). Companion to [05_jev_agents_design.md](05_jev_agents_design.md).

Reproduce: `./dev.sh settlement`, wait, Ctrl+C, then
`python tools/analyze_run.py logs`.

Note: the stat now called `food` (100 = well fed, 0 = starving) was called
`hunger` before 2026-09-18; this document keeps the old name where it records
what happened at the time.

## Run 1 (250 ticks, 12 actors, 2 s ticks)

Numbers from `tools/analyze_run.py`:

| metric | value |
| --- | --- |
| stint ticks (Jev decisions) | 2187 |
| stints | 132 (75 ended by Jev eject, 57 by tick budget) |
| planner turns | 99, 0 API failures |
| Jev latency p50 / p95 | 180 ms / ~400 ms |
| Jev state size | 1.7k–2.3k input tokens per call |
| Jev top-option probability (mean) | 0.50–0.71 per actor |
| intent rejections | 62 (wrong_tick / late_tick), see below |
| wolves spawned | 3; none reached a settler |
| deaths | 0 |

What the settlers did: chopped trees and mined rocks, crafted axes, a
pickaxe, a sword, three chests and a message board; dov placed a chest and a
board next to the spawn and wrote a note announcing the settlement; hale
wrote progress notes on a second board; several planners keep persistent
memory notes with recipe costs and coordinates.

Jev behaviour worth noting:

- Jev handles travel well once pathing is right: `follow_travel` at 0.85–0.97
  probability tick after tick, `extract` at ~0.85 once adjacent.
- `eject` is well calibrated as a "done or pointless" signal: it rose from
  ~0.2 during travel to 0.5–0.65 once the brief's goal was met, and ended
  more stints than the tick budget did.
- `danger` never exceeded 0.5 because no wolf came within view.

### Problems found and fixed during the night

1. **Planner model.** GLM 5.3 flash refuses to disable reasoning and takes
   ~6 s per call; the planner default is now `qwen/qwen3.7-flash` with
   reasoning off (~2 s per call). GLM works with low effort if wanted.
2. **Hunger too fast.** One point per tick killed everyone at tick ~120. Now
   one point per 4 ticks, starvation damage every 4 ticks.
3. **Twelve settlers spawn packed together**, so every first step ran into a
   neighbour and the world silently refused the move. The agent now treats
   tiles occupied this tick as blocked when pathing, and a move that was
   accepted but went nowhere is logged as `blocked` and feeds the
   repeat-failure rule.
4. **gRPC thread pool of 10.** Each observation stream pins a thread; with 12
   agents every unary RPC starved, leases expired, and agents exited. Pool is
   now 64.
5. **Pathfinding blocked the event loop.** Walkability scanned every
   remembered object per A* node. The world model now keeps position indexes;
   A* is capped at 2.5k nodes; travel options do one search instead of two.
6. **First Jev call of a stint sometimes took 5 s** (idle connection). Jev
   calls now have a 0.9 s per-tick budget; on timeout the last action is
   repeated. Stale queued observations are skipped so a slow call costs one
   tick, not three.
7. **Planner text-only loop.** dov's planner drifted into writing
   `start_stint(...)` as prose, made no tool calls, and repeated the same
   thought 40 times. Two consecutive tool-less turns now reset the history.
8. **Wolves never met anyone.** They spawn 20–40 tiles out and wandered
   randomly. Wandering steps now drift toward the nearest settler 60% of
   the time.
9. Stint logs were written under `agents/logs`; the runner now passes
   `--log-root ../logs`.

## Run 2 (768 ticks, after fixes; my shutdown watcher failed so it ran longer than planned)

| metric | value |
| --- | --- |
| stint ticks | 6942 |
| stints | 512 (309 eject, 184 tick budget, 19 ended by death) |
| planner turns | 196, 0 API failures, 6 history resets after text-only turns |
| Jev latency p50 / p95 | 180 ms / ~400 ms; 81 per-tick timeouts (1.2%) handled by repeating the last action |
| intent rejections | 62 late/wrong tick in total, all early in stints; the rest were `dead` |
| wolves | 12 spawned; 9 killed by settlers (bram, greta, cleo, kai x2, esme, jory x2, finn) |
| settler deaths | 58, all to wolves; wolf_6 alone killed 17 settlers |

What happened: the prowling wolves reached the settlement around tick 300.
Settlers with swords killed nine wolves; settlers without weapons died,
often several times each. Jev chose the canned `say:wolf_here` phrase 106
times, so "There is a wolf here!" spread through observations, and planners
reacted to it ("Bram just said there is a wolf here, so I need to eat
first"). Planners wrote strategy notes to memory and to the boards ("craft a
sword before anything else", "coordinate with Ada and Kai as a group of
three, never separate"). Jev's `danger` question fired above 0.5 on 302
ticks, almost all while a wolf was within a few tiles.

Balance conclusions applied after the run: wolf damage 3 → 2 and wolf health
10 → 8, so an unarmed settler survives long enough to run and two settlers
can kill a wolf together. Dead settlers no longer submit intents each tick.

Cost: Jev used roughly 14M input tokens across both runs (~7k calls at
~2k tokens); the planner used well under 10M tokens on Qwen 3.7 flash.

## Open items / ideas

- The viewer could not be checked visually this session (the Chrome
  extension was not connected); its WebSocket feed was verified by a script
  and `npm run build` passes. Open http://localhost:5173 during a run, pick
  an entity in the top-right dropdown, and press `P` for the agent panel.
- Planners often write briefs with several sequential goals ("chop, then
  mine, then craft"); Jev follows the first and ejects. Shorter briefs work
  better; the system prompt could say so explicitly.
- Jev's `say:` options went unused in run 1 but were chosen 106 times in run
  2 once wolves appeared; the canned phrases are worth keeping.
- Milestone 7 (Parquet logging / replay) is still open; `stints.jsonl` plus
  the world log cover analysis for now.
- Tick rate: 2 s / 1.2 s deadline is comfortable. Jev p95 is ~0.4 s, so
  1 s ticks with a 0.6 s deadline should work on a good connection.

## Building update runs (2026-09-17)

Two runs after the building update (docs/08_building.md) on the regenerated
island, site (1540, 972).

| Run | Wolves | Ticks | Settler deaths | Planks | Walls crafted / placed | Other |
|-----|--------|-------|----------------|--------|------------------------|-------|
| `20260917-091932-settlement` | on | 739 | 22 | 45 | 0 / 0 | 2 workshop tables, 1 door crafted, 0 rests |
| `20260917-094801-settlement_peaceful` | off | 830 | 2 (hunger) | 120 | 48 / 20 | 3 beds crafted, 1 placed, 6 rests, 4 build-tool stints |

- The material chain, the workshop gate, the `build` tool and resting all work
  live. Build stints ended `build_done` once and `build_out_of_items` three
  times; nobody sealed themselves in.
- With wolves on, building never starts: every death drops the inventory being
  saved up. Wolf pressure is the bottleneck, not the building mechanics.
- Planners first placed walls one `place` call at a time and only found `build`
  around tick 700. The prompt now shows a worked three-call house example.
- In 830 ticks nobody closed a full house. Longer runs are needed to see one.

## Cooperation update runs (2026-09-17)

Changes under test: wolves 16 health / bite 3 (nobody wins alone), a 60-tile
`shout` channel, Jev's `shout:wolf` and `travel_to:rally:<speaker>` options
and `threat` state block, the settler roster in `look`, and the soft 30-call
planner tool budget. See "Implementation notes" in
[05_jev_agents_design.md](05_jev_agents_design.md).

Why the budget mattered: in `20260917-091932-settlement` the old hard 12-call
limit ended 110 of 155 planner turns. Each of those turns lost its reflection
and its message history, so most planners remembered nothing between turns
except `memory.md`.

Run `20260917-112432-settlement` (wolves on, 233 ticks; it was interrupted
from outside at tick 232, a clean SIGINT shutdown, not a crash):

| metric | value |
| --- | --- |
| planner turns | 28, 0 failures, 0 history resets |
| tool budget | spent (soft) 7 times, hard backstop 0 times |
| wolves | 5 spawned, 3 killed |
| who fought | wolf_1: dov, esme, greta; wolf_2: dov, lena; wolf_3: dov, esme, greta |
| settler deaths | 2 (versus roughly 7 by tick 232 in the previous wolves-on run, with much weaker wolves) |
| shouts | 31 (23 planner, 10 Jev); Jev chose the rally walk 161 times |
| crafts | 7 axes, 4 swords, 1 pickaxe, 5 planks, 1 workshop table placed |

Observations:

- Every wolf that died was attacked by two or three settlers. The tougher
  wolves still dealt 21 to 48 damage per fight, so fights are costly but won.
- Planners use `shout` for introductions and plans as well as alarms, which
  makes nearby Jevs walk over. Worth watching: it may pull settlers off work.
- Jev re-picked the rally walk every tick while already walking. The option is
  now withheld while the current journey already heads to that shouter.
- One planner introduced itself as "Jev". The prompt now says Jev is the reflex
  layer, not a name, and each turn's prompt opens with "Your name is <id>."

### Follow-up runs the same day

A movement bug surfaced first: a settler could step onto a tile whose occupant
had a move claim that then failed. Two settlers shared a tile and the position
index raised `KeyError` a few ticks later, which silently killed the tick loop
(this, not an outside interrupt, is what ended the 233-tick run above at tick
232; a second run died at tick 24). Fixed in `world/movement.py` with regression
tests; the tick loop now logs `tick_loop_crashed` with its traceback.

| Run | Change under test | Ticks | Wolves killed / spawned | Settler deaths (wolf / hunger) | Time in stints | Jev attacks when a wolf is adjacent |
| --- | --- | --- | --- | --- | --- | --- |
| `20260917-115123-settlement` | cooperation update | 580 | 10 / 12 | 29 / 0 | 35% | 43% (23 of 54) |
| `20260917-121935-settlement` | plus real-time planner | 631 | 13 / 14 | 8 / 4 | 58% | 72% (83 of 115) |

- In the first of these, wolves were killed by groups of two to five, but most
  settlers died with allies within 5 tiles: planners fought with single
  `attack` calls (145) and bodies stood idle in planning mode 65% of the time.
- A scripted `fight` tool was tried and rejected by the user: fighting belongs
  to Jev. Instead the planner is told it is on a real-time clock, every tool
  result shows the tick and the ticks the turn has cost, and a `!!` alert says
  to hand back to Jev with a fighting brief. After an alert the next call was
  `start_stint` 162 times out of 267; planner `attack` calls fell to 13.
- Still open: building slowed down in the second run (4 walls placed versus
  15), 27 stints ended in `repeated_failure`, four settlers starved, and
  planners use `shout` for chatter (81 shouts), which pulls nearby Jevs over.
