# Settlement Runs: Findings and Iteration Log

Overnight build and first runs of the planner + Jev settlement scenario
(2026-09-17). Companion to [05_jev_agents_design.md](05_jev_agents_design.md).

Reproduce: `./dev.sh settlement`, wait, Ctrl+C, then
`python tools/analyze_run.py logs`.

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
- Jev's `say:` options are never chosen; consider dropping them from the
  option list unless another settler or a wolf is in view.
- Milestone 7 (Parquet logging / replay) is still open; `stints.jsonl` plus
  the world log cover analysis for now.
- Tick rate: 2 s / 1.2 s deadline is comfortable. Jev p95 is ~0.4 s, so
  1 s ticks with a 0.6 s deadline should work on a good connection.
