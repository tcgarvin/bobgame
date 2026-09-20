"""The lines every tool result and every turn prompt carries.

The clock line, the `!!` body alerts and the threat alert: physics only, so a
turn that has run for hundreds of ticks always knows what the body is doing.
"""

from __future__ import annotations

from .. import items
from ..enclosure import enclosed_fact
from ..geometry import chebyshev
from ..options import WOLF_ALERT_RADIUS
from ..worldmodel import WorldModel
from .common import FATIGUE_ALERT_MARGIN, FOOD_ALERT_AT, RECENT_ATTACK_TICKS


def turn_clock_line(model: WorldModel, turn_start_tick: int) -> str:
    """The tick, the clock, the turn's cost so far, and the body's three numbers.

    A turn lasts hundreds of ticks, so the `look` the model opened with is
    stale by the time it reads this. The body's state goes on every tool
    result instead of waiting for the next turn.
    """
    elapsed = max(0, model.tick - turn_start_tick)
    info = model.self_info
    moon = model.clock.moon_text()
    return (
        f"[tick {model.tick} · {model.clock.as_text()}; "
        f"this turn has cost {elapsed} ticks so far; "
        f"food {info.food}/{info.max_food}, "
        f"health {info.health}/{info.max_health}, "
        f"fatigue {info.fatigue}/{info.max_fatigue}"
        f"{'; ' + moon if moon else ''}]"
    )


def body_alerts(model: WorldModel) -> list[str]:
    """`!!` lines for a body that is starving, nearly starving or nearly spent.

    Physics only: what the numbers are and what they do next. A dead or
    sleeping body gets none: it cannot act on them.
    """
    info = model.self_info
    if not info.alive or info.asleep:
        return []
    alerts: list[str] = []
    if info.food <= 0:
        alerts.append(
            f"!! STARVING: food 0/{info.max_food}; you lose "
            f"{items.STARVATION_DAMAGE} health every "
            f"{items.STARVATION_INTERVAL_TICKS} ticks until you eat "
            f"(health {info.health}/{info.max_health}). One berry restores "
            f"{items.BERRY_FOOD_RESTORE} food."
        )
    elif info.food <= FOOD_ALERT_AT:
        alerts.append(
            f"!! FOOD LOW: food {info.food}/{info.max_food}, falling 1 every "
            f"{items.FOOD_INTERVAL_TICKS} ticks; at 0 you lose "
            f"{items.STARVATION_DAMAGE} health every "
            f"{items.STARVATION_INTERVAL_TICKS} ticks. One berry restores "
            f"{items.BERRY_FOOD_RESTORE} food."
        )
    if info.fatigue >= items.PLAYER_MAX_FATIGUE - FATIGUE_ALERT_MARGIN:
        alerts.append(
            f"!! FATIGUE {info.fatigue}/{info.max_fatigue}: at "
            f"{items.PLAYER_MAX_FATIGUE} you collapse where you stand and sleep "
            f"until fatigue {items.COLLAPSE_WAKE_FATIGUE}."
        )
    enclosed = enclosed_fact(model)
    if enclosed:
        alerts.append(enclosed)
    return alerts


def alert_window_start(tick: int, turn_start_tick: int) -> int:
    """The first tick whose bites still count as "under attack" right now.

    A turn lasts hundreds of ticks because stints run inside it, so damage
    from early in the turn says nothing about now: only the last few ticks do.
    """
    return max(turn_start_tick, tick - RECENT_ATTACK_TICKS)


def threat_alert(model: WorldModel, since_tick: int) -> str:
    """A loud line when the actor is being bitten or a wolf is close, else `""`.

    The planner cannot act every tick, so the alert always ends the same way:
    whatever the response is, it has to be handed to Jev.
    """
    info = model.self_info
    hits = [hit for hit in model.damage_since(since_tick) if hit.attacker_id]
    if hits and info.alive:
        lost = sum(hit.amount for hit in hits)
        attackers = ", ".join(sorted({hit.attacker_id for hit in hits}))
        return (
            f"!! UNDER ATTACK: {attackers} took {lost} health from you in the "
            f"last {max(1, model.tick - since_tick)} ticks "
            f"(health {info.health}/{info.max_health}). "
            f"{HAND_BACK_ADVICE}"
        )
    wolves = model.wolves_near(WOLF_ALERT_RADIUS)
    if wolves and info.alive:
        nearest = wolves[0]
        distance = chebyshev(nearest.position, info.position)
        allies = len(model.allies_near(info.position, 3))
        return (
            f"!! WOLF NEAR: {nearest.entity_id} is {distance} tiles away at "
            f"{nearest.position}, {allies} settlers within 3 tiles of you. "
            f"{HAND_BACK_ADVICE}"
        )
    return ""


HAND_BACK_ADVICE = (
    "It acts every tick and you only act every few ticks. Whatever you want "
    "your body to do about it has to go to Jev in a start_stint brief; nothing "
    "you do from here happens at world speed."
)
