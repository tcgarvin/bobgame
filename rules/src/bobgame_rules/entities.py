"""Who lives in the world: entity types, their maxima, combat and wolves."""

from typing import Mapping

from .items import (
    AXE,
    COPPER_AXE,
    COPPER_PICKAXE,
    IRON_AXE,
    IRON_PICKAXE,
    IRON_SWORD,
    PICKAXE,
    SWORD,
)

# --- Entity types ----------------------------------------------------------

# `Entity.entity_type` of a settler and of a wolf.
DEFAULT_ENTITY_TYPE = "default"
WOLF_ENTITY_TYPE = "wolf"

# --- What a settler's body holds -------------------------------------------

PLAYER_MAX_HEALTH = 20
PLAYER_MAX_FOOD = 100
PLAYER_MAX_FATIGUE = 100

# --- Combat ----------------------------------------------------------------

# A wolf bites for this much before any wielded bonus.
WOLF_ATTACK_DAMAGE = 3

# entity_type -> damage one attack deals bare.
BASE_ATTACK_DAMAGE: Mapping[str, int] = {WOLF_ENTITY_TYPE: WOLF_ATTACK_DAMAGE}
# What everyone else (a settler) hits for with nothing in hand.
DEFAULT_ATTACK_DAMAGE = 2

WIELD_DAMAGE_BONUS: Mapping[str, int] = {
    SWORD: 3,
    IRON_SWORD: 5,
    AXE: 2,
    COPPER_AXE: 2,
    IRON_AXE: 3,
    PICKAXE: 1,
    COPPER_PICKAXE: 1,
    IRON_PICKAXE: 2,
}

# Kinds a settler can wield; everything else in hand gives no bonus.
WIELDABLE_KINDS: frozenset[str] = frozenset(WIELD_DAMAGE_BONUS)

# Attack damage lost while tired (docs/10_metal_and_sleep.md, "Fatigue").
TIRED_DAMAGE_PENALTY = 1


def attack_damage(entity_type: str, wielded: str) -> int:
    """Damage one attack of this entity type wielding `wielded` deals."""
    base = BASE_ATTACK_DAMAGE.get(entity_type, DEFAULT_ATTACK_DAMAGE)
    return base + WIELD_DAMAGE_BONUS.get(wielded, 0)


# --- Wolves ----------------------------------------------------------------

# Tuned so a lone settler wins a wolf fight only at the cost of about half
# their health, while two or three attacking together barely get scratched:
# damage is simultaneous, so every extra attacker shortens the fight.
WOLF_MAX_HEALTH = 16

# Scenario-tunable defaults; a world config may override the first four
# (`max_wolves`, `wolf_spawn_min_distance`, `wolf_spawn_max_distance`,
# `wolf_spawn_interval_ticks`).
WOLF_SPAWN_INTERVAL_TICKS = 40
MAX_WOLVES = 3
WOLF_SPAWN_MIN_DISTANCE = 20
WOLF_SPAWN_MAX_DISTANCE = 40
WOLF_CHASE_RADIUS = 8
WOLF_DESPAWN_DISTANCE = 50
WOLF_WANDER_CHANCE = 0.5
# When wandering, a wolf that can smell a player (beyond WOLF_CHASE_RADIUS)
# drifts toward them this often; otherwise wolves spawn 20+ tiles out and never
# meet anyone.
WOLF_PROWL_CHANCE = 0.6
WOLF_SPAWN_ATTEMPTS = 400
