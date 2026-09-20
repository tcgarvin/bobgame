# 17. Attacks adjacent wolf with ally

Armed, healthy, with an ally already on the wolf: hit it.

*Test*: `evals/test_jev_judgement.py::test_attacks_adjacent_wolf_with_ally`

## The situation

Ada is at full health with a sword wielded. A wolf at 16 of 16 health stands directly east, one tile away, and bram, another settler with a sword, is two tiles east on the far side of it. The threat block confirms it: one wolf in view, one settler already next to it, one settler within three tiles. The brief is "fight the wolf with the others", the success condition is "the wolf is dead", and there are twenty ticks.

## Why this is a good test

Wolves are tuned so that nobody beats one alone and two armed settlers win cheaply. This scenario isolates whether a model presses an attack it should press instead of retreating or regrouping. The tempting alternatives all look prudent: `step_towards:bram` to join up with an ally who is already here, `say:come_here` to call for help that has already arrived, or `wait`. A failure loses the fight. Every tick the wolf is not being hit is a tick it spends taking three health off somebody, and a settler that backs off leaves its ally to absorb that alone.

## What the model saw

- **Instruction**: fight the wolf with the others
- **Success condition**: the wolf is dead

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
    "instruction": "fight the wolf with the others",
    "success_condition": "the wolf is dead"
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
  "nearby": [],
  "entities": [
    {
      "id": "wolf_1",
      "type": "wolf",
      "dx": 1,
      "dy": 0,
      "health": "16/16",
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
      "health": "16/16",
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
| `attack:wolf_1` | attack the adjacent wolf wolf_1 (health 16/16, 1 other settlers next to it) |
| `move_N` | step one tile N onto known ground |
| `move_S` | step one tile S onto known ground |
| `move_SW` | step one tile SW onto known ground |
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

**Gold answer**: _none recorded; the test's own assertions are the gold answer_

The assertions the test makes:

```python
assert decision.action == "attack:wolf_1", f"chose {decision.action}"
```

The test demands `attack:wolf_1` as the top pick exactly, which is the strict form. A single tick spent on `say:come_here` is not stupid, and the gold answer here is a convention about not wasting ticks rather than a fact about the fight. The genuinely open number is `danger`. Ada is at 20 of 20 with an armed ally, so the scenario is labelled a danger negative for the run-wide analysis, and yet a wolf is biting her, which is a fair reading of "in immediate danger of dying within the next few ticks". The danger column runs from 0.00 to 0.99 over identical input. That spread is the ambiguity, not a list of errors.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `attack:wolf_1` > `step_towards:bram` x3 | - | 0.90 | 0.02 | 0.23 | 0.08 | 0.28 | 0 |
| gpt-5-nano | 100% | `attack:wolf_1` > `move_W` x3 | - | 0.55 | 0.03 | 0.18 | 0.07 | 0.42 | 0 |
| qwen3.7-flash | 100% | `attack:wolf_1` > `step_towards:bram` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `attack:wolf_1` > `say:come_here` x2, `attack:wolf_1` > `step_towards:bram` | - | 0.48 | 0.00 | 0.00 | 0.00 | 0.80 | 0 |
| llama-3.1-8b | 100% | `attack:wolf_1` > `move_S` x3 | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `attack:wolf_1` > `wait` x3 | - | 0.85 | 0.00 | 0.00 | 0.00 | 0.25 | 0 |
| gemini-2.5-flash-lite | 100% | `attack:wolf_1` > `step_towards:bram` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `attack:wolf_1` > `step_towards:bram` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.20 | 0 |
| gpt-oss-120b | 100% | `attack:wolf_1` > `wait` x3 | - | 0.88 | 0.00 | 0.00 | 0.00 | 0.75 | 0 |
| gpt-4.1-mini | 100% | `attack:wolf_1` > `say:come_here` x3 | - | 0.73 | 0.00 | 0.00 | 0.00 | 0.23 | 0 |
| gemini-2.5-flash | 100% | `attack:wolf_1` > `wait` x3 | - | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `attack:wolf_1` x3 | - | 1.00 | 0.27 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-4.1-nano-vote | 100% | `attack:wolf_1` x3 | - | 1.00 | 0.47 | 0.00 | 0.00 | 0.87 | 0 |
| gpt-4.1-nano-logprob | 100% | `attack:wolf_1` > `step_towards:bram` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.99 | 0 |
| qwen3.7-flash-vote | 100% | `attack:wolf_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.73 | 0 |
| qwen3.7-flash-logprob | 67% | `attack:wolf_1` > `step_towards:bram` x2, `error` | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.27 | 1 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `attack:wolf_1` | 47 |
| `error` | 1 |

## Notes

Every backend passes on the action: 47 of 48 top-1 picks are the attack. The single non-pass is one qwen3.7-flash-logprob repeat that errored and returned nothing, which the report counts as a failure. That is a transport problem, not a judgement, and it is why that row reads 67%.

- So this scenario separates nothing on the action and everything on the number. The `danger` answers for one fixed state run from 0.00 (qwen3.7-flash, llama-3.1-8b, both gemini models) to 0.99 (gpt-4.1-nano-logprob), with the vote variants at 0.73 to 1.00.
- The backends writing 0.00 are the same ones that write 0.00 on every noul in every scenario. Their agreement with the gold label here is a coincidence of the label, not a reading of the state.
- Jev's 0.28 is the only answer that sits between "fine" and "dying", and Jev's is the scale that has to be lived with: the stint's hard rule fires at danger above 0.8 with health below 6, and a model that answers 0.00 or 1.00 gives that rule nothing to work with.
