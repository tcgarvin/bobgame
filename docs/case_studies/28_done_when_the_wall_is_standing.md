# 28. Done when the wall is standing

The wall went up last tick: it is on the map, the pack is empty, and the brief is finished even though nothing was gathered.

*Test*: `evals/test_jev_situations.py::test_done_when_the_wall_is_standing`

## The situation

Ada carries nothing, wields nothing, and a wood wall stands one tile east. It went up on the previous tick. The brief was to place a wood wall next to itself, and is finished when a wood wall stands on a neighbouring tile. There is no place option and no craft option left on the list, because the materials are spent: what remains is `wait`, five move directions and two says.

## Why this is a good test

The evidence of success is a change in the world rather than a change in the pack, and the pack is empty, which is exactly what an unfinished gathering brief looks like. The model has to notice that the thing the brief asked for now exists, listed in `nearby` and drawn on the map. The right action follows from the right judgement: with nothing left to do, `wait` is correct and every move wastes a tick. A failure here is a settler standing beside its own finished wall for twenty ticks, and then a planner turn spent discovering that.

## What the model saw

- **Instruction**: Place a wood_wall next to you.
- **Success condition**: a wood_wall stands on a tile next to you

**The map Jev was shown** (17x17, `@` is the settler):

```text
.................
.................
.................
.................
.................
.................
.................
.................
........@#.......
.................
.................
.................
.................
.................
.................
.................
.................
```

Legend: . walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown

**The facts every state carries**:

- Food falls 1 every 4 ticks. At food 0 you lose 1 health every 4 ticks until you eat. Eating a berry restores 20 food; berries come from bushes marked B on the map (b is a bush with no berry). Health regenerates 1 per 5 ticks only while food is above 50 and you are not tired.
- A wolf has 16 health and bites an adjacent settler for 3 every tick. Every settler attacking the same wolf hits it on the same tick.
- Fatigue rises 1 every 4 ticks by day and every 3 ticks at night. From 60 you are tired: the work a tool adds per extract action is halved and your attacks hit for 1 less. At 100 you collapse where you stand and sleep until fatigue falls to 70. Sleeping on a bed recovers 1 fatigue per tick at night and 1 fatigue per 2 ticks by day; on the ground 1 fatigue per 2 ticks at night and 1 fatigue per 4 ticks by day.
- A day is 300 ticks: the first two thirds are light and the rest is night.

**The rest of the state**, as sent (JSON):

```json
{
  "brief": {
    "instruction": "Place a wood_wall next to you.",
    "success_condition": "a wood_wall stands on a tile next to you"
  },
  "self": {
    "name": "ada",
    "position": [
      100,
      100
    ],
    "health": "20/20",
    "food": "80/100",
    "fatigue": "0/100 (fresh)",
    "asleep": false,
    "wielded": "",
    "inventory": {},
    "at_workshop_table": false,
    "at_furnace": false,
    "at_anvil": false
  },
  "so_far": {
    "ticks_used": 0,
    "ticks_left": 20,
    "inventory_change": {},
    "actions": {},
    "moved_from_start": "dx 0 dy 0",
    "net_tiles_moved": 0
  },
  "clock": {
    "day": 0,
    "tick_of_day": "40/300",
    "night": false
  },
  "settlement": {
    "dx": 0,
    "dy": 0
  },
  "travel": null,
  "nearby": [
    {
      "id": "wood_wall_1",
      "type": "wood_wall",
      "dx": 1,
      "dy": 0
    }
  ],
  "entities": [],
  "map_legend": ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": []
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `move_N` | step one tile N onto known ground |
| `move_S` | step one tile S onto known ground |
| `move_SW` | step one tile SW onto known ground |
| `move_W` | step one tile W onto known ground |
| `move_NW` | step one tile NW onto known ground |
| `say:come_here` | say "Come to me." out loud to anyone nearby |
| `say:all_good` | say "All good here." out loud to anyone nearby |

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: done above 0.6

The assertions the test makes:

```python
assert decision.done > DONE_HIGH, f"done was {decision.done}"
```

Little room to argue, but two wrinkles are worth naming. The map glyph for a wall is `#`, the same character used for blocked terrain, so a model reading the map rather than the `nearby` list sees an obstacle and not an achievement. And `wait` is also what a confused model does when nothing appeals, so the action column alone cannot separate understanding from paralysis. The `done` number is what separates them. gpt-oss-20b is the illustration: it waits on all three repeats while reporting `done` 0.00 and `lost` 0.67, which is the right action reached by the wrong reasoning.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `wait` > `move_W` x3 | - | 0.83 | 0.76 | 0.20 | 0.09 | 0.03 | 0 |
| gpt-5-nano | 0% | `move_W` > `move_N`, `move_W` > `wait` x2 | - | 0.39 | 0.02 | 0.03 | 0.03 | 0.15 | 0 |
| qwen3.7-flash | 100% | `wait` x3 | - | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 0% | `move_N` > `wait` x2, `move_S` > `wait` | - | 0.87 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 0% | `move_N` > `wait` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 0% | `wait` x2, `wait` > `say:all_good` | - | 0.80 | 0.00 | 0.33 | 0.67 | 0.00 | 0 |
| gemini-2.5-flash-lite | 0% | `move_W` > `move_NW` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 0% | `move_N` > `move_W` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `wait` > `move_N` x3 | - | 0.74 | 0.98 | 0.02 | 0.01 | 0.01 | 0 |
| gpt-4.1-mini | 100% | `wait` > `say:all_good` x3 | - | 0.90 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `wait` x3 | - | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 0% | `move_W` > `wait`, `wait` > `move_W`, `wait` > `move_N` | - | 0.73 | 0.33 | 0.00 | 0.00 | 0.73 | 0 |
| gpt-4.1-nano-vote | 0% | `move_N` > `move_NW` x2, `move_W` > `move_N` | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.07 | 0 |
| gpt-4.1-nano-logprob | 0% | `move_N` > `move_W` x3 | - | 0.40 | 0.00 | 0.00 | 0.06 | 0.11 | 0 |
| qwen3.7-flash-vote | 100% | `wait` x3 | - | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `wait` > `move_N` x3 | - | 0.97 | 0.97 | 0.05 | 0.00 | 0.01 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `wait` | 26 |
| `move_N` | 14 |
| `move_W` | 7 |
| `move_S` | 1 |

## Notes

Jev passes on all three repeats at `done` 0.76, waiting each time. qwen3.7-flash, gemini-2.5-flash and gpt-4.1-mini write 1.00 and gpt-oss-120b 0.98, so the top of the field is comfortable. The failures split into two distinct modes rather than one. The first is not recognising the finished condition and wandering off: mistral-nemo, llama-3.1-8b and gpt-4.1-nano put a move first at `done` 0.00, and `move_N` takes 14 of the 48 top-1 picks. The second is gpt-oss-20b's, which is more interesting: correct action, `done` 0.00, `lost` 0.67. It read the empty pack and the absent place option as "this brief cannot be done from here" rather than "this brief is done", which is the same evidence pointed in the opposite direction. Elicitation did nothing for gpt-4.1-nano, which fails at 0% in json, vote and logprob alike. The Jev detail is the 0.76 itself. It is the only mid-range yes in the table, clearing the stint's 0.6 without reaching for the 1.00 that several chat models write on anything they believe, and it comes off the same scale that produced 0.08 on case 26.
