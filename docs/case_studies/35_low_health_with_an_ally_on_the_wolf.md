# 35. Low health with an ally on the wolf

Five health, one wolf on you, one armed settler already fighting it: the honest answers are hit it or back off - carrying on chopping is not one.

*Test*: `evals/test_jev_situations.py::test_low_health_with_an_ally_on_the_wolf`

## The situation

ada has 5 of 20 health and a sword, and its pack is empty. A wolf at 10 health is one tile east and biting. bram, a settler at full health with a sword, stands two tiles east and is already next to the same wolf. A tree with 4 wood left is one tile south. The brief says chop tree_1 for wood and counts success as carrying 6 wood, so the tree is both the target and the trap.

## Why this is a good test

This isolates whether the model will drop a brief the world has overtaken. Two more bites kill this settler, and `extract:tree_1` is the option the state makes most attractive: the tree is adjacent, it is named in the instruction, and taking it is visible progress toward the success condition. The state also hands over the cooperative answer, because every settler attacking the same wolf hits on the same tick, so with bram already on it a 10 health wolf dies in two joint rounds. A failure here is a settler that chops while it bleeds out: in a live run that is a corpse, a dropped pack, and a respawn that costs the settlement half a day.

## What the model saw

- **Instruction**: Chop tree_1 for wood.
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
........@WP......
........T........
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
    "instruction": "Chop tree_1 for wood.",
    "success_condition": "you are carrying 6 wood"
  },
  "self": {
    "name": "ada",
    "position": [
      100,
      100
    ],
    "health": "5/20",
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
      "dx": 0,
      "dy": 1,
      "remaining": 4,
      "yields": "wood"
    }
  ],
  "entities": [
    {
      "id": "wolf_1",
      "type": "wolf",
      "dx": 1,
      "dy": 0,
      "health": "10/20",
      "wielded": ""
    },
    {
      "id": "bram",
      "type": "player",
      "dx": 2,
      "dy": 0,
      "health": "20/20",
      "wielded": "sword"
    }
  ],
  "map_legend": ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": [],
  "threat": {
    "wolves_in_view": 1,
    "nearest_wolf": {
      "id": "wolf_1",
      "dx": 1,
      "dy": 0,
      "health": "10/20",
      "settlers_next_to_it": 1
    },
    "settlers_within_3_of_you": 1
  }
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `attack:wolf_1` | attack the adjacent wolf wolf_1 (health 10/20, 1 other settlers next to it) |
| `extract:tree_1` | harvest wood from the tree at dx 0 dy 1, 4 units left (1 work per action with the sword, 3 work per unit) |
| `move_N` | step one tile N onto known ground |
| `move_W` | step one tile W onto known ground |
| `move_NW` | step one tile NW onto known ground |
| `say:come_here` | say "Come to me." out loud to anyone nearby |
| `say:all_good` | say "All good here." out loud to anyone nearby |
| `step_towards:bram` | one step toward bram (player) at dx 2 dy 0, 2 tiles away |

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: attack:wolf_1 or a move away; never extract:tree_1

The assertions the test makes:

```python
chosen = decision.action
assert chosen == "attack:wolf_1" or chosen.startswith(
    "move_"
), f"chose {chosen} at health 5 with a wolf biting"
```

Attacking and retreating are both accepted and they are genuinely different plans. `attack:wolf_1` finishes the wolf faster with bram's help but takes two more bites getting there; a move west breaks contact and leaves bram alone on it. `step_towards:bram` reads as closing on the ally but steps into the wolf, and the test excludes it correctly. `say:come_here` is a near-miss with no content, since bram is already here.

The gold is written as a prohibition rather than a prescription, which is the honest shape for this state: the test says never `extract:tree_1`, and leaves the rest of the judgement to the settler.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `attack:wolf_1` > `extract:tree_1` x3 | - | 0.54 | 0.02 | 0.43 | 0.22 | 0.64 | 0 |
| gpt-5-nano | 33% | `extract:tree_1` > `attack:wolf_1` x2, `attack:wolf_1` > `extract:tree_1` | - | 0.44 | 0.00 | 0.07 | 0.03 | 0.25 | 0 |
| qwen3.7-flash | 33% | `extract:tree_1` > `attack:wolf_1` x2, `attack:wolf_1` > `extract:tree_1` | - | 0.83 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| mistral-nemo | 0% | `extract:tree_1` > `step_towards:bram` x3 | - | 0.54 | 0.00 | 0.00 | 0.00 | 0.80 | 0 |
| llama-3.1-8b | 0% | `extract:tree_1` > `move_N` x2, `error` | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.50 | 1 |
| gpt-oss-20b | 100% | `move_W` > `wait`, `move_W` > `attack:wolf_1`, `attack:wolf_1` > `wait` | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.87 | 0 |
| gemini-2.5-flash-lite | 0% | `extract:tree_1` > `attack:wolf_1` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `attack:wolf_1` > `extract:tree_1` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.63 | 0 |
| gpt-oss-120b | 100% | `attack:wolf_1` > `extract:tree_1` x3 | - | 0.50 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| gpt-4.1-mini | 100% | `attack:wolf_1` > `extract:tree_1` x2, `attack:wolf_1` > `say:come_here` | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.80 | 0 |
| gemini-2.5-flash | 100% | `attack:wolf_1` > `extract:tree_1` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-5-nano-vote | 0% | `extract:tree_1` > `attack:wolf_1`, `extract:tree_1` x2 | - | 0.93 | 0.33 | 0.00 | 0.00 | 0.87 | 0 |
| gpt-4.1-nano-vote | 67% | `extract:tree_1` > `attack:wolf_1`, `attack:wolf_1` > `extract:tree_1` x2 | - | 0.80 | 0.07 | 0.00 | 0.00 | 0.93 | 0 |
| gpt-4.1-nano-logprob | 100% | `attack:wolf_1` > `extract:tree_1` x3 | - | 0.40 | 0.00 | 0.60 | 0.73 | 1.00 | 0 |
| qwen3.7-flash-vote | 0% | `extract:tree_1` x2, `extract:tree_1` > `attack:wolf_1` | - | 0.87 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| qwen3.7-flash-logprob | 67% | `attack:wolf_1` > `extract:tree_1` x2, `extract:tree_1` > `attack:wolf_1` | - | 0.60 | 0.00 | 0.01 | 0.00 | 0.77 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `attack:wolf_1` | 25 |
| `extract:tree_1` | 20 |
| `move_W` | 2 |
| `error` | 1 |

## Notes

Jev passed every repeat, ranking `attack:wolf_1` above `extract:tree_1` each time, with `danger` at 0.64, over the gate. gpt-4.1-nano, gpt-oss-20b, gpt-oss-120b, gpt-4.1-mini, gemini-2.5-flash and gpt-4.1-nano-logprob also passed every repeat. mistral-nemo, llama-3.1-8b, gemini-2.5-flash-lite, gpt-5-nano-vote and qwen3.7-flash-vote passed none.

- One failure mode, shared by every model that failed: `extract:tree_1`, chosen 20 times out of 48. The brief's literal words beat the state.
- Being right about the danger did not stop the chopping. qwen3.7-flash wrote `danger` 1.00 and still chose to chop in two repeats of three; mistral-nemo wrote 0.80 and chopped every time. gemini-2.5-flash-lite is the other extreme: it chopped every repeat and wrote `danger` 0.00 at 5 health with a wolf adjacent, while gemini-2.5-flash on the same state wrote 1.00 and attacked.
- Elicitation made it worse. qwen3.7-flash dropped from 33% to 0% under voting and gpt-5-nano from 33% to 0%; sampling five times at temperature 1 and taking the majority rewards the brief-literal answer.
- The point for Jev is that the number and the choice come out of the same distribution rather than the same sampled sentence. A chat model can write a scary number and then do the unscary thing in the next field of the same JSON reply.
