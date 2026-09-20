"""Food, health, respawn, fatigue and sleep numbers.

Contract: docs/10_metal_and_sleep.md, sections 3 and 4.
"""

from typing import Mapping

# --- Food and health -------------------------------------------------------

# Food drops one point every FOOD_INTERVAL_TICKS ticks. At a 2 s tick a
# full stomach (100) lasts ~13 minutes before starvation damage begins.
FOOD_PER_TICK = 1
FOOD_INTERVAL_TICKS = 4
STARVATION_INTERVAL_TICKS = 4
STARVATION_DAMAGE = 1
REGEN_INTERVAL_TICKS = 5
REGEN_AMOUNT = 1
# Health only regrows above this food (and only while not tired).
REGEN_FOOD_THRESHOLD = 50

# --- Dying and coming back -------------------------------------------------

RESPAWN_DELAY_TICKS = 10
RESPAWN_FOOD = 50
# A respawned settler comes back part-rested.
RESPAWN_FATIGUE = 30
# Respawning settlers keep this far from every living wolf when they can. It is
# wider than the wolves' chase radius (8), so a wolf camping the settlement
# does not notice them arrive.
RESPAWN_SAFE_DISTANCE = 10
RESPAWN_RING_DISTANCES = (12, 24)

# --- Fatigue ---------------------------------------------------------------

# One point of fatigue every N ticks awake. Nights are tiring.
FATIGUE_PER_STEP = 1
FATIGUE_INTERVAL_DAY = 4
FATIGUE_INTERVAL_NIGHT = 3

# At or above TIRED_FATIGUE a settler works slower and stops regenerating.
TIRED_FATIGUE = 60
# A collapsed sleeper wakes once fatigue has fallen back to this.
COLLAPSE_WAKE_FATIGUE = 70

# --- Sleep -----------------------------------------------------------------

# Fatigue recovered per sleeping tick, as (points, ticks): "`points` fatigue
# every `ticks` ticks". A bed is strictly faster than the ground in both
# periods, and every period is fast enough that a settler need not lie down
# for most of the daylight: before the 2026-09-20 retunes the ground was 1 per
# 2 at night and 1 per 4 by day, and 31-45% of all settler-ticks were asleep.
BED_NIGHT_RECOVERY = (1, 1)
BED_DAY_RECOVERY = (2, 3)
GROUND_NIGHT_RECOVERY = (2, 3)
GROUND_DAY_RECOVERY = (1, 2)

# (on a bed, at night) -> (fatigue points recovered, every this many ticks).
SLEEP_RECOVERY: Mapping[tuple[bool, bool], tuple[int, int]] = {
    (True, True): BED_NIGHT_RECOVERY,
    (True, False): BED_DAY_RECOVERY,
    (False, True): GROUND_NIGHT_RECOVERY,
    (False, False): GROUND_DAY_RECOVERY,
}

# `Entity.sleeping_on` when the sleeper lies on the bare ground.
GROUND = ""

# One rule, read both ways: a settler will not lie down at or below this much
# food, and a sleeper that falls to it wakes. Waking ends the sleep, so it
# happens at most once per sleep. Before 2026-09-20 the line was food 0, and a
# settler slept from food 39 through the night and died four ticks after waking.
HUNGRY_WAKE_FOOD = 20

# A settler will not fall asleep below this much fatigue. Before 2026-09-20 the
# floor was 1, and one settler took ten sleeps of one or two ticks at fatigue
# 1. Collapse at max fatigue is unaffected.
MIN_SLEEP_FATIGUE = 20
