"""The `say` mechanic: one line on a channel, heard by distance.

A conversation (`conversations.py`) is the only back-and-forth channel; `say`
is a single line on `local` or `shout`, or a viewer-only `thought`.
"""

from typing import Mapping

from .events import TickEvents, UtteranceEvent
from .state import WOLF_ENTITY_TYPE, World
from .types import HEARING_RADIUS_BY_CHANNEL, SAY_CHANNELS, SayIntent

__all__ = ["hearer_ids", "process_say_phase"]


def hearer_ids(world: World, entity_id: str, channel: str) -> list[str]:
    """Who hears `entity_id` on `channel`: living, non-wolf settlers within
    the channel's earshot (`HEARING_RADIUS_BY_CHANNEL`), sorted for a stable
    report, and never the speaker.

    Used only to tell the speaker who heard them (docs/09); what each listener
    actually receives is filtered independently in
    `services/observation_service.py`.
    """
    radius = HEARING_RADIUS_BY_CHANNEL.get(channel)
    if radius is None:
        return []
    speaker = world.get_entity(entity_id)
    heard = []
    for other_id, other in world.all_entities().items():
        if other_id == entity_id or not other.alive:
            continue
        if other.entity_type == WOLF_ENTITY_TYPE:
            continue
        if (
            abs(other.position.x - speaker.position.x) <= radius
            and abs(other.position.y - speaker.position.y) <= radius
        ):
            heard.append(other_id)
    return sorted(heard)


def process_say_phase(
    world: World, intents: Mapping[str, SayIntent], events: TickEvents
) -> None:
    """Emit one utterance per speaker; channel filtering happens downstream.

    The action result's details carry who heard it (comma-separated entity
    ids, empty when nobody did) for `local` and `shout`, so the speaker's own
    tool result can say so; `thought` (viewer-only, no in-world hearers) keeps
    the plain channel name it always had.
    """
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        if intent.channel not in SAY_CHANNELS:
            events.acted(entity_id, "say", False, f"unknown channel {intent.channel}")
            continue
        entity = world.get_entity(entity_id)
        events.utterances.append(
            UtteranceEvent(
                speaker_id=entity_id,
                channel=intent.channel,
                text=intent.text,
                position=entity.position,
            )
        )
        if intent.channel in HEARING_RADIUS_BY_CHANNEL:
            detail = f"heard: {', '.join(hearer_ids(world, entity_id, intent.channel))}"
        else:
            detail = intent.channel
        events.acted(entity_id, "say", True, detail)
