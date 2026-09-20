# 15. Lost when target out of view

No river, no reeds, no step option towards either: the brief is unreachable.  This is the recorded failure the `lost` question was written for - Jev used to step north and south for thirty ticks at stuck 0.4.

*Test*: `evals/test_jev_judgement.py::test_lost_when_target_out_of_view`

## The situation

Ada stands in the middle of an empty seventeen by seventeen view with an axe wielded and an empty pack. She is healthy, fed and fresh. Two trees are in sight, one to the north east and one to the south west. There is no water anywhere on the map, no reeds, and the brief names no places. The brief says "Walk to the river and collect fiber from reeds", with "you are carrying 2 fiber" as the success condition and forty ticks to do it in. Every option on offer is a compass step, a step toward one of the two trees, a wait, or a say.

## Why this is a good test

This is the scenario the `lost` noul was written for. The recorded failure behind it is a settler that spent thirty ticks stepping north and south toward something it had no option to reach, with `stuck` sitting at 0.4 the entire time. The judgement being isolated is whether a model can tell that the thing the brief needs is absent from the state, rather than merely far away. Both wrong answers are comfortable: a compass step feels like searching, and `step_towards:tree_1` is the only concrete target in the world, even though a tree yields wood and the brief wants fiber. A failure costs forty ticks of walking and a planner turn that never learns the brief was unwritable.

## What the model saw

- **Instruction**: Walk to the river and collect fiber from reeds
- **Success condition**: you are carrying 2 fiber

**The map Jev was shown** (17x17, `@` is the settler):

```text
.................
.................
.................
.................
.................
............T....
.................
.................
........@........
.................
.................
....T............
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
    "instruction": "Walk to the river and collect fiber from reeds",
    "success_condition": "you are carrying 2 fiber"
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
      "dx": 4,
      "dy": -3,
      "remaining": 4,
      "yields": "wood"
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
| `move_NE` | step one tile NE onto known ground |
| `move_E` | step one tile E onto known ground |
| `move_SE` | step one tile SE onto known ground |
| `move_S` | step one tile S onto known ground |
| `move_SW` | step one tile SW onto known ground |
| `move_W` | step one tile W onto known ground |
| `move_NW` | step one tile NW onto known ground |
| `say:come_here` | say "Come to me." out loud to anyone nearby |
| `say:all_good` | say "All good here." out loud to anyone nearby |
| `step_towards:tree_1` | one step toward tree_1 (tree) at dx 4 dy -3, 4 tiles away |
| `step_towards:tree_2` | one step toward tree_2 (tree) at dx -4 dy 3, 4 tiles away |

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: _none recorded; the test's own assertions are the gold answer_

The assertions the test makes:

```python
assert not (
    decision.action.startswith("move_")
    and decision.confidence > BLIND_STEP_CONFIDENCE
), f"stepped blindly: {decision.action} at {decision.confidence}"
assert decision.lost > LOST_HIGH, f"lost was {decision.lost}"
```

The second assertion, that the settler must not take a compass step at confidence above 0.5, is a convention rather than a fact. A real settler in this position would reasonably pick a direction and walk. What the stint cannot do is tell a considered search apart from a flail, so the rule is that guessing is allowed but confident guessing is not. Jev threads that needle by choosing `step_towards:tree_1` at confidence 0.21, which is a weak pick with the numbers carrying the meaning. The 0.5 cut-off on `lost` is also a judgement call. It sits below the 0.6 that would actually end a stint, so a model at 0.45 here would fail the test and behave correctly in a run.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `step_towards:tree_1` > `move_N` x3 | - | 0.21 | 0.02 | 0.48 | 0.78 | 0.02 | 0 |
| gpt-5-nano | 0% | `move_E` > `move_SE`, `move_E` > `move_S`, `move_E` > `move_NE` | - | 0.39 | 0.00 | 0.05 | 0.02 | 0.03 | 0 |
| qwen3.7-flash | 0% | `move_N` > `move_NE` x2, `move_N` > `move_W` | - | 0.36 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 0% | `step_towards:tree_1` > `move_E` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 0% | `move_N` > `move_NE` x3 | - | 0.45 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `wait` > `move_N`, `wait`, `wait` > `step_towards:tree_1` | - | 0.60 | 0.00 | 0.00 | 0.97 | 0.00 | 0 |
| gemini-2.5-flash-lite | 0% | `move_N` > `move_W` x3 | - | 0.30 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 0% | `move_E` > `move_NE` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `wait` > `move_E` x3 | - | 0.40 | 0.00 | 0.00 | 1.00 | 0.00 | 0 |
| gpt-4.1-mini | 0% | `move_E` > `step_towards:tree_1`, `move_E` > `move_NE`, `move_N` > `move_NE` | - | 0.50 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 0% | `move_N` > `move_NE` x3 | - | 0.25 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 0% | `move_E` x2, `move_E` > `move_W` | - | 0.87 | 0.07 | 0.00 | 0.00 | 0.40 | 0 |
| gpt-4.1-nano-vote | 0% | `move_E` x2, `move_E` > `move_SE` | - | 0.87 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `move_E` > `move_NE` x3 | - | 0.40 | 0.01 | 0.01 | 0.73 | 0.04 | 0 |
| qwen3.7-flash-vote | 33% | `move_E` > `move_N`, `wait` > `move_N`, `wait` > `move_S` | - | 0.60 | 0.00 | 0.20 | 0.33 | 0.00 | 0 |
| qwen3.7-flash-logprob | 0% | `move_N` > `move_NE` x3 | - | 0.38 | 0.00 | 0.08 | 0.43 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `move_E` | 18 |
| `move_N` | 16 |
| `wait` | 8 |
| `step_towards:tree_1` | 6 |

## Notes

The hardest scenario in this set. Only four backends pass: Jev at lost 0.78 across all three repeats, gpt-oss-20b at 0.97, gpt-oss-120b at 1.00 and gpt-4.1-nano-logprob at 0.73. qwen3.7-flash-vote passes one repeat of three. Eleven backends fail with an identical mode: pick a compass direction, report lost 0.00. Eighteen of the 48 top-1 picks are `move_E`, sixteen are `move_N`, and gemini-2.5-flash, qwen3.7-flash and gpt-4.1-mini answer 0.00 on all four nouls.

- mistral-nemo fails differently and worse, committing to `step_towards:tree_1` at confidence 0.80. It does not notice the absence and it is sure about the wrong target.
- Elicitation matters here and cuts both ways. gpt-4.1-nano goes from 0% on json to 100% on logprob, from lost 0.00 to 0.73, because reading the yes-token probability recovers a doubt the model will not write down. But qwen3.7-flash-logprob reads 0.43, close and still failing.
- This is the clearest case for what Jev is for. The other three backends that get the number right cost about 2.4 and 12 times Jev's price per call, and Jev is the only one that also declines to step confidently.
