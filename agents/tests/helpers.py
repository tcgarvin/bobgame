"""Builders for synthetic observations and a scripted stand-in for Jev."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Mapping, Sequence

import pytest

from agents import world_pb2 as pb
from agents.jev_agent.conversation import (
    ConversationReport,
    ConverserMove,
    MoveCall,
    NoteCall,
)
from agents.jev_agent.agent import JevAgent
from agents.jev_agent.client import IntentResult
from agents.jev_agent.jevclient import JevDecision
from agents.jev_agent.journal import (
    Journal,
    JournalRewrite,
    SECTION_LEARNINGS,
    SECTION_ME,
    SECTION_OTHERS,
    SECTION_STORY,
    SECTION_TOMORROW,
    finish_rewrite,
)
from agents.jev_agent.outcomes import NO_DETAIL, SUBMITTED, ActionOutcome
from agents.jev_agent.planner import PlannerDeps
from agents.jev_agent.reflex import EMPTY_REFLEX, ReflexBrief
from agents.jev_agent.stint import Brief, StintReport, never_ends
from agents.jev_agent.worldmodel import WorldModel

GRASS = "grass"
WATER = "deep_water"


def make_entity(
    entity_id: str,
    position: tuple[int, int],
    *,
    entity_type: str = "player",
    health: int = 20,
    max_health: int = 20,
    food: int = 80,
    max_food: int = 100,
    wielded: str = "",
    alive: bool = True,
    inventory: Mapping[str, int] | None = None,
    fatigue: int = 0,
    max_fatigue: int = 100,
    asleep: bool = False,
    sleeping_on: str = "",
    collapsed: bool = False,
) -> pb.Entity:
    """A proto Entity with sensible player defaults."""
    items = [
        pb.InventoryItem(kind=kind, quantity=quantity)
        for kind, quantity in sorted((inventory or {}).items())
    ]
    return pb.Entity(
        entity_id=entity_id,
        position=pb.Position(x=position[0], y=position[1]),
        entity_type=entity_type,
        health=health,
        max_health=max_health,
        food=food,
        max_food=max_food,
        wielded=wielded,
        alive=alive,
        inventory=pb.Inventory(items=items),
        fatigue=fatigue,
        max_fatigue=max_fatigue,
        asleep=asleep,
        sleeping_on=sleeping_on,
        collapsed=collapsed,
    )


def make_object(
    object_id: str,
    object_type: str,
    position: tuple[int, int],
    state: Mapping[str, str] | None = None,
) -> pb.WorldObject:
    """A proto WorldObject."""
    return pb.WorldObject(
        object_id=object_id,
        object_type=object_type,
        position=pb.Position(x=position[0], y=position[1]),
        state=dict(state or {}),
    )


def make_tiles(
    centre: tuple[int, int],
    radius: int = 8,
    *,
    blocked: Iterable[tuple[int, int]] = (),
    floor: str = GRASS,
) -> list[pb.Tile]:
    """A square of walkable tiles around `centre`, minus `blocked`."""
    blocked_set = set(blocked)
    tiles: list[pb.Tile] = []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            position = (centre[0] + dx, centre[1] + dy)
            is_blocked = position in blocked_set
            tiles.append(
                pb.Tile(
                    position=pb.Position(x=position[0], y=position[1]),
                    walkable=not is_blocked,
                    opaque=is_blocked,
                    floor_type="mountain" if is_blocked else floor,
                )
            )
    return tiles


def make_observation(
    tick: int,
    self_entity: pb.Entity,
    *,
    tiles: Sequence[pb.Tile] | None = None,
    objects: Sequence[pb.WorldObject] = (),
    entities: Sequence[pb.Entity] = (),
    events: Sequence[pb.ObservationEvent] = (),
    clock: pb.WorldClock | None = None,
) -> pb.Observation:
    """An Observation with a fully walkable view unless told otherwise."""
    position = (self_entity.position.x, self_entity.position.y)
    return pb.Observation(
        tick_id=tick,
        self=self_entity,
        visible_tiles=list(tiles if tiles is not None else make_tiles(position)),
        visible_objects=list(objects),
        visible_entities=list(entities),
        events=list(events),
        clock=clock if clock is not None else make_clock(tick),
    )


def make_clock(
    tick: int,
    day_length: int = 300,
    night_start: int = 200,
    *,
    new_moon_tonight: bool = False,
    next_new_moon_day: int = -1,
    save_tick: int = 0,
) -> pb.WorldClock:
    """The world clock for `tick`, with the settlement day length.

    `next_new_moon_day` is -1 by default, the world's value for "this world has
    no new moon" (docs/14 section 1).
    """
    tick_of_day = tick % day_length
    return pb.WorldClock(
        day=tick // day_length,
        tick_of_day=tick_of_day,
        day_length=day_length,
        night=tick_of_day >= night_start,
        new_moon_tonight=new_moon_tonight,
        next_new_moon_day=next_new_moon_day,
        save_tick=save_tick,
    )


def acted_event(
    entity_id: str, action_type: str, success: bool, details: str = ""
) -> pb.ObservationEvent:
    """An `EntityActed` observation event."""
    return pb.ObservationEvent(
        entity_acted=pb.EntityActed(
            entity_id=entity_id,
            action_type=action_type,
            success=success,
            details=details,
        )
    )


def utterance_event(
    speaker_id: str,
    text: str,
    position: tuple[int, int],
    channel: str = "local",
) -> pb.ObservationEvent:
    """An `Utterance` observation event spoken from `position`."""
    return pb.ObservationEvent(
        utterance=pb.Utterance(
            speaker_id=speaker_id,
            channel=channel,
            text=text,
            position=pb.Position(x=position[0], y=position[1]),
        )
    )


def damaged_event(
    entity_id: str, attacker_id: str, amount: int, remaining_health: int
) -> pb.ObservationEvent:
    """An `EntityDamaged` observation event."""
    return pb.ObservationEvent(
        entity_damaged=pb.EntityDamaged(
            entity_id=entity_id,
            attacker_id=attacker_id,
            amount=amount,
            remaining_health=remaining_health,
        )
    )


@dataclass
class FakeJevClient:
    """A `JevClient` that replays scripted answers and records what it saw."""

    script: list[JevDecision] = field(default_factory=list)
    default_action: str = "wait"
    calls: list[tuple[dict[str, Any], dict[str, str]]] = field(default_factory=list)

    async def decide(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> JevDecision:
        """Pop the next scripted decision, falling back to `default_action`."""
        self.calls.append((dict(state), dict(options)))
        if self.script:
            return self.script.pop(0)
        return JevDecision(
            action=self.default_action,
            probabilities={self.default_action: 1.0},
            confidence=1.0,
            input_tokens=100,
            latency_ms=1,
        )

    @property
    def last_options(self) -> dict[str, str]:
        """The option set offered on the most recent call."""
        return self.calls[-1][1]

    @property
    def last_state(self) -> dict[str, Any]:
        """The state sent on the most recent call."""
        return self.calls[-1][0]


def converse_object(
    conversation_id: str,
    anchor: tuple[int, int],
    participants: Sequence[str],
    *,
    speaker: str = "",
    turn_started: int = 0,
    utterances: int = 0,
    transcript: Sequence[Mapping[str, Any]] = (),
) -> pb.WorldObject:
    """A `conversation` world object with the state the world writes."""
    return make_object(
        conversation_id,
        "conversation",
        anchor,
        {
            "participants": json.dumps(list(participants)),
            "speaker": speaker,
            "turn_started": str(turn_started),
            "opened_tick": "1",
            "opened_by": participants[0] if participants else "",
            "utterances": str(utterances),
            "transcript": json.dumps(list(transcript)),
        },
    )


def conversation_utterance_event(
    speaker_id: str,
    text: str,
    position: tuple[int, int],
    conversation_id: str,
    channel: str = "conversation",
) -> pb.ObservationEvent:
    """An `Utterance` event that belongs to a conversation."""
    return pb.ObservationEvent(
        utterance=pb.Utterance(
            speaker_id=speaker_id,
            channel=channel,
            text=text,
            position=pb.Position(x=position[0], y=position[1]),
            conversation_id=conversation_id,
        )
    )


@dataclass
class FakeConverser:
    """A `Converser` that replays scripted moves and records its prompts."""

    script: list[ConverserMove] = field(default_factory=list)
    default_action: str = "pass"
    # `note_text` is the "agreed or learned" field; `note_commitment` is the
    # "you said you would" field (docs/09 section 10, item 5).
    note_text: str = ""
    note_commitment: str = ""
    prompts: list[str] = field(default_factory=list)
    note_prompts: list[str] = field(default_factory=list)
    usage: dict[str, object] = field(default_factory=dict)

    async def move(self, prompt: str) -> MoveCall:
        """Pop the next scripted move, falling back to `default_action`."""
        self.prompts.append(prompt)
        if self.script:
            return MoveCall(self.script.pop(0), self.usage)
        return MoveCall(ConverserMove(action=self.default_action), self.usage)

    async def note(self, prompt: str) -> NoteCall:
        """Return the fixed closing note fields."""
        self.note_prompts.append(prompt)
        return NoteCall(self.note_text, self.note_commitment, self.usage)


def died_event(entity_id: str, killer_id: str = "") -> pb.ObservationEvent:
    """An `EntityDied` observation event."""
    return pb.ObservationEvent(
        entity_died=pb.EntityDied(entity_id=entity_id, killer_id=killer_id)
    )


def respawned_event(
    entity_id: str, position: tuple[int, int] = (10, 10)
) -> pb.ObservationEvent:
    """An `EntityRespawned` observation event."""
    return pb.ObservationEvent(
        entity_respawned=pb.EntityRespawned(
            entity_id=entity_id, position=pb.Position(x=position[0], y=position[1])
        )
    )


_RESULT_RE = re.compile(r"^(?P<action>\S+) (?P<status>ok|failed): (?P<detail>.*)$")


def canned_outcome(text: str) -> ActionOutcome:
    """An `ActionOutcome` that renders back to `text`, for a fake bridge.

    Tests write what the world said as the one line the planner used to see,
    which is more readable than four keyword arguments; this turns that line
    back into the record the real tick loop would have built.
    """
    description, _, tail = text.partition(" -> ")
    if not tail:
        return ActionOutcome.submitted_only(description)
    if tail == SUBMITTED:
        return ActionOutcome.submitted_only(description)
    match = _RESULT_RE.match(tail)
    if match is None:
        return ActionOutcome.never_ran(description, tail)
    detail = match.group("detail")
    return ActionOutcome.from_event(
        description,
        match.group("action"),
        match.group("status") == "ok",
        "" if detail == NO_DETAIL else detail,
    )


class RecordingBridge:
    """An `AgentBridge` that records calls instead of touching a world."""

    def __init__(self, world_model: WorldModel) -> None:
        self._model = world_model
        self.briefs: list[Brief] = []
        self.drivers: list[object] = []
        self.actions: list[tuple[pb.Intent, str]] = []
        self.waits: list[int] = []
        self.thoughts: list[str] = []
        self.reflex = EMPTY_REFLEX
        self.reflex_notes: list[str] = []
        self.conversation_reports: list[ConversationReport] = []
        self.conversation_purposes: list[str] = []
        self.direct_result = ""
        # Consumed one per `direct_action` call, ahead of `direct_result`.
        self.direct_results: list[str] = []
        self.wake_calls: list[int] = []
        self.wake_result = ""
        self.journal_waits = 0
        self.active_waits = 0
        # Called while a turn waits for the journal, to stand in for a rewrite
        # finishing between turns.
        self.on_journal_wait: Callable[[], None] = lambda: None
        # The extra end rules `travel_to` hands down, one per stint.
        self.end_checks: list[Callable[[WorldModel], str]] = []
        # The end reason every recorded stint reports back.
        self.stint_end_reason = "eject"

    @property
    def model(self) -> WorldModel:
        return self._model

    async def run_stint(
        self,
        brief: Brief,
        driver: object | None = None,
        end_check: Callable[[WorldModel], str] = never_ends,
    ) -> StintReport:
        self.briefs.append(brief)
        self.drivers.append(driver)
        self.end_checks.append(end_check)
        return StintReport(
            brief=brief,
            ticks_used=3,
            end_reason=self.stint_end_reason,
            start_position=(10, 10),
            end_position=(12, 10),
            start_stats="hp 20/20, food 80/100",
            end_stats="hp 20/20, food 77/100",
            inventory_delta={"wood": 2},
            action_counts={"extract": (3, 0)},
            notable=["discovered 2 new objects"],
            tail=["t3 extract:tree_1 -> accepted (eject 0.80, danger 0.01)"],
        )

    async def direct_action(self, intent: pb.Intent, description: str) -> ActionOutcome:
        self.actions.append((intent, description))
        if self.direct_results:
            return canned_outcome(self.direct_results.pop(0))
        if self.direct_result:
            return canned_outcome(self.direct_result)
        return ActionOutcome.from_event(description, "action", True, "")

    async def wait_ticks(self, ticks: int) -> str:
        self.waits.append(ticks)
        return f"waited {ticks}"

    async def await_wake(self, since_tick: int) -> str:
        self.wake_calls.append(since_tick)
        return self.wake_result

    async def await_active(self) -> None:
        self.active_waits += 1

    async def await_conversation(self) -> ConversationReport | None:
        if not self.conversation_reports:
            return None
        return self.conversation_reports.pop(0)

    def set_reflex(self, brief: ReflexBrief) -> None:
        self.reflex = brief

    def clear_reflex(self) -> None:
        self.reflex = EMPTY_REFLEX

    def set_conversation_purpose(self, purpose: str) -> None:
        self.conversation_purposes.append(purpose)

    def drain_notes(self, *, for_prompt: bool = False) -> list[str]:
        notes = list(self.reflex_notes)
        self.reflex_notes.clear()
        return notes

    async def await_journal(self) -> None:
        self.journal_waits += 1
        self.on_journal_wait()

    def set_thought(self, thought: str) -> None:
        self.thoughts.append(thought)


@pytest.fixture
def world_model() -> WorldModel:
    """A model with a tree, a chest, a board, and a neighbour in view."""
    model = WorldModel("ada")
    model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10), inventory={"wood": 2}),
            objects=[
                make_object("tree_1", "tree", (12, 10)),
                make_object("chest_1", "chest", (9, 10), {"contents": '{"berry": 3}'}),
                make_object(
                    "board_1",
                    "message_board",
                    (11, 11),
                    {
                        "notes": '[{"title": "Wood pile", "text": "chest by the '
                        'spring", "author": "bob", "tick": 4}]'
                    },
                ),
            ],
            entities=[make_entity("bob", (11, 10))],
        )
    )
    return model


@pytest.fixture
def bridge(world_model: WorldModel) -> RecordingBridge:
    """A recording bridge over that model."""
    return RecordingBridge(world_model)


@pytest.fixture
def deps(bridge: RecordingBridge, tmp_path: Path) -> PlannerDeps:
    """Planner dependencies pointing at a throwaway memory file."""
    return PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")


class FakeWorldClient:
    """Replays a fixed observation script and records everything submitted."""

    def __init__(self, observations: Sequence[pb.Observation]) -> None:
        self._observations = list(observations)
        self.lease_id = "lease-1"
        self.submitted: list[pb.Intent] = []
        self.statuses: list[tuple[str, str, str, str]] = []
        self.closed = False

    async def acquire_lease(self) -> None:
        return None

    async def run_lease_renewal(self) -> None:
        while True:
            await asyncio.sleep(3600)

    async def observations(self) -> AsyncIterator[pb.Observation]:
        for observation in self._observations:
            # Yield control so the planner task can make progress between ticks,
            # exactly as it would while waiting on the real stream.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            yield observation

    async def submit_intent(self, tick_id: int, intent: pb.Intent) -> IntentResult:
        self.submitted.append(intent)
        return IntentResult(accepted=True, reason="")

    async def report_status(self, *status: str) -> bool:
        self.statuses.append(tuple(status))  # type: ignore[arg-type]
        return True

    async def close(self) -> None:
        self.closed = True


def build_agent(world: FakeWorldClient, jev: FakeJevClient, tmp_path: Path) -> JevAgent:
    """A JevAgent wired to fakes and a throwaway log directory."""
    return JevAgent(
        world,  # type: ignore[arg-type]
        jev,
        "ada",
        log_root=tmp_path,
        planner_model="test",
    )


class FakeJournalWriter:
    """A `JournalWriter` that records its calls and answers when told to."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []
        self.clock_facts: list[str] = []
        self.gate = asyncio.Event()
        self.gate.set()

    async def rewrite(
        self, journal: Journal, day_log: str, entity_id: str, clock_fact: str = ""
    ) -> JournalRewrite:
        self.calls.append((day_log, entity_id))
        self.clock_facts.append(clock_fact)
        await self.gate.wait()
        if self.error is not None:
            raise self.error
        return finish_rewrite(
            journal,
            {
                SECTION_STORY: "I slept.",
                SECTION_ME: "A builder.",
                SECTION_OTHERS: "",
                SECTION_LEARNINGS: "",
                SECTION_TOMORROW: "Chop six wood.",
            },
            len,
            {"cost_usd": 0.002},
            "fake-journal-model",
        )
