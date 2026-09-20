# 05. Picks the berry off the bush it stands on



*Test*: `evals/test_jev_basics.py::test_picks_the_berry_off_the_bush_it_stands_on`

## The situation

Ada stands on bush_1, which carries a berry. She has an empty pack, no tool wielded, full health and food 50. The brief says to pick berries from bush_1, and counts the job done at 1 berry. The option list offers `collect:bush_1` for the berry underfoot, the eight moves, `wait` and two `say` lines, and she has 10 ticks.

## Why this is a good test

The judgement is reading an object at dx 0 dy 0 as "here", not "over there". The map does not help: the bush is underneath the `@` glyph, so the view shows bare ground and only the `nearby` list and the option text say the bush is there. The tempting wrong answers are the moves, chosen by a model that thinks it still has to reach something. A settler that moves steps off the bush, loses the collect option, and spends the rest of a 10 tick stint trying to get back to a tile it started on.

## What the model saw

- **Instruction**: Pick berries from bush_1.
- **Success condition**: you are carrying 1 berry

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
........@........
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
    "instruction": "Pick berries from bush_1.",
    "success_condition": "you are carrying 1 berry"
  },
  "self": {
    "name": "ada",
    "position": [
      100,
      100
    ],
    "health": "20/20",
    "food": "50/100",
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
    "ticks_left": 10,
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
      "id": "bush_1",
      "type": "bush",
      "dx": 0,
      "dy": 0,
      "berry": true
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
| `collect:bush_1` | pick the berry off the bush on this tile |
| `move_N` | step one tile N onto known ground |
| `move_NE` | step one tile NE onto known ground |
| `move_E` | step one tile E onto known ground |
| `move_SE` | step one tile SE onto known ground |
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

**Gold answer**: `collect:bush_1`

The assertions the test makes:

```python
assert decision.action == "collect:bush_1", f"chose {decision.action}"
```

There is no honest alternative. Food is 50, one point under the regeneration threshold, so eating the berry afterwards would be sensible, but eating is not on the option list and the brief only asks for the berry to be carried. The one real ambiguity is in the state rather than the choice: an object underfoot is invisible on the map, so a model that reads the picture and not the JSON has nothing to go on. `done` should be low, because the pack is empty.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `collect:bush_1` > `wait`, `collect:bush_1` > `move_E`, `collect:bush_1` > `say:all_good` | 3/3 | 1.00 | 0.03 | 0.12 | 0.05 | 0.02 | 0 |
| gpt-5-nano | 100% | `collect:bush_1` > `wait` x2, `collect:bush_1` > `move_E` | 3/3 | 0.67 | 0.03 | 0.08 | 0.08 | 0.28 | 0 |
| qwen3.7-flash | 100% | `collect:bush_1` > `wait` x3 | 3/3 | 0.95 | 0.67 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `collect:bush_1` > `wait` x3 | 3/3 | 0.88 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `collect:bush_1` > `move_N` x3 | 3/3 | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `collect:bush_1` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `collect:bush_1` > `wait` x3 | 3/3 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `collect:bush_1` > `move_N` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `collect:bush_1` > `wait` x3 | 3/3 | 0.85 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `collect:bush_1` > `wait` x3 | 3/3 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `collect:bush_1` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `collect:bush_1` x3 | 3/3 | 1.00 | 0.47 | 0.00 | 0.00 | 0.53 | 0 |
| gpt-4.1-nano-vote | 100% | `collect:bush_1` x3 | 3/3 | 1.00 | 0.07 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `collect:bush_1` > `move_N` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.10 | 0 |
| qwen3.7-flash-vote | 100% | `collect:bush_1` x3 | 3/3 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `collect:bush_1` > `wait` x3 | 3/3 | 0.95 | 0.00 | 0.00 | 0.00 | 0.04 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `collect:bush_1` | 48 |

## Notes

Every backend passed, 48 of 48 on `collect:bush_1`, so the underfoot case gives nobody trouble. Jev, `gpt-oss-20b`, `gemini-2.5-flash` and the three vote backends were at 1.00 confidence. The noul columns show the usual split: most chat models wrote 0.00 across the board, while `qwen3.7-flash` wrote `done` 0.67 and `qwen3.7-flash-vote` wrote 1.00 with an empty pack, and `gpt-5-nano-vote` wrote `danger` 0.53 on empty grass with no wolf in the state. Jev's own numbers were 0.03 done and 0.02 danger, low but not flat.
