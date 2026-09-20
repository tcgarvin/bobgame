# 09. Places the wall it carries



*Test*: `evals/test_jev_basics.py::test_places_the_wall_it_carries`

## The situation

Ada stands on open grass carrying two wood_walls and nothing else. Nothing is in view: the `nearby` list is empty and the map is bare. The brief says to place a wood_wall on the tile next to her, and counts the job done when a wood_wall stands next to her. Exactly one placement is offered, `place:wood_wall:N`, alongside the eight moves, `wait` and two `say` lines. She has 10 ticks.

## Why this is a good test

The judgement is recognising that a carried building piece can be put down here, with no site to reach and no station to stand at. The tempting wrong answers are the moves, and `move_N` in particular, because a model that reads "on the tile next to you" as a destination will try to go there first. A settler that walks instead of placing wanders with a wall in its pack, and in a real run that is how a wall line ends up one tile off or never gets built at all.

## What the model saw

- **Instruction**: Place a wood_wall on the tile next to you.
- **Success condition**: a wood_wall stands next to you

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
    "instruction": "Place a wood_wall on the tile next to you.",
    "success_condition": "a wood_wall stands next to you"
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
    "inventory": {
      "wood_wall": 2
    },
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
  "nearby": [],
  "entities": [],
  "map_legend": ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": []
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `place:wood_wall:N` | set down the wood_wall on the empty tile to the N |
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

**Gold answer**: `place:wood_wall:N`

The assertions the test makes:

```python
assert placements, f"no placement offered in {sorted(criteria)}"
assert decision.action in placements, f"chose {decision.action}"
```

The test accepts any `place:wood_wall:*` key, but the option builder only offered the north tile, so in practice the scenario collapses to "place rather than move or wait". That makes it easier than the assertion suggests, and it means the scenario does not test direction choice at all. Which tile the wall goes on is a convention the brief leaves open on purpose. `done` should be low, because no wall is standing yet, and this is another spot where a model can confuse "I have a wall" with "a wall stands next to me".

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `place:wood_wall:N` > `move_S`, `place:wood_wall:N` > `say:all_good`, `place:wood_wall:N` > `move_NE` | 3/3 | 0.99 | 0.05 | 0.13 | 0.05 | 0.02 | 0 |
| gpt-5-nano | 100% | `place:wood_wall:N` > `move_E`, `place:wood_wall:N` > `move_N` x2 | 3/3 | 0.59 | 0.00 | 0.03 | 0.03 | 0.09 | 0 |
| qwen3.7-flash | 100% | `place:wood_wall:N` > `wait` x3 | 3/3 | 0.95 | 0.67 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `place:wood_wall:N` > `wait` x3 | 3/3 | 0.87 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `place:wood_wall:N` > `move_N` x3 | 3/3 | 0.79 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `place:wood_wall:N` > `wait` x3 | 3/3 | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `place:wood_wall:N` > `move_N` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `place:wood_wall:N` > `move_N` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `place:wood_wall:N` > `wait` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `place:wood_wall:N` > `wait` x3 | 3/3 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `place:wood_wall:N` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `place:wood_wall:N` x3 | 3/3 | 1.00 | 0.60 | 0.00 | 0.00 | 0.13 | 0 |
| gpt-4.1-nano-vote | 100% | `place:wood_wall:N` x3 | 3/3 | 1.00 | 0.67 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `place:wood_wall:N` > `move_N` x3 | 3/3 | 0.70 | 0.24 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 100% | `place:wood_wall:N` x3 | 3/3 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `place:wood_wall:N` > `wait` x3 | 3/3 | 0.95 | 0.00 | 0.01 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `place:wood_wall:N` | 48 |

## Notes

Every backend passed, 48 of 48 on `place:wood_wall:N`, with Jev at 0.99 confidence. The failure mode to watch for was the false `done`, and it showed up again: `qwen3.7-flash-vote` wrote 1.00, `qwen3.7-flash` 0.67 and `gpt-5-nano-vote` 0.60, all above the 0.6 gate, for a settler whose wall is still in its pack. That is the same confusion as case 06, from the same backends. Jev wrote 0.05. Most other chat models wrote 0.00 for all four nouls, which is safe here and carries no information anywhere else.
