"""Preconditions and intent construction both layers share.

An action the settler can take exists twice over: as one of the options Jev
picks from, and as a planner tool. When the two disagree about whether the
action is legal right now, the settler pays for it — a planner `sleep` at
fatigue 3 spent a tick on a refusal Jev's option layer would never have
offered, and a planner `shout` ignored the cooldown Jev obeys.

An `Attempt` is the one answer: the intent to submit, or the sentence saying
why not. `options.py` turns an allowed one into an `Option`; a planner tool
returns the refusal text or hands the intent to `bridge.direct_action`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .. import world_pb2 as pb
from . import items
from .enclosure import SEAL_MIN_FREE_TILES, blocks_movement, would_seal
from .geometry import (
    NO_DIRECTION,
    ORDERED_DIRECTIONS,
    Coord,
    chebyshev,
    direction_name,
    offset,
    same_or_adjacent,
)
from .worldmodel import VIEW_RADIUS, ObjectInfo, WorldModel

# Every refusal ends with this: the model is paying by the tick, and "you did
# not spend one" is the difference between retrying now and waiting.
NO_TICK = "No tick spent."

# `EntityInfo.entity_type` for a wolf, which is nobody to talk to.
WOLF_ENTITY_TYPE = "wolf"

# What a message board's note fields are cut to.
NOTE_TITLE_MAX = 60
NOTE_TEXT_MAX = 500

# Shouting. The cooldown keeps twelve settlers from filling every ear; it is
# physics, so it binds Jev and the planner alike. `MAX_SHOUT_LENGTH` is the
# one truncation rule for a shout, whoever writes it.
SHOUT_COOLDOWN_TICKS = 8
MAX_SHOUT_LENGTH = 120
# The world's action type for a `SayIntent` (`world/src/world/speech.py`).
SAY_ACTION_TYPE = "say"


def _no_intent() -> pb.Intent:
    """The empty intent a refused `Attempt` carries.

    Protobuf has no null, and an empty message reads better than an optional
    nobody may dereference. It is built per attempt because a proto message is
    mutable and a shared one would be a shared mutable default.
    """
    return pb.Intent()


@dataclass(frozen=True)
class Attempt:
    """Whether an action may be tried right now, and what to say when not.

    Exactly one of the two halves is meaningful: a refused attempt carries
    an empty intent, and an allowed one carries an empty `refusal`.
    """

    intent: pb.Intent = field(default_factory=_no_intent)
    refusal: str = ""
    # What the actor is doing, as `direct_action` and the trace should read it.
    description: str = ""

    @property
    def allowed(self) -> bool:
        """Whether there is an intent to submit."""
        return not self.refusal

    @classmethod
    def refused(cls, reason: str) -> "Attempt":
        """An attempt that costs no tick, carrying the reason it costs none."""
        return cls(refusal=reason)


# --------------------------------------------------------------------------
# Speaking
# --------------------------------------------------------------------------


def shout_attempt(model: WorldModel, text: str) -> Attempt:
    """Shout `text`, unless the last shout was too recent.

    The cooldown is the world's pace, not a rule of etiquette: a settler who
    shouts every tick drowns out the sixty tiles around it.
    """
    last = model.last_own_shout_tick()
    if last >= 0 and model.tick - last < SHOUT_COOLDOWN_TICKS:
        waited = model.tick - last
        return Attempt.refused(
            f"you shouted {waited} tick(s) ago; a shout carries every "
            f"{SHOUT_COOLDOWN_TICKS} ticks. No tick spent."
        )
    line = text[:MAX_SHOUT_LENGTH]
    return Attempt(
        intent=pb.Intent(say=pb.SayIntent(text=line, channel=items.SHOUT_CHANNEL)),
        description=f"shout {line[:60]!r}",
    )


def converse_intent(
    action: str,
    *,
    conversation_id: str = "",
    text: str = "",
    direction: pb.Direction = pb.DIRECTION_UNSPECIFIED,
    target_entity_id: str = "",
) -> pb.Intent:
    """A `ConverseIntent` wrapped in an Intent, with the text truncated.

    `target_entity_id` is the settler a `hail` addresses. Every converse intent
    in the package is built here, so the truncation happens exactly once.
    """
    return pb.Intent(
        converse=pb.ConverseIntent(
            action=action,
            conversation_id=conversation_id,
            text=text[: items.CONVERSATION_TEXT_LIMIT],
            direction=direction,
            target_entity_id=target_entity_id,
        )
    )


def give_intent(entity_id: str, kind: str, amount: int) -> pb.Intent:
    """A `GiveIntent` wrapped in an Intent."""
    return pb.Intent(
        give=pb.GiveIntent(target_entity_id=entity_id, kind=kind, amount=max(1, amount))
    )


# --------------------------------------------------------------------------
# Sleeping
# --------------------------------------------------------------------------


def sleep_attempt(model: WorldModel, bed: str = "") -> Attempt:
    """Lie down on `bed` (or the ground), unless the body is not tired enough.

    The world refuses a sleep below `items.MIN_SLEEP_FATIGUE`, so trying it
    only burns a tick; the option layer has never offered one and the planner's
    `sleep` now says the same thing instead of finding out.
    """
    info = model.self_info
    if info.fatigue < items.MIN_SLEEP_FATIGUE:
        return Attempt.refused(
            f"you are not tired enough to sleep: fatigue {info.fatigue}, and "
            f"the world refuses a sleep below {items.MIN_SLEEP_FATIGUE}. "
            "No tick spent."
        )
    return Attempt(
        intent=pb.Intent(sleep=pb.SleepIntent(object_id=bed)),
        description=f"sleep on {bed or 'the ground'}",
    )


# --------------------------------------------------------------------------
# Eating and gathering
# --------------------------------------------------------------------------


def eat_now(kind: str = items.BERRY) -> Attempt:
    """Eat one `kind`, with no check that the pack holds it.

    For the caller that has just put it there this tick: the world model is
    only refreshed at the top of the next tick, so the pack it shows is one
    action out of date and the check would refuse a berry in the hand.
    """
    return Attempt(
        intent=pb.Intent(eat=pb.EatIntent(item_type=kind, amount=1)),
        description=f"eat {kind}",
    )


def eat_attempt(model: WorldModel, kind: str = items.BERRY) -> Attempt:
    """Eat one `kind` from the pack, unless there is none in it."""
    if model.self_info.inventory.get(kind, 0) <= 0:
        return Attempt.refused(f"you carry no {kind}. {NO_TICK}")
    return eat_now(kind)


def bush_with_berry_here(model: WorldModel) -> str:
    """The id of a bush with a berry on the actor's own tile, or `""`."""
    for obj in model.object_at(model.position):
        if obj.object_type == items.BUSH and obj.has_berry:
            return obj.object_id
    return ""


def collect_here_attempt(model: WorldModel) -> Attempt:
    """Pick the berry off the bush under the body, unless there is none."""
    bush_id = bush_with_berry_here(model)
    if not bush_id:
        return Attempt.refused(
            f"no bush with a berry on it stands on your tile. {NO_TICK}"
        )
    return Attempt(
        intent=pb.Intent(
            collect=pb.CollectIntent(object_id=bush_id, item_type=items.BERRY, amount=1)
        ),
        description=f"pick a berry off {bush_id}",
    )


def _reachable_object(model: WorldModel, object_id: str, verb: str) -> str:
    """`""` when `object_id` is known and on or next to the body, else why not."""
    obj = model.objects.get(object_id)
    if obj is None:
        return f"you have never seen an object called {object_id}. {NO_TICK}"
    if not same_or_adjacent(model.position, obj.position):
        distance = chebyshev(obj.position, model.position)
        return (
            f"{object_id} is at {obj.position}, {distance} tiles away, and you "
            f"can only {verb} what is on or next to your tile. {NO_TICK}"
        )
    return ""


def extract_attempt(model: WorldModel, object_id: str) -> Attempt:
    """Harvest material from an object on or next to the body.

    A vein needs a wielded pickaxe of a high enough tier; everything else
    yields to bare hands, only more slowly.
    """
    refusal = _reachable_object(model, object_id, "harvest")
    if refusal:
        return Attempt.refused(refusal)
    obj = model.objects[object_id]
    wielded = model.self_info.wielded
    if not items.can_extract(wielded, obj.object_type):
        needed = ", ".join(sorted(items.VEIN_REQUIRED_TOOLS.get(obj.object_type, ())))
        holding = f"the {wielded}" if wielded else "nothing"
        return Attempt.refused(
            f"a {obj.object_type} needs one of these wielded: {needed}; you "
            f"hold {holding}. {NO_TICK}"
        )
    return Attempt(
        intent=pb.Intent(extract=pb.ExtractIntent(object_id=object_id)),
        description=f"extract {object_id}",
    )


def dismantle_attempt(model: WorldModel, object_id: str) -> Attempt:
    """Take a placed building piece apart; the planner's tool only (docs/08).

    Jev is never offered this: the intent is an `ExtractIntent`, Jev reads
    "extract" as "gather", and it would cheerfully eat the town wall.
    """
    refusal = _reachable_object(model, object_id, "dismantle")
    if refusal:
        return Attempt.refused(refusal)
    obj = model.objects[object_id]
    if obj.object_type not in items.PLACEABLE_KINDS:
        return Attempt.refused(
            f"{object_id} is a {obj.object_type}, which nobody placed and "
            f"nothing can dismantle. {NO_TICK}"
        )
    return Attempt(
        intent=pb.Intent(extract=pb.ExtractIntent(object_id=object_id)),
        description=f"dismantle {object_id}",
    )


# --------------------------------------------------------------------------
# Putting things down
# --------------------------------------------------------------------------


def can_place_ground(model: WorldModel, position: Coord) -> bool:
    """Whether a road or floor may go on `position`.

    Ground objects never block, but they may not cover a natural object and
    only one may lie on a tile.
    """
    if not model.is_known(position):
        return False
    tile = model.tiles.get(position)
    if tile is not None and not tile.walkable:
        return False
    for obj in model.object_at(position):
        if obj.object_type in items.NATURAL_OBJECT_TYPES:
            return False
        if obj.object_type in items.GROUND_LAYER_KINDS:
            return False
    return True


def can_place_structure(model: WorldModel, position: Coord) -> bool:
    """Whether a wall, door, bed or other structure may go on `position`."""
    if not model.is_known(position) or not model.is_walkable(position):
        return False
    return not model.structure_objects_at(position)


def place_target(model: WorldModel, direction: pb.Direction) -> Coord:
    """The tile a placement in `direction` lands on; the body's own for none."""
    if direction == NO_DIRECTION:
        return model.position
    return offset(model.position, direction)


def _tile_occupants_text(model: WorldModel, target: Coord) -> str:
    """What is standing on `target`, as this actor's own model sees it."""
    parts = [obj.object_id for obj in model.object_at(target)]
    parts.extend(
        entity.entity_id
        for entity in model.entities_near(VIEW_RADIUS)
        if entity.position == target
    )
    tile = model.tiles.get(target)
    if tile is not None and not tile.walkable:
        parts.append(f"{tile.floor_type} you cannot stand on")
    if not parts:
        if tile is None:
            return f"{target} is not in your model of the world"
        return f"{target} looks empty to you"
    return f"{target} holds {', '.join(parts)}"


def _free_direction_text(model: WorldModel, kind: str) -> str:
    """Which neighbouring directions would take `kind`, for the layer it is on."""
    position = model.position
    check = can_place_ground if items.is_ground_kind(kind) else can_place_structure
    free = [
        direction_name(direction)
        for direction in ORDERED_DIRECTIONS
        if check(model, offset(position, direction))
    ]
    if not free:
        return f"no neighbouring tile would take a {kind} either"
    return f"neighbouring tiles that would take a {kind}: {', '.join(free)}"


def place_failure_lines(model: WorldModel, kind: str, direction: pb.Direction) -> str:
    """Why a placement will not work, from the model, plus the free sides.

    The world's own refusal says "target already holds an object" and nothing
    more, so a planner that cannot see the tile places blind; three blind
    `place door` calls in the 2026-09-20 run all failed that way.
    """
    target = place_target(model, direction)
    return f"{_tile_occupants_text(model, target)}; {_free_direction_text(model, kind)}"


def place_attempt(
    model: WorldModel, kind: str, direction: pb.Direction, *, check_seal: bool = True
) -> Attempt:
    """Put one carried building item down on the tile `direction` names.

    Ground pieces (roads and floors) go on the body's own tile and take
    `NO_DIRECTION`; structures go on a neighbour. A structure that would shut
    the actor into a pocket is refused: a settler once walled the six free
    neighbours of her own tile one by one and starved in the cell. A door lets
    settlers through, so placing one is never a seal.
    """
    ground = items.is_ground_kind(kind)
    if ground and direction != NO_DIRECTION:
        return Attempt.refused(
            f"{kind} is a ground piece and goes on the tile you stand on, with "
            f"no direction. {NO_TICK}"
        )
    if not ground and direction == NO_DIRECTION:
        return Attempt.refused(
            f"{kind} is a structure and needs a direction; only "
            f"{sorted(items.GROUND_LAYER_KINDS)} go on your own tile"
        )
    target = place_target(model, direction)
    allowed = (
        can_place_ground(model, target)
        if ground
        else can_place_structure(model, target)
    )
    if not allowed:
        return Attempt.refused(
            f"nothing can go there: {place_failure_lines(model, kind, direction)}. "
            f"{NO_TICK}"
        )
    if not ground:
        if any(entity.position == target for entity in model.entities_near(2)):
            return Attempt.refused(
                f"nothing can go there: {place_failure_lines(model, kind, direction)}. "
                f"{NO_TICK}"
            )
        if (
            check_seal
            and blocks_movement(kind)
            and would_seal(model, model.position, target)
        ):
            return Attempt.refused(
                f"a {kind} there would shut you into a pocket of fewer than "
                f"{SEAL_MIN_FREE_TILES} tiles. {NO_TICK}"
            )
    where = direction_name(direction) if direction != NO_DIRECTION else "here"
    return Attempt(
        intent=pb.Intent(place=pb.PlaceIntent(kind=kind, direction=direction)),
        description=f"place {kind} {where}",
    )


# --------------------------------------------------------------------------
# Piles and chests
# --------------------------------------------------------------------------


def pile_here(model: WorldModel) -> ObjectInfo | None:
    """The item pile on the body's own tile, or None."""
    for obj in model.object_at(model.position):
        if obj.object_type == items.ITEM_PILE:
            return obj
    return None


def pickup_attempt(model: WorldModel, kind: str, amount: int = 1) -> Attempt:
    """Take `kind` out of the pile under the body.

    A pile sits on one tile and the body has to be on it, so a pickup from
    anywhere else is a tick spent on a refusal the model could have read.
    """
    pile = pile_here(model)
    if pile is None:
        return Attempt.refused(f"no item pile lies on the tile you stand on. {NO_TICK}")
    available = pile.contents().get(kind, 0)
    if available <= 0:
        holds = ", ".join(sorted(pile.contents())) or "nothing"
        return Attempt.refused(
            f"the pile here holds no {kind}; it holds {holds}. {NO_TICK}"
        )
    return Attempt(
        intent=pb.Intent(pickup=pb.PickupIntent(kind=kind, amount=max(1, amount))),
        description=f"pickup {amount} {kind}",
    )


def drop_attempt(model: WorldModel, kind: str, amount: int = 1) -> Attempt:
    """Drop `kind` onto the body's tile as a pile."""
    carried = model.self_info.inventory.get(kind, 0)
    if carried <= 0:
        return Attempt.refused(f"you carry no {kind} to drop. {NO_TICK}")
    return Attempt(
        intent=pb.Intent(drop=pb.DropIntent(kind=kind, amount=max(1, amount))),
        description=f"drop {amount} {kind}",
    )


def _chest_within_reach(model: WorldModel, object_id: str) -> ObjectInfo | None:
    """The chest `object_id` names, when it is on or next to the body."""
    obj = model.objects.get(object_id)
    if obj is None or obj.object_type != items.CHEST:
        return None
    if not same_or_adjacent(model.position, obj.position):
        return None
    return obj


def deposit_attempt(
    model: WorldModel, object_id: str, kind: str, amount: int = 1
) -> Attempt:
    """Put `kind` from the pack into a chest on or next to the body."""
    chest = _chest_within_reach(model, object_id)
    if chest is None:
        return Attempt.refused(_no_chest_text(model, object_id))
    carried = model.self_info.inventory.get(kind, 0)
    if carried <= 0:
        return Attempt.refused(f"you carry no {kind} to put in. {NO_TICK}")
    return Attempt(
        intent=pb.Intent(
            deposit=pb.DepositIntent(
                object_id=object_id, kind=kind, amount=max(1, amount)
            )
        ),
        description=f"deposit {amount} {kind} into {object_id}",
    )


def withdraw_attempt(
    model: WorldModel, object_id: str, kind: str, amount: int = 1
) -> Attempt:
    """Take `kind` out of a chest on or next to the body."""
    chest = _chest_within_reach(model, object_id)
    if chest is None:
        return Attempt.refused(_no_chest_text(model, object_id))
    inside = chest.contents().get(kind, 0)
    if inside <= 0:
        holds = ", ".join(sorted(chest.contents())) or "nothing"
        return Attempt.refused(
            f"{object_id} holds no {kind}; it holds {holds}. {NO_TICK}"
        )
    return Attempt(
        intent=pb.Intent(
            withdraw=pb.WithdrawIntent(
                object_id=object_id, kind=kind, amount=max(1, amount)
            )
        ),
        description=f"withdraw {amount} {kind} from {object_id}",
    )


def _no_chest_text(model: WorldModel, object_id: str) -> str:
    """Why a chest id is no good: unseen, the wrong kind, or out of reach."""
    obj = model.objects.get(object_id)
    if obj is None:
        return f"you have never seen a chest called {object_id}. {NO_TICK}"
    if obj.object_type != items.CHEST:
        return f"{object_id} is a {obj.object_type}, not a chest. {NO_TICK}"
    distance = chebyshev(obj.position, model.position)
    return (
        f"{object_id} is at {obj.position}, {distance} tiles away, and a chest "
        f"has to be on or next to your tile. {NO_TICK}"
    )


# --------------------------------------------------------------------------
# Reaching other settlers
# --------------------------------------------------------------------------


def seated_settlers(model: WorldModel) -> frozenset[str]:
    """Everyone this actor can see holding a conversation seat."""
    return frozenset(
        participant
        for conversation in model.conversations()
        for participant in conversation.participants
    )


def hail_refusal(model: WorldModel, settler: str) -> str:
    """Why this actor cannot hail `settler` right now, or `""` (docs/09 §9).

    Adjacency is deliberately not checked here: both layers walk over first,
    and the option layer offers the walk as the same `hail:` key.
    """
    if model.my_conversation() is not None:
        return f"you are already in a conversation. {NO_TICK}"
    if settler == model.entity_id:
        return f"you cannot hail yourself. {NO_TICK}"
    target = model.entities.get(settler)
    if target is None or target.entity_type == WOLF_ENTITY_TYPE:
        return f"you have never seen a settler called {settler}"
    if not target.alive:
        return f"{settler} is dead. {NO_TICK}"
    if target.asleep and target.last_seen == model.tick:
        return f"{settler} is asleep right now and cannot be hailed"
    if settler in seated_settlers(model):
        return f"{settler} is already in a conversation. {NO_TICK}"
    return ""


def hail_attempt(model: WorldModel, settler: str, line: str) -> Attempt:
    """Address the settler beside you, which seats you both (docs/09 §9)."""
    refusal = hail_refusal(model, settler)
    if refusal:
        return Attempt.refused(refusal)
    if not line.strip():
        return Attempt.refused(
            f"a hail needs an opening line: what you say when you reach {settler}"
        )
    target = model.entities[settler]
    if chebyshev(target.position, model.position) != 1:
        return Attempt.refused(
            f"you are not next to {settler} yet; last seen at {target.position} "
            f"on tick {target.last_seen}"
        )
    return Attempt(
        intent=converse_intent(
            items.ACTION_HAIL, target_entity_id=settler, text=line.strip()
        ),
        description=f"hail {settler} with {line.strip()!r}",
    )


def join_attempt(model: WorldModel, conversation_id: str) -> Attempt:
    """Take a free seat at a conversation whose anchor is next to the body."""
    if model.my_conversation() is not None:
        return Attempt.refused(f"you are already in a conversation. {NO_TICK}")
    conversation = model.conversation_by_id(conversation_id)
    if conversation is None:
        return Attempt.refused(
            f"you have not seen a conversation called {conversation_id!r}"
        )
    if conversation.free_seats <= 0:
        return Attempt.refused(
            f"{conversation_id} has no free seat "
            f"({len(conversation.participants)} settlers in it)"
        )
    if chebyshev(conversation.anchor, model.position) != 1:
        return Attempt.refused(f"you are not next to {conversation_id} yet")
    return Attempt(
        intent=converse_intent("join", conversation_id=conversation_id),
        description=f"join {conversation_id}",
    )


def give_attempt(
    model: WorldModel, entity_id: str, kind: str, amount: int = 1
) -> Attempt:
    """Hand items to a settler next to you or seated in your conversation."""
    if not entity_id:
        return Attempt.refused(f"a give needs the name of who receives it. {NO_TICK}")
    if not kind:
        return Attempt.refused(
            f"a give needs the name of an item in your pack. {NO_TICK}"
        )
    carried = model.self_info.inventory.get(kind, 0)
    if carried <= 0:
        return Attempt.refused(f"you carry no {kind} to give. {NO_TICK}")
    return Attempt(
        intent=give_intent(entity_id, kind, amount),
        description=f"give {max(1, amount)} {kind} to {entity_id}",
    )


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def wrong_note_target(
    model: WorldModel, object_id: str, wanted_kind: str, right_tool: str
) -> str:
    """`""` when `object_id` is a `wanted_kind`, else why and what to use instead.

    Only checked against what this actor has actually seen; an object it has
    never observed is left to the world, which still refuses it by id.
    """
    obj = model.objects.get(object_id)
    if obj is None or obj.object_type == wanted_kind:
        return ""
    return f"{object_id} is a {obj.object_type}, not a {wanted_kind}; use {right_tool}"


def write_sign_attempt(model: WorldModel, sign_id: str, text: str) -> Attempt:
    """Write (or blank) the one line a standing sign carries."""
    refusal = wrong_note_target(model, sign_id, items.SIGN, "write_note")
    if refusal:
        return Attempt.refused(refusal)
    line = text[: items.SIGN_TEXT_MAX]
    return Attempt(
        intent=pb.Intent(
            write_note=pb.WriteNoteIntent(
                object_id=sign_id, slot=items.SIGN_SLOT, title="", text=line
            )
        ),
        description=f'write "{line}" on {sign_id}' if line else f"blank {sign_id}",
    )


def write_note_attempt(
    model: WorldModel, board_id: str, slot: int, title: str, text: str
) -> Attempt:
    """Write one of a message board's twenty note slots."""
    refusal = wrong_note_target(model, board_id, items.MESSAGE_BOARD, "write_sign")
    if refusal:
        return Attempt.refused(refusal)
    return Attempt(
        intent=pb.Intent(
            write_note=pb.WriteNoteIntent(
                object_id=board_id,
                slot=slot,
                title=title[:NOTE_TITLE_MAX],
                text=text[:NOTE_TEXT_MAX],
            )
        ),
        description=f"write note {slot} on {board_id}",
    )


# --------------------------------------------------------------------------
# Who may take which action
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Exposure:
    """Which of the two layers may take one kind of action, and why.

    The asymmetries are deliberate, and each one was paid for once. Writing
    them down here, with a test that checks the real option keys and the real
    tool names against it, is what keeps a later refactor from "tidying" one
    of them away.
    """

    kind: str
    jev: bool
    planner: bool
    note: str = ""


EXPOSURE: Mapping[str, Exposure] = {
    exposure.kind: exposure
    for exposure in (
        Exposure("wait", jev=True, planner=True),
        Exposure("move", jev=True, planner=False, note="a planner step costs 3 ticks"),
        Exposure("attack", jev=True, planner=False, note="fighting is world speed"),
        Exposure("extract", jev=True, planner=False, note="gathering is world speed"),
        Exposure("collect", jev=True, planner=False, note="gathering is world speed"),
        Exposure("eat", jev=True, planner=True),
        Exposure("craft", jev=True, planner=True),
        Exposure("equip", jev=True, planner=True),
        Exposure("place", jev=True, planner=True),
        Exposure(
            "place_sign",
            jev=False,
            planner=True,
            note="Jev cannot write, so it would plant blank posts",
        ),
        Exposure("write_sign", jev=False, planner=True, note="Jev cannot write"),
        Exposure("write_note", jev=False, planner=True, note="Jev cannot write"),
        Exposure("rest", jev=True, planner=True),
        Exposure("sleep", jev=True, planner=True),
        Exposure("wake", jev=True, planner=True),
        Exposure("pickup", jev=True, planner=True),
        Exposure("drop", jev=False, planner=True, note="Jev has no reason to shed"),
        Exposure("deposit", jev=True, planner=True),
        Exposure("withdraw", jev=True, planner=True),
        Exposure("give", jev=False, planner=True, note="giving needs a reason"),
        Exposure("shout", jev=True, planner=True, note="Jev only says the brief's"),
        Exposure("hail", jev=True, planner=True, note="Jev only says the brief's"),
        Exposure("join_conversation", jev=True, planner=True),
        Exposure("open_conversation", jev=False, planner=True, note="needs a purpose"),
        Exposure(
            "dismantle",
            jev=False,
            planner=True,
            note="Jev reads extract as gather and would eat the town wall",
        ),
        Exposure("build", jev=False, planner=True, note="shapes are arithmetic"),
        Exposure("travel_to", jev=False, planner=True, note="code owns the walk"),
    )
}


def jev_action_kinds() -> frozenset[str]:
    """Every action kind the option layer may put in front of Jev."""
    return frozenset(kind for kind, e in EXPOSURE.items() if e.jev)


def planner_action_kinds() -> frozenset[str]:
    """Every action kind the planner has a tool for."""
    return frozenset(kind for kind, e in EXPOSURE.items() if e.planner)
