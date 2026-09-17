"""ObservationService gRPC implementation."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Iterator, Mapping

import grpc
import structlog

from .. import world_pb2 as pb
from .. import world_pb2_grpc
from ..conversion import entity_to_proto, object_to_proto, tile_to_proto
from ..events import ActionResult
from ..lease import LeaseManager
from ..state import Entity, World
from ..tick import TickContext, TickLoop, TickResult
from ..items import CONVERSATION_CHANNEL
from ..types import AUDIBLE_CHANNELS, LOCAL_CHANNEL, SHOUT_CHANNEL, Position

logger = structlog.get_logger()

# View radius in tiles (Chebyshev) for tiles, objects and entities.
VIEW_RADIUS = 8
# Earshot for `local` utterances.
HEARING_RADIUS = 10
# Earshot for `shout` utterances: far enough to call the settlement to a fight,
# which is the point of shouting.
SHOUT_RADIUS = 60
HEARING_RADIUS_BY_CHANNEL: Mapping[str, int] = {
    LOCAL_CHANNEL: HEARING_RADIUS,
    SHOUT_CHANNEL: SHOUT_RADIUS,
    # Conversation lines carry at local range, so bystanders can listen in.
    CONVERSATION_CHANNEL: HEARING_RADIUS,
}


def _within(a: Position, b: Position, radius: int) -> bool:
    """Chebyshev distance test."""
    return abs(a.x - b.x) <= radius and abs(a.y - b.y) <= radius


@dataclass
class ObservationSubscriber:
    """Tracks a subscriber waiting for observations."""

    entity_id: str
    lease_id: str
    queue: asyncio.Queue[pb.Observation] = field(default_factory=asyncio.Queue)


class ObservationServiceServicer(world_pb2_grpc.ObservationServiceServicer):
    """Implements the ObservationService for streaming observations."""

    def __init__(
        self,
        world: World,
        tick_loop: TickLoop,
        lease_manager: LeaseManager,
    ):
        self.world = world
        self.tick_loop = tick_loop
        self.lease_manager = lease_manager
        self._subscribers: dict[str, ObservationSubscriber] = {}
        # Events from the tick that just finished, replayed into the next
        # tick's observations.
        self._last_result: TickResult | None = None

    def on_tick_complete(self, result: TickResult) -> None:
        """Store the finished tick's events for the next observation round."""
        self._last_result = result

    def StreamObservations(
        self, request: pb.StreamObservationsRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.Observation]:
        """Stream observations for an entity.

        Yields an Observation at the start of each tick.
        """
        lease_id = request.lease_id
        entity_id = request.entity_id

        # Validate lease
        if not self.lease_manager.is_valid_lease(lease_id, entity_id):
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details("Invalid lease")
            return

        logger.info(
            "observation_stream_started",
            entity_id=entity_id,
            peer=context.peer(),
        )

        # Create subscriber
        subscriber = ObservationSubscriber(entity_id=entity_id, lease_id=lease_id)
        self._subscribers[entity_id] = subscriber

        try:
            while context.is_active():
                # Check lease is still valid
                if not self.lease_manager.is_valid_lease(lease_id, entity_id):
                    logger.info("observation_stream_lease_expired", entity_id=entity_id)
                    break

                # Wait for next observation
                try:
                    observation = subscriber.queue.get_nowait()
                    yield observation
                except asyncio.QueueEmpty:
                    time.sleep(0.01)
                    continue

        except Exception as e:
            logger.warning("observation_stream_error", error=str(e))
        finally:
            self._subscribers.pop(entity_id, None)
            logger.info("observation_stream_ended", entity_id=entity_id)

    def broadcast_observations(self, context: TickContext) -> None:
        """Broadcast observations to all subscribers at the start of a tick.

        Called by the server when a new tick starts, before the deadline.
        Agents should submit intents for context.tick_id.
        """
        for entity_id, subscriber in list(self._subscribers.items()):
            try:
                observation = self._generate_observation(entity_id, context)
                if observation:
                    subscriber.queue.put_nowait(observation)
            except Exception as e:
                logger.warning(
                    "observation_generation_failed",
                    entity_id=entity_id,
                    error=str(e),
                )

    def _generate_observation(
        self, entity_id: str, context: TickContext
    ) -> pb.Observation | None:
        """Generate an observation for an entity.

        Dead entities still get observations: they need to see `self.alive`
        is false and that they will respawn.
        """
        try:
            entity = self.world.get_entity(entity_id)
        except Exception:
            return None

        centre = entity.position

        self_proto = _entity_proto(entity)

        visible_entities = [
            _entity_proto(other)
            for other in self.world.all_entities().values()
            if other.entity_id != entity_id
            and _within(other.position, centre, VIEW_RADIUS)
        ]

        visible_tiles = self._get_nearby_tiles(centre, radius=VIEW_RADIUS)
        visible_objects = self._get_nearby_objects(centre, radius=VIEW_RADIUS)
        events = self._build_events(entity_id, centre)

        observation = pb.Observation(
            tick_id=context.tick_id,
            deadline_ms=context.deadline_ms,
            visible_entities=visible_entities,
            visible_tiles=visible_tiles,
            visible_objects=visible_objects,
            events=events,
        )
        # 'self' is a Python keyword, so the field is set via CopyFrom.
        observation.self.CopyFrom(self_proto)
        return observation

    def _build_events(
        self, entity_id: str, centre: Position
    ) -> list[pb.ObservationEvent]:
        """Build the previous tick's events this entity could perceive."""
        result = self._last_result
        if result is None:
            return []

        events: list[pb.ObservationEvent] = []

        for move in result.move_results:
            if not move.success:
                continue
            visible = (
                move.entity_id == entity_id
                or _within(move.to_pos, centre, VIEW_RADIUS)
                or _within(move.from_pos, centre, VIEW_RADIUS)
            )
            if visible:
                moved = pb.EntityMoved(
                    entity_id=move.entity_id,
                    to=pb.Position(x=move.to_pos.x, y=move.to_pos.y),
                )
                # `from` is a Python keyword, so it cannot be a kwarg.
                getattr(moved, "from").CopyFrom(
                    pb.Position(x=move.from_pos.x, y=move.from_pos.y)
                )
                events.append(pb.ObservationEvent(entity_moved=moved))

        for action in result.action_results:
            if action.entity_id == entity_id or self._actor_in_view(action, centre):
                events.append(
                    pb.ObservationEvent(
                        entity_acted=pb.EntityActed(
                            entity_id=action.entity_id,
                            action_type=action.action_type,
                            success=action.success,
                            details=action.details,
                        )
                    )
                )

        for utterance in result.utterances:
            # `thought` never reaches another agent's observation.
            if utterance.channel not in AUDIBLE_CHANNELS:
                continue
            earshot = HEARING_RADIUS_BY_CHANNEL[utterance.channel]
            if not _within(utterance.position, centre, earshot):
                continue
            events.append(
                pb.ObservationEvent(
                    utterance=pb.Utterance(
                        speaker_id=utterance.speaker_id,
                        channel=utterance.channel,
                        text=utterance.text,
                        position=pb.Position(
                            x=utterance.position.x, y=utterance.position.y
                        ),
                        conversation_id=utterance.conversation_id,
                    )
                )
            )

        for damage in result.damage_events:
            if damage.entity_id == entity_id or _within(
                damage.position, centre, VIEW_RADIUS
            ):
                events.append(
                    pb.ObservationEvent(
                        entity_damaged=pb.EntityDamaged(
                            entity_id=damage.entity_id,
                            attacker_id=damage.attacker_id,
                            amount=damage.amount,
                            remaining_health=damage.remaining_health,
                        )
                    )
                )

        for death in result.deaths:
            if death.entity_id == entity_id or _within(
                death.position, centre, VIEW_RADIUS
            ):
                events.append(
                    pb.ObservationEvent(
                        entity_died=pb.EntityDied(
                            entity_id=death.entity_id,
                            killer_id=death.killer_id,
                            position=pb.Position(
                                x=death.position.x, y=death.position.y
                            ),
                        )
                    )
                )

        for respawn in result.respawns:
            if respawn.entity_id == entity_id or _within(
                respawn.position, centre, VIEW_RADIUS
            ):
                events.append(
                    pb.ObservationEvent(
                        entity_respawned=pb.EntityRespawned(
                            entity_id=respawn.entity_id,
                            position=pb.Position(
                                x=respawn.position.x, y=respawn.position.y
                            ),
                        )
                    )
                )

        for added in result.objects_added:
            if _within(added.obj.position, centre, VIEW_RADIUS):
                events.append(
                    pb.ObservationEvent(
                        object_added=pb.ObjectAdded(object=object_to_proto(added.obj))
                    )
                )

        for removed in result.objects_removed:
            if _within(removed.position, centre, VIEW_RADIUS):
                events.append(
                    pb.ObservationEvent(
                        object_removed=pb.ObjectRemoved(
                            object_id=removed.object_id,
                            position=pb.Position(
                                x=removed.position.x, y=removed.position.y
                            ),
                        )
                    )
                )

        for change in result.object_changes:
            if not self._object_in_view(change.object_id, centre):
                continue
            events.append(
                pb.ObservationEvent(
                    object_changed=pb.ObjectChanged(
                        object_id=change.object_id,
                        field=change.field,
                        old_value=change.old_value,
                        new_value=change.new_value,
                    )
                )
            )

        return events

    def _actor_in_view(self, action: ActionResult, centre: Position) -> bool:
        """Whether the acting entity is currently within view of centre."""
        try:
            actor = self.world.get_entity(action.entity_id)
        except Exception:
            return False
        return _within(actor.position, centre, VIEW_RADIUS)

    def _object_in_view(self, object_id: str, centre: Position) -> bool:
        """Whether an object is still present and within view of centre."""
        try:
            obj = self.world.get_object(object_id)
        except Exception:
            return False
        return _within(obj.position, centre, VIEW_RADIUS)

    def _get_nearby_objects(
        self, center: Position, radius: int = VIEW_RADIUS
    ) -> list[pb.WorldObject]:
        """Get objects within a radius of the center position."""
        objects = []
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                pos = Position(x=center.x + dx, y=center.y + dy)
                for obj in self.world.get_objects_at(pos):
                    objects.append(object_to_proto(obj))
        return objects

    def _get_nearby_tiles(
        self, center: Position, radius: int = VIEW_RADIUS
    ) -> list[pb.Tile]:
        """Get tiles within a radius of the center position.

        A wall makes its tile `walkable = false` so agent path finding needs no
        new concept (docs/08_building.md, "Blocking"). Doors stay walkable:
        they only stop wolves, which have no observation stream.
        """
        tiles = []
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                pos = Position(x=center.x + dx, y=center.y + dy)
                if not self.world.in_bounds(pos):
                    continue
                tile = self.world.get_tile(pos)
                if tile.walkable and self.world.is_blocked(pos):
                    tile = tile.model_copy(update={"walkable": False})
                tiles.append(tile_to_proto(tile))
        return tiles


def _entity_proto(entity: Entity) -> pb.Entity:
    """Convert an entity, making sure the stat fields are populated.

    `conversion.entity_to_proto` is owned by the mechanics track; until it maps
    the new stat fields this fills them in so observations always carry them.
    """
    proto = entity_to_proto(entity)
    proto.health = entity.health
    proto.max_health = entity.max_health
    proto.hunger = entity.hunger
    proto.max_hunger = entity.max_hunger
    proto.wielded = entity.wielded
    proto.alive = entity.alive
    return proto
