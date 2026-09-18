"""Tests for conversations (docs/09_conversation_and_reflex.md, section 2)."""

import json
from pathlib import Path

import pytest

from world import world_pb2 as pb
from world.combat import kill_entity
from world.conversations import (
    PARTICIPANTS_KEY,
    PASSES_KEY,
    SPEAKER_KEY,
    TRANSCRIPT_KEY,
    UTTERANCES_KEY,
    all_conversations,
    conversation_of,
    read_participants,
    read_transcript,
)
from world.events import TickEvents
from world.items import (
    CONVERSATION,
    CONVERSATION_CHANNEL,
    CONVERSATION_LONELY_TICKS,
    CONVERSATION_MAX_PARTICIPANTS,
    CONVERSATION_MAX_UTTERANCES,
    CONVERSATION_TEXT_LIMIT,
    CONVERSATION_TRANSCRIPT_KEPT,
    CONVERSATION_TURN_TICKS,
    WOOD_WALL,
)
from world.lease import LeaseManager
from world.recording import RunRecorder
from world.replay.loader import RunLoader
from world.replay.session import ReplaySession
from world.services.observation_service import ObservationServiceServicer
from world.state import Entity, Inventory, Tile, World, WorldObject
from world.tick import (
    TickConfig,
    TickContext,
    TickLoop,
    TickResult,
    process_tick,
)
from world.types import (
    CONVERSE_JOIN,
    CONVERSE_LEAVE,
    CONVERSE_OPEN,
    CONVERSE_PASS,
    CONVERSE_SPEAK,
    ConverseIntent,
    Direction,
    EntityIntent,
    MoveIntent,
    PlaceIntent,
    Position,
)
from world.viewer_payload import utterance_payload

ANCHOR = Position(x=5, y=5)

# Everyone starts on a tile adjacent to the anchor.
SEATS = {
    "ada": Position(x=4, y=5),
    "bram": Position(x=6, y=5),
    "cleo": Position(x=5, y=4),
    "dora": Position(x=5, y=6),
    "evan": Position(x=4, y=4),
}


@pytest.fixture
def world() -> World:
    """20x20 world with five settlers around the anchor tile (5, 5)."""
    world = World(width=20, height=20)
    for entity_id, position in SEATS.items():
        world.add_entity(
            Entity(entity_id=entity_id, position=position, entity_type="player")
        )
    return world


def run_tick(world: World, *intents: EntityIntent) -> TickResult:
    """Run one full tick with the given intents and advance the world."""
    ctx = TickContext(
        tick_id=world.tick,
        start_time_ms=0,
        deadline_ms=2**60,
        world=world,
    )
    for intent in intents:
        accepted, reason = ctx.submit_intent(intent.entity_id, intent)
        assert accepted, reason
    result = process_tick(world, ctx)
    world.advance_tick()
    return result


def open_intent(entity_id: str, direction: Direction, text: str) -> ConverseIntent:
    return ConverseIntent(
        entity_id=entity_id, action=CONVERSE_OPEN, direction=direction, text=text
    )


def join_intent(entity_id: str, conversation_id: str) -> ConverseIntent:
    return ConverseIntent(
        entity_id=entity_id, action=CONVERSE_JOIN, conversation_id=conversation_id
    )


def speak_intent(entity_id: str, text: str) -> ConverseIntent:
    return ConverseIntent(entity_id=entity_id, action=CONVERSE_SPEAK, text=text)


def pass_intent(entity_id: str) -> ConverseIntent:
    return ConverseIntent(entity_id=entity_id, action=CONVERSE_PASS)


def leave_intent(entity_id: str) -> ConverseIntent:
    return ConverseIntent(entity_id=entity_id, action=CONVERSE_LEAVE)


def failures(result: TickResult, entity_id: str) -> list[str]:
    """Failure reasons this entity's converse actions produced."""
    return [
        action.details
        for action in result.action_results
        if action.entity_id == entity_id
        and action.action_type == "converse"
        and not action.success
    ]


def open_and_join(world: World, joiners: tuple[str, ...] = ("bram",)) -> WorldObject:
    """Open a conversation as ada and have `joiners` join it the next tick."""
    run_tick(world, open_intent("ada", Direction.EAST, "anyone got clay?"))
    conversation = all_conversations(world)[0]
    run_tick(
        world, *[join_intent(joiner, conversation.object_id) for joiner in joiners]
    )
    return world.get_object(conversation.object_id)


def idle_ticks(world: World, count: int) -> None:
    """Run `count` ticks with nobody doing anything."""
    for _ in range(count):
        run_tick(world)


class TestOpen:
    def test_open_creates_a_conversation_object(self, world: World) -> None:
        result = run_tick(world, open_intent("ada", Direction.EAST, "hello there"))

        conversations = all_conversations(world)
        assert len(conversations) == 1
        conversation = conversations[0]
        assert conversation.object_type == CONVERSATION
        assert conversation.object_id.startswith("conv_")
        assert conversation.position == ANCHOR
        assert read_participants(conversation) == ["ada"]
        assert conversation.get_state(SPEAKER_KEY) == ""
        assert conversation.get_state("opened_by") == "ada"
        assert read_transcript(conversation) == [
            {"tick": 0, "speaker": "ada", "text": "hello there"}
        ]
        assert [added.obj.object_id for added in result.objects_added] == [
            conversation.object_id
        ]

    def test_open_details_name_the_conversation(self, world: World) -> None:
        result = run_tick(world, open_intent("ada", Direction.EAST, "hello"))
        conversation_id = all_conversations(world)[0].object_id
        details = [
            action.details
            for action in result.action_results
            if action.entity_id == "ada" and action.success
        ]
        assert details == [f"open {conversation_id}"]

    def test_opening_line_is_a_local_utterance_with_the_id(self, world: World) -> None:
        result = run_tick(world, open_intent("ada", Direction.EAST, "hello"))
        conversation_id = all_conversations(world)[0].object_id

        assert len(result.utterances) == 1
        utterance = result.utterances[0]
        assert utterance.channel == "local"
        assert utterance.conversation_id == conversation_id
        assert utterance.position == SEATS["ada"]

    def test_conversation_does_not_block_movement(self, world: World) -> None:
        run_tick(world, open_intent("ada", Direction.EAST, "hello"))
        assert not world.is_blocked(ANCHOR)
        assert world.is_passable(ANCHOR)

    def test_open_needs_text(self, world: World) -> None:
        result = run_tick(world, open_intent("ada", Direction.EAST, "   "))
        assert failures(result, "ada") == ["opening line is required"]
        assert all_conversations(world) == []

    def test_open_needs_a_direction(self, world: World) -> None:
        result = run_tick(
            world, ConverseIntent(entity_id="ada", action=CONVERSE_OPEN, text="hi")
        )
        assert failures(result, "ada") == ["open needs a direction"]

    def test_open_refuses_an_occupied_anchor(self, world: World) -> None:
        result = run_tick(world, open_intent("ada", Direction.NORTH, "hi"))
        assert failures(result, "ada") == ["anchor is occupied by an entity"]

    def test_open_refuses_unwalkable_anchor(self, world: World) -> None:
        world.set_tile(Tile(position=ANCHOR, walkable=False, opaque=False))
        result = run_tick(world, open_intent("ada", Direction.EAST, "hi"))
        assert failures(result, "ada") == ["anchor is not walkable"]

    def test_open_refuses_a_blocking_object(self, world: World) -> None:
        world.add_object(
            WorldObject(object_id="wall_1", position=ANCHOR, object_type=WOOD_WALL)
        )
        result = run_tick(world, open_intent("ada", Direction.EAST, "hi"))
        assert failures(result, "ada") == ["anchor holds a blocking object"]

    def test_open_refuses_a_second_conversation(self, world: World) -> None:
        open_and_join(world)
        result = run_tick(world, open_intent("cleo", Direction.SOUTH, "hi"))
        assert failures(result, "cleo") == ["anchor already holds a conversation"]

    def test_open_refuses_a_participant(self, world: World) -> None:
        open_and_join(world)
        result = run_tick(world, open_intent("ada", Direction.NORTH, "hi"))
        assert failures(result, "ada") == ["already in a conversation"]

    def test_two_opens_on_one_anchor_resolve_lexicographically(
        self, world: World
    ) -> None:
        result = run_tick(
            world,
            open_intent("ada", Direction.EAST, "mine"),
            open_intent("bram", Direction.WEST, "mine too"),
        )
        assert len(all_conversations(world)) == 1
        assert read_participants(all_conversations(world)[0]) == ["ada"]
        assert failures(result, "bram") == ["anchor already holds a conversation"]


class TestJoin:
    def test_join_seats_the_entity_and_starts_the_turns(self, world: World) -> None:
        conversation = open_and_join(world)
        assert read_participants(conversation) == ["ada", "bram"]
        # The opener takes the first turn once a second participant arrives.
        assert conversation.get_state(SPEAKER_KEY) == "ada"

    def test_join_requires_adjacency(self, world: World) -> None:
        world.update_entity_position("evan", Position(x=1, y=1))
        run_tick(world, open_intent("ada", Direction.EAST, "hi"))
        conversation_id = all_conversations(world)[0].object_id
        result = run_tick(world, join_intent("evan", conversation_id))
        assert failures(result, "evan") == ["not adjacent to the conversation"]

    def test_join_requires_an_existing_conversation(self, world: World) -> None:
        result = run_tick(world, join_intent("bram", "conv_404"))
        assert failures(result, "bram") == ["no conversation conv_404"]

    def test_join_refuses_a_non_conversation_object(self, world: World) -> None:
        world.add_object(
            WorldObject(object_id="chest_1", position=ANCHOR, object_type="chest")
        )
        result = run_tick(world, join_intent("bram", "chest_1"))
        assert failures(result, "bram") == ["chest_1 is not a conversation"]

    def test_join_refuses_an_existing_participant(self, world: World) -> None:
        conversation = open_and_join(world)
        result = run_tick(world, join_intent("bram", conversation.object_id))
        assert failures(result, "bram") == ["already in a conversation"]

    def test_seat_cap_rejects_the_last_joiner(self, world: World) -> None:
        run_tick(world, open_intent("ada", Direction.EAST, "hi"))
        conversation_id = all_conversations(world)[0].object_id
        result = run_tick(
            world,
            *[
                join_intent(entity_id, conversation_id)
                for entity_id in ("bram", "cleo", "dora", "evan")
            ],
        )
        conversation = world.get_object(conversation_id)
        participants = read_participants(conversation)
        assert len(participants) == CONVERSATION_MAX_PARTICIPANTS
        assert participants == ["ada", "bram", "cleo", "dora"]
        assert failures(result, "evan") == ["conversation is full"]


class TestSpeaking:
    def test_speak_advances_the_turn_and_emits_an_utterance(self, world: World) -> None:
        conversation = open_and_join(world)
        result = run_tick(world, speak_intent("ada", "we need clay"))

        utterance = result.utterances[0]
        assert utterance.channel == CONVERSATION_CHANNEL
        assert utterance.conversation_id == conversation.object_id
        assert utterance.text == "we need clay"

        updated = world.get_object(conversation.object_id)
        assert updated.get_state(SPEAKER_KEY) == "bram"
        assert updated.get_state(UTTERANCES_KEY) == "1"
        assert read_transcript(updated)[-1]["text"] == "we need clay"

    def test_turn_order_is_round_robin_in_join_order(self, world: World) -> None:
        conversation = open_and_join(world, ("bram", "cleo"))
        assert read_participants(conversation) == ["ada", "bram", "cleo"]

        order = []
        for _ in range(4):
            current = world.get_object(conversation.object_id)
            speaker = current.get_state(SPEAKER_KEY)
            order.append(speaker)
            run_tick(world, speak_intent(speaker, "a word"))
        assert order == ["ada", "bram", "cleo", "ada"]

    def test_speaking_out_of_turn_is_refused(self, world: World) -> None:
        open_and_join(world)
        result = run_tick(world, speak_intent("bram", "me first"))
        assert failures(result, "bram") == ["not your turn"]
        assert result.utterances == []

    def test_speaking_outside_a_conversation_is_refused(self, world: World) -> None:
        result = run_tick(world, speak_intent("ada", "hello?"))
        assert failures(result, "ada") == ["not in a conversation"]

    def test_empty_speech_is_refused(self, world: World) -> None:
        open_and_join(world)
        result = run_tick(world, speak_intent("ada", "  "))
        assert failures(result, "ada") == ["text is required"]

    def test_long_lines_are_truncated(self, world: World) -> None:
        conversation = open_and_join(world)
        run_tick(world, speak_intent("ada", "x" * (CONVERSATION_TEXT_LIMIT + 50)))
        line = read_transcript(world.get_object(conversation.object_id))[-1]
        assert len(line["text"]) == CONVERSATION_TEXT_LIMIT

    def test_transcript_keeps_the_last_twelve_lines(self, world: World) -> None:
        conversation = open_and_join(world)
        for index in range(20):
            speaker = world.get_object(conversation.object_id).get_state(SPEAKER_KEY)
            run_tick(world, speak_intent(speaker, f"line {index}"))

        transcript = read_transcript(world.get_object(conversation.object_id))
        assert len(transcript) == CONVERSATION_TRANSCRIPT_KEPT
        assert transcript[-1]["text"] == "line 19"
        assert transcript[0]["text"] == f"line {20 - CONVERSATION_TRANSCRIPT_KEPT}"

    def test_utterance_cap_closes_the_conversation(self, world: World) -> None:
        conversation = open_and_join(world)
        for index in range(CONVERSATION_MAX_UTTERANCES):
            current = world.all_objects().get(conversation.object_id)
            assert current is not None, f"closed early after {index} lines"
            run_tick(world, speak_intent(current.get_state(SPEAKER_KEY), "talk"))

        assert all_conversations(world) == []


class TestPassingAndLeaving:
    def test_a_full_round_of_passes_closes_the_conversation(self, world: World) -> None:
        conversation = open_and_join(world)
        run_tick(world, pass_intent("ada"))
        assert world.get_object(conversation.object_id).get_state(PASSES_KEY) == "1"
        run_tick(world, pass_intent("bram"))
        assert all_conversations(world) == []

    def test_a_speak_resets_the_pass_streak(self, world: World) -> None:
        conversation = open_and_join(world)
        run_tick(world, pass_intent("ada"))
        run_tick(world, speak_intent("bram", "still here"))
        updated = world.get_object(conversation.object_id)
        assert updated.get_state(PASSES_KEY) == "0"

    def test_passing_out_of_turn_is_refused(self, world: World) -> None:
        open_and_join(world)
        result = run_tick(world, pass_intent("bram"))
        assert failures(result, "bram") == ["not your turn"]

    def test_leave_frees_the_seat_and_closes_a_pair(self, world: World) -> None:
        conversation = open_and_join(world, ("bram", "cleo"))
        run_tick(world, leave_intent("cleo"))
        updated = world.get_object(conversation.object_id)
        assert read_participants(updated) == ["ada", "bram"]

        run_tick(world, leave_intent("bram"))
        assert all_conversations(world) == []

    def test_leaving_speaker_hands_the_turn_on(self, world: World) -> None:
        conversation = open_and_join(world, ("bram", "cleo"))
        run_tick(world, leave_intent("ada"))
        updated = world.get_object(conversation.object_id)
        assert updated.get_state(SPEAKER_KEY) == "bram"

    def test_leaving_outside_a_conversation_is_refused(self, world: World) -> None:
        result = run_tick(world, leave_intent("ada"))
        assert failures(result, "ada") == ["not in a conversation"]


class TestLifecycle:
    def test_walking_away_removes_the_participant(self, world: World) -> None:
        conversation = open_and_join(world, ("bram", "cleo"))
        run_tick(world, MoveIntent(entity_id="cleo", direction=Direction.NORTH))
        updated = world.get_object(conversation.object_id)
        assert read_participants(updated) == ["ada", "bram"]

    def test_death_removes_the_participant(self, world: World) -> None:
        conversation = open_and_join(world, ("bram", "cleo"))
        kill_entity(world, "cleo", "wolf_1", TickEvents())
        run_tick(world)
        updated = world.get_object(conversation.object_id)
        assert read_participants(updated) == ["ada", "bram"]

    def test_a_conversation_nobody_joins_closes(self, world: World) -> None:
        run_tick(world, open_intent("ada", Direction.EAST, "anyone?"))
        idle_ticks(world, CONVERSATION_LONELY_TICKS - 1)
        assert len(all_conversations(world)) == 1
        idle_ticks(world, 1)
        assert all_conversations(world) == []

    def test_an_unused_turn_counts_as_a_pass(self, world: World) -> None:
        conversation = open_and_join(world)
        idle_ticks(world, CONVERSATION_TURN_TICKS - 1)
        assert world.get_object(conversation.object_id).get_state(SPEAKER_KEY) == "ada"

        idle_ticks(world, 1)
        updated = world.get_object(conversation.object_id)
        assert updated.get_state(SPEAKER_KEY) == "bram"
        assert updated.get_state(PASSES_KEY) == "1"

    def test_two_timed_out_turns_close_the_conversation(self, world: World) -> None:
        open_and_join(world)
        idle_ticks(world, 2 * CONVERSATION_TURN_TICKS)
        assert all_conversations(world) == []

    def test_a_participant_who_dies_alone_closes_it(self, world: World) -> None:
        conversation = open_and_join(world)
        kill_entity(world, "bram", "wolf_1", TickEvents())
        run_tick(world)
        assert conversation.object_id not in world.all_objects()

    def test_closing_reports_the_removal(self, world: World) -> None:
        conversation = open_and_join(world)
        # bram's departure leaves ada alone, so it closes on the same tick.
        result = run_tick(world, leave_intent("bram"))
        assert [event.object_id for event in result.objects_removed] == [
            conversation.object_id
        ]
        assert conversation.object_id not in world.all_objects()


class TestPlacementAndVisibility:
    def test_a_structure_may_not_stand_on_the_anchor(self, world: World) -> None:
        world.set_entity(
            world.get_entity("ada").with_inventory(Inventory(items=((WOOD_WALL, 1),)))
        )
        open_and_join(world)
        result = run_tick(
            world,
            PlaceIntent(entity_id="ada", kind=WOOD_WALL, direction=Direction.EAST),
        )
        place_failures = [
            action.details
            for action in result.action_results
            if action.action_type == "place" and not action.success
        ]
        assert place_failures == ["target already holds an object"]
        assert world.get_entity("ada").inventory.count(WOOD_WALL) == 1

    def test_object_events_reach_observations(self, world: World) -> None:
        service = ObservationServiceServicer(world, TickLoop(world), LeaseManager())
        result = run_tick(world, open_intent("ada", Direction.EAST, "hello"))
        service.on_tick_complete(result)

        observation = service._generate_observation(
            "bram", TickContext(tick_id=1, start_time_ms=0, deadline_ms=2**60)
        )
        assert observation is not None
        added = [
            event.object_added
            for event in observation.events
            if event.WhichOneof("event") == "object_added"
        ]
        assert [obj.object.object_type for obj in added] == [CONVERSATION]
        assert any(
            obj.object_type == CONVERSATION for obj in observation.visible_objects
        )

    def test_utterance_conversation_id_reaches_observations(self, world: World) -> None:
        service = ObservationServiceServicer(world, TickLoop(world), LeaseManager())
        conversation = open_and_join(world)
        result = run_tick(world, speak_intent("ada", "we need clay"))
        service.on_tick_complete(result)

        observation = service._generate_observation(
            "cleo", TickContext(tick_id=9, start_time_ms=0, deadline_ms=2**60)
        )
        assert observation is not None
        utterances = [
            event.utterance
            for event in observation.events
            if event.WhichOneof("event") == "utterance"
        ]
        assert len(utterances) == 1
        assert utterances[0].channel == CONVERSATION_CHANNEL
        assert utterances[0].conversation_id == conversation.object_id
        assert isinstance(utterances[0], pb.Utterance)

    def test_viewer_payload_carries_the_conversation_id(self, world: World) -> None:
        result = run_tick(world, open_intent("ada", Direction.EAST, "hello"))
        payload = utterance_payload(result.utterances[0])
        assert payload["conversation_id"] == all_conversations(world)[0].object_id

    def test_object_state_is_json_the_viewer_can_read(self, world: World) -> None:
        conversation = open_and_join(world)
        state = dict(conversation.state)
        assert json.loads(state[PARTICIPANTS_KEY]) == ["ada", "bram"]
        assert isinstance(json.loads(state[TRANSCRIPT_KEY]), list)

    def test_state_changes_are_reported(self, world: World) -> None:
        run_tick(world, open_intent("ada", Direction.EAST, "hello"))
        conversation_id = all_conversations(world)[0].object_id
        result = run_tick(world, join_intent("bram", conversation_id))
        changed = {
            change.field
            for change in result.object_changes
            if change.object_id == conversation_id
        }
        assert {PARTICIPANTS_KEY, SPEAKER_KEY} <= changed


class TestRecordingAndReplay:
    def test_a_conversation_survives_a_record_replay_round_trip(
        self, world: World, tmp_path: Path
    ) -> None:
        recorder = RunRecorder(
            run_dir=tmp_path / "20260917-120000-test",
            run_id="20260917-120000-test",
            config_name="test",
            config_path="world/configs/default.toml",
            world=world,
            tick_config=TickConfig(tick_duration_ms=100, intent_deadline_ms=50),
        )
        recorder.start()
        recorder.record_tick(
            run_tick(world, open_intent("ada", Direction.EAST, "anyone got clay?"))
        )
        conversation_id = all_conversations(world)[0].object_id
        recorder.record_tick(run_tick(world, join_intent("bram", conversation_id)))
        recorder.record_tick(run_tick(world, speak_intent("ada", "we need clay")))
        recorder.record_tick(run_tick(world, leave_intent("bram")))
        recorder.close()

        session = ReplaySession(
            RunLoader(tmp_path / "20260917-120000-test"), tmp_path / "cache"
        )
        session.seek(2)
        replayed = session.world.get_object(conversation_id)
        assert replayed.object_type == CONVERSATION
        assert read_participants(replayed) == ["ada", "bram"]
        assert replayed.get_state(SPEAKER_KEY) == "bram"

        # The last tick closes it, so replaying past it drops the object.
        session.seek(3)
        assert conversation_id not in session.world.all_objects()


class TestLookups:
    def test_conversation_of_finds_the_seat(self, world: World) -> None:
        conversation = open_and_join(world)
        found = conversation_of(world, "bram")
        assert found is not None
        assert found.object_id == conversation.object_id

    def test_conversation_of_returns_none_for_a_bystander(self, world: World) -> None:
        open_and_join(world)
        assert conversation_of(world, "cleo") is None
