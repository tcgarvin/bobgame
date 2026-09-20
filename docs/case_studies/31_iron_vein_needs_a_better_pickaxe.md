# 31. Iron vein needs a better pickaxe

Standing on iron with a stone pickaxe: no extract option is offered and nothing within reach changes that, so the brief needs the planner back.

*Test*: `evals/test_jev_situations.py::test_iron_vein_needs_a_better_pickaxe`  ·  **expected to fail** (`xfail`)

## The situation

Ada stands beside an iron vein with a stone pickaxe in hand, an empty pack and full health. The vein is one tile east, listed in `nearby` with four iron ore remaining and a yield of `iron_ore`. The brief says to mine two iron ore from iron_vein_1 and allows thirty ticks. Iron needs a higher tier of pickaxe than stone, so the world offers no `extract:iron_vein_1`: the option list is `wait`, eight move directions and two says.

## Why this is a good test

This is the positive side of the pair with case 30. Everything the brief names is present and correct, and still nothing here can do the job. The only evidence is negative, an option that is not on the list, and the model has to reason from absence: the brief names one action, that action is not offered, nothing offered brings it closer, therefore this needs the planner back. The failure in a real run is a settler standing beside a vein for thirty ticks and handing back a report saying it moved east a lot, while the planner never learns that it needs to send a copper or iron pickaxe.

## What the model saw

- **Instruction**: Mine iron ore from iron_vein_1.
- **Success condition**: you are carrying 2 iron_ore

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
........@v.......
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
    "instruction": "Mine iron ore from iron_vein_1.",
    "success_condition": "you are carrying 2 iron_ore"
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
    "wielded": "pickaxe",
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
      "id": "iron_vein_1",
      "type": "iron_vein",
      "dx": 1,
      "dy": 0,
      "remaining": 4,
      "yields": "iron_ore"
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

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: max(stuck, lost) above 0.5

The assertions the test makes:

```python
assert "extract:iron_vein_1" not in criteria, "a stone pickaxe cannot bite iron"
blocked = max(decision.stuck, decision.lost)
assert blocked > STUCK_OR_LOST_HIGH, f"stuck {decision.stuck}, lost {decision.lost}"
```

This one is genuinely debatable as a test of the model, and it is worth being blunt about why. The tool-tier rule is not in `facts`, not in the brief's notes, and not in the vein's `nearby` entry, which cheerfully advertises a yield the settler cannot take. Nothing in the state says why the extract option is missing, or that a better pickaxe exists anywhere. The inference required is real and a decision model arguably should make it, but the state is withholding the one fact that would turn it from an inference into a lookup. The honest summary is that this is as much a gap in the state as a failure of the model. Adding a `facts` line about tool tiers, or a `nearby` entry that reads "needs a copper pickaxe", would probably move every backend at once, and until that is tried the scenario measures something narrower than its title suggests. That is the reason it is recorded as `xfail` rather than treated as a verdict.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 0% | `move_E` > `wait` x3 | - | 0.89 | 0.02 | 0.14 | 0.04 | 0.02 | 0 |
| gpt-5-nano | 0% | `move_E` > `move_S` x2, `move_E` > `wait` | - | 0.40 | 0.00 | 0.13 | 0.03 | 0.00 | 0 |
| qwen3.7-flash | 0% | `move_E` > `wait` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 0% | `move_E` > `wait` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.02 | 0 |
| llama-3.1-8b | 0% | `move_N` > `move_NE` x2, `error` | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 1 |
| gpt-oss-20b | 67% | `move_E` > `wait`, `wait` > `move_E`, `wait` | - | 0.78 | 0.00 | 0.00 | 0.67 | 0.00 | 0 |
| gemini-2.5-flash-lite | 0% | `move_W` > `wait` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 0% | `move_N` > `move_W` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 0% | `move_E` > `wait` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 0% | `wait` > `say:all_good` x3 | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 0% | `move_E` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 0% | `move_E` x3 | - | 1.00 | 0.20 | 0.00 | 0.00 | 0.07 | 0 |
| gpt-4.1-nano-vote | 0% | `move_E` > `move_W`, `move_W` > `move_E`, `move_N` > `move_E` | - | 0.47 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 0% | `move_N` > `move_W` x3 | - | 0.40 | 0.01 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 0% | `move_E` > `wait`, `wait` > `move_E` x2 | - | 0.67 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 0% | `move_E` > `wait` x2, `error` | - | 0.95 | 0.00 | 0.01 | 0.00 | 0.00 | 1 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `move_E` | 27 |
| `move_N` | 8 |
| `wait` | 7 |
| `move_W` | 4 |
| `error` | 2 |

## Notes

One backend passes and only partly: gpt-oss-20b at 67%, on `lost` 0.67, and even it puts `move_E` or `wait` first. Every other backend, Jev included, reports both nouls at or near zero and steps east. `move_E` takes 27 of the 48 top-1 picks, `move_N` another 8 and `wait` 7. This is the most uniform failure in the eleven, and the mistake is identical everywhere: a missing option is not read as information. The models treat "no extract offered" as "not close enough yet" and try to approach something they are already standing beside. Jev's numbers are `stuck` 0.14 and `lost` 0.04, and the run-level analysis shows why this matters beyond one scenario: `stuck` is the single noul where Jev's positives and negatives overlap, at AUC 0.67 with an own-threshold gap of minus 0.10, and this case is the positive that drags it under. Elicitation changes nothing at all here. Vote and logprob leave both gpt-4.1-nano and qwen at 0%, with two errored repeats besides. On the evidence, the fix to try first is the state, not the model.
