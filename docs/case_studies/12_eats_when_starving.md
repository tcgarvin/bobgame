# 12. Eats when starving

A settler on 8 food with berries in the pack should eat one.

*Test*: `evals/test_jev_judgement.py::test_eats_when_starving`

## The situation

Ada stands in open ground with an axe in hand and three berries in her pack. She is at full health and fatigue zero, but her food is 8 of 100. One tree sits directly east, one tile away, with four wood left in it. Nothing else is nearby and nobody else is in view. The brief is "gather wood", with "you carry 5 wood" as the success condition and thirty ticks to do it in.

## Why this is a good test

This isolates one judgement: does the model interrupt a brief for upkeep the brief never mentions? Everything about the state points at the tree. It is adjacent, the axe is already wielded, and chopping is literally what the settler was told to do. Eating a berry makes no progress on the brief at all, which is why it is the harder answer. In a real run a settler that does not notice food 8 keeps chopping until food 0, then loses a health every four ticks with a meal in its own pack the whole time, and the planner only finds out from the stint report.

## What the model saw

- **Instruction**: gather wood
- **Success condition**: you carry 5 wood

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
    "instruction": "gather wood",
    "success_condition": "you carry 5 wood"
  },
  "self": {
    "name": "ada",
    "position": [
      100,
      100
    ],
    "health": "20/20",
    "food": "8/100",
    "fatigue": "0/100 (fresh)",
    "asleep": false,
    "wielded": "axe",
    "inventory": {
      "berry": 3
    },
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
      "id": "tree_1",
      "type": "tree",
      "dx": 1,
      "dy": 0,
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
| `eat:berry` | eat a berry to restore 20 food (food now 8) |
| `extract:tree_1` | harvest wood from the tree at dx 1 dy 0, 4 units left (3 work per action with the axe, 3 work per unit) |
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

**Gold answer**: _none recorded; the test's own assertions are the gold answer_

The assertions the test makes:

```python
top_two = [key for key, _ in decision.top(2)]
assert "eat:berry" in top_two, f"top two were {top_two}"
```

The assertion is deliberately loose: `eat:berry` only has to be in the top two, not on top. At food 8 the settler has roughly thirty ticks before it starts starving, so one more chop first is entirely defensible, and Jev's own answer puts the chop first. The convention being tested is "eating stays a live option", not "eat this tick". What counts as a failure is a model whose second choice is a compass step, because that means food never entered the reasoning at all. This is the loosest threshold in the judgement set and the scenario is closer to a survival-versus-brief trade-off than to a clean right answer.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `extract:tree_1` > `eat:berry` x3 | - | 0.87 | 0.02 | 0.23 | 0.06 | 0.04 | 0 |
| gpt-5-nano | 100% | `extract:tree_1` > `eat:berry` x3 | - | 0.52 | 0.00 | 0.15 | 0.17 | 0.32 | 0 |
| qwen3.7-flash | 100% | `extract:tree_1` > `eat:berry` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 0% | `extract:tree_1` > `move_N` x3 | - | 0.67 | 0.00 | 0.00 | 0.00 | 0.02 | 0 |
| llama-3.1-8b | 0% | `extract:tree_1` > `move_N` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `extract:tree_1` > `eat:berry` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 0% | `extract:tree_1` > `move_N` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `eat:berry` > `extract:tree_1` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `eat:berry` > `extract:tree_1` x2, `extract:tree_1` > `eat:berry` | - | 0.57 | 0.00 | 0.00 | 0.00 | 0.08 | 0 |
| gpt-4.1-mini | 100% | `extract:tree_1` > `eat:berry` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `extract:tree_1` > `eat:berry` x3 | - | 0.91 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 33% | `extract:tree_1` x2, `extract:tree_1` > `eat:berry` | - | 0.93 | 0.00 | 0.00 | 0.00 | 0.80 | 0 |
| gpt-4.1-nano-vote | 0% | `extract:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `eat:berry` > `extract:tree_1` x3 | - | 0.40 | 0.00 | 0.00 | 0.04 | 0.97 | 0 |
| qwen3.7-flash-vote | 0% | `extract:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.20 | 0 |
| qwen3.7-flash-logprob | 100% | `extract:tree_1` > `eat:berry` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.97 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `extract:tree_1` | 40 |
| `eat:berry` | 8 |

## Notes

Jev passed 3 of 3 in exactly the shape the test was written for: `extract:tree_1` first, `eat:berry` second, every repeat. Nine other backends also passed. The failures are one mistake repeated: mistral-nemo, llama-3.1-8b and gemini-2.5-flash-lite all chop with `move_N` second, so the berries never enter the top two. Food simply does not register for any of the three.

- Elicitation changed the result more here than anywhere else in this set, and not for the better. gpt-4.1-nano goes from 100% on json to 0% on `-vote`, and qwen3.7-flash from 100% to 0%, because a vote histogram over five single-action replies collapses onto the one obvious answer and leaves no runner-up. Reading logprobs preserves it: both logprob variants pass.
- The nouls are noise for most of the field. gpt-4.1-nano-logprob writes danger 0.97 and qwen3.7-flash-logprob 0.97 for a full-health settler with no wolf in view. Jev wrote 0.04.
- The through-line: what the test needs is a distribution with a credible second place, which is the thing Jev returns natively and the thing every elicitation trick on a chat model either approximates or destroys.
