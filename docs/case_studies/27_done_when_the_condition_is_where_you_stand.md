# 27. Done when the condition is where you stand

A success condition about position, not inventory: the chest is one tile away, which is what "next to" means, and the pack is irrelevant.

*Test*: `evals/test_jev_situations.py::test_done_when_the_condition_is_where_you_stand`  ·  **expected to fail** (`xfail`)

## The situation

Ada carries nothing and wields nothing. A chest holding four wood sits one tile east, listed in `nearby` at dx 1 dy 0 and drawn as `C` beside the settler on the map. The brief says "Walk to chest_1" and is finished when the settler is standing next to chest_1. Forty ticks are allotted for a journey that is already over. The options are `wait`, all eight move directions, two says, and a withdrawal from the chest.

## Why this is a good test

Every other `done` case in the suite is a count of things in a pack. This one is about position, and the answer is already in the state: dx 1, dy 0 is what "next to" means everywhere else in the world, since extracting, depositing and attacking all require exactly that offset. A model that has learned "done means the inventory matches the number" has nothing to count here and falls back on the instruction verb, which says walk, so it walks. In a real run that is a settler which arrives and does not notice: it steps onto the chest tile and past it, spending a forty-tick stint on a brief that was satisfied before the first tick.

## What the model saw

- **Instruction**: Walk to chest_1.
- **Success condition**: you are standing next to chest_1

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
........@C.......
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
    "instruction": "Walk to chest_1.",
    "success_condition": "you are standing next to chest_1"
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
    "ticks_left": 40,
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
      "id": "chest_1",
      "type": "chest",
      "dx": 1,
      "dy": 0,
      "contents": {
        "wood": 4
      }
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
| `withdraw:chest_1:wood` | take wood out of chest chest_1 (4 inside) |
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

**Gold answer**: done above 0.6

The assertions the test makes:

```python
assert decision.done > DONE_HIGH, f"done was {decision.done}"
```

"Next to" is a convention, and this is where the gold answer is thinnest. The test reads it as the eight neighbouring tiles, consistent with every adjacency rule in the world. A settler could instead read "Walk to chest_1" as "be on it", in which case `move_E` is right, and that is exactly what Jev does. The scenario also has a structural oddity: the test asserts nothing about the action, so a model can step east and still pass by reporting `done` high, and gpt-4.1-mini does precisely that on all three repeats. Whether stepping away from a condition you have just declared met is good settler behaviour is not obvious. This case is marked `xfail` because Jev fails it, at 0.18 here and 0.16 to 0.23 across two runs. The fairest description is that the model has not learned a convention the state does contain, not that it cannot read the state.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 0% | `move_E` > `withdraw:chest_1:wood` x3 | - | 0.92 | 0.18 | 0.10 | 0.03 | 0.02 | 0 |
| gpt-5-nano | 0% | `move_E` > `move_S`, `wait` > `move_E`, `move_E` > `wait` | - | 0.39 | 0.00 | 0.10 | 0.05 | 0.02 | 0 |
| qwen3.7-flash | 0% | `move_E` > `wait` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 0% | `move_N` > `move_NE` x3 | - | 0.91 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 0% | `move_N` > `move_NE` x3 | - | 0.45 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 67% | `withdraw:chest_1:wood` > `wait` x2, `withdraw:chest_1:wood` > `move_E` | - | 0.92 | 0.67 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 0% | `withdraw:chest_1:wood` > `move_W` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 0% | `move_E` > `move_NE` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `withdraw:chest_1:wood` > `wait` x3 | - | 0.50 | 0.99 | 0.00 | 0.00 | 0.05 | 0 |
| gpt-4.1-mini | 100% | `move_E` > `wait` x3 | - | 0.70 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 0% | `move_E` > `wait` x3 | - | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 0% | `move_E`, `move_E` > `wait`, `move_E` > `withdraw:chest_1:wood` | - | 0.73 | 0.20 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-vote | 0% | `move_E` x3 | - | 1.00 | 0.07 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `move_E` > `move_NE` x3 | - | 0.40 | 0.64 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 0% | `move_E` > `move_W`, `move_E` x2 | - | 0.93 | 0.33 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 0% | `move_E` > `wait` x3 | - | 0.95 | 0.12 | 0.02 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `move_E` | 32 |
| `withdraw:chest_1:wood` | 9 |
| `move_N` | 6 |
| `wait` | 1 |

## Notes

gpt-oss-120b is the clear winner: `done` at 0.99 on all three repeats, and `withdraw:chest_1:wood` as its top action, which is a move that only makes sense once you have arrived. gpt-4.1-mini also reaches 100% at `done` 1.00 while still stepping east, and gpt-oss-20b manages 67%. Everyone else, Jev included, reports at or near 0.00 and moves; `move_E` takes 32 of the 48 top-1 picks. The shared failure mode is answering `done` from the instruction verb rather than from the success condition, so a journey that looks unstarted reads as unfinished. Elicitation produced the one surprise: gpt-4.1-nano scores 0% in json and 0% under vote but 100% under logprob at 0.64, so reading a yes or no off the token probabilities got an answer out of the model that asking it to write a number did not. Jev's version of the mistake is the mildest in the table. Its 0.18 sits above the 0.08 it writes on a plainly unfinished brief in case 26, which is why its run-level `done` AUC is a perfect 1.00 and its own best threshold is 0.15. The ordering is correct; the margin is 0.10, and the stint's cut-off is 0.6.
