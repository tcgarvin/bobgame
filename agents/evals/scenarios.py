"""The one place the eval suite touches the agent's state-building code.

`options.enumerate_options` and `jevstate.build_state` are the two functions
whose signatures the evals depend on. Every call to them lives in
`run_scenario`, so a change to either is one edit here, and every scenario
sends exactly what a real stint would send.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Sequence

from agents import world_pb2 as pb
from agents.jev_agent.jevstate import StintProgress, build_state
from agents.jev_agent.options import (
    brief_object_ids,
    enumerate_options,
    options_to_criteria,
)
from agents.jev_agent.stint import Brief
from agents.jev_agent.worldmodel import WorldModel

# The observation builders live in `tests/`, which is not a package: pytest puts
# that directory on `sys.path` for the unit tests, and the evals need the same.
_TESTS_DIR = Path(__file__).resolve().parent.parent / "tests"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from helpers import (  # type: ignore[import-not-found]  # noqa: E402
    make_clock,
    make_entity,
    make_object,
    make_observation,
    make_tiles,
)

__all__ = [
    "Scenario",
    "make_clock",
    "make_entity",
    "make_object",
    "make_observation",
    "make_tiles",
    "make_world",
    "run_scenario",
]

Scenario = tuple[dict[str, Any], dict[str, str]]


def make_world(entity_id: str, observations: Sequence[pb.Observation]) -> WorldModel:
    """A world model for `entity_id` that has seen `observations` in order.

    Several observations let a scenario show the actor a place it has since
    walked away from, which is what "knows the way there" means to Jev.
    """
    if not observations:
        raise ValueError("a scenario needs at least one observation")
    model = WorldModel(entity_id)
    for observation in observations:
        model.update(observation)
    return model


def run_scenario(
    model: WorldModel,
    brief: Brief,
    *,
    progress: StintProgress | None = None,
) -> Scenario:
    """The exact state and criteria a stint would send to Jev for this tick.

    Returns `(state, criteria)` ready to hand to `JevClient.decide`. Named
    places and the ids the brief mentions come off the brief, as in `Stint`.
    """
    options = enumerate_options(
        model,
        brief.travel,
        shouts=brief.shouts,
        invitations=brief.invitations,
        places=brief.places,
        brief_text=brief.text,
    )
    criteria = options_to_criteria(options)
    state = build_state(
        model,
        instruction=brief.instruction,
        success_condition=brief.success_condition,
        progress=(
            StintProgress(ticks_left=brief.max_ticks) if progress is None else progress
        ),
        notes=brief.notes,
        travel=brief.travel,
        places=brief.places,
        highlight_ids=brief_object_ids(model, brief.text),
    )
    return state, criteria
