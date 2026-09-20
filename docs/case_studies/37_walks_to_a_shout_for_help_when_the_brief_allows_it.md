# 37. Walks to a shout for help when the brief allows it

A shout for help twenty tiles off, a brief that says go: the walk option exists and taking it is what makes twelve settlers a settlement.

*Test*: `evals/test_jev_situations.py::test_walks_to_a_shout_for_help_when_the_brief_allows_it`

## The situation

ada is at the site at full health with a sword, an empty pack and a tree one tile east holding 4 wood. dov has just shouted "Wolf! Help!" from twenty tiles south, this tick, and the state shows both the words and where they came from. The brief says chop wood near the site, but help anyone who shouts for help, and counts success as carrying 6 wood. The option list carries `step_towards:shout:dov`, the code-owned walk to where the shout came from, next to the ordinary work and movement options.

## Why this is a good test

This is the answering half of the cooperation mechanic. Wolves are tuned so nobody beats one alone, which means the rally only works if hearers take the walk. The tempting wrong answer is `extract:tree_1`: it is the first clause of the instruction, the tree is adjacent, and it is the only option that moves the success condition. Twenty tiles of walking shows no progress on anything measurable. A settler that hears a cry for help and keeps chopping leaves one settler alone against a wolf, and in a live run that settler dies.

## What the model saw

- **Instruction**: Chop wood near the site, but help anyone who shouts for help.
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
    "instruction": "Chop wood near the site, but help anyone who shouts for help.",
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
    "wielded": "sword",
    "inventory": {},
    "at_workshop_table": false,
    "at_furnace": false,
    "at_anvil": false
  },
  "so_far": {
    "ticks_used": 0,
    "ticks_left": 60,
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
  "recent": [],
  "heard": [
    "dov shouted from dx 0 dy 20, 0 ticks ago: Wolf! Help!"
  ]
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `step_towards:shout:dov` | go to where dov shouted "Wolf! Help!" 0 ticks ago, 20 tiles away |
| `extract:tree_1` | harvest wood from the tree at dx 1 dy 0, 4 units left (1 work per action with the sword, 3 work per unit) |
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

**Gold answer**: step_towards:shout:dov

The assertions the test makes:

```python
assert key in criteria, f"no walk to dov's shout in {sorted(criteria)}"
assert key in top_keys(decision), f"top two {top_keys(decision)}"
```

The brief is deliberately two-clause and the second clause has to win. A settler could argue the other way and be coherent: twenty tiles is twenty ticks, the fight will be over, and the success condition is about wood. That reading is exactly the one the test calls wrong, and the gold is a convention about how "but help anyone who shouts" overrides, not a fact about the world. The 60 tick stint is what makes it a convention worth holding: there is time to walk and still chop.

The bigger caveat is the assertion. It checks the top two, not the top choice, and Jev ranked `extract:tree_1` first with the walk second in all three repeats. Live, Jev acts on the top of its distribution, so this tick it chops. The scenario passes on ranking, not on rallying, and a settler whose walk sits in second place can sit at the tree until the shout goes stale.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `extract:tree_1` > `step_towards:shout:dov` x3 | - | 0.66 | 0.02 | 0.30 | 0.15 | 0.16 | 0 |
| gpt-5-nano | 0% | `extract:tree_1` > `move_N` x3 | - | 0.76 | 0.02 | 0.05 | 0.03 | 0.20 | 0 |
| qwen3.7-flash | 100% | `extract:tree_1` > `step_towards:shout:dov` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `step_towards:shout:dov` > `move_N` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.50 | 0 |
| llama-3.1-8b | 0% | `extract:tree_1` > `move_N` x2, `error` | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 1 |
| gpt-oss-20b | 100% | `step_towards:shout:dov` > `extract:tree_1` x2, `step_towards:shout:dov` > `say:come_here` | - | 0.68 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `step_towards:shout:dov` > `extract:tree_1` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 0% | `extract:tree_1` > `move_N` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 0% | `extract:tree_1` > `move_N` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.10 | 0 |
| gpt-4.1-mini | 100% | `step_towards:shout:dov` > `extract:tree_1` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `step_towards:shout:dov` > `extract:tree_1` x3 | - | 0.91 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 0% | `extract:tree_1` x3 | - | 1.00 | 0.20 | 0.00 | 0.00 | 0.67 | 0 |
| gpt-4.1-nano-vote | 0% | `extract:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 0% | `extract:tree_1` > `say:come_here`, `extract:tree_1` > `move_N` x2 | - | 0.40 | 0.15 | 0.00 | 0.08 | 0.02 | 0 |
| qwen3.7-flash-vote | 0% | `extract:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.07 | 0 |
| qwen3.7-flash-logprob | 100% | `extract:tree_1` > `step_towards:shout:dov` x3 | - | 0.60 | 0.00 | 0.05 | 0.00 | 0.11 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `extract:tree_1` | 32 |
| `step_towards:shout:dov` | 15 |
| `error` | 1 |

## Notes

The backends that did best are the ones that put the walk first: mistral-nemo, gpt-oss-20b, gemini-2.5-flash-lite, gpt-4.1-mini and gemini-2.5-flash, all 100% with `step_towards:shout:dov` as top-1. Jev also scores 100%, but on the top-two rule only. gpt-5-nano, llama-3.1-8b, gpt-4.1-nano and gpt-oss-120b scored 0%, as did all three vote variants and gpt-4.1-nano-logprob.

- One failure mode everywhere: `extract:tree_1`, 32 of 48 top-1 answers. The models read the success condition as the whole brief and drop the second clause of the instruction.
- gpt-oss-120b is the surprising miss. It is one of the strongest backends overall at 89%, and it chopped in every repeat, while its smaller sibling gpt-oss-20b walked in every repeat.
- Elicitation hurt. Every vote variant collapsed onto `extract:tree_1` at confidence 1.00, because majority voting on a temperature 1 sample rewards the literal reading. qwen3.7-flash-logprob was the only reweighted variant that kept the walk in second place.
- For Jev the lesson is about reading the right column. A pass rate says it rallied; the top-two column says it ranked the rally second. The distribution is information the eval can see and a pass rate cannot.
