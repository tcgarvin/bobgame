# 25. Keeps working with a wolf seven tiles off

Full health, reeds underfoot, a wolf across the clearing: a wolf in view is not yet a wolf on you, and abandoning every stint that sees one is how nothing gets built.

*Test*: `evals/test_jev_situations.py::test_keeps_working_with_a_wolf_seven_tiles_off`

## The situation

Ada is at full health with bare hands, standing next to reeds that hold three fiber. A wolf at full health is seven tiles east across open ground. The brief is to gather fiber from reeds_1 and finishes at three fiber, with 30 ticks to do it. The wolf is not adjacent, so there is no attack option, only a walk toward it.

## Why this is a good test

This is the matched negative for `danger`. A wolf seven tiles away cannot bite this tick or the next; it needs several ticks to close, and the settler at 20 health survives more than four bites even once it arrives. The tempting wrong answers are the prudent-looking ones: back away west, or report a high `danger` so the stint ends and the planner is called back to think. Both feel responsible and both are wrong. The stint machinery ends a brief on 0.6 twice in a row, so an inflated number on a wolf in view means that every stint aborts the first time anything with teeth walks into the 17 by 17 window, and nothing ever gets built.

## What the model saw

- **Instruction**: Gather fiber from reeds_1.
- **Success condition**: you are carrying 3 fiber

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
........@r.....W.
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
    "instruction": "Gather fiber from reeds_1.",
    "success_condition": "you are carrying 3 fiber"
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
    "ticks_left": 30,
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
      "id": "reeds_1",
      "type": "reeds",
      "dx": 1,
      "dy": 0,
      "remaining": 3,
      "yields": "fiber"
    }
  ],
  "entities": [
    {
      "id": "wolf_1",
      "type": "wolf",
      "dx": 7,
      "dy": 0,
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
      "dx": 7,
      "dy": 0,
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
| `extract:reeds_1` | harvest fiber from the reeds at dx 1 dy 0, 3 units left (1 work per action bare-handed, 3 work per unit) |
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
| `step_towards:wolf_1` | one step toward wolf_1 (wolf) at dx 7 dy 0, 7 tiles away |

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: extract:reeds_1, danger below 0.5

The assertions the test makes:

```python
assert "extract:reeds_1" in top_keys(decision), f"top two {top_keys(decision)}"
assert decision.danger < DANGER_LOW, f"danger was {decision.danger}"
```

The action is close to forced and the whole scenario separates on one number, which makes the threshold worth defending out loud. 0.5 is a judgement call. The settler is unarmed, the wolf is approaching, and bare-handed a lone settler loses that fight: reading the situation as half dangerous is not foolish. The defence of the low answer is in the wording of the question, which asks about dying within the next few ticks, not about being under threat. A model that scores this high is answering "is there a wolf" rather than "am I about to die". That is the distinction the noul exists to draw, and it is the distinction that lets a settlement tolerate wolves on the map at all.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `extract:reeds_1` > `move_E` x3 | - | 0.98 | 0.02 | 0.14 | 0.06 | 0.04 | 0 |
| gpt-5-nano | 67% | `extract:reeds_1` > `move_E` x3 | - | 0.53 | 0.02 | 0.10 | 0.05 | 0.42 | 0 |
| qwen3.7-flash | 100% | `extract:reeds_1` > `wait` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 67% | `extract:reeds_1` > `move_E` x3 | - | 0.57 | 0.00 | 0.00 | 0.00 | 0.23 | 0 |
| llama-3.1-8b | 100% | `extract:reeds_1` > `move_N` x3 | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.20 | 0 |
| gpt-oss-20b | 100% | `extract:reeds_1` > `wait` x3 | - | 0.93 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `extract:reeds_1` > `move_E` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `extract:reeds_1` > `move_W` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `extract:reeds_1` > `move_E` x3 | - | 0.68 | 0.00 | 0.00 | 0.00 | 0.10 | 0 |
| gpt-4.1-mini | 100% | `extract:reeds_1` > `wait` x3 | - | 0.85 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `extract:reeds_1` > `wait` x3 | - | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 0% | `extract:reeds_1` x3 | - | 1.00 | 0.13 | 0.00 | 0.00 | 0.80 | 0 |
| gpt-4.1-nano-vote | 0% | `extract:reeds_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.87 | 0 |
| gpt-4.1-nano-logprob | 0% | `extract:reeds_1` > `move_W` x3 | - | 0.60 | 0.03 | 0.00 | 0.00 | 0.62 | 0 |
| qwen3.7-flash-vote | 100% | `extract:reeds_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `extract:reeds_1` > `wait` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `extract:reeds_1` | 48 |

## Notes

Every backend that produced a row picked `extract:reeds_1` on every repeat, all 48 of them, so the action column is empty of information and the pass rate is entirely the danger number. Jev passes with 0.04. Ten other backends also pass. The failures are all the same inflation: gpt-5-nano at 0.42 and mistral-nemo at 0.23 fail a repeat each, and the three clear failures are gpt-5-nano under vote at 0.80, gpt-4.1-nano under vote at 0.87 and gpt-4.1-nano under logprob at 0.62. That is the sharpest elicitation finding in these eleven files, and it runs the wrong way. Plain gpt-4.1-nano reports 0.00 here and passes; asked the same question five times as a yes or no and scored by vote fraction, it reports 0.87 and fails. A vote has no way to express "a bit": with a wolf on the map most single samples answer yes, and the fraction turns a shrug into near certainty. Jev's 0.04 here against 0.39 on the starving settler in case 32 and 0.51 on two adjacent wolves in case 34 is a scale being used rather than a switch being thrown.
