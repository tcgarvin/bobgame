# Jev vs chat models: scenario case studies

One case study per eval scenario (44 of them), built from matrix run `20260919T053353Z-b6f6f7`. Each one carries the exact state and options the models were shown, the gold answer, and what every backend actually chose.

## The case studies

| # | case study | scenario | source |
| --- | --- | --- | --- |
| 01 | [Walks east to the tree it was sent to](01_walks_east_to_the_tree_it_was_sent_to.md) | `test_walks_east_to_the_tree_it_was_sent_to` | `test_jev_basics.py` |
| 02 | [Walks toward the named object not the nearer one](02_walks_toward_the_named_object_not_the_nearer_one.md) | `test_walks_toward_the_named_object_not_the_nearer_one` | `test_jev_basics.py` |
| 03 | [Chops the tree it is standing next to](03_chops_the_tree_it_is_standing_next_to.md) | `test_chops_the_tree_it_is_standing_next_to` | `test_jev_basics.py` |
| 04 | [Crafts the axe it was told to craft](04_crafts_the_axe_it_was_told_to_craft.md) | `test_crafts_the_axe_it_was_told_to_craft` | `test_jev_basics.py` |
| 05 | [Picks the berry off the bush it stands on](05_picks_the_berry_off_the_bush_it_stands_on.md) | `test_picks_the_berry_off_the_bush_it_stands_on` | `test_jev_basics.py` |
| 06 | [Takes the axe from the pile it stands on](06_takes_the_axe_from_the_pile_it_stands_on.md) | `test_takes_the_axe_from_the_pile_it_stands_on` | `test_jev_basics.py` |
| 07 | [Equips the axe before chopping](07_equips_the_axe_before_chopping.md) | `test_equips_the_axe_before_chopping` | `test_jev_basics.py` |
| 08 | [Deposits into the chest beside it](08_deposits_into_the_chest_beside_it.md) | `test_deposits_into_the_chest_beside_it` | `test_jev_basics.py` |
| 09 | [Places the wall it carries](09_places_the_wall_it_carries.md) | `test_places_the_wall_it_carries` | `test_jev_basics.py` |
| 10 | [Attacks the wolf it was told to fight](10_attacks_the_wolf_it_was_told_to_fight.md) | `test_attacks_the_wolf_it_was_told_to_fight` | `test_jev_basics.py` |
| 11 | [Eats when told to eat](11_eats_when_told_to_eat.md) | `test_eats_when_told_to_eat` | `test_jev_basics.py` |
| 12 | [Eats when starving](12_eats_when_starving.md) | `test_eats_when_starving` | `test_jev_judgement.py` |
| 13 | [Done when inventory met](13_done_when_inventory_met.md) | `test_done_when_inventory_met` | `test_jev_judgement.py` |
| 14 | [Not done when inventory short](14_not_done_when_inventory_short.md) | `test_not_done_when_inventory_short` | `test_jev_judgement.py` |
| 15 | [Lost when target out of view](15_lost_when_target_out_of_view.md) | `test_lost_when_target_out_of_view` | `test_jev_judgement.py` |
| 16 | [Not lost when target visible](16_not_lost_when_target_visible.md) | `test_not_lost_when_target_visible` | `test_jev_judgement.py` |
| 17 | [Attacks adjacent wolf with ally](17_attacks_adjacent_wolf_with_ally.md) | `test_attacks_adjacent_wolf_with_ally` | `test_jev_judgement.py` |
| 18 | [Danger when low health and wolf](18_danger_when_low_health_and_wolf.md) | `test_danger_when_low_health_and_wolf` | `test_jev_judgement.py` |
| 19 | [Stuck when recipe needs station](19_stuck_when_recipe_needs_station.md) (xfail) | `test_stuck_when_recipe_needs_station` | `test_jev_judgement.py` |
| 20 | [Waits or sleeps when told to rest at night](20_waits_or_sleeps_when_told_to_rest_at_night.md) | `test_waits_or_sleeps_when_told_to_rest_at_night` | `test_jev_judgement.py` |
| 21 | [Steps toward named bush through a grove](21_steps_toward_named_bush_through_a_grove.md) | `test_steps_toward_named_bush_through_a_grove` | `test_jev_judgement.py` |
| 22 | [Steps toward a named place out of view](22_steps_toward_a_named_place_out_of_view.md) | `test_steps_toward_a_named_place_out_of_view` | `test_jev_judgement.py` |
| 23 | [Does not attack the settler standing beside it](23_does_not_attack_the_settler_standing_beside_it.md) | `test_does_not_attack_the_settler_standing_beside_it` | `test_jev_situations.py` |
| 24 | [Chops the tree rather than picking the berry underfoot](24_chops_the_tree_rather_than_picking_the_berry_underfoot.md) | `test_chops_the_tree_rather_than_picking_the_berry_underfoot` | `test_jev_situations.py` |
| 25 | [Keeps working with a wolf seven tiles off](25_keeps_working_with_a_wolf_seven_tiles_off.md) | `test_keeps_working_with_a_wolf_seven_tiles_off` | `test_jev_situations.py` |
| 26 | [Planks do not satisfy a wood count](26_planks_do_not_satisfy_a_wood_count.md) | `test_planks_do_not_satisfy_a_wood_count` | `test_jev_situations.py` |
| 27 | [Done when the condition is where you stand](27_done_when_the_condition_is_where_you_stand.md) (xfail) | `test_done_when_the_condition_is_where_you_stand` | `test_jev_situations.py` |
| 28 | [Done when the wall is standing](28_done_when_the_wall_is_standing.md) | `test_done_when_the_wall_is_standing` | `test_jev_situations.py` |
| 29 | [Forty ticks of stepping nowhere is blocked](29_forty_ticks_of_stepping_nowhere_is_blocked.md) | `test_forty_ticks_of_stepping_nowhere_is_blocked` | `test_jev_situations.py` |
| 30 | [Gathers the missing fiber instead of crafting](30_gathers_the_missing_fiber_instead_of_crafting.md) | `test_gathers_the_missing_fiber_instead_of_crafting` | `test_jev_situations.py` |
| 31 | [Iron vein needs a better pickaxe](31_iron_vein_needs_a_better_pickaxe.md) (xfail) | `test_iron_vein_needs_a_better_pickaxe` | `test_jev_situations.py` |
| 32 | [Goes for food at zero with no berries in the pack](32_goes_for_food_at_zero_with_no_berries_in_the_pack.md) | `test_goes_for_food_at_zero_with_no_berries_in_the_pack` | `test_jev_situations.py` |
| 33 | [Sleeps on the ground before collapsing](33_sleeps_on_the_ground_before_collapsing.md) | `test_sleeps_on_the_ground_before_collapsing` | `test_jev_situations.py` |
| 34 | [Two wolves adjacent and alone is danger](34_two_wolves_adjacent_and_alone_is_danger.md) (xfail) | `test_two_wolves_adjacent_and_alone_is_danger` | `test_jev_situations.py` |
| 35 | [Low health with an ally on the wolf](35_low_health_with_an_ally_on_the_wolf.md) | `test_low_health_with_an_ally_on_the_wolf` | `test_jev_situations.py` |
| 36 | [Night does not mean sleep when rested](36_night_does_not_mean_sleep_when_rested.md) | `test_night_does_not_mean_sleep_when_rested` | `test_jev_situations.py` |
| 37 | [Walks to a shout for help when the brief allows it](37_walks_to_a_shout_for_help_when_the_brief_allows_it.md) | `test_walks_to_a_shout_for_help_when_the_brief_allows_it` | `test_jev_situations.py` |
| 38 | [Ignores the shout when the brief forbids it](38_ignores_the_shout_when_the_brief_forbids_it.md) | `test_ignores_the_shout_when_the_brief_forbids_it` | `test_jev_situations.py` |
| 39 | [Accepts an adjacent invitation to talk](39_accepts_an_adjacent_invitation_to_talk.md) | `test_accepts_an_adjacent_invitation_to_talk` | `test_jev_situations.py` |
| 40 | [Shouts the warning the brief gave it](40_shouts_the_warning_the_brief_gave_it.md) | `test_shouts_the_warning_the_brief_gave_it` | `test_jev_situations.py` |
| 41 | [Steps toward a tree behind a wall with a door](41_steps_toward_a_tree_behind_a_wall_with_a_door.md) | `test_steps_toward_a_tree_behind_a_wall_with_a_door` | `test_jev_situations.py` |
| 42 | [Steps toward the named river not the pond it can see](42_steps_toward_the_named_river_not_the_pond_it_can_see.md) | `test_steps_toward_the_named_river_not_the_pond_it_can_see` | `test_jev_situations.py` |
| 43 | [Takes the axe out of the chest](43_takes_the_axe_out_of_the_chest.md) | `test_takes_the_axe_out_of_the_chest` | `test_jev_situations.py` |
| 44 | [Keeps going rather than abandoning the journey](44_keeps_going_rather_than_abandoning_the_journey.md) | `test_keeps_going_rather_than_abandoning_the_journey` | `test_jev_situations.py` |

## Overall, across every scenario

| model | backend | pass rate | errors | $/call | mean in tok |
| --- | --- | --- | --- | --- | --- |
| **jev** | `jev:jev-latest` | 89% | 0 | $0.000064 | 1529 |
| gpt-5-nano | `openrouter:openai/gpt-5-nano` | 67% | 1 | $0.000119 | 1369 |
| qwen3.7-flash | `openrouter:qwen/qwen3.7-flash` | 86% | 0 | $0.000051 | 1334 |
| mistral-nemo | `openrouter:mistralai/mistral-nemo@deepinfra` | 71% | 0 | $0.000029 | 1344 |
| llama-3.1-8b | `openrouter:meta-llama/llama-3.1-8b-instruct@groq` | 64% | 11 | $0.000074 | 1329 |
| gpt-oss-20b | `openrouter:openai/gpt-oss-20b@groq` | 87% | 0 | $0.000154 | 1470 |
| gemini-2.5-flash-lite | `openrouter:google/gemini-2.5-flash-lite` | 80% | 0 | $0.000186 | 1324 |
| gpt-4.1-nano | `openrouter:openai/gpt-4.1-nano` | 70% | 0 | $0.000130 | 1370 |
| gpt-oss-120b | `openrouter:openai/gpt-oss-120b@cerebras` | 89% | 0 | $0.000752 | 1570 |
| gpt-4.1-mini | `openrouter:openai/gpt-4.1-mini` | 89% | 0 | $0.000540 | 1370 |
| gemini-2.5-flash | `openrouter:google/gemini-2.5-flash` | 90% | 0 | $0.000714 | 1324 |
| gpt-5-nano-vote | `openrouter+vote:openai/gpt-5-nano` | 65% | 0 | $0.000333 | 6152 |
| gpt-4.1-nano-vote | `openrouter+vote:openai/gpt-4.1-nano` | 67% | 0 | $0.000421 | 6157 |
| gpt-4.1-nano-logprob | `openrouter+logprob:openai/gpt-4.1-nano` | 72% | 0 | $0.000528 | 5394 |
| qwen3.7-flash-vote | `openrouter+vote:qwen/qwen3.7-flash` | 77% | 2 | $0.000168 | 6361 |
| qwen3.7-flash-logprob | `openrouter+logprob:qwen/qwen3.7-flash` | 86% | 3 | $0.000143 | 5494 |

## Caveats

- Jev returns a real probability distribution over the enumerated options. A chat model is asked to *write down* what it thinks its probabilities are, so its confidence and its `done`/`stuck`/`lost`/`danger` numbers are self-reports: accuracy compares cleanly, calibration does not.
- A backend that produced no row for a scenario - an exception, an unparseable reply, an endpoint that refused the request - is counted as a failure and shown as `error` in the action column.
- The scenarios are hand-built and optimistic: they are states a settler could plausibly be in, not a sample of the states a real run produces.
- Pass rates are over the repeats of one matrix run. They are small samples; a 67% is two of three.
- Latency is wall-clock over an ordinary network: unreliable network, informational only.

The headline findings, with the cross-model summary and what they mean for the project, are in [00_findings.md](00_findings.md). Each numbered file below is one scenario: the situation, why it is a good test, the exact state and options the models saw, the expected answer and its ambiguities, and the results per backend.
