"""Tests for observation radius filtering and event replay."""

import pytest

from world import world_pb2 as pb
from world.events import (
    ActionResult,
    DamageEvent,
    DeathEvent,
    EntitySpawnedEvent,
    ObjectAddedEvent,
    ObjectRemovedEvent,
    RespawnEvent,
    UtteranceEvent,
)
from world.foraging import ObjectChange
from world.lease import LeaseManager
from world.movement import MoveResult
from world.services.observation_service import (
    VIEW_RADIUS,
    ObservationServiceServicer,
)
from world.state import Entity, World, WorldObject
from world.tick import TickContext, TickLoop, TickResult
from world.types import Position


@pytest.fixture
def world() -> World:
    world = World(width=60, height=60)
    world.add_entity(
        Entity(entity_id="ada", position=Position(x=30, y=30), entity_type="player")
    )
    return world


@pytest.fixture
def service(world: World) -> ObservationServiceServicer:
    return ObservationServiceServicer(world, TickLoop(world), LeaseManager())


def _context(tick_id: int = 7) -> TickContext:
    return TickContext(tick_id=tick_id, start_time_ms=0, deadline_ms=1000)


def _observe(
    service: ObservationServiceServicer, entity_id: str = "ada"
) -> pb.Observation:
    observation = service._generate_observation(entity_id, _context())
    assert observation is not None
    return observation


def _event_kinds(observation: pb.Observation) -> list[str]:
    return [event.WhichOneof("event") for event in observation.events]


class TestVisibility:
    def test_view_radius_is_eight(self, service: ObservationServiceServicer) -> None:
        assert VIEW_RADIUS == 8

    def test_tiles_cover_the_radius(self, service: ObservationServiceServicer) -> None:
        observation = _observe(service)
        assert len(observation.visible_tiles) == 17 * 17

    def test_only_entities_within_radius_are_visible(
        self, world: World, service: ObservationServiceServicer
    ) -> None:
        world.add_entity(Entity(entity_id="near", position=Position(x=36, y=30)))
        world.add_entity(Entity(entity_id="far", position=Position(x=45, y=30)))

        observation = _observe(service)

        ids = {e.entity_id for e in observation.visible_entities}
        assert ids == {"near"}

    def test_self_carries_stat_fields(
        self, world: World, service: ObservationServiceServicer
    ) -> None:
        world._entities["ada"] = world.get_entity("ada").model_copy(
            update={"health": 12, "hunger": 44, "wielded": "axe", "alive": True}
        )

        observation = _observe(service)

        assert observation.self.health == 12
        assert observation.self.max_health == 20
        assert observation.self.hunger == 44
        assert observation.self.max_hunger == 100
        assert observation.self.wielded == "axe"
        assert observation.self.alive is True

    def test_dead_entities_still_observe(
        self, world: World, service: ObservationServiceServicer
    ) -> None:
        world._entities["ada"] = world.get_entity("ada").model_copy(
            update={"alive": False, "health": 0}
        )

        observation = _observe(service)

        assert observation.self.alive is False

    def test_only_objects_within_radius_are_visible(
        self, world: World, service: ObservationServiceServicer
    ) -> None:
        world.add_object(
            WorldObject(
                object_id="near_bush",
                position=Position(x=34, y=34),
                object_type="bush",
            )
        )
        world.add_object(
            WorldObject(
                object_id="far_bush",
                position=Position(x=50, y=50),
                object_type="bush",
            )
        )

        observation = _observe(service)

        ids = {o.object_id for o in observation.visible_objects}
        assert ids == {"near_bush"}


class TestEventReplay:
    def test_no_events_before_the_first_tick(
        self, service: ObservationServiceServicer
    ) -> None:
        assert _event_kinds(_observe(service)) == []

    def test_moves_are_filtered_by_radius(
        self, world: World, service: ObservationServiceServicer
    ) -> None:
        service.on_tick_complete(
            TickResult(
                tick_id=6,
                move_results=[
                    MoveResult(
                        entity_id="near",
                        success=True,
                        from_pos=Position(x=35, y=30),
                        to_pos=Position(x=36, y=30),
                    ),
                    MoveResult(
                        entity_id="far",
                        success=True,
                        from_pos=Position(x=50, y=50),
                        to_pos=Position(x=51, y=50),
                    ),
                ],
            )
        )

        observation = _observe(service)

        moved = [e.entity_moved.entity_id for e in observation.events]
        assert moved == ["near"]

    def test_own_action_is_always_included(
        self, world: World, service: ObservationServiceServicer
    ) -> None:
        # The other actor is far away and has no entity in the world at all.
        service.on_tick_complete(
            TickResult(
                tick_id=6,
                move_results=[],
                action_results=[
                    ActionResult("ada", "craft", True, "made an axe"),
                    ActionResult("ghost", "craft", True, "made an axe"),
                ],
            )
        )

        observation = _observe(service)

        acted = [e.entity_acted.entity_id for e in observation.events]
        assert acted == ["ada"]

    def test_actions_of_visible_entities_are_included(
        self, world: World, service: ObservationServiceServicer
    ) -> None:
        world.add_entity(Entity(entity_id="near", position=Position(x=33, y=30)))
        service.on_tick_complete(
            TickResult(
                tick_id=6,
                move_results=[],
                action_results=[ActionResult("near", "eat", True, "ate a berry")],
            )
        )

        assert _event_kinds(_observe(service)) == ["entity_acted"]

    def test_local_utterances_within_ten_tiles(
        self, service: ObservationServiceServicer
    ) -> None:
        service.on_tick_complete(
            TickResult(
                tick_id=6,
                move_results=[],
                utterances=[
                    UtteranceEvent("bram", "local", "hello", Position(x=39, y=30)),
                    UtteranceEvent("cleo", "local", "far", Position(x=42, y=30)),
                ],
            )
        )

        observation = _observe(service)

        speakers = [e.utterance.speaker_id for e in observation.events]
        assert speakers == ["bram"]

    def test_shouts_carry_sixty_tiles_and_keep_their_channel(
        self, service: ObservationServiceServicer
    ) -> None:
        service.on_tick_complete(
            TickResult(
                tick_id=6,
                move_results=[],
                utterances=[
                    UtteranceEvent("bram", "shout", "wolf!", Position(x=90, y=30)),
                    UtteranceEvent("cleo", "shout", "too far", Position(x=91, y=30)),
                ],
            )
        )

        observation = _observe(service)

        heard = [
            (e.utterance.speaker_id, e.utterance.channel) for e in observation.events
        ]
        assert heard == [("bram", "shout")]
        assert observation.events[0].utterance.position.x == 90

    def test_thought_utterances_are_never_observed(
        self, service: ObservationServiceServicer
    ) -> None:
        service.on_tick_complete(
            TickResult(
                tick_id=6,
                move_results=[],
                utterances=[
                    UtteranceEvent("ada", "thought", "hmm", Position(x=30, y=30))
                ],
            )
        )

        assert _event_kinds(_observe(service)) == []

    def test_damage_death_and_respawn_within_radius(
        self, service: ObservationServiceServicer
    ) -> None:
        service.on_tick_complete(
            TickResult(
                tick_id=6,
                move_results=[],
                damage_events=[
                    DamageEvent("bram", "wolf_1", 3, 7, Position(x=34, y=30)),
                    DamageEvent("cleo", "wolf_2", 3, 7, Position(x=55, y=55)),
                ],
                deaths=[DeathEvent("bram", "wolf_1", Position(x=34, y=30))],
                respawns=[RespawnEvent("bram", Position(x=31, y=31))],
            )
        )

        observation = _observe(service)

        assert _event_kinds(observation) == [
            "entity_damaged",
            "entity_died",
            "entity_respawned",
        ]
        assert observation.events[0].entity_damaged.entity_id == "bram"

    def test_object_added_removed_and_changed(
        self, world: World, service: ObservationServiceServicer
    ) -> None:
        near_bush = WorldObject(
            object_id="bush_1", position=Position(x=32, y=30), object_type="bush"
        )
        world.add_object(near_bush)
        service.on_tick_complete(
            TickResult(
                tick_id=6,
                move_results=[],
                objects_added=[
                    ObjectAddedEvent(
                        WorldObject(
                            object_id="chest_1",
                            position=Position(x=31, y=30),
                            object_type="chest",
                        )
                    ),
                    ObjectAddedEvent(
                        WorldObject(
                            object_id="chest_far",
                            position=Position(x=55, y=55),
                            object_type="chest",
                        )
                    ),
                ],
                objects_removed=[
                    ObjectRemovedEvent("tree_9", Position(x=30, y=25)),
                    ObjectRemovedEvent("tree_far", Position(x=5, y=5)),
                ],
                object_changes=[
                    ObjectChange("bush_1", "berry_count", "1", "0"),
                    ObjectChange("unknown_object", "berry_count", "1", "0"),
                ],
            )
        )

        observation = _observe(service)

        assert _event_kinds(observation) == [
            "object_added",
            "object_removed",
            "object_changed",
        ]
        assert observation.events[0].object_added.object.object_id == "chest_1"
        assert observation.events[1].object_removed.object_id == "tree_9"
        assert observation.events[2].object_changed.object_id == "bush_1"

    def test_events_do_not_leak_into_the_following_tick(
        self, service: ObservationServiceServicer
    ) -> None:
        service.on_tick_complete(
            TickResult(
                tick_id=6,
                move_results=[],
                entities_spawned=[
                    EntitySpawnedEvent("wolf_1", Position(x=31, y=30), "wolf")
                ],
                action_results=[ActionResult("ada", "wait", True, "")],
            )
        )
        assert _event_kinds(_observe(service)) == ["entity_acted"]

        service.on_tick_complete(TickResult(tick_id=7, move_results=[]))
        assert _event_kinds(_observe(service)) == []
