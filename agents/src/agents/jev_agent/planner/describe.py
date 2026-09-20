"""`describe_world`: everything the actor knows, rendered for one `look`.

Also the renderers the single-tick tools borrow when a call fails and the model
needs to be told where to go instead.
"""

from __future__ import annotations

from .. import items
from ..enclosure import own_pieces_line
from ..geometry import chebyshev
from ..worldmodel import VIEW_RADIUS, HeardUtterance, WorldModel
from .common import WOLF_ENTITY_TYPE

# How many item piles `look` and a failed `pickup` list, nearest first.
PILES_SHOWN = 6
# How many berry bushes an `eat` with an empty pack lists, nearest first.
BUSHES_SHOWN = 6
# How many signs `look` lists, nearest first: everything in view plus a few
# more the actor remembers.
SIGNS_SHOWN = 8

# Object types that show up in bulk once building starts; `describe_world`
# prints a count and one example for these instead of three.
BULK_OBJECT_TYPES: frozenset[str] = items.GROUND_LAYER_KINDS | frozenset(
    {items.WOOD_WALL, items.STONE_WALL}
)

# The things a builder keeps asking about: where can I craft, where can I heal,
# and where is the raw material for planks, rope and stone walls.
BUILD_SITE_TYPES: tuple[str, ...] = (
    items.WORKSHOP_TABLE,
    items.FURNACE,
    items.ANVIL,
    items.BED,
    items.DOOR,
    items.REEDS,
    items.CLAY_DEPOSIT,
    items.COPPER_VEIN,
    items.IRON_VEIN,
)


def _build_site_lines(model: WorldModel) -> list[str]:
    """One terse line per building landmark the actor knows about."""
    lines: list[str] = []
    origin = model.position
    for object_type in BUILD_SITE_TYPES:
        known = model.objects_by_type([object_type])
        if not known:
            continue
        nearest = known[0]
        distance = chebyshev(nearest.position, origin)
        reach = "here" if distance <= 1 else f"d{distance}"
        lines.append(
            f"  {object_type}: {len(known)} known; nearest {nearest.object_id} "
            f"at {nearest.position} ({reach})"
        )
    if not lines:
        return []
    return ["building landmarks:"] + lines


def _roster_lines(model: WorldModel) -> list[str]:
    """Every settler the actor has met, with where and when it last saw them."""
    met = sorted(
        (
            entity
            for entity in model.entities.values()
            if entity.entity_id != model.entity_id and entity.entity_type != "wolf"
        ),
        key=lambda entity: entity.entity_id,
    )
    if not met:
        return ["settlers you have met: none yet"]
    lines = ["settlers you have met (last seen):"]
    for entity in met:
        age = model.tick - entity.last_seen
        when = "now" if age <= 0 else f"{age} ticks ago"
        if not entity.alive:
            state = ", dead"
        elif entity.asleep:
            state = f", asleep as of {when}"
        else:
            state = ""
        lines.append(
            f"  {entity.entity_id} at {entity.position} "
            f"(d{chebyshev(entity.position, model.position)}, {when}{state})"
        )
    return lines


def _wolf_memory_lines(model: WorldModel) -> list[str]:
    """Wolves this actor knows of but cannot see, and the deaths it witnessed.

    Three settlers in the 2026-09-20 hamlet run hunted wolf_6 for a thousand
    ticks after watching it die, because nothing they were shown said so.
    """
    lines: list[str] = []
    out_of_sight = [
        entity
        for entity in model.entities.values()
        if entity.entity_type == WOLF_ENTITY_TYPE
        and entity.alive
        and model.death_of(entity.entity_id) is None
        and chebyshev(entity.position, model.position) > VIEW_RADIUS
    ]
    for wolf in sorted(out_of_sight, key=lambda w: w.entity_id):
        age = model.tick - wolf.last_seen
        lines.append(f"  {wolf.entity_id} last seen {age} ticks ago at {wolf.position}")
    if lines:
        lines.insert(0, "wolves you know of but cannot see:")

    dead = [
        death
        for death in model.recent_deaths()
        if death.entity_type == WOLF_ENTITY_TYPE
    ]
    if dead:
        seen = ", ".join(f"{d.entity_id} (tick {d.tick})" for d in dead)
        lines.append(f"wolves you saw die: {seen}")
    return lines


def _conversation_lines(model: WorldModel) -> list[str]:
    """The conversations in view: id, anchor, who is seated, free seats."""
    conversations = model.conversations()
    if not conversations:
        return ["conversations in view: none"]
    lines = ["conversations in view:"]
    lines.extend(f"  {conversation.summary()}" for conversation in conversations)
    return lines


def _heard_line(utterance: HeardUtterance, tick: int) -> str:
    """One heard line; shouts carry their origin so the planner can go there."""
    if utterance.channel != items.SHOUT_CHANNEL:
        return f'{utterance.speaker_id}: "{utterance.text}"'
    return (
        f"{utterance.speaker_id} SHOUTED from {utterance.position}, "
        f'{tick - utterance.tick} ticks ago: "{utterance.text}"'
    )


def berry_bush_lines(model: WorldModel, limit: int) -> list[str]:
    """The nearest known bushes that carried a berry when last seen."""
    position = model.self_info.position
    lines: list[str] = []
    for bush in model.objects_by_type([items.BUSH]):
        if not bush.has_berry:
            continue
        distance = chebyshev(bush.position, position)
        seen = (
            "in view" if distance <= VIEW_RADIUS else f"last seen tick {bush.last_seen}"
        )
        lines.append(
            f"bush {bush.object_id} at {bush.position} (d{distance}): berry ({seen})"
        )
        if len(lines) >= limit:
            break
    return lines


def _bush_with_berry_here(model: WorldModel) -> str:
    """The id of a bush with a berry on the actor's own tile, or `""`."""
    for obj in model.object_at(model.position):
        if obj.object_type == items.BUSH and obj.has_berry:
            return obj.object_id
    return ""


def pile_lines(model: WorldModel, limit: int) -> list[str]:
    """The nearest item piles and what each holds, one line per pile."""
    position = model.self_info.position
    lines: list[str] = []
    for pile in model.objects_by_type([items.ITEM_PILE])[:limit]:
        contents = pile.contents()
        if not contents:
            continue
        summary = ", ".join(f"{k} x{v}" for k, v in sorted(contents.items()))
        lines.append(
            f"item pile {pile.object_id} at {pile.position} "
            f"(d{chebyshev(pile.position, position)}): {summary}"
        )
    return lines


def _sign_lines(model: WorldModel, limit: int) -> list[str]:
    """Every sign in view plus the nearest few known, with what each reads."""
    signs = model.signs_known()
    if not signs:
        return []
    position = model.self_info.position
    lines = ["signs:"]
    for sign in signs[:limit]:
        distance = chebyshev(sign.position, position)
        where = "in view" if distance <= VIEW_RADIUS else f"d{distance}"
        if sign.sign_text:
            reads = (
                f'"{sign.sign_text}" (by {sign.sign_author or "nobody"}, '
                f"tick {sign.sign_tick})"
            )
        else:
            reads = "blank"
        lines.append(f"  {sign.object_id} at {sign.position} ({where}): {reads}")
    return lines


def describe_world(model: WorldModel) -> str:
    """The `look()` summary: everything the actor knows, in a readable block."""
    info = model.self_info
    dx, dy = model.settlement_offset()
    inventory = (
        ", ".join(f"{kind} x{count}" for kind, count in sorted(info.inventory.items()))
        or "empty"
    )
    moon = model.clock.moon_text()
    lines = [
        f"tick {model.tick} · {model.clock.as_text()}"
        f"{'; ' + moon if moon else ''}, you are "
        f"{model.entity_id} at {info.position}",
        f"health {info.health}/{info.max_health}, food {info.food}/{info.max_food}"
        f", fatigue {info.fatigue}/{info.max_fatigue} ({info.fatigue_word})"
        f", wielded: {info.wielded or 'nothing'}, alive: {info.alive}"
        f"{', asleep' if info.asleep else ''}",
        f"inventory: {inventory}",
        f"settlement centre is dx {dx}, dy {dy} (distance "
        f"{chebyshev(info.position, model.settlement)})",
    ]

    grouped: dict[str, list[str]] = {}
    for obj in model.objects.values():
        grouped.setdefault(obj.object_type, []).append(obj.object_id)
    if grouped:
        lines.append("known objects:")
        for object_type in sorted(grouped):
            count = len(grouped[object_type])
            # Roads, floors and walls come in dozens; a count and one example is
            # all the planner can act on without drowning the prompt.
            shown = 1 if object_type in BULK_OBJECT_TYPES else 3
            nearest = model.objects_by_type([object_type])[:shown]
            examples = ", ".join(
                f"{o.object_id} at {o.position} "
                f"(d{chebyshev(o.position, info.position)})"
                for o in nearest
            )
            lines.append(f"  {object_type}: {count} known; nearest {examples}")
    else:
        lines.append("known objects: none yet")

    own = own_pieces_line(model)
    if own:
        lines.append(own)

    lines.extend(_build_site_lines(model))

    for chest in model.objects_by_type(["chest"])[:4]:
        contents = chest.contents() or {"(empty)": 0}
        summary = ", ".join(f"{k} x{v}" for k, v in sorted(contents.items()))
        lines.append(f"chest {chest.object_id} at {chest.position}: {summary}")

    lines.extend(pile_lines(model, PILES_SHOWN))
    lines.extend(_sign_lines(model, SIGNS_SHOWN))

    for board in model.objects_by_type(["message_board"])[:2]:
        unread = model.unread_note_count(board.object_id)
        new_text = f", {unread} new since you last read" if unread else ""
        lines.append(f"message board {board.object_id} at {board.position}{new_text}:")
        by_slot = board.notes_by_slot()
        if not by_slot:
            lines.append("  (no notes yet)")
        for slot, note in sorted(by_slot.items()):
            title = str(note.get("title", ""))
            author = str(note.get("author", "?"))
            if not title:
                continue
            mark = " (new)" if model.is_note_unread(board.object_id, slot, note) else ""
            lines.append(f"  [{slot}] {title} - {author}{mark}")

    visible = model.entities_near(8)
    if visible:
        lines.append("entities in view:")
        for entity in visible:
            wielding = f", wielding {entity.wielded}" if entity.wielded else ""
            state = ""
            if not entity.alive:
                state = ", dead"
            elif entity.asleep:
                state = ", asleep"
            lines.append(
                f"  {entity.entity_id} ({entity.entity_type}) at {entity.position}, "
                f"hp {entity.health}/{entity.max_health}{wielding}{state}"
            )
    else:
        lines.append("entities in view: none")

    lines.extend(_wolf_memory_lines(model))
    lines.extend(_roster_lines(model))
    lines.extend(_conversation_lines(model))

    heard = model.recent_utterances(5)
    if heard:
        lines.append("recently heard:")
        lines.extend(f"  {_heard_line(u, model.tick)}" for u in heard)

    recent = model.recent_history(8)
    if recent:
        lines.append("your recent ticks:")
        lines.extend(f"  {line}" for line in recent)

    return "\n".join(lines)
