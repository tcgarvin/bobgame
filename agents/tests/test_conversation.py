"""Conversation mode: parsing, turns, reports and the note kept afterwards."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from typing import Mapping, Sequence

from agents import world_pb2 as pb
from agents.jev_agent.journal import Journal
from agents.jev_agent.conversation import (
    ACTION_EAT,
    ACTION_GIVE,
    ACTION_LEAVE,
    ACTION_PASS,
    ACTION_SPEAK,
    END_CLOSED,
    END_NOBODY_JOINED,
    END_REMOVED,
    NOTE_STALE,
    OBJECT_GRACE_TICKS,
    OUTCOME_NO_KIND,
    OUTCOME_NO_TARGET,
    ApproachDriver,
    ConversationSession,
    Converser,
    ConverserMove,
    MoveCall,
    NoteCall,
    free_seat_tiles,
    joined_conversation,
    joined_conversation_id,
    merge_transcript,
    transcript_for,
)
from agents.jev_agent.options import enumerate_options
from agents.jev_agent.tracelog import AgentTrace
from agents.jev_agent.worldmodel import TranscriptLine, WorldModel

from helpers import (
    FakeConverser,
    acted_event,
    conversation_utterance_event,
    converse_object,
    make_entity,
    make_observation,
    make_object,
)

ANCHOR = (11, 10)


def observe(
    model: WorldModel,
    tick: int,
    *,
    objects: Sequence[pb.WorldObject] = (),
    events: Sequence[pb.ObservationEvent] = (),
    position: tuple[int, int] = (10, 10),
    inventory: Mapping[str, int] | None = None,
) -> object:
    """Feed the model one observation for `ada` and return the digest."""
    return model.update(
        make_observation(
            tick,
            make_entity("ada", position, inventory=inventory),
            objects=list(objects),
            events=list(events),
        )
    )


def session_for(
    model: WorldModel, converser: Converser, tmp_path: Path, purpose: str = ""
) -> ConversationSession:
    """A session on `conv_1` writing its trace and notes under `tmp_path`."""
    session = ConversationSession(
        "conv_1",
        model,
        converser,
        trace=AgentTrace("ada", tmp_path),
        memory_path=tmp_path / "memory.md",
        purpose=purpose,
    )
    session.begin()
    return session


def trace_rows(session: ConversationSession, tmp_path: Path) -> list[dict]:
    """Every line the session's trace wrote, oldest first."""
    session.trace.close()
    path = tmp_path / "agent-ada" / "conversations.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def trace_turns(session: ConversationSession, tmp_path: Path) -> list[dict]:
    """Every `turn` line the session wrote, oldest first."""
    return [row for row in trace_rows(session, tmp_path) if row["event"] == "turn"]


def trace_events(
    session: ConversationSession, tmp_path: Path, event: str
) -> list[dict]:
    """Every line of `event` the session's trace wrote, oldest first."""
    return [row for row in trace_rows(session, tmp_path) if row["event"] == event]


class FailingConverser:
    """A converser whose model call always raises, so no usage is recorded."""

    async def move(self, prompt: str) -> MoveCall:
        """Fail the call."""
        raise RuntimeError("no model")

    async def note(self, prompt: str) -> NoteCall:
        """Fail the call."""
        raise RuntimeError("no model")


class SlowConverser:
    """A converser whose call takes `delay` seconds, or waits for `release`."""

    def __init__(self, move: ConverserMove, delay: float = 0.0) -> None:
        self.move_to_return = move
        self.delay = delay
        self.release = asyncio.Event()
        self.prompts: list[str] = []

    async def move(self, prompt: str) -> MoveCall:
        """Answer after the delay, or when the test releases the call."""
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        else:
            await self.release.wait()
        return MoveCall(self.move_to_return)

    async def note(self, prompt: str) -> NoteCall:
        """No note: this converser only exists for its move timing."""
        return NoteCall("")


# -- world model -------------------------------------------------------------


def test_a_conversation_object_is_parsed_out_of_its_state() -> None:
    model = WorldModel("ada")
    observe(
        model,
        1,
        objects=[
            converse_object(
                "conv_1",
                ANCHOR,
                ["mira", "ada"],
                speaker="mira",
                turn_started=7,
                utterances=2,
                transcript=[{"tick": 3, "speaker": "mira", "text": "hello"}],
            )
        ],
    )
    conversation = model.conversation_by_id("conv_1")
    assert conversation is not None
    assert conversation.anchor == ANCHOR
    assert conversation.participants == ("mira", "ada")
    assert conversation.speaker == "mira"
    assert conversation.turn_started == 7
    assert conversation.free_seats == 2
    assert conversation.transcript[0].text == "hello"
    assert (
        model.my_conversation() is conversation or model.my_conversation() is not None
    )


def test_my_conversation_is_empty_when_the_actor_holds_no_seat() -> None:
    model = WorldModel("ada")
    observe(model, 1, objects=[converse_object("conv_1", ANCHOR, ["mira"])])
    assert model.my_conversation() is None


def test_heard_lines_are_kept_per_conversation() -> None:
    model = WorldModel("ada")
    observe(
        model,
        1,
        objects=[converse_object("conv_1", ANCHOR, ["mira", "ada"])],
        events=[
            conversation_utterance_event("mira", "we need planks", ANCHOR, "conv_1"),
            conversation_utterance_event(
                "bo", "over here", (4, 4), "conv_2", channel="local"
            ),
        ],
    )
    lines = model.heard_conversation_lines("conv_1")
    assert [line.text for line in lines] == ["we need planks"]
    assert [line.text for line in model.heard_conversation_lines("conv_2")] == [
        "over here"
    ]


def test_the_object_transcript_fills_in_lines_said_before_joining() -> None:
    model = WorldModel("ada")
    observe(
        model,
        5,
        objects=[
            converse_object(
                "conv_1",
                ANCHOR,
                ["mira", "ada"],
                transcript=[{"tick": 1, "speaker": "mira", "text": "who is free?"}],
            )
        ],
        events=[conversation_utterance_event("mira", "anyone?", ANCHOR, "conv_1")],
    )
    lines = transcript_for(model, model.conversation_by_id("conv_1"), "conv_1")
    assert [line.text for line in lines] == ["who is free?", "anyone?"]


def test_a_join_is_recognised_from_the_worlds_own_action_event() -> None:
    model = WorldModel("ada")
    digest = observe(
        model, 2, events=[acted_event("ada", "converse", True, "join conv_1")]
    )
    assert joined_conversation_id(digest) == "conv_1"  # type: ignore[arg-type]

    digest = observe(
        model, 3, events=[acted_event("ada", "converse", False, "conversation is full")]
    )
    assert joined_conversation_id(digest) == ""  # type: ignore[arg-type]


def test_a_join_is_recognised_as_taking_a_seat() -> None:
    model = WorldModel("ada")
    digest = observe(
        model, 2, events=[acted_event("ada", "converse", True, "join conv_7")]
    )

    seat = joined_conversation(digest)  # type: ignore[arg-type]
    assert (seat.conversation_id, seat.action) == ("conv_7", "join")


def test_a_hail_is_recognised_as_taking_a_seat() -> None:
    model = WorldModel("ada")
    digest = observe(
        model, 2, events=[acted_event("ada", "converse", True, "hail conv_7 mira")]
    )

    seat = joined_conversation(digest)  # type: ignore[arg-type]
    assert (seat.conversation_id, seat.action, seat.target) == (
        "conv_7",
        "hail",
        "mira",
    )


def test_being_hailed_is_recognised_as_taking_a_seat() -> None:
    model = WorldModel("ada")
    digest = observe(
        model, 2, events=[acted_event("ada", "converse", True, "hailed conv_7 ivo")]
    )

    seat = joined_conversation(digest)  # type: ignore[arg-type]
    assert (seat.conversation_id, seat.action) == ("conv_7", "hailed")


def test_a_failed_accept_takes_no_seat() -> None:
    model = WorldModel("ada")
    digest = observe(
        model, 2, events=[acted_event("ada", "converse", False, "not next to mira")]
    )

    seat = joined_conversation(digest)  # type: ignore[arg-type]
    assert (seat.conversation_id, seat.action) == ("", "")


# -- the session -------------------------------------------------------------


async def test_the_session_waits_while_it_is_someone_elses_turn(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    converser = FakeConverser()
    digest = observe(
        model,
        1,
        objects=[converse_object("conv_1", ANCHOR, ["mira", "ada"], speaker="mira")],
    )
    session = session_for(model, converser, tmp_path)

    intent = session.decide(digest)  # type: ignore[arg-type]

    assert intent.HasField("wait")
    assert not converser.prompts
    assert not session.finished


async def test_on_its_turn_the_session_asks_once_and_speaks_on_the_next_tick(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    converser = FakeConverser(
        script=[ConverserMove(action=ACTION_SPEAK, text="I have planks to spare.")]
    )
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    digest = observe(model, 1, objects=[conversation])
    session = session_for(model, converser, tmp_path)

    first = session.decide(digest)  # type: ignore[arg-type]
    assert first.HasField("wait"), "the tick loop never waits on the model"
    await asyncio.sleep(0)

    digest = observe(model, 2, objects=[conversation])
    second = session.decide(digest)  # type: ignore[arg-type]

    assert second.converse.action == ACTION_SPEAK
    assert second.converse.text == "I have planks to spare."
    assert second.converse.conversation_id == "conv_1"
    assert len(converser.prompts) == 1
    assert "ada" in converser.prompts[0]


async def test_an_answer_that_arrives_after_the_turn_moved_on_is_discarded(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    converser = FakeConverser(script=[ConverserMove(action=ACTION_SPEAK, text="late")])
    digest = observe(
        model,
        1,
        objects=[
            converse_object(
                "conv_1", ANCHOR, ["ada", "mira"], speaker="ada", turn_started=1
            )
        ],
    )
    session = session_for(model, converser, tmp_path)
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    # The world timed the turn out and gave it back later, with a new number.
    digest = observe(
        model,
        12,
        objects=[
            converse_object(
                "conv_1", ANCHOR, ["ada", "mira"], speaker="ada", turn_started=11
            )
        ],
    )
    intent = session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    assert intent.HasField("wait"), "the stale answer is thrown away"
    assert len(converser.prompts) == 2, "and the converser is asked again"


async def test_pass_and_leave_become_converse_intents(tmp_path: Path) -> None:
    for action in (ACTION_PASS, ACTION_LEAVE):
        model = WorldModel("ada")
        converser = FakeConverser(script=[ConverserMove(action=action)])
        conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
        digest = observe(model, 1, objects=[conversation])
        session = session_for(model, converser, tmp_path)
        session.decide(digest)  # type: ignore[arg-type]
        await asyncio.sleep(0)
        digest = observe(model, 2, objects=[conversation])

        intent = session.decide(digest)  # type: ignore[arg-type]

        assert intent.converse.action == action


async def test_eating_keeps_the_turn_and_the_converser_is_asked_again(
    tmp_path: Path,
) -> None:
    """A settler starved in its seat in the first live conversation."""
    model = WorldModel("ada")
    converser = FakeConverser(
        script=[
            ConverserMove(action=ACTION_EAT, kind="berry"),
            ConverserMove(action=ACTION_SPEAK, text="better"),
        ]
    )
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    session = session_for(model, converser, tmp_path)
    session.decide(observe(model, 1, objects=[conversation]))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    eaten = session.decide(observe(model, 2, objects=[conversation]))  # type: ignore[arg-type]
    assert eaten.eat.item_type == "berry"

    session.decide(observe(model, 3, objects=[conversation]))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    spoken = session.decide(observe(model, 4, objects=[conversation]))  # type: ignore[arg-type]
    assert spoken.converse.action == ACTION_SPEAK
    assert len(converser.prompts) == 2


async def test_a_give_keeps_the_turn_and_the_converser_is_asked_again(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    converser = FakeConverser(
        script=[
            ConverserMove(action=ACTION_GIVE, give_to="mira", kind="plank", amount=3),
            ConverserMove(action=ACTION_SPEAK, text="there you go"),
        ]
    )
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    digest = observe(model, 1, objects=[conversation], inventory={"plank": 4})
    session = session_for(model, converser, tmp_path)
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    digest = observe(model, 2, objects=[conversation], inventory={"plank": 4})
    given = session.decide(digest)  # type: ignore[arg-type]
    assert given.give.target_entity_id == "mira"
    assert given.give.kind == "plank"
    assert given.give.amount == 3

    digest = observe(model, 3, objects=[conversation], inventory={"plank": 1})
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)
    digest = observe(model, 4, objects=[conversation], inventory={"plank": 1})
    spoken = session.decide(digest)  # type: ignore[arg-type]

    assert spoken.converse.action == ACTION_SPEAK
    assert len(converser.prompts) == 2


async def test_the_session_ends_when_the_conversation_object_is_gone(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="mira")
    digest = observe(model, 1, objects=[conversation])
    session = session_for(model, FakeConverser(), tmp_path)
    session.decide(digest)  # type: ignore[arg-type]

    digest = observe(model, 2, objects=[])
    session.decide(digest)  # type: ignore[arg-type]

    assert session.finished
    assert session.end_reason == END_CLOSED


async def test_an_opener_nobody_joined_says_so(tmp_path: Path) -> None:
    model = WorldModel("ada")
    conversation = converse_object("conv_1", ANCHOR, ["ada"])
    digest = observe(model, 1, objects=[conversation])
    session = session_for(model, FakeConverser(), tmp_path)
    session.decide(digest)  # type: ignore[arg-type]

    digest = observe(model, 2, objects=[])
    session.decide(digest)  # type: ignore[arg-type]

    assert session.end_reason == END_NOBODY_JOINED


async def test_losing_the_seat_ends_the_session(tmp_path: Path) -> None:
    model = WorldModel("ada")
    digest = observe(
        model, 1, objects=[converse_object("conv_1", ANCHOR, ["ada", "mira"])]
    )
    session = session_for(model, FakeConverser(), tmp_path)
    session.decide(digest)  # type: ignore[arg-type]

    digest = observe(
        model, 2, objects=[converse_object("conv_1", ANCHOR, ["mira", "bo"])]
    )
    session.decide(digest)  # type: ignore[arg-type]

    assert session.end_reason == END_REMOVED


async def test_a_conversation_that_never_appears_is_given_a_few_ticks(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    digest = observe(model, 1)
    session = session_for(model, FakeConverser(), tmp_path)
    session.decide(digest)  # type: ignore[arg-type]
    assert not session.finished

    digest = observe(model, 1 + OBJECT_GRACE_TICKS)
    session.decide(digest)  # type: ignore[arg-type]
    assert session.end_reason == END_NOBODY_JOINED


# -- what the last move did --------------------------------------------------


async def test_a_failed_give_is_fed_back_with_the_worlds_own_reason(
    tmp_path: Path,
) -> None:
    """greta gave `3 berry` five times over with two in her pack (tick 272+)."""
    model = WorldModel("ada")
    converser = FakeConverser(
        script=[
            ConverserMove(action=ACTION_GIVE, give_to="mira", kind="berry", amount=3),
            ConverserMove(action=ACTION_GIVE, give_to="mira", kind="berry", amount=3),
        ]
    )
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    session = session_for(model, converser, tmp_path)
    session.decide(observe(model, 1, objects=[conversation], inventory={"berry": 2}))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    session.decide(observe(model, 2, objects=[conversation], inventory={"berry": 2}))  # type: ignore[arg-type]

    # The world refused the give; the next prompt must carry its reason.
    digest = observe(
        model,
        3,
        objects=[conversation],
        events=[acted_event("ada", "give", False, "not enough berry to give")],
        inventory={"berry": 2},
    )
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    prompt = converser.prompts[-1]
    assert "Your moves so far this turn:" in prompt
    assert "give 3 berry to mira -> not enough berry to give" in prompt
    assert "Your pack: berry x2." in prompt, "the pack is re-read every turn"


async def test_a_successful_give_is_reported_back_and_counted_once(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    converser = FakeConverser(
        script=[
            ConverserMove(action=ACTION_GIVE, give_to="mira", kind="plank", amount=2),
            ConverserMove(action=ACTION_SPEAK, text="there you go"),
        ]
    )
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    session = session_for(model, converser, tmp_path)
    session.decide(observe(model, 1, objects=[conversation], inventory={"plank": 2}))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    session.decide(observe(model, 2, objects=[conversation], inventory={"plank": 2}))  # type: ignore[arg-type]

    digest = observe(
        model,
        3,
        objects=[conversation],
        events=[acted_event("ada", "give", True, "gave 2 plank to mira")],
    )
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)
    session.finish(END_CLOSED)
    report = await session.write_report()

    assert "give 2 plank to mira -> gave 2 plank to mira" in converser.prompts[-1]
    assert report.given == ("gave 2 plank to mira",)


async def test_an_eat_outcome_reaches_the_next_prompt(tmp_path: Path) -> None:
    model = WorldModel("ada")
    converser = FakeConverser(
        script=[
            ConverserMove(action=ACTION_EAT, kind="berry"),
            ConverserMove(action=ACTION_SPEAK, text="better"),
        ]
    )
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    session = session_for(model, converser, tmp_path)
    session.decide(observe(model, 1, objects=[conversation]))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    session.decide(observe(model, 2, objects=[conversation]))  # type: ignore[arg-type]

    digest = observe(
        model,
        3,
        objects=[conversation],
        events=[acted_event("ada", "eat", False, "no berry to eat")],
    )
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    assert "eat berry -> no berry to eat" in converser.prompts[-1]


async def test_outcomes_are_dropped_when_a_new_turn_begins(tmp_path: Path) -> None:
    model = WorldModel("ada")
    converser = FakeConverser(
        script=[
            ConverserMove(action=ACTION_GIVE, give_to="mira", kind="berry", amount=3),
            ConverserMove(action=ACTION_SPEAK, text="fresh turn"),
        ]
    )
    first = converse_object(
        "conv_1", ANCHOR, ["ada", "mira"], speaker="ada", turn_started=1
    )
    session = session_for(model, converser, tmp_path)
    session.decide(observe(model, 1, objects=[first]))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    session.decide(observe(model, 2, objects=[first]))  # type: ignore[arg-type]

    later = converse_object(
        "conv_1", ANCHOR, ["ada", "mira"], speaker="ada", turn_started=20
    )
    digest = observe(
        model,
        20,
        objects=[later],
        events=[acted_event("ada", "give", False, "not enough berry to give")],
    )
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    assert "Your moves so far this turn:" not in converser.prompts[-1]


async def test_a_give_with_no_target_is_refused_without_spending_a_tick(
    tmp_path: Path,
) -> None:
    """greta answered `give` with an empty `give_to` at tick 317 and passed."""
    model = WorldModel("ada")
    converser = FakeConverser(
        script=[
            ConverserMove(action=ACTION_GIVE, give_to="", kind="berry", amount=3),
            ConverserMove(action=ACTION_GIVE, give_to="mira", kind="", amount=3),
            ConverserMove(action=ACTION_SPEAK, text="who wants them?"),
        ]
    )
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    session = session_for(model, converser, tmp_path)
    session.decide(observe(model, 1, objects=[conversation]))  # type: ignore[arg-type]
    await asyncio.sleep(0)

    refused = session.decide(observe(model, 2, objects=[conversation]))  # type: ignore[arg-type]
    assert refused.HasField("wait"), "nothing is sent to the world"
    await asyncio.sleep(0)
    assert OUTCOME_NO_TARGET in converser.prompts[-1]

    kindless = session.decide(observe(model, 3, objects=[conversation]))  # type: ignore[arg-type]
    assert kindless.HasField("wait")
    await asyncio.sleep(0)
    assert OUTCOME_NO_KIND in converser.prompts[-1]

    spoken = session.decide(observe(model, 4, objects=[conversation]))  # type: ignore[arg-type]
    assert spoken.converse.action == ACTION_SPEAK, "the turn was never spent"


# -- call timing -------------------------------------------------------------


async def test_latency_is_the_length_of_the_call_not_the_wait_to_collect_it(
    tmp_path: Path,
) -> None:
    """Traced `latency_ms` of 34-48 s hid calls that really took about 4 s."""
    model = WorldModel("ada")
    converser = SlowConverser(ConverserMove(action=ACTION_SPEAK, text="hi"), delay=0.05)
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    session = ConversationSession(
        "conv_1",
        model,
        converser,
        trace=AgentTrace("ada", tmp_path),
        memory_path=tmp_path / "memory.md",
    )
    session.begin()
    session.decide(observe(model, 1, objects=[conversation]))  # type: ignore[arg-type]

    # The answer is ready long before the tick loop comes back for it.
    await asyncio.sleep(0.3)
    spoken = session.decide(observe(model, 2, objects=[conversation]))  # type: ignore[arg-type]

    assert spoken.converse.action == ACTION_SPEAK
    turns = trace_turns(session, tmp_path)
    assert len(turns) == 1
    assert 20 <= turns[0]["latency_ms"] < 250, turns[0]["latency_ms"]


async def test_a_call_outrunning_its_turn_is_cancelled_and_traced_at_once(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    converser = SlowConverser(ConverserMove(action=ACTION_SPEAK, text="too late"))
    mine = converse_object(
        "conv_1", ANCHOR, ["ada", "mira"], speaker="ada", turn_started=1
    )
    session = ConversationSession(
        "conv_1",
        model,
        converser,
        trace=AgentTrace("ada", tmp_path),
        memory_path=tmp_path / "memory.md",
    )
    session.begin()
    session.decide(observe(model, 1, objects=[mine]))  # type: ignore[arg-type]
    await asyncio.sleep(0)

    # The world timed the turn out and handed it to mira.
    hers = converse_object(
        "conv_1", ANCHOR, ["ada", "mira"], speaker="mira", turn_started=12
    )
    waiting = session.decide(observe(model, 12, objects=[hers]))  # type: ignore[arg-type]
    await asyncio.sleep(0)

    assert waiting.HasField("wait")
    turns = trace_turns(session, tmp_path)
    assert len(turns) == 1, "the dead call is traced on the tick it is dropped"
    assert turns[0]["tick"] == 12
    assert turns[0]["note"] == NOTE_STALE
    assert turns[0]["cancelled"] is True
    assert converser.release.is_set() is False


async def test_a_finished_answer_is_traced_stale_on_someone_elses_turn(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    converser = FakeConverser(script=[ConverserMove(action=ACTION_SPEAK, text="late")])
    mine = converse_object(
        "conv_1", ANCHOR, ["ada", "mira"], speaker="ada", turn_started=1
    )
    session = session_for(model, converser, tmp_path)
    session.decide(observe(model, 1, objects=[mine]))  # type: ignore[arg-type]
    await asyncio.sleep(0)

    hers = converse_object(
        "conv_1", ANCHOR, ["ada", "mira"], speaker="mira", turn_started=12
    )
    session.decide(observe(model, 12, objects=[hers]))  # type: ignore[arg-type]

    turns = trace_turns(session, tmp_path)
    assert [turn["note"] for turn in turns] == [NOTE_STALE]
    assert turns[0]["tick"] == 12
    assert turns[0]["move"]["text"] == "late"


# -- transcript merge --------------------------------------------------------


def test_a_line_heard_a_tick_after_it_was_said_appears_once() -> None:
    """The prompt showed `t330 iris: ...` and `t331 iris: ...`, one line."""
    model = WorldModel("ada")
    observe(
        model,
        11,
        objects=[
            converse_object(
                "conv_1",
                ANCHOR,
                ["mira", "ada"],
                transcript=[{"tick": 10, "speaker": "mira", "text": "give Cleo the 3"}],
            )
        ],
        events=[
            conversation_utterance_event("mira", "give Cleo the 3", ANCHOR, "conv_1")
        ],
    )

    lines = transcript_for(model, model.conversation_by_id("conv_1"), "conv_1")

    assert [(line.tick, line.text) for line in lines] == [(10, "give Cleo the 3")]


def test_the_same_line_said_again_much_later_is_kept() -> None:
    lines = merge_transcript(
        [
            TranscriptLine(10, "mira", "who has planks?"),
            TranscriptLine(11, "mira", "who has planks?"),
            TranscriptLine(40, "mira", "who has planks?"),
            TranscriptLine(12, "bo", "who has planks?"),
        ]
    )

    assert [(line.tick, line.speaker) for line in lines] == [
        (10, "mira"),
        (12, "bo"),
        (40, "mira"),
    ]


# -- purpose (docs/09 section 10, item 4) ------------------------------------


async def test_the_prompt_shows_the_purpose_only_when_this_actor_has_one(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    converser = FakeConverser()
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    digest = observe(model, 1, objects=[conversation])
    session = session_for(
        model, converser, tmp_path, purpose="agree who builds the wall"
    )
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    assert converser.prompts
    assert "You started this conversation because: agree who builds the wall" in (
        converser.prompts[-1]
    )


async def test_the_prompt_says_nothing_when_there_is_no_purpose(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    converser = FakeConverser()
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    digest = observe(model, 1, objects=[conversation])
    session = session_for(model, converser, tmp_path)
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    assert converser.prompts
    assert "You started this conversation because" not in converser.prompts[-1]


def test_conversation_start_traces_the_purpose(tmp_path: Path) -> None:
    model = WorldModel("ada")
    session = session_for(model, FakeConverser(), tmp_path, purpose="ask for wood")
    rows = trace_events(session, tmp_path, "conversation_start")
    assert rows[0]["purpose"] == "ask for wood"


# -- report and note ---------------------------------------------------------


async def test_the_report_carries_the_transcript_gifts_and_note(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    converser = FakeConverser(note_text="mira is short of planks")
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="mira")
    digest = observe(
        model,
        1,
        objects=[conversation],
        events=[conversation_utterance_event("mira", "any planks?", ANCHOR, "conv_1")],
    )
    session = session_for(model, converser, tmp_path)
    session.decide(digest)  # type: ignore[arg-type]

    digest = observe(
        model,
        2,
        objects=[conversation],
        events=[acted_event("mira", "give", True, "gave 2 plank to ada")],
    )
    session.decide(digest)  # type: ignore[arg-type]
    session.finish(END_CLOSED)

    report = await session.write_report()
    text = report.to_text()

    assert report.end_reason == END_CLOSED
    assert report.received == {"plank": 2}
    assert report.agreed == "mira is short of planks"
    assert "any planks?" in text
    assert "plank +2" in text
    assert "mira is short of planks" in text


async def test_a_note_lands_under_todays_notes_in_the_journal(tmp_path: Path) -> None:
    model = WorldModel("ada")
    converser = FakeConverser(note_text="bo will bring stone tomorrow")
    digest = observe(
        model, 4, objects=[converse_object("conv_1", ANCHOR, ["ada", "bo"])]
    )
    session = session_for(model, converser, tmp_path)
    session.decide(digest)  # type: ignore[arg-type]
    session.finish(END_CLOSED)

    await session.write_report()

    journal = Journal.parse((tmp_path / "memory.md").read_text(encoding="utf-8"))
    assert journal.scratch == [
        "- [conversation, tick 4, with bo] agreed: bo will bring stone tomorrow"
    ]


async def test_an_empty_note_writes_nothing(tmp_path: Path) -> None:
    model = WorldModel("ada")
    digest = observe(
        model, 4, objects=[converse_object("conv_1", ANCHOR, ["ada", "bo"])]
    )
    session = session_for(model, FakeConverser(note_text="  "), tmp_path)
    session.decide(digest)  # type: ignore[arg-type]
    session.finish(END_CLOSED)

    await session.write_report()

    assert not (tmp_path / "memory.md").exists(), "nothing wrote, nothing seeded"


async def test_a_turn_and_the_closing_note_carry_what_their_calls_cost(
    tmp_path: Path,
) -> None:
    usage = {
        "input_tokens": 900,
        "output_tokens": 12,
        "cached_tokens": 800,
        "requests": 1,
        "cost_usd": 0.0004,
    }
    model = WorldModel("ada")
    converser = FakeConverser(
        script=[ConverserMove(action=ACTION_SPEAK, text="hi")],
        note_text="mira wants planks",
        usage=dict(usage),
    )
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    digest = observe(model, 1, objects=[conversation])
    session = session_for(model, converser, tmp_path)
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)
    session.decide(observe(model, 2, objects=[conversation]))  # type: ignore[arg-type]
    session.finish(END_CLOSED)
    await session.write_report()
    session.trace.close()

    path = tmp_path / "agent-ada" / "conversations.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    turn = next(row for row in rows if row["event"] == "turn")
    end = next(row for row in rows if row["event"] == "conversation_end")
    assert turn["usage"] == usage
    assert end["usage"] == usage


async def test_a_failed_converser_call_records_no_usage(tmp_path: Path) -> None:
    model = WorldModel("ada")
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    digest = observe(model, 1, objects=[conversation])
    session = session_for(model, FailingConverser(), tmp_path)
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)
    session.decide(observe(model, 2, objects=[conversation]))  # type: ignore[arg-type]

    turn = trace_turns(session, tmp_path)[0]
    assert "usage" not in turn
    assert turn["move"]["action"] == ACTION_PASS


async def test_the_conversation_trace_records_start_turn_and_end(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    converser = FakeConverser(script=[ConverserMove(action=ACTION_SPEAK, text="hi")])
    conversation = converse_object("conv_1", ANCHOR, ["ada", "mira"], speaker="ada")
    digest = observe(model, 1, objects=[conversation])
    session = session_for(model, converser, tmp_path)
    session.decide(digest)  # type: ignore[arg-type]
    await asyncio.sleep(0)
    digest = observe(model, 2, objects=[conversation])
    session.decide(digest)  # type: ignore[arg-type]
    session.finish(END_CLOSED)
    await session.write_report()
    session.trace.close()

    path = tmp_path / "agent-ada" / "conversations.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        events = [json.loads(line)["event"] for line in handle if line.strip()]
    assert events == ["conversation_start", "turn", "conversation_end"]


# -- joining -----------------------------------------------------------------


def test_jev_is_offered_a_join_next_to_the_anchor() -> None:
    model = WorldModel("ada")
    observe(model, 1, objects=[converse_object("conv_1", ANCHOR, ["mira"])])

    options = {option.key: option for option in enumerate_options(model)}

    option = options["join_conversation:conv_1"]
    assert option.intent.converse.action == "join"
    assert option.intent.converse.conversation_id == "conv_1"


def test_a_conversation_further_away_is_offered_as_a_walk() -> None:
    model = WorldModel("ada")
    observe(model, 1, objects=[converse_object("conv_1", (15, 10), ["mira"])])

    options = {option.key: option for option in enumerate_options(model)}

    option = options["join_conversation:conv_1"]
    assert option.intent.HasField("move")
    assert option.travel_target is not None
    assert option.travel_target.target == (15, 10)


def test_no_join_is_offered_to_an_actor_already_seated_or_to_a_full_table() -> None:
    seated = WorldModel("ada")
    observe(seated, 1, objects=[converse_object("conv_1", ANCHOR, ["mira", "ada"])])
    assert not [
        option
        for option in enumerate_options(seated)
        if option.key.startswith("join_conversation:")
    ]

    full = WorldModel("ada")
    observe(
        full,
        1,
        objects=[converse_object("conv_2", ANCHOR, ["a", "b", "c", "d"])],
    )
    assert not [
        option
        for option in enumerate_options(full)
        if option.key.startswith("join_conversation:")
    ]


def test_free_seat_tiles_skip_blocked_and_occupied_ground() -> None:
    model = WorldModel("ada")
    observe(
        model,
        1,
        objects=[
            converse_object("conv_1", ANCHOR, ["mira"]),
            make_object("wall_1", "wood_wall", (12, 10)),
        ],
    )

    tiles = free_seat_tiles(model, ANCHOR)

    assert (12, 10) not in tiles
    assert (11, 9) in tiles


def test_the_approach_driver_walks_and_then_stops() -> None:
    model = WorldModel("ada")
    observe(model, 1, objects=[converse_object("conv_1", (14, 10), ["mira"])])
    driver = ApproachDriver([(13, 10)], "a free tile next to conv_1")

    assert driver.stop_reason(model) == ""
    choice = driver.choose(model)
    assert choice.option.intent.HasField("move")

    observe(
        model,
        2,
        position=(13, 10),
        objects=[converse_object("conv_1", (14, 10), ["mira"])],
    )
    assert driver.stop_reason(model) == ApproachDriver.ARRIVED
