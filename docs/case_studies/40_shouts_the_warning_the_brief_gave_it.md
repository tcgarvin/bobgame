# 40. Shouts the warning the brief gave it

The brief hands over one phrase and says shout it when a wolf appears; a wolf has just appeared eight tiles away.

*Test*: `evals/test_jev_situations.py::test_shouts_the_warning_the_brief_gave_it`

## The situation

ada has an axe, full health and an empty pack, and a tree with 4 wood in it stands one tile east. A wolf at 16 health has just come into view eight tiles south with no settler near it. The brief says chop wood, and shout the warning as soon as you see a wolf, and it hands over one phrase for the purpose: `shout:0` says "Wolf near the river!" to everyone within sixty tiles, along with where it came from. The success condition is still carrying 6 wood.

## Why this is a good test

This is the early-warning half of the rally loop, where case study 37 is the answering half. Eight tiles is no threat to this settler, so there is no survival pressure to force the issue, and the tempting answer is to chop now and shout later, by which time the wolf is adjacent to someone else. The option list also offers `step_towards:wolf_1`, which would walk a lone settler into a fight it cannot win. A settlement whose settlers never raise the alarm is one where the first anyone hears of a wolf is the bite.

## What the model saw

- **Instruction**: Chop wood, and shout the warning as soon as you see a wolf.
- **Success condition**: you are carrying 6 wood

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
........@T.......
.................
.................
.................
.................
.................
.................
.................
........W........
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
    "instruction": "Chop wood, and shout the warning as soon as you see a wolf.",
    "success_condition": "you are carrying 6 wood"
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
    "wielded": "axe",
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
      "id": "tree_1",
      "type": "tree",
      "dx": 1,
      "dy": 0,
      "remaining": 4,
      "yields": "wood"
    }
  ],
  "entities": [
    {
      "id": "wolf_1",
      "type": "wolf",
      "dx": 0,
      "dy": 8,
      "health": "16/20",
      "wielded": ""
    }
  ],
  "map_legend": ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": [],
  "threat": {
    "wolves_in_view": 1,
    "nearest_wolf": {
      "id": "wolf_1",
      "dx": 0,
      "dy": 8,
      "health": "16/20",
      "settlers_next_to_it": 0
    },
    "settlers_within_3_of_you": 0
  }
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `shout:0` | shout "Wolf near the river!" - every settler within 60 tiles hears it and where it came from |
| `extract:tree_1` | harvest wood from the tree at dx 1 dy 0, 4 units left (3 work per action with the axe, 3 work per unit) |
| `move_N` | step one tile N onto known ground |
| `move_S` | step one tile S onto known ground |
| `move_SW` | step one tile SW onto known ground |
| `move_W` | step one tile W onto known ground |
| `move_NW` | step one tile NW onto known ground |
| `say:come_here` | say "Come to me." out loud to anyone nearby |
| `say:all_good` | say "All good here." out loud to anyone nearby |
| `step_towards:wolf_1` | one step toward wolf_1 (wolf) at dx 0 dy 8, 8 tiles away |

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: shout:0

The assertions the test makes:

```python
assert "shout:0" in criteria, f"no shout offered in {sorted(criteria)}"
assert "shout:0" in top_keys(decision), f"top two {top_keys(decision)}"
```

Chopping this tick is genuinely defensible. The wolf is eight tiles away, "as soon as you see" is satisfied a tick later too, and the shout costs an action. The gold treats "as soon as" as this tick, which is a convention the brief writes and the test enforces rather than something the world makes true.

As in case study 37, the assertion is on the top two rather than the top choice, and Jev passes with `extract:tree_1` first and `shout:0` second in all three repeats. Live, that means the warning does not go out this tick, and whether it goes out at all depends on the wolf's next move changing the distribution. The pass is real, but it is a pass on ranking, not on doing.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `extract:tree_1` > `shout:0` x3 | - | 0.72 | 0.02 | 0.25 | 0.09 | 0.07 | 0 |
| gpt-5-nano | 0% | `extract:tree_1` > `move_N` x3 | - | 0.50 | 0.05 | 0.07 | 0.05 | 0.40 | 0 |
| qwen3.7-flash | 100% | `extract:tree_1` > `shout:0` x3 | - | 0.72 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `shout:0` > `step_towards:wolf_1` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| llama-3.1-8b | 100% | `extract:tree_1` > `shout:0` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 0% | `extract:tree_1` > `wait` x3 | - | 0.79 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 0% | `extract:tree_1` > `step_towards:wolf_1` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 0% | `extract:tree_1` > `move_N` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.13 | 0 |
| gpt-oss-120b | 100% | `shout:0` > `extract:tree_1` x3 | - | 0.45 | 0.00 | 0.00 | 0.00 | 0.10 | 0 |
| gpt-4.1-mini | 100% | `extract:tree_1` > `shout:0` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `shout:0` > `extract:tree_1` x3 | - | 0.61 | 0.00 | 0.00 | 0.00 | 0.10 | 0 |
| gpt-5-nano-vote | 33% | `extract:tree_1` > `shout:0`, `extract:tree_1`, `extract:tree_1` > `step_towards:wolf_1` | - | 0.73 | 0.13 | 0.00 | 0.00 | 0.73 | 0 |
| gpt-4.1-nano-vote | 33% | `extract:tree_1` > `move_N`, `extract:tree_1` > `shout:0`, `extract:tree_1` > `move_S` | - | 0.50 | 0.00 | 0.00 | 0.00 | 0.87 | 0 |
| gpt-4.1-nano-logprob | 0% | `extract:tree_1` > `move_N` x3 | - | 0.40 | 0.00 | 0.00 | 0.03 | 0.38 | 0 |
| qwen3.7-flash-vote | 0% | `extract:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.40 | 0 |
| qwen3.7-flash-logprob | 100% | `extract:tree_1` > `shout:0` x3 | - | 0.62 | 0.00 | 0.01 | 0.00 | 0.04 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `extract:tree_1` | 39 |
| `shout:0` | 9 |

## Notes

`shout:0` was the top-1 answer 9 times of 48 and `extract:tree_1` 39 times. Only mistral-nemo, gpt-oss-120b and gemini-2.5-flash put the shout first. Jev, qwen3.7-flash, llama-3.1-8b, gpt-4.1-mini and qwen3.7-flash-logprob pass on second place. gpt-5-nano, gpt-oss-20b, gemini-2.5-flash-lite, gpt-4.1-nano, gpt-4.1-nano-logprob and qwen3.7-flash-vote scored 0%.

- The failure mode is the same one as in case study 37: the success condition is read as the whole brief and the instruction's second clause is dropped. It is the single most common error across this block.
- gpt-oss-20b and gemini-2.5-flash-lite did not rank the shout in their top two at all, and gemini-2.5-flash-lite's runner-up was `step_towards:wolf_1`, the one option that gets a lone settler killed.
- Elicitation hurt again. qwen3.7-flash goes from 100% to 0% under voting, and the vote variants of both gpt models land at 33%.
- Jev is being asked to do two things at once here, stay calm and still speak. It gets the calm exactly right, with `danger` at 0.07 for a wolf eight tiles off, matching its answer on case study 25, and it gets the speaking to second place.
