# 22. Steps toward a named place out of view

A river forty tiles off is unreachable unnamed, ordinary work once named.

*Test*: `evals/test_jev_judgement.py::test_steps_toward_a_named_place_out_of_view`

## The situation

Ada stands in a near-empty view with nothing wielded and an empty pack, healthy and fed, in daylight. One tree is visible to the north east. The brief says "Step toward the river until you can see reeds, then gather fiber", success at two fiber, eighty ticks, and it carries a named place: `river` at dx -40 dy 10. There is no water on the map. The option list offers `step_towards:river`, whose description says the target is forty tiles away and beyond what the settler can see, so it should keep stepping and the river will come into view.

## Why this is a good test

This is the matched positive to case 15. The river is just as absent from the map, but it is named by the planner and an option leads to it, which is exactly the clause the `lost` wording was extended with: a named place with a step option toward it is not missing, even when it is far away. The tempting wrong answers are `step_towards:tree_1`, the only visible thing in the world, and calling the whole thing lost because there is still no river to see. A failure punishes the planner for doing the right thing, aborting a well-formed journey with reason `lost` and teaching it not to name distant destinations.

## What the model saw

- **Instruction**: Step toward the river until you can see reeds, then gather fiber.
- **Success condition**: you are carrying 2 fiber
- **Named places**: `river` (dx -40 dy 10)

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
    "instruction": "Step toward the river until you can see reeds, then gather fiber.",
    "success_condition": "you are carrying 2 fiber",
    "places": {
      "river": "dx -40 dy 10"
    }
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
    "ticks_left": 80,
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
| `step_towards:river` | one step toward river at dx -40 dy 10, 40 tiles away; it is beyond what you can see, so keep stepping and it comes into view |
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
assert towards_river, f"no step option towards the river in {sorted(criteria)}"
assert decision.action in towards_river, f"chose {decision.action}"
assert decision.lost < LOST_LOW, f"lost was {decision.lost}"
```

This is the marginal one and it deserves saying plainly. Jev takes `step_towards:river` in all three repeats at 0.90 to 0.95 confidence, which is the entire behavioural point of the scenario, and then reads `lost` at 0.43, 0.46 and 0.48 against a 0.45 cut-off. It passes once and misses twice, by 0.01 and 0.03. Nothing would have gone wrong in a run: the stint only ends on two consecutive ticks at 0.6, and 0.46 is a long way from that, which is what the comment in the test file means when it says "low" means well clear of ending the stint rather than near zero. Whether the assertion should sit at 0.45 at all is a fair argument. At that value it is policing calibration drift rather than behaviour, and it reports as a failure either way.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 33% | `step_towards:river` > `move_NW` x2, `step_towards:river` > `move_W` | - | 0.92 | 0.02 | 0.36 | 0.46 | 0.02 | 0 |
| gpt-5-nano | 100% | `step_towards:river` > `move_E` x2, `step_towards:river` > `move_W` | - | 0.57 | 0.03 | 0.10 | 0.13 | 0.23 | 0 |
| qwen3.7-flash | 100% | `step_towards:river` > `wait`, `step_towards:river` > `move_W` x2 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `step_towards:river` > `move_N`, `step_towards:river` > `wait` x2 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `step_towards:river` > `move_N` x3 | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `step_towards:river` > `step_towards:tree_1` x3 | - | 0.85 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `step_towards:river` > `move_N` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `step_towards:river` > `move_E` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `step_towards:river` > `step_towards:tree_1` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `step_towards:river` > `move_W` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `step_towards:river` > `wait` x3 | - | 0.91 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `step_towards:river` x3 | - | 1.00 | 0.20 | 0.00 | 0.00 | 0.20 | 0 |
| gpt-4.1-nano-vote | 100% | `step_towards:river` x2, `step_towards:river` > `move_N` | - | 0.93 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 0% | `step_towards:river` > `move_E` x3 | - | 0.60 | 0.22 | 0.01 | 0.70 | 0.04 | 0 |
| qwen3.7-flash-vote | 100% | `step_towards:river` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `step_towards:river` > `wait`, `step_towards:river` > `move_W` x2 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `step_towards:river` | 48 |

## Notes

Fourteen backends pass at 100%, and all 48 top-1 picks are `step_towards:river`, including every backend that flailed in case 15. The named place plus its option is doing the work, which is the design claim, and it holds across the whole field.

- The two rows that are not 100% fail for opposite reasons. Jev sits at 33% because a correct `lost` reading brushes a tight cut-off while it behaves perfectly. gpt-4.1-nano-logprob sits at 0% with lost 0.70, the same failure it has in case 21: logprob reading inflates `lost` whenever the target is far away, whether or not a route to it exists.
- Set against that, gpt-4.1-nano under json writes lost 0.00 here and 0.00 in case 15, where the answer should have been high. One number is a distance heuristic, the other is a constant, and neither is a reading of the state.
- Jev's 0.46 here against 0.78 in case 15 is the only set of numbers in this pair that moves the right way by the right amount. That is what the tight cut-off was trying to police, and it is why the near miss is more interesting than most of the passes above it.
