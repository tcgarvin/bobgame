"""Options that keep the actor alive: eating, resting, sleeping, fighting, shouting."""

from __future__ import annotations

from typing import Mapping, Sequence

from ... import world_pb2 as pb
from .. import items
from ..actions import eat_attempt, shout_attempt, sleep_attempt
from ..briefs import Option, TravelState
from ..geometry import Coord, chebyshev, offset
from ..walk import plan_step
from ..worldmodel import EntityInfo, WorldModel
from .common import (
    HEARD_SHOUT_KEY_PREFIX,
    HEARD_SHOUT_MAX_AGE_TICKS,
    MAX_BRIEF_SHOUTS,
    SHOUT_KEY_PREFIX,
    _move,
)


def _survival_options(
    model: WorldModel,
    inventory: Mapping[str, int],
    position: Coord,
    travel: TravelState | None,
    shouts: Sequence[str],
) -> list[Option]:
    options: list[Option] = []
    eat = eat_attempt(model, items.BERRY)
    if eat.allowed:
        food = model.self_info.food
        options.append(
            Option(
                key="eat:berry",
                description=f"eat a berry to restore 20 food (food now {food})",
                intent=eat.intent,
            )
        )
    options.extend(_rest_options(model))
    options.extend(_sleep_options(model))
    options.extend(_shout_options(model, shouts))
    options.extend(_heard_shout_options(model, position, travel))
    for entity in model.entities_near(1):
        if not entity.alive or entity.entity_id == model.entity_id:
            continue
        if chebyshev(entity.position, position) != 1:
            continue
        options.append(
            Option(
                key=f"attack:{entity.entity_id}",
                description=(
                    f"attack the adjacent {entity.entity_type} {entity.entity_id} "
                    f"(health {entity.health}/{entity.max_health}"
                    f"{_allies_in_the_fight(model, entity)})"
                ),
                intent=pb.Intent(
                    attack=pb.AttackIntent(target_entity_id=entity.entity_id)
                ),
            )
        )
    return options


def _allies_in_the_fight(model: WorldModel, target: EntityInfo) -> str:
    """For a wolf, how many other settlers are already next to it."""
    if target.entity_type != "wolf":
        return ""
    allies = len(model.allies_near(target.position, 1))
    return f", {allies} other settlers next to it"


def _shout_options(model: WorldModel, shouts: Sequence[str]) -> list[Option]:
    """One option per shout phrase the planner wrote into the brief.

    When to shout is Jev's call under the brief; code only enforces a cooldown.
    """
    options: list[Option] = []
    for index, phrase in enumerate(shouts[:MAX_BRIEF_SHOUTS]):
        attempt = shout_attempt(model, phrase)
        if not attempt.allowed:
            return []
        options.append(
            Option(
                key=f"{SHOUT_KEY_PREFIX}{index}",
                description=(
                    f'shout "{phrase}" - every settler within {items.SHOUT_RADIUS} '
                    "tiles hears it and where it came from"
                ),
                intent=attempt.intent,
            )
        )
    return options


def _heard_shout_options(
    model: WorldModel, position: Coord, travel: TravelState | None
) -> list[Option]:
    """Walking to where the most recent shout came from, whatever it was about.

    Not offered again while the actor is already on its way to that spot.
    """
    for shout in model.recent_shouts(HEARD_SHOUT_MAX_AGE_TICKS):
        target = shout.position
        distance = chebyshev(target, position)
        if distance <= 1:
            continue
        label = f"where {shout.speaker_id} shouted"
        already_going = (
            travel is not None and travel.label == label and travel.target == target
        )
        if already_going:
            continue
        step = plan_step(model, position, target, stop_adjacent=True)
        if not step.found:
            continue
        age = model.tick - shout.tick
        return [
            Option(
                key=f"{HEARD_SHOUT_KEY_PREFIX}{shout.speaker_id}",
                description=(
                    f'go to where {shout.speaker_id} shouted "{shout.text}" '
                    f"{age} ticks ago, {distance} tiles away"
                ),
                intent=_move(step.direction),
                travel_target=TravelState(
                    target=target,
                    label=label,
                    stop_adjacent=True,
                ),
            )
        ]
    return []


def _rest_options(model: WorldModel) -> list[Option]:
    """Resting on a bed, offered only to a wounded actor standing by one."""
    info = model.self_info
    if info.health >= info.max_health or info.food <= 0:
        return []
    for obj in model.objects_near(1):
        if obj.object_type != items.BED:
            continue
        return [
            Option(
                key=f"rest:{obj.object_id}",
                description=(
                    f"rest on the bed {obj.object_id} to heal {items.REST_HEAL} "
                    f"health (health now {info.health}/{info.max_health})"
                ),
                intent=pb.Intent(rest=pb.RestIntent(object_id=obj.object_id)),
            )
        ]
    return []


def _sleep_options(model: WorldModel) -> list[Option]:
    """Sleeping on an adjacent bed or on the ground, and waking again.

    While asleep the only legal action is waking, so the two sets never appear
    together.
    """
    info = model.self_info
    night = model.clock.night
    if info.asleep:
        return [
            Option(
                key="wake",
                description=(
                    f"stop sleeping and stand up (fatigue "
                    f"{info.fatigue}/{info.max_fatigue})"
                ),
                intent=pb.Intent(wake=pb.WakeIntent()),
            )
        ]
    # The world refuses a sleep below this fatigue, so it is not an option.
    if not sleep_attempt(model).allowed:
        return []
    options: list[Option] = []
    for obj in model.objects_near(1):
        if obj.object_type != items.BED:
            continue
        options.append(
            Option(
                key=f"sleep:{obj.object_id}",
                description=(
                    f"sleep on the bed {obj.object_id}: it recovers "
                    f"{items.sleep_recovery_text(True, night)} and heals 1 health "
                    f"every {items.REGEN_INTERVAL_TICKS} ticks while you sleep "
                    f"(fatigue {info.fatigue}/{info.max_fatigue}). You wake at "
                    f"fatigue 0, on damage, at food {items.HUNGRY_WAKE_FOOD}, "
                    "or on a wake action"
                ),
                intent=pb.Intent(sleep=pb.SleepIntent(object_id=obj.object_id)),
            )
        )
        break
    options.append(
        Option(
            key="sleep:ground",
            description=(
                f"sleep on the ground where you stand: it recovers "
                f"{items.sleep_recovery_text(False, night)} while you sleep "
                f"(fatigue {info.fatigue}/{info.max_fatigue}). You wake at "
                f"fatigue 0, on damage, at food {items.HUNGRY_WAKE_FOOD}, "
                "or on a wake action"
            ),
            intent=pb.Intent(sleep=pb.SleepIntent()),
        )
    )
    return options


def retreat_option(model: WorldModel, options: Sequence[Option]) -> Option | None:
    """The move that best backs away from the nearest wolf, toward the settlement.

    Used by the hard danger rule, which overrides Jev when the actor is about to
    die. Returns None when no movement option improves the situation.
    """
    wolf = model.nearest_wolf()
    if wolf is None:
        return None
    position = model.position
    settlement = model.settlement
    best: Option | None = None
    best_score = float("-inf")
    current_gap = chebyshev(position, wolf.position)
    for option in options:
        if not option.key.startswith("move_"):
            continue
        direction = option.intent.move.direction
        target = offset(position, direction)
        gap = chebyshev(target, wolf.position)
        if gap <= current_gap:
            continue
        score = gap * 2.0 - chebyshev(target, settlement) * 0.1
        if score > best_score:
            best_score = score
            best = option
    return best
